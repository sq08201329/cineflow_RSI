"""漂移量纲与可追溯单测（功能 020 契约 C4，T2011）。

- **同量纲率 100%**：`thresholds["window_unit"] == {1:"day",7:"week"}[cfg.period_days]`
  且 `thresholds["period_days"] == cfg.period_days == cadence_of(record.period)`；
- **口径版本可区分**：`metric_hash` 纳入 `period_days` ⇒ 日级与周级不共用同一
  `detector_version`（口径变更即新版本、历史判定不回溯）；
- **自描述率 100%**：判定类（`normal|drift`）记录 `snapshot_fingerprint` 非空且为 64 位
  小写十六进制，三种**如实标注类**（`insufficient` / `no_baseline` / `no_data`）为 `None`；
  `record.samples == 所读快照的 samples`；
- **每周期读一份**：`(evaluator_id, period)` 只有一个快照路径，读取取最后一次物化；
- **三值各归其位**：无快照 ⇒ `no_data`；样本不足 ⇒ `insufficient`（**不是** `no_data`）；
  首周期无基线 ⇒ `no_baseline`；非判定类一律不得携带 `psi` / `quantile_shifts`
  （模型层约束，违反即 `ValidationError`）；
- 日级形态产出 `window_unit == "week"` ⇒ 红；`ops/calibrate.py` 的 `drift` 子命令输出
  含 `period_days` / `window_unit` 两键。
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from core.calibration.drift_config import DriftConfig
from core.calibration.drift_metrics import (
    available_periods,
    detect_drift,
    detector_version,
    metric_hash,
    missing_periods,
    record_path,
    snapshot_path,
)
from core.calibration.drift_models import JUDGED_VERDICTS, DriftMetrics, DriftVerdict
from core.calibration.ledger import snapshot_fingerprint
from core.calibration.models import PairingRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
_MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
_SHORT_YAML = REPO_ROOT / "configs" / "shortdrama.yaml"
_AGENT = "visual"
_KEY = "judge.cinematic@1.0.0"
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _weekly_cfg() -> DriftConfig:
    return DriftConfig.from_yaml(_MOVIE_YAML)


def _daily_cfg(**overrides) -> DriftConfig:
    """短剧态 cfg（读真实配置），必要时按字段覆盖（同口径比较用）。"""
    cfg = DriftConfig.from_yaml(_SHORT_YAML)
    return replace(cfg, **overrides) if overrides else cfg


def _engine():
    from core.calibration.db import create_anchor_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_anchor_schema(engine)
    return engine


def _write_period(
    data_dir, period: str, *, scores, evaluator_key: str = _KEY, agent_id: str = _AGENT
):
    from core.calibration.ledger import write_anchor_snapshots

    pairs = [
        PairingRecord(
            anchor_id=f"{period}-a{index}",
            evaluator_key=evaluator_key,
            anchor_score=float(score),
            auto_score=0.5,
        )
        for index, score in enumerate(scores)
    ]
    return write_anchor_snapshots(data_dir, agent_id, period, pairs)


def _write_ledger_version(data_dir, period: str, evaluator_key: str = _KEY, agent_id: str = _AGENT):
    from core.calibration.ledger import append_ledger
    from core.calibration.models import BiasRecord

    append_ledger(
        data_dir,
        agent_id,
        [BiasRecord(evaluator_key=evaluator_key, period=period, samples=3, note="夹具")],
    )


def _band(center: float = 0.5) -> list[float]:
    return [round(center + 0.05 * z, 6) for z in (-2, -1, 0, 1, 2)]


class Test阈值自描述与同量纲:
    def test_周级与日级各自的窗口单位(self):
        weekly, daily = _weekly_cfg(), _daily_cfg()
        assert weekly.thresholds_snapshot()["window_unit"] == "week"
        assert weekly.thresholds_snapshot()["period_days"] == 7
        assert daily.thresholds_snapshot()["window_unit"] == "day"
        assert daily.thresholds_snapshot()["period_days"] == 1

    def test_记录口径三者一致率百分之百(self, tmp_path):
        from core.calibration.periods import cadence_of

        data_dir = tmp_path / "calibration"
        for period in ("2026-W34", "2026-W35", "2026-W36", "2026-W37"):
            _write_period(data_dir, period, scores=_band())
            _write_ledger_version(data_dir, period)
        _write_period(data_dir, "2026-W38", scores=_band(0.6))
        _write_ledger_version(data_dir, "2026-W38")
        cfg = _weekly_cfg()
        record = detect_drift(_AGENT, _KEY, "2026-W38", cfg, data_dir)
        assert record.thresholds["window_unit"] == "week"
        assert record.thresholds["period_days"] == cfg.period_days == cadence_of(record.period) == 7

    def test_日级标签配周级配置即拒绝(self, tmp_path):
        """口径不一致 ⇒ 拒绝静默比较（"日级形态产出 window_unit=week"因此不可能）。"""
        from core.evaluators.errors import ValidationError

        data_dir = tmp_path / "calibration"
        _write_period(data_dir, "2026-09-25", scores=_band())
        with pytest.raises(ValidationError, match="cadence"):
            detect_drift(_AGENT, _KEY, "2026-09-25", _weekly_cfg(), data_dir)

    def test_日级形态产出_day_单位(self, tmp_path):
        data_dir = tmp_path / "calibration"
        for day in ("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"):
            _write_period(data_dir, day, scores=_band())
            _write_ledger_version(data_dir, day)
        _write_period(data_dir, "2026-09-25", scores=_band(0.6))
        _write_ledger_version(data_dir, "2026-09-25")
        cfg = _daily_cfg()
        record = detect_drift(_AGENT, _KEY, "2026-09-25", cfg, data_dir)
        assert record.thresholds["window_unit"] == "day"
        assert record.verdict in JUDGED_VERDICTS

    def test_缺失周期按_cadence_派生(self):
        assert missing_periods(["2026-W38", "2026-W40"]) == ("2026-W39",)
        assert missing_periods(["2026-09-01", "2026-09-04"]) == ("2026-09-02", "2026-09-03")
        assert missing_periods([]) == ()


class Test口径版本可区分:
    def test_日级与周级不共用同一个哈希(self):
        weekly = _weekly_cfg()
        assert metric_hash(replace(weekly, period_days=1)) != metric_hash(weekly)
        assert detector_version(replace(weekly, period_days=1)) != detector_version(weekly)
        assert detector_version(weekly).startswith("drift_detector@1.0.0+")
        assert detector_version(_daily_cfg()) != detector_version(weekly)

    def test_历史记录不回溯改写(self, tmp_path):
        """口径变更（thresholds 变）后对**同一周期**再检测 ⇒ 拒绝改写历史记录。"""
        from core.calibration.errors import DriftRecordConflictError

        data_dir = tmp_path / "calibration"
        for period in ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38"):
            _write_period(data_dir, period, scores=_band())
            _write_ledger_version(data_dir, period)
        cfg = _weekly_cfg()
        detect_drift(_AGENT, _KEY, "2026-W38", cfg, data_dir)
        path = record_path(data_dir, _AGENT, _KEY, "2026-W38")
        before = path.read_bytes()
        with pytest.raises(DriftRecordConflictError):
            detect_drift(_AGENT, _KEY, "2026-W38", replace(cfg, psi_threshold=0.05), data_dir)
        assert path.read_bytes() == before


class Test自描述率与三值各归其位:
    def test_判定类必带指纹而标注类必为_none(self, tmp_path):
        data_dir = tmp_path / "calibration"
        for period in ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38"):
            _write_period(data_dir, period, scores=_band())
            _write_ledger_version(data_dir, period)
        cfg = _weekly_cfg()

        judged = detect_drift(_AGENT, _KEY, "2026-W38", cfg, data_dir)
        assert judged.verdict in JUDGED_VERDICTS
        assert _FINGERPRINT_RE.fullmatch(judged.snapshot_fingerprint)
        on_disk = json.loads(
            snapshot_path(data_dir, _AGENT, "judge.cinematic", "2026-W38").read_text(
                encoding="utf-8"
            )
        )
        assert judged.snapshot_fingerprint == snapshot_fingerprint(on_disk)
        assert judged.samples == on_disk["samples"]  # 所读快照的锚点数 = samples

        # 无快照 ⇒ no_data 且指纹为 None
        no_data = detect_drift(_AGENT, _KEY, "2026-W39", cfg, data_dir)
        assert no_data.verdict is DriftVerdict.NO_DATA
        assert no_data.snapshot_fingerprint is None
        assert no_data.psi is None and no_data.quantile_shifts == {}

    def test_首周期无基线(self, tmp_path):
        data_dir = tmp_path / "calibration"
        _write_period(data_dir, "2026-W38", scores=_band())
        _write_ledger_version(data_dir, "2026-W38")
        record = detect_drift(_AGENT, _KEY, "2026-W38", _weekly_cfg(), data_dir)
        assert record.verdict is DriftVerdict.NO_BASELINE
        assert record.baseline_ref is None  # 不得携带基线引用
        assert record.snapshot_fingerprint is None
        assert "首周期无基线" in record.note

    def test_样本不足是_insufficient_而非_no_data(self, tmp_path):
        data_dir = tmp_path / "calibration"
        for period in ("2026-W34", "2026-W35", "2026-W36", "2026-W37"):
            _write_period(data_dir, period, scores=_band())
            _write_ledger_version(data_dir, period)
        _write_period(data_dir, "2026-W38", scores=[0.5])  # 当前周期样本不足
        _write_ledger_version(data_dir, "2026-W38")
        record = detect_drift(_AGENT, _KEY, "2026-W38", _weekly_cfg(), data_dir)
        assert record.verdict is DriftVerdict.INSUFFICIENT
        assert record.psi is None and record.quantile_shifts == {}
        assert record.snapshot_fingerprint is None
        assert record.baseline_ref  # 序列有基线但样本不足 ⇒ 基线引用必须非空
        assert "样本不足" in record.note

    def test_标注类携带指标即_ValidationError(self):
        """模型层约束常驻：非判定类不得携带 `psi` / `quantile_shifts`。"""
        from core.evaluators.errors import ValidationError

        base = {
            "evaluator_key": _KEY,
            "agent_id": _AGENT,
            "period": "2026-W38",
            "samples": 1,
            "detector_version": detector_version(_weekly_cfg()),
            "thresholds": _weekly_cfg().thresholds_snapshot(),
        }
        for verdict in (
            DriftVerdict.INSUFFICIENT,
            DriftVerdict.NO_BASELINE,
            DriftVerdict.NO_DATA,
        ):
            with pytest.raises(ValidationError, match="不得携带 psi"):
                DriftMetrics(**base, verdict=verdict, psi=0.3, baseline_ref="2026-W34..2026-W37")
            with pytest.raises(ValidationError, match="quantile_shifts"):
                DriftMetrics(
                    **base,
                    verdict=verdict,
                    quantile_shifts={"p50": 0.1},
                    baseline_ref="2026-W34..2026-W37",
                )
            with pytest.raises(ValidationError, match="snapshot_fingerprint"):
                DriftMetrics(
                    **base,
                    verdict=verdict,
                    snapshot_fingerprint="ab" * 32,
                    baseline_ref="2026-W34..2026-W37",
                )
        # 判定类必须携带 psi 与指纹（指纹由 detect_drift 填；模型层不强制其非空，
        # 以兼容既有直接构造点——机检落在 **detect_drift 的产出**上，见 T2011③）
        with pytest.raises(ValidationError, match="必须携带 psi"):
            DriftMetrics(**base, verdict=DriftVerdict.NORMAL, baseline_ref="2026-W34..2026-W37")
        with pytest.raises(ValidationError, match="snapshot_fingerprint"):
            DriftMetrics(
                **base,
                verdict=DriftVerdict.NORMAL,
                psi=0.1,
                quantile_shifts={"p50": 0.0},
                baseline_ref="2026-W34..2026-W37",
                snapshot_fingerprint="zz",  # 形态非法（非 64 位小写十六进制）
            )

    def test_五值枚举取值域(self):
        assert {verdict.value for verdict in DriftVerdict} == {
            "normal",
            "drift",
            "insufficient",
            "no_baseline",
            "no_data",
        }
        assert JUDGED_VERDICTS == (DriftVerdict.NORMAL, DriftVerdict.DRIFT)


class Test每周期读一份:
    def test_同一评估器周期只有一个快照路径(self, tmp_path):
        """同周期多轮物化 ⇒ 路径唯一、读取取最后一次物化（轮标识不进快照路径）。"""
        data_dir = tmp_path / "calibration"
        _write_period(data_dir, "2026-W39", scores=_band())
        first = snapshot_path(data_dir, _AGENT, "judge.cinematic", "2026-W39")
        first_bytes = first.read_bytes()
        _write_period(data_dir, "2026-W39", scores=_band(0.7))  # 迟到/回补：内容变化
        assert first.read_bytes() != first_bytes
        assert available_periods(data_dir, _AGENT, "judge.cinematic") == ("2026-W39",)
        assert len(list((data_dir / "snapshots" / _AGENT / "judge.cinematic").glob("*.json"))) == 1

    def test_缺口周期不入窗口且缺口如实报(self, tmp_path):
        data_dir = tmp_path / "calibration"
        for period in ("2026-W34", "2026-W35", "2026-W37", "2026-W38"):
            _write_period(data_dir, period, scores=_band())
            _write_ledger_version(data_dir, period)
        record = detect_drift(_AGENT, _KEY, "2026-W38", _weekly_cfg(), data_dir)
        assert "2026-W36" in record.note  # 缺口如实列出（不插值）


class Test校准_CLI_输出口径:
    def test_drift_子命令含_period_days_与_window_unit(self, tmp_path):
        """T2041⑦：`ops/calibrate.py drift` 的输出含 `period_days` / `window_unit` 两键。"""
        source = (REPO_ROOT / "ops" / "calibrate.py").read_text(encoding="utf-8")
        assert '"period_days": config.period_days' in source
        assert '"window_unit": config.window_unit' in source
