"""开发 Agent 四评估器包（US2）：两硬规则门禁 + 两确定性代理。

组合恒为 **2 gate + 2 代理**——**无 judge、无人类锚点**（澄清第 1 条；本环节证据来源缺失
须如实登记，不得以伪信号补齐）。`build_dev_evaluators` 为**唯一装配点**（loop 接线 / CLI /
契约与无偏性测试共用）：评估器集合与 `evaluator_weights.dev` 权重键一一对应（缺项/多项即
拒绝装配，防配置漂移）；参数全来自 DevConfig（条目数区间 / 标记区间 / 重复率上限 /
`dev.signals` 模拟数据源参数，零硬编码，原则五）。
"""

from agents.dev.config import DevConfig, DevConfigError
from agents.dev.evaluators.buzz_heat import BuzzHeatEvaluator
from agents.dev.evaluators.composite import COMPOSITE_POLICY, composite_dev, evaluate_dev
from agents.dev.evaluators.genre_regression import GenreRegressionEvaluator
from agents.dev.evaluators.slate_combination import SlateCombinationEvaluator
from agents.dev.evaluators.slate_structure import SlateStructureEvaluator
from agents.dev.signals import SOURCE_BOX_OFFICE, SOURCE_BUZZ, SimulatedSignalSource
from core.evaluators.registry import Registry

__all__ = [
    "BuzzHeatEvaluator",
    "GenreRegressionEvaluator",
    "SlateCombinationEvaluator",
    "SlateStructureEvaluator",
    "COMPOSITE_POLICY",
    "build_dev_evaluators",
    "composite_dev",
    "evaluate_dev",
]


def build_dev_evaluators(config: DevConfig, *, registry: Registry | None = None) -> dict:
    """按 `evaluator_weights.dev` 装配真实四评估器（两门禁 + 两代理）。

    返回 {"gates", "proxies", "all"}——门禁先行评估，任一判 0 短路不跑代理（合成编排见
    composite.py）。`registry` 注入用于按 `id@version` 取回冻结实例的回放路径：同一注册
    中心内重复注册同键即被拒（原则一）；不传则不注册（每次装配得到独立实例）。
    """
    gates = [
        SlateStructureEvaluator(slate_entries=config.slate_entries),
        SlateCombinationEvaluator(
            slate_entries=config.slate_entries,
            production_marks=config.production_marks,
            max_direction_repeat_rate=config.max_direction_repeat_rate,
        ),
    ]
    proxies = [
        GenreRegressionEvaluator(SimulatedSignalSource(SOURCE_BOX_OFFICE, config.signals)),
        BuzzHeatEvaluator(SimulatedSignalSource(SOURCE_BUZZ, config.signals)),
    ]
    all_evaluators = [*gates, *proxies]
    # 权重节与评估器集合必须一一对应（缺项/多项即拒绝装配——配置漂移不得静默）
    assembled = {evaluator.spec.evaluator_id for evaluator in all_evaluators}
    weighted = set(config.evaluator_weights)
    if assembled != weighted:
        raise DevConfigError(
            "evaluator_weights.dev 与装配的评估器不一致："
            f"缺权重键 {sorted(assembled - weighted)}、多余权重键 {sorted(weighted - assembled)}"
        )
    if registry is not None:
        for evaluator in all_evaluators:
            registry.register(evaluator)
    return {"gates": gates, "proxies": proxies, "all": all_evaluators}
