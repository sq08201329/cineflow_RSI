"""drift 配置解析单测（功能 012 / T1105，先于实现编写；FR-009 配置化）。

- 从 configs/movie.yaml 读真实 calibration.drift 段（窗口/分桶/双阈值/样本下限/
  降权系数/排除开关/检测范围/双信号规则）；
- 缺段/缺字段/非法值即报错（不允许静默用默认值——原则五 + 020 风格的诚实边界）；
- 数据目录约定（calibration/drift 四层子目录）随 T1101 落地，由本文件机检存在。
"""

from pathlib import Path

import pytest
import yaml

from core.calibration.drift_config import DoubleSignalRule, DriftConfig
from core.calibration.errors import CalibrationConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"

_VALID = {
    "window": 5,
    "buckets": 10,
    "psi_threshold": 0.2,
    "quantile_threshold": 0.1,
    "min_samples": 3,
    "suspect_weight": 0.5,
    "confirmed_exclude": True,
    "scope_kinds": ["judge"],
    "double_signal": {
        "enabled": True,
        "reliability_target": 0.6,
        "base_level": "warning",
        "escalated_level": "critical",
    },
}


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "form.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def _write_drift(tmp_path: Path, drift: dict) -> Path:
    return _write(tmp_path, {"calibration": {"drift": drift}})


def _with(**overrides) -> dict:
    section = {**_VALID, **overrides}
    return section


class Test读取真实配置:
    def test_movie_yaml_drift_段(self):
        config = DriftConfig.from_yaml(MOVIE_YAML)
        assert config.window == 5
        assert config.buckets == 10
        assert config.psi_threshold == pytest.approx(0.2)
        assert config.quantile_threshold == pytest.approx(0.1)
        assert config.min_samples == 3
        assert config.suspect_weight == pytest.approx(0.5)
        assert config.confirmed_exclude is True
        assert config.scope_kinds == ("judge",)  # 默认仅 judge 类（proxy/rule 默认关闭）

    def test_双信号规则(self):
        rule = DriftConfig.from_yaml(MOVIE_YAML).double_signal
        assert rule == DoubleSignalRule(
            enabled=True,
            reliability_target=0.6,
            base_level="warning",
            escalated_level="critical",
        )
        assert rule.escalated_level != rule.base_level  # 级别升级（双信号强化告警）

    def test_阈值快照形态(self):
        assert DriftConfig.from_yaml(MOVIE_YAML).thresholds_snapshot() == {
            "psi": 0.2,
            "quantile": 0.1,
            "min_samples": 3,
            "window": 5,
        }

    def test_数据目录约定存在(self):
        for sub in ("metrics", "status", "dispositions", "reports"):
            keep = REPO_ROOT / "calibration" / "drift" / sub / ".gitkeep"
            assert keep.is_file(), f"缺少数据目录占位 {keep}"


class Test缺段与缺字段:
    def test_缺_calibration_段报错(self, tmp_path):
        path = _write(tmp_path, {"form": "movie"})
        with pytest.raises(CalibrationConfigError, match="calibration"):
            DriftConfig.from_yaml(path)

    def test_缺_drift_段报错(self, tmp_path):
        path = _write(tmp_path, {"calibration": {"top_k": 5}})
        with pytest.raises(CalibrationConfigError, match="drift"):
            DriftConfig.from_yaml(path)

    def test_drift_段非映射报错(self, tmp_path):
        path = _write(tmp_path, {"calibration": {"drift": "oops"}})
        with pytest.raises(CalibrationConfigError, match="drift"):
            DriftConfig.from_yaml(path)

    @pytest.mark.parametrize(
        "missing",
        [
            "window",
            "buckets",
            "psi_threshold",
            "quantile_threshold",
            "min_samples",
            "suspect_weight",
            "confirmed_exclude",
            "scope_kinds",
            "double_signal",
        ],
    )
    def test_缺字段报错并指名(self, tmp_path, missing):
        section = {k: v for k, v in _VALID.items() if k != missing}
        path = _write_drift(tmp_path, section)
        with pytest.raises(CalibrationConfigError, match=missing):
            DriftConfig.from_yaml(path)

    def test_配置文件不存在报错(self, tmp_path):
        with pytest.raises(CalibrationConfigError):
            DriftConfig.from_yaml(tmp_path / "ghost.yaml")


class Test取值域:
    @pytest.mark.parametrize("key", ["window", "buckets", "min_samples"])
    def test_整数字段拒绝非正数与布尔(self, tmp_path, key):
        for bad in (0, -1, True, "5"):
            path = _write_drift(tmp_path, _with(**{key: bad}))
            with pytest.raises(CalibrationConfigError, match=key):
                DriftConfig.from_yaml(path)

    def test_buckets_至少两桶(self, tmp_path):
        path = _write_drift(tmp_path, _with(buckets=1))
        with pytest.raises(CalibrationConfigError, match="buckets"):
            DriftConfig.from_yaml(path)

    @pytest.mark.parametrize("key", ["psi_threshold", "quantile_threshold"])
    def test_阈值必须为正数(self, tmp_path, key):
        for bad in (0, -0.1, "high", True):
            path = _write_drift(tmp_path, _with(**{key: bad}))
            with pytest.raises(CalibrationConfigError, match=key):
                DriftConfig.from_yaml(path)

    def test_降权系数域(self, tmp_path):
        for bad in (-0.1, 1.5, "half", True):
            path = _write_drift(tmp_path, _with(suspect_weight=bad))
            with pytest.raises(CalibrationConfigError, match="suspect_weight"):
                DriftConfig.from_yaml(path)

    def test_排除开关必须布尔(self, tmp_path):
        for bad in ("true", 1, None):
            path = _write_drift(tmp_path, _with(confirmed_exclude=bad))
            with pytest.raises(CalibrationConfigError, match="confirmed_exclude"):
                DriftConfig.from_yaml(path)

    def test_检测范围必须为非空前缀列表(self, tmp_path):
        for bad in ("judge", [], [""], [1], {"judge": True}):
            path = _write_drift(tmp_path, _with(scope_kinds=bad))
            with pytest.raises(CalibrationConfigError, match="scope_kinds"):
                DriftConfig.from_yaml(path)

    def test_检测范围可纳入_proxy(self, tmp_path):
        path = _write_drift(tmp_path, _with(scope_kinds=["judge", "proxy"]))
        assert DriftConfig.from_yaml(path).scope_kinds == ("judge", "proxy")


class Test双信号规则:
    @pytest.mark.parametrize(
        "missing", ["enabled", "reliability_target", "base_level", "escalated_level"]
    )
    def test_缺键报错(self, tmp_path, missing):
        rule = {k: v for k, v in _VALID["double_signal"].items() if k != missing}
        path = _write_drift(tmp_path, _with(double_signal=rule))
        with pytest.raises(CalibrationConfigError, match=f"double_signal.{missing}"):
            DriftConfig.from_yaml(path)

    def test_规则非映射报错(self, tmp_path):
        path = _write_drift(tmp_path, _with(double_signal=True))
        with pytest.raises(CalibrationConfigError, match="double_signal"):
            DriftConfig.from_yaml(path)

    def test_开关必须布尔(self, tmp_path):
        rule = {**_VALID["double_signal"], "enabled": "yes"}
        path = _write_drift(tmp_path, _with(double_signal=rule))
        with pytest.raises(CalibrationConfigError, match="enabled"):
            DriftConfig.from_yaml(path)

    def test_信度目标域(self, tmp_path):
        for bad in (-0.1, 1.2, "high"):
            rule = {**_VALID["double_signal"], "reliability_target": bad}
            path = _write_drift(tmp_path, _with(double_signal=rule))
            with pytest.raises(CalibrationConfigError, match="reliability_target"):
                DriftConfig.from_yaml(path)

    def test_级别非空且必须升级(self, tmp_path):
        for overrides in ({"base_level": ""}, {"escalated_level": ""}):
            rule = {**_VALID["double_signal"], **overrides}
            path = _write_drift(tmp_path, _with(double_signal=rule))
            with pytest.raises(CalibrationConfigError, match="level"):
                DriftConfig.from_yaml(path)
        same = {**_VALID["double_signal"], "escalated_level": "warning"}
        path = _write_drift(tmp_path, _with(double_signal=same))
        with pytest.raises(CalibrationConfigError, match="升级"):
            DriftConfig.from_yaml(path)
