"""功能 015 US3（T1519）：`agents/pilot/pilot.py` 试水运行测试（契约 C10）。

覆盖：启动前预检（输入不足 / 配置缺项 → 拒绝且零成本零落树）、一次运行六阶段 done、
**同输入同配置两次运行逐字节一致**（注入确定性时钟）、断点续跑不重跑已完成阶段、
输入/配置变更后拒绝续跑、跳过与失败语义。
"""

import json
from pathlib import Path

import pytest

from agents.pilot.pilot import (
    PilotInputs,
    PrecheckError,
    precheck,
    resume_pilot,
    run_pilot,
)
from core.orchestration.errors import OrchestrationError

FORM = "shortdrama"


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=2,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
    )


def _fixed_clock():
    counter = {"n": 0}

    def _tick() -> str:
        counter["n"] += 1
        return f"2026-09-21T00:00:{counter['n']:02d}+00:00"

    return _tick


class Test预检:
    def test_输入不足即拒(self, pilot_demo_config_path, pilot_dirs):
        with pytest.raises(PrecheckError):
            precheck(
                form=FORM,
                config_path=pilot_demo_config_path,
                inputs=PilotInputs(topic="", target_duration_min=0, characters=()),
                data_dir=pilot_dirs,
            )

    def test_配置缺项即拒且零落树(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        broken = tmp_path / "broken.yaml"
        payload = pilot_demo_config_path.read_text(encoding="utf-8").replace(
            "editing:\n  exploration_per_round_usd: 50", "editing:\n  exploration_removed: 50"
        )
        broken.write_text(payload, encoding="utf-8")
        before = sorted(path.name for path in pilot_dirs.rglob("*"))
        with pytest.raises(PrecheckError) as excinfo:
            precheck(form=FORM, config_path=broken, inputs=_inputs(), data_dir=pilot_dirs)
        assert "配置" in str(excinfo.value) or "editing" in str(excinfo.value)
        assert sorted(path.name for path in pilot_dirs.rglob("*")) == before  # 零落树

    def test_合格输入返回预检结论(self, pilot_demo_config_path, pilot_dirs):
        report = precheck(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
        )
        assert report["form"] == FORM
        assert report["loaders"]  # 全部加载器逐个通过
        assert report["config_fingerprint"]


class Test一次运行:
    def test_六阶段全_done_且产出样片包(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        result = run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
            run_id="run-a",
            clock=_fixed_clock(),
        )
        assert result.record.status.value == "done"
        assert result.record.completed_stages == (
            "script",
            "storyboard",
            "visual",
            "sound",
            "editing",
            "promo",
        )
        assert result.package_dir is not None
        for name in ("manifest.json", "reel.mp4", "products.json", "cost.json", "state.json"):
            assert (result.package_dir / name).is_file()
        # 运行记录落盘（续跑依据）
        run_file = pilot_dirs / "runs" / "run-a.json"
        assert run_file.is_file()
        assert json.loads(run_file.read_text(encoding="utf-8"))["run_id"] == "run-a"

    def test_两次运行逐字节一致(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        # 同一 run（同输入同配置，独立工件根与运行目录）执行两次 → 产物逐字节一致
        first = run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=tmp_path / "first" / "pilot",
            artifacts_root=tmp_path / "first" / "artifacts",
            run_id="run-1",
            clock=_fixed_clock(),
        )
        second = run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=tmp_path / "second" / "pilot",
            artifacts_root=tmp_path / "second" / "artifacts",
            run_id="run-1",
            clock=_fixed_clock(),
        )
        for name in ("manifest.json", "reel.mp4", "products.json", "cost.json", "state.json"):
            assert (first.package_dir / name).read_bytes() == (
                second.package_dir / name
            ).read_bytes(), name


class Test续跑:
    def test_续跑不重跑已完成阶段(self, pilot_demo_config_path, pilot_dirs, tmp_path, monkeypatch):
        run_id = "run-resume"
        result = run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
            clock=_fixed_clock(),
        )
        assert result.record.status.value == "done"
        resumed = resume_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
            clock=_fixed_clock(),
        )
        # 幂等：全部完成后续跑原样返回，零重跑（attempts 不增）
        assert resumed.record == result.record
        assert all(state.attempts == 1 for state in resumed.record.stages)

    def test_输入变更拒绝续跑(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        run_id = "run-reject"
        run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
            clock=_fixed_clock(),
        )
        changed = PilotInputs(
            topic="换一个题材",
            target_duration_min=2,
            characters=("林静", "陈默"),
            constraints=(),
        )
        with pytest.raises(OrchestrationError):
            resume_pilot(
                form=FORM,
                config_path=pilot_demo_config_path,
                inputs=changed,
                data_dir=pilot_dirs,
                artifacts_root=tmp_path / "artifacts",
                run_id=run_id,
                clock=_fixed_clock(),
            )

    def test_配置变更拒绝续跑(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        run_id = "run-reject-cfg"
        run_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
            run_id=run_id,
            clock=_fixed_clock(),
        )
        other = tmp_path / "configs" / "other.yaml"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(
            pilot_demo_config_path.read_text(encoding="utf-8").replace(
                "exploration_per_round_usd: 50", "exploration_per_round_usd: 55"
            ),
            encoding="utf-8",
        )
        with pytest.raises(OrchestrationError):
            resume_pilot(
                form=FORM,
                config_path=other,
                inputs=_inputs(),
                data_dir=pilot_dirs,
                artifacts_root=tmp_path / "artifacts",
                run_id=run_id,
                clock=_fixed_clock(),
            )


def test_形态值随运行记录透传(pilot_demo_config_path, pilot_dirs, tmp_path):
    result = run_pilot(
        form=FORM,
        config_path=pilot_demo_config_path,
        inputs=_inputs(),
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
        run_id="run-form",
        clock=_fixed_clock(),
    )
    assert result.record.form == FORM
    assert Path(result.package_dir).is_dir()
