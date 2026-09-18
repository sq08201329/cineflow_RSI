"""虚拟时钟单测（US1 / T110）。

契约 §2：tick_execution += ⌈batch_size / worker_count⌉；构造即校验
worker_count ≥ 1；tick_execution 要求 batch_size ≥ 1。
"""

import pytest

from core.replay.clock import VirtualClock
from core.replay.errors import ValidationError


class Test构造校验:
    def test_默认值(self):
        clock = VirtualClock(worker_count=4)
        assert clock.decision_rounds == 0
        assert clock.effective_sequential_rounds == 0.0
        assert clock.worker_count == 4

    @pytest.mark.parametrize("bad_w", [0, -1, 1.5, True, "4"])
    def test_worker_count_非法拒构造(self, bad_w):
        with pytest.raises(ValidationError):
            VirtualClock(worker_count=bad_w)


class Test决策轮:
    def test_tick_decision_逐次加一(self):
        clock = VirtualClock(worker_count=2)
        for _ in range(3):
            clock.tick_decision()
        assert clock.decision_rounds == 3
        assert clock.effective_sequential_rounds == 0.0


class Test有效串行轮:
    @pytest.mark.parametrize(
        "batch_size, worker_count, expected",
        [
            (1, 4, 1),  # ⌈1/4⌉ = 1
            (4, 4, 1),  # ⌈4/4⌉ = 1
            (5, 4, 2),  # ⌈5/4⌉ = 2
            (8, 4, 2),  # ⌈8/4⌉ = 2
            (1, 1, 1),
            (10, 3, 4),  # ⌈10/3⌉ = 4
        ],
    )
    def test_ceil_k_over_w(self, batch_size, worker_count, expected):
        clock = VirtualClock(worker_count=worker_count)
        clock.tick_execution(batch_size)
        assert clock.effective_sequential_rounds == expected

    def test_累加(self):
        clock = VirtualClock(worker_count=2)
        clock.tick_execution(3)  # ⌈3/2⌉ = 2
        clock.tick_execution(2)  # ⌈2/2⌉ = 1
        assert clock.effective_sequential_rounds == 3.0

    @pytest.mark.parametrize("bad_k", [0, -1, 0.5, True])
    def test_batch_size_非法报错(self, bad_k):
        clock = VirtualClock(worker_count=2)
        with pytest.raises(ValidationError):
            clock.tick_execution(bad_k)
