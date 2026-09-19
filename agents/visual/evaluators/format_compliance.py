"""格式合规评估器：rule.format_compliance@1.0.0（硬规则门禁）。

ffprobe 实测元数据对照 configs 片段规格（分辨率/帧率/时长/编码）；
probe_meta 为 None（ffmpeg 不可用/无法解码）→ FrameDecodeError 受控报错
（闭环记 FAILED，不崩溃）。
"""

from agents.visual.frames import FrameDecodeError
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "rule.format_compliance"
VERSION = "1.0.0"


class FormatComplianceEvaluator(Evaluator):
    """片段规格合规硬规则（确定性、零成本）。"""

    def __init__(self, clip_spec: dict) -> None:
        self._spec = clip_spec
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=VERSION,
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        meta = context.get("probe_meta")
        if meta is None:
            raise FrameDecodeError("片段元数据缺失（ffmpeg 不可用/无法解码）")
        violations: list[str] = []
        if (meta["width"], meta["height"]) != (self._spec["width"], self._spec["height"]):
            violations.append(
                f"分辨率不符：{meta['width']}x{meta['height']} ≠ "
                f"{self._spec['width']}x{self._spec['height']}"
            )
        if abs(meta["fps"] - float(self._spec["fps"])) > 1e-6:
            violations.append(f"帧率不符：{meta['fps']} ≠ {self._spec['fps']}")
        if abs(meta["duration_seconds"] - float(self._spec["duration_seconds"])) > 0.5:
            violations.append(
                f"时长不符：{meta['duration_seconds']}s ≠ {self._spec['duration_seconds']}s"
            )
        if meta["codec"] != self._spec["codec"]:
            violations.append(f"编码不符：{meta['codec']} ≠ {self._spec['codec']}")
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={"violations": violations, "probe_meta": meta},
        )
