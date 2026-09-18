"""无偏性轨迹夹具生成器（US3 / T133）。

- consistent_pair：一致轨迹对（replay 保持 real 的排序，τ 应 ≥ 0.95）；
- biased_pairs：N 种注入偏差的轨迹对（排序被破坏，必须 100% reject）。
全部确定性（固定种子），供无偏性门禁与回归用例共用。
"""

import random

_BASE_SEED = 20260918


def _base_scores(n: int) -> list[float]:
    """单调上升的基准得分序列（模拟策略逐步变优的真实轨迹）。"""
    rng = random.Random(_BASE_SEED)
    return sorted(round(rng.random(), 3) for _ in range(n))


def consistent_pair(n: int = 20) -> tuple[list[float], list[float]]:
    """一致轨迹对：replay = real + 保序微扰（排序完全一致 → τ = 1）。"""
    real = _base_scores(n)
    replay = [min(1.0, score + 0.001) for score in real]  # 保序微扰不改变名次
    return real, replay


def biased_pairs(n: int = 20) -> list[tuple[str, list[float], list[float]]]:
    """注入偏差的轨迹对（名称, real, replay）：每种都把排序打破到 τ < 0.95。"""
    real = _base_scores(n)
    rng = random.Random(_BASE_SEED + 1)

    shuffled = real[:]
    rng.shuffle(shuffled)

    half = n // 2
    swap_halves = real[half:] + real[:half]

    clamped = [round(score * 2) / 2 for score in real]  # 大量同分破坏区分度

    drop_top = real[:]
    for i in range(n - 3, n):  # 最高分被恶意压低（reward hacking 形态）
        drop_top[i] = 0.01

    return [
        ("reverse", real, list(reversed(real))),
        ("shuffle", real, shuffled),
        ("swap_halves", real, swap_halves),
        ("clamp_ties", real, clamped),
        ("drop_top", real, drop_top),
    ]
