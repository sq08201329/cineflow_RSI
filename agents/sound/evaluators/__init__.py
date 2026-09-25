"""声音四评估器包（US2）：双 gate（loudness/av_sync）+ 双 proxy（asr/emotion）。

build_sound_evaluators 为**唯一装配点**的委派入口（loop 接线 / 契约与无偏性测试共用）：
评估器集合与参数**完全由 `configs/*.yaml` 的 `evaluators.plugins.sound` 声明驱动**
（021 C1/C2；单槽 `all` ⇒ 返回扁平列表）；集合与 `evaluator_weights.sound` 权重键一一对应，
既有参数由 `SoundConfig` 的 dataclass 字段经 `agent_config` 槽位读取（原则五：分档/阈值/
cer_cap/校准带零硬编码）。
"""

from agents.sound.config import SoundConfig
from agents.sound.evaluators.asr import AsrTranscriptEvaluator
from agents.sound.evaluators.av_sync import AvSyncEvaluator
from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator
from agents.sound.evaluators.loudness import LoudnessComplianceEvaluator
from agents.sound.evaluators.plugins import AGENT, SLOT_LAYOUT
from core.evaluators.base import Evaluator
from core.evaluators.plugin import assemble, parse_manifest

__all__ = [
    "AsrTranscriptEvaluator",
    "AvSyncEvaluator",
    "EmotionMusicMatchEvaluator",
    "LoudnessComplianceEvaluator",
    "build_sound_evaluators",
]


def build_sound_evaluators(config: SoundConfig) -> list[Evaluator]:
    """按 `evaluators.plugins.sound` 声明装配真实四评估器（双 gate + 双 proxy）。

    返回**扁平列表**且下标顺序 == 声明顺序（单槽 `all` 即列表本体）。
    """
    manifest = parse_manifest(config.plugin_declarations, AGENT, slots=SLOT_LAYOUT)
    return assemble(manifest, agent_config=config)["all"]
