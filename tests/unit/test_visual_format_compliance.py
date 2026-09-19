"""格式合规评估器单测（US1 / T310，rule.format_compliance@1.0.0）。

规格合规/违规逐维断言；ffmpeg 不可用/无法解码 → 受控报错（节点 FAILED 的触发源）。
"""

import pytest

from agents.visual.evaluators.format_compliance import FormatComplianceEvaluator
from agents.visual.frames import FrameDecodeError
from core.evaluators.base import ArtifactRef, EvaluatorKind


@pytest.fixture()
def evaluator(visual_config):
    return FormatComplianceEvaluator(visual_config.clip_spec)


def _meta(**overrides):
    meta = {"width": 320, "height": 240, "fps": 8.0, "duration_seconds": 2.0, "codec": "h264"}
    meta.update(overrides)
    return meta


class Test注册元数据:
    def test_spec(self, evaluator):
        assert evaluator.spec.key == "rule.format_compliance@1.0.0"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True


class Test合规判定:
    def test_合规片段满分(self, evaluator):
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"probe_meta": _meta()})
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []

    @pytest.mark.parametrize(
        "override, keyword",
        [
            ({"width": 640}, "分辨率"),
            ({"height": 480}, "分辨率"),
            ({"fps": 30.0}, "帧率"),
            ({"duration_seconds": 5.0}, "时长"),
            ({"codec": "vp9"}, "编码"),
        ],
    )
    def test_违规逐维拦截(self, evaluator, override, keyword):
        result = evaluator.evaluate(
            ArtifactRef(artifact_hash="ab" * 32), {"probe_meta": _meta(**override)}
        )
        assert result.score == 0.0
        assert any(keyword in v for v in result.diagnostics["violations"])

    def test_确定性(self, evaluator):
        ctx = {"probe_meta": _meta()}
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        assert evaluator.evaluate(artifact, ctx) == evaluator.evaluate(artifact, ctx)


class Test受控报错:
    def test_探测失败报错不崩溃(self, evaluator):
        """ffmpeg 不可用/片段无法解码：评估器报错（闭环记 FAILED），不崩溃。"""
        with pytest.raises(FrameDecodeError):
            evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"probe_meta": None})
