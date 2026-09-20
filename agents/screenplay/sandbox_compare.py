"""回放沙盘对比报告（合同 screenplay-degraded.md C14，FR-009）。

人工提交的新版本 vs 当前部署版本在**模拟器池上回放对比**（零 LLM、零生成——回放只读
历史节点，原则三）：逐树得分、分项评估器差异（策略最终落点节点的 eval_breakdown）、
pareto_auc 曲线（复用 `dreaming/reward.pareto_auc` 权威口径）、UNKNOWN 覆盖说明。

诚实边界（原则六）：
- 回放命中 UNKNOWN（该树无历史覆盖）→ 该树记 0 分并提示扩大线上记录，**不编造**；
- 新版本全劣 → 报告如实呈现（`verdict=deployed_better`）并建议保留现版本，
  不做"矮子里拔将军"；
- 报告只增不改（`{comparison_id}.json` 落盘一次），采纳/拒绝的留痕在 adoption.py。
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agents.screenplay.policy_versions import load_policy_source
from core.replay.pool import SimulatorPool
from dreaming.reward import pareto_auc

AGENT_ID = "screenplay"
DEFAULT_COMPARISON_DIR = Path("screenplay/comparisons")
UNKNOWN_NOTE = "该树回放 UNKNOWN（无历史覆盖）：记 0 分，请扩大线上记录（不编造）"


@dataclass(frozen=True)
class ReplayComparison:
    """回放对比报告（C14）：逐树得分 / 分项差异 / pareto 曲线与 AUC / UNKNOWN 说明。"""

    comparison_id: str
    agent_id: str
    new_version: str
    deployed_version: str
    per_tree: list[dict]
    per_evaluator: list[dict]
    pareto_curve: dict
    pareto_auc: dict
    mean_score: dict
    unknown_trees: list[str]
    verdict: str  # new_better | deployed_better | inconclusive
    note: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(frozen=True)
class _PolicyReplay:
    """单策略回放结果（池级曲线 + 逐树得分 + 分项明细）。"""

    curve: list[float]
    per_tree: list[dict]
    per_evaluator: dict[str, float] = field(default_factory=dict)


def _breakdown_of(store, node_id: str | None) -> dict[str, float]:
    """策略最终落点节点的分项明细（evaluator_id → score；多版本同 id 取均值）。"""
    if node_id is None:
        return {}
    try:
        node = store.get_node(node_id)
    except Exception:  # noqa: BLE001 - 节点缺失如实返回空（不编造分项）
        return {}
    grouped: dict[str, list[float]] = {}
    for key, fragment in (node.eval_breakdown or {}).items():
        base = key.rsplit("@", 1)[0]
        grouped.setdefault(base, []).append(float(fragment["score"]))
    return {base: sum(scores) / len(scores) for base, scores in grouped.items()}


def _replay_source(
    source: str,
    *,
    trees,
    store,
    replay_fn,
) -> _PolicyReplay:
    """回放一个策略：池级轨迹（全池）+ 逐树单池轨迹（逐树得分与分项差异）。"""
    pool = SimulatorPool(store)
    for tree in trees:
        pool.add_tree(tree)
    trajectory = replay_fn(source, pool)
    per_tree: list[dict] = []
    grouped: dict[str, list[float]] = {}
    for tree in trees:
        tree_pool = SimulatorPool(store)
        tree_pool.add_tree(tree)
        try:
            tree_trajectory = replay_fn(source, tree_pool)
        except Exception as exc:  # noqa: BLE001 - 单树回放失败如实记 0 分注明
            per_tree.append(
                {
                    "tree_id": tree.tree_id,
                    "score": 0.0,
                    "curve": [],
                    "auc": 0.0,
                    "unknown": True,
                    "note": f"回放失败：{exc}",
                }
            )
            continue
        curve = list(tree_trajectory.best_score_curve)
        unknown = not curve
        breakdown = _breakdown_of(store, tree_trajectory.final_node_id)
        for evaluator_id, score in breakdown.items():
            grouped.setdefault(evaluator_id, []).append(score)
        per_tree.append(
            {
                "tree_id": tree.tree_id,
                "score": 0.0 if unknown else float(max(curve)),
                "curve": curve,
                "auc": pareto_auc(curve),
                "unknown": unknown,
                "note": UNKNOWN_NOTE if unknown else "",
            }
        )
    per_evaluator = {
        evaluator_id: sum(scores) / len(scores) for evaluator_id, scores in grouped.items()
    }
    return _PolicyReplay(
        curve=list(trajectory.best_score_curve), per_tree=per_tree, per_evaluator=per_evaluator
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compare_versions(
    new_version: str,
    deployed_version: str,
    pool: SimulatorPool,
    cfg,
    *,
    store,
    history_root: str | Path = "policies/history",
    agent_id: str = AGENT_ID,
    comparison_dir: str | Path = DEFAULT_COMPARISON_DIR,
    replay_fn=None,
) -> ReplayComparison:
    """回放对比新版本 vs 部署版本（C14；零 LLM 由 replay_fn 保证——只读历史节点）。

    pool：模拟器池（提供逐树视图的树集合）；store：树存储（逐树单池与节点分项读取）。
    cfg：形态配置（当前实现不消费其数值，保留为口径扩展位与调用一致性）。
    """
    if new_version == deployed_version:
        raise ValueError(f"新版本与部署版本相同（{new_version}）：无需对比")
    if replay_fn is None:  # pragma: no cover - 生产路径为沙箱回放（002）
        raise ValueError("compare_versions 必须提供 replay_fn（生产为 002 沙箱回放）")
    trees = pool.trees
    new_replay = _replay_source(
        load_policy_source(new_version, history_root=history_root, agent_id=agent_id),
        trees=trees,
        store=store,
        replay_fn=replay_fn,
    )
    deployed_replay = _replay_source(
        load_policy_source(deployed_version, history_root=history_root, agent_id=agent_id),
        trees=trees,
        store=store,
        replay_fn=replay_fn,
    )

    per_tree = [
        {
            "tree_id": new_row["tree_id"],
            "new_score": new_row["score"],
            "deployed_score": deployed_row["score"],
            "delta": new_row["score"] - deployed_row["score"],
            "new_unknown": new_row["unknown"],
            "deployed_unknown": deployed_row["unknown"],
            "note": new_row["note"] or deployed_row["note"],
        }
        for new_row, deployed_row in zip(new_replay.per_tree, deployed_replay.per_tree, strict=True)
    ]
    evaluator_ids = sorted(set(new_replay.per_evaluator) | set(deployed_replay.per_evaluator))
    per_evaluator = [
        {
            "evaluator_id": evaluator_id,
            "new_score": new_replay.per_evaluator.get(evaluator_id, 0.0),
            "deployed_score": deployed_replay.per_evaluator.get(evaluator_id, 0.0),
            "delta": new_replay.per_evaluator.get(evaluator_id, 0.0)
            - deployed_replay.per_evaluator.get(evaluator_id, 0.0),
        }
        for evaluator_id in evaluator_ids
    ]
    mean_score = {
        "new": _mean([row["score"] for row in new_replay.per_tree]),
        "deployed": _mean([row["score"] for row in deployed_replay.per_tree]),
    }
    auc = {
        "new": pareto_auc(new_replay.curve),
        "deployed": pareto_auc(deployed_replay.curve),
    }
    unknown_trees = [
        row["tree_id"] for row in per_tree if row["new_unknown"] or row["deployed_unknown"]
    ]

    if mean_score["new"] > mean_score["deployed"]:
        verdict = "new_better"
    elif mean_score["new"] < mean_score["deployed"]:
        verdict = "deployed_better"
    elif auc["new"] > auc["deployed"]:
        verdict = "new_better"
    elif auc["new"] < auc["deployed"]:
        verdict = "deployed_better"
    else:
        verdict = "inconclusive"
    notes = []
    if verdict == "deployed_better":
        notes.append("新版本在回放上不优于部署版本：建议保留现版本（不做矮子里拔将军）")
    if unknown_trees:
        notes.append(f"存在 UNKNOWN 树 {unknown_trees}：覆盖不足，请扩大线上记录")
    comparison_id = f"cmp-{agent_id}-{new_version}-{deployed_version}"
    comparison = ReplayComparison(
        comparison_id=comparison_id,
        agent_id=agent_id,
        new_version=new_version,
        deployed_version=deployed_version,
        per_tree=per_tree,
        per_evaluator=per_evaluator,
        pareto_curve={"new": new_replay.curve, "deployed": deployed_replay.curve},
        pareto_auc=auc,
        mean_score=mean_score,
        unknown_trees=unknown_trees,
        verdict=verdict,
        note="；".join(notes),
        created_at=datetime.now(UTC).isoformat(),
    )
    _persist(comparison, Path(comparison_dir))
    return comparison


def _persist(comparison: ReplayComparison, directory: Path) -> Path:
    """报告落盘（只增不改；同 comparison_id 已存在即拒绝覆盖）。"""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{comparison.comparison_id}.json"
    if target.exists():
        raise FileExistsError(f"对比报告已存在（只增不改）：{target}")
    target.write_text(comparison.to_json(), encoding="utf-8")
    return target


def load_comparison(
    comparison_id: str, *, comparison_dir: str | Path = DEFAULT_COMPARISON_DIR
) -> dict:
    """按 id 读取对比报告（采纳的依据引用；不存在即报错，不静默返回空）。"""
    path = Path(comparison_dir) / f"{comparison_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"对比报告不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))
