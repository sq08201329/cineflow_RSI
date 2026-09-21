"""功能 015 阶段 2（T1509）：`core/orchestration/ledger.py` 账目汇总测试（契约 C4）。

覆盖：按阶段/形态汇总 == 各 Agent 落盘成本之和、对账不一致即报错（含容差语义、
键集不一致拒绝）、金额合法性、记录可序列化、形态值透传。
"""

import json

import pytest

from core.evaluators.errors import ValidationError
from core.orchestration.errors import LedgerMismatchError
from core.orchestration.ledger import summarize_cost
from core.orchestration.models import (
    ProductRef,
    RunRecord,
    RunStatus,
    StageState,
    StageStatus,
    fingerprint_of,
)


def _stage(stage_id: str, cost: float) -> StageState:
    product = ProductRef(kind="k", ref=stage_id, content_hash=fingerprint_of(stage_id))
    return (
        StageState(stage_id=stage_id)
        .transition_to(StageStatus.RUNNING, at="2026-09-21T00:00:00+00:00")
        .transition_to(
            StageStatus.DONE,
            at="2026-09-21T00:00:01+00:00",
            products=(product,),
            cost_usd=cost,
            input_fingerprint=fingerprint_of("materials"),
        )
    )


def _record(form: str = "form-x") -> RunRecord:
    return RunRecord(
        run_id="run-1",
        form=form,
        config_fingerprint=fingerprint_of("config"),
        input_fingerprint=fingerprint_of("materials"),
        stages=(_stage("a1", 1.0), _stage("a2", 0.5), _stage("a3", 0.25)),
        started_at="2026-09-21T00:00:00+00:00",
        status=RunStatus.DONE,
        finished_at="2026-09-21T00:00:09+00:00",
    )


class Test对账一致:
    def test_汇总逐项与合计(self):
        record = _record()
        ledger = summarize_cost(record, {"a1": 1.0, "a2": 0.5, "a3": 0.25})
        assert ledger.run_id == "run-1" and ledger.form == "form-x"
        assert ledger.by_stage == {"a1": 1.0, "a2": 0.5, "a3": 0.25}
        assert ledger.by_source == {"a1": 1.0, "a2": 0.5, "a3": 0.25}
        assert ledger.by_form == {"form-x": 1.75}
        assert ledger.total_usd == pytest.approx(record.total_cost_usd)
        assert all(line.delta_usd == 0.0 for line in ledger.lines)
        assert [line.stage_id for line in ledger.lines] == ["a1", "a2", "a3"]

    def test_合计等于各来源之和(self):
        ledger = summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25})
        assert ledger.total_usd == pytest.approx(sum(ledger.by_stage.values()))
        assert ledger.total_usd == pytest.approx(sum(ledger.by_source.values()))

    def test_容差内的浮点误差视为一致(self):
        ledger = summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25 + 1e-12})
        assert ledger.total_usd == pytest.approx(1.75 + 1e-12)

    def test_记录可序列化(self):
        ledger = summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25})
        payload = json.loads(json.dumps(ledger.to_dict(), ensure_ascii=False, sort_keys=True))
        assert payload["by_stage"]["a2"] == 0.5
        assert payload["lines"][0]["delta_usd"] == 0.0


class Test对账失败:
    def test_阶段金额不一致即报错(self):
        with pytest.raises(LedgerMismatchError) as excinfo:
            summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.9})
        message = str(excinfo.value)
        assert "a3" in message and "0.25" in message and "0.9" in message

    def test_超容差才报错(self):
        with pytest.raises(LedgerMismatchError):
            summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25 + 1e-6})

    def test_缺键拒绝(self):
        with pytest.raises(LedgerMismatchError) as excinfo:
            summarize_cost(_record(), {"a1": 1.0, "a2": 0.5})
        assert "a3" in str(excinfo.value)

    def test_多键拒绝(self):
        with pytest.raises(LedgerMismatchError):
            summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25, "ghost": 0.0})

    def test_负值或非数值拒绝(self):
        with pytest.raises(ValidationError):
            summarize_cost(_record(), {"a1": -1.0, "a2": 0.5, "a3": 0.25})
        with pytest.raises(ValidationError):
            summarize_cost(_record(), {"a1": "1.0", "a2": 0.5, "a3": 0.25})
        with pytest.raises(ValidationError):
            summarize_cost(_record(), {"a1": 1.0, "a2": 0.5, "a3": 0.25}, tolerance_usd=-1.0)


class Test形态与状态:
    def test_形态值如实进入汇总(self):
        ledger = summarize_cost(_record(form="form-y"), {"a1": 1.0, "a2": 0.5, "a3": 0.25})
        assert ledger.to_dict()["by_form"] == {"form-y": 1.75}
        assert ledger.form == "form-y"

    def test_失败运行已花费照实汇总(self):
        failed = (
            StageState(stage_id="a2")
            .transition_to(StageStatus.RUNNING, at="t0")
            .transition_to(StageStatus.FAILED, at="t1", failure_reason="候选全败", cost_usd=0.4)
        )
        record = RunRecord(
            run_id="run-2",
            form="form-x",
            config_fingerprint=fingerprint_of("config"),
            input_fingerprint=fingerprint_of("materials"),
            stages=(
                _stage("a1", 1.0),
                failed,
                StageState(stage_id="a3", status=StageStatus.SKIPPED),
            ),
            started_at="2026-09-21T00:00:00+00:00",
            status=RunStatus.FAILED,
            failure_stage="a2",
            failure_reason="候选全败",
            finished_at="2026-09-21T00:00:09+00:00",
        )
        ledger = summarize_cost(record, {"a1": 1.0, "a2": 0.4, "a3": 0.0})
        assert ledger.total_usd == pytest.approx(1.4)
        assert ledger.by_stage["a3"] == 0.0
