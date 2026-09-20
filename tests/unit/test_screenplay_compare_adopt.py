"""回放对比与采纳的接口形状单测（功能 009 / T929 骨架；C14 全场景由 T926 补齐）。

本批（T929）只锁定**接口稳定性**（可导入 + 数据模型字段 + 机检顺序），
C14 的四场景（逐树/分项/pareto/UNKNOWN、未采纳指针不变、采纳后指针更新+留痕、
拒绝留痕理由非空、回放零 LLM）由 T926 在本文件内扩展。
"""

import copy
import json
from pathlib import Path

import pytest
import yaml

from agents.screenplay.adoption import (
    AdoptionError,
    AdoptionRecord,
    adopt,
    deployed_version,
)
from agents.screenplay.sandbox_compare import (
    ReplayComparison,
    compare_versions,
    load_comparison,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


def _config_copy(tmp_path: Path, *, pointer: str | None = None) -> Path:
    """配置副本（可选写入部署指针），用于指针读写与"未采纳不变"机检。"""
    raw = copy.deepcopy(_REAL_CONFIG)
    if pointer is not None:
        raw["deployment"] = {"screenplay": {"current_policy_version": pointer}}
    path = tmp_path / "movie.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


class Test接口可导入:
    def test_契约符号齐备(self):
        from agents.screenplay import adoption, sandbox_compare

        assert callable(sandbox_compare.compare_versions)
        assert callable(sandbox_compare.load_comparison)
        assert callable(adoption.adopt)
        assert callable(adoption.deployed_version)
        assert callable(adoption.load_adoption_record)

    def test_ReplayComparison_字段集(self):
        """C14 报告字段锁定（逐树得分/分项差异/pareto 曲线/UNKNOWN 说明/结论）。"""
        assert set(ReplayComparison.__dataclass_fields__) == {
            "comparison_id",
            "agent_id",
            "new_version",
            "deployed_version",
            "per_tree",
            "per_evaluator",
            "pareto_curve",
            "pareto_auc",
            "mean_score",
            "unknown_trees",
            "verdict",
            "note",
            "created_at",
        }

    def test_AdoptionRecord_字段集(self):
        """C14 采纳记录字段锁定（结论/人/时间/依据/理由/指针前后值）。"""
        assert set(AdoptionRecord.__dataclass_fields__) == {
            "comparison_id",
            "agent_id",
            "decision",
            "by",
            "reason",
            "at",
            "deployed_before",
            "deployed_after",
            "adopted_version",
            "record_path",
        }


class Test部署指针:
    def test_未配置指针返回_None(self, tmp_path):
        config_path = _config_copy(tmp_path)
        assert deployed_version(config_path) is None  # 如实不伪造

    def test_指针读取(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        assert deployed_version(config_path) == "9f2c41ab77de"


class Test采纳机检顺序:
    """机检先于副作用：输入校验与依据检查都在指针变更之前。"""

    def test_决策枚举非法拒绝(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="decision"):
            adopt(
                "cmp-x",
                "maybe",
                "sunqi",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"  # 指针不变

    @pytest.mark.parametrize("reason", ["", "   "], ids=["空", "仅空白"])
    def test_拒绝留痕理由非空(self, tmp_path, reason):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="理由"):
            adopt(
                "cmp-x",
                "reject",
                "sunqi",
                reason,
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"

    def test_决策人为空拒绝(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(AdoptionError, match="决策人"):
            adopt(
                "cmp-x",
                "adopt",
                "",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )

    def test_依据报告缺失拒绝且指针不变(self, tmp_path):
        config_path = _config_copy(tmp_path, pointer="9f2c41ab77de")
        with pytest.raises(FileNotFoundError, match="对比报告"):
            adopt(
                "cmp-missing",
                "adopt",
                "sunqi",
                "理由",
                config_path=config_path,
                comparison_dir=tmp_path / "comparisons",
                adoption_dir=tmp_path / "adoptions",
            )
        assert deployed_version(config_path) == "9f2c41ab77de"


class Test报告读取:
    def test_缺报告拒绝(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_comparison("cmp-missing", comparison_dir=tmp_path / "comparisons")

    def test_报告落盘只增不改(self, tmp_path):
        from agents.screenplay.sandbox_compare import _persist

        report = ReplayComparison(
            comparison_id="cmp-screenplay-a-b",
            agent_id="screenplay",
            new_version="a",
            deployed_version="b",
            per_tree=[],
            per_evaluator=[],
            pareto_curve={"new": [], "deployed": []},
            pareto_auc={"new": 0.0, "deployed": 0.0},
            mean_score={"new": 0.0, "deployed": 0.0},
            unknown_trees=[],
            verdict="inconclusive",
            note="",
            created_at="2026-09-20T00:00:00+00:00",
        )
        directory = tmp_path / "comparisons"
        path = _persist(report, directory)
        assert json.loads(path.read_text(encoding="utf-8"))["comparison_id"] == report.comparison_id
        with pytest.raises(FileExistsError):  # 只增不改
            _persist(report, directory)

    def test_拒绝同版本对比(self, tmp_path):
        from core.replay.pool import SimulatorPool

        class _Store:
            pass

        with pytest.raises(ValueError, match="相同"):
            compare_versions(
                "same",
                "same",
                SimulatorPool(_Store()),  # 同版本判定先于池操作
                None,
                store=_Store(),
                history_root=tmp_path,
                comparison_dir=tmp_path / "comparisons",
                replay_fn=lambda source, pool: None,
            )
