"""LLM 统一网关（最小版，contracts/llm-gateway.md，宪章原则三）。

一切 LLM 调用必须经此网关：统一计费（价目表折算）、内容哈希缓存
（命中零成本不调后端）、指数退避重试（上限 3 次，4xx 不重试）。
网关内部重试后仍失败的调用，调用方不得再次重试（防双层重试叠加放大）。
网关不做任何提示词业务逻辑（物料组装属 agents 层）。
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import blake3

from core.llm_gateway.routing import ProfileConfigError

MAX_RETRIES = 3  # 重试上限（首次 + 3 次重试）
INITIAL_BACKOFF_SECONDS = 0.5


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
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    role: str = ""
    profile_id: str = ""


class LLMBackend(Protocol):
    """网关后端协议（Mock / Http 实现）。"""

    call_count: int

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult: ...


@dataclass(frozen=True)
class LLMResult:
    """网关产出：文本、usage、折算成本、缓存命中标记（+ 角色与档案，便于追溯）。"""

    text: str
    usage: dict
    cost_usd: float
    cached: bool
    role: str = ""
    profile_id: str = ""


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
        """本次调用生效的（模型名，价目）——**估算与折算同源**（遗留 1 的收敛点）。

        - 接档案：按角色路由 → （档案 id 作为模型名，档案价目）；未映射角色走显式默认；
        - 未接档案：沿用 `model=` + `price_book`（单档案等价现状）。
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

        缓存命中不计入（零成本、零 token，另见 `cache_hits`）；树节点 `CostRecord` 口径不变
        （分解只在报告层呈现）。
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
    ) -> LLMResult:
        """调用 LLM（唯一昂贵动作，原则三）：按角色路由到档案（接档案时）或按 `model` 旧路径。

        接档案后**角色必填**（缺即配置错误，不猜测）；模型名与价目都由档案决定，
        调用点的 `model=` 只在未接档案的旧路径生效——**业务代码零厂商字面量**由本设计保证。
        """
        decision_role = ""
        profile_id = ""
        zero_marginal = False
        if self._profiles is not None:
            decision = self.route(role)
            profile = self._profiles.profile(decision.profile_id)
            call_model, price = (
                profile.profile_id,
                {
                    "prompt_per_1k": float(profile.prices["prompt_per_1k"]),
                    "completion_per_1k": float(profile.prices["completion_per_1k"]),
                },
            )
            decision_role, profile_id = str(decision.role), profile.profile_id
            zero_marginal = profile.zero_marginal
        else:
            if model is None:
                raise PricingNotFoundError("未接入档案时必须给出 model（缺价目不允许静默零成本）")
            call_model, price = model, self._price_of(model)  # 缺价目立即报错，不调后端

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

        cost = (
            raw.prompt_tokens / 1000 * price["prompt_per_1k"]
            + raw.completion_tokens / 1000 * price["completion_per_1k"]
        )
        usage = {"prompt_tokens": raw.prompt_tokens, "completion_tokens": raw.completion_tokens}
        result = LLMResult(
            text=raw.text,
            usage=usage,
            cost_usd=cost,
            cached=False,
            role=decision_role,
            profile_id=profile_id,
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
