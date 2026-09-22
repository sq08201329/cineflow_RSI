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


def host_of_url(url: str) -> str:
    """URL → `scheme://host[:port]`（快照只记 host：不含路径/查询串/密钥）。"""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        raise ProfileConfigError(f"档案端点 URL 形态非法（期望 http(s)://host[:port]）：{url!r}")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


@dataclass(frozen=True)
class ModelProfile:
    """一条模型档案（端点 + 凭证变量名 + 价目 + 口径备注）。"""

    profile_id: str
    api_key_env: str
    prices: Mapping[str, float]
    base_url: str | None = None
    base_url_env: str | None = None
    price_note: str = ""
    zero_marginal: bool = False
    legacy_env: bool = False
    notes: tuple[str, ...] = ()

    @property
    def endpoint_ref(self) -> str:
        """快照里的端点引用：URL 形态只记 host；env 形态记变量名（**永不记密钥**）。"""
        if self.base_url:
            return host_of_url(self.base_url)
        return str(self.base_url_env)

    def cost_usd(self, *, prompt_tokens: int, completion_tokens: int) -> float:
        """按档案价目折算（记账口径 = 价目表；**记账 ≠ 厂商账单**）。"""
        return prompt_tokens / 1000 * float(
            self.prices["prompt_per_1k"]
        ) + completion_tokens / 1000 * float(self.prices["completion_per_1k"])

    def to_snapshot(self) -> dict:
        """档案快照条目（价目 + 备注 + 端点 host/变量名 + 标记；**无密钥**）。"""
        return {
            "profile_id": self.profile_id,
            "endpoint": self.endpoint_ref,
            "endpoint_source": "base_url" if self.base_url else "base_url_env",
            "api_key_env": self.api_key_env,
            "prices": {key: float(self.prices[key]) for key in _PRICE_KEYS},
            "price_note": self.price_note,
            "zero_marginal": self.zero_marginal,
            "legacy_env": self.legacy_env,
        }


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
    prices, zero_marginal, price_notes = _parse_prices(profile_id, raw)
    price_note = raw.get("price_note")
    notes = list(price_notes)
    if not isinstance(price_note, str) or not price_note:
        price_note = ""
        notes.append(f"档案 {profile_id!r} 缺 price_note（价目口径备注）：建议补上以便审计复算")
    return (
        ModelProfile(
            profile_id=profile_id,
            api_key_env=api_key_env,
            prices=prices,
            base_url=str(base_url) if base_url else None,
            base_url_env=str(base_url_env) if base_url_env else None,
            price_note=price_note,
            zero_marginal=zero_marginal,
            legacy_env=bool(raw.get("legacy_env", False)),
            notes=tuple(notes),
        ),
        notes,
    )


def _parse_prices(
    profile_id: str, raw: Mapping[str, Any]
) -> tuple[dict[str, float], bool, list[str]]:
    """价目解析：两值必须齐备；全 0 必须显式 `zero_marginal: true`（不允许"忘了填价目"）。"""
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
            "endpoint": LEGACY_BASE_URL_ENV,
            "endpoint_source": "base_url_env",
            "api_key_env": LEGACY_API_KEY_ENV,
            "prices": {key: float(prices.get(key, 0.0)) for key in _PRICE_KEYS},
            "price_note": "旧扁平价目表（未声明档案；端点/凭证沿用旧变量名）",
            "zero_marginal": all(float(prices.get(key, 0.0)) == 0.0 for key in _PRICE_KEYS),
            "legacy_env": True,
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
