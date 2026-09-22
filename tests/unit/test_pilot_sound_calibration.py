"""响度标定的后端纪律单测（1.4.0 修复既有遗留）。

背景：`agents.pilot.stages.build_sound_plans` 经 `_calibrated_params` 用**模拟**合成器
合成一遍波形来测响度、反推增益——切 `sound: http` 后该口径不成立。本文件钉死两条：

1. 后端为 http → **如实拒绝**（`StageFailedError`，文案写明原因与替代做法），
   不用模拟响度冒充真实平台产出；
2. 后端为 simulated（默认）→ 行为不变（每句台词一 TTS + 一轨配乐，带 loudness_gain_db）。
"""

from types import SimpleNamespace

import pytest

from agents.pilot.stages import build_sound_plans
from agents.sound.config import SoundConfig
from core.orchestration.errors import StageFailedError

REPO_ROOT_CONFIG = "configs/movie.yaml"


@pytest.fixture()
def sound_config():
    """声音形态配置：直接读 configs/movie.yaml 的 sound 段。"""
    from pathlib import Path

    return SoundConfig.from_yaml(Path(__file__).resolve().parents[2] / REPO_ROOT_CONFIG)


def _runtime(sound_config, *, backend: str) -> SimpleNamespace:
    """最小运行时视图：`build_sound_plans` 只读 configs.sound 与 backends.resolved。"""
    return SimpleNamespace(
        configs=SimpleNamespace(sound=sound_config),
        backends=SimpleNamespace(resolved={"sound": backend}),
    )


def test_真实后端_如实拒绝响度标定并写明替代做法(sound_config):
    timing_sheet = SimpleNamespace(utterances=())
    with pytest.raises(StageFailedError) as excinfo:
        build_sound_plans(_runtime(sound_config, backend="http"), timing_sheet)
    message = str(excinfo.value)
    assert "响度标定不支持真实后端" in message
    assert "sound 后端 = http" in message
    assert "替代做法" in message  # 报错必须给出替代做法（不只是一句"不支持"）
    assert "零生成零扣费" in message


def test_模拟后端_标定行为不变(sound_config, make_timing_sheet):
    from agents.sound.audio import decode_wav_samples, synthesize_wav
    from agents.sound.evaluators.loudness import measure_loudness_lufs

    timing_sheet = make_timing_sheet()
    plans = build_sound_plans(_runtime(sound_config, backend="simulated"), timing_sheet)

    assert len(plans) == len(timing_sheet.utterances) + 1  # 每句台词一 TTS + 一轨配乐
    assert [plan["gen_type"] for plan in plans][-1] == "music"
    for plan in plans:
        params = plan["gen_params"]
        tier = "music" if plan["gen_type"] == "music" else "dialogue"
        target = float(sound_config.loudness[tier]["target_lufs"])
        # 标定口径未变：把反推出的增益施加到波上，实测响度 == 该档目标（gate 可过的依据）
        wav, _ = synthesize_wav(params, sound_config.simulated_gen, sound_config.sample_rate)
        measured = measure_loudness_lufs(decode_wav_samples(wav), sound_config.sample_rate)
        assert measured == pytest.approx(target, abs=0.1)
