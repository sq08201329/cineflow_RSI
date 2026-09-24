"""LLM 模型档案（功能 016 / T1604）：解析、校验、迁移与快照。

## 档案是什么（**凭证/端点/价目只在这里，码内不内置**）

一条 `ModelProfile` = 端点（URL 或环境变量名）+ 凭证环境变量名 + 价目（每 1k tokens）+
价目口径备注（`price_note`）+ 零边际成本标记（`zero_marginal`）+ 是否沿用旧变量名
（`legacy_env`，报告标注用）。**任何厂商价目都不进代码**——价目内置即历史成本不可复现
（宪章原则一），故档案与价目随配置快照冻结
（`ProfileSnapshot` → 各 Agent 的 `config_snapshot["llm_profiles"]`）。

## 校验纪律（C1：缺项 100% 报错，不静默零成本、不回落默认价）

- 每条档案必须含：`base_url` **或** `base_url_env`（缺即报错）+ `api_key_env`（缺即报错）
  + `prices.prompt_per_1k` / `prices.completion_per_1k`（缺即报错）；
- `prices` 允许全 0，但必须显式 `zero_marginal: true`（自建/本地推理：零边际成本**仍非免费**）；
- `price_note` 缺省可通过，但记入 notes 告警（口径必须可追溯）；
- 快照只记端点 **host**（URL 形态）或变量名（env 形态），**绝不记密钥**；
- 非法配置一律 `ProfileConfigError`（`GatewayError` 子类，调用方仍可统一捕获）。

## 旧扁平配置迁移（C3）

旧写法（`<agent>.model` + `<agent>.model_prices`，端点与密钥取隐式 `OPENAI_*`）自动映射为
单档案：档案 id = 模型名、价目取自 `model_prices`、端点/凭证名登记为 `OPENAI_BASE_URL` /
`OPENAI_API_KEY` 并把 `legacy_env` 标为真（报告标注"沿用旧变量名"）。映射规则与结果写入
`migration_notes`（启动报告与快照可见，**不做静默兼容**）；新旧并存时**以新写法为准**并在
notes 标注"旧键被忽略"。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import blake3

from core.llm_gateway.routing import ProfileConfigError, Role, RoleRouting, resolve_routing

# 旧扁平写法的隐式凭证变量名（迁移时登记为档案的端点/密钥来源；报告标注"沿用旧变量名"）
LEGACY_BASE_URL_ENV = "OPENAI_BASE_URL"
LEGACY_API_KEY_ENV = "OPENAI_API_KEY"

# 归档旧写法时扫描的模型键（各 Agent 段：screenplay 用 model，promo 用 default_model）
_LEGACY_MODEL_KEYS = ("model", "default_model")

# 旧写法各段 → 角色（迁移时的角色映射；未列出的段不参与迁移）
_LEGACY_SECTION_ROLES = {"screenplay": Role.GENERATION, "promo": Role.COPYWRITING}

_PRICE_KEYS = ("prompt_per_1k", "completion_per_1k")

# 两维价目的格位（C5）：格位键 = `<峰谷>_<缓存>`；**声明即四格齐备**（缺一即报错）
MATRIX_CELLS = ("peak_miss", "peak_hit", "off_peak_miss", "off_peak_hit")
# 声明的维度（进快照，供报告/校准记录标注"这张价目表认哪些维度"）
DECLARED_DIMENSIONS = ("peak_off_peak", "cache_hit_miss")


def host_of_url(url: str) -> str:
    """URL → `scheme://host[:port]`（快照只记 host：不含路径/查询串/密钥）。"""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        raise ProfileConfigError(f"档案端点 URL 形态非法（期望 http(s)://host[:port]）：{url!r}")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def cost_from_prices(
    prices: Mapping[str, float], *, prompt_tokens: int, completion_tokens: int
) -> float:
    """按价目折算金额（**全仓唯一的折算算术**）：网关折算、预算估算与档案折算同取它。"""
    return prompt_tokens / 1000 * float(prices["prompt_per_1k"]) + completion_tokens / 1000 * float(
        prices["completion_per_1k"]
    )


@dataclass(frozen=True)
class ModelProfile:
    """一条模型档案（端点 + 凭证变量名 + 价目 + 口径备注）。"""

    profile_id: str
    model: str  # 厂商模型名（**请求体里的 model**；档案 id 只是本仓的路由键）
    api_key_env: str
    prices: Mapping[str, float]
    base_url: str | None = None
    base_url_env: str | None = None
    price_note: str = ""
    zero_marginal: bool = False
    legacy_env: bool = False
    timeout_seconds: float | None = None  # 单请求超时（推理模型需更长；None = 用后端默认）
    request_options: Mapping[str, Any] = field(default_factory=dict)  # 请求参数（白名单，见下）
    price_matrix: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def vendor_model(self) -> str:
        """发给厂商的模型名（真实故障：曾把档案 id 当模型名发出，被厂商 400 拒）。"""
        return self.model or self.profile_id

    @property
    def endpoint_ref(self) -> str:
        """快照里的端点引用：URL 形态只记 host；env 形态记变量名（**永不记密钥**）。"""
        if self.base_url:
            return host_of_url(self.base_url)
        return str(self.base_url_env)

    def cost_usd(self, *, prompt_tokens: int, completion_tokens: int) -> float:
        """按档案价目折算（记账口径 = 价目表；**记账 ≠ 厂商账单**）。

        **委派 `price_cell` + `cost_from_prices`**：全仓只有一份折算口径（本方法曾是一条独立的
        硬编码线性式，两维价目一上就会与网关折算脱钩）。未声明 `price_matrix` 的档案取基础两键
        （四格同价）；矩阵档案的格位取决于调用时刻与缓存命中，本方法拿不到这两个输入，
        故对矩阵档案报错并指向 `price_cell`——**声明矩阵后不回落基础价**。
        """
        _, prices = price_cell(self, moment=None, cache_hit=False)
        return cost_from_prices(
            prices, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        )

    def to_snapshot(self) -> dict:
        """档案快照条目（价目 + 备注 + 端点 host/变量名 + 标记；**无密钥**）。

        声明了两维价目的档案**追加**（可选）键 `price_matrix` / `declared_dimensions`：
        旧快照条目无这两个键（原语义 = 四格同价），读取端按**键集**分派、不按版本号猜，
        且旧快照**永不重写**（改矩阵只影响新节点）。
        """
        snapshot = {
            "profile_id": self.profile_id,
            "model": self.vendor_model,
            "endpoint": self.endpoint_ref,
            "endpoint_source": "base_url" if self.base_url else "base_url_env",
            "api_key_env": self.api_key_env,
            "prices": {key: float(self.prices[key]) for key in _PRICE_KEYS},
            "price_note": self.price_note,
            "zero_marginal": self.zero_marginal,
            "legacy_env": self.legacy_env,
            "timeout_seconds": self.timeout_seconds,
            "request_options": _deep_copy_options(self.request_options),
        }
        if self.price_matrix:
            snapshot["price_matrix"] = {
                cell: {key: float(self.price_matrix[cell][key]) for key in _PRICE_KEYS}
                for cell in MATRIX_CELLS
            }
            snapshot["declared_dimensions"] = list(DECLARED_DIMENSIONS)
        return snapshot


def price_cell(
    profile: ModelProfile,
    *,
    moment: Any = None,
    cache_hit: bool = False,
    is_peak: Any = None,
) -> tuple[str, dict[str, float]]:
    """单点取价（C5）：`(cell_key, prices)`——网关折算与预算估算**同取**本函数。

    - 未声明 `price_matrix` ⇒ `("", 基础价目)`：四格同价，口径「未区分峰谷/缓存」；
    - 声明矩阵 ⇒ 格位键 = `<峰谷>_<缓存>`，峰谷由**注入的渠道日历** `is_peak(moment)` 判定
      （C8：峰谷时区/窗口/归属随 `config_snapshot["budget_tiers"]` 冻结，**不进档案快照**，
      故本模块不持有日历，只按注入值判定）；一次调用只取一格（跨切换时刻不拆分）。
    - 矩阵档案缺 `moment` / 缺日历 ⇒ `ProfileConfigError`：**声明矩阵后不回落基础价**
      （静默取 `prices` 会让"两维价目"形同虚设）。
    """
    if not profile.price_matrix:
        return "", {key: float(profile.prices[key]) for key in _PRICE_KEYS}
    if moment is None or not callable(is_peak):
        raise ProfileConfigError(
            f"档案 {profile.profile_id!r} 声明了 price_matrix：取价必须给出调用时刻与渠道日历"
            "（缺则无从判定峰谷——声明矩阵后不回落基础价）"
        )
    cell = f"{'peak' if bool(is_peak(moment)) else 'off_peak'}_{'hit' if cache_hit else 'miss'}"
    return cell, {key: float(profile.price_matrix[cell][key]) for key in _PRICE_KEYS}


def snapshot_entry_cells(entry: Mapping) -> dict[str, dict[str, float]]:
    """**冻结快照条目** → 四格价目视图（读取端的形状分派口径）。

    - 条目**无** `price_matrix` 键 ⇒ 四格皆取基础 `prices`（旧快照原语义：四格同价）；
    - 条目有 `price_matrix` ⇒ 逐格如实取出（不回落基础价）。

    分派只认**键集**（不按版本号猜），故新旧两形状可同时读取；快照文件本身**永不重写**。
    """
    prices = {key: float(entry["prices"][key]) for key in _PRICE_KEYS}
    matrix = entry.get("price_matrix")
    if not isinstance(matrix, Mapping):
        return {cell: dict(prices) for cell in MATRIX_CELLS}
    return {cell: {key: float(matrix[cell][key]) for key in _PRICE_KEYS} for cell in MATRIX_CELLS}


@dataclass(frozen=True)
class ProfileSnapshot:
    """档案集合快照（并入各 Agent 的 `config_snapshot["llm_profiles"]`，随树冻结）。

    冻结的是"当时的价目口径"——改配置价目只影响新节点（可审计复算历史成本）。
    """

    profiles: tuple[dict, ...]
    default_profile: str
    default_reason: str
    roles: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    migration_notes: tuple[str, ...] = ()
    source: str = "llm_section"  # llm_section | legacy | legacy_price_book

    @property
    def fingerprint(self) -> str:
        """快照指纹（BLAKE3）：路由决策的 `profile_snapshot_ref` 用它可追溯。"""
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return blake3.blake3(canonical.encode()).hexdigest()

    @property
    def ref(self) -> str:
        return f"llm_profiles@{self.fingerprint[:12]}"

    def price_book(self) -> dict[str, dict]:
        """网关价目表视图：`{profile_id: {prompt_per_1k, completion_per_1k}}`（US2 用）。"""
        return {entry["profile_id"]: dict(entry["prices"]) for entry in self.profiles}

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "profiles": [dict(entry) for entry in self.profiles],
            "default_profile": self.default_profile,
            "default_reason": self.default_reason,
            "roles": dict(self.roles),
            "notes": list(self.notes),
            "migration_notes": list(self.migration_notes),
        }


@dataclass(frozen=True)
class ProfileLoad:
    """一次解析的完整结果（档案 + 路由 + 说明 + 来源），网关与各 Agent 共用。"""

    profiles: Mapping[str, ModelProfile]
    routing: RoleRouting
    notes: tuple[str, ...] = ()
    migration_notes: tuple[str, ...] = ()
    source: str = "llm_section"

    def profile(self, profile_id: str) -> ModelProfile:
        if profile_id not in self.profiles:
            raise ProfileConfigError(
                f"档案不存在：{profile_id!r}（已声明：{sorted(self.profiles)}）"
            )
        return self.profiles[profile_id]

    def snapshot(self) -> ProfileSnapshot:
        return ProfileSnapshot(
            profiles=tuple(profile.to_snapshot() for profile in self.profiles.values()),
            default_profile=self.routing.default_profile,
            default_reason=self.routing.default_reason,
            roles={str(role): profile for role, profile in self.routing.roles.items()},
            notes=self.notes,  # ProfileLoad.notes 已是"档案解析 + 路由校验"的合并视图
            migration_notes=self.migration_notes,
            source=self.source,
        )


# ---------------------------------------------------------------------------
# 解析（C1）+ 路由（C2）
# ---------------------------------------------------------------------------


def load_profiles(
    config: Mapping[str, Any],
) -> tuple[dict[str, ModelProfile], RoleRouting, list[str]]:
    """解析 `llm` 段 →（档案表，路由，notes）；缺项/非法一律 `ProfileConfigError`（C1/C2）。"""
    section = config.get("llm")
    if not isinstance(section, Mapping):
        raise ProfileConfigError(
            "配置缺少 llm 段（模型档案与角色路由必须以配置为权威，码内不内置价目）"
        )
    raw_profiles = section.get("profiles")
    if not isinstance(raw_profiles, Mapping) or not raw_profiles:
        raise ProfileConfigError("llm.profiles 必须为非空映射（档案 id → 档案定义）")
    profiles: dict[str, ModelProfile] = {}
    notes: list[str] = []
    for profile_id, raw in raw_profiles.items():
        profile, profile_notes = _parse_profile(str(profile_id), raw)
        profiles[str(profile_id)] = profile
        notes.extend(profile_notes)
    routing = resolve_routing(profiles, section.get("roles"), section.get("default_profile"))
    return profiles, routing, notes


def _parse_profile(profile_id: str, raw: Any) -> tuple[ModelProfile, list[str]]:
    if not isinstance(raw, Mapping):
        raise ProfileConfigError(f"llm.profiles[{profile_id}] 必须是映射（档案定义），实际 {raw!r}")
    base_url = raw.get("base_url")
    base_url_env = raw.get("base_url_env")
    if not base_url and not base_url_env:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 缺端点：必须声明 base_url（URL）或 base_url_env（环境变量名）"
            "——缺项即报错，不回落默认端点"
        )
    if base_url and base_url_env:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 同时声明 base_url 与 base_url_env：端点来源必须唯一（二选一）"
        )
    api_key_env = raw.get("api_key_env")
    if not isinstance(api_key_env, str) or not api_key_env:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 缺 api_key_env：必须声明凭证环境变量名"
            "——不隐式读 OPENAI_*（假阳性归零），也不允许无凭证档案"
        )
    model = str(raw.get("model") or profile_id)  # 缺省 = 档案 id（旧形态兼容）
    price_matrix, matrix_notes = _parse_price_matrix(profile_id, raw)
    prices, zero_marginal, price_notes = _parse_prices(
        profile_id, raw, has_matrix=bool(price_matrix)
    )
    timeout_seconds = _parse_timeout(profile_id, raw)
    request_options = _parse_request_options(profile_id, raw)
    price_note = raw.get("price_note")
    notes = [*price_notes, *matrix_notes]
    if not isinstance(price_note, str) or not price_note:
        price_note = ""
        notes.append(f"档案 {profile_id!r} 缺 price_note（价目口径备注）：建议补上以便审计复算")
    return (
        ModelProfile(
            profile_id=profile_id,
            model=model,
            api_key_env=api_key_env,
            prices=prices,
            base_url=str(base_url) if base_url else None,
            base_url_env=str(base_url_env) if base_url_env else None,
            price_note=price_note,
            zero_marginal=zero_marginal,
            legacy_env=bool(raw.get("legacy_env", False)),
            timeout_seconds=timeout_seconds,
            request_options=request_options,
            price_matrix=price_matrix,
            notes=tuple(notes),
        ),
        notes,
    )


def _parse_price_matrix(profile_id: str, raw: Mapping[str, Any]) -> tuple[dict, list[str]]:
    """两维价目解析（C5）：**声明即四格齐备**（缺格、缺键、多余格一律报错）。

    取值校验沿用 `_parse_prices` 的纪律（≥ 0、非 bool、缺项不回落默认价）；零价目纪律
    落在**四格**上（四格全 0 仍须 `zero_marginal: true`；**单格为 0 合法**）。
    """
    matrix = raw.get("price_matrix")
    if matrix is None:
        return {}, []
    if not isinstance(matrix, Mapping) or not matrix:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 的 price_matrix 必须为非空映射（四格：{list(MATRIX_CELLS)}）"
        )
    missing = [cell for cell in MATRIX_CELLS if cell not in matrix]
    unknown = [cell for cell in matrix if cell not in MATRIX_CELLS]
    if missing or unknown:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 的 price_matrix 必须四格齐备（缺 {missing}、多 {unknown}）："
            f"格位键 = <峰谷>_<缓存>，合法取值 {list(MATRIX_CELLS)}——声明即四格，缺格不回落基础价"
        )
    parsed: dict[str, dict[str, float]] = {}
    for cell in MATRIX_CELLS:
        cell_raw = matrix[cell]
        if not isinstance(cell_raw, Mapping):
            raise ProfileConfigError(
                f"档案 {profile_id!r} 的 price_matrix.{cell} 必须为映射"
                f"（{list(_PRICE_KEYS)}），实际 {cell_raw!r}"
            )
        parsed[cell] = {}
        for key in _PRICE_KEYS:
            value = cell_raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ProfileConfigError(
                    f"档案 {profile_id!r} 的 price_matrix.{cell}.{key} 非合法数值：{value!r}"
                    "（必须为 ≥ 0 的数值；缺项即报错，不回落基础价）"
                )
            parsed[cell][key] = float(value)
    zero_marginal = bool(raw.get("zero_marginal", False))
    notes: list[str] = []
    all_zero = all(value == 0.0 for cell in parsed.values() for value in cell.values())
    if all_zero and not zero_marginal:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 的价目矩阵四格全 0 但未声明 zero_marginal: true"
            "——零价目必须显式声明（自建/本地推理：零边际成本仍非免费）"
        )
    if not all_zero and zero_marginal:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 声明了 zero_marginal: true 但价目矩阵含非 0 格位："
            "零边际成本标记与价目矛盾"
        )
    notes.append(
        f"档案 {profile_id!r} 声明两维价目（{list(DECLARED_DIMENSIONS)}）："
        f"格位 {list(MATRIX_CELLS)}；峰谷由渠道日历按调用开始时刻判定（随 budget_tiers 快照冻结）"
    )
    return parsed, notes


def _parse_prices(
    profile_id: str, raw: Mapping[str, Any], *, has_matrix: bool = False
) -> tuple[dict[str, float], bool, list[str]]:
    """价目解析：两值必须齐备；全 0 必须显式 `zero_marginal: true`（不允许"忘了填价目"）。

    声明 `price_matrix` 时基础两键仍须齐备且为合法数值，但零价目纪律改由**四格**承担
    （见 `_parse_price_matrix`）——基础键此时不参与折算。
    """
    prices = raw.get("prices")
    if not isinstance(prices, Mapping):
        raise ProfileConfigError(
            f"档案 {profile_id!r} 缺价目：必须声明 prices.prompt_per_1k / completion_per_1k"
            "（缺价目即报错，不允许静默零成本）"
        )
    parsed: dict[str, float] = {}
    for key in _PRICE_KEYS:
        value = prices.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ProfileConfigError(
                f"档案 {profile_id!r} 的价目字段 {key!r} 非合法数值：{value!r}"
                "（必须为 ≥ 0 的数值；缺项即报错，不回落默认价）"
            )
        parsed[key] = float(value)
    zero_marginal = bool(raw.get("zero_marginal", False))
    notes: list[str] = []
    if has_matrix:
        return parsed, zero_marginal, notes
    if all(value == 0.0 for value in parsed.values()):
        if not zero_marginal:
            raise ProfileConfigError(
                f"档案 {profile_id!r} 价目全 0 但未声明 zero_marginal: true"
                "——零价目必须显式声明（自建/本地推理：零边际成本仍非免费）"
            )
        notes.append(f"档案 {profile_id!r} 为零边际成本档案（记账 0.0；机器/运维成本不在本表）")
    elif zero_marginal:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 声明了 zero_marginal: true 但价目非 0：零边际成本标记与价目矛盾"
        )
    return parsed, zero_marginal, notes


# 请求参数白名单（防注入任意请求字段）：**档案 = 模型 + 请求参数组合**
# `thinking`：思考模式开关 `{type: enabled|disabled}`（厂商默认 enabled + effort=high）
# `reasoning_effort`：思考强度 low|high|max
# 注：**思考模式下 `temperature` 被厂商忽略**（不报错但无效，已如实登记在文档与快照说明中）
_REQUEST_OPTION_WHITELIST: dict[str, tuple[str, ...] | None] = {
    "thinking": ("enabled", "disabled"),
    "reasoning_effort": ("low", "high", "max"),
}


def _parse_request_options(profile_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """档案声明的请求参数白名单校验（可选）：非法键/非法取值一律报错，防注入任意字段。"""
    options = raw.get("request_options")
    if options is None:
        return {}
    if not isinstance(options, Mapping):
        raise ProfileConfigError(
            f"档案 {profile_id!r} 的 request_options 必须为映射，实际 {type(options).__name__}"
        )
    parsed: dict[str, Any] = {}
    for key, value in options.items():
        if key not in _REQUEST_OPTION_WHITELIST:
            raise ProfileConfigError(
                f"档案 {profile_id!r} 的 request_options 出现白名单外的键 {key!r}："
                f"只允许 {sorted(_REQUEST_OPTION_WHITELIST)}（防注入任意请求字段）"
            )
        allowed = _REQUEST_OPTION_WHITELIST[key]
        if key == "thinking":
            if not isinstance(value, Mapping) or str(value.get("type")) not in allowed:
                raise ProfileConfigError(
                    f"档案 {profile_id!r} 的 request_options.thinking 必须为 "
                    f"{{'type': {'|'.join(allowed)}}}，实际 {value!r}"
                )
            unknown = set(value) - {"type"}
            if unknown:
                raise ProfileConfigError(
                    f"档案 {profile_id!r} 的 request_options.thinking 含未知键 {sorted(unknown)}"
                )
        elif str(value) not in allowed:
            raise ProfileConfigError(
                f"档案 {profile_id!r} 的 request_options.{key} 取值非法：{value!r}"
                f"（只允许 {list(allowed)}）"
            )
        parsed[str(key)] = dict(value) if isinstance(value, Mapping) else value
    return parsed


def _deep_copy_options(options: Mapping[str, Any]) -> dict:
    """请求参数深拷贝（快照冻结用；嵌套一层足够表达白名单结构）。"""
    return {
        str(key): dict(value) if isinstance(value, Mapping) else value
        for key, value in options.items()
    }


def _parse_timeout(profile_id: str, raw: Mapping[str, Any]) -> float | None:
    """单请求超时（秒，可选）：**推理模型需要更长**——真实实测：思维链+正文在 30s 内
    回不来（`The read operation timed out`），故按时长在档案里声明（缺省用后端默认 30s）。
    非正数/类型不符即报错（配置错误不静默）。
    """
    value = raw.get("timeout_seconds")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ProfileConfigError(
            f"档案 {profile_id!r} 的 timeout_seconds 非正数或类型不符：{value!r}"
            "（必须为 > 0 的秒数；缺省即用后端默认超时）"
        )
    return float(value)


# ---------------------------------------------------------------------------
# 迁移（C3）：旧扁平配置 → 单档案
# ---------------------------------------------------------------------------


def migrate_legacy(
    config: Mapping[str, Any],
) -> tuple[dict[str, ModelProfile], RoleRouting, list[str]] | None:
    """旧写法（`<agent>.model` + `<agent>.model_prices`）→（单档案，路由，迁移说明）。

    返回 `None` 表示配置里没有旧写法；旧写法存在但缺价目 → `ProfileConfigError`（C1 同纪律）。
    """
    profiles: dict[str, ModelProfile] = {}
    roles: dict[Role, str] = {}
    migration_notes: list[str] = []
    for section_name, section in config.items():
        if not isinstance(section, Mapping) or not isinstance(section.get("model_prices"), Mapping):
            continue
        model_key = next((key for key in _LEGACY_MODEL_KEYS if section.get(key)), None)
        if model_key is None:
            continue
        model = str(section[model_key])
        price_table = section["model_prices"]
        prices = price_table.get(model)
        if not isinstance(prices, Mapping):
            raise ProfileConfigError(
                f"旧写法迁移失败：{section_name}.model_prices 缺少模型 {model!r} 的价目"
                "（旧写法缺价目即报错，与档案校验同纪律）"
            )
        if model not in profiles:
            all_zero = all(float(value) == 0.0 for value in prices.values())
            parsed, zero_marginal, notes = _parse_prices(
                model, {"prices": prices, "zero_marginal": all_zero}
            )
            profiles[model] = ModelProfile(
                profile_id=model,
                model=model,
                api_key_env=LEGACY_API_KEY_ENV,
                prices=parsed,
                base_url_env=LEGACY_BASE_URL_ENV,
                price_note="旧扁平配置迁移（端点/密钥沿用 OPENAI_BASE_URL / OPENAI_API_KEY）",
                zero_marginal=zero_marginal,
                legacy_env=True,
                notes=tuple(notes),
            )
            migration_notes.append(
                f"{section_name}.{model_key} + model_prices → 档案 {model!r}"
                "（端点/凭证名登记为 OPENAI_BASE_URL / OPENAI_API_KEY：沿用旧变量名，"
                "报告标注 legacy_env=true）"
            )
        role = _LEGACY_SECTION_ROLES.get(section_name)
        if role is not None:
            roles[role] = model
    if not profiles:
        return None
    default = roles.get(Role.GENERATION) or sorted(profiles)[0]
    if len(profiles) > 1:
        migration_notes.append(
            f"旧写法未声明默认档案：取 {default!r}（多档案建议改用 llm 段显式声明 default_profile）"
        )
    routing = RoleRouting(
        roles=roles,
        default_profile=default,
        default_reason="legacy_migration",
        notes=(f"路由来自旧扁平配置迁移（档案数 {len(profiles)}）",),
    )
    return profiles, routing, migration_notes


def load_or_migrate(config: Mapping[str, Any]) -> ProfileLoad:
    """统一入口：有 `llm` 段 → 新写法（旧键被忽略，notes 标注）；否则走旧写法迁移。

    两侧都没有旧/新写法 → `ProfileConfigError`（不静默给出空档案）。
    """
    has_section = isinstance(config.get("llm"), Mapping)
    legacy = migrate_legacy(config)
    if has_section:
        profiles, routing, notes = load_profiles(config)
        notes = [*notes, *routing.notes]  # 合并视图：档案级 notes + 路由级 notes（含孤档案提示）
        if legacy is not None:
            notes = [
                *notes,
                "新旧写法并存：以 llm 段为准，旧扁平键被忽略（screenplay.model/model_prices、"
                "promo.default_model/model_prices）",
            ]
        return ProfileLoad(
            profiles=profiles,
            routing=routing,
            notes=tuple(notes),
            source="llm_section",
        )
    if legacy is None:
        raise ProfileConfigError(
            "配置既无 llm 段（档案与角色路由）也无旧扁平写法（<agent>.model + model_prices）："
            "无法确定 LLM 端点与价目——缺项即报错，不静默给出空档案"
        )
    profiles, routing, migration_notes = legacy
    return ProfileLoad(
        profiles=profiles,
        routing=routing,
        notes=tuple(routing.notes),
        migration_notes=tuple(migration_notes),
        source="legacy",
    )


def legacy_price_book_snapshot(price_book: Mapping[str, Mapping[str, float]]) -> ProfileSnapshot:
    """从**旧扁平价目表**合成单档案快照（网关未接档案时的等价现状路径）。

    用于既有调用形态（`LLMGateway(backend, price_book=...)`）——快照如实标注来源为
    `legacy_price_book`，价目逐条取自价目表，端点/凭证名标记为沿用旧变量名。
    """
    profiles = tuple(
        {
            "profile_id": str(model),
            "model": str(model),
            "endpoint": LEGACY_BASE_URL_ENV,
            "endpoint_source": "base_url_env",
            "api_key_env": LEGACY_API_KEY_ENV,
            "prices": {key: float(prices.get(key, 0.0)) for key in _PRICE_KEYS},
            "price_note": "旧扁平价目表（未声明档案；端点/凭证沿用旧变量名）",
            "zero_marginal": all(float(prices.get(key, 0.0)) == 0.0 for key in _PRICE_KEYS),
            "legacy_env": True,
            "timeout_seconds": None,
            "request_options": {},
        }
        for model, prices in price_book.items()
    )
    ids = [entry["profile_id"] for entry in profiles]
    return ProfileSnapshot(
        profiles=profiles,
        default_profile=ids[0] if len(ids) == 1 else "",
        default_reason="legacy_migration",
        roles={},
        notes=("未声明 llm 档案：快照取自旧扁平价目表（单档案等价现状路径）",),
        migration_notes=(),
        source="legacy_price_book",
    )


# ---------------------------------------------------------------------------
# 快照接线助手（功能 016 / T1612）
# ---------------------------------------------------------------------------


def gateway_profile_snapshot(gateway: object) -> dict | None:
    """从网关取档案快照（`LLMGateway.profile_snapshot().to_dict()`）。

    网关未提供该能力（桩网关/未接入档案）时返回 `None`——调用方据此**不落** `llm_profiles`
    键（既有调用形态零变化 = SC-005 单档案等价现状）。
    """
    if gateway is None:
        return None
    provider = getattr(gateway, "profile_snapshot", None)
    if not callable(provider):
        return None
    snapshot = provider()
    to_dict = getattr(snapshot, "to_dict", None)
    return dict(to_dict()) if callable(to_dict) else None


def with_llm_profiles(snapshot: Mapping[str, Any], llm_profiles: Mapping[str, Any] | None) -> dict:
    """把档案快照并入 Agent 的 `config_snapshot`（键 `llm_profiles`）：未提供时不落键。

    历史节点因此绑定"当时的价目口径"——改配置价目只影响新节点（冻结可审计复算）。
    """
    merged = dict(snapshot)
    if llm_profiles is not None:
        merged["llm_profiles"] = dict(llm_profiles)
    return merged
