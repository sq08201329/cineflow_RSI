"""回放沙盘对比报告（合同 screenplay-degraded.md C14，FR-009）。

人工提交的新版本 vs 当前部署版本在**模拟器池上回放对比**（零 LLM、零生成——回放只读
历史节点，原则三）：
- 策略结构计划 → **回放匹配结构键**（`loop.stage_match_key`：策略可复现部分）→
  002 池 probe 规范化精确匹配 → 命中即取**历史得分**（不重算、不生成）；
- 逐树得分、分项评估器差异（命中历史节点的 eval_breakdown 均值）、pareto_auc 曲线
  （复用 `dreaming/reward.pareto_auc` 权威口径）、UNKNOWN 覆盖说明。

诚实边界（原则六）：
- 未命中的阶段不给分（UNKNOWN = 零信息）；整树无命中 → 该树记 0 分并提示扩大线上记录；
- 新版本全劣 → 报告如实呈现（`verdict=deployed_better`）并建议保留现版本；
- **未过无偏性验收不得产出对比报告**（FR-013 发布阻塞：回放口径不可信时对比证据无效）；
- 报告只增不改（`{comparison_id}.json` 落盘一次），采纳/拒绝的留痕在 adoption.py。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agents.screenplay.artifact import STAGES
from agents.screenplay.loop import stage_match_key
from agents.screenplay.policy_versions import PolicySubmissionError, load_policy_source
from core.replay.pool import SimulatorPool
from dreaming.reward import pareto_auc
from policies.base import Budget
from policies.versioning import policy_version

AGENT_ID = "screenplay"
DEFAULT_COMPARISON_DIR = Path("screenplay/comparisons")
UNKNOWN_NOTE = "该树回放 UNKNOWN（无历史覆盖）：记 0 分，请扩大线上记录（不编造）"


class CompareError(Exception):
    """对比报告产出被拒（未过无偏性验收 / 策略版本不可用 / 回放口径非法）。"""


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
class UnbiasednessAttestation:
    """无偏性验收凭证（FR-013 发布阻塞门禁的凭据形态）。

    对比报告必须附验收结论：`verdict == "pass"` 才允许产出（`compare_versions` 前置检查）。
    来源 = 无偏性验收报告（002 `verify_unbiasedness(...).to_dict()`）的 JSON，
    可落盘流转（CLI `--unbiasedness <path>`）。
    """

    verdict: str
    tau: float | None = None
    threshold: float | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "UnbiasednessAttestation":
        if not isinstance(payload, dict) or "verdict" not in payload:
            raise CompareError("无偏性验收结论必须为含 verdict 的 JSON 对象")
        return cls(
            verdict=str(payload["verdict"]),
            tau=None if payload.get("tau") is None else float(payload["tau"]),
            threshold=None if payload.get("threshold") is None else float(payload["threshold"]),
            notes=str(payload.get("notes", "")),
        )

    @classmethod
    def load(cls, path: str | Path) -> "UnbiasednessAttestation":
        """按路径读取验收结论（不存在/非法即报错，不静默放行）。"""
        target = Path(path)
        if not target.is_file():
            raise CompareError(f"无偏性验收结论不存在：{target}（FR-013 发布阻塞凭据）")
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CompareError(f"无偏性验收结论非合法 JSON：{target}（{exc}）") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class _PolicyReplay:
    """单策略回放结果：池级最优曲线 + 逐树得分 + 分项明细（全部来自历史节点）。"""

    curve: list[float]
    per_tree: list[dict]
    per_evaluator: dict[str, float] = field(default_factory=dict)


def _instantiate(source: str):
    """实例化人工策略（源码已过静态检查；回放不执行生成、不触网关）。"""
    namespace: dict = {"__name__": "screenplay_replay"}
    exec(compile(source, "<policy>", "exec"), namespace)  # noqa: S102 - 已过静态检查
    policy_class = namespace.get("Policy")
    if not isinstance(policy_class, type):
        raise CompareError("策略源码缺少 Policy 类（回放对比需要 plan(inputs, config) 接口）")
    return policy_class()


def _breakdown_of(store, node_id: str) -> dict[str, float]:
    """历史节点的分项明细（evaluator_id → score；同 id 多版本取均值）。"""
    node = store.get_node(node_id)
    grouped: dict[str, list[float]] = {}
    for key, fragment in (node.eval_breakdown or {}).items():
        base = key.rsplit("@", 1)[0]
        grouped.setdefault(base, []).append(float(fragment["score"]))
    return {base: sum(scores) / len(scores) for base, scores in grouped.items()}


def replay_policy(
    source: str,
    trees,
    *,
    cfg,
    inputs: dict,
    store,
) -> _PolicyReplay:
    """回放一个策略：按结构键在每棵树的历史节点上 probe（零生成、零 LLM）。"""
    policy = _instantiate(source)
    version = policy_version(source)
    plans = policy.plan(inputs, cfg)
    if not isinstance(plans, dict):
        raise CompareError(f"策略 plan 必须返回分阶段计划 dict，实际为 {plans!r}")

    per_tree: list[dict] = []
    grouped: dict[str, list[float]] = {}
    curve: list[float] = []
    best = 0.0
    for tree in trees:
        tree_pool = SimulatorPool(store)
        tree_pool.add_tree(tree)
        simulator = tree_pool.build(
            worker_count=1, budget=Budget(max_probes=len(STAGES)), latency_quantum_ms=0
        )
        hits: list[float] = []
        hit_stages: list[str] = []
        miss_stages: list[str] = []
        for stage in STAGES:
            markers = plans.get(stage)
            if not isinstance(markers, dict):
                miss_stages.append(stage)  # 策略未产出该阶段计划 → 无覆盖（不编造）
                continue
            key = stage_match_key(
                stage,
                policy_version=version,
                inputs=inputs,
                config=cfg,
                markers=markers,
            )
            result = simulator.probe(tree.root_id, key)
            if result.status != "ok" or not result.nodes:
                miss_stages.append(stage)
                continue
            node = store.get_node(result.nodes[0].node_id)
            score = float(node.score)
            hits.append(score)
            hit_stages.append(stage)
            best = max(best, score)
            curve.append(best)
            for evaluator_id, value in _breakdown_of(store, node.node_id).items():
                grouped.setdefault(evaluator_id, []).append(value)
        unknown = not hits
        per_tree.append(
            {
                "tree_id": tree.tree_id,
                "score": 0.0 if unknown else sum(hits) / len(hits),
                "curve": list(hits),
                "hits": hit_stages,
                "misses": miss_stages,
                "unknown": unknown,
                "note": UNKNOWN_NOTE if unknown else "",
            }
        )
    per_evaluator = {
        evaluator_id: sum(values) / len(values) for evaluator_id, values in grouped.items()
    }
    return _PolicyReplay(curve=curve, per_tree=per_tree, per_evaluator=per_evaluator)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compare_versions(
    new_version: str,
    deployed_version: str,
    pool: SimulatorPool,
    cfg,
    *,
    store,
    inputs: dict,
    unbiasedness=None,
    history_root: str | Path = "policies/history",
    agent_id: str = AGENT_ID,
    comparison_dir: str | Path = DEFAULT_COMPARISON_DIR,
) -> ReplayComparison:
    """回放对比新版本 vs 部署版本（C14）。

    unbiasedness：**必经无偏性验收结论**（FR-013 发布阻塞：未附结论或未达标一律拒绝
    产出对比报告）；store：树存储（逐树单池与分项读取）；inputs：产出输入
    （题材/目标时长/角色设定——策略 plan 的输入，回放据此重算结构键）。
    """
    if new_version == deployed_version:
        raise ValueError(f"新版本与部署版本相同（{new_version}）：无需对比")
    if unbiasedness is None or getattr(unbiasedness, "verdict", None) != "pass":
        tau = None if unbiasedness is None else getattr(unbiasedness, "tau", None)
        raise CompareError(
            f"未过无偏性验收（FR-013 发布阻塞）：回放口径不可信时不得产出对比报告（τ={tau}）"
        )
    try:
        new_source = load_policy_source(new_version, history_root=history_root, agent_id=agent_id)
        deployed_source = load_policy_source(
            deployed_version, history_root=history_root, agent_id=agent_id
        )
    except PolicySubmissionError as exc:
        raise CompareError(f"策略版本不可用：{exc}") from exc

    trees = pool.trees
    new_replay = replay_policy(new_source, trees, cfg=cfg, inputs=inputs, store=store)
    deployed_replay = replay_policy(deployed_source, trees, cfg=cfg, inputs=inputs, store=store)

    per_tree = [
        {
            "tree_id": new_row["tree_id"],
            "new_score": new_row["score"],
            "deployed_score": deployed_row["score"],
            "delta": new_row["score"] - deployed_row["score"],
            "new_hits": new_row["hits"],
            "deployed_hits": deployed_row["hits"],
            "new_misses": new_row["misses"],
            "deployed_misses": deployed_row["misses"],
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
    comparison = ReplayComparison(
        comparison_id=f"cmp-{agent_id}-{new_version}-{deployed_version}",
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


def parse_created_at(comparison: dict) -> float:
    """报告创建时间（秒级；审计与排序用）。"""
    return time.mktime(datetime.fromisoformat(comparison["created_at"]).timetuple())
