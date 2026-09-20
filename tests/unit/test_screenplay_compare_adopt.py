"""回放对比与采纳单测（功能 009 / T926，先于实现编写；C14 全场景 + T929 接口形状）。

C14：
- **observed/probe 规范化精确匹配**：回放匹配槽 = 策略可复现的**结构键**
  （stage/policy_version/model/temperature/max_tokens/target_duration_min/plan_digest），
  参数键序乱排仍命中（002 `normalize_params` 口径）；未命中即 UNKNOWN（不编造）；
- **观测投影白名单**：节点观测只向沙箱暴露 gen_params/stage/job_id（默认零泄露）；
- 逐树得分 / 分项评估器差异 / pareto_auc 曲线（dreaming/reward 口径）/ UNKNOWN 记 0
  分并提示扩大记录；新版本优/劣两路径报告呈现；
- 报告只增不改（重复生成即 FileExistsError）；**未过无偏性验收不得产出对比报告**
  （FR-013 发布阻塞）；
- 采纳门禁：未采纳指针逐字节不变（机检 yaml 哈希）；采纳后指针更新 + AdoptionRecord
  落盘；拒绝/采纳理由非空；回放零 LLM（网关 chat 被替换为抛错仍可产出报告）。
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from agents.screenplay.adoption import (
    AdoptionError,
    AdoptionRecord,
    adopt,
    deployed_version,
)
from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.loop import stage_match_key
from agents.screenplay.sandbox_compare import (
    CompareError,
    ReplayComparison,
    compare_versions,
    load_comparison,
)
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.models import CostRecord, NodeStatus, new_id

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_INPUTS = {"topic": "病房里的三个月", "target_duration_min": 3, "characters": ["林静", "陈默"]}
_WEIGHTS = _REAL_CONFIG["evaluator_weights"]["screenplay"]
_STAGES = ("outline", "scenes", "script")

# 结构键随 plan_digest 变化 → 两个策略产出不同结构键（回放靠结构键精确匹配）
_PLAN_A = {
    "outline": {"scene": 1, "lines": 6, "beats": 8},
    "scenes": {"scene": 1, "lines": 6, "beats": 8},
    "script": {"scene": 1, "lines": 9, "beats": 8},
}
_PLAN_B = {
    "outline": {"scene": 2, "lines": 4, "beats": 8},
    "scenes": {"scene": 2, "lines": 4, "beats": 8},
    "script": {"scene": 2, "lines": 4, "beats": 8},
}


def _config(**overrides) -> ScreenplayConfig:
    raw = copy.deepcopy(_REAL_CONFIG)
    raw["screenplay"].update({"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3})
    raw["screenplay"].update(overrides)
    return ScreenplayConfig.from_dict(raw)


def _config_copy(tmp_path: Path, *, pointer: str | None = None) -> Path:
    """配置副本（pointer=None → 显式移除部署指针段），用于指针读写与"未采纳不变"机检。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    raw.pop("deployment", None)  # 真实配置含首版指针：副本显式移除后再按用例写入
    if pointer is not None:
        raw["deployment"] = {"screenplay": {"current_policy_version": pointer}}
    path = tmp_path / "movie.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _policy_source(plan: dict, *, bump: float = 0.0) -> str:
    """人工策略源码（结构计划内联；bump 只改源码文本 → 版本号变、结构键不变）。"""
    return (
        "class Policy:\n"
        '    """回放对比用例策略（结构计划内联；接口 plan(inputs, config)）。"""\n'
        f"    PLAN = {plan!r}\n"
        f"    BUMP = {bump!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLAN\n"
    )


def _history(tmp_path: Path, versions: dict[str, str]) -> Path:
    """写策略历史（版本 = 源码 BLAKE3 前 12 位，meta 缺省不影响回放对比）。"""
    import blake3

    root = tmp_path / "history" / "screenplay"
    root.mkdir(parents=True, exist_ok=True)
    for source in versions.values():
        version = blake3.blake3(source.encode()).hexdigest()[:12]
        (root / f"{version}.py").write_text(source, encoding="utf-8")
    return tmp_path / "history"


def _version_of(source: str) -> str:
    import blake3

    return blake3.blake3(source.encode()).hexdigest()[:12]


def _recorded_key(stage: str, plan: dict, policy_version: str, config: ScreenplayConfig) -> dict:
    """记录侧结构键（与线上同构成）。"""
    return stage_match_key(
        stage,
        policy_version=policy_version,
        inputs=_INPUTS,
        config=config,
        markers=plan[stage],
    )


def _build_tree(
    make_tree,
    make_node,
    store,
    *,
    plan: dict,
    policy_version: str,
    config: ScreenplayConfig,
    scores: dict[str, float],
    agent_id: str = "screenplay",
):
    """一棵冻结轮次树：root + 三阶段节点（gen_params = 结构键，得分 = 记录值）。"""
    tree = make_tree(
        agent_id=agent_id,
        project_id="screenplay-compare",
        policy_version=policy_version,
        config_snapshot={
            "evaluator_weights": _WEIGHTS,
            "observation_fields": ["gen_params", "stage", "job_id"],
        },
    )
    store.create_tree(tree)
    store.append_node(
        make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id=agent_id,
            policy_version=policy_version,
            eval_breakdown={},
            score=0.0,
            status=NodeStatus.EVALUATED,
            created_at=0.0,
        )
    )
    for index, stage in enumerate(_STAGES):
        store.append_node(
            make_node(
                node_id=new_id(),
                tree_id=tree.tree_id,
                parent_id=tree.root_id,
                depth=1,
                agent_id=agent_id,
                policy_version=policy_version,
                observation_context={
                    "gen_params": _recorded_key(stage, plan, policy_version, config),
                    "stage": stage,
                    "job_id": f"cmp-{tree.tree_id[-4:]}-{stage}",
                },
                artifact_hash=f"{index + 1:064x}",
                eval_breakdown={
                    "rule.beat_structure@1.0.0": {"score": scores[stage]},
                    "proxy.entity_consistency@1.0.0": {"score": scores[stage]},
                    "judge.dramatic_tension@1.0.0": {"score": scores[stage]},
                },
                score=scores[stage],
                cost=CostRecord(llm_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=float(index + 1),
            )
        )
    return tree


@pytest.fixture()
def compare_env(tree_store, make_tree, make_node, tmp_path):
    """对比环境：两策略源码 + 历史池（两棵树，记录两策略结构键的得分）。"""
    config = _config()
    deployed_source = _policy_source(_PLAN_A)
    new_source = _policy_source(_PLAN_A, bump=1.0)  # 同结构、不同代码 → 结构键相同
    history_root = _history(tmp_path, {"deployed": deployed_source, "new": new_source})
    versions = {
        "deployed": _version_of(deployed_source),
        "new": _version_of(new_source),
    }
    # 树 1：新版本更优；树 2：新版本更优（mean 视角）——记录两侧结构键的历史得分
    trees = []
    for tree_scores in (
        {
            "deployed": {"outline": 0.50, "scenes": 0.50, "script": 0.50},
            "new": {"outline": 0.90, "scenes": 0.90, "script": 0.90},
        },
        {
            "deployed": {"outline": 0.70, "scenes": 0.70, "script": 0.70},
            "new": {"outline": 0.80, "scenes": 0.80, "script": 0.80},
        },
    ):
        tree = _build_tree(
            make_tree,
            make_node,
            tree_store,
            plan=_PLAN_A,
            policy_version=versions["deployed"],
            config=config,
            scores=tree_scores["deployed"],
        )
        for stage, score in tree_scores["new"].items():
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="screenplay",
                    policy_version=versions["new"],
                    observation_context={
                        "gen_params": _recorded_key(stage, _PLAN_A, versions["new"], config),
                        "stage": stage,
                        "job_id": f"new-{stage}",
                    },
                    eval_breakdown={"proxy.entity_consistency@1.0.0": {"score": score}},
                    score=score,
                    cost=CostRecord(),
                    status=NodeStatus.EVALUATED,
                    created_at=float(10 + _STAGES.index(stage)),
                )
            )
        trees.append(tree)
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    return {
        "config": config,
        "store": tree_store,
        "pool": pool,
        "history_root": history_root,
        "versions": versions,
        "sources": {"deployed": deployed_source, "new": new_source},
        "comparison_dir": tmp_path / "comparisons",
        "adoption_dir": tmp_path / "adoptions",
        "tmp_path": tmp_path,
    }


def _passing_unbiasedness():
    report = verify_unbiasedness([0.5, 0.7, 0.9], [0.5, 0.7, 0.9], threshold=0.95)
    assert report.verdict == "pass"
    return report


def _compare(env, *, new_version=None, deployed_version=None, unbiasedness=None, **kwargs):
    versions = env["versions"]
    return compare_versions(
        new_version or versions["new"],
        deployed_version or versions["deployed"],
        env["pool"],
        env["config"],
        store=env["store"],
        inputs=_INPUTS,
        unbiasedness=_passing_unbiasedness() if unbiasedness is None else unbiasedness,
        history_root=env["history_root"],
        comparison_dir=env["comparison_dir"],
        **kwargs,
    )


class Test接口可导入:
    def test_契约符号齐备(self):
        from agents.screenplay import adoption, sandbox_compare

        assert callable(sandbox_compare.compare_versions)
        assert callable(sandbox_compare.load_comparison)
        assert callable(adoption.adopt)
        assert callable(adoption.deployed_version)
        assert callable(adoption.load_adoption_record)

    def test_ReplayComparison_字段集(self):
        """C14 报告字段锁定（逐树得分/分项差异/pareto 曲线/UNKNOWN 说明/结论）。"""
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

    def test_AdoptionRecord_字段集(self):
        """C14 采纳记录字段锁定（结论/人/时间/依据/理由/指针前后值）。"""
        assert set(AdoptionRecord.__dataclass_fields__) == {
            "comparison_id",
            "agent_id",
            "decision",
            "by",
            "reason",
            "at",
            "deployed_before",
            "deployed_after",
            "adopted_version",
            "record_path",
        }


class Test回放匹配与投影:
    """C14 / T926 analyze 修订：规范化精确匹配 + 观测投影白名单。"""

    def test_结构键参数键序乱排仍命中(self, compare_env):
        """002 规范化精确匹配：同一结构键键序乱排仍命中（回放打分读历史得分）。"""
        config = compare_env["config"]
        version = compare_env["versions"]["deployed"]
        tree = compare_env["pool"].trees[0]
        simulator = SimulatorPool(compare_env["store"])
        simulator.add_tree(tree)
        builder = simulator.build(worker_count=1, budget=_budget(), latency_quantum_ms=0)
        key = _recorded_key("outline", _PLAN_A, version, config)
        shuffled = dict(reversed(list(key.items())))
        result = builder.probe(tree.root_id, shuffled)
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.5)  # 历史得分（回放不重算）

    def test_未命中即_UNKNOWN_零信息(self, compare_env):
        """不匹配走法不得得分、不得携带节点信息（FR-004）。"""
        tree = compare_env["pool"].trees[0]
        simulator = SimulatorPool(compare_env["store"])
        simulator.add_tree(tree)
        builder = simulator.build(worker_count=1, budget=_budget(), latency_quantum_ms=0)
        result = builder.probe(tree.root_id, {"stage": "outline", "plan_digest": "deadbeef"})
        assert result.status == "unknown"
        assert result.nodes == []

    def test_观测投影白名单只暴露三槽(self, compare_env):
        """观测投影白名单 = [gen_params, stage, job_id]：缓存键/响应哈希/摘要不进沙箱。"""
        tree = compare_env["pool"].trees[0]
        node = next(
            n for n in compare_env["store"].nodes_of(tree.tree_id) if n.parent_id is not None
        )
        whitelist = tree.config_snapshot["observation_fields"]
        assert whitelist == ["gen_params", "stage", "job_id"]
        from core.replay.observation import project_observation

        observation = project_observation(node, whitelist)
        assert set(observation.fields) <= {"gen_params", "stage", "job_id"}
        assert observation.score == pytest.approx(node.score)

    def test_结构键含策略可复现字段(self, compare_env):
        """结构键 = 策略可复现部分（不含生成产物摘要）——回放无需生成即可匹配。"""
        key = _recorded_key(
            "scenes", _PLAN_A, compare_env["versions"]["deployed"], compare_env["config"]
        )
        assert set(key) == {
            "stage",
            "policy_version",
            "model",
            "temperature",
            "max_tokens",
            "target_duration_min",
            "plan_digest",
        }
        # 同结构不同代码（policy_version 不同）→ 键不同（策略版本可回溯）
        other = _recorded_key(
            "scenes", _PLAN_A, compare_env["versions"]["new"], compare_env["config"]
        )
        assert other != key
        assert other["plan_digest"] == key["plan_digest"]  # 结构一致 → 计划摘要一致


def _budget():
    from policies.base import Budget

    return Budget(max_probes=8)


class Test对比报告:
    def test_新版本更优路径(self, compare_env):
        """C14 场景 1：逐树得分/分项差异/pareto_auc 齐全，verdict = new_better。"""
        report = _compare(compare_env)
        assert report.verdict == "new_better"
        assert len(report.per_tree) == 2
        for row in report.per_tree:
            assert set(row) >= {"tree_id", "new_score", "deployed_score", "delta"}
            assert row["delta"] > 0
        assert report.mean_score["new"] > report.mean_score["deployed"]
        assert set(report.pareto_auc) == {"new", "deployed"}
        assert all(0.0 <= value <= 1.0 for value in report.pareto_auc.values())
        assert set(report.pareto_curve) == {"new", "deployed"}
        assert report.per_evaluator  # 分项评估器差异（来自命中历史节点）
        assert all(
            set(item) >= {"evaluator_id", "new_score", "deployed_score", "delta"}
            for item in report.per_evaluator
        )
        assert report.unknown_trees == []
        assert report.note == "" or "UNKNOWN" not in report.note
        # 报告落盘（依据引用可按 id 读回）
        stored = load_comparison(report.comparison_id, comparison_dir=compare_env["comparison_dir"])
        assert stored["verdict"] == "new_better" and stored["comparison_id"] == report.comparison_id

    def test_新版本全劣路径如实呈现(self, compare_env):
        """C14 场景 2：新版本不优于部署版本 → deployed_better + 建议保留现版本。"""
        report = _compare(
            compare_env,
            new_version=compare_env["versions"]["deployed"],
            deployed_version=compare_env["versions"]["new"],
        )
        assert report.verdict == "deployed_better"
        assert "保留现版本" in report.note

    def test_UNKNOWN_树记零并提示扩大记录(self, compare_env):
        """C14 场景 4：策略结构在历史无覆盖 → 该树 0 分 + 提示扩大记录（不编造）。"""
        ghost_plan = {stage: {**_PLAN_B[stage], "ghost": True} for stage in _STAGES}
        ghost_source = _policy_source(ghost_plan)
        _history(compare_env["tmp_path"], {"ghost": ghost_source})
        report = _compare(
            compare_env,
            new_version=_version_of(ghost_source),
            deployed_version=compare_env["versions"]["deployed"],
        )
        assert report.mean_score["new"] == 0.0
        assert len(report.unknown_trees) == 2  # 两棵树均无覆盖
        assert "扩大" in report.note and "UNKNOWN" in report.note
        assert all(row["new_score"] == 0.0 for row in report.per_tree)
        assert report.verdict == "deployed_better"  # 无覆盖不得冒充优势

    def test_报告只增不改(self, compare_env):
        report = _compare(compare_env)
        with pytest.raises(FileExistsError):
            _compare(compare_env)
        assert (
            report.comparison_id == f"cmp-screenplay-{report.new_version}-{report.deployed_version}"
        )

    def test_同版本对比拒绝(self, compare_env):
        version = compare_env["versions"]["new"]
        with pytest.raises(ValueError, match="相同"):
            _compare(compare_env, new_version=version, deployed_version=version)

    def test_未过无偏性不得产出对比报告(self, compare_env):
        """FR-013 发布阻塞：无偏性未达标（或未附结论）→ 拒绝产出对比报告。"""
        rejected = verify_unbiasedness([0.5, 0.7, 0.9], [0.9, 0.7, 0.5], threshold=0.95)
        assert rejected.verdict == "reject"
        with pytest.raises(CompareError, match="无偏性"):
            _compare(compare_env, unbiasedness=rejected)
        with pytest.raises(CompareError, match="无偏性"):
            compare_versions(
                compare_env["versions"]["new"],
                compare_env["versions"]["deployed"],
                compare_env["pool"],
                compare_env["config"],
                store=compare_env["store"],
                inputs=_INPUTS,
                unbiasedness=None,
                history_root=compare_env["history_root"],
                comparison_dir=compare_env["comparison_dir"],
            )
        assert list(compare_env["comparison_dir"].glob("*.json")) == []  # 未产出任何报告

    def test_回放零_LLM_审计(self, compare_env, monkeypatch):
        """回放只读历史节点：网关 chat 被替换为抛错仍可产出报告（零 LLM，原则三）。"""
        from core.llm_gateway.gateway import LLMGateway

        def _boom(*args, **kwargs):  # pragma: no cover - 触发即失败
            raise AssertionError("回放路径不得调用 LLM 网关")

        monkeypatch.setattr(LLMGateway, "chat", _boom)
        report = _compare(compare_env)
        assert report.verdict == "new_better"


class Test部署指针:
    def test_未配置指针返回_None(self, tmp_path):
        config_path = _config_copy(tmp_path)
        assert deployed_version(config_path) is None  # 如实不伪造

    def test_指针读取(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        assert deployed_version(config_path) == "9f2c41ab77de"


class Test采纳门禁:
    """C14 场景 3：未采纳指针不变（机检）；采纳后指针更新 + 记录落盘。"""

    def _adopt(self, env, *, decision, reason="具备优势，采纳", by="sunqi", config_path):
        report = _compare(
            env,
            new_version=env["versions"]["new"],
            deployed_version=env["versions"]["deployed"],
        )
        return adopt(
            report.comparison_id,
            decision,
            by,
            reason,
            config_path=config_path,
            comparison_dir=env["comparison_dir"],
            adoption_dir=env["adoption_dir"],
            history_root=env["history_root"],
        )

    def test_未采纳指针逐字节不变(self, compare_env):
        """机检：未采纳（拒绝）→ 配置副本哈希逐字节不变。"""
        config_path = _config_copy(
            compare_env["tmp_path"], pointer=compare_env["versions"]["deployed"]
        )
        before = hashlib.sha256(config_path.read_bytes()).hexdigest()
        record = self._adopt(
            compare_env, decision="reject", reason="回放证据不足", config_path=config_path
        )
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before  # 逐字节不变
        assert deployed_version(config_path) == compare_env["versions"]["deployed"]
        assert record.decision == "reject"
        assert record.deployed_before == record.deployed_after  # 指针未动
        assert record.adopted_version is None
        assert record.reason == "回放证据不足"
        assert record.record_path.is_file()  # 拒绝同样留痕

    def test_采纳后指针更新且记录落盘(self, compare_env):
        config_path = _config_copy(
            compare_env["tmp_path"], pointer=compare_env["versions"]["deployed"]
        )
        before_lines = config_path.read_text(encoding="utf-8").splitlines()
        record = self._adopt(compare_env, decision="adopt", config_path=config_path)
        assert deployed_version(config_path) == compare_env["versions"]["new"]
        assert record.deployed_before == compare_env["versions"]["deployed"]
        assert record.deployed_after == compare_env["versions"]["new"]
        assert record.adopted_version == compare_env["versions"]["new"]
        assert record.by == "sunqi" and record.reason
        assert record.record_path.is_file()
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["decision"] == "adopt"
        assert stored["comparison_id"] == record.comparison_id  # 依据引用
        # 定点改写：仅指针行变化，其余行逐字节保留
        after_lines = config_path.read_text(encoding="utf-8").splitlines()
        pointer_before = f"    current_policy_version: {compare_env['versions']['deployed']}"
        pointer_after = f"    current_policy_version: {record.adopted_version}"
        assert pointer_before in before_lines and pointer_after in after_lines
        assert [line for line in before_lines if line != pointer_before] == [
            line for line in after_lines if line != pointer_after
        ]

    def test_决策枚举非法拒绝(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="decision"):
            adopt(
                "cmp-x",
                "maybe",
                "sunqi",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"  # 指针不变

    @pytest.mark.parametrize("reason", ["", "   "], ids=["空", "仅空白"])
    def test_理由非空(self, tmp_path, reason):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="理由"):
            adopt(
                "cmp-x",
                "reject",
                "sunqi",
                reason,
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"

    def test_决策人为空拒绝(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="决策人"):
            adopt(
                "cmp-x",
                "adopt",
                "",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )

    def test_依据报告缺失拒绝且指针不变(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(FileNotFoundError, match="对比报告"):
            adopt(
                "cmp-missing",
                "adopt",
                "sunqi",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"

    def test_基线漂移拒绝(self, compare_env):
        """报告基线版本与当前指针不一致（已漂移）→ 拒绝决策，指针不变。"""
        config_path = _config_copy(compare_env["tmp_path"], pointer="000000000000")
        with pytest.raises(AdoptionError, match="漂移"):
            self._adopt(compare_env, decision="adopt", config_path=config_path)
        assert deployed_version(config_path) == "000000000000"

    def test_未版本化策略不得采纳(self, compare_env):
        """待采纳版本不在策略历史内 → 拒绝（未版本化的策略不得部署）。"""
        config_path = _config_copy(
            compare_env["tmp_path"], pointer=compare_env["versions"]["deployed"]
        )
        report = _compare(
            compare_env,
            new_version=compare_env["versions"]["new"],
            deployed_version=compare_env["versions"]["deployed"],
        )
        with pytest.raises(AdoptionError, match="策略历史"):
            adopt(
                report.comparison_id,
                "adopt",
                "sunqi",
                "理由",
                config_path=config_path,
                comparison_dir=compare_env["comparison_dir"],
                adoption_dir=compare_env["adoption_dir"],
                history_root=compare_env["tmp_path"] / "empty-history",
            )
        assert deployed_version(config_path) == compare_env["versions"]["deployed"]


class Test报告读取:
    def test_缺报告拒绝(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_comparison("cmp-missing", comparison_dir=tmp_path / "comparisons")


class Test策略产出面:
    """对比/采纳模块不产策略版本、不触 LLM（结构审计）。"""

    def test_模块不写策略历史与不引用网关(self):
        import inspect

        from agents.screenplay import adoption, sandbox_compare

        for module in (sandbox_compare, adoption):
            source = inspect.getsource(module)
            assert "record_policy" not in source
            assert "LLMGateway" not in source
            assert "gateway" not in source

    def test_评估器引用面(self):
        """回放对比只读历史（ArtifactRef 不参与）：模块不编排评估器。"""
        import inspect

        from agents.screenplay import sandbox_compare

        source = inspect.getsource(sandbox_compare)
        assert "evaluate_screenplay" not in source
        assert "build_screenplay_evaluators" not in source
