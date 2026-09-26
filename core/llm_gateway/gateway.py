"""LLM 统一网关（最小版，contracts/llm-gateway.md，宪章原则三）。

一切 LLM 调用必须经此网关：统一计费（价目表折算）、内容哈希缓存
（命中零成本不调后端）、指数退避重试（上限 3 次，4xx 不重试）。
网关内部重试后仍失败的调用，调用方不得再次重试（防双层重试叠加放大）。
网关不做任何提示词业务逻辑（物料组装属 agents 层）。

功能 019 追加三件（契约 C5~C10）：**两维价目**（峰谷 × 厂商缓存命中四格取价，单点
`price_cell` 由折算与估算同取）、**可选 spend guard**（固定六步序列的第③步：预估价 vs 余量，
拒绝即 `BudgetRefusedError` 上抛、网关三量不变），以及**成本上界估算的唯一实现**
（`estimate_cost_usd`，调用点只做薄调用）。守卫与价目矩阵都是**可选注入**：
`spend_guard=None` 且档案未声明 `price_matrix` 时，网关行为与改造前逐字节一致、独立可用。
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import blake3

from core.llm_gateway.profiles import cost_from_prices, price_cell
from core.llm_gateway.routing import ProfileConfigError

MAX_RETRIES = 3  # 重试上限（首次 + 3 次重试）
INITIAL_BACKOFF_SECONDS = 0.5

# 价目口径备注（C7/C8）：三种情形各一句，随结果如实透传（报告与校准记录可直接复述）
NOTE_NO_MATRIX = "未区分峰谷/缓存（档案未声明 price_matrix：四格同价）"
NOTE_CELL = "按格位取价"
NOTE_CACHE_UNREPORTED = "厂商未报告命中 token（按未命中计）"
NOTE_LOCAL_CACHE = "本地内容哈希缓存命中（零成本、零后端调用、不占额）"


class GatewayError(Exception):
    """网关全部错误的基类。"""


class PricingNotFoundError(GatewayError):
    """价目表缺该模型：不允许静默零成本。"""


class TransientBackendError(GatewayError):
    """后端 5xx/超时：可重试。"""


class PermanentBackendError(GatewayError):
    """后端 4xx：不重试直接失败。"""


@dataclass(frozen=True)
class BackendResult:
    """后端原始产出：文本 + token 用量（+ 路由信息，由网关在接档案时填充）。

    `role` / `profile_id`（功能 016 / 契约 C6）：**后端自己不感知角色**——网关按角色路由后
    用 `dataclasses.replace` 补上，便于调用方与账目追溯"这次调用走的哪条档案"。既有后端实现
    与测试构造不受影响（默认空串）。

    `cached_prompt_tokens`（功能 019 / 契约 C7）：**末位追加**的可选字段——厂商响应 usage 里
    报出的**命中 prompt token 数**（协议字段，随厂商差异在 `backends/` 内调整，**不新增档案
    配置键**）；旧后端不设该字段即为 `None`（按未命中档记账并记口径备注）。
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    role: str = ""
    profile_id: str = ""
    cached_prompt_tokens: int | None = None


class LLMBackend(Protocol):
    """网关后端协议（Mock / Http 实现）。"""

    call_count: int

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult: ...


@dataclass(frozen=True)
class LLMResult:
    """网关产出：文本、usage、折算成本、缓存命中标记（+ 角色/档案/格位/口径备注）。

    功能 019 追加三项（均**末位**、有默认值）：`cell_key` = 本次折算所用格位键
    （`<峰谷>_<缓存>`；未声明矩阵或本地缓存命中 ⇒ `""`）、`cached_prompt_tokens` = 厂商报告的
    命中 token 数（未报告 ⇒ `None`）、`pricing_note` = 价目口径备注（三种情形 + 本地命中）。
    """

    text: str
    usage: dict
    cost_usd: float
    cached: bool
    role: str = ""
    profile_id: str = ""
    cell_key: str = ""
    cached_prompt_tokens: int | None = None
    pricing_note: str = ""


@dataclass(frozen=True)
class SpendRequest:
    """门禁请求（C10）：渠道 id + 环节 id + **预估额**。

    预估价由**网关**填（成本上界估算的唯一实现在本模块；守卫不自行估算，避免两套口径）。
    守卫侧（`core/billing/`）按结构化协议读这三个字段——本模块对 `core.billing` **零 import**，
    `spend_guard=None` 时网关独立可用。
    """

    channel_id: str
    stage: str
    estimated_usd: float


def estimate_cost_usd(prompt: str, prices: Mapping[str, float], max_tokens: int) -> float:
    """成本**上界**估算（全仓唯一实现，T1921）：输入按 `max(1, len(prompt) // 2)` token、
    输出按 `max_tokens` 满额，价目取 `price_cell` 的格位价（估算与折算**同源**，不会脱钩）。

    输出满额是刻意保守（保证 actual ≤ estimated）；**不并入** `backends/mock.py` 的同名算式
    ——那是模拟后端的 usage 生成（造伪 token），用途不同，并入即语义错位。
    """
    prompt_tokens = max(1, len(prompt) // 2)
    return cost_from_prices(prices, prompt_tokens=prompt_tokens, completion_tokens=max_tokens)


@dataclass(frozen=True)
class _Resolved:
    """一次调用的第①步结果：本次生效的档案、模型名、基础价目与角色。"""

    profile: object | None
    call_model: str
    prices: dict
    role: str
    profile_id: str
    zero_marginal: bool


class LLMGateway:
    """统一入口：chat(prompt, model=..., ...) -> LLMResult。

    `profiles`（功能 016）：模型档案解析结果 `ProfileLoad`；缺省 None = 既有调用形态
    （只按 `price_book` 折算，快照走"旧扁平价目表"路径）——**单档案行为与现状等价**。
    """

    def __init__(
        self,
        backend: LLMBackend,
        price_book: dict[str, dict],
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
        profiles=None,
        spend_guard=None,
        channel_id: str = "",
        peak_windows=None,
    ) -> None:
        self.backend = backend
        self._price_book = price_book
        # 模型档案（功能 016）：`ProfileLoad`（配置解析结果）；None = 既有调用形态
        # （`price_book=` 单档案等价现状，快照取自该价目表并如实标注来源）
        self._profiles = profiles
        self._sleep = sleep
        self._max_retries = max_retries
        self._cache: dict[str, LLMResult] = {}
        self.total_cost_usd = 0.0  # 网关账本（对账三方之一）
        self.call_count = 0  # 真实计费调用次数（缓存命中不计）
        # 成本分解（功能 016 / 契约 C6）：{role: {profile_id: {calls, tokens, cost}}}
        self._breakdown: dict[str, dict[str, dict]] = {}
        self.cache_hits = 0  # 缓存命中次数（命中不计费、不计 token）
        # 预算门禁（功能 019 / 契约 C10）：**可选**注入；None = 既有装配（行为逐字节不变）
        self._spend_guard = spend_guard
        self._channel_id = str(channel_id or "")
        if spend_guard is not None and not self._channel_id:
            raise GatewayError(
                "注入 spend_guard 时必须同时声明 channel_id（C10 的请求含渠道 id："
                "守卫按渠道绑定判定，缺渠道无从归属）"
            )
        # 渠道日历（功能 019 / 契约 C6）：**注入的判定值**（`is_peak(moment)` + 归属口径三字段）
        # ——网关不读配置文件、也不持有 `budget:` 段，故仍可独立装配与单测
        self._peak_windows = peak_windows
        # 报告层元数据（C7/C8/C10）：每（角色，档案）的估算额、所用格位与口径备注
        self._meta: dict[str, dict[str, dict]] = {}

    def _cache_key(self, prompt: str, model: str, temperature: float, max_tokens: int) -> str:
        raw = f"{model}|{prompt}|{temperature}|{max_tokens}"
        return blake3.blake3(raw.encode("utf-8")).hexdigest()

    def _price_of(self, model: str) -> dict:
        if model not in self._price_book:
            raise PricingNotFoundError(f"价目表缺少模型 {model!r}（不允许静默零成本）")
        return self._price_book[model]

    # ---- 角色路由与价目（功能 016 / 契约 C4~C6）----

    @property
    def profiles(self):
        """档案解析结果（未接档案时为 None）。"""
        return self._profiles

    def route(self, role):
        """角色 → 路由决策（C4）：接档案时按 `resolve_routing` 的映射/默认回落；可追溯快照引用。"""
        from core.llm_gateway.routing import Role, RouteDecision, route

        if self._profiles is None:
            raise ProfileConfigError(
                "未接入档案（profiles=None）：无法按角色路由（用 model= 旧路径）"
            )
        if role is None:
            raise ProfileConfigError(
                f"接入档案后调用必须声明角色：合法角色为 {[m.value for m in Role]}"
            )
        if not isinstance(role, Role):
            raise ProfileConfigError(
                f"角色必须是 Role 枚举成员，实际 {role!r}：合法角色为 {[m.value for m in Role]}"
            )
        decision = route(
            role, self._profiles.routing, profile_snapshot_ref=self.profile_snapshot().ref
        )
        assert isinstance(decision, RouteDecision)
        return decision

    def prices_for(self, *, role=None, model: str | None = None) -> tuple[str, dict]:
        """本次调用生效的（模型名，**基础价目**）——路由键与基础两键的取数口。

        - 接档案：按角色路由 → （档案 id 作为模型名，档案基础价目）；未映射角色走显式默认；
        - 未接档案：沿用 `model=` + `price_book`（单档案等价现状）。

        **声明 `price_matrix` 的档案这里返回的是基础两键**（不是本次生效的格位）：生效格位由
        `price_cell` 判定、其键与口径备注随 `LLMResult.cell_key` / `pricing_note` 如实透传——
        本方法供"取模型名（缓存键）与基础视图"用，**不得**拿它当矩阵档案的折算依据。
        """
        if self._profiles is not None:
            decision = self.route(role)
            profile = self._profiles.profile(decision.profile_id)
            return profile.profile_id, {
                "prompt_per_1k": float(profile.prices["prompt_per_1k"]),
                "completion_per_1k": float(profile.prices["completion_per_1k"]),
            }
        if model is None:
            raise PricingNotFoundError("未接入档案时必须给出 model（缺价目不允许静默零成本）")
        return model, self._price_of(model)

    # ---- 门禁与格位（功能 019 / 契约 C5~C10）----

    @property
    def spend_guard(self):
        """注入的门禁（未注入 ⇒ None；网关不读它的内部结构）。"""
        return self._spend_guard

    def _resolve(self, *, role=None, model: str | None = None) -> _Resolved:
        """第①步：角色路由与取价（模型名 + 基础价目 + 角色/档案留痕）。"""
        if self._profiles is None:
            if model is None:
                raise PricingNotFoundError("未接入档案时必须给出 model（缺价目不允许静默零成本）")
            # 缺价目立即报错，不调后端、不占额
            return _Resolved(
                profile=None,
                call_model=model,
                prices=dict(self._price_of(model)),
                role="",
                profile_id="",
                zero_marginal=False,
            )
        decision = self.route(role)
        profile = self._profiles.profile(decision.profile_id)
        return _Resolved(
            profile=profile,
            call_model=profile.profile_id,
            prices={
                "prompt_per_1k": float(profile.prices["prompt_per_1k"]),
                "completion_per_1k": float(profile.prices["completion_per_1k"]),
            },
            role=str(decision.role),
            profile_id=profile.profile_id,
            zero_marginal=profile.zero_marginal,
        )

    def cell_for(
        self, profile, *, moment: datetime, cache_hit: bool, peak: bool | None
    ) -> tuple[str, dict]:
        """取格位（`price_cell` 的唯一调用口径）。

        `peak` 为**已判定**的峰谷归属（`None` = 无渠道日历/未判定）：判定在调用开始时做**一次**，
        折算与估算共用同一判定（跨峰谷切换的调用按开始时刻归属，不拆分、不重判）。
        """
        resolver = None if peak is None else (lambda _moment: peak)
        return price_cell(profile, moment=moment, cache_hit=cache_hit, is_peak=resolver)

    def _peak_verdict(self, moment: datetime) -> bool | None:
        """调用开始时刻的峰谷归属（渠道日历由调用方注入；未注入 ⇒ None，矩阵档案取价即报错）。"""
        calendar = self._peak_windows
        return None if calendar is None else bool(calendar.is_peak(moment))

    def estimate_cost(
        self,
        prompt: str,
        *,
        role=None,
        model: str | None = None,
        max_tokens: int = 1024,
        moment: datetime | None = None,
    ) -> float:
        """本次调用的成本**上界**估算（**唯一属主**＝本模块；调用点只做薄调用）。

        价目取 `price_cell` 的**未命中档**（缓存命中在调用前未知 ⇒ 按保守的未命中估），
        峰谷按**调用开始时刻**（缺省 = 现在）判定——与折算同取一个函数，两处口径不会脱钩。
        """
        resolved = self._resolve(role=role, model=model)
        if resolved.profile is not None and resolved.profile.price_matrix:
            at = moment or datetime.now(UTC)
            _, prices = self.cell_for(
                resolved.profile, moment=at, cache_hit=False, peak=self._peak_verdict(at)
            )
        else:
            prices = resolved.prices
        return estimate_cost_usd(prompt, prices, max_tokens)

    def _attribution_note(self) -> str:
        """峰谷归属口径（C6：报告/运行记录口径备注可见；日历由调用方注入）。"""
        calendar = self._peak_windows
        if calendar is None:
            return ""
        attribution = str(getattr(calendar, "attribution", "") or "")
        timezone = str(getattr(calendar, "timezone", "") or "")
        windows = tuple(getattr(calendar, "windows", ()) or ())
        rendered = (
            "全谷时（windows=[]）"
            if not windows
            else "、".join(f"[{window.start}, {window.end})" for window in windows)
        )
        return (
            f"峰谷归属=attribution={attribution or '未声明'}（闭开区间 [start, end)，跨峰谷不拆分；"
            f"时区={timezone or '未声明'}；峰时区间={rendered}）"
        )

    def _pricing_note(self, *, cell_key: str, cache_hit: bool, has_matrix: bool) -> str:
        """价目口径备注（三种情形，C7/C8）：未声明矩阵 / 按格位取价 / 厂商未报命中。"""
        if not has_matrix:
            note = NOTE_NO_MATRIX
        else:
            note = f"{NOTE_CELL}：{cell_key}"
            if not cache_hit and cell_key.endswith("_miss"):
                note = f"{note}；{NOTE_CACHE_UNREPORTED}"
        attribution = self._attribution_note()
        return f"{note}；{attribution}" if attribution else note

    def _record_meta(
        self, *, role: str, profile_id: str, estimated_usd: float, cell_key: str, note: str
    ) -> None:
        """报告层元数据：估算额 / 已用格位 / 口径备注（**三数分离**的报告侧材料）。"""
        bucket = self._meta.setdefault(role, {}).setdefault(
            profile_id, {"estimated_usd": 0.0, "cells": set(), "notes": set()}
        )
        bucket["estimated_usd"] += estimated_usd
        bucket["cells"].add(cell_key)
        bucket["notes"].add(note)

    def _accumulate(
        self,
        *,
        role: str,
        profile_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        zero_marginal: bool,
    ) -> None:
        """累积成本分解（C6）：按（角色，档案）分条目，金额与 token 如实累计。"""
        by_role = self._breakdown.setdefault(role, {})
        entry = by_role.setdefault(
            profile_id,
            {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
                "zero_marginal": zero_marginal,
            },
        )
        entry["calls"] += 1
        entry["prompt_tokens"] += prompt_tokens
        entry["completion_tokens"] += completion_tokens
        entry["cost_usd"] += cost

    def cost_breakdown(self) -> dict:
        """成本分解（C6）：

        `{role: {profile_id: {calls, prompt_tokens, completion_tokens, cost_usd}}}`

        缓存命中不计入（零成本、零 token，另见 `cache_hits`）；树节点 `CostRecord` 六字段
        口径不变（总成本照旧入账），LLM 腿（角色 × 档案）分解为 022 新增扩展字段，
        口径与本函数一致。
        """
        return {
            role: {profile_id: dict(entry) for profile_id, entry in sorted(profiles.items())}
            for role, profiles in sorted(self._breakdown.items())
        }

    def cost_report(self) -> dict:
        """账目报告（功能 016 / FR-009）：分解条目 + 合并视图 + **价目口径备注** +
        **"记账 ≠ 厂商账单"显式声明**（记账是折算值，厂商账单以其官方计价为准）。"""
        snapshot = self.profile_snapshot()
        prices_by_profile = snapshot.price_book()
        notes_by_profile = {entry["profile_id"]: entry for entry in snapshot.to_dict()["profiles"]}
        entries: list[dict] = []
        by_profile: dict[str, dict] = {}
        for role, profiles in sorted(self._breakdown.items()):
            for profile_id, entry in sorted(profiles.items()):
                meta = notes_by_profile.get(profile_id, {})
                price_note = meta.get("price_note") or (
                    "零边际成本（自建/本地推理；机器与运维成本不在本表）"
                    if entry["zero_marginal"]
                    else "价目口径未声明（price_note 缺失）"
                )
                entries.append(
                    {
                        "role": role,
                        "profile_id": profile_id,
                        **dict(entry),
                        "prices": prices_by_profile.get(profile_id, {}),
                        "price_note": price_note,
                        "endpoint": meta.get("endpoint", ""),
                        "api_key_env": meta.get("api_key_env", ""),
                        # 019：估算额 / 所用格位 / 口径备注（三数分离的报告侧材料；未接矩阵时
                        # 格位为空串、备注为「未区分峰谷/缓存」）
                        "estimated_usd": self._meta.get(role, {})
                        .get(profile_id, {})
                        .get("estimated_usd", 0.0),
                        "price_cells": sorted(
                            self._meta.get(role, {}).get(profile_id, {}).get("cells", ())
                        ),
                        "pricing_notes": sorted(
                            self._meta.get(role, {}).get(profile_id, {}).get("notes", ())
                        ),
                    }
                )
                merged = by_profile.setdefault(
                    profile_id,
                    {"calls": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0},
                )
                merged["calls"] += entry["calls"]
                merged["cost_usd"] += entry["cost_usd"]
                merged["prompt_tokens"] += entry["prompt_tokens"]
                merged["completion_tokens"] += entry["completion_tokens"]
        return {
            "entries": entries,
            "by_profile": by_profile,
            "total_usd": sum(entry["cost_usd"] for entry in entries),
            "cache_hits": self.cache_hits,
            "profile_snapshot_ref": snapshot.ref,
            "accounting_note": (
                "记账 ≠ 厂商账单：此处金额是按配置档案价目折算的记账值（价目口径以各档案的 "
                "price_note 为准，如「峰时缓存未命中上限」这类保守高估——峰谷计价与缓存命中率"
                "等局限已在配置里登记）；厂商实际账单以其官方计价与账单为准（原则六）。"
            ),
        }

    def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        role=None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        stage: str = "",
    ) -> LLMResult:
        """调用 LLM（唯一昂贵动作，原则三）：**固定六步序列**（C10，顺序即语义）。

        ① 角色路由与取价（格位）→ ② **本地缓存判定**（命中即返：零成本、零后端调用、
        **不占额**，故不过门禁）→ ③ **门禁 `check`**（预估额 vs 余量）→ ④ 后端调用 →
        ⑤ 入账（`total_cost_usd`/`call_count`/`_breakdown`）→ ⑥ `reservation.settle(actual)`。

        接档案后**角色必填**（缺即配置错误，不猜测）；模型名与价目都由档案决定，
        调用点的 `model=` 只在未接档案的旧路径生效——**业务代码零厂商字面量**由本设计保证。
        `stage=` 为**调用点声明**的环节 id（C9）：注入守卫时按档位判定（缺声明即拒绝
        `tier_undeclared`，不静默归入默认档）；未注入守卫时只随请求如实透传。
        拒绝（`BudgetRefusedError`）**原样上抛**：本方法的三量均不变、缓存不写。
        后端调用失败时 `reservation` **不结算**（该次调用是否已被厂商计费未知）：
        预留如实留在账本的未结算清单里（`reserved_usd > 0` + 未结算标记），**不静默清零**——
        余量的处置由运营决定（处置口径见 C11 的"崩溃残留预留"）。
        """
        moment = datetime.now(UTC)  # 调用**开始**时刻：跨峰谷按它归属，一次调用只取一格
        resolved = self._resolve(role=role, model=model)
        decision_role, profile_id = resolved.role, resolved.profile_id
        call_model, zero_marginal = resolved.call_model, resolved.zero_marginal
        has_matrix = bool(
            resolved.profile is not None and getattr(resolved.profile, "price_matrix", None)
        )

        key = self._cache_key(prompt, call_model, temperature, max_tokens)
        if key in self._cache:
            cached = self._cache[key]
            self.cache_hits += 1
            return LLMResult(
                text=cached.text,
                usage=cached.usage,
                cost_usd=0.0,
                cached=True,
                role=decision_role,
                profile_id=profile_id,
                cell_key="",  # 本地命中不进任何格位（与厂商 prompt 缓存维度正交）
                pricing_note=NOTE_LOCAL_CACHE,
            )

        # 峰谷归属只在调用开始时判定**一次**（跨谷峰切换不拆分、不按 token 摊分）
        peak = self._peak_verdict(moment)
        # ③ 门禁：预估额由**网关**填（唯一估算实现，守卫不自行估算）
        estimate = self._estimate(
            prompt, resolved=resolved, moment=moment, max_tokens=max_tokens, peak=peak
        )
        reservation = None
        if self._spend_guard is not None:
            reservation = self._spend_guard.check(
                SpendRequest(
                    channel_id=self._channel_id,
                    stage=str(stage or ""),
                    estimated_usd=estimate,
                )
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                raw = self.backend.complete(
                    prompt, model=call_model, temperature=temperature, max_tokens=max_tokens
                )
                break
            except TransientBackendError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    self._sleep(INITIAL_BACKOFF_SECONDS * (2**attempt))  # 指数退避
            # PermanentBackendError 不重试，直接上抛
        else:
            raise last_error  # type: ignore[misc]

        reported = raw.cached_prompt_tokens  # 厂商报告的命中 prompt token 数（None = 未报告）
        reading_valid = reported is None or (
            not isinstance(reported, bool)
            and isinstance(reported, int)
            and 0 <= reported <= raw.prompt_tokens
        )
        cache_hit = bool(reading_valid and reported is not None)
        if resolved.profile is not None:
            cell_key, prices = self.cell_for(
                resolved.profile, moment=moment, cache_hit=cache_hit, peak=peak
            )
        else:
            cell_key, prices = "", resolved.prices
        cost = cost_from_prices(
            prices,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
        )
        note = self._pricing_note(cell_key=cell_key, cache_hit=cache_hit, has_matrix=has_matrix)
        usage = {"prompt_tokens": raw.prompt_tokens, "completion_tokens": raw.completion_tokens}
        result = LLMResult(
            text=raw.text,
            usage=usage,
            cost_usd=cost,
            cached=False,
            role=decision_role,
            profile_id=profile_id,
            cell_key=cell_key,
            cached_prompt_tokens=reported if reading_valid else None,
            pricing_note=note,
        )
        # 先记账再判正文：这次调用**确实发生过、token 确实被消耗**（原则二"费用照计"），
        # 故账本与成本分解照记；只是**不落缓存、不返回空结果**。
        self.total_cost_usd += cost
        self.call_count += 1
        self._accumulate(  # 成本分解（C6）：按（角色，档案）累计
            role=decision_role,
            profile_id=profile_id or call_model,
            prompt_tokens=raw.prompt_tokens,
            completion_tokens=raw.completion_tokens,
            cost=cost,
            zero_marginal=zero_marginal,
        )
        self._record_meta(
            role=decision_role,
            profile_id=profile_id or call_model,
            estimated_usd=estimate,
            cell_key=cell_key,
            note=note,
        )
        # ⑥ 结算：预留 → 实测（实测超预估 ⇒ 守卫侧 over_limit 告警；已发生的花费不回滚）
        if reservation is not None:
            reservation.settle(cost)
        # 厂商读数非法（命中数 > prompt 数 / 负数 / bool）⇒ 报错，**不静默钳制、不按 0 计**；
        # 本次调用费用已照记（原则二），只是不落缓存（下次仍会真实调用并重新判定）。
        if not reading_valid:
            raise TransientBackendError(
                f"厂商 usage 的命中 token 读数非法（cached_prompt_tokens={reported!r}，"
                f"prompt_tokens={raw.prompt_tokens}，model={call_model!r}"
                + (f"，profile={profile_id!r}" if profile_id else "")
                + "）：不静默钳制、也不按 0 计——本次调用费用照记（原则二），"
                "该次结果不落缓存"
            )
        # 空/缺失正文一律如实失败（原则六：不静默、不编造）——真实跑批踩到过：某次调用返回
        # 空内容，下游对 None/空串做哈希/正则，报错只有「TypeError: expected string or
        # bytes-like object, got 'NoneType'」，无法定位是哪次调用。这里指名模型/角色/档案，
        # 并说明收到的是什么（None / 空串 / 全空白）。
        if not isinstance(raw.text, str) or not raw.text.strip():
            raise TransientBackendError(
                f"后端返回空文本（model={call_model!r}"
                + (f"，role={decision_role!r}" if decision_role else "")
                + (f"，profile={profile_id!r}" if profile_id else "")
                + f"）：收到 {type(raw.text).__name__}"
                + ("（None：正文缺失）" if raw.text is None else "（空串/全空白：可能被截断）")
                + "——本次调用费用照记（原则二），但空正文不落缓存、不返回给调用方："
                "请重试或检查模型与 max_tokens 设置"
            )
        self._cache[key] = result
        return result

    def _estimate(
        self,
        prompt: str,
        *,
        resolved: _Resolved,
        moment: datetime,
        max_tokens: int,
        peak: bool | None,
    ) -> float:
        """上界估算的内部口径：矩阵档案取**未命中格**（缓存未知 ⇒ 保守），其余取基础价。"""
        if resolved.profile is not None and resolved.profile.price_matrix:
            _, prices = self.cell_for(resolved.profile, moment=moment, cache_hit=False, peak=peak)
        else:
            prices = resolved.prices
        return estimate_cost_usd(prompt, prices, max_tokens)

    def profile_snapshot(self):
        """LLM 档案快照（功能 016 / 契约 C7）：档案 + 价目 + 价目口径备注 + 端点 host +
        迁移说明，**不含密钥**——各 Agent 构造 `config_snapshot` 时并入键 `llm_profiles`，
        历史节点因此绑定当时的价目口径（改价不漂移，可审计复算）。

        未接入档案（既有 `price_book=` 调用形态）时，快照取自该价目表并标注来源
        `legacy_price_book`：单档案等价现状（SC-005），且端点/凭证名如实标注"沿用旧变量名"。
        """
        from core.llm_gateway.profiles import legacy_price_book_snapshot

        if self._profiles is not None:
            return self._profiles.snapshot()
        return legacy_price_book_snapshot(self._price_book)
