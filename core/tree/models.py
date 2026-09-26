"""发现树领域模型（frozen dataclass，immutable 第一道防线）。

校验规则见 specs/001-tree-evaluators/data-model.md §1：
score/status 一致性、depth 递推、非空字段、artifact_hash 格式、成本分量非负。
PLANNED 仅允许内存态构造，落盘拒绝在 store 层执行。
"""

import re
import time
from dataclasses import dataclass, field
from enum import StrEnum

from uuid_utils import uuid7

from core.tree.errors import ValidationError

_ARTIFACT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class NodeStatus(StrEnum):
    """节点状态机：PLANNED（内存态）→ EVALUATED / FAILED（终态，无反向迁移）。"""

    PLANNED = "planned"
    EVALUATED = "evaluated"
    FAILED = "failed"


def new_id() -> str:
    """生成 RFC 9562 uuid7（时间有序），作为 tree_id / node_id。"""
    return str(uuid7())


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串")


_LLM_BREAKDOWN_ENTRY_KEYS = ("calls", "prompt_tokens", "completion_tokens", "cost_usd")


def _validate_llm_breakdown(breakdown: dict, llm_calls: int, llm_tokens: int) -> None:
    """llm_breakdown 结构校验（功能 022 / plan D2）。

    形态 `{role: {profile_id: {calls, prompt_tokens, completion_tokens, cost_usd}}}`，
    对齐网关 cost_breakdown() 口径。只做结构校验：键为非空字符串、各分量数值 ≥ 0、
    Σ calls ≤ llm_calls 且 Σ tokens ≤ llm_tokens；不耦合 Role 枚举（原则五），
    也不校验 cost_usd 总额（六字段无对应标量，口径局限如实登记）。
    """
    if not isinstance(breakdown, dict):
        raise ValidationError(f"CostRecord.llm_breakdown 必须为 dict，实际为 {breakdown!r}")
    total_calls = 0
    total_tokens = 0
    for role, profiles in breakdown.items():
        _require_non_empty(role, "llm_breakdown 角色键")
        if not isinstance(profiles, dict):
            raise ValidationError(f"llm_breakdown[{role!r}] 必须为 dict，实际为 {profiles!r}")
        for profile_id, entry in profiles.items():
            _require_non_empty(profile_id, "llm_breakdown 档案键")
            if not isinstance(entry, dict):
                raise ValidationError(
                    f"llm_breakdown[{role!r}][{profile_id!r}] 必须为 dict，实际为 {entry!r}"
                )
            for key in _LLM_BREAKDOWN_ENTRY_KEYS:
                if key not in entry:
                    raise ValidationError(
                        f"llm_breakdown[{role!r}][{profile_id!r}] 缺键 {key!r}（四键必须齐全）"
                    )
                value = entry[key]
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                    raise ValidationError(
                        f"llm_breakdown[{role!r}][{profile_id!r}].{key} 必须为 ≥ 0 的数值，"
                        f"实际为 {value!r}"
                    )
            total_calls += entry["calls"]
            total_tokens += entry["prompt_tokens"] + entry["completion_tokens"]
    if total_calls > llm_calls:
        raise ValidationError(
            f"llm_breakdown Σ calls={total_calls} 超过 llm_calls={llm_calls}（分解不得超过总量）"
        )
    if total_tokens > llm_tokens:
        raise ValidationError(
            f"llm_breakdown Σ tokens={total_tokens} 超过 llm_tokens={llm_tokens}"
            "（分解不得超过总量）"
        )


@dataclass(frozen=True)
class CostRecord:
    """成本记录：每个节点必须入账（含 FAILED 节点），全字段 ≥ 0。

    llm_breakdown 为 022 新增扩展字段（按扩展更新）：LLM 腿「角色 × 档案」分解，
    历史行 JSON 缺此键时读回默认空映射（展示层标「未标定」，禁回填）。
    """

    llm_calls: int = 0
    llm_tokens: int = 0
    generation_api_calls: int = 0
    generation_api_cost_usd: float = 0.0
    human_review_minutes: float = 0.0
    wall_clock_seconds: float = 0.0
    llm_breakdown: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for f in (
            "llm_calls",
            "llm_tokens",
            "generation_api_calls",
            "generation_api_cost_usd",
            "human_review_minutes",
            "wall_clock_seconds",
        ):
            value = getattr(self, f)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValidationError(f"CostRecord.{f} 必须为 ≥ 0 的数值，实际为 {value!r}")
        _validate_llm_breakdown(self.llm_breakdown, self.llm_calls, self.llm_tokens)


@dataclass(frozen=True)
class TreeNode:
    """发现树节点：一经落盘即 immutable（本 dataclass frozen + 存储层触发器双保险）。"""

    node_id: str
    tree_id: str
    parent_id: str | None
    depth: int
    agent_id: str
    policy_version: str
    prompt: str
    observation_context: dict
    artifact_hash: str
    eval_breakdown: dict
    score: float | None
    cost: CostRecord
    status: NodeStatus
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        _require_non_empty(self.node_id, "node_id")
        _require_non_empty(self.tree_id, "tree_id")
        _require_non_empty(self.agent_id, "agent_id")
        _require_non_empty(self.policy_version, "policy_version")

        if not isinstance(self.status, NodeStatus):
            object.__setattr__(self, "status", NodeStatus(self.status))

        # depth 递推的模型层部分：根 depth 恒为 0，非根 depth ≥ 1；
        # "非根 = 父 depth + 1" 的完整校验需父节点在场，由 store.append_node 执行
        if not isinstance(self.depth, int) or isinstance(self.depth, bool) or self.depth < 0:
            raise ValidationError(f"depth 必须为 ≥ 0 的整数，实际为 {self.depth!r}")
        if self.parent_id is None and self.depth != 0:
            raise ValidationError("根节点（parent_id 为 None）depth 必须为 0")
        if self.parent_id is not None and self.depth == 0:
            raise ValidationError("非根节点 depth 必须 ≥ 1")

        if not _ARTIFACT_HASH_RE.match(self.artifact_hash):
            raise ValidationError("artifact_hash 必须为 64 位小写十六进制（BLAKE3）")

        # score/status 一致性（状态机终态约束）
        if self.status is NodeStatus.EVALUATED:
            if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
                raise ValidationError("EVALUATED 节点 score 必填")
            if not 0.0 <= self.score <= 1.0:
                raise ValidationError(f"score 必须 ∈ [0,1]，实际为 {self.score}")
        elif self.score is not None:
            raise ValidationError(f"{self.status.value} 节点 score 必须为 None")

        if not isinstance(self.cost, CostRecord):
            raise ValidationError("cost 必须为 CostRecord（每个节点必须入账）")

    @classmethod
    def child_of(cls, parent: "TreeNode", **fields) -> "TreeNode":
        """构造子节点：tree_id/parent_id/depth 由父节点强制继承，显式传入即拒绝。"""
        for reserved in ("tree_id", "parent_id", "depth"):
            if reserved in fields:
                raise ValidationError(f"child_of 不允许显式传入 {reserved}（由父节点递推）")
        return cls(
            tree_id=parent.tree_id,
            parent_id=parent.node_id,
            depth=parent.depth + 1,
            **fields,
        )


@dataclass(frozen=True)
class DiscoveryTree:
    """发现树：config_snapshot 落盘即冻结（评估器版本组合 + 权重全量快照）。"""

    tree_id: str
    project_id: str
    agent_id: str
    policy_version: str
    root_id: str
    node_ids: list[str]
    config_snapshot: dict

    def __post_init__(self) -> None:
        _require_non_empty(self.tree_id, "tree_id")
        _require_non_empty(self.project_id, "project_id")
        _require_non_empty(self.agent_id, "agent_id")
        _require_non_empty(self.policy_version, "policy_version")
        if not isinstance(self.config_snapshot, dict) or not self.config_snapshot:
            raise ValidationError("config_snapshot 必须为非空 dict（快照随树冻结，不得为空）")
