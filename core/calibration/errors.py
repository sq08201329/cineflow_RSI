"""校准子包的错误类型体系（风格对齐 core/evaluators/errors.py）。

领域模型构造校验复用 core/evaluators/errors.py 的 ValidationError
（core 内错误语义一致）；此处仅承载校准特有的配置错误。
"""


class CalibrationError(Exception):
    """校准子包全部错误的基类。"""


class CalibrationConfigError(CalibrationError):
    """形态配置 calibration 段读取失败：文件缺失、缺段、缺字段、非法值。"""


class DriftOutOfScopeError(CalibrationError):
    """检测范围外：评估器类别不在 calibration.drift.scope_kinds（显式拒绝，非静默跳过）。"""


class DriftRecordConflictError(CalibrationError):
    """漂移记录已存在且内容不同：只增不改（历史判定不回溯改写）。"""
