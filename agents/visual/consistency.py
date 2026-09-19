"""一致性验收（US3 / T329，contracts/consistency.md；发布阻塞门禁）。

对冻结树的全部已评估节点：按 config_snapshot 冻结的评估器版本组合，
从注册中心取冻结版本实例对同一工件哈希重算得分，与落盘值逐字节对照
（得分已 quantize 定点归一）；一致率必须 100%。τ 门禁分档
（data-model §5）：池内 < 2 棵树时 tau=null 只验重算一致率；≥2 棵时
真实 vs 回放（其他树）得分序列复用 002 verify_unbiasedness。
"""

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agents.visual.frames import probe_clip, sample_frames
from core.evaluators.base import ArtifactRef, EvalResult
from core.evaluators.composite import composite_score_versioned
from core.evaluators.quantize import quantize_score
from core.evaluators.registry import Registry
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.artifacts import ArtifactStore
from core.tree.models import NodeStatus
from core.tree.store import TreeStore
from policies.base import Budget


@dataclass(frozen=True)
class ConsistencyReport:
    """一致性验收报告（data-model §1 schema）。"""

    tree_id: str
    checked_nodes: int
    consistent_rate: float
    drift_nodes: list[dict]
    tau: float | None
    threshold: float
    verdict: str  # "pass" | "reject"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _evaluated_clip_nodes(store: TreeStore, tree_id: str):
    """树上全部已评估片段节点（锚点根与 FAILED 不参与重算对照）。"""
    return [
        node
        for node in store.nodes_of(tree_id)
        if node.parent_id is not None
        and node.status is NodeStatus.EVALUATED
        and node.eval_breakdown
    ]


def _recompute_node(node, snapshot, registry, artifacts) -> tuple[dict, float]:
    """按冻结版本组合对同一工件重算：逐评估器明细 + 合成得分。"""
    blob = artifacts.get(node.artifact_hash)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)  # 先出 with 块（关闭 flush）再探测
    try:
        meta = probe_clip(tmp_path)
        samples = sample_frames(tmp_path, snapshot["frame_sampling"])
    finally:
        tmp_path.unlink(missing_ok=True)

    ctx = {
        "probe_meta": meta,
        "samples": samples,
        "gen_params": node.observation_context["gen_params"],
    }
    artifact_ref = ArtifactRef(artifact_hash=node.artifact_hash)
    recomputed: dict[str, EvalResult] = {}
    for key in node.eval_breakdown:
        evaluator_id, version = key.rsplit("@", 1)
        evaluator = registry.get(evaluator_id, version)  # 冻结版本实例
        recomputed[key] = evaluator.evaluate(artifact_ref, ctx)

    weights = {
        k: (0.0 if str(v).lower() == "gate" else float(v))
        for k, v in snapshot["evaluator_weights"].items()
    }
    # 合规门禁短路的节点明细缺 judge：按交集权重合成（与 loop 落盘口径一致）
    effective = {k: v for k, v in weights.items() if k in {b.rsplit("@", 1)[0] for b in recomputed}}
    if any(k.startswith("rule.") and r.score == 0.0 for k, r in recomputed.items()):
        score = 0.0
    else:
        # 与 loop 落盘口径一致：合成得分同样经 quantize 定点归一后对照
        score = quantize_score(composite_score_versioned(recomputed, effective))
    return recomputed, score


def verify_consistency(
    tree_id: str,
    store: TreeStore,
    artifacts: ArtifactStore,
    registry: Registry,
    *,
    tau_threshold: float = 0.95,
) -> ConsistencyReport:
    """一致性验收：重算逐字节对照 + τ 分档门禁。"""
    snapshot = next(
        t for t in store.trees_by(agent_id="visual") if t.tree_id == tree_id
    ).config_snapshot
    notes: list[str] = []
    nodes = _evaluated_clip_nodes(store, tree_id)

    if len(nodes) < 2:
        notes.append(f"样本不足：已评估片段节点 {len(nodes)} < 2，无法验收")
        return ConsistencyReport(
            tree_id=tree_id,
            checked_nodes=len(nodes),
            consistent_rate=0.0,
            drift_nodes=[],
            tau=None,
            threshold=tau_threshold,
            verdict="reject",
            notes=notes,
        )

    drift_nodes: list[dict] = []
    drifted_node_ids: set[str] = set()
    for node in nodes:
        recomputed, score = _recompute_node(node, snapshot, registry, artifacts)
        for key, result in recomputed.items():
            stored = node.eval_breakdown[key]["score"]
            if result.score != stored:  # 逐字节对照（得分已定点归一）
                drift_nodes.append(
                    {
                        "node_id": node.node_id,
                        "evaluator_key": key,
                        "stored": stored,
                        "recomputed": result.score,
                    }
                )
                drifted_node_ids.add(node.node_id)
        if score != node.score:
            drift_nodes.append(
                {
                    "node_id": node.node_id,
                    "evaluator_key": "<composite>",
                    "stored": node.score,
                    "recomputed": score,
                }
            )
            drifted_node_ids.add(node.node_id)

    checked = len(nodes)
    consistent_rate = (checked - len(drifted_node_ids)) / checked

    # τ 门禁分档（data-model §5）：池内 < 2 棵树只验重算一致率
    pool_trees = store.trees_by(agent_id="visual")
    tau: float | None = None
    tau_ok = True
    if len(pool_trees) < 2:
        notes.append("首轮：池内不足 2 棵树，τ 门禁待第二棵树入池后启用（tau=null）")
    else:
        others = [t for t in pool_trees if t.tree_id != tree_id]
        pool = SimulatorPool(store)
        for other in others:
            pool.add_tree(other)
        simulator = pool.build(
            worker_count=4, budget=Budget(max_probes=len(nodes) * 2), latency_quantum_ms=0
        )
        real_scores = [node.score for node in nodes]
        replay_scores = []
        for node in nodes:
            hit = simulator.probe(others[0].root_id, node.observation_context["gen_params"])
            replay_scores.append(hit.nodes[0].score if hit.status == "ok" and hit.nodes else None)
        report = verify_unbiasedness(real_scores, replay_scores, threshold=tau_threshold)
        tau = report.tau
        tau_ok = report.verdict == "pass"
        notes.append(f"τ 门禁：{report.notes}")

    verdict = "pass" if consistent_rate == 1.0 and tau_ok else "reject"
    return ConsistencyReport(
        tree_id=tree_id,
        checked_nodes=checked,
        consistent_rate=consistent_rate,
        drift_nodes=drift_nodes,
        tau=tau,
        threshold=tau_threshold,
        verdict=verdict,
        notes=notes,
    )
