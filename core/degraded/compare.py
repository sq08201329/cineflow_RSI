"""回放沙盘对比报告（降级模式机制件，业务无关）。

人工提交的新版本 vs 当前部署版本在**模拟器池上回放对比**（零 LLM、零生成——回放只读
历史节点，原则三）：

- 策略结构计划 → **回放匹配结构键**（注入的 `match_key`：策略可复现部分）→ 池 probe
  规范化精确匹配 → 命中即取**历史得分**（不重算、不生成）；
- 逐树得分、分项评估器差异（命中历史节点的 eval_breakdown 均值）、pareto_auc 曲线、
  UNKNOWN 覆盖说明；
- **最小池门槛（前置）**：可比对树数 < 门槛 ⇒ **拒绝产出报告**（错误含实测树数与门槛值）。

诚实边界（原则六）：未命中的阶段不给分（UNKNOWN = 零信息）；整树无命中 → 该树记 0 分并
提示扩大线上记录；新版本全劣 → 报告如实呈现并建议保留现版本；**未过无偏性验收不得产出
对比报告**；报告只增不改，采纳/拒绝的留痕在 `adoption.py`。

**策略执行隔离的两项落地义务**（宪章 v2.0.0 原则四例外条款，本模块是机制落点）：

① 策略执行**带执行超时**：静态检查不禁循环，策略内死循环必须判 `CompareError` 而非挂死宿主；
② 断言守护"**不向策略执行交付任何环境对象**"：策略仅 `plan(inputs, config)`，既不
`observed()` 也不 `probe()`，探测由宿主代执行——防后续改动把模拟器或观测通道塞进策略形参。
"""

import json
import signal
import threading
import time
import types
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.degraded.policy import (
    DEFAULT_POLICY_HISTORY_ROOT,
    PolicySubmissionError,
    load_policy_source,
)
from core.replay.pool import SimulatorPool
from policies.base import Budget
from policies.versioning import policy_version

UNKNOWN_NOTE = "该树回放 UNKNOWN（无历史覆盖）：记 0 分，请扩大线上记录（不编造）"
# 策略执行超时上限（义务①；静态检查不禁循环，宿主不得被策略死循环占用）
POLICY_EXECUTION_TIMEOUT_SECONDS = 5.0
# 环境对象形态（义务②）：暴露探测/观测通道或树访问通道的对象一律不得交付策略执行
_ENVIRONMENT_CHANNELS = (
    "probe",
    "observed",
    "observation_context",
    "latent",
    "get_node",
    "nodes_of",
)


class CompareError(Exception):
    """对比报告产出被拒（未过无偏性验收 / 树数低于门槛 / 策略版本不可用 / 口径非法）。"""


@dataclass(frozen=True)
class ReplayComparison:
    """回放对比报告：逐树得分 / 分项差异 / pareto 曲线与 AUC / UNKNOWN 说明。"""

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
    """无偏性验收凭证（发布阻塞门禁的凭据形态）。

    对比报告必须附验收结论：`verdict == "pass"` 才允许产出（`compare_versions` 前置检查）。
    来源 = 无偏性验收报告的 JSON，可落盘流转（调用方按路径加载）。
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
            raise CompareError(f"无偏性验收结论不存在：{target}（发布阻塞凭据）")
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


def pareto_auc(best_score_curve: list[float]) -> float:
    """Pareto AUC（逐轮最优得分曲线的梯形面积除以满分红线面积，归一化 [0,1]）。

    与 005 奖励函数的权威口径**同值**（此处按口径复制：原则五的单向依赖禁止
    `core → dreaming`）；等值性由 `tests/unit/test_core_degraded_compare.py` 的
    权威实现对照断言守住——改一处不改另一处即红。
    """
    n = len(best_score_curve)
    if n == 0:
        return 0.0
    if n == 1:
        return float(best_score_curve[0])
    area = sum((best_score_curve[i] + best_score_curve[i + 1]) / 2 for i in range(n - 1))
    return area / (n - 1)


def _walk(value, *, path: str, depth: int = 0, seen: set[int] | None = None):
    """递归产出交付物内的 (路径, 对象)：只走容器与对象自身字段，深度有限且按 id 去重。"""
    if seen is None:
        seen = set()
    if id(value) in seen or depth > 6:
        return
    seen.add(id(value))
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, path=f"{path}[{key!r}]", depth=depth + 1, seen=seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            yield from _walk(item, path=f"{path}[{index}]", depth=depth + 1, seen=seen)
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            yield from _walk(
                getattr(value, item.name), path=f"{path}.{item.name}", depth=depth + 1, seen=seen
            )


def _channels_of(value) -> list[str]:
    """对象暴露的探测/观测通道名（反射失败即视为未暴露——不因异常中断校验）。"""
    exposed: list[str] = []
    for name in _ENVIRONMENT_CHANNELS:
        try:
            if getattr(value, name, None) is not None:
                exposed.append(name)
        except Exception:  # noqa: BLE001 - 属性取值异常不构成"暴露"证据
            continue
    return exposed


def environment_leaks(*delivered, environment: Sequence = ()) -> list[str]:
    """交付物中的环境对象路径（空列表 = 纯声明）。

    环境对象 = ① 宿主回放环境自身（模拟器池 / 模拟器 / 树存储，按身份识别）；
    ② 携带探测或观测通道的对象；③ 模块与可调用者（把函数交给策略等于交出通道）。
    """
    leaks: list[str] = []
    for index, value in enumerate(delivered):
        for path, node in _walk(value, path=f"delivered[{index}]"):
            if any(node is item for item in environment):
                leaks.append(f"{path}：宿主回放环境对象")
            elif isinstance(node, types.ModuleType) or callable(node):
                leaks.append(f"{path}：模块或可调用者")
            else:
                channels = _channels_of(node)
                if channels:
                    leaks.append(f"{path}：暴露 {'/'.join(channels)}")
    return leaks


def assert_no_environment_objects(*delivered, environment: Sequence = ()) -> None:
    """断言：**不向策略执行交付任何环境对象**（宪章 v2.0.0 原则四例外条款义务②）。

    策略只接收 `plan(inputs, config)` 两个纯声明——既无模拟器也无观测通道，探测由宿主
    代执行。后续若有人把模拟器、观测口或存储塞进策略形参，此处即报错（不得静默交付）。
    """
    leaks = environment_leaks(*delivered, environment=environment)
    if leaks:
        raise CompareError(
            "不得向策略执行交付环境对象（原则四例外条款义务②：策略仅 plan(inputs, config)）：\n"
            + "\n".join(f"- {item}" for item in leaks)
        )


@contextmanager
def _policy_deadline(seconds: float | None = None):
    """策略执行超时（义务①）：超时即 `CompareError`，宿主不被策略死循环占用。

    SIGALRM 只对主线程可用：非主线程**拒绝执行**策略（宁可拒绝，也不无超时执行）。
    边界（如实标注）：吞掉 `BaseException` 的策略仍可绕过进程内的信号截断——该残余风险
    由静态检查与"人工显式采纳"两道前置把关，与容器隔离的例外口径一致。
    """
    if threading.current_thread() is not threading.main_thread():
        raise CompareError("策略执行超时依赖主线程信号：非主线程调用拒绝执行（不得无超时执行策略）")
    limit = POLICY_EXECUTION_TIMEOUT_SECONDS if seconds is None else float(seconds)

    def _on_alarm(_signum, _frame):
        # 再武装：吞掉一次超时异常的策略不得因此绕过截断（下一轮到期再次抛出）
        signal.setitimer(signal.ITIMER_REAL, limit)
        raise CompareError(f"策略执行超时（>{limit:g}s）：静态检查不禁循环，超时即拒绝该次回放")

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _instantiate(source: str):
    """实例化人工策略（源码已过静态检查；回放不执行生成、不触网关）。"""
    namespace: dict = {"__name__": "policy_replay"}
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
    stages: Sequence[str],
    match_key,
) -> _PolicyReplay:
    """回放一个策略：按结构键在每棵树的历史节点上 probe（零生成、零 LLM）。

    `stages` 与 `match_key` 由调用方注入（业务常量与结构键构造）；策略执行受超时约束、
    且只接收 `(inputs, cfg)` 两个纯声明（两项义务见模块 docstring）。
    """
    # 交付面 = 策略执行的实际实参（断言与调用共用同一元组：后续加参数即在同一处失败）
    delivered = (inputs, cfg)
    assert_no_environment_objects(*delivered, environment=(store,))
    with _policy_deadline():
        policy = _instantiate(source)
        version = policy_version(source)
        plans = policy.plan(*delivered)
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
            worker_count=1, budget=Budget(max_probes=len(stages)), latency_quantum_ms=0
        )
        hits: list[float] = []
        hit_stages: list[str] = []
        miss_stages: list[str] = []
        for stage in stages:
            markers = plans.get(stage)
            if not isinstance(markers, dict):
                miss_stages.append(stage)  # 策略未产出该阶段计划 → 无覆盖（不编造）
                continue
            key = match_key(
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
    match_key,
    stages: Sequence[str],
    min_comparable_trees: int,
    agent_id: str,
    comparison_dir: str | Path,
    unbiasedness=None,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
) -> ReplayComparison:
    """回放对比新版本 vs 部署版本。

    unbiasedness：**必经无偏性验收结论**（未附结论或未达标一律拒绝产出对比报告）；
    min_comparable_trees：最小可比对树数门槛（可比对树数 < 门槛即拒绝产出报告）；
    match_key / stages：注入的结构键构造与阶段序列（业务件提供）；
    store：树存储（逐树单池与分项读取）；inputs：产出输入（策略 plan 的输入）。
    """
    if new_version == deployed_version:
        raise ValueError(f"新版本与部署版本相同（{new_version}）：无需对比")
    if unbiasedness is None or getattr(unbiasedness, "verdict", None) != "pass":
        tau = None if unbiasedness is None else getattr(unbiasedness, "tau", None)
        raise CompareError(
            f"未过无偏性验收（发布阻塞）：回放口径不可信时不得产出对比报告（τ={tau}）"
        )
    trees = pool.trees
    if len(trees) < int(min_comparable_trees):
        raise CompareError(
            f"可比对树数不足（实测 {len(trees)} < 门槛 {int(min_comparable_trees)}）："
            "拒绝产出对比报告（样本不足的对比证据无效）"
        )
    try:
        new_source = load_policy_source(new_version, agent_id=agent_id, history_root=history_root)
        deployed_source = load_policy_source(
            deployed_version, agent_id=agent_id, history_root=history_root
        )
    except PolicySubmissionError as exc:
        raise CompareError(f"策略版本不可用：{exc}") from exc

    new_replay = replay_policy(
        new_source, trees, cfg=cfg, inputs=inputs, store=store, stages=stages, match_key=match_key
    )
    deployed_replay = replay_policy(
        deployed_source,
        trees,
        cfg=cfg,
        inputs=inputs,
        store=store,
        stages=stages,
        match_key=match_key,
    )

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


def load_comparison(comparison_id: str, *, comparison_dir: str | Path) -> dict:
    """按 id 读取对比报告（采纳的依据引用；不存在即报错，不静默返回空）。"""
    path = Path(comparison_dir) / f"{comparison_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"对比报告不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_created_at(comparison: dict) -> float:
    """报告创建时间（秒级；审计与排序用）。"""
    return time.mktime(datetime.fromisoformat(comparison["created_at"]).timetuple())
