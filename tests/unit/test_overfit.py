"""防过拟合筛选单测（US2 / T413，contracts/approval.md §2 双池筛选）。

最近树永远只 validation、train 第一但 validation 跌出前 20% 丢弃、
首轮无 validation 跳过注明、泛化不误判。
"""

from dreaming.overfit import evaluate_overfit, split_train_validation


class Test分树:
    def test_最近树永远只_validation(self, multi_tree_pool):
        """US2 验收场景 1：池内 ≥2 棵树 → 最近一棵只在 validation 集。"""
        trees, _ = multi_tree_pool(3)
        train, validation = split_train_validation(trees)
        latest = max(t.tree_id for t in trees)  # uuid7 时间有序
        assert [t.tree_id for t in validation] == [latest]
        assert latest not in [t.tree_id for t in train]
        assert len(train) == 2

    def test_单棵树_首轮无validation(self, multi_tree_pool):
        trees, _ = multi_tree_pool(1)
        train, validation = split_train_validation(trees)
        assert len(train) == 1 and validation == []

    def test_空池(self):
        assert split_train_validation([]) == ([], [])


class Test判定:
    def test_train第一_validation跌出前20_判过拟合(self):
        """US2 验收场景 2：train 第一 + validation 跌出前 20% → 丢弃并注明。"""
        train_rewards = {"memorizer": 0.9, "a": 0.6, "b": 0.5}
        validation_rewards = {"a": 0.7, "b": 0.6, "c": 0.55, "d": 0.5, "memorizer": 0.1}
        verdict = evaluate_overfit("memorizer", train_rewards, validation_rewards, top_ratio=0.2)
        assert verdict.overfit is True
        assert not verdict.skipped
        assert "validation" in verdict.note

    def test_泛化良好不误判(self):
        train = {"winner": 0.9, "a": 0.6}
        validation = {"winner": 0.8, "a": 0.6}
        verdict = evaluate_overfit("winner", train, validation, top_ratio=0.2)
        assert verdict.overfit is False

    def test_train非第一不判过拟合(self):
        verdict = evaluate_overfit(
            "mid", {"x": 0.9, "mid": 0.5}, {"mid": 0.1, "x": 0.8}, top_ratio=0.2
        )
        assert verdict.overfit is False

    def test_首轮无validation_跳过注明(self):
        verdict = evaluate_overfit("only", {"only": 0.9}, {}, top_ratio=0.2)
        assert verdict.skipped is True
        assert verdict.overfit is False
        assert "首轮" in verdict.note or "validation" in verdict.note

    def test_validation并列边界(self):
        # 5 个候选，前 20% = 第 1 名；candidate 恰好第 2 → 跌出前 20% → 过拟合
        validation = {f"c{i}": 0.9 - i * 0.1 for i in range(5)}
        verdict = evaluate_overfit("c1", {"c1": 0.99, "c0": 0.5}, validation, top_ratio=0.2)
        assert verdict.overfit is True
