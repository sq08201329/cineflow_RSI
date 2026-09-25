"""开发 Agent 的绑定薄工厂与槽位布局（021 C1/C3）。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——
单一事实源仍是 `DevConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
对象型参数（`SimulatedSignalSource`，先例 `agents/dev/evaluators/__init__.py`）在工厂内经
`agent_config` 构造；`registry` 槽位**不在**本 Agent 的声明槽位面（由
`build_dev_evaluators` 的关键字符参承担，装配点在提供时逐实例注册）。

`SLOT_LAYOUT` 是槽位名的**唯一映射声明**（dev 无 judge ⇒ 两槽位）；`all` 是装配点按本顺序
拼接的派生汇总键，不可声明。本模块的模块级公开函数**必须**全部被 `configs/*.yaml` 的
`impl` 以 `agents.dev.evaluators.plugins:<attr>` 引用（孤立插件即不可用）。
"""

from agents.dev.config import DevConfig
from agents.dev.evaluators.buzz_heat import BuzzHeatEvaluator
from agents.dev.evaluators.genre_regression import GenreRegressionEvaluator
from agents.dev.evaluators.slate_combination import SlateCombinationEvaluator
from agents.dev.evaluators.slate_structure import SlateStructureEvaluator
from agents.dev.signals import SOURCE_BOX_OFFICE, SOURCE_BUZZ, SimulatedSignalSource
from core.evaluators.base import Evaluator

AGENT = "dev"
SLOT_LAYOUT = ("gates", "proxies")
SINGLE_EVALUATOR_SLOTS = ()


def slate_structure(*, agent_config: DevConfig) -> Evaluator:
    return SlateStructureEvaluator(slate_entries=agent_config.slate_entries)


def slate_combination(*, agent_config: DevConfig) -> Evaluator:
    return SlateCombinationEvaluator(
        slate_entries=agent_config.slate_entries,
        production_marks=agent_config.production_marks,
        max_direction_repeat_rate=agent_config.max_direction_repeat_rate,
    )


def genre_regression(*, agent_config: DevConfig) -> Evaluator:
    return GenreRegressionEvaluator(SimulatedSignalSource(SOURCE_BOX_OFFICE, agent_config.signals))


def buzz_heat(*, agent_config: DevConfig) -> Evaluator:
    return BuzzHeatEvaluator(SimulatedSignalSource(SOURCE_BUZZ, agent_config.signals))


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
