"""防过拟合筛选（US2 / T415，宪章过拟合纪律）。

分树：模拟器池按时间划分 train/validation——**最近一棵树永远只做
validation**（uuid7 tree_id 时间有序）；首轮无 validation → 跳过判定注明。
判定：候选 train 第一但 validation 跌出前 top_ratio（默认 20%）→ 过拟合丢弃。
"""

from dataclasses import dataclass

from core.tree.models import DiscoveryTree


def split_train_validation(
    trees: list[DiscoveryTree],
) -> tuple[list[DiscoveryTree], list[DiscoveryTree]]:
    """按时间分树：最近一棵永远只做 validation（防泄漏的红线）。"""
    ordered = sorted(trees, key=lambda t: t.tree_id)  # uuid7 时间有序
    if len(ordered) < 2:
        return ordered, []  # 首轮：无 validation
    return ordered[:-1], [ordered[-1]]


@dataclass(frozen=True)
class OverfitVerdict:
    """过拟合判定结果。"""

    overfit: bool
    skipped: bool  # 首轮无 validation → True
    note: str


def evaluate_overfit(
    candidate_version: str,
    train_rewards: dict[str, float],
    validation_rewards: dict[str, float],
    *,
    top_ratio: float = 0.2,
) -> OverfitVerdict:
    """判定纯函数：train 第一 且 validation 排名跌出前 top_ratio → 过拟合。"""
    if not validation_rewards:
        return OverfitVerdict(
            overfit=False,
            skipped=True,
            note="首轮无 validation 树，跳过过拟合判定（待第二棵树入池后启用）",
        )
    train_best = max(train_rewards, key=lambda v: train_rewards[v])
    if candidate_version != train_best:
        return OverfitVerdict(
            overfit=False, skipped=False, note="候选非 train 第一，无需过拟合判定"
        )
    # validation 排名（降序）：并列取最劣名次（保守）
    ranked = sorted(validation_rewards.values(), reverse=True)
    candidate_score = validation_rewards.get(candidate_version)
    if candidate_score is None:
        rank = len(ranked)  # 未上榜 = 最末
    else:
        rank = ranked.index(candidate_score) + 1 + (ranked.count(candidate_score) - 1)
    cutoff = max(1, int(len(ranked) * top_ratio))
    if rank > cutoff:
        return OverfitVerdict(
            overfit=True,
            skipped=False,
            note=(
                f"过拟合：train 第一但 validation 排名 {rank}/{len(ranked)} "
                f"跌出前 {top_ratio:.0%}，丢弃"
            ),
        )
    return OverfitVerdict(overfit=False, skipped=False, note="validation 泛化良好")
