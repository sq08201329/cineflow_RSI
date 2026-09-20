"""剧本回放无偏性验收（功能 009 / T937，先于实现编写，发布阻塞门禁 FR-013/SC-008）。

剧本夹具池（2 棵不同时间树 × 4 组结构梯度）：回放打分（`SimulatorPool` probe 揭示的
历史得分，`gen_params` 结构键规范化精确匹配）vs 真实重跑（网关缓存命中下重执行 +
真实七评估器重算 + 合成定点；协议注入路径 `evaluators=<list>` 免 judge 计费）
得分序列 Kendall τ ≥ 0.95 放行；注入偏差 ≥3 形态 100% 拒绝；
**未达标不得产出回放对比报告**（`compare_versions` 前置门禁）。
复用 core/replay/unbiasedness.py（002 同套件，008 同口径接入）。
"""

import copy
import random
from pathlib import Path

import pytest
import yaml

from agents.screenplay.artifact import ScriptArtifact
from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.evaluators import build_screenplay_evaluators
from agents.screenplay.evaluators.composite import composite_screenplay
from agents.screenplay.loop import stage_match_key
from agents.screenplay.sandbox_compare import CompareError, compare_versions
from core.evaluators.base import ArtifactRef
from core.evaluators.quantize import quantize_score
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.models import CostRecord, NodeStatus, new_id
from policies.base import Budget
from policies.versioning import policy_version

pytestmark = pytest.mark.unbiasedness

REPO_ROOT = Path(__file__).resolve().parents[2]
_TAU_THRESHOLD = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))[
    "replay"
]["unbiasedness_tau_threshold"]

_INPUTS = {"topic": "病房里的三个月", "target_duration_min": 3, "characters": ["林静", "陈默"]}
_POLICY_VERSION = "9f2c41ab77de"  # 人工策略版本（夹具口径）
_STAGE = "script"  # 非大纲阶段：judge 不适用 → 协议注入路径零 judge 计费


def _config() -> ScreenplayConfig:
    """无偏性夹具配置：页数窗口按夹具收窄（9 行 → 3 页），其余取真实 movie.yaml。"""
    raw = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
    )
    raw["screenplay"].update({"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3})
    return ScreenplayConfig.from_dict(raw)


def _gateway(config: ScreenplayConfig) -> LLMGateway:
    return LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)


def _markers_ladder(make_script_artifact) -> list[dict]:
    """8 组结构梯度（4 档同名异写 × 2 档时间线回退）：代理扣分 → 得分谱系分散。

    门禁不受影响（节拍/页数/场景角色/比例恒定）：同名异写只改**行归属指称**，
    时间线只改场景 `time_marker`（非降序被打破 → 代理扣分）。组合后的得分谱系
    覆盖 [0.533, 1.0]，供无偏性验收（τ 具判别力）。
    """
    payloads = []
    for entity_hits in range(4):
        for timeline_hits in range(2):
            payload = make_script_artifact(stage=_STAGE).to_dict()
            # 同名异写（未登记但与登记名同源 → entity 代理逐条扣分）
            injected = 0
            for line in payload["lines"]:
                if line["kind"] == "dialogue" and injected < entity_hits:
                    line["character"] = "小静"  # 林静 的另一种写法
                    injected += 1
            # 时间线回退（scene-3 时间戳回退到 scene-2 之前 → timeline 代理命中）
            if timeline_hits:
                payload["scenes"][2]["time_marker"] = (
                    payload["scenes"][1]["time_marker"] - timeline_hits
                )
            payloads.append(
                {
                    "beats": payload["beats"],
                    "scenes": payload["scenes"],
                    "characters": payload["characters"],
                    "lines": payload["lines"],
                }
            )
    return payloads


def _real_rerun_score(markers: dict, config: ScreenplayConfig, evaluators) -> float:
    """真实重跑：真实七评估器重算 + 合成定点（协议注入路径，judge 不适用 → 零计费）。"""
    artifact = ScriptArtifact(stage=_STAGE, text="（网关正文占位）", **markers)
    artifact_ref = ArtifactRef(artifact_hash="ab" * 32, metadata={"stage": _STAGE})
    context = {
        "artifact": artifact,
        "stage": _STAGE,
        "inputs": _INPUTS,
        "markers": markers,
        "config": config,
    }
    breakdown = {}
    for evaluator in evaluators:
        result = evaluator.evaluate(artifact_ref, context)
        breakdown[evaluator.spec.key] = {"score": result.score, "diagnostics": result.diagnostics}
    return quantize_score(composite_screenplay(breakdown, config.evaluator_weights))


def _key_of(markers: dict, config: ScreenplayConfig) -> dict:
    return stage_match_key(
        _STAGE,
        policy_version=_POLICY_VERSION,
        inputs=_INPUTS,
        config=config,
        markers=markers,
    )


def _screenplay_pool(tree_store, make_tree, make_node, markers_ladder, real_scores, config):
    """2 棵不同时间剧本树：子节点 gen_params = 结构键，得分 = 真实重跑值。"""
    trees = []
    per_tree = len(markers_ladder) // 2
    for t in range(2):
        tree = make_tree(
            agent_id="screenplay",
            project_id="screenplay-unbiased",
            policy_version=_POLICY_VERSION,
            config_snapshot={
                "evaluator_weights": config.evaluator_weights,
                "observation_fields": ["gen_params", "stage", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="screenplay",
                policy_version=_POLICY_VERSION,
                eval_breakdown={},
                score=0.0,
                status=NodeStatus.EVALUATED,
                created_at=float(t * 100),
            )
        )
        for i in range(per_tree):
            idx = t * per_tree + i
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="screenplay",
                    policy_version=_POLICY_VERSION,
                    observation_context={
                        # 回放匹配槽（002 规范化精确匹配键）：策略可复现结构键
                        "gen_params": _key_of(markers_ladder[idx], config),
                        "stage": _STAGE,
                        "job_id": f"ub-j{idx}",
                    },
                    score=real_scores[idx],
                    cost=CostRecord(llm_calls=1),
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 100 + 1 + i),
                )
            )
        trees.append(tree)
    return trees


def _replay_scores(trees, tree_store, markers_ladder, config) -> list[float]:
    """回放打分：池化模拟器按结构键 probe 收集历史得分（键序乱排仍命中）。"""
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    root_of = {tree.tree_id: tree.root_id for tree in trees}
    per_tree = len(markers_ladder) // 2
    scores = []
    for idx, markers in enumerate(markers_ladder):
        tree = trees[idx // per_tree]
        simulator = SimulatorPool(tree_store)
        simulator.add_tree(tree)
        builder = simulator.build(
            worker_count=1, budget=Budget(max_probes=len(markers_ladder)), latency_quantum_ms=0
        )
        key = _key_of(markers, config)
        # 键序乱排（规范化精确匹配：同语义参数必得同字符串，002 `normalize_params` 口径）
        probe_key = dict(reversed(list(key.items()))) if idx % 2 else key
        result = builder.probe(root_of[tree.tree_id], probe_key)
        assert result.status == "ok", f"结构键 {idx} 未命中（回放精确匹配被破坏）"
        scores.append(result.nodes[0].score)
    return scores


@pytest.fixture()
def screenplay_unbiased_fixture(
    tree_store, make_tree, make_node, make_script_artifact, screenplay_config
):
    """剧本无偏性夹具：结构梯度序列 + 真实重跑序列 + 池化回放序列。"""
    config = _config()
    gateway = _gateway(config)
    # 真实七评估器装配（协议注入路径：列表 → 逐评估器打分 + 正式合成口径）
    assembly = build_screenplay_evaluators(config, gateway)
    evaluators = assembly["all"]
    markers_ladder = _markers_ladder(make_script_artifact)
    real = [_real_rerun_score(markers, config, evaluators) for markers in markers_ladder]
    trees = _screenplay_pool(tree_store, make_tree, make_node, markers_ladder, real, config)
    replay = _replay_scores(trees, tree_store, markers_ladder, config)
    return {
        "real": real,
        "replay": replay,
        "markers": markers_ladder,
        "trees": trees,
        "store": tree_store,
        "config": config,
        "gateway": gateway,
        "evaluators": evaluators,
    }


class Test剧本无偏性放行:
    def test_回放对真实重跑_tau达标(self, screenplay_unbiased_fixture):
        real = screenplay_unbiased_fixture["real"]
        replay = screenplay_unbiased_fixture["replay"]
        assert len(real) == 8  # 2 棵树 × 4 组结构梯度
        report = verify_unbiasedness(real, replay, threshold=_TAU_THRESHOLD)
        assert report.verdict == "pass", report.notes
        assert report.tau >= 0.95, report.notes

    def test_门槛取_configs_口径(self, screenplay_unbiased_fixture):
        assert _TAU_THRESHOLD == 0.95
        report = verify_unbiasedness(
            screenplay_unbiased_fixture["real"], screenplay_unbiased_fixture["replay"]
        )
        assert report.verdict == "pass"

    def test_回放得分即历史得分(self, screenplay_unbiased_fixture):
        """回放只读历史节点得分（不重算、不生成）——序列逐位相等。"""
        assert screenplay_unbiased_fixture["replay"] == screenplay_unbiased_fixture["real"]

    def test_得分区分度(self, screenplay_unbiased_fixture):
        """梯度有效性前提：得分谱系足够分散（τ 有判别力）。"""
        real = screenplay_unbiased_fixture["real"]
        assert len(set(real)) >= 4, f"得分区分度不足（梯度失效）：{real}"

    def test_网关缓存命中下重执行逐字节一致(self, screenplay_unbiased_fixture):
        """FR-013 可复现基础：同输入重执行命中网关缓存（零成本、文本逐字节一致）。"""
        gateway = screenplay_unbiased_fixture["gateway"]
        config = screenplay_unbiased_fixture["config"]
        prompt = "【剧本生成 · script 阶段】可复现性前提断言"
        first = gateway.chat(prompt, model=config.model, temperature=0.0, max_tokens=1024)
        second = gateway.chat(prompt, model=config.model, temperature=0.0, max_tokens=1024)
        assert first.cached is False and first.cost_usd > 0
        assert second.cached is True and second.cost_usd == 0.0  # 缓存命中零成本
        assert second.text == first.text  # 逐字节复现

    def test_真实重跑自身逐位一致(self, screenplay_unbiased_fixture):
        """SC-004：同结构重算得分逐位一致（评估器确定性 + 定点归一）。"""
        config = screenplay_unbiased_fixture["config"]
        evaluators = screenplay_unbiased_fixture["evaluators"]
        markers = screenplay_unbiased_fixture["markers"][2]
        first = _real_rerun_score(markers, config, evaluators)
        second = _real_rerun_score(markers, config, evaluators)
        assert first == second


class Test注入偏差百分之百拒绝:
    """篡改回放序列（逆序/乱序/压低最高分/口径反转/胜率抹平）必须 100% 被拒（SC-008）。"""

    def _variants(self, real):
        rng = random.Random(20260920)
        shuffled = real[:]
        rng.shuffle(shuffled)
        drop_top = real[:]
        for i in range(len(real) - 3, len(real)):  # 最高分被压低（reward hacking 形态）
            drop_top[i] = 0.01
        return [
            ("reverse_逆序", real, list(reversed(real))),
            ("shuffle_乱序", real, shuffled),
            ("drop_top_篡改得分", real, drop_top),
            # 口径反转（代理扣分口径反向：score = 1 − 原分）→ 排序整体颠倒
            ("tampered_deduction_口径反转", real, [round(1.0 - s, 6) for s in real]),
            # judge 胜率抹平（全 0.5）→ 排序信息归零
            ("tampered_judge_胜率抹平", real, [0.5] * len(real)),
            # 门禁违规率口径放宽（低分被抬到满分）→ 取舍被伪造
            ("tampered_ratio_放宽抬分", real, [1.0 if s < 0.75 else s for s in real]),
        ]

    def test_全部偏差形态被拒绝(self, screenplay_unbiased_fixture):
        real = screenplay_unbiased_fixture["real"]
        variants = self._variants(real)
        assert len(variants) >= 3
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=_TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95


class Test未达标不得产出对比报告:
    """FR-013 发布阻塞：无偏性未达标 → 对比报告拒绝产出（0 报告落盘）。"""

    def test_未达标对比报告拒绝产出(self, screenplay_unbiased_fixture, tmp_path):
        real = screenplay_unbiased_fixture["real"]
        rejected = verify_unbiasedness(real, list(reversed(real)), threshold=_TAU_THRESHOLD)
        assert rejected.verdict == "reject"
        pool = SimulatorPool(screenplay_unbiased_fixture["store"])
        for tree in screenplay_unbiased_fixture["trees"]:
            pool.add_tree(tree)
        comparison_dir = tmp_path / "comparisons"
        with pytest.raises(CompareError, match="无偏性"):
            compare_versions(
                "111111111111",
                "222222222222",
                pool,
                screenplay_unbiased_fixture["config"],
                store=screenplay_unbiased_fixture["store"],
                inputs=_INPUTS,
                unbiasedness=rejected,
                history_root=tmp_path / "history",
                comparison_dir=comparison_dir,
            )
        assert list(comparison_dir.glob("*.json")) == []  # 未产出任何对比报告

    def test_策略版本指认可用于对比(self):
        """夹具策略版本即人工策略版本口径（版本 = 源码哈希前 12 位）。"""
        assert policy_version("class Policy:\n    pass\n") == policy_version(
            "class Policy:\n    pass\n"
        )
        assert len(_POLICY_VERSION) == 12
