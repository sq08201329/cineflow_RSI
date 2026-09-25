"""开发 Agent 四评估器包（US2）：两硬规则门禁 + 两确定性代理。

组合恒为 **2 gate + 2 代理**——**无 judge、无人类锚点**（澄清第 1 条；本环节证据来源缺失
须如实登记，不得以伪信号补齐）。`build_dev_evaluators` 为**唯一装配点**的委派入口
（loop 接线 / CLI / 契约与无偏性测试共用）：评估器集合与参数**完全由 `configs/*.yaml` 的
`evaluators.plugins.dev` 声明驱动**（021 C1/C2）；装配得到的集合与 `evaluator_weights.dev`
权重键一一对应（缺项/多项即拒绝装配，防配置漂移）；既有参数由 `DevConfig` 的 dataclass
字段经 `agent_config` 槽位读取（条目数区间 / 标记区间 / 重复率上限 / `dev.signals` 模拟
数据源参数，零硬编码，原则五）。
"""

from agents.dev.config import DevConfig
from agents.dev.evaluators.buzz_heat import BuzzHeatEvaluator
from agents.dev.evaluators.composite import COMPOSITE_POLICY, composite_dev, evaluate_dev
from agents.dev.evaluators.genre_regression import GenreRegressionEvaluator
from agents.dev.evaluators.plugins import AGENT, SLOT_LAYOUT
from agents.dev.evaluators.slate_combination import SlateCombinationEvaluator
from agents.dev.evaluators.slate_structure import SlateStructureEvaluator
from core.evaluators.plugin import assemble, parse_manifest
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
    """按 `evaluators.plugins.dev` 声明装配真实四评估器（两门禁 + 两代理）。

    返回 {"gates", "proxies", "all"}——门禁先行评估，任一判 0 短路不跑代理（合成编排见
    composite.py）。`registry` 注入用于按 `id@version` 取回冻结实例的回放路径：同一注册
    中心内重复注册同键即被拒（原则一）；不传则不注册（每次装配得到独立实例）。
    `registry` **不在**声明槽位面内（由本关键字符参承担），装配点在提供时逐实例注册。
    评估器集合与 `evaluator_weights.dev` 键集必须一一对应（缺项/多项即拒绝装配）。
    """
    manifest = parse_manifest(config.plugin_declarations, AGENT, slots=SLOT_LAYOUT)
    return assemble(manifest, agent_config=config, registry=registry)
