"""评估器基类与领域模型（frozen dataclass，契约 §1）。

- EvaluatorSpec：evaluator_id / version / kind / deterministic / cost_per_call / calibration，
  以 evaluator_id@version 全局唯一标识（宪章原则一，版本冻结）；
- EvalResult：score ∈ [0,1] + 人可读 diagnostics（随节点 eval_breakdown 落盘）；
- ArtifactRef：内容寻址引用（哈希 + 可选元信息），评估器不直接接触对象存储客户端；
- Evaluator 抽象基类：compare 仅 judge 类实现，默认 NotImplementedError（FR-013）。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

from core.evaluators.errors import ValidationError

_ARTIFACT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class EvaluatorKind(StrEnum):
    """评估器四类：硬规则 / 代理模型 / judge / 人类锚点（FR-015）。"""

    RULE = "rule"
    PROXY_MODEL = "proxy_model"
    JUDGE = "judge"
    HUMAN = "human"


@dataclass(frozen=True)
class ArtifactRef:
    """工件的内容寻址引用：哈希 + 可选元信息（评估器拿不到存储凭证）。"""

    artifact_hash: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _ARTIFACT_HASH_RE.match(self.artifact_hash):
            raise ValidationError("artifact_hash 必须为 64 位小写十六进制（BLAKE3）")


@dataclass(frozen=True)
class EvaluatorSpec:
    """评估器注册元数据：一经注册即冻结，行为变更必须升版本号。"""

    evaluator_id: str
    version: str
    kind: EvaluatorKind
    deterministic: bool
    cost_per_call: float = 0.0
    calibration: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.evaluator_id, str) or not self.evaluator_id:
            raise ValidationError("evaluator_id 必须为非空字符串")
        if not isinstance(self.version, str) or not self.version:
            raise ValidationError("version 必须为非空字符串")
        if not isinstance(self.kind, EvaluatorKind):
            try:
                object.__setattr__(self, "kind", EvaluatorKind(self.kind))
            except ValueError as exc:
                raise ValidationError(f"kind 必须为 {list(EvaluatorKind)} 之一") from exc
        if (
            not isinstance(self.cost_per_call, (int, float))
            or isinstance(self.cost_per_call, bool)
            or self.cost_per_call < 0
        ):
            raise ValidationError(f"cost_per_call 必须为 ≥ 0 的数值，实际为 {self.cost_per_call!r}")

    @property
    def key(self) -> str:
        """全局唯一键：evaluator_id@version。"""
        return f"{self.evaluator_id}@{self.version}"


@dataclass(frozen=True)
class EvalResult:
    """单次评估产出：归一化得分 + 人可读诊断明细。"""

    score: float
    diagnostics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not 0.0 <= self.score <= 1.0
        ):
            raise ValidationError(f"score 必须 ∈ [0,1]，实际为 {self.score!r}")


class Evaluator(ABC):
    """评估器抽象基类：实现方必须提供 spec 与 evaluate。"""

    spec: EvaluatorSpec

    @abstractmethod
    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        """对工件评估，返回 score ∈ [0,1] 与人可读 diagnostics。"""
        ...

    def compare(self, a: ArtifactRef, b: ArtifactRef, context: dict) -> float:
        """仅 judge 类实现：返回 a 的胜率 ∈ [0,1]。默认不可用。"""
        raise NotImplementedError("compare 仅 judge 类评估器实现")
