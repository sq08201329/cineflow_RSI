"""开发 Agent 二门禁评估器单测（功能 017 / US2 / T1726，先于实现编写）。

契约 C7/C8：
- `rule.slate_structure`：组合条目数 ∈ 配置区间、方向标识组合内唯一、必填要点（genre/
  constraints/characters）齐全；任一违规 → **判 0 且诊断点名具体违规项**（越界实测条目数
  与区间 / 重复的方向标识 / 缺失字段名）；
- `rule.slate_combination`（组合层核心门禁）：方向重复率 ≤ 配置上限、条目数 ≤ 配置上限、
  进入生产标记数 ∈ 配置区间且每个标记指向组合内已存在的条目；0 标记与区间下界冲突时
  **以区间为准**（判 0 并如实记录，C5/C8）；
- 失败语义：`EvalResult(score=0.0, diagnostics.violations)` 返回、**不抛异常**（先例
  `agents/screenplay/evaluators/beat_structure.py:84`）；阈值全配置化，缺项即装配期报错。

真实配置下两门禁必须对**同一份注入缺陷工件**给出可复现的判 0（SC-003）。
"""

import pytest

from agents.dev.config import DevConfigError
from agents.dev.evaluators.slate_combination import SlateCombinationEvaluator
from agents.dev.evaluators.slate_structure import SlateStructureEvaluator
from core.evaluators.base import ArtifactRef, EvalResult, EvaluatorKind

# 结构门禁的违规注入面（conftest 的 make_topic_slate 变体）
_STRUCTURE_DEFECTS = ("duplicate_direction", "missing_essentials", "count_out_of_range")
# 组合门禁的违规注入面（标记区间/指向 + 重复率超限由参数注入）
_COMBINATION_DEFECTS = ("marks_out_of_range", "dangling_mark")


def _artifact_ref() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


def _evaluate(evaluator, slate) -> EvalResult:
    return evaluator.evaluate(_artifact_ref(), {"artifact": slate})


def _structure_gate(dev_config) -> SlateStructureEvaluator:
    return SlateStructureEvaluator(slate_entries=dev_config.slate_entries)


def _combination_gate(
    dev_config,
    *,
    slate_entries=None,
    production_marks=None,
    max_direction_repeat_rate=None,
) -> SlateCombinationEvaluator:
    return SlateCombinationEvaluator(
        slate_entries=dev_config.slate_entries if slate_entries is None else slate_entries,
        production_marks=(
            dev_config.production_marks if production_marks is None else production_marks
        ),
        max_direction_repeat_rate=(
            dev_config.max_direction_repeat_rate
            if max_direction_repeat_rate is None
            else max_direction_repeat_rate
        ),
    )


class Test结构门禁:
    def test_合规组合满分且注册元数据齐备(self, dev_config, topic_slate):
        evaluator = _structure_gate(dev_config)
        result = _evaluate(evaluator, topic_slate)
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        spec = evaluator.spec
        assert spec.evaluator_id == "rule.slate_structure"
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert spec.version.startswith("1.0.0+") and len(spec.version) == len("1.0.0+") + 12

    def test_条目数越界判零并点名实测值与区间(self, dev_config, make_topic_slate):
        """越界即判 0：诊断必须给实测条目数与配置区间（不得只回一个 0）。"""
        result = _evaluate(_structure_gate(dev_config), make_topic_slate("count_out_of_range"))
        assert result.score == 0.0
        violation = next(item for item in result.diagnostics["violations"] if "条目数" in item)
        assert str(result.diagnostics["entry_count"]) in violation
        assert "3" in violation and "6" in violation  # movie 区间 [3, 6]
        assert result.diagnostics["entry_count"] == 1
        assert result.diagnostics["slate_interval"] == list(dev_config.slate_entries)

    def test_方向标识重复判零并点名(self, dev_config, make_topic_slate):
        slate = make_topic_slate("duplicate_direction")
        result = _evaluate(_structure_gate(dev_config), slate)
        assert result.score == 0.0
        duplicated = slate.direction_ids()[0]
        assert result.diagnostics["duplicate_direction_ids"] == [duplicated]
        assert any(
            "重复" in item and duplicated in item for item in result.diagnostics["violations"]
        )

    def test_要点缺失判零并点名字段名(self, dev_config, make_topic_slate):
        """被标记条目要点不全同此判 0：诊断点名缺失字段（不留空待补，FR-011）。"""
        result = _evaluate(_structure_gate(dev_config), make_topic_slate("missing_essentials"))
        assert result.score == 0.0
        missing = result.diagnostics["missing_fields"]["dir-awakening"]
        assert set(missing) == {"genre", "constraints", "characters"}
        violation = next(
            item for item in result.diagnostics["violations"] if "dir-awakening" in item
        )
        for field in missing:
            assert field in violation

    def test_全部缺陷变体判零且不抛异常(self, dev_config, make_topic_slate):
        """失败语义：以 EvalResult 返回（score 0.0 + violations），**不抛异常**。"""
        for variant in _STRUCTURE_DEFECTS:
            result = _evaluate(_structure_gate(dev_config), make_topic_slate(variant))
            assert isinstance(result, EvalResult)
            assert result.score == 0.0, variant
            assert result.diagnostics["violations"], variant

    def test_缺条目数区间即装配期拒绝(self):
        for value in (None, (3,), (0, 3), (5, 3), "3-6"):
            with pytest.raises(DevConfigError):
                SlateStructureEvaluator(slate_entries=value)


class Test组合门禁:
    def test_合规组合满分且注册元数据齐备(self, dev_config, topic_slate):
        evaluator = _combination_gate(dev_config)
        result = _evaluate(evaluator, topic_slate)
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["direction_repeat_rate"] == 0.0
        assert evaluator.spec.evaluator_id == "rule.slate_combination"
        assert evaluator.spec.kind is EvaluatorKind.RULE
        assert evaluator.spec.deterministic is True
        assert evaluator.spec.version.startswith("1.0.0+")

    def test_方向重复率超限判零并点名(self, dev_config, make_topic_slate):
        # 上限 0.0 时同一份重复组合（去重 3/4 → 重复率 0.25）必然超限
        evaluator = _combination_gate(dev_config, max_direction_repeat_rate=0.0)
        result = _evaluate(evaluator, make_topic_slate("duplicate_direction"))
        assert result.score == 0.0
        assert result.diagnostics["direction_repeat_rate"] == pytest.approx(0.25)
        assert any("重复率" in item for item in result.diagnostics["violations"])

    def test_条目数超上限判零(self, dev_config, topic_slate):
        """条目数上限由形态配置给出（越界即判 0，不由代码兜底）。"""
        evaluator = _combination_gate(dev_config, slate_entries=(1, 3))
        result = _evaluate(evaluator, topic_slate)  # 合规组合 4 条 > 上限 3
        assert result.score == 0.0
        assert any("条目数" in item and "上限" in item for item in result.diagnostics["violations"])

    def test_标记数量越界判零并点名(self, dev_config, make_topic_slate):
        """0 标记与区间下界 [1, 1] 冲突：以区间为准判 0 并如实点名。"""
        result = _evaluate(_combination_gate(dev_config), make_topic_slate("marks_out_of_range"))
        assert result.score == 0.0
        assert result.diagnostics["production_marks"] == []
        violation = next(item for item in result.diagnostics["violations"] if "标记数" in item)
        assert "0" in violation and "[1, 1]" in violation

    def test_悬空标记判零并点名(self, dev_config, make_topic_slate):
        result = _evaluate(_combination_gate(dev_config), make_topic_slate("dangling_mark"))
        assert result.score == 0.0
        assert result.diagnostics["dangling_marks"] == ["dir-not-in-slate"]
        assert any(
            "dir-not-in-slate" in item and "不存在" in item
            for item in result.diagnostics["violations"]
        )

    def test_零标记在区间允许时如实记录原因(self, dev_config, make_topic_slate):
        """区间下界为 0 时 0 标记不违规，但必须**如实记下原因**（不得省略、不得硬凑，SC-010）。"""
        evaluator = _combination_gate(dev_config, production_marks=(0, 1))
        result = _evaluate(evaluator, make_topic_slate("marks_out_of_range"))
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["production_marks"] == []
        assert result.diagnostics["marks_reason"]

    def test_全部缺陷变体判零且不抛异常(self, dev_config, make_topic_slate):
        evaluator = _combination_gate(dev_config)
        for variant in _COMBINATION_DEFECTS:
            result = _evaluate(evaluator, make_topic_slate(variant))
            assert isinstance(result, EvalResult)
            assert result.score == 0.0, variant
            assert result.diagnostics["violations"], variant

    def test_缺配置即装配期拒绝(self, dev_config):
        """缺项即装配期报错（不静默取码内默认，FR-012）：直连构造亦不得放过。"""
        base = {
            "slate_entries": dev_config.slate_entries,
            "production_marks": dev_config.production_marks,
            "max_direction_repeat_rate": dev_config.max_direction_repeat_rate,
        }
        for overrides in (
            {"slate_entries": None},
            {"production_marks": None},
            {"max_direction_repeat_rate": None},
            {"max_direction_repeat_rate": 1.5},
            {"production_marks": (2, 1)},
        ):
            with pytest.raises(DevConfigError):
                SlateCombinationEvaluator(**{**base, **overrides})
