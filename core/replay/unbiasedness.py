"""回放无偏性验收（contracts/unbiasedness.md，宪章发布阻塞门禁）。

手写 Kendall τ-b（处理同分对，O(n²)，零新依赖——research 决策 4）。
verify_unbiasedness 是发布门禁：τ ≥ threshold 放行，否则拒绝并输出
JSON 对比报告；FAILED 轮次（得分为 None）在计算前剔除并计入 notes。
"""

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from core.replay.errors import ValidationError

DEFAULT_TAU_THRESHOLD = 0.95


def kendall_tau(a: Sequence[float], b: Sequence[float]) -> float:
    """Kendall τ-b：τ = (C − D) / sqrt((n0 − n1)(n0 − n2))。

    n0 = n(n−1)/2；n1/n2 为仅在 a/b 中的同分对数。
    退化规则（分母为零，即一方全同分）：逐元素一致 → 1.0，否则 → 0.0。
    """
    if len(a) != len(b):
        raise ValidationError(f"两序列必须等长，实际为 {len(a)} 与 {len(b)}")
    n = len(a)
    if n < 2:
        raise ValidationError(f"样本不足：序列长度 {n} < 2")

    concordant = discordant = ties_a = ties_b = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff_a = (a[i] > a[j]) - (a[i] < a[j])
            diff_b = (b[i] > b[j]) - (b[i] < b[j])
            # τ-b 口径：同分对计入各自序列的 tie 计数（含双方同分），
            # 只有双方都非同分才参与 concordant/discordant 判定
            if diff_a == 0:
                ties_a += 1
            if diff_b == 0:
                ties_b += 1
            if diff_a == 0 or diff_b == 0:
                continue
            if diff_a == diff_b:
                concordant += 1
            else:
                discordant += 1

    n0 = n * (n - 1) // 2
    denominator = math.sqrt((n0 - ties_a) * (n0 - ties_b))
    if denominator == 0:
        return 1.0 if list(a) == list(b) else 0.0
    return (concordant - discordant) / denominator


@dataclass(frozen=True)
class UnbiasednessReport:
    """无偏性报告（data-model §4 schema；tau 为 None 表示样本不足未计算）。"""

    policy_version: str
    tau: float | None
    threshold: float
    verdict: Literal["pass", "reject"]
    real_scores: list[float]
    replay_scores: list[float]
    notes: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def verify_unbiasedness(
    real_scores: Sequence[float | None],
    replay_scores: Sequence[float | None],
    *,
    threshold: float = DEFAULT_TAU_THRESHOLD,
    policy_version: str = "",
) -> UnbiasednessReport:
    """无偏性验收：剔除 FAILED 轮次（None）后计算 τ，按门槛判定。"""
    if len(real_scores) != len(replay_scores):
        raise ValidationError(f"两序列必须等长，实际为 {len(real_scores)} 与 {len(replay_scores)}")

    # FAILED 轮次剔除：任一侧得分为 None 的轮次不进 τ 计算
    kept = [
        (r, p)
        for r, p in zip(real_scores, replay_scores, strict=True)
        if r is not None and p is not None
    ]
    dropped = len(real_scores) - len(kept)
    real = [r for r, _ in kept]
    replay = [p for _, p in kept]

    notes_parts = []
    if dropped:
        notes_parts.append(f"剔除 FAILED 轮次 {dropped} 个（得分为 None 不参与 τ 计算）")

    if len(kept) < 2:
        notes_parts.append(f"样本不足：有效轮次 {len(kept)} < 2，无法计算 τ")
        return UnbiasednessReport(
            policy_version=policy_version,
            tau=None,
            threshold=threshold,
            verdict="reject",
            real_scores=real,
            replay_scores=replay,
            notes="；".join(notes_parts),
        )

    tau = kendall_tau(real, replay)
    verdict: Literal["pass", "reject"] = "pass" if tau >= threshold else "reject"
    notes_parts.append(f"τ={tau:.4f}，门槛={threshold}，判定={verdict}")
    return UnbiasednessReport(
        policy_version=policy_version,
        tau=tau,
        threshold=threshold,
        verdict=verdict,
        real_scores=real,
        replay_scores=replay,
        notes="；".join(notes_parts),
    )
