"""calibration 段配置解析单测（功能 010 / T507，先于实现编写）。

- 从 configs/movie.yaml 读真实 calibration 段；
- 缺段/缺字段/类型错误均报清晰错误（风格对齐 core/evaluators/weights.py）；
- self_pairing_exclusions 映射结构校验（防自循环配对的配置驱动来源）。
"""

from pathlib import Path

import pytest
import yaml

from core.calibration.config import CalibrationConfig
from core.calibration.errors import CalibrationConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"

_VALID_SECTION = {
    "period_days": 7,
    "top_k": 5,
    "min_samples": 3,
    "bias_threshold": 0.15,
    "reliability_target": 0.6,
    "ridge_lambda": 1.0,
    "self_pairing_exclusions": {"platform_truth": ["human.platform_metrics"]},
}


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "form.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


class Test读取真实配置:
    def test_movie_yaml_calibration_段(self):
        config = CalibrationConfig.from_yaml(MOVIE_YAML)
        assert config.period_days == 7
        assert config.top_k == 5
        assert config.min_samples == 3
        assert config.bias_threshold == pytest.approx(0.15)
        assert config.reliability_target == pytest.approx(0.6)
        assert config.ridge_lambda == pytest.approx(1.0)

    def test_自循环排除映射(self):
        config = CalibrationConfig.from_yaml(MOVIE_YAML)
        assert config.self_pairing_exclusions == {"platform_truth": ("human.platform_metrics",)}


class Test缺段与缺字段:
    def test_缺_calibration_段报错(self, tmp_path):
        path = _write(tmp_path, {"form": "x"})
        with pytest.raises(CalibrationConfigError, match="calibration"):
            CalibrationConfig.from_yaml(path)

    def test_calibration_段非映射报错(self, tmp_path):
        path = _write(tmp_path, {"calibration": "oops"})
        with pytest.raises(CalibrationConfigError, match="calibration"):
            CalibrationConfig.from_yaml(path)

    @pytest.mark.parametrize(
        "missing",
        [
            "period_days",
            "top_k",
            "min_samples",
            "bias_threshold",
            "reliability_target",
            "ridge_lambda",
            "self_pairing_exclusions",
        ],
    )
    def test_缺字段报错并指名(self, tmp_path, missing):
        section = {k: v for k, v in _VALID_SECTION.items() if k != missing}
        path = _write(tmp_path, {"calibration": section})
        with pytest.raises(CalibrationConfigError, match=missing):
            CalibrationConfig.from_yaml(path)

    def test_配置文件不存在报错(self, tmp_path):
        with pytest.raises(CalibrationConfigError):
            CalibrationConfig.from_yaml(tmp_path / "ghost.yaml")


class Test类型与取值域:
    @pytest.mark.parametrize("key", ["period_days", "top_k", "min_samples"])
    def test_整数字段拒绝非正数与布尔(self, tmp_path, key):
        for bad in (0, -1, True, "5"):
            path = _write(tmp_path, {"calibration": {**_VALID_SECTION, key: bad}})
            with pytest.raises(CalibrationConfigError, match=key):
                CalibrationConfig.from_yaml(path)

    def test_bias_threshold_非负(self, tmp_path):
        path = _write(tmp_path, {"calibration": {**_VALID_SECTION, "bias_threshold": -0.1}})
        with pytest.raises(CalibrationConfigError, match="bias_threshold"):
            CalibrationConfig.from_yaml(path)

    @pytest.mark.parametrize("bad", [-0.1, 1.2, "high"])
    def test_reliability_target_域(self, tmp_path, bad):
        path = _write(tmp_path, {"calibration": {**_VALID_SECTION, "reliability_target": bad}})
        with pytest.raises(CalibrationConfigError, match="reliability_target"):
            CalibrationConfig.from_yaml(path)

    def test_ridge_lambda_非负数值(self, tmp_path):
        path = _write(tmp_path, {"calibration": {**_VALID_SECTION, "ridge_lambda": "strong"}})
        with pytest.raises(CalibrationConfigError, match="ridge_lambda"):
            CalibrationConfig.from_yaml(path)

    def test_排除映射结构(self, tmp_path):
        for bad in ("platform_truth", {"platform_truth": "human.platform_metrics"}, {1: []}):
            path = _write(
                tmp_path, {"calibration": {**_VALID_SECTION, "self_pairing_exclusions": bad}}
            )
            with pytest.raises(CalibrationConfigError, match="self_pairing_exclusions"):
                CalibrationConfig.from_yaml(path)
