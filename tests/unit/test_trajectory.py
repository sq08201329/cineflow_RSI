"""回放轨迹模型单测（补充 T105 校验分支覆盖：结局状态机与字段校验）。"""

import pytest

from core.replay.errors import ValidationError
from core.replay.trajectory import TrajectoryStatus


class Test轨迹校验:
    def test_结局状态机四态(self, make_trajectory):
        for status in TrajectoryStatus:
            assert make_trajectory(status=status).status is status

    def test_status_接受字符串并归一化(self, make_trajectory):
        assert make_trajectory(status="timeout").status is TrajectoryStatus.TIMEOUT

    def test_status_非法值拒构造(self, make_trajectory):
        with pytest.raises(ValueError):
            make_trajectory(status="exploded")

    @pytest.mark.parametrize("bad", [-1, 0.5, True])
    def test_probe_count_非法拒构造(self, make_trajectory, bad):
        with pytest.raises(ValidationError):
            make_trajectory(probe_count=bad)

    def test_串行轮负数拒构造(self, make_trajectory):
        with pytest.raises(ValidationError):
            make_trajectory(effective_sequential_rounds=-0.5)

    @pytest.mark.parametrize("bad", [-0.1, 1.1, True])
    def test_曲线元素越界拒构造(self, make_trajectory, bad):
        with pytest.raises(ValidationError):
            make_trajectory(best_score_curve=[0.5, bad])

    def test_policy_version_非字符串拒构造(self, make_trajectory):
        with pytest.raises(ValidationError):
            make_trajectory(policy_version=123)

    def test_to_dict_为_JSON_值语义(self, make_trajectory):
        import json

        data = make_trajectory().to_dict()
        json.dumps(data)  # 必须可直接 JSON 序列化
        assert data["status"] == "completed"
        assert data["policy_version"] == "a1b2c3d4e5f6"
