"""声音 Agent 的绑定薄工厂与槽位布局（021 C1/C3；**单槽 `all`**）。

sound 的装配函数返回**扁平列表**，故其唯一可声明槽位名为 `all`（= 列表本体）——与五个
dict Agent 的派生汇总键 `all` 同名不同物，判定由传入的 `slots` 入参区分、**无人工特例分支**。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——单一
事实源仍是 `SoundConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
本模块的模块级公开函数**必须**全部被 `configs/*.yaml` 的 `impl` 以
`agents.sound.evaluators.plugins:<attr>` 引用（孤立插件即不可用）。
"""

from agents.sound.config import SoundConfig
from agents.sound.evaluators.asr import AsrTranscriptEvaluator
from agents.sound.evaluators.av_sync import AvSyncEvaluator
from agents.sound.evaluators.emotion import EmotionMusicMatchEvaluator
from agents.sound.evaluators.loudness import LoudnessComplianceEvaluator
from core.evaluators.base import Evaluator

AGENT = "sound"
SLOT_LAYOUT = ("all",)
SINGLE_EVALUATOR_SLOTS = ()


def loudness_compliance(*, agent_config: SoundConfig) -> Evaluator:
    return LoudnessComplianceEvaluator(agent_config.loudness)


def av_sync(*, agent_config: SoundConfig) -> Evaluator:
    return AvSyncEvaluator(agent_config.av_sync_threshold_ms)


def asr_transcript(*, agent_config: SoundConfig) -> Evaluator:
    return AsrTranscriptEvaluator(agent_config.asr["cer_cap"])


def emotion_music_match(*, agent_config: SoundConfig) -> Evaluator:
    return EmotionMusicMatchEvaluator(agent_config.emotion["calibration_band"])


def _to_return_shape(assembled: dict) -> dict:
    """把装配点的槽位映射（每槽位 `list`）还原为该 Agent **既有返回形状**：单评估器槽位取
    首元素，列表槽位（含派生的 `all`）原样——签名与返回形状逐字不变是 021 C2 的兼容承诺。

    **零实例（= 声明面未声明的槽位）不产出该键**：返回形状**如实反映已声明集合**，下游据此
    把"该槽位未声明"当**明确语义**处理（跳过依赖该槽位的工作并如实标注，或显式报错）——
    不猜、不补兜底默认。既有两形态全槽位声明 ⇒ 返回形状逐字不变（021 T2111 基线）。
    """
    return {
        slot: (instances[0] if slot in SINGLE_EVALUATOR_SLOTS else instances)
        for slot, instances in assembled.items()
        if instances
    }
