"""TimingSheet 时序元数据模型与校验（澄清 Q1：同步输入仅时序元数据，不碰视觉工件）。

utterances [{text, start_ms, end_ms}] + effects [{kind, at_ms}]；
校验：时间戳非负整数、start_ms < end_ms、台词区间 [start, end) 不重叠——
上游输入错误在执行前拒绝（C1 场景 5：适配器 0 调用、0 成本）。
ValidationError 风格对齐 core（core.tree.errors，同 core/tree/models.py 域模型用法）。
"""

from dataclasses import dataclass
from typing import Any

from core.tree.errors import ValidationError


def _require_int_ms(value: Any, field_name: str) -> int:
    """毫秒时间戳必须为非负整数。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} 必须为整数毫秒时间戳，实际为 {value!r}")
    if value < 0:
        raise ValidationError(f"{field_name} 必须非负，实际为 {value!r}")
    return value


@dataclass(frozen=True)
class Utterance:
    """台词片段：区间语义 [start_ms, end_ms)。"""

    text: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text:
            raise ValidationError(f"utterance.text 必须为非空字符串，实际为 {self.text!r}")
        _require_int_ms(self.start_ms, "utterance.start_ms")
        _require_int_ms(self.end_ms, "utterance.end_ms")
        if not self.start_ms < self.end_ms:
            raise ValidationError(
                f"utterance 要求 start_ms < end_ms，实际为 {self.start_ms} >= {self.end_ms}"
            )


@dataclass(frozen=True)
class Effect:
    """音效事件：单点时间戳。"""

    kind: str
    at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValidationError(f"effect.kind 必须为非空字符串，实际为 {self.kind!r}")
        _require_int_ms(self.at_ms, "effect.at_ms")


def _normalize_utterance(item: Utterance | dict) -> Utterance:
    if isinstance(item, Utterance):
        return item
    if isinstance(item, dict):
        return Utterance(
            text=item.get("text"),
            start_ms=item.get("start_ms"),
            end_ms=item.get("end_ms"),
        )
    raise ValidationError(f"utterance 必须为 dict 或 Utterance，实际为 {item!r}")


def _normalize_effect(item: Effect | dict) -> Effect:
    if isinstance(item, Effect):
        return item
    if isinstance(item, dict):
        return Effect(kind=item.get("kind"), at_ms=item.get("at_ms"))
    raise ValidationError(f"effect 必须为 dict 或 Effect，实际为 {item!r}")


@dataclass(frozen=True)
class TimingSheet:
    """时序元数据：台词清单 + 音效事件清单（构造即校验，非法输入拒绝）。"""

    utterances: list[Utterance | dict]
    effects: list[Effect | dict]

    def __post_init__(self) -> None:
        utterances = sorted(
            (_normalize_utterance(u) for u in self.utterances),
            key=lambda u: (u.start_ms, u.end_ms),
        )
        # 区间语义 [start, end)：后条 start < 前条 end 即重叠（贴边合法）
        for prev, curr in zip(utterances, utterances[1:], strict=False):
            if curr.start_ms < prev.end_ms:
                raise ValidationError(
                    f"utterances 时间区间重叠：[{prev.start_ms}, {prev.end_ms}) 与 "
                    f"[{curr.start_ms}, {curr.end_ms})"
                )
        object.__setattr__(self, "utterances", utterances)
        object.__setattr__(self, "effects", [_normalize_effect(e) for e in self.effects])
