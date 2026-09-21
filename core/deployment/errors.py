"""部署子包错误类型（风格对齐 core/calibration/errors.py）。

模型字段校验复用 `core.evaluators.errors.ValidationError`（core 内错误语义一致）；
此处只承载部署特有的错误：配置读取、模式迁移拒绝、留痕冲突。
"""


class DeploymentError(Exception):
    """部署子包全部错误的基类。"""


class DeploymentConfigError(DeploymentError):
    """deployment 段配置读取失败：文件缺失、缺段、缺字段、非法值。"""


class ModeTransitionError(DeploymentError):
    """部署模式迁移被拒（非法迁移 / 影子期未满 / 重标定期间禁开 auto）。"""


class DeploymentRecordConflictError(DeploymentError):
    """部署留痕已存在且内容不同：只增不改（历史不回溯改写）。"""


class PointerMismatchError(DeploymentError):
    """部署指针与留痕不一致（防外部绕过）：拒绝部署并落告警，人工核对后才可继续。"""


class RollbackTargetMissingError(DeploymentError):
    """回滚目标版本工件缺失：显式报错并保持人工审批（绝不停留在不确定状态）。"""


class DeploymentEvidenceError(DeploymentError):
    """证据非法（payload 形态/取值不符合口径）——与"证据缺失"区分：前者报错，后者 missing。"""
