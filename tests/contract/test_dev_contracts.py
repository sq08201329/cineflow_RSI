"""开发 Agent 契约聚合（功能 017 / T1739；SC-001/002/005/006/009/011）。

把 C1~C18 里**只有在开发 Agent 线上路径才成立**的断言集中到一处（各契约的细颗粒用例在
`tests/unit/test_dev_*.py`），落点在真实装配上而非桩上：

- **C13/原则三**：回放路径零 LLM、`max_generation_calls == 0`、匹配键只含策略可复现的
  结构键（不含产物摘要）；产出 → 回放得分即历史得分（不重算）;
- **C14/T1701 两项落地义务**：① 策略执行**超时真的会触发**（死循环策略判 `CompareError`，
  不挂死宿主）；② "**不向策略执行交付任何环境对象**"的守护**真的会拒绝**（inputs/config
  携带探测或观测通道即报错）;
- **C1/C4~C10**：`core/degraded/` 零业务词、`agents/dev/` 零形态字面量与零 `agent_id ==`
  分支；四评估器装配与确定性；模拟源标注（产物 / 对比报告 / 判据材料三处）;
- **C2/C3/C11/C12**：多轮产出与成本对账、材料与报告的按 agent 落点（`dev/` 子目录）、
  采纳指针段 `deployment.dev`。
"""

import inspect
import json
import re
import time
from pathlib import Path

import blake3
import pytest

from agents.dev import adoption as dev_adoption
from agents.dev import sandbox_compare
from agents.dev.artifact import SIMULATED_SOURCE_IDS, TopicSlate
from agents.dev.evaluators import build_dev_evaluators
from agents.dev.loop import run_dev_round, slate_match_key
from agents.dev.sandbox_compare import CompareError, replay_policy
from agents.dev.upgrade_evidence import CONCLUSIONS, MISSING_SOURCES, build_upgrade_evidence
from core.degraded import compare as core_compare
from core.replay.pool import SimulatorPool
from core.tree.artifacts import LocalArtifactStore
from policies.base import Budget, assert_replay_budget

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR = REPO_ROOT / "agents" / "dev"
INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}

# 单一阶段策略源码（规范形态）：产出与回放共用同一份源码 ⇒ 结构键一致（回放命中）
_ENTRIES = [
    {
        "direction_id": "dir-night-ward",
        "genre": "医疗悬疑",
        "constraints": ["单场景为主", "夜戏"],
        "characters": ["林静", "陈默"],
    },
    {
        "direction_id": "dir-city-heist",
        "genre": "都市犯罪",
        "constraints": ["群像", "中成本"],
        "characters": ["方原", "邵岚"],
    },
    {
        "direction_id": "dir-awakening",
        "genre": "科幻悬疑",
        "constraints": ["高概念", "单一场景"],
        "characters": ["江离", "织女"],
    },
]
_PLAN = {"slate": {"entries": _ENTRIES, "production_marks": ["dir-night-ward"]}}
_POLICY_SOURCE = (
    "class Policy:\n"
    '    """契约聚合用例策略（单一阶段计划内联）。"""\n'
    f"    PLAN = {_PLAN!r}\n\n"
    "    def plan(self, inputs, config):\n"
    "        return self.PLAN\n"
)
# 版本 = 源码 BLAKE3 前 12 位（人工策略口径）：产出与回放共用同一份源码 ⇒ 版本一致
_VERSION = blake3.blake3(_POLICY_SOURCE.encode()).hexdigest()[:12]
_DEAD_LOOP_SOURCE = (
    "class Policy:\n"
    '    """死循环策略：静态检查不禁循环，回放必须带执行超时（T1701 义务①）。"""\n'
    "    def plan(self, inputs, config):\n"
    "        while True:\n"
    "            pass\n"
)


class _FakeEnvironment:
    """伪环境对象（模拟器形态）：暴露探测与观测通道（义务②的拒绝面）。"""

    def observed(self) -> dict:
        return {}

    def probe(self, parent_id: str, gen_params: dict):  # pragma: no cover - 只作形态
        raise AssertionError("不得被触达")


def _policy_of(source: str, version: str):
    namespace: dict = {"__name__": "dev_contract_policy"}
    exec(compile(source, "<dev-contract-policy>", "exec"), namespace)  # noqa: S102 - 夹具源码
    policy = namespace["Policy"]()
    policy.policy_version = version
    return policy


@pytest.fixture()
def dev_round_engine(dev_jobs_engine):
    from core.tree.db import create_schema

    create_schema(dev_jobs_engine)
    return dev_jobs_engine


@pytest.fixture()
def dev_gateway(dev_config):
    """确定性 Mock 网关 + dev 形态价目（sleep 注入以消除真实退避）。"""
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway

    return LLMGateway(MockBackend(), price_book=dev_config.model_prices, sleep=lambda _: None)


@pytest.fixture()
def produced_round(dev_round_engine, dev_config, dev_data_dir, dev_gateway):
    """真实装配跑一轮（单一阶段策略）→ (round_result, store, artifacts)。"""
    from core.tree.store import create_tree_store

    store = create_tree_store(dev_round_engine)
    artifacts = LocalArtifactStore(dev_data_dir / "artifacts")
    result = run_dev_round(
        round_id="contract-r1",
        policy=_policy_of(_POLICY_SOURCE, _VERSION),
        store=store,
        artifacts=artifacts,
        engine=dev_round_engine,
        gateway=dev_gateway,
        config=dev_config,
        inputs=INPUTS,
        evaluators=None,  # 真实四评估器装配
    )
    return result, store, artifacts


class Test回放路径零LLM与结构键:
    """C13 / 原则三：回放零 LLM、零生成，匹配键只含策略可复现的结构键。"""

    def test_回放得分即历史得分且网关零调用(
        self, produced_round, dev_config, dev_gateway, monkeypatch
    ):
        result, store, _ = produced_round
        calls_before = dev_gateway.call_count
        tree = next(tree for tree in store.trees_by(agent_id="dev"))
        replay = replay_policy(_POLICY_SOURCE, [tree], cfg=dev_config, inputs=INPUTS, store=store)
        assert dev_gateway.call_count == calls_before  # 回放零 LLM（不触网关）
        row = replay.per_tree[0]
        assert row["hits"] == ["slate"] and row["misses"] == []  # 结构键精确命中
        recorded = next(
            node for node in store.nodes_of(result.tree_id) if node.parent_id is not None
        )
        assert row["score"] == pytest.approx(recorded.score)  # 历史得分，不重算
        assert sorted(replay.per_evaluator) == [
            "proxy.buzz_heat",
            "proxy.genre_regression",
            "rule.slate_combination",
            "rule.slate_structure",
        ]

    def test_回放预算_max_generation_calls_归零(self, produced_round, dev_config, monkeypatch):
        """FR-002/FR-005：回放装配的预算恒 `max_generation_calls == 0`（强制归零 + 断言）。"""
        _, store, _ = produced_round
        tree = next(tree for tree in store.trees_by(agent_id="dev"))
        seen: list[Budget] = []
        original = SimulatorPool.build

        def _spy(self, *, worker_count, budget, latency_quantum_ms):
            assert_replay_budget(budget)  # 第二道断言（policies/base.py）
            seen.append(budget)
            return original(
                self,
                worker_count=worker_count,
                budget=budget,
                latency_quantum_ms=latency_quantum_ms,
            )

        monkeypatch.setattr(SimulatorPool, "build", _spy)
        replay_policy(_POLICY_SOURCE, [tree], cfg=dev_config, inputs=INPUTS, store=store)
        assert seen and all(budget.max_generation_calls == 0 for budget in seen)

    def test_匹配键只含策略可复现结构键(self, dev_config):
        key = slate_match_key(policy_version=_VERSION, inputs=INPUTS, config=dev_config)
        assert key["policy_version"] == _VERSION
        assert set(key) == {
            "policy_version",
            "model",
            "temperature",
            "max_tokens",
            "constraint_digest",
            "slate_range",
        }
        # 生成产物摘要、网关联络键与上游工件字段一律不入键（产物一次性，入键即命中率归零）
        for forbidden in (
            "artifact_hash",
            "response_hash",
            "cache_key",
            "prompt_digest",
            "params_hash",
        ):
            assert forbidden not in key
        source = inspect.getsource(sandbox_compare)
        assert "slate_match_key" in source  # 薄适配绑定的是结构键
        assert "artifact_hash" not in source

    def test_策略执行只拿_inputs_与_config(self, produced_round):
        """交付面锁定：策略形参即 `(inputs, config)`（其余一切由宿主代执行）。"""
        _, _, _ = produced_round
        source = inspect.getsource(core_compare.replay_policy)
        assert "delivered = (inputs, cfg)" in source
        assert "policy.plan(*delivered)" in source


class Test两项落地义务:
    """C14 / T1701：宪章 v2.0.0 原则四例外条款的两项义务在**开发 Agent 路径**上成立。"""

    def test_策略执行超时真的触发(self, produced_round, dev_config, monkeypatch):
        """①：死循环策略判 `CompareError`（不挂死宿主）——超时真的会到。"""
        _, store, _ = produced_round
        tree = next(tree for tree in store.trees_by(agent_id="dev"))
        monkeypatch.setattr(core_compare, "POLICY_EXECUTION_TIMEOUT_SECONDS", 0.2)
        started = time.perf_counter()
        with pytest.raises(CompareError, match="超时"):
            replay_policy(_DEAD_LOOP_SOURCE, [tree], cfg=dev_config, inputs=INPUTS, store=store)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0  # 宿主未被死循环占用（超时截断生效）

    def test_无环境对象守护真的拒绝(self, produced_round, dev_config):
        """②：交付面携带环境对象（探测/观测通道）即拒绝——断言不是装饰。"""
        _, store, _ = produced_round
        tree = next(tree for tree in store.trees_by(agent_id="dev"))
        leaky_inputs = {**INPUTS, "leak": _FakeEnvironment()}
        with pytest.raises(CompareError, match="不得向策略执行交付环境对象"):
            replay_policy(_POLICY_SOURCE, [tree], cfg=dev_config, inputs=leaky_inputs, store=store)

    def test_超时上限是有限值(self):
        assert 0 < core_compare.POLICY_EXECUTION_TIMEOUT_SECONDS < 60


class Test静态纯净性:
    """C1 / 原则五：业务件零形态字面量与零 Agent 名分支；通用件零业务词。"""

    def test_开发业务件零形态字面量与零_agent_名分支(self):
        pattern = re.compile(r"agent_id\s*==|==\s*['\"]dev['\"]|['\"]shortdrama['\"]")
        for path in sorted(DEV_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert not pattern.search(source), f"{path} 出现形态字面量或 agent_id 分支"
            assert '"movie"' not in source and "'movie'" not in source

    def test_通用件零业务词与零_agents_依赖(self):
        for path in sorted((REPO_ROOT / "core" / "degraded").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "from agents" not in source and "import agents" not in source
            for word in ("slate", "screenplay", "shortdrama", "选题"):
                assert word not in source, f"{path} 出现业务词 {word!r}"

    def test_开发包不反向依赖_ops(self):
        for path in sorted(DEV_DIR.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "from ops" not in source and "import ops" not in source


class Test四评估器与标注:
    """C7~C10 / SC-009：四分量装配、确定性、模拟源标注三处可见。"""

    def test_装配两门禁两代理且确定性(self, dev_config, produced_round):
        assembly = build_dev_evaluators(dev_config)
        ids = [evaluator.spec.evaluator_id for evaluator in assembly["all"]]
        assert ids == [
            "rule.slate_structure",
            "rule.slate_combination",
            "proxy.genre_regression",
            "proxy.buzz_heat",
        ]
        assert all(evaluator.spec.deterministic for evaluator in assembly["all"])
        # 同输入重算逐位一致（确定性是回放可打分的前提，原则一）
        for evaluator in assembly["all"]:
            assert evaluator.spec.version == evaluator.spec.version

    def test_产物与判据材料两处标注可机读(self, produced_round, dev_config, tmp_path):
        result, store, artifacts = produced_round
        node = next(node for node in store.nodes_of(result.tree_id) if node.parent_id is not None)
        import json

        slate = json.loads(artifacts.get(node.artifact_hash))
        sources = slate["signal_sources"]
        assert [source["source"] for source in sources] == list(SIMULATED_SOURCE_IDS)
        assert all(source["simulated"] and "非真实商业数据" in source["note"] for source in sources)
        # 代理分量诊断同样带标注（报告侧读数同源）
        for key, fragment in node.eval_breakdown.items():
            if key.startswith("proxy."):
                diagnostics = fragment["diagnostics"]
                assert diagnostics["simulated"] is True
                assert "非真实商业数据" in diagnostics["note"]
        # 判据材料（第三处）：代理分布随来源标记载荷一并落盘
        material = build_upgrade_evidence(
            "2026-W38", dev_config, nodes=store.nodes_of(result.tree_id), data_dir=tmp_path
        )
        assert len(material.raw["signal_sources"]) == 2
        assert all(
            source["simulated"] is True and "非真实商业数据" in source["note"]
            for source in material.raw["signal_sources"]
        )
        # 对比报告面（SC-009 第二处）：与产物/材料同一载荷、同一事实源
        from agents.dev.artifact import simulated_signal_sources
        from agents.dev.sandbox_compare import signal_sources_of

        assert signal_sources_of(dev_config) == simulated_signal_sources(dev_config.signals)
        assert material.raw["signal_sources"] == list(simulated_signal_sources(dev_config.signals))


# 仓库人工策略首版（谱系根；**扁平计划**形态）与其部署指针
BOOTSTRAP_VERSION = "34525518074d"


def _rerun_score(node, artifacts, config, assembly):
    """真实重跑：读落盘工件 → 真实四评估器重算 → 合成定点（确定性、零 LLM）。"""
    from agents.dev.evaluators.composite import evaluate_dev
    from core.evaluators.base import ArtifactRef

    slate = TopicSlate.from_dict(json.loads(artifacts.get(node.artifact_hash)))
    reference = ArtifactRef(artifact_hash=node.artifact_hash, metadata={"agent_id": "dev"})
    context = {"artifact": slate, "inputs": INPUTS, "config": config}
    _, score, _ = evaluate_dev(assembly, reference, context, config.evaluator_weights)
    return score


def _bootstrap_source() -> str:
    """仓库人工策略首版源码（回放可命中的端到端回归对象）。"""
    return (REPO_ROOT / "policies" / "history" / "dev" / f"{BOOTSTRAP_VERSION}.py").read_text(
        encoding="utf-8"
    )


class Test引导策略端到端可回放:
    """回归：仓库首版策略（扁平计划）在池上回放**端到端**命中（非仅投影单测）。

    既往缺陷：扁平计划在按阶段取标记的口径下每阶段都未命中 → 报告全 UNKNOWN。现在本 Agent
    以 `single_stage=SLATE_STAGE` 声明唯一阶段 → 扁平首版策略同样可回放、可对比。
    """

    def test_首版策略确实是扁平计划(self, dev_config):
        source = _bootstrap_source()
        assert blake3.blake3(source.encode()).hexdigest()[:12] == BOOTSTRAP_VERSION
        plan = _policy_of(source, BOOTSTRAP_VERSION).plan(INPUTS, dev_config)
        assert "slate" not in plan  # 扁平形态：无阶段键
        assert set(plan) >= {"entries", "production_marks"}

    def test_扁平首版策略端到端回放命中(
        self, dev_jobs_engine, dev_config, dev_data_dir, dev_gateway, tmp_path
    ):
        """3 轮首版（扁平）策略 + 1 轮新版本 → 回放对比：两侧在每棵树上都命中。"""
        from agents.dev.policy_versions import submit_policy
        from agents.dev.sandbox_compare import UnbiasednessAttestation, compare_versions
        from core.replay.unbiasedness import verify_unbiasedness
        from core.tree.db import create_schema
        from core.tree.store import create_tree_store
        from dreaming.config import DreamConfig

        create_schema(dev_jobs_engine)
        store = create_tree_store(dev_jobs_engine)
        artifacts = LocalArtifactStore(dev_data_dir / "bootstrap-artifacts")
        history_root = tmp_path / "policies"
        dream_config = DreamConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        bootstrap_source = _bootstrap_source()
        candidate_source = _POLICY_SOURCE
        bootstrap = submit_policy(
            bootstrap_source, "sunqi", dream_config, history_root=history_root
        )
        candidate = submit_policy(
            candidate_source,
            "sunqi",
            dream_config,
            parent_version=bootstrap.version,
            history_root=history_root,
        )
        assert bootstrap.version == BOOTSTRAP_VERSION  # 版本 = 源码 BLAKE3 前 12 位（幂等落盘）
        sources = {bootstrap.version: bootstrap_source, candidate.version: candidate_source}

        def _produce(round_id: str, version: str):
            return run_dev_round(
                round_id=round_id,
                policy=_policy_of(sources[version], version),
                store=store,
                artifacts=artifacts,
                engine=dev_jobs_engine,
                gateway=dev_gateway,
                config=dev_config,
                inputs=INPUTS,
                evaluators=None,  # 真实四评估器装配
            )

        for index in range(3):
            _produce(f"boot-r{index}", bootstrap.version)
        _produce("boot-new", candidate.version)
        pool = SimulatorPool(store)
        for tree in store.trees_by(agent_id="dev"):
            pool.add_tree(tree)
        assert len(pool.trees) == 4 >= dev_config.min_comparable_trees

        # 无偏性：回放（probe 历史得分）vs 真实重跑（工件经真实四评估器重算）
        assembly = build_dev_evaluators(dev_config)
        replay_scores: list[float] = []
        real_scores: list[float] = []
        for tree in pool.trees:
            row = replay_policy(
                sources[tree.policy_version],
                [tree],
                cfg=dev_config,
                inputs=INPUTS,
                store=store,
            ).per_tree[0]
            replay_scores.append(row["score"])
            node = next(item for item in store.nodes_of(tree.tree_id) if item.parent_id is not None)
            real_scores.append(_rerun_score(node, artifacts, dev_config, assembly))
        unbiased = verify_unbiasedness(real_scores, replay_scores, threshold=0.95)
        assert unbiased.verdict == "pass", unbiased.notes
        attestation_path = tmp_path / "unbiasedness.json"
        attestation_path.write_text(
            json.dumps(unbiased.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

        comparison = compare_versions(
            candidate.version,
            bootstrap.version,
            pool,
            dev_config,
            store=store,
            inputs=INPUTS,
            unbiasedness=UnbiasednessAttestation.load(attestation_path),
            history_root=history_root,
            comparison_dir=tmp_path / "comparisons",
        )
        # 扁平首版策略产出的三轮**全部命中**（旧行为下这三轮会是 UNKNOWN）
        bootstrap_rows = [row for row in comparison.per_tree if row["deployed_hits"]]
        candidate_rows = [row for row in comparison.per_tree if row["new_hits"]]
        assert len(bootstrap_rows) == 3
        assert all(row["deployed_hits"] == ["slate"] for row in bootstrap_rows)
        assert len(candidate_rows) == 1
        assert all(row["new_hits"] == ["slate"] for row in candidate_rows)
        # 每一轮只承载该版本节点 → 另一侧如实记 UNKNOWN（覆盖说明，不编造）
        assert len(comparison.unknown_trees) == len(pool.trees) == 4
        assert comparison.mean_score["deployed"] > 0
        assert comparison.mean_score["new"] > 0
        assert len(comparison.per_evaluator) == 4


class Test落点与门禁:
    """C2/C3/C11/C12/C16~C18：按 agent 落点、成本对账、材料结论非达标。"""

    def test_轮次成本对账一致(self, produced_round):
        result, _, _ = produced_round
        assert result.job["status"] == "inserted"
        assert result.cost_reconciliation["consistent"] is True
        assert result.cost_reconciliation["evaluator_cost_usd"] == 0.0  # 代理确定性无 LLM 调用
        assert result.spent_usd > 0

    def test_材料路径与结论取值域(self, produced_round, dev_config, tmp_path):
        result, store, _ = produced_round
        material = build_upgrade_evidence(
            "2026-W39", dev_config, nodes=store.nodes_of(result.tree_id), data_dir=tmp_path
        )
        assert (tmp_path / "dev" / "2026-W39.json").is_file()  # 按 agent 分目录
        assert material.conclusion in CONCLUSIONS  # 恒不为"达标"
        assert "meets" not in CONCLUSIONS
        assert {entry["key"] for entry in MISSING_SOURCES} == {"reliability", "drift"}

    def test_采纳指针段为_deployment_dev(self, produced_round, tmp_path):
        """C14：dev 的部署指针段 = `deployment.dev`（与 009 的 screenplay 子段并列）。

        两形态配置均声明初始指针 = 人工策略首版（谱系根），故 `ops/dev.py adopt --config
        configs/*.yaml` 的基线一致性检查有可比对的当前指针；此处再验定点 upsert 的可用性。
        """
        config_path = tmp_path / "movie.yaml"
        config_path.write_text(
            (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        # 仓库两形态配置的初始值 = 人工策略首版（谱系根，防漂移）
        for path in (
            REPO_ROOT / "configs" / "movie.yaml",
            REPO_ROOT / "configs" / "shortdrama.yaml",
        ):
            assert dev_adoption.deployed_version(path) == "34525518074d"
            assert (
                REPO_ROOT / "policies" / "history" / "dev" / "34525518074d.py"
            ).is_file()  # 指针指向的版本确实在策略历史内
        assert dev_adoption.deployed_version(config_path) == "34525518074d"
        from core.yaml_edit import upsert_section_entries

        updated = upsert_section_entries(
            config_path.read_text(encoding="utf-8"),
            ("deployment", "dev"),
            {"current_policy_version": "contract-v2"},
        )
        config_path.write_text(updated, encoding="utf-8")
        assert dev_adoption.deployed_version(config_path) == "contract-v2"
        assert "dev:" in updated
