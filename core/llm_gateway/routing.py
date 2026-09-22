"""LLM 角色路由（功能 016 / T1608）：固定角色枚举 → 档案（单层映射 + 默认回落）。

## 角色枚举的来源（**按既有调用点盘点后定稿**，2026-09-22 核对）

`Role` 的成员不是新造概念，而是把仓库里**全部** `LLMGateway.chat(...)` 调用点归类后固定：

| 角色 | 调用点（现状） | 语义 |
| --- | --- | --- |
| `generation` | `agents/screenplay/loop.py`（剧本三阶段生成） | 内容生成 |
| `judge` | 四处：`agents/screenplay/evaluators/dramatic_tension.py`、
`agents/storyboard/evaluators/script_fit.py`、`agents/visual/evaluators/cinematic.py`、
`agents/editing/evaluators/narrative.py` | LLM judge 委员会（四处共用一名） |
| `dreaming_candidates` | `dreaming/candidates.py`（候选策略生成） | 做梦层候选生成 |
| `copywriting` | `agents/promo/material.py`（物料文案） | 宣发文案生成 |

盘点结论：**共 7 个调用点、4 个角色**（与规格列举一致，无需扩充）；新增调用点必须先
扩充本枚举（枚举外角色在**配置解析期**即报错，见 `resolve_routing`）——避免"judge 拼错
静默降级到默认档案"这类假阴性。

## 路由纪律

- **单层映射**：`roles: {角色: 档案 id}`；值不是字符串（多层/嵌套）即报错；映射指向不存在
  的档案即报错；映射值等于角色名自身（`judge: judge`）视为**自指**并报错；
- **默认档案**：档案数 = 1 → 自动认定（`reason="single_profile"`，notes 标注）；≥ 2 必须显式
  `default_profile`，缺即报错；默认档案不存在即报错；
- **未映射的枚举内角色** → 回落默认档案（`reason="default_fallback"`，US2 的 C4）；
- **未被任何角色引用的档案** → 允许，但记入 notes（提示可能笔误）；
- 角色取值只在这里定义（配置校验、文档、调用点共用同一枚举）。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProfileConfigError(Exception):
    """档案/角色路由配置错误（缺项、枚举外角色、映射非法）——不静默回落。"""


class Role(StrEnum):
    """LLM 调用角色（固定枚举；来源见模块 docstring 的调用点盘点）。"""

    GENERATION = "generation"
    JUDGE = "judge"
    DREAMING_CANDIDATES = "dreaming_candidates"
    COPYWRITING = "copywriting"


def role_values() -> tuple[str, ...]:
    """合法角色取值（报错文案与配置校验共用；顺序固定便于断言）。"""
    return tuple(member.value for member in Role)


@dataclass(frozen=True)
class RoleRouting:
    """角色 → 档案的路由结果（单层；默认档案含认定方式与理由）。"""

    roles: Mapping[Role, str] = field(default_factory=dict)
    default_profile: str = ""
    default_reason: str = "declared"  # declared | single_profile | legacy_migration
    notes: tuple[str, ...] = ()

    def profile_for(self, role: Role) -> str | None:
        """角色命中的档案（未映射 → None，由 `route` 决定回落）。"""
        return self.roles.get(role)


@dataclass(frozen=True)
class RouteDecision:
    """一次调用的路由决策（可追溯：角色 / 档案 / 理由 / 快照引用）。"""

    role: Role
    profile_id: str
    reason: str  # role_mapping | default_fallback
    profile_snapshot_ref: str = ""

    def to_dict(self) -> dict:
        return {
            "role": str(self.role),
            "profile_id": self.profile_id,
            "reason": self.reason,
            "profile_snapshot_ref": self.profile_snapshot_ref,
        }


def resolve_routing(
    profiles: Mapping[str, Any],
    raw_roles: Mapping[str, Any] | None,
    default_profile: str | None,
) -> RoleRouting:
    """校验并归一角色映射（C2）：枚举校验、映射单层、档案存在性、默认档案规则、孤档案提示。

    `profiles` 只按**键集**使用（档案 id），与 `ModelProfile` 无耦合（避免模块循环依赖）。
    """
    profile_ids = tuple(profiles)
    if not profile_ids:
        raise ProfileConfigError("llm.profiles 为空：至少需要一条档案（缺项即报错，不回落默认）")
    notes = list(_validate_default(profile_ids, default_profile))
    routing, default_reason = _parse_roles(raw_roles, profile_ids, default_profile)
    default = _default_of(profile_ids, default_profile)
    notes.extend(_orphan_notes(routing, profile_ids, default))
    if default_reason == "single_profile":
        notes.insert(0, f"默认档案自动认定：唯一档案 {default!r}")
    return RoleRouting(
        roles=routing,
        default_profile=default,
        default_reason=default_reason,
        notes=tuple(notes),
    )


def _default_of(profile_ids: tuple[str, ...], default_profile: str | None) -> str:
    return default_profile or (profile_ids[0] if len(profile_ids) == 1 else "")


def _validate_default(profile_ids: tuple[str, ...], default_profile: str | None) -> list[str]:
    """默认档案规则：单档案自动认定 / 多档案必须显式 / 声明值必须存在。"""
    if default_profile is None:
        if len(profile_ids) == 1:
            return []
        raise ProfileConfigError(
            f"llm.default_profile 缺失：档案数为 {len(profile_ids)}（{sorted(profile_ids)}）时"
            "必须显式声明默认档案（单档案才可省略并自动认定）"
        )
    if default_profile not in profile_ids:
        raise ProfileConfigError(
            f"llm.default_profile 指向不存在的档案 {default_profile!r}："
            f"已声明档案为 {sorted(profile_ids)}"
        )
    return []


def _parse_roles(
    raw_roles: Mapping[str, Any] | None, profile_ids: tuple[str, ...], default_profile: str | None
) -> tuple[dict[Role, str], str]:
    if raw_roles is None:
        raw_roles = {}
    if not isinstance(raw_roles, Mapping):
        raise ProfileConfigError(
            f"llm.roles 必须是单层映射（角色 → 档案 id），实际为 {type(raw_roles).__name__}"
        )
    routing: dict[Role, str] = {}
    for raw_role, target in raw_roles.items():
        try:
            role = Role(str(raw_role))
        except ValueError as exc:
            raise ProfileConfigError(
                f"llm.roles 出现枚举外角色 {raw_role!r}：合法角色为 {list(role_values())}"
                "（拼错即报错，防静默降级到默认档案）"
            ) from exc
        if isinstance(target, Mapping):
            raise ProfileConfigError(
                f"llm.roles[{role}] 是嵌套映射（多层路由不被支持）：值为档案 id 字符串，"
                f"实际 {type(target).__name__}——路由必须单层、可预测、可审计"
            )
        if not isinstance(target, str) or not target:
            raise ProfileConfigError(f"llm.roles[{role}] 必须为非空档案 id 字符串，实际 {target!r}")
        if target == str(role):
            raise ProfileConfigError(
                f"llm.roles[{role}] 指向自身（自指）：值必须是 llm.profiles 里的档案 id，而非角色名"
            )
        if target not in profile_ids:
            raise ProfileConfigError(
                f"llm.roles[{role}] 指向不存在的档案 {target!r}：已声明档案为 {sorted(profile_ids)}"
            )
        routing[role] = target
    default_reason = "declared" if default_profile else "single_profile"
    return routing, default_reason


def _orphan_notes(
    routing: Mapping[Role, str], profile_ids: tuple[str, ...], default_profile: str | None
) -> list[str]:
    referenced = set(routing.values())
    orphans = sorted(pid for pid in profile_ids if pid not in referenced and pid != default_profile)
    if orphans:
        return [f"未被任何角色引用的档案（可能笔误）：{orphans}"]
    return []


def route(role: Role, routing: RoleRouting, *, profile_snapshot_ref: str = "") -> RouteDecision:
    """一次调用的路由决策（C4）：命中映射 → `role_mapping`；未映射（枚举内）→ 默认回落。

    **不静默回落到其它档案**：未映射时只走 `routing.default_profile`（若为空即配置错误，
    在解析期已被拒），永不"猜"另一条档案。
    """
    if not isinstance(role, Role):
        raise ProfileConfigError(
            f"角色必须是 Role 枚举成员，实际 {role!r}：合法角色为 {list(role_values())}"
        )
    mapped = routing.profile_for(role)
    if mapped is not None:
        return RouteDecision(
            role=role,
            profile_id=mapped,
            reason="role_mapping",
            profile_snapshot_ref=profile_snapshot_ref,
        )
    if not routing.default_profile:
        raise ProfileConfigError(
            f"角色 {role} 未映射且无默认档案（{routing.default_reason}）：配置解析期应已拒绝"
        )
    return RouteDecision(
        role=role,
        profile_id=routing.default_profile,
        reason="default_fallback",
        profile_snapshot_ref=profile_snapshot_ref,
    )
