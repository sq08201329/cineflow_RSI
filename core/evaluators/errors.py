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
