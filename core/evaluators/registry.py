"""评估器注册中心（契约 §2，宪章原则一的工程落点）。

- 键为 evaluator_id@version，全局唯一；重复注册拒绝并附冲突键；
- 非确定性评估器禁止进入回放打分路径（人类校准锚点例外）；
- get 未命中时报错消息附可用版本列表。
"""

from core.evaluators.base import Evaluator, EvaluatorKind, EvaluatorSpec
from core.evaluators.errors import RegistrationError


class Registry:
    """评估器全局唯一命名空间。"""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, ev: Evaluator) -> None:
        spec = ev.spec
        # 缺失必填字段（evaluator_id / version / kind）——防御绕过模型层校验的实现
        if not getattr(spec, "evaluator_id", None):
            raise RegistrationError("spec.evaluator_id 缺失或为空")
        if not getattr(spec, "version", None):
            raise RegistrationError(f"{spec.evaluator_id}: spec.version 缺失或为空")
        if getattr(spec, "kind", None) is None:
            raise RegistrationError(f"{spec.key}: spec.kind 缺失")

        # 确定性校验：非确定性评估器禁止进入回放打分路径，人类锚点例外（宪章原则一）
        if not spec.deterministic and spec.kind is not EvaluatorKind.HUMAN:
            raise RegistrationError(
                f"{spec.key}: 非确定性评估器（deterministic=False）禁止注册，"
                "仅人类锚点（kind=human）例外；行为变更请升版本号并保证确定性"
            )

        if spec.key in self._evaluators:
            raise RegistrationError(
                f"重复注册被拒绝：{spec.key} 已存在；行为变更必须升版本号（宪章原则一）"
            )
        self._evaluators[spec.key] = ev

    def get(self, evaluator_id: str, version: str) -> Evaluator:
        key = f"{evaluator_id}@{version}"
        try:
            return self._evaluators[key]
        except KeyError:
            available = sorted(
                spec.version for spec in self.list_all() if spec.evaluator_id == evaluator_id
            )
            detail = f"可用版本：{', '.join(available)}" if available else "该 evaluator_id 未注册"
            raise RegistrationError(f"未找到评估器 {key}（{detail}）") from None

    def list_all(self) -> list[EvaluatorSpec]:
        return [ev.spec for ev in self._evaluators.values()]


_default_registry = Registry()


def register(ev: Evaluator) -> None:
    """注册到模块级默认注册中心（契约形态）。"""
    _default_registry.register(ev)


def get(evaluator_id: str, version: str) -> Evaluator:
    """从模块级默认注册中心取回评估器。"""
    return _default_registry.get(evaluator_id, version)


def list_all() -> list[EvaluatorSpec]:
    """列出模块级默认注册中心的全部 spec。"""
    return _default_registry.list_all()


def reset() -> None:
    """清空模块级默认注册中心（仅测试用于隔离）。"""
    global _default_registry
    _default_registry = Registry()
