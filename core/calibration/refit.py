"""权重再拟合提案与生效门禁（功能 010 US3，契约 C7/C8；research 决策 5/6/7）。

拟合（本模块第一段）：约束岭回归（numpy 实现，零新增依赖）——
min Σ(anchor_i − Σ_e w_e·s_ei)² + λ_ridge·‖w − w_current‖²，s.t. w_e ≥ 0，Σw = 1，
投影梯度 + 单纯形投影（Duchi 等 2008）；fixed_keys（gate 硬规则）权重冻结为零，
自由键收缩到 Σ = 1 − Σfixed。提案生成与生效门禁见本模块后半部分。
"""

import numpy as np

_FIT_MAX_ITER = 5000
_FIT_TOL = 1e-12


def _project_simplex(v: np.ndarray) -> np.ndarray:
    """投影到概率单纯形 {w ≥ 0, Σw = 1}（Duchi 等 2008，O(m log m)）。"""
    u = np.sort(v)[::-1]
    cumsum = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > cumsum - 1)[0][-1]
    theta = (cumsum[rho] - 1.0) / (rho + 1)
    return np.maximum(v - theta, 0.0)


def fit_weights(
    samples: list[tuple[float, dict[str, float]]],
    current_weights: dict[str, float],
    ridge_lambda: float,
    *,
    fixed_keys: frozenset[str] = frozenset(),
) -> dict[str, float]:
    """约束岭回归拟合候选权重。

    samples：[(anchor_score, {裸键: 分量得分})]，缺分量的样本由调用方剔除；
    fixed_keys 的权重冻结（gate 硬规则不入拟合）；零样本退化返回现权重。
    """
    keys = list(current_weights)
    w0 = np.array([float(current_weights[k]) for k in keys])
    if not samples:
        return {k: float(w) for k, w in zip(keys, w0, strict=True)}

    free_idx = [i for i, k in enumerate(keys) if k not in fixed_keys]
    X = np.array([[float(s[k]) for k in keys] for _, s in samples])
    y = np.array([float(a) for a, _ in samples])

    # 仅在自由变量上优化；fixed 维保持 w0（gate = 0.0）
    Xf = X[:, free_idx]
    w0f = w0[free_idx]
    fixed_sum = float(w0.sum() - w0f.sum())
    free_budget = 1.0 - fixed_sum  # 自由键单纯形预算：Σw_free = 1 − Σw_fixed

    w = w0f.copy()
    # 步长 = 1/L，L 为目标函数梯度的 Lipschitz 常数
    lipschitz = 2.0 * (float(np.linalg.norm(Xf, 2) ** 2) + ridge_lambda)
    step = 1.0 / max(lipschitz, 1e-12)
    for _ in range(_FIT_MAX_ITER):
        gradient = 2.0 * Xf.T @ (Xf @ w - y) + 2.0 * ridge_lambda * (w - w0f)
        w_next = _project_simplex(w - step * gradient) * free_budget
        if np.linalg.norm(w_next - w) < _FIT_TOL:
            w = w_next
            break
        w = w_next

    w_full = w0.copy()
    w_full[free_idx] = w
    result = {k: max(0.0, float(w_full[i])) for i, k in enumerate(keys)}
    # 数值尾数归一：自由键和精确为 1 − fixed_sum
    free_total = sum(result[k] for k in keys if k not in fixed_keys)
    if free_total > 0:
        scale = free_budget / free_total
        for k in keys:
            if k not in fixed_keys:
                result[k] *= scale
    return result
