"""进化对比报告单测（US3 / T222）。

EvolutionReport schema（data-model §4）：双曲线、成本、pareto_auc/并行惩罚
分量、verdict、谱系引用（policy_version → pool_tree_ids）。
"""

import json
from pathlib import Path

import blake3
import pytest

from agents.promo.loop import freeze_round_tree, run_round
from agents.promo.report import decide_verdict, generate_evolution_report
from ops.ingest_metrics import ingest_round

HISTORY_DIR = Path(__file__).resolve().parents[2] / "policies" / "history" / "promo"


class StubPromoPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _brief(budget, temperature):
    return {
        "prompt": "写一句宣发文案",
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": budget,
    }


@pytest.fixture()
def frozen_trees(
    tree_store, campaigns_engine, tmp_path, simulated_platform, mock_gateway, promo_config
):
    """两轮探索产出并回流冻结（t=0.3 与 t=0.7 两档物料）。"""
    from core.tree.artifacts import LocalArtifactStore

    for round_id in ("r-e1", "r-e2"):
        run_round(
            round_id,
            StubPromoPolicy([_brief(2.0, 0.3), _brief(2.0, 0.7)]),
            tree_store,
            LocalArtifactStore(tmp_path / f"artifacts-{round_id}"),
            simulated_platform,
            mock_gateway,
            promo_config,
            engine=campaigns_engine,
        )
        ingest_round(round_id, tree_store, simulated_platform, campaigns_engine, promo_config)
    return [freeze_round_tree(r, tree_store, campaigns_engine) for r in ("r-e1", "r-e2")]


def _policy_sources():
    return sorted(HISTORY_DIR.glob("*.py"))


class Test手写策略版本:
    def test_基线与变体文件名即内容哈希(self):
        """T223：policies/history/promo/ 下两个手写策略，文件名 = BLAKE3 前 12 位。"""
        paths = _policy_sources()
        assert len(paths) == 2
        for path in paths:
            expected = blake3.blake3(path.read_bytes()).hexdigest()[:12]
            assert path.stem == expected, f"{path.name} 文件名与内容哈希不符"

    def test_两策略探索参数不同(self):
        sources = [p.read_text() for p in _policy_sources()]
        assert any("0.7" in s for s in sources) and any("0.7" not in s for s in sources)


class TestEvolutionReport:
    def test_报告schema与双曲线(self, tree_store, frozen_trees):
        report = generate_evolution_report(
            tree_store, frozen_trees, _policy_sources(), max_probes=6
        )
        data = json.loads(report.to_json())
        assert data["agent_id"] == "promo"
        assert set(data["pool_tree_ids"]) == {t.tree_id for t in frozen_trees}
        assert data["verdict"] in ("variant_better", "baseline_better", "inconclusive")
        assert len(data["variants"]) == 2
        for variant in data["variants"]:
            assert len(variant["policy_version"]) == 12  # BLAKE3 前 12 位
            assert variant["best_score_curve"]  # 双曲线非空
            assert variant["probe_count"] >= 0
            assert variant["effective_sequential_rounds"] >= 0
            assert "generation_api_cost_usd" in variant["total_cost"]
            assert set(variant["reward_parts"]) == {"pareto_auc", "parallel_penalty"}

    def test_谱系引用可追溯(self, tree_store, frozen_trees):
        """FR-011：报告数据点可定位来源树与策略版本。"""
        paths = _policy_sources()
        report = generate_evolution_report(tree_store, frozen_trees, paths, max_probes=6)
        versions = {v.policy_version for v in report.variants}
        assert versions == {blake3.blake3(p.read_bytes()).hexdigest()[:12] for p in paths}
        assert report.pool_tree_ids == [t.tree_id for t in frozen_trees]

    def test_变体覆盖更多走法_曲线不劣于基线(self, tree_store, frozen_trees):
        """变体（双档探测）覆盖基线（单档）未及的走法：最优得分 ≥ 基线。"""
        report = generate_evolution_report(
            tree_store, frozen_trees, _policy_sources(), max_probes=6
        )
        baseline, variant = report.variants[0], report.variants[1]
        assert max(variant.best_score_curve) >= max(baseline.best_score_curve)


class TestVerdict:
    @pytest.mark.parametrize(
        "baseline, variant, expected",
        [
            (0.5, 0.6, "variant_better"),
            (0.6, 0.5, "baseline_better"),
            (0.5, 0.5, "inconclusive"),
            (0.5, 0.505, "inconclusive"),  # 差异小于显著阈值
        ],
    )
    def test_判定(self, baseline, variant, expected):
        assert decide_verdict(baseline, variant) == expected
