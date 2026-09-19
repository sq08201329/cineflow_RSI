"""锚点配对单测（功能 010 US2 / T516，先于实现编写；契约 C4 场景 1~2）。

- 逐锚点取节点 eval_breakdown 逐分量配对（锚点得分 × 分量得分）；
- 自循环剔除由配置 self_pairing_exclusions 驱动：platform_truth 锚点剔除
  human.platform_metrics 分量并在 PairingRecord 注明（FR-004）；
- 人评盲评锚点无排除项 → 全量配对；eval_breakdown 缺分量跳过并注明。
"""

import pytest

from core.calibration.models import AnchorScore
from core.calibration.pairing import pair_anchors

_EXCLUSIONS = {"platform_truth": ("human.platform_metrics",)}

_PROMO_BREAKDOWN = {
    "human.platform_metrics@1.0.0": {"score": 0.5},
    "proxy.ctr_history@1.0.0": {"score": 0.4},
}
_VISUAL_BREAKDOWN = {
    "proxy.aesthetic@1.0.0": {"score": 0.7},
    "judge.cinematic@1.0.0": {"score": 0.6},
}


def _anchor(node_id: str, source: str, score: float = 0.8) -> AnchorScore:
    return AnchorScore(
        anchor_id=f"a-{node_id}",
        node_id=node_id,
        artifact_hash="ab" * 32,
        agent_id="promo" if source == "platform_truth" else "visual",
        source=source,
        score=score,
        reviewer="r1",
        round_id="calib-r1",
        created_at="2026-09-19T00:00:00+00:00",
    )


@pytest.fixture()
def promo_node(tree_store, build_calibration_tree):
    """promo 树：节点含 human.platform_metrics 与 proxy.ctr_history 两分量。"""
    _, node_ids = build_calibration_tree([(0.45, dict(_PROMO_BREAKDOWN))], agent_id="promo")
    return node_ids[0]


@pytest.fixture()
def visual_node(tree_store, build_calibration_tree):
    _, node_ids = build_calibration_tree([(0.65, dict(_VISUAL_BREAKDOWN))], agent_id="visual")
    return node_ids[0]


class Test自循环剔除:
    def test_platform_truth_剔除自身分量(self, tree_store, promo_node):
        records = pair_anchors(
            [_anchor(promo_node, "platform_truth")], tree_store, _EXCLUSIONS
        )
        by_key = {r.evaluator_key: r for r in records}
        excluded = by_key["human.platform_metrics@1.0.0"]
        assert excluded.excluded is True
        assert excluded.auto_score is None
        assert "human.platform_metrics" in excluded.excluded_components
        assert "剔除" in excluded.note
        # proxy 分量正常配对
        paired = by_key["proxy.ctr_history@1.0.0"]
        assert paired.excluded is False
        assert paired.auto_score == pytest.approx(0.4)
        assert paired.anchor_score == pytest.approx(0.8)

    def test_排除映射来自配置驱动而非命名约定(self, tree_store, promo_node):
        """空排除配置时 platform_truth 锚点也全量配对（规则完全由配置表达）。"""
        records = pair_anchors([_anchor(promo_node, "platform_truth")], tree_store, {})
        assert all(r.excluded is False for r in records)
        assert len(records) == 2


class Test人评全量配对:
    def test_visual_人评锚点全量配对(self, tree_store, visual_node):
        records = pair_anchors([_anchor(visual_node, "human_blind")], tree_store, _EXCLUSIONS)
        assert len(records) == 2
        for record in records:
            assert record.excluded is False
            assert record.auto_score is not None
        by_key = {r.evaluator_key: r for r in records}
        assert by_key["proxy.aesthetic@1.0.0"].auto_score == pytest.approx(0.7)
        assert by_key["judge.cinematic@1.0.0"].auto_score == pytest.approx(0.6)


class Test缺分量跳过:
    def test_期望评估器缺失时跳过并注明(self, tree_store, visual_node):
        records = pair_anchors(
            [_anchor(visual_node, "human_blind")],
            tree_store,
            _EXCLUSIONS,
            evaluator_keys=(
                "proxy.aesthetic@1.0.0",
                "judge.cinematic@1.0.0",
                "proxy.identity_consistency@1.0.0",  # 节点 breakdown 缺该分量
            ),
        )
        by_key = {r.evaluator_key: r for r in records}
        missing = by_key["proxy.identity_consistency@1.0.0"]
        assert missing.auto_score is None
        assert missing.excluded is False
        assert "缺分量" in missing.note
        # 已有分量不受影响
        assert by_key["proxy.aesthetic@1.0.0"].auto_score == pytest.approx(0.7)


class Test多锚点:
    def test_逐锚点配对(self, tree_store, build_calibration_tree):
        _, node_ids = build_calibration_tree(
            [(0.5, dict(_VISUAL_BREAKDOWN)), (0.6, dict(_VISUAL_BREAKDOWN))], agent_id="visual"
        )
        anchors = [_anchor(nid, "human_blind", score=0.8 + i * 0.05) for i, nid in enumerate(node_ids)]
        records = pair_anchors(anchors, tree_store, _EXCLUSIONS)
        assert len(records) == 4  # 2 锚点 × 2 分量
        assert {r.anchor_id for r in records} == {a.anchor_id for a in anchors}
