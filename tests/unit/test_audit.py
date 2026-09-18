"""immutable 审计核心比对逻辑单测（T031）。

覆盖 ops/audit_immutable.py 的纯函数：recompute_node_score / audit_sample /
audit_samples。DB 抽样（_load_samples）属 PG 集成面，不在单测范围。
"""

import pytest

from core.tree.errors import ValidationError
from ops.audit_immutable import audit_sample, audit_samples, recompute_node_score

SNAPSHOT = {
    "evaluator_weights": {"rule.gate": 0.0, "proxy.a": 0.5, "proxy.b": 0.5},
    "evaluator_versions": {"rule.gate": "1.0.0"},
}


def _node_fields(**overrides):
    fields = {
        "node_id": "n1",
        "status": "evaluated",
        "score": 0.7,
        "eval_breakdown": {
            "rule.gate@1.0.0": {"score": 1.0},
            "proxy.a@1.0.0": {"score": 0.8},
            "proxy.b@2.0.0": {"score": 0.6},
        },
    }
    fields.update(overrides)
    return fields


class TestRecomputeNodeScore:
    def test_版本化键对齐权重并复算(self):
        assert recompute_node_score(_node_fields()["eval_breakdown"], SNAPSHOT) == pytest.approx(
            0.7
        )

    def test_gate_零分复算为零(self):
        breakdown = _node_fields()["eval_breakdown"] | {"rule.gate@1.0.0": {"score": 0.0}}
        assert recompute_node_score(breakdown, SNAPSHOT) == 0.0

    def test_快照缺权重节报错(self):
        with pytest.raises(ValidationError, match="evaluator_weights"):
            recompute_node_score(_node_fields()["eval_breakdown"], {})

    def test_breakdown_键在权重中无对应报错(self):
        breakdown = _node_fields()["eval_breakdown"] | {"proxy.ghost@1.0.0": {"score": 0.1}}
        with pytest.raises(ValidationError, match="proxy.ghost"):
            recompute_node_score(breakdown, SNAPSHOT)

    def test_明细缺_score_字段报错(self):
        breakdown = _node_fields()["eval_breakdown"] | {"proxy.a@1.0.0": {"note": "无分数"}}
        with pytest.raises(ValidationError, match="score"):
            recompute_node_score(breakdown, SNAPSHOT)

    def test_权重有而_breakdown_缺评估器_由_composite_拒绝(self):
        from core.evaluators.errors import WeightMismatchError

        breakdown = {"rule.gate@1.0.0": {"score": 1.0}, "proxy.a@1.0.0": {"score": 0.8}}
        with pytest.raises(WeightMismatchError):
            recompute_node_score(breakdown, SNAPSHOT)


class TestAuditSample:
    def test_一致返回_none(self):
        assert audit_sample(_node_fields(), SNAPSHOT) is None

    def test_score_不一致返回差异明细(self):
        finding = audit_sample(_node_fields(score=0.9), SNAPSHOT)
        assert finding["node_id"] == "n1"
        assert finding["stored"] == 0.9
        assert finding["recomputed"] == pytest.approx(0.7)

    def test_failed_节点_score_none_合法(self):
        node = _node_fields(status="failed", score=None, eval_breakdown={})
        assert audit_sample(node, SNAPSHOT) is None

    def test_failed_节点带_score_报差异(self):
        finding = audit_sample(
            _node_fields(status="failed", score=0.5, eval_breakdown={}), SNAPSHOT
        )
        assert "FAILED" in finding["reason"]

    def test_复算失败计入差异(self):
        finding = audit_sample(_node_fields(), {})
        assert "复算失败" in finding["reason"]


class TestAuditSamples:
    def test_批量报告(self):
        samples = [
            (_node_fields(), SNAPSHOT),
            (_node_fields(node_id="n2", score=0.9), SNAPSHOT),
            (_node_fields(node_id="n3", status="failed", score=None, eval_breakdown={}), SNAPSHOT),
        ]
        report = audit_samples(samples)
        assert report["checked"] == 3
        assert report["ok"] == 2
        assert [f["node_id"] for f in report["failures"]] == ["n2"]

    def test_空样本(self):
        assert audit_samples([]) == {"checked": 0, "ok": 0, "failures": []}


class Test主程序入口:
    def test_缺_dsn_返回非零(self, monkeypatch, capsys):
        monkeypatch.delenv("CINEFLOW_PG_DSN", raising=False)
        monkeypatch.setattr("sys.argv", ["audit_immutable.py"])
        from ops.audit_immutable import main

        assert main() == 2
        assert "DSN" in capsys.readouterr().out
