"""015 试水链的 012 漂移门禁接线测试（防回归：接线由**实参捕获**证明，非形状断言）。

背景：012 提供了 `DriftGate` 与各 loop 的 `drift_gate` 参数，但门禁只有在
编排层**真正把实例传下去**时才生效——只断言"loop 支持该参数"会永远为绿。
本文件的断言口径：

- 生产入口（`run_pilot` → `stages._*_entry`）调用各 Agent loop 时，
  `drift_gate` 实参**非 None**（移除装配即变红）；
- 四个有 judge 的阶段（script/storyboard/visual/editing）拿到的是**同一个实例**
  （runtime 装配一次、逐段透传：一处装配错配即变红）；
- 门禁读取的判据数据根 = 形态配置声明的校准目录（`web.data_dirs.calibration`
  相对形态配置目录的上一级解析）——在该目录登记 suspect，门禁必须读到。
"""

import pytest

from agents.pilot import stages as stages_module
from agents.pilot.pilot import PilotInputs, run_pilot
from core.calibration.drift_gate import DriftGate
from core.calibration.drift_models import DriftMetrics, DriftVerdict
from core.calibration.drift_status import register_suspect

FORM = "shortdrama"
_AT = "2026-09-21T10:00:00+00:00"
# 判据数据根相对形态配置目录的上一级（仓库根）解析：演示配置在 <tmp>/configs/ 下
_CALIBRATION_SUBDIR = "calibration"
# 四个有 judge 阶段的 loop 入口（promo/sound 无 judge 层 → 不接线，见 012 T1117 口径）
_WIRED_LOOPS = {
    "script": "run_screenplay_round",
    "storyboard": "run_storyboard_round",
    "visual": "run_visual_round",
    "editing": "run_editing_round",
}


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=2,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
    )


def _metrics(evaluator_key: str, agent_id: str) -> DriftMetrics:
    return DriftMetrics(
        evaluator_key=evaluator_key,
        agent_id=agent_id,
        period="2026-W39",
        verdict=DriftVerdict.DRIFT,
        samples=12,
        detector_version="drift_detector@1.0.0+0123456789ab",
        psi=0.31,
        quantile_shifts={"p25": 0.2, "p50": 0.2, "p75": 0.2, "p90": 0.2},
        baseline_ref="2026-W34..2026-W38",
        thresholds={"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5},
        note="PSI 0.3100 > 0.2",
    )


def test_生产路径向各_judge_阶段透传同一门禁实例(
    pilot_demo_config_path, pilot_dirs, tmp_path, monkeypatch
):
    """实参捕获：四个有 judge 阶段收到非 None 且同一实例的漂移门禁。"""
    captured: dict[str, object] = {}
    for stage_id, attr in _WIRED_LOOPS.items():
        real = getattr(stages_module, attr)

        def _spy(*args, _real=real, _stage=stage_id, **kwargs):
            captured[_stage] = kwargs.get("drift_gate")
            return _real(*args, **kwargs)

        monkeypatch.setattr(stages_module, attr, _spy)

    result = run_pilot(
        form=FORM,
        config_path=pilot_demo_config_path,
        inputs=_inputs(),
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
        run_id="run-drift-wiring",
        clock=lambda: "2026-01-01T00:00:00+00:00",
    )
    assert result.record.status.value == "done"
    assert set(captured) == set(_WIRED_LOOPS)  # 四个阶段全部经生产入口调用
    gates = list(captured.values())
    assert all(isinstance(gate, DriftGate) for gate in gates)  # 未接线（None）即失败
    assert len({id(gate) for gate in gates}) == 1  # runtime 装配一次 → 同一实例透传


def test_门禁读取的判据数据根为配置声明的校准目录(pilot_demo_config_path, pilot_dirs, tmp_path):
    """数据根接线：校准目录下的 suspect 登记必须进入门禁（读错目录 → 状态凭空消失）。"""
    calibration_dir = tmp_path / _CALIBRATION_SUBDIR
    evaluator_key = "judge.narrative_flow@1.0.0"
    register_suspect(calibration_dir, evaluator_key, _metrics(evaluator_key, "editing"), at=_AT)

    runtime = stages_module.build_runtime(
        form=FORM,
        config_path=pilot_demo_config_path,
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
    )
    gate = runtime.drift_gate
    assert isinstance(gate, DriftGate)
    assert evaluator_key in gate.registry.current()
    # 配置口径随装配进 runtime（非硬编码）：suspect 降权系数取自 calibration.drift
    assert gate.cfg.suspect_weight == 0.5
    # 该分量确实被降权（权重键无版本 → 按 evaluator_id 匹配；归一到原总权重）
    weighted = gate.apply_weights({"judge.narrative_flow": 1.0, "proxy.pacing_curve": 1.0})
    assert weighted["judge.narrative_flow"] < 1.0 < weighted["proxy.pacing_curve"]


def test_形态配置缺校准目录声明即报错(pilot_form_config_path, pilot_dirs, tmp_path):
    """配置缺项不静默：`web.data_dirs.calibration` 缺失 → 装配拒绝（不用魔法默认值）。"""
    source = pilot_form_config_path(FORM).read_text(encoding="utf-8")
    broken = tmp_path / "broken-configs" / "shortdrama.yaml"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(
        source.replace("    calibration: calibration ", "    calibration_removed: calibration "),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as excinfo:
        stages_module.build_runtime(
            form=FORM,
            config_path=broken,
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
        )
    assert "calibration" in str(excinfo.value)


def test_未接线的形态配置目录不含判据数据(pilot_demo_config_path, pilot_dirs, tmp_path):
    """对照：校准目录无登记时门禁为空登记（权重原样）——空登记不等于未接线。"""
    runtime = stages_module.build_runtime(
        form=FORM,
        config_path=pilot_demo_config_path,
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
    )
    assert runtime.drift_gate.registry.current() == {}
    assert runtime.drift_gate.apply_weights({"judge.cinematic": 0.3}) == {"judge.cinematic": 0.3}
