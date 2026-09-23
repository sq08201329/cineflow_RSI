"""开发 Agent 回放无偏性验收（功能 017 / T1740，先于实现编写；FR-013 / SC-008）。

选题树回放打分（`SimulatorPool` probe 揭示的**历史得分**，`slate_match_key` 结构键规范化
精确匹配）vs **真实重跑**（同一工件经真实四评估器重算 + 定点归一：本环节评估器确定性、
零 LLM——回放可打分的前提，原则一）得分序列 Kendall τ ≥ 0.95 放行；注入偏差 ≥3 形态
**100% 拒绝**；**未达标不得产出回放对比报告**（`compare_versions` 前置门禁）。
复用 `core/replay/unbiasedness.py`（002 同套件，009 同口径接入）。
"""

import copy
import random
from pathlib import Path

import pytest
import yaml

from agents.dev.artifact import TopicSlate, simulated_signal_sources
from agents.dev.config import DevConfig
from agents.dev.evaluators import build_dev_evaluators
from agents.dev.evaluators.composite import evaluate_dev
from agents.dev.loop import slate_match_key
from agents.dev.sandbox_compare import CompareError, compare_versions
from core.evaluators.base import ArtifactRef
from core.replay.pool import SimulatorPool
from core.replay.unbiasedness import verify_unbiasedness
from core.tree.models import CostRecord, NodeStatus, new_id
from policies.base import Budget

pytestmark = pytest.mark.unbiasedness

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
_TAU_THRESHOLD = _REAL_CONFIG["replay"]["unbiasedness_tau_threshold"]

_INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}
_POLICY_VERSION = "9f2c41ab77de"  # 人工策略版本（夹具口径：版本 = 源码 BLAKE3 前 12 位）
# 结构梯度：逐档取不同题材（模拟数据源按题材夹具给出不同票房/热度系数）→ 代理分量分散
_GENRES = (
    "医疗悬疑",
    "都市犯罪",
    "科幻悬疑",
    "家庭剧情",
    "古装权谋",
    "公路喜剧",
    "现实职场",
    "青春成长",
)


def _config() -> DevConfig:
    """无偏性夹具配置：题材夹具钉住逐题材的模拟源系数（取值谱系分散，τ 具判别力）。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    raw["dev"]["signals"]["fixtures"] = {
        "genres": {
            genre: {"box_office_factor": round(0.4 + 0.15 * index, 6), "buzz_factor": 0.2}
            for index, genre in enumerate(_GENRES)
        }
    }
    return DevConfig.from_dict(raw)


def _slate_of(genre: str) -> TopicSlate:
    """一档立项组合工件（3 条方向、1 个进入生产标记——落在 movie 形态区间内）。"""
    entries = [
        {
            "direction_id": f"dir-{genre}-{index}",
            "genre": genre,
            "constraints": ["单场景为主", f"档位 {index}"],
            "characters": [f"角色甲{index}", f"角色乙{index}"],
            "rationale": f"{genre}方向 {index} 的立项论证要点（无偏性夹具）。",
            "in_production": index == 0,
        }
        for index in range(3)
    ]
    return TopicSlate.from_dict(
        {
            "entries": entries,
            "production_marks": [entries[0]["direction_id"]],
            "signal_sources": list(simulated_signal_sources(_REAL_CONFIG["dev"]["signals"])),
        }
    )


def _real_rerun_score(slate: TopicSlate, config: DevConfig, assembly) -> float:
    """真实重跑：同一工件经真实四评估器重算 + 定点归一（确定性，零 LLM）。"""
    artifact_ref = ArtifactRef(artifact_hash=slate.slate_hash(), metadata={"agent_id": "dev"})
    context = {"artifact": slate, "inputs": _INPUTS, "config": config}
    _, score, _ = evaluate_dev(assembly, artifact_ref, context, config.evaluator_weights)
    return score


def _key_of(config: DevConfig) -> dict:
    return slate_match_key(policy_version=_POLICY_VERSION, inputs=_INPUTS, config=config)


def _build_pool(tree_store, make_tree, make_node, slates, real_scores, config):
    """逐档一棵冻结轮次树（**一轮一棵树、单一产出节点**——本环节真实形状）：

    子节点 `gen_params` = 该版本的结构键，得分 = 真实重跑值；回放按结构键 probe 命中它。
    """
    trees = []
    for index, (slate, score) in enumerate(zip(slates, real_scores, strict=True)):
        tree = make_tree(
            agent_id="dev",
            project_id="dev-unbiased",
            policy_version=_POLICY_VERSION,
            config_snapshot={
                "evaluator_weights": config.evaluator_weights,
                "observation_fields": ["gen_params", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="dev",
                policy_version=_POLICY_VERSION,
                eval_breakdown={},
                score=0.0,
                created_at=float(index * 100),
            )
        )
        tree_store.append_node(
            make_node(
                node_id=new_id(),
                tree_id=tree.tree_id,
                parent_id=tree.root_id,
                depth=1,
                agent_id="dev",
                policy_version=_POLICY_VERSION,
                observation_context={
                    "job_id": f"ub-{index}",
                    "gen_params": _key_of(config),
                },
                artifact_hash=slate.slate_hash(),
                eval_breakdown={
                    "rule.slate_structure@1.0.0": {"score": 1.0},
                    "rule.slate_combination@1.0.0": {"score": 1.0},
                    "proxy.genre_regression@1.0.0": {"score": score},
                    "proxy.buzz_heat@1.0.0": {"score": score},
                },
                score=score,
                cost=CostRecord(llm_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=float(index * 100 + 1),
            )
        )
        trees.append(tree)
    return trees


def _replay_scores(trees, tree_store, config):
    """回放打分：逐树单池按结构键 probe 收集历史得分（键序乱排仍命中）。"""
    scores = []
    for index, tree in enumerate(trees):
        simulator = SimulatorPool(tree_store)
        simulator.add_tree(tree)
        builder = simulator.build(worker_count=1, budget=Budget(max_probes=1), latency_quantum_ms=0)
        key = _key_of(config)
        probe_key = dict(reversed(list(key.items()))) if index % 2 else key
        result = builder.probe(tree.root_id, probe_key)
        assert result.status == "ok", "结构键未命中（回放精确匹配被破坏）"
        scores.append(result.nodes[0].score)
    return scores


@pytest.fixture()
def dev_unbiased_fixture(tree_store, make_tree, make_node):
    """无偏性夹具：结构梯度序列 + 真实重跑序列 + 池化回放序列。"""
    config = _config()
    assembly = build_dev_evaluators(config)
    slates = [_slate_of(genre) for genre in _GENRES]
    real = [_real_rerun_score(slate, config, assembly) for slate in slates]
    trees = _build_pool(tree_store, make_tree, make_node, slates, real, config)
    replay = _replay_scores(trees, tree_store, config)
    return {
        "config": config,
        "assembly": assembly,
        "slates": slates,
        "real": real,
        "replay": replay,
        "trees": trees,
        "store": tree_store,
    }


class Test开发无偏性放行:
    def test_回放对真实重跑_tau达标(self, dev_unbiased_fixture):
        real = dev_unbiased_fixture["real"]
        replay = dev_unbiased_fixture["replay"]
        assert len(real) == len(_GENRES) == 8
        report = verify_unbiasedness(real, replay, threshold=_TAU_THRESHOLD)
        assert report.verdict == "pass", report.notes
        assert report.tau >= 0.95, report.notes

    def test_门槛取_configs_口径(self, dev_unbiased_fixture):
        assert _TAU_THRESHOLD == 0.95
        report = verify_unbiasedness(dev_unbiased_fixture["real"], dev_unbiased_fixture["replay"])
        assert report.verdict == "pass"

    def test_回放得分即历史得分(self, dev_unbiased_fixture):
        """回放只读历史节点得分（不重算、不生成）——序列逐位相等。"""
        assert dev_unbiased_fixture["replay"] == dev_unbiased_fixture["real"]

    def test_得分区分度(self, dev_unbiased_fixture):
        """梯度有效性前提：得分谱系足够分散（τ 有判别力）。"""
        real = dev_unbiased_fixture["real"]
        assert len(set(real)) >= 4, f"得分区分度不足（梯度失效）：{real}"

    def test_真实重跑自身逐位一致(self, dev_unbiased_fixture):
        """SC-004：同工件重算得分逐位一致（四评估器确定性 + 定点归一）。"""
        config = dev_unbiased_fixture["config"]
        assembly = dev_unbiased_fixture["assembly"]
        slate = dev_unbiased_fixture["slates"][2]
        assert _real_rerun_score(slate, config, assembly) == _real_rerun_score(
            slate, config, assembly
        )

    def test_代理分量来源标注随诊断(self, dev_unbiased_fixture):
        """SC-009：真实重跑的代理分量诊断带模拟源标注（回放读数同源）。"""
        slate = dev_unbiased_fixture["slates"][0]
        context = {"artifact": slate, "inputs": _INPUTS, "config": dev_unbiased_fixture["config"]}
        breakdown, _, _ = evaluate_dev(
            dev_unbiased_fixture["assembly"],
            ArtifactRef(artifact_hash=slate.slate_hash(), metadata={"agent_id": "dev"}),
            context,
            dev_unbiased_fixture["config"].evaluator_weights,
        )
        proxies = [key for key in breakdown if key.startswith("proxy.")]
        assert len(proxies) == 2
        for key in proxies:
            diagnostics = breakdown[key]["diagnostics"]
            assert diagnostics["simulated"] is True
            assert "非真实商业数据" in diagnostics["note"]


class Test注入偏差百分之百拒绝:
    """篡改回放序列（逆序/乱序/压低最高分/口径反转/抬分）必须 100% 被拒（SC-008）。"""

    def _variants(self, real):
        rng = random.Random(20260923)
        shuffled = real[:]
        rng.shuffle(shuffled)
        drop_top = real[:]
        for index in range(len(real) - 3, len(real)):  # 最高分被压低（reward hacking 形态）
            drop_top[index] = 0.01
        return [
            ("reverse_逆序", real, list(reversed(real))),
            ("shuffle_乱序", real, shuffled),
            ("drop_top_篡改得分", real, drop_top),
            # 口径反转（代理扣分口径反向：score = 1 − 原分）→ 排序整体颠倒
            ("tampered_deduction_口径反转", real, [round(1.0 - score, 6) for score in real]),
            # 无偏性凭证伪造（全 0.5）→ 排序信息归零
            ("tampered_常量序列", real, [0.5] * len(real)),
            # 门禁违规率口径放宽（低分被抬到满分）→ 取舍被伪造
            ("tampered_放宽抬分", real, [1.0 if score < 0.75 else score for score in real]),
        ]

    def test_全部偏差形态被拒绝(self, dev_unbiased_fixture):
        real = dev_unbiased_fixture["real"]
        variants = self._variants(real)
        assert len(variants) >= 3
        for name, real_seq, replay_seq in variants:
            report = verify_unbiasedness(real_seq, replay_seq, threshold=_TAU_THRESHOLD)
            assert report.verdict == "reject", f"偏差形态 {name} 未被拒绝（τ={report.tau}）"
            assert report.tau < 0.95


class Test未达标不得产出对比报告:
    """FR-013 发布阻塞：无偏性未达标 → 对比报告拒绝产出（0 报告落盘）。"""

    def test_未达标对比报告拒绝产出(self, dev_unbiased_fixture, tmp_path):
        real = dev_unbiased_fixture["real"]
        rejected = verify_unbiasedness(real, list(reversed(real)), threshold=_TAU_THRESHOLD)
        assert rejected.verdict == "reject"
        pool = SimulatorPool(dev_unbiased_fixture["store"])
        for tree in dev_unbiased_fixture["trees"]:
            pool.add_tree(tree)
        comparison_dir = tmp_path / "comparisons"
        with pytest.raises(CompareError, match="无偏性"):
            compare_versions(
                "111111111111",
                "222222222222",
                pool,
                dev_unbiased_fixture["config"],
                store=dev_unbiased_fixture["store"],
                inputs=_INPUTS,
                unbiasedness=rejected,
                history_root=tmp_path / "history",
                comparison_dir=comparison_dir,
            )
        assert list(comparison_dir.glob("*.json")) == []  # 未产出任何对比报告

    def test_达标凭证方可用于对比(self, dev_unbiased_fixture, tmp_path):
        """放行侧：τ 达标的凭证可以流转（JSON 落盘 → 读回 → verdict=pass）。"""
        import json

        from agents.dev.sandbox_compare import UnbiasednessAttestation

        real = dev_unbiased_fixture["real"]
        report = verify_unbiasedness(real, dev_unbiased_fixture["replay"], threshold=_TAU_THRESHOLD)
        path = tmp_path / "unbiasedness.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        attestation = UnbiasednessAttestation.load(path)
        assert attestation.verdict == "pass"
        assert attestation.tau == pytest.approx(report.tau)

    def test_策略版本口径为源码哈希前缀(self):
        """夹具策略版本即人工策略版本口径（版本 = 源码 BLAKE3 前 12 位）。"""
        from policies.versioning import policy_version

        source = "class Policy:\n    def plan(self, inputs, config):\n        return {}\n"
        version = policy_version(source)
        assert len(version) == len(_POLICY_VERSION) == 12
        assert version == policy_version(source)  # 同源码同版本
