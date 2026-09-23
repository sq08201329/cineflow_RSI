"""通用件回放对比单测（功能 017 / T1707，先于实现编写；契约 C1/C14）。

`core/degraded/compare.py` = 009 `sandbox_compare.py` 的机制抽出：

- `match_key` 以**可调用注入**（阶段 / 策略版本 / 输入 / 配置 / 阶段标记 → 结构键）；
  阶段序列同样注入（业务常量），核心件内零 agent 名与阶段名分支；
- **最小池门槛（前置）**：可比对树数 < 门槛 ⇒ **拒绝产出报告并报错**（错误含实测树数与门槛值）；
- 逐树得分 / 分项评估器差异 / pareto_auc 曲线 / UNKNOWN 说明；报告只增不改；
- **策略执行隔离的两项落地义务**（宪章 v2.0.0 原则四例外条款）：
  ① 策略执行**带执行超时**（静态检查不禁循环：策略内死循环必须判 `CompareError` 而非挂死宿主）；
  ② 断言守护"**不向策略执行交付任何环境对象**"：策略只被喂 `plan(inputs, config)`，
     既不 `observed()` 也不 `probe()`，探测由宿主代执行。
"""

import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.degraded import compare as compare_module
from core.degraded.compare import (
    POLICY_EXECUTION_TIMEOUT_SECONDS,
    CompareError,
    ReplayComparison,
    UnbiasednessAttestation,
    assert_no_environment_objects,
    compare_versions,
    load_comparison,
    pareto_auc,
    parse_created_at,
    replay_policy,
)
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus, new_id
from policies.versioning import policy_version

_STAGES = ("stage-a", "stage-b", "stage-c")
_INPUTS = {"topic": "某题材", "budget": 3}
_AGENT_ID = "screenplay"
_FLOOR = 2


def _cfg():
    """形态配置（duck-typed）：本用例只要求能被匹配键读取。"""
    return SimpleNamespace(model="model-a", max_tokens=1024)


def _match_key(stage, *, policy_version, inputs, config, markers):
    """注入的回放匹配键（结构键：阶段 + 策略版本 + 输入摘要 + 阶段标记）。"""
    return {
        "stage": stage,
        "policy_version": policy_version,
        "topic": inputs["topic"],
        "plan_tag": markers.get("plan_tag", ""),
    }


def _recorded_key(stage: str, tag: str, version: str) -> dict:
    """记录侧结构键（与注入匹配键同构成 → 规范化精确匹配命中）。"""
    return {
        "stage": stage,
        "policy_version": version,
        "topic": _INPUTS["topic"],
        "plan_tag": tag,
    }


def _policy_source(tag: str, *, bump: float = 0.0) -> str:
    """人工策略源码（结构计划内联；bump 只改源码文本 → 版本号变、结构键不变）。"""
    plan = {stage: {"plan_tag": tag} for stage in _STAGES}
    return (
        "class Policy:\n"
        '    """回放对比用例策略（纯计算 + plan(inputs, config) 接口）。"""\n'
        f"    PLAN = {plan!r}\n"
        f"    BUMP = {bump!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLAN\n"
    )


_ARGS_ECHO_SOURCE = (
    "class Policy:\n"
    '    """探针策略：如实回传收到的位置参数（断言只拿到 inputs/config）。"""\n'
    "    def plan(self, *args):\n"
    "        return {'stage-a': {'arg_count': len(args),"
    " 'arg_types': [type(item).__name__ for item in args]}}\n"
)

_DEAD_LOOP_SOURCE = (
    "class Policy:\n"
    '    """死循环策略（超时用例）：静态检查不禁循环，回放必须带执行超时。"""\n'
    "    def plan(self, inputs, config):\n"
    "        while True:\n"
    "            pass\n"
)


class _FakeSimulator:
    """伪环境对象（模拟器形态）：暴露探测与观测通道。"""

    def observed(self) -> dict:
        return {}

    def probe(self, parent_id: str, gen_params: dict):  # pragma: no cover - 只作形态
        raise AssertionError("不得被策略触达")


class _SpyMatchKey:
    """注入匹配键的探针：记录每次调用的入参（回放确实经注入键，非内建分支）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, stage, *, policy_version, inputs, config, markers):
        self.calls.append(
            {
                "stage": stage,
                "policy_version": policy_version,
                "inputs": inputs,
                "config": config,
                "markers": markers,
            }
        )
        return _recorded_key(stage, markers.get("plan_tag", ""), policy_version)


def _history(tmp_path: Path, sources: dict[str, str]) -> Path:
    """写策略历史（版本 = 源码 BLAKE3 前 12 位；meta 缺省不影响回放对比）。"""
    root = tmp_path / "history" / _AGENT_ID
    root.mkdir(parents=True, exist_ok=True)
    for source in sources.values():
        (root / f"{policy_version(source)}.py").write_text(source, encoding="utf-8")
    return tmp_path / "history"


def _build_tree(tree_store, make_tree, make_node, *, deployed: dict, new: dict):
    """一棵冻结树：每个阶段记两份历史节点（部署版本低分 / 新版本高分）。"""
    tree = make_tree(
        agent_id=_AGENT_ID,
        project_id="core-degraded",
        policy_version=new["version"],
        config_snapshot={"observation_fields": ["gen_params"]},
    )
    tree_store.create_tree(tree)
    tree_store.append_node(
        make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id=_AGENT_ID,
            policy_version=new["version"],
            observation_context={},
            eval_breakdown={},
            score=0.0,
            cost=CostRecord(),
            status=NodeStatus.EVALUATED,
            created_at=0.0,
        )
    )
    for index, stage in enumerate(_STAGES):
        for variant in (deployed, new):
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id=_AGENT_ID,
                    policy_version=variant["version"],
                    observation_context={
                        "gen_params": _recorded_key(stage, variant["tag"], variant["version"])
                    },
                    eval_breakdown={
                        "rule.core@1": {"score": variant["scores"][stage]},
                        "proxy.core@1": {"score": variant["scores"][stage] / 2},
                    },
                    score=variant["scores"][stage],
                    cost=CostRecord(),
                    status=NodeStatus.EVALUATED,
                    created_at=float(index + 1),
                )
            )
    return tree


@pytest.fixture()
def compare_env(tree_store, make_tree, make_node, tmp_path):
    """对比环境：两棵冻结树 + 两版策略源码 + 历史根（新版本在两棵树上均更优）。"""
    cfg = _cfg()
    deployed_source = _policy_source("deployed")
    new_source = _policy_source("new", bump=1.0)
    history_root = _history(tmp_path, {"deployed": deployed_source, "new": new_source})
    versions = {"deployed": policy_version(deployed_source), "new": policy_version(new_source)}
    trees = []
    for scores in (
        {
            "deployed": {"stage-a": 0.50, "stage-b": 0.50, "stage-c": 0.50},
            "new": {"stage-a": 0.90, "stage-b": 0.90, "stage-c": 0.90},
        },
        {
            "deployed": {"stage-a": 0.70, "stage-b": 0.70, "stage-c": 0.70},
            "new": {"stage-a": 0.80, "stage-b": 0.80, "stage-c": 0.80},
        },
    ):
        trees.append(
            _build_tree(
                tree_store,
                make_tree,
                make_node,
                deployed={
                    "tag": "deployed",
                    "version": versions["deployed"],
                    "scores": scores["deployed"],
                },
                new={"tag": "new", "version": versions["new"], "scores": scores["new"]},
            )
        )
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    return {
        "cfg": cfg,
        "store": tree_store,
        "pool": pool,
        "trees": trees,
        "history_root": history_root,
        "versions": versions,
        "sources": {"deployed": deployed_source, "new": new_source},
        "comparison_dir": tmp_path / "comparisons",
        "tmp_path": tmp_path,
    }


def _passing_attestation() -> UnbiasednessAttestation:
    return UnbiasednessAttestation(verdict="pass", tau=0.98, threshold=0.95)


def _compare(env, *, new_version=None, deployed_version=None, unbiasedness=None, **kwargs):
    versions = env["versions"]
    options = {
        "store": env["store"],
        "inputs": _INPUTS,
        "unbiasedness": _passing_attestation() if unbiasedness is None else unbiasedness,
        "match_key": _match_key,
        "stages": _STAGES,
        "min_comparable_trees": _FLOOR,
        "agent_id": _AGENT_ID,
        "history_root": env["history_root"],
        "comparison_dir": env["comparison_dir"],
    }
    options.update(kwargs)
    return compare_versions(
        new_version or versions["new"],
        deployed_version or versions["deployed"],
        env["pool"],
        env["cfg"],
        **options,
    )


class Test报告内容:
    def test_逐树得分与分项差异与_pareto_auc(self, compare_env):
        """C14：报告 = 逐树得分 / 分项评估器差异 / pareto 曲线与 AUC / UNKNOWN 说明。"""
        report = _compare(compare_env)
        assert isinstance(report, ReplayComparison)
        assert report.agent_id == _AGENT_ID
        assert report.comparison_id == (
            f"cmp-{_AGENT_ID}-{report.new_version}-{report.deployed_version}"
        )
        assert report.verdict == "new_better"
        assert len(report.per_tree) == 2
        for row in report.per_tree:
            assert row["delta"] > 0
            assert row["new_unknown"] is False and row["deployed_unknown"] is False
        assert report.mean_score["new"] > report.mean_score["deployed"]
        assert set(report.pareto_auc) == {"new", "deployed"}
        assert set(report.pareto_curve) == {"new", "deployed"}
        assert all(0.0 <= value <= 1.0 for value in report.pareto_auc.values())
        assert {item["evaluator_id"] for item in report.per_evaluator} == {
            "rule.core",
            "proxy.core",
        }
        assert all(item["delta"] > 0 for item in report.per_evaluator)
        assert report.unknown_trees == []
        stored = load_comparison(report.comparison_id, comparison_dir=compare_env["comparison_dir"])
        assert stored["verdict"] == "new_better"
        assert parse_created_at(stored) > 0.0

    def test_新版本全劣如实呈现(self, compare_env):
        report = _compare(
            compare_env,
            new_version=compare_env["versions"]["deployed"],
            deployed_version=compare_env["versions"]["new"],
        )
        assert report.verdict == "deployed_better"
        assert "保留现版本" in report.note

    def test_未命中即_UNKNOWN_记零并提示(self, compare_env):
        """诚实边界：整树无命中 → 该树记 0 分 + 提示扩大线上记录（不编造）。"""
        ghost_source = _policy_source("ghost")
        _history(compare_env["tmp_path"], {"ghost": ghost_source})
        report = _compare(compare_env, new_version=policy_version(ghost_source))
        assert report.mean_score["new"] == 0.0
        assert len(report.unknown_trees) == 2
        assert "UNKNOWN" in report.note and "扩大" in report.note
        assert report.verdict == "deployed_better"

    def test_报告只增不改(self, compare_env):
        report = _compare(compare_env)
        with pytest.raises(FileExistsError):
            _compare(compare_env)
        assert (compare_env["comparison_dir"] / f"{report.comparison_id}.json").is_file()

    def test_同版本对比拒绝(self, compare_env):
        version = compare_env["versions"]["new"]
        with pytest.raises(ValueError, match="相同"):
            _compare(compare_env, new_version=version, deployed_version=version)


class Test前置门禁:
    def test_未过无偏性不得产出报告(self, compare_env):
        with pytest.raises(CompareError, match="无偏性"):
            _compare(
                compare_env,
                unbiasedness=UnbiasednessAttestation(verdict="reject", tau=0.4, threshold=0.95),
            )
        assert list(compare_env["comparison_dir"].glob("*.json")) == []

    def test_最小池门槛拒绝产出并报实测数与门槛(self, compare_env):
        """SC-011：可比对树数 < 门槛 ⇒ 拒绝产出报告，错误含实测树数与门槛值。"""
        with pytest.raises(CompareError) as excinfo:
            _compare(compare_env, min_comparable_trees=_FLOOR + 1)
        message = str(excinfo.value)
        assert "2" in message and "3" in message
        assert list(compare_env["comparison_dir"].glob("*.json")) == []

    def test_达到门槛即产出(self, compare_env):
        report = _compare(compare_env, min_comparable_trees=_FLOOR)
        assert report.verdict == "new_better"

    def test_空池即低于门槛被拒(self, compare_env):
        empty = SimulatorPool(compare_env["store"])
        with pytest.raises(CompareError, match="可比对树数"):
            compare_versions(
                compare_env["versions"]["new"],
                compare_env["versions"]["deployed"],
                empty,
                compare_env["cfg"],
                store=compare_env["store"],
                inputs=_INPUTS,
                unbiasedness=_passing_attestation(),
                match_key=_match_key,
                stages=_STAGES,
                min_comparable_trees=1,
                agent_id=_AGENT_ID,
                history_root=compare_env["history_root"],
                comparison_dir=compare_env["comparison_dir"],
            )

    def test_策略版本不可用即报错(self, compare_env):
        with pytest.raises(CompareError, match="策略版本不可用"):
            _compare(compare_env, new_version="000000000000")


class Test无偏性凭证:
    def test_按路径读取并校验(self, tmp_path):
        path = tmp_path / "attestation.json"
        path.write_text('{"verdict": "pass", "tau": 0.97}', encoding="utf-8")
        attestation = UnbiasednessAttestation.load(path)
        assert attestation.verdict == "pass"
        assert attestation.tau == pytest.approx(0.97)
        assert attestation.notes == ""

    def test_凭证缺失或非法即报错(self, tmp_path):
        with pytest.raises(CompareError, match="不存在"):
            UnbiasednessAttestation.load(tmp_path / "missing.json")
        broken = tmp_path / "broken.json"
        broken.write_text("{不是 JSON", encoding="utf-8")
        with pytest.raises(CompareError, match="JSON"):
            UnbiasednessAttestation.load(broken)
        with pytest.raises(CompareError, match="verdict"):
            UnbiasednessAttestation.from_dict({"tau": 0.9})


class Test匹配键注入:
    def test_匹配键以可调用注入(self, compare_env):
        """回放确实经注入键：每次（树 × 阶段）调用一次，入参含版本/输入/配置/阶段标记。"""
        spy = _SpyMatchKey()
        report = _compare(compare_env, match_key=spy)
        assert report.verdict == "new_better"
        assert len(spy.calls) == 2 * 2 * len(_STAGES)  # 2 棵树 × 2 版策略 × 3 阶段
        assert {call["stage"] for call in spy.calls} == set(_STAGES)
        assert {call["policy_version"] for call in spy.calls} == set(
            compare_env["versions"].values()
        )
        assert all(call["inputs"] is _INPUTS for call in spy.calls)
        assert all(call["config"] is compare_env["cfg"] for call in spy.calls)
        assert {tuple(sorted(call["markers"])) for call in spy.calls} == {("plan_tag",)}

    def test_换匹配键口径即未命中(self, compare_env):
        """匹配键是唯一匹配口径：换掉键构造（含生成产物摘要类字段）→ 全 UNKNOWN。"""

        def other_key(stage, *, policy_version, inputs, config, markers):
            return {"stage": stage, "policy_version": policy_version, "extra": "不匹配"}

        report = _compare(compare_env, match_key=other_key)
        assert report.mean_score["new"] == 0.0
        assert report.mean_score["deployed"] == 0.0
        assert report.verdict == "inconclusive"


class Test策略执行隔离义务:
    """宪章 v2.0.0 原则四例外条款的两项落地义务（T1701/T1707）。"""

    def test_默认执行超时为有限正数(self):
        assert 0 < POLICY_EXECUTION_TIMEOUT_SECONDS <= 30

    def test_死循环策略超时判错不挂死(self, compare_env, monkeypatch):
        """义务①：策略内死循环必须被超时截断（静态检查不禁循环）。"""
        monkeypatch.setattr(compare_module, "POLICY_EXECUTION_TIMEOUT_SECONDS", 0.3)
        started = time.perf_counter()
        with pytest.raises(CompareError, match="超时"):
            replay_policy(
                _DEAD_LOOP_SOURCE,
                [],
                cfg=compare_env["cfg"],
                inputs=_INPUTS,
                store=compare_env["store"],
                stages=_STAGES,
                match_key=_match_key,
            )
        assert time.perf_counter() - started < 5.0

    def test_非主线程拒绝无超时执行(self, compare_env):
        """无 SIGALRM 可用的线程上下文：拒绝执行策略，不得无超时执行。"""
        captured: dict = {}

        def _run():
            try:
                replay_policy(
                    _policy_source("new"),
                    [],
                    cfg=compare_env["cfg"],
                    inputs=_INPUTS,
                    store=compare_env["store"],
                    stages=_STAGES,
                    match_key=_match_key,
                )
            except CompareError as exc:
                captured["error"] = str(exc)

        thread = threading.Thread(target=_run)
        thread.start()
        thread.join(timeout=10)
        assert "主线程" in captured.get("error", "")

    def test_环境对象不得交付策略(self, compare_env):
        """义务②：模拟器/观测通道形态的对象一律拒绝（含容器内嵌套）。"""
        assert_no_environment_objects(_INPUTS, compare_env["cfg"])  # 纯声明通过
        with pytest.raises(CompareError, match="环境对象"):
            assert_no_environment_objects({"sim": _FakeSimulator()})
        with pytest.raises(CompareError, match="环境对象"):
            assert_no_environment_objects({"deep": [{"sim": _FakeSimulator()}]})
        with pytest.raises(CompareError, match="环境对象"):
            assert_no_environment_objects(
                {"store": compare_env["store"]}, environment=(compare_env["store"],)
            )

    def test_回放路径交付环境对象即拒绝且策略未执行(self, compare_env):
        """守护断言接在生产路径上：inputs 夹带环境对象 → 报错，策略一次都不跑。"""
        spy = _SpyMatchKey()
        with pytest.raises(CompareError, match="环境对象"):
            replay_policy(
                _policy_source("new"),
                compare_env["trees"],
                cfg=compare_env["cfg"],
                inputs={**_INPUTS, "sim": _FakeSimulator()},
                store=compare_env["store"],
                stages=_STAGES,
                match_key=spy,
            )
        assert spy.calls == []

    def test_策略只收到_inputs_与_config(self, compare_env):
        """策略仅 `plan(inputs, config)`：只收两个纯声明参数（探测由宿主代执行）。"""
        spy = _SpyMatchKey()
        replay_policy(
            _ARGS_ECHO_SOURCE,
            compare_env["trees"],
            cfg=compare_env["cfg"],
            inputs=_INPUTS,
            store=compare_env["store"],
            stages=_STAGES,
            match_key=spy,
        )
        assert spy.calls[0]["markers"] == {
            "arg_count": 2,
            "arg_types": ["dict", "SimpleNamespace"],
        }


class Test回放不经环境对象:
    def test_回放只读历史节点不重算(self, compare_env):
        """回放得分取自历史节点（同键命中即取记录值，宿主执行探测）。"""
        replay = replay_policy(
            compare_env["sources"]["new"],
            compare_env["trees"],
            cfg=compare_env["cfg"],
            inputs=_INPUTS,
            store=compare_env["store"],
            stages=_STAGES,
            match_key=_match_key,
        )
        assert [row["score"] for row in replay.per_tree] == [
            pytest.approx(0.9),
            pytest.approx(0.8),
        ]
        assert replay.per_evaluator["rule.core"] == pytest.approx(0.85)


class Test口径对齐权威实现:
    """pareto_auc 复用 `dreaming.reward` 的梯形归一化口径（core → dreaming 反向依赖被
    原则五禁止，故按包复制口径；等值性由本节机检守住——改一处不改另一处即红）。"""

    @pytest.mark.parametrize(
        "curve",
        [[], [0.4], [0.0, 0.5, 1.0], [0.2, 0.4, 0.9], [0.9, 0.9, 0.9]],
        ids=["空", "单点", "线性", "可区分均值口径", "常值"],
    )
    def test_与权威实现同值(self, curve):
        from dreaming.reward import pareto_auc as authoritative

        assert pareto_auc(list(curve)) == authoritative(list(curve))

    def test_梯形口径期望值(self):
        # 梯形 = (0.2+0.4)/2 + (0.4+0.9)/2 = 0.95；/ (n-1)=2 → 0.475（均值口径为 0.5）
        assert pareto_auc([0.2, 0.4, 0.9]) == pytest.approx(0.475)


class Test报告字段集:
    def test_字段集锁定(self):
        assert set(ReplayComparison.__dataclass_fields__) == {
            "comparison_id",
            "agent_id",
            "new_version",
            "deployed_version",
            "per_tree",
            "per_evaluator",
            "pareto_curve",
            "pareto_auc",
            "mean_score",
            "unknown_trees",
            "verdict",
            "note",
            "created_at",
        }

    def test_报告不含形态或_agent_名分支(self):
        """核心件零业务分支：源码不含 agent 名/形态字面量（详细断言见纯净性测试）。"""
        source = Path(compare_module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"agent_id\s*==", source)
