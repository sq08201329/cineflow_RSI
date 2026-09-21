"""版本分组键单测（功能 011 / T1003，先于实现编写）。

- 确定性：同 config_snapshot 多次调用必得同 hash（64 位小写十六进制 BLAKE3）；
- 同语义同 hash：键序不同的等价版本集 → 同 hash；
- 版本差异不同 hash：任一评估器版本变化 → 不同 hash（跨版本不混池的依据，原则一）；
- config 微调同 hash：config_snapshot 其余键（权重/阈值/观测白名单/形态标注）变化
  不影响分组键——版本集一致即语义一致，微调如实进 config_note 不进分组；
- 缺版本集即拒绝：不得静默把未知版本集的树混进同一组。
"""

import pytest

from core.replay.errors import ValidationError
from core.replay.merged_pool import EVALUATOR_VERSIONS_KEY, evaluator_versions_hash

VERSIONS = {"rule.x": "1.0.0", "proxy.y": "1.0.0"}


def _snapshot(**extra) -> dict:
    """基准快照：评估器版本集 + 可选其余键（配置微调变体）。"""
    return {EVALUATOR_VERSIONS_KEY: dict(VERSIONS), **extra}


class Test版本分组键确定性:
    def test_同快照多次调用同_hash(self):
        snapshot = _snapshot(evaluator_weights={"rule.x": 0.0})
        first = evaluator_versions_hash(snapshot)
        assert first == evaluator_versions_hash(snapshot)
        assert len(first) == 64 and first == first.lower()
        assert all(ch in "0123456789abcdef" for ch in first)

    def test_键序无关_同语义同_hash(self):
        forward = {EVALUATOR_VERSIONS_KEY: {"rule.x": "1.0.0", "proxy.y": "2.0.0"}}
        backward = {EVALUATOR_VERSIONS_KEY: {"proxy.y": "2.0.0", "rule.x": "1.0.0"}}
        assert evaluator_versions_hash(forward) == evaluator_versions_hash(backward)

    def test_同版本集不同其他键仍同_hash(self):
        """同语义：键序以外的差异不改变分组（版本集是唯一语义分量）。"""
        snapshot = _snapshot(extra_key={"nested": [1, 2, 3]})
        assert evaluator_versions_hash(snapshot) == evaluator_versions_hash(_snapshot())


class Test版本差异分组:
    @pytest.mark.parametrize(
        "changed",
        [
            {"rule.x": "2.0.0", "proxy.y": "1.0.0"},  # 单评估器升级
            {"rule.x": "1.0.0", "proxy.y": "1.1.0"},  # 另一评估器升级
            {"rule.x": "1.0.0", "proxy.y": "1.0.0", "judge.z": "1.0.0"},  # 新增评估器
            {"rule.x": "1.0.0"},  # 移除评估器
        ],
    )
    def test_版本集变化即不同_hash(self, changed):
        assert evaluator_versions_hash(_snapshot()) != evaluator_versions_hash(
            {EVALUATOR_VERSIONS_KEY: changed}
        )

    def test_版本号不做规范化_大小写敏感(self):
        """版本字符串按原样入哈希（不 lower、不 strip）——版本即语义。"""
        lower = {EVALUATOR_VERSIONS_KEY: {"rule.x": "1.0.0", "proxy.y": "abc"}}
        upper = {EVALUATOR_VERSIONS_KEY: {"rule.x": "1.0.0", "proxy.y": "ABC"}}
        assert evaluator_versions_hash(lower) != evaluator_versions_hash(upper)


class Test_config_微调不影响分组:
    @pytest.mark.parametrize(
        "tweak",
        [
            {"evaluator_weights": {"rule.x": 0.5, "proxy.y": 0.5}},  # 权重微调
            {"observation_fields": ["gen_params", "stage"]},  # 观测白名单变化
            {"form": "short_drama"},  # 形态标注变化
            {"composite_policy": "适用权重归一，定点 6 位"},  # 合成口径附注
        ],
    )
    def test_微调同_hash(self, tweak):
        """config 微调不影响分组（版本集一致即语义一致）——微调由 config_note 如实标注。"""
        assert evaluator_versions_hash(_snapshot()) == evaluator_versions_hash(_snapshot(**tweak))


class Test缺版本集拒绝:
    def test_缺版本集键(self):
        with pytest.raises(ValidationError, match=EVALUATOR_VERSIONS_KEY):
            evaluator_versions_hash({"evaluator_weights": {"rule.x": 0.0}})

    def test_空版本集(self):
        with pytest.raises(ValidationError, match=EVALUATOR_VERSIONS_KEY):
            evaluator_versions_hash({EVALUATOR_VERSIONS_KEY: {}})

    def test_版本集非映射(self):
        with pytest.raises(ValidationError, match=EVALUATOR_VERSIONS_KEY):
            evaluator_versions_hash({EVALUATOR_VERSIONS_KEY: ["1.0.0"]})

    def test_版本值非字符串(self):
        with pytest.raises(ValidationError, match="版本"):
            evaluator_versions_hash({EVALUATOR_VERSIONS_KEY: {"rule.x": 1.0}})

    def test_config_snapshot_非映射(self):
        with pytest.raises(ValidationError, match="config_snapshot"):
            evaluator_versions_hash(None)
