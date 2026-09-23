"""开发 Agent 回放对比与采纳单测（功能 017 / T1736，先于实现编写；C14）。

C14：

- **规范化精确匹配**：匹配槽 = `slate_match_key`（策略可复现的结构键：策略版本 / 模型与
  采样档 / 输出预算 / 立项约束摘要 / 组合区间），参数键序乱排仍命中（002 `normalize_params`
  口径）；未命中即 UNKNOWN（不编造）；
- 逐树得分 / 分项评估器差异 / pareto_auc 曲线 / UNKNOWN 记 0 分并提示扩大记录；新版本
  优/劣两路径如实呈现；**未过无偏性验收不得产出对比报告**（FR-013 发布阻塞）；报告只增不改；
- **最小池门槛（前置，SC-011）**：可比对树数 < `dev.min_comparable_trees` ⇒ 拒绝产出报告，
  错误含**实测树数与门槛值**；
- 采纳门禁：未采纳指针逐字节不变（机检副本哈希）；采纳后指针更新 + `AdoptionRecord` 落盘；
  拒绝/采纳理由非空；基线漂移与未版本化版本拒绝；回放零 LLM（网关 chat 被替换为抛错仍可产出）。
"""

import copy
import hashlib
import json
from pathlib import Path

import blake3
import pytest
import yaml

from agents.dev.adoption import (
    AdoptionError,
    AdoptionRecord,
    adopt,
    deployed_version,
)
from agents.dev.loop import SLATE_STAGE, slate_match_key, slate_plan
from agents.dev.sandbox_compare import (
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

_INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}
# 门槛取形态配置（movie = 3）：用例按门槛值构造池子（足量 / 不足两个分支）
_FLOOR = _REAL_CONFIG["dev"]["min_comparable_trees"]

# 两版人工策略的方向池：new 版换了方向取舍（结构键靠策略版本区分，不靠产物摘要）
_ENTRIES_A = [
    {
        "direction_id": direction_id,
        "genre": genre,
        "constraints": list(constraints),
        "characters": list(characters),
    }
    for direction_id, genre, constraints, characters in (
        ("dir-night-ward", "医疗悬疑", ("单场景为主", "夜戏"), ("林静", "陈默")),
        ("dir-city-heist", "都市犯罪", ("群像", "中成本"), ("方原", "邵岚")),
        ("dir-awakening", "科幻悬疑", ("高概念", "单一场景"), ("江离", "织女")),
    )
]
_ENTRIES_B = [
    *copy.deepcopy(_ENTRIES_A),
    {
        "direction_id": "dir-return",
        "genre": "家庭剧情",
        "constraints": ["室内戏"],
        "characters": ["苏禾", "苏玉兰"],
    },
]


def _stage_plan(entries: list[dict]) -> dict:
    """单一阶段计划（规范形态）：`{slate: {entries, production_marks}}`。"""
    return {
        SLATE_STAGE: {
            "entries": copy.deepcopy(entries),
            "production_marks": [entries[0]["direction_id"]],
        }
    }


def _policy_source(plan: dict, *, bump: float = 0.0) -> str:
    """人工策略源码（单一阶段计划内联；bump 只改源码文本 → 版本号变、结构键随之变）。"""
    return (
        "class Policy:\n"
        '    """回放对比用例策略（单一阶段计划内联；接口 plan(inputs, config)）。"""\n'
        f"    PLAN = {plan!r}\n"
        f"    BUMP = {bump!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLAN\n"
    )


def _flat_policy_source(entries: list[dict], *, bump: float = 0.0) -> str:
    """扁平计划策略源码（**引导树形态**：`{entries, production_marks}`，无阶段键）。"""
    plan = {
        "entries": copy.deepcopy(entries),
        "production_marks": [entries[0]["direction_id"]],
    }
    return (
        "class Policy:\n"
        '    """回放对比用例策略（扁平计划：无阶段键，引导树形态）。"""\n'
        f"    PLAN = {plan!r}\n"
        f"    BUMP = {bump!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLAN\n"
    )


def _version_of(source: str) -> str:
    return blake3.blake3(source.encode()).hexdigest()[:12]


def _history(tmp_path: Path, versions: dict[str, str]) -> Path:
    """写策略历史（版本 = 源码 BLAKE3 前 12 位，meta 缺省不影响回放对比）。"""
    root = tmp_path / "history" / "dev"
    root.mkdir(parents=True, exist_ok=True)
    for source in versions.values():
        (root / f"{_version_of(source)}.py").write_text(source, encoding="utf-8")
    return tmp_path / "history"


def _config_copy(tmp_path: Path, *, pointer: str | None = None) -> Path:
    """配置副本（pointer=None → 显式移除部署指针段），用于指针读写与"未采纳不变"机检。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    raw.pop("deployment", None)  # 真实配置的 deployment 段无 dev 子段：副本显式移除后按用例写入
    if pointer is not None:
        raw["deployment"] = {"dev": {"current_policy_version": pointer}}
    path = tmp_path / "movie.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _replay_key(policy_version: str, config) -> dict:
    """记录侧结构键（与线上同构成：`agents/dev/loop.py:slate_match_key`）。"""
    return slate_match_key(policy_version=policy_version, inputs=_INPUTS, config=config)


def _build_tree(
    make_tree,
    make_node,
    store,
    *,
    versions: dict[str, str],
    config,
    scores: dict[str, float],
    project_id: str = "dev-compare",
):
    """一棵冻结轮次树：root + 两版产出节点（gen_params = 各自结构键，得分 = 记录值）。

    单棵树承载两版节点的历史得分：回放按结构键分别命中各自版本（009 对比用例同口径）——
    否则每棵树只覆盖一个版本，"谁更好"就退化成跨树比较。
    """
    primary = versions["deployed"]
    tree = make_tree(
        agent_id="dev",
        project_id=project_id,
        policy_version=primary,
        config_snapshot={
            "evaluator_weights": config.evaluator_weights,
            "observation_fields": ["gen_params", "job_id"],
        },
    )
    store.create_tree(tree)
    store.append_node(
        make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id="dev",
            policy_version=primary,
            eval_breakdown={},
            score=0.0,
            created_at=0.0,
        )
    )
    for index, (label, version) in enumerate(versions.items()):
        score = scores[label]
        store.append_node(
            make_node(
                node_id=new_id(),
                tree_id=tree.tree_id,
                parent_id=tree.root_id,
                depth=1,
                agent_id="dev",
                policy_version=version,
                observation_context={
                    "job_id": f"{label}-{tree.tree_id[-4:]}",
                    "gen_params": _replay_key(version, config),
                },
                artifact_hash=blake3.blake3(f"{tree.tree_id}-{label}".encode()).hexdigest(),
                eval_breakdown={
                    "rule.slate_structure@1.0.0": {"score": score},
                    "rule.slate_combination@1.0.0": {"score": score},
                    "proxy.genre_regression@1.0.0": {"score": score},
                    "proxy.buzz_heat@1.0.0": {"score": score},
                },
                score=score,
                cost=CostRecord(llm_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=float(index + 1),
            )
        )
    return tree


@pytest.fixture()
def compare_env(tree_store, make_tree, make_node, tmp_path, dev_config):
    """对比环境：两版策略 + 历史池（门槛足量棵树，逐树记录两版结构键的得分）。"""
    config = dev_config
    deployed_source = _policy_source(_stage_plan(_ENTRIES_A))
    new_source = _policy_source(_stage_plan(_ENTRIES_B))
    history_root = _history(tmp_path, {"deployed": deployed_source, "new": new_source})
    versions = {"deployed": _version_of(deployed_source), "new": _version_of(new_source)}
    for index, (deployed_score, new_score) in enumerate(((0.50, 0.90), (0.70, 0.80), (0.60, 0.75))):
        _build_tree(
            make_tree,
            make_node,
            tree_store,
            versions=versions,
            config=config,
            scores={"deployed": deployed_score, "new": new_score},
            project_id=f"dev-compare-{index}",
        )
    pool = SimulatorPool(tree_store)
    for tree in tree_store.trees_by(agent_id="dev"):
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
        from agents.dev import adoption, sandbox_compare

        assert callable(sandbox_compare.compare_versions)
        assert callable(sandbox_compare.load_comparison)
        assert callable(sandbox_compare.parse_created_at)
        assert callable(sandbox_compare.replay_policy)
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


class Test计划形态:
    """单一阶段形态为规范形态：回放对比按阶段取结构键（扁平形态取不到覆盖）。"""

    def test_单一阶段计划被解包(self):
        flat = {"entries": [{"direction_id": "a"}], "production_marks": ["a"]}
        assert slate_plan({SLATE_STAGE: flat}) == flat

    def test_扁平形态按兼容处理(self):
        """引导树策略的扁平形态仍被接受（历史不可改写），产出路径不受影响。"""
        flat = {"entries": [{"direction_id": "a"}], "production_marks": ["a"]}
        assert slate_plan(flat) == flat
        assert slate_plan("不是映射") is None

    def test_扁平引导树形态同样可回放(self, tree_store, make_tree, make_node, tmp_path, dev_config):
        """引导树形态（扁平计划）经单一阶段声明后**可回放**（不再退化为全 UNKNOWN）。"""
        config = dev_config
        deployed_source = _flat_policy_source(_ENTRIES_A)
        new_source = _flat_policy_source(_ENTRIES_B)
        history_root = _history(tmp_path, {"deployed": deployed_source, "new": new_source})
        versions = {"deployed": _version_of(deployed_source), "new": _version_of(new_source)}
        for index, (deployed_score, new_score) in enumerate(
            ((0.50, 0.90), (0.70, 0.80), (0.60, 0.75))
        ):
            _build_tree(
                make_tree,
                make_node,
                tree_store,
                versions=versions,
                config=config,
                scores={"deployed": deployed_score, "new": new_score},
                project_id=f"dev-flat-{index}",
            )
        pool = SimulatorPool(tree_store)
        for tree in tree_store.trees_by(agent_id="dev"):
            pool.add_tree(tree)
        report = compare_versions(
            versions["new"],
            versions["deployed"],
            pool,
            config,
            store=tree_store,
            inputs=_INPUTS,
            unbiasedness=_passing_unbiasedness(),
            history_root=history_root,
            comparison_dir=tmp_path / "flat-comparisons",
        )
        assert report.verdict == "new_better"
        assert report.unknown_trees == []  # 扁平计划不再退化为全 UNKNOWN
        assert all(
            row["new_hits"] == ["slate"] and row["deployed_hits"] == ["slate"]
            for row in report.per_tree
        )


class Test回放匹配:
    def test_结构键参数键序乱排仍命中(self, compare_env, make_tree, make_node):
        """002 规范化精确匹配：同一结构键键序乱排仍命中（回放读历史得分，不重算）。"""
        from policies.base import Budget

        version = compare_env["versions"]["deployed"]
        tree = compare_env["pool"].trees[0]
        simulator = SimulatorPool(compare_env["store"])
        simulator.add_tree(tree)
        builder = simulator.build(worker_count=1, budget=Budget(max_probes=1), latency_quantum_ms=0)
        key = _replay_key(version, compare_env["config"])
        result = builder.probe(tree.root_id, dict(reversed(list(key.items()))))
        assert result.status == "ok"
        assert result.nodes[0].score == pytest.approx(0.50)  # 历史得分（回放不重算）

    def test_未命中即_UNKNOWN_零信息(self, compare_env):
        from policies.base import Budget

        tree = compare_env["pool"].trees[0]
        simulator = SimulatorPool(compare_env["store"])
        simulator.add_tree(tree)
        builder = simulator.build(worker_count=1, budget=Budget(max_probes=1), latency_quantum_ms=0)
        result = builder.probe(tree.root_id, {"policy_version": "deadbeef"})
        assert result.status == "unknown"
        assert result.nodes == []

    def test_结构键只含策略可复现字段(self, compare_env):
        """C13：结构键不含生成产物摘要与上游工件哈希（产物一次性，入键即命中率归零）。"""
        key = _replay_key(compare_env["versions"]["deployed"], compare_env["config"])
        assert set(key) == {
            "policy_version",
            "model",
            "temperature",
            "max_tokens",
            "constraint_digest",
            "slate_range",
        }
        for forbidden in ("artifact_hash", "response_hash", "prompt_digest", "candidate_version"):
            assert forbidden not in key


class Test对比报告:
    def test_新版本更优路径(self, compare_env):
        """C14 场景 1：逐树得分/分项差异/pareto_auc 齐全，verdict = new_better。"""
        report = _compare(compare_env)
        assert report.verdict == "new_better"
        assert report.agent_id == "dev"
        assert len(report.per_tree) == 3  # 可比对树数 = 池内已冻结树
        for row in report.per_tree:
            assert set(row) >= {"tree_id", "new_score", "deployed_score", "delta"}
            assert row["delta"] > 0
        assert report.mean_score["new"] > report.mean_score["deployed"]
        assert set(report.pareto_auc) == {"new", "deployed"}
        assert all(0.0 <= value <= 1.0 for value in report.pareto_auc.values())
        assert set(report.pareto_curve) == {"new", "deployed"}
        # 分项评估器差异（四分量，来自命中历史节点的 eval_breakdown）
        assert {item["evaluator_id"] for item in report.per_evaluator} == {
            "rule.slate_structure",
            "rule.slate_combination",
            "proxy.genre_regression",
            "proxy.buzz_heat",
        }
        assert report.unknown_trees == []
        assert report.note == "" or "UNKNOWN" not in report.note
        stored = load_comparison(report.comparison_id, comparison_dir=compare_env["comparison_dir"])
        assert stored["verdict"] == "new_better" and stored["agent_id"] == "dev"

    def test_新版本全劣路径如实呈现(self, compare_env):
        """C14 场景 2：新版本不优于部署版本 → deployed_better + 建议保留现版本。"""
        report = _compare(
            compare_env,
            new_version=compare_env["versions"]["deployed"],
            deployed_version=compare_env["versions"]["new"],
        )
        assert report.verdict == "deployed_better"
        assert "保留现版本" in report.note

    def test_UNKNOWN_树记零并提示扩大记录(self, compare_env, tmp_path):
        """策略结构在历史无覆盖 → 该树 0 分 + 提示扩大记录（不编造）。"""
        ghost_source = _policy_source(_stage_plan(_ENTRIES_B), bump=9.0)
        _history(compare_env["tmp_path"], {"ghost": ghost_source})
        report = _compare(
            compare_env,
            new_version=_version_of(ghost_source),
            deployed_version=compare_env["versions"]["deployed"],
        )
        assert report.mean_score["new"] == 0.0
        assert len(report.unknown_trees) == 3  # 三棵树均无覆盖
        assert "扩大" in report.note and "UNKNOWN" in report.note
        assert all(row["new_score"] == 0.0 for row in report.per_tree)
        assert report.verdict == "deployed_better"  # 无覆盖不得冒充优势

    def test_报告只增不改(self, compare_env):
        report = _compare(compare_env)
        with pytest.raises(FileExistsError):
            _compare(compare_env)
        assert report.comparison_id == f"cmp-dev-{report.new_version}-{report.deployed_version}"

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
        assert _compare(compare_env).verdict == "new_better"


class Test最小池门槛:
    """C14 / SC-011：可比对树数 < `dev.min_comparable_trees` ⇒ 拒绝产出报告。"""

    def test_树数不足拒绝产出且错误含实测与门槛(
        self, tree_store, make_tree, make_node, tmp_path, dev_config
    ):
        config = dev_config
        deployed_source = _policy_source(_stage_plan(_ENTRIES_A))
        new_source = _policy_source(_stage_plan(_ENTRIES_B))
        history_root = _history(tmp_path, {"deployed": deployed_source, "new": new_source})
        versions = {"deployed": _version_of(deployed_source), "new": _version_of(new_source)}
        for index, score in enumerate((0.5, 0.6)):
            _build_tree(
                make_tree,
                make_node,
                tree_store,
                versions=versions,
                config=config,
                scores={"deployed": score, "new": score},
                project_id=f"dev-underfloor-{index}",
            )
        pool = SimulatorPool(tree_store)
        for tree in tree_store.trees_by(agent_id="dev"):
            pool.add_tree(tree)
        comparison_dir = tmp_path / "comparisons"
        assert len(pool.trees) == 2 < _FLOOR
        with pytest.raises(CompareError) as exc:
            compare_versions(
                _version_of(new_source),
                _version_of(deployed_source),
                pool,
                config,
                store=tree_store,
                inputs=_INPUTS,
                unbiasedness=_passing_unbiasedness(),
                history_root=history_root,
                comparison_dir=comparison_dir,
            )
        message = str(exc.value)
        assert "可比对树数不足" in message
        assert f"实测 {len(pool.trees)}" in message  # 实测树数
        assert f"门槛 {_FLOOR}" in message  # 门槛值
        assert list(comparison_dir.glob("*.json")) == []  # 0 报告产出

    def test_达到门槛即产出报告(self, compare_env):
        assert len(compare_env["pool"].trees) >= _FLOOR
        assert _compare(compare_env).verdict == "new_better"

    def test_门槛缺失即报错(self, compare_env):
        """门槛来自形态配置（`dev.min_comparable_trees`）：缺项即报错，不静默放行。"""
        from dataclasses import replace

        stale = replace(compare_env["config"])
        object.__setattr__(stale, "min_comparable_trees", None)
        with pytest.raises(CompareError, match="min_comparable_trees"):
            compare_versions(
                compare_env["versions"]["new"],
                compare_env["versions"]["deployed"],
                compare_env["pool"],
                stale,
                store=compare_env["store"],
                inputs=_INPUTS,
                unbiasedness=_passing_unbiasedness(),
                history_root=compare_env["history_root"],
                comparison_dir=compare_env["comparison_dir"],
            )
        assert list(compare_env["comparison_dir"].glob("*.json")) == []


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
        report = _compare(env)
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
        """机检：未采纳（拒绝）→ 配置副本哈希逐字节不变（SC-002）。"""
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
        assert record.agent_id == "dev"
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["decision"] == "adopt"
        assert stored["comparison_id"] == record.comparison_id  # 依据引用
        # 定点改写：仅指针行变化，注释与其他行逐字节保留
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
        report = _compare(compare_env)
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


class Test模拟源标注同源:
    """SC-009 第二处：对比报告面携带模拟源载荷，且**不另写一份标注口径**。"""

    def test_报告面标注与产物同源(self, compare_env):
        import inspect

        from agents.dev import sandbox_compare as module
        from agents.dev.artifact import simulated_signal_sources

        assert module.signal_sources_of(compare_env["config"]) == simulated_signal_sources(
            compare_env["config"].signals
        )
        for source in module.signal_sources_of(compare_env["config"]):
            assert source["simulated"] is True
            assert "非真实商业数据" in source["note"]
            assert len(source["params_digest"]) == 12
        # 标注文案只在产物模块实现一次（本模块不得内联第二份口径）
        assert "非真实商业数据" not in inspect.getsource(module)

    def test_缺模拟源参数即报错(self):
        from types import SimpleNamespace

        from agents.dev.sandbox_compare import signal_sources_of

        with pytest.raises(CompareError, match="dev.signals"):
            signal_sources_of(SimpleNamespace(min_comparable_trees=1))


class Test策略产出面:
    """对比/采纳模块不产策略版本、不触 LLM、不编排评估器（结构审计）。"""

    def test_模块不引用网关与策略历史写入(self):
        import inspect

        from agents.dev import adoption, sandbox_compare

        for module in (sandbox_compare, adoption):
            source = inspect.getsource(module)
            assert "record_policy" not in source
            assert "LLMGateway" not in source
            assert "gateway" not in source

    def test_不编排评估器(self):
        import inspect

        from agents.dev import sandbox_compare

        source = inspect.getsource(sandbox_compare)
        assert "build_dev_evaluators" not in source
        assert "evaluate_dev" not in source
