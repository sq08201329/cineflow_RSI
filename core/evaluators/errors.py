"""评估器框架的错误类型体系。

契约见 specs/001-tree-evaluators/contracts/evaluator-registry.md。
与 core/tree 相互独立（core 内各模块不互相耦合）。
"""


class EvaluatorError(Exception):
    """评估器框架全部错误的基类。"""


class ValidationError(EvaluatorError):
    """评估器侧领域模型构造校验失败（必填字段、score 越界、哈希格式等）。"""


class RegistrationError(EvaluatorError):
    """注册中心拒绝：重复键、非确定性（非人类锚点）、缺字段、get 未命中。"""


class WeightMismatchError(EvaluatorError):
    """合成评分时 breakdown 与 weights 键集合不一致（拒绝静默按部分权重计算）。"""


class WeightConfigError(EvaluatorError):
    """形态配置权重读取失败：文件缺失、缺键、非法权重值。"""


class PluginDeclarationError(EvaluatorError):
    """插件声明面非法（021 C1/C3）：缺 `evaluators`/`plugins`/本 Agent 子键、槽位表外、
    叶子键缺或多、`impl` 非字符串或 `:` 数不为 1、`version` 非字符串或空串、`params` 非映射。

    文案一律点名声明路径 `evaluators.plugins.<agent>.<slot>.<evaluator_id>.<leaf>` 与实测值，
    禁止无定位信息的"插件装配失败"。
    """


class PluginAssemblyError(EvaluatorError):
    """插件装配期非法（021 C2/C4）：`impl` 不可解析/非 callable、参数严格性不符、
    `evaluator_id`/`kind` 前缀/`version` 三项一致性不符、注入槽位越界或取值为 `None`、
    装配集合与 `evaluator_weights.<agent>` 键集不匹配、产出非 `EvaluatorSpec` 实例。

    文案一律点名声明路径并给出**两侧实测值**（不静默取任一侧）。
    """
