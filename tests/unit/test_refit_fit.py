"""约束岭回归拟合单测（功能 010 US3 / T523，先于实现编写；research 决策 5）。

目标函数：min Σ(anchor_i − Σ_e w_e·s_ei)² + λ_ridge·‖w − w_current‖²，
s.t. w_e ≥ 0 且 Σw = 1（投影梯度 + 单纯形投影，numpy 实现零新增依赖）。

断言：结果非负且和为一；小样本下向现权重收缩（收缩保守性，宪章诚实边界同向）；
零样本退化返回现权重；λ_ridge 注入生效；fixed_keys（gate）权重冻结为零。
"""

import numpy as np
import pytest

from core.calibration.refit import fit_weights

# 两个自由评估器的合成数据集：真权重 (0.8, 0.2)，加噪锚点
_TRUE = np.array([0.8, 0.2])
_CURRENT = {"proxy.aesthetic": 0.5, "judge.cinematic": 0.5}


def _samples(n: int, seed: int = 7) -> list[tuple[float, dict[str, float]]]:
    rng = np.random.default_rng(seed)
    keys = list(_CURRENT)
    samples = []
    for _ in range(n):
        auto = rng.uniform(0.1, 0.9, size=len(keys))
        anchor = float(np.dot(auto, _TRUE) + rng.normal(0, 0.02))
        anchor = min(max(anchor, 0.0), 1.0)
        samples.append((anchor, {k: float(s) for k, s in zip(keys, auto, strict=True)}))
    return samples


def _to_vec(weights: dict[str, float], keys: list[str]) -> np.ndarray:
    return np.array([weights[k] for k in keys])


def _unconstrained_lstsq(samples, keys) -> np.ndarray:
    X = np.array([[s[k] for k in keys] for _, s in samples])
    y = np.array([a for a, _ in samples])
    return np.linalg.lstsq(X, y, rcond=None)[0]


class Test约束成立:
    def test_非负且和为一(self):
        candidate = fit_weights(_samples(20), _CURRENT, 1.0)
        assert all(v >= -1e-12 for v in candidate.values())
        assert sum(candidate.values()) == pytest.approx(1.0, abs=1e-9)

    def test_大样本收敛到真权重附近(self):
        candidate = fit_weights(_samples(200), _CURRENT, 1.0)
        vec = _to_vec(candidate, list(_CURRENT))
        assert np.linalg.norm(vec - _TRUE) < 0.1

    def test_gate_权重冻结(self):
        current = {"rule.format_compliance": 0.0, "proxy.a": 0.6, "proxy.b": 0.4}
        samples = [
            (0.7, {"rule.format_compliance": 1.0, "proxy.a": 0.8, "proxy.b": 0.6}),
            (0.5, {"rule.format_compliance": 1.0, "proxy.a": 0.4, "proxy.b": 0.7}),
            (0.6, {"rule.format_compliance": 1.0, "proxy.a": 0.5, "proxy.b": 0.5}),
        ]
        candidate = fit_weights(
            samples, current, 1.0, fixed_keys=frozenset({"rule.format_compliance"})
        )
        assert candidate["rule.format_compliance"] == 0.0  # gate 永不被拟合改动
        assert candidate["proxy.a"] + candidate["proxy.b"] == pytest.approx(1.0, abs=1e-9)


class Test收缩保守性:
    def test_小样本向现权重收缩(self):
        keys = list(_CURRENT)
        samples = _samples(3)
        candidate = _to_vec(fit_weights(samples, _CURRENT, 1.0), keys)
        w0 = _to_vec(_CURRENT, keys)
        unconstrained = _unconstrained_lstsq(samples, keys)
        assert np.linalg.norm(candidate - w0) < np.linalg.norm(unconstrained - w0)

    def test_零样本退化返回现权重(self):
        assert fit_weights([], _CURRENT, 1.0) == _CURRENT

    def test_lambda_注入生效(self):
        samples = _samples(3)
        near = fit_weights(samples, _CURRENT, 10.0)
        far = fit_weights(samples, _CURRENT, 0.01)
        keys = list(_CURRENT)
        w0 = _to_vec(_CURRENT, keys)
        # λ 越大越贴近现权重（收缩强度单调生效）
        assert np.linalg.norm(_to_vec(near, keys) - w0) < np.linalg.norm(_to_vec(far, keys) - w0)

    def test_零_lambda_逼近无约束解(self):
        keys = list(_CURRENT)
        samples = _samples(30)
        candidate = _to_vec(fit_weights(samples, _CURRENT, 0.0), keys)
        unconstrained = _unconstrained_lstsq(samples, keys)
        # 无约束解若可行（非负），λ=0 应与其几乎重合
        if np.all(unconstrained >= 0):
            assert np.linalg.norm(candidate - unconstrained) < 1e-3
