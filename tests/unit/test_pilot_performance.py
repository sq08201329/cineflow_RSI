"""功能 018 阶段 6 / US3（契约 C13）：性能画像与预算门禁的证据面。

覆盖：

1. 画像落 `pilot/profiles/{run_id}.json`：各环节 `started_at`/`finished_at` + 体量指标（**取自
   既有明细**，与包内 `state.volume` 同源）+ `pilot.performance` 阈值快照 + `verdict` + 时钟口径；
   体量口径与 `screenplay.target_duration_min × 60` 一致（SC-012①）；
2. **固定时钟运行恒 `not_evaluable`** + "耗时为确定性常量、不构成性能证据"（不产出达标结论）；
3. **退化交叉核验**：各环节时间戳全等 ⇒ 即使声明 `system` 也强制 `not_evaluable`（声明撒谎
   被证据推翻）；
4. `status: unstandardized` ⇒ 只出台账与体量 + "未标定"，**无数字、无达标结论**；
5. 声明阈值 + 系统时钟 ⇒ 逐环节对照给出 `meets` / `below`（结论词机读）；
6. **画像不入包**（五件套不变）；**新增字段的墙钟隔离**：同输入同配置下一次固定时钟、一次
   系统时钟两次运行，`source`/`channels`/`work_kind`/`eval_breakdown`/`volume` 子集逐字节一致；
7. `ops/pilot.py perf` 退出码语义（0 达标 / 1 未达标或不可评价 / 2 用法错误）。
"""

import json
from pathlib import Path

import pytest
import yaml

from agents.pilot import run_report
from agents.pilot import stages as stages_module
from agents.pilot.package import PACKAGE_FILES
from agents.pilot.pilot import PilotInputs, run_pilot
from agents.pilot.stages import PILOT_STAGE_IDS
from core.orchestration.models import (
    ProductRef,
    RunRecord,
    RunStatus,
    StageState,
    StageStatus,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FORM = "shortdrama"
FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
_CAPTURED: dict = {}
# 被比对的新增字段子集（墙钟隔离机检；跨时钟必须逐字节一致）
_NEW_FIELDS = ("channels", "work_kind")


def _base_text(tmp_path: Path) -> str:
    text = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    assert "root: billing" in text
    text = text.replace("root: billing", f"root: {tmp_path / 'billing'}")
    for old, new in (
        ("target_duration_s: 120.0", "target_duration_s: 30.0"),
        ("script_target_minutes: 2.0", "script_target_minutes: 0.5"),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def _config(tmp_path: Path, *, mutate=None, name: str = "demo") -> Path:
    payload = yaml.safe_load(_base_text(tmp_path))
    if mutate is not None:
        mutate(payload)
    path = tmp_path / "configs" / f"shortdrama-{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _declared_thresholds(payload: dict, seconds: float) -> None:
    payload["pilot"]["performance"] = {
        "status": "declared",
        "stage_seconds": {stage_id: seconds for stage_id in PILOT_STAGE_IDS},
    }


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=0.5,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


def _counter_clock():
    """系统时钟口径的替身：每次调用 +1 秒（时间戳两两不等 ⇒ 不触发退化核验）。"""
    state = {"n": 0}

    def _tick() -> str:
        state["n"] += 1
        return f"2026-01-01T00:00:{state['n'] % 60:02d}+00:00"

    return _tick


def _run(config_path: Path, data_dir: Path, artifacts_root: Path, run_id: str, clock):
    """跑一轮并捕获 runtime（画像取数需要本次运行的工件根与形态配置）。"""
    original = stages_module.build_runtime
    captured: dict = {}

    def _capture(**kwargs):
        runtime = original(**kwargs)
        captured["runtime"] = runtime
        return runtime

    stages_module.build_runtime = _capture
    try:
        result = run_pilot(
            form=FORM,
            config_path=config_path,
            inputs=_inputs(),
            data_dir=data_dir,
            artifacts_root=artifacts_root,
            run_id=run_id,
            clock=clock,
        )
    finally:
        stages_module.build_runtime = original
    return result, captured["runtime"]


@pytest.fixture(scope="module")
def pilot_runs(tmp_path_factory):
    """两次真实运行：固定时钟（A）与系统时钟（B）——**同输入同 run_id、独立工件根**。

    同 run_id 是墙钟隔离对照的前提（节点 id 由 run_id 派生；两套数据根互不干扰）。
    """
    tmp_path = tmp_path_factory.mktemp("performance")
    config_path = _config(tmp_path)
    fixed, fixed_runtime = _run(
        config_path,
        tmp_path / "a" / "pilot",
        tmp_path / "a" / "artifacts",
        "run-clock",
        lambda: FIXED_TIMESTAMP,
    )
    system, system_runtime = _run(
        config_path,
        tmp_path / "b" / "pilot",
        tmp_path / "b" / "artifacts",
        "run-clock",
        _counter_clock(),
    )
    assert fixed.record.status is RunStatus.DONE and system.record.status is RunStatus.DONE
    _CAPTURED.update(
        {
            "fixed": fixed,
            "system": system,
            "runtime_fixed": fixed_runtime,
            "runtime_system": system_runtime,
            "config_path": config_path,
            "tmp": tmp_path,
        }
    )
    return _CAPTURED


def _profile(run, *, clock_mode: str, which: str) -> dict:
    """画像（`which` 指明取哪一次运行的 runtime：工件根随运行独立）。"""
    return run_report.build_profile(
        run.record, _CAPTURED[f"runtime_{which}"], clock_mode=clock_mode
    )


def _load(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _new_fields(package_dir: Path) -> dict:
    manifest = _load(package_dir, "manifest.json")
    state = _load(package_dir, "state.json")
    return {
        "manifest": {key: manifest[key] for key in _NEW_FIELDS},
        "stages": [
            {"stage_id": row["stage_id"], "source": row["source"]} for row in manifest["stages"]
        ],
        "state": {key: state[key] for key in ("eval_breakdown", "volume", "derived_products")},
    }


class Test画像字段:
    def test_画像字段与体量取自既有明细(self, pilot_runs):
        profile = _profile(pilot_runs["fixed"], clock_mode="fixed", which="fixed")
        record = pilot_runs["fixed"].record
        assert profile["run_id"] == record.run_id
        assert profile["form"] == FORM
        assert profile["clock_mode"] == "fixed"
        assert tuple(profile["stages"]) == PILOT_STAGE_IDS
        for stage_id, row in profile["stages"].items():
            state = record.stage(stage_id)
            assert row["started_at"] == state.started_at
            assert row["finished_at"] == state.finished_at
            assert row["cost_usd"] == state.cost_usd
            assert row["gateway_delta_usd"] == state.detail["gateway_delta_usd"]
        volume = profile["volume"]
        assert volume["shot_count"] == record.stage("storyboard").detail["shot_count"]
        assert volume["clip_count"] == record.stage("visual").detail["clip_count"]
        assert volume["reel_duration_s"] == pytest.approx(
            record.stage("editing").detail["reel"]["duration_ms"] / 1000.0
        )
        # SC-012①：成片时长（生效值）与剧本目标 × 60 同口径
        assert volume["target_duration_s"] == pytest.approx(
            volume["script_target_minutes"] * 60.0, abs=1e-6
        )
        assert volume["page_count"] is not None

    def test_画像与包内体量同源(self, pilot_runs):
        package_dir = pilot_runs["fixed"].package_dir
        state = _load(package_dir, "state.json")
        profile = _profile(pilot_runs["fixed"], clock_mode="fixed", which="fixed")
        assert profile["volume"] == state["volume"]  # 同一取值来源（单一快照）

    def test_画像落报告侧且不入包(self, pilot_runs):
        profile = _profile(pilot_runs["fixed"], clock_mode="fixed", which="fixed")
        path = run_report.write_profile(pilot_runs["tmp"] / "a" / "pilot", profile)
        assert path == run_report.profile_path(
            pilot_runs["tmp"] / "a" / "pilot", pilot_runs["fixed"].run_id
        )
        assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == profile["verdict"]
        assert sorted(p.name for p in pilot_runs["fixed"].package_dir.iterdir()) == sorted(
            PACKAGE_FILES
        )

    def test_画像身份_append_only(self, pilot_runs):
        """同一 `(run_id, 时钟口径)` 幂等；换时钟口径 ⇒ 拒绝（报告侧 append-only）。"""
        data_dir = pilot_runs["tmp"] / "c" / "pilot"
        fixed = _profile(pilot_runs["fixed"], clock_mode="fixed", which="fixed")
        run_report.write_profile(data_dir, fixed)
        run_report.write_profile(data_dir, fixed)  # 同口径重算：幂等
        with pytest.raises(run_report.ReportError, match="append-only"):
            run_report.write_profile(
                data_dir, _profile(pilot_runs["fixed"], clock_mode="system", which="fixed")
            )


class Test结论词:
    def test_固定时钟恒不可评价(self, pilot_runs):
        profile = _profile(pilot_runs["fixed"], clock_mode="fixed", which="fixed")
        assert profile["verdict"] == "not_evaluable"
        assert run_report.FIXED_CLOCK_NOTE in profile["verdict_reason"]
        assert "不构成性能证据" in profile["verdict_reason"]

    def test_未标定只出台账与体量(self, pilot_runs):
        profile = _profile(pilot_runs["system"], clock_mode="system", which="system")
        assert profile["thresholds"]["status"] == "unstandardized"
        assert profile["thresholds"]["stage_seconds"] == {}  # 不发明数字
        assert profile["verdict"] == "not_evaluable"
        assert "未标定" in profile["verdict_reason"]

    def test_时间戳退化强制不可评价(self, tmp_path):
        """全部时间戳同值 + 声明 system ⇒ 证据推翻声明（恒 `not_evaluable`）。"""
        config_path = _config(tmp_path, mutate=_declared(1e9), name="declared-degenerate")
        runtime = stages_module.build_runtime(
            form=FORM,
            config_path=config_path,
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
        )
        record = _record(timestamps=[FIXED_TIMESTAMP] * 2, run_id="run-degenerate")
        profile = run_report.build_profile(record, runtime, clock_mode="system")
        assert profile["verdict"] == "not_evaluable"
        assert run_report.DEGENERATE_NOTE in profile["verdict_reason"]

    def test_声明阈值与系统时钟逐环节对照(self, tmp_path):
        config_path = _config(tmp_path, mutate=_declared(3600.0), name="declared-loose")
        runtime = stages_module.build_runtime(
            form=FORM,
            config_path=config_path,
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
        )
        record = _record(
            timestamps=["2026-01-01T00:00:01+00:00", "2026-01-01T00:00:03+00:00"],
            run_id="run-meets",
        )
        meets = run_report.build_profile(record, runtime, clock_mode="system")
        assert meets["verdict"] == "meets"
        tight = _config(tmp_path, mutate=_declared(0.5), name="declared-tight")
        tight_runtime = stages_module.build_runtime(
            form=FORM,
            config_path=tight,
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
        )
        below = run_report.build_profile(record, tight_runtime, clock_mode="system")
        assert below["verdict"] == "below"
        assert "超阈值环节" in below["verdict_reason"]

    def test_时钟口径非法即拒绝(self, pilot_runs):
        with pytest.raises(run_report.ReportError, match="时钟口径"):
            _profile(pilot_runs["fixed"], clock_mode="guessed", which="fixed")


def _declared(seconds: float):
    def _mutate(payload: dict) -> None:
        _declared_thresholds(payload, seconds)

    return _mutate


def _record(*, timestamps: list[str], run_id: str) -> RunRecord:
    """合成运行记录（同值时间戳对 + 最小产物）：只用于结论词判定（口径与实跑一致）。"""
    start, finish = timestamps[0], timestamps[1]
    stages = tuple(
        StageState(
            stage_id=stage_id,
            status=StageStatus.DONE,
            input_fingerprint="ab" * 32,
            products=(ProductRef(kind="reel", ref="ref", content_hash="cd" * 32),),
            cost_usd=0.0,
            started_at=start,
            finished_at=finish,
            detail={"spent_usd": 0.0, "gateway_delta_usd": 0.0},
        )
        for stage_id in PILOT_STAGE_IDS
    )
    return RunRecord(
        run_id=run_id,
        form=FORM,
        config_fingerprint="ef" * 32,
        input_fingerprint="ab" * 32,
        stages=stages,
        status=RunStatus.DONE,
        started_at=start,
        finished_at=finish,
    )


class Test墙钟隔离:
    def test_新增字段跨时钟逐字节一致(self, pilot_runs):
        fixed = _new_fields(pilot_runs["fixed"].package_dir)
        system = _new_fields(pilot_runs["system"].package_dir)
        assert fixed == system

    def test_新字段不含墙钟(self, pilot_runs):
        payload = json.dumps(_new_fields(pilot_runs["fixed"].package_dir), sort_keys=True)
        for banned in ("created_at", "timestamp", "wall_clock", "started_at", "finished_at"):
            assert banned not in payload, banned


class Testperf_子命令:
    def test_退出码_不可评价即_1(self, pilot_runs):
        from ops.pilot import main

        code = main(
            [
                "perf",
                "--form",
                FORM,
                "--config",
                str(pilot_runs["config_path"]),
                "--data-dir",
                str(pilot_runs["tmp"] / "a" / "pilot"),
                "--run-id",
                "run-clock",
                "--artifacts-root",
                str(pilot_runs["tmp"] / "a" / "artifacts"),
                "--clock",
                "fixed",
            ]
        )
        assert code == 1  # 固定时钟 = 不可评价
        path = run_report.profile_path(pilot_runs["tmp"] / "a" / "pilot", "run-clock")
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert profile["verdict"] == "not_evaluable"

    def test_缺时钟声明即用法错误(self, pilot_runs):
        from ops.pilot import main

        with pytest.raises(SystemExit) as excinfo:
            main(
                [
                    "perf",
                    "--form",
                    FORM,
                    "--config",
                    str(pilot_runs["config_path"]),
                    "--data-dir",
                    str(pilot_runs["tmp"] / "a" / "pilot"),
                    "--run-id",
                    "run-clock",
                ]
            )
        assert excinfo.value.code == 2  # 用法错误（时钟口径无默认）
