"""锚点 × eval_breakdown 配对（功能 010 US2，契约 C4）。

- 逐锚点取节点 eval_breakdown 逐分量配对（锚点得分 × 分量得分）；
- 自循环剔除完全由配置 self_pairing_exclusions 驱动（research 决策 4：
  拒绝命名约定推断）；命中时该分量不入配对，PairingRecord 注明剔除；
- evaluator_keys 给定期望评估器集合时，节点 breakdown 缺分量 → 跳过并注明。
"""

from collections.abc import Iterable

from core.calibration.models import AnchorScore, PairingRecord
from core.tree.store import TreeStore


def pair_anchors(
    anchors: list[AnchorScore],
    store: TreeStore,
    exclusions: dict,
    *,
    evaluator_keys: Iterable[str] | None = None,
) -> list[PairingRecord]:
    """锚点 × 评估器分量配对。

    exclusions 形如 {"platform_truth": ("human.platform_metrics",)}（配置
    calibration.self_pairing_exclusions 的读出形态）；evaluator_keys 为 None 时
    以各节点 breakdown 实际分量为准。
    """
    records: list[PairingRecord] = []
    for anchor in anchors:
        node = store.get_node(anchor.node_id)
        breakdown = node.eval_breakdown
        excluded_ids = tuple(exclusions.get(anchor.source.value, ()))
        keys = tuple(evaluator_keys) if evaluator_keys is not None else tuple(breakdown)
        for evaluator_key in keys:
            evaluator_id = evaluator_key.split("@")[0]
            if evaluator_id in excluded_ids:
                # 防自循环：锚点来源与分量同源，剔除并注明（FR-004）
                records.append(
                    PairingRecord(
                        anchor_id=anchor.anchor_id,
                        evaluator_key=evaluator_key,
                        anchor_score=anchor.score,
                        auto_score=None,
                        excluded=True,
                        excluded_components=(evaluator_id,),
                        note=f"防自循环剔除：{anchor.source.value} 锚点不与 {evaluator_id} 配对",
                    )
                )
                continue
            component = breakdown.get(evaluator_key)
            if component is None:
                records.append(
                    PairingRecord(
                        anchor_id=anchor.anchor_id,
                        evaluator_key=evaluator_key,
                        anchor_score=anchor.score,
                        auto_score=None,
                        note=f"eval_breakdown 缺分量 {evaluator_key}，跳过配对",
                    )
                )
                continue
            records.append(
                PairingRecord(
                    anchor_id=anchor.anchor_id,
                    evaluator_key=evaluator_key,
                    anchor_score=anchor.score,
                    auto_score=float(component["score"]),
                )
            )
    return records
