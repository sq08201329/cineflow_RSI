"""观测投影：Observation / ProbeResult 与字段白名单（FR-002，不变量 1）。

Observation 是已揭示节点对策略可见的唯一投影：只含观测上下文白名单字段，
绝不携带未揭示节点的任何信息。白名单由树 config_snapshot 的
"observation_fields" 清单决定（缺省为空——默认零泄露）。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from core.tree.models import CostRecord, TreeNode

# 观测字段白名单在 config_snapshot 中的配置键
OBSERVATION_FIELDS_KEY = "observation_fields"


@dataclass(frozen=True)
class Observation:
    """已揭示节点的观测投影。"""

    node_id: str
    depth: int
    score: float | None  # FAILED 节点为 None
    cost: CostRecord
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    """一次 probe 的产出：命中真实历史（ok）或 UNKNOWN（不得分、零信息）。"""

    status: Literal["ok", "unknown"]
    nodes: list[Observation] = field(default_factory=list)
    virtual_cost: CostRecord = field(default_factory=CostRecord)

    @classmethod
    def unknown(cls) -> "ProbeResult":
        """无匹配走法：策略不得获得得分与任何节点信息（FR-004）。"""
        return cls(status="unknown")


def project_observation(node: TreeNode, whitelist: Iterable[str]) -> Observation:
    """把已揭示节点投影为 Observation：字段白名单过滤，未揭示信息零携带。"""
    allowed = set(whitelist)
    return Observation(
        node_id=node.node_id,
        depth=node.depth,
        score=node.score,
        cost=node.cost,
        fields={k: v for k, v in node.observation_context.items() if k in allowed},
    )


def observation_whitelist(config_snapshot: dict) -> tuple[str, ...]:
    """从树配置快照读取观测字段白名单（缺省为空——默认零泄露）。"""
    fields = config_snapshot.get(OBSERVATION_FIELDS_KEY, ())
    if not isinstance(fields, (list, tuple)):
        return ()
    return tuple(str(f) for f in fields)


def sum_costs(records: Iterable[CostRecord]) -> CostRecord:
    """虚拟成本合计：一批揭示节点的成本逐项相加。"""
    total = CostRecord()
    for record in records:
        total = CostRecord(
            llm_calls=total.llm_calls + record.llm_calls,
            llm_tokens=total.llm_tokens + record.llm_tokens,
            generation_api_calls=total.generation_api_calls + record.generation_api_calls,
            generation_api_cost_usd=(
                total.generation_api_cost_usd + record.generation_api_cost_usd
            ),
            human_review_minutes=total.human_review_minutes + record.human_review_minutes,
            wall_clock_seconds=total.wall_clock_seconds + record.wall_clock_seconds,
        )
    return total
