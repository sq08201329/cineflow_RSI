"""进化对比报告（US3 / T224，FR-010/FR-011）。

基线与变体两手写策略版本（policies/history/promo/，代码即版本）分别在
002 模拟器池中回放，产出 EvolutionReport JSON：双曲线、成本、
pareto_auc 与并行惩罚奖励分量、谱系引用（policy_version → pool_tree_ids）。
一期为人工策略变体（做梦层自动进化属周 11~12，宪章原则六）。
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.replay.pool import SimulatorPool
from core.tree.models import DiscoveryTree
from core.tree.store import TreeStore
from policies.base import Budget
from policies.versioning import policy_version

VERDICT_MARGIN = 0.01  # 奖励差显著阈值：小于此判 inconclusive


def pareto_auc(best_score_curve: list[float], probe_count: int) -> float:
    """奖励分量：逐轮最优得分曲线的均值（曲线下的归一化面积）。"""
    if not best_score_curve:
        return 0.0
    return sum(best_score_curve) / len(best_score_curve)


def parallel_penalty(effective_sequential_rounds: float, probe_count: int) -> float:
    """奖励分量：并行惩罚 = 有效串行轮 / max(probe 数, 1)（宪章奖励函数口径）。"""
    return effective_sequential_rounds / max(probe_count, 1)


def decide_verdict(baseline_reward: float, variant_reward: float) -> str:
    """双版本奖励对比判定（显著阈值内判 inconclusive）。"""
    diff = variant_reward - baseline_reward
    if diff > VERDICT_MARGIN:
        return "variant_better"
    if diff < -VERDICT_MARGIN:
        return "baseline_better"
    return "inconclusive"


@dataclass(frozen=True)
class VariantResult:
    """单策略版本的回放结果与奖励分量。"""

    policy_version: str
    best_score_curve: list[float]
    probe_count: int
    effective_sequential_rounds: float
    total_cost: dict
    reward_parts: dict
    reward: float


@dataclass(frozen=True)
class EvolutionReport:
    """进化对比报告（data-model EvolutionReport schema）。"""

    agent_id: str
    generated_at: str
    pool_tree_ids: list[str]
    variants: list[VariantResult]
    verdict: str
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _replay_policy_source(
    source: str, pool: SimulatorPool, *, worker_count: int, max_probes: int, lambda_: float
) -> VariantResult:
    """在池内回放一份手写策略源码（宿主内 exec：一手写可信代码，非做梦产出）。"""
    namespace: dict = {}
    exec(compile(source, "<policy>", "exec"), namespace)  # noqa: S102 - 一手写策略
    policy = namespace["Policy"]()
    simulator = pool.build(
        worker_count=worker_count, budget=Budget(max_probes=max_probes), latency_quantum_ms=0
    )
    final_node_id = policy.solve(simulator, simulator.budget)
    version = policy_version(source)
    simulator.finalize(policy_version=version, final_node_id=final_node_id)
    trajectory = simulator.trajectory()

    auc = pareto_auc(trajectory.best_score_curve, trajectory.probe_count)
    penalty = parallel_penalty(trajectory.effective_sequential_rounds, trajectory.probe_count)
    return VariantResult(
        policy_version=version,
        best_score_curve=trajectory.best_score_curve,
        probe_count=trajectory.probe_count,
        effective_sequential_rounds=trajectory.effective_sequential_rounds,
        total_cost=asdict(trajectory.total_cost),
        reward_parts={"pareto_auc": auc, "parallel_penalty": penalty},
        reward=auc - lambda_ * penalty,
    )


def generate_evolution_report(
    store: TreeStore,
    trees: list[DiscoveryTree],
    policy_paths: list[Path],
    *,
    worker_count: int = 4,
    max_probes: int = 8,
    lambda_: float = 0.5,
) -> EvolutionReport:
    """对基线与变体（policy_paths[0] 为基线）分别回放并产出对比报告。"""
    if len(policy_paths) < 2:
        raise ValueError("进化报告至少需要基线与变体两个策略版本")
    pool = SimulatorPool(store)
    for tree in trees:
        pool.add_tree(tree)

    variants = [
        _replay_policy_source(
            path.read_text(encoding="utf-8"),
            pool,
            worker_count=worker_count,
            max_probes=max_probes,
            lambda_=lambda_,
        )
        for path in policy_paths
    ]
    verdict = decide_verdict(variants[0].reward, variants[1].reward)
    return EvolutionReport(
        agent_id=trees[0].agent_id if trees else "unknown",
        generated_at=datetime.now().astimezone().isoformat(),
        pool_tree_ids=[tree.tree_id for tree in trees],
        variants=variants,
        verdict=verdict,
        notes={"lambda": lambda_, "verdict_margin": VERDICT_MARGIN},
    )
