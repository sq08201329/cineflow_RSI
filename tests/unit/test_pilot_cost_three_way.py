"""功能 018 阶段 6 / US3（契约 C13 / FR-006）：全链路成本**三方一致**与第三方腿（网关记账）。

覆盖：

1. `cost.json` 覆盖七环节、逐项差额在 1e-9 容差内、合计等于逐项之和、`reconciled` 为真；
2. **第三方腿**（`cost.json` 的确定性段）：逐环节网关记账增量 + 可比性分级 + 逐项核对结论；
3. **可比性分级**——LLM 腿专属环节（`dev`/`script`）逐项**相等**（`script` 取"阶段成本 +
   该环节评估器计费增量"：运营表只记生成、judge 计费只进树节点，见 screenplay loop 的
   `_reconcile` 桥，**仍是等式**）；混合腿（`storyboard`/`visual`/`editing`/`promo`）只断言
   "阶段成本 ≥ 该环节网关增量"；平台腿（`sound`）网关增量恒 0；
4. **账本腿**（`billing/{channel}/ledger.json`）仅在 `window.kind == run` 且窗口实例 == 本次
   `run_id` 时逐项比对；`day`/`period` 窗口是**窗口累计**，如实备注"不作逐项比对"
   （不静默比对、也不静默跳过）；不一致即报错；
5. `FAILED` 照常入账（原则二）；第三方腿**缺采样即报错**（不静默取 0——"未发生花费"与
   "没采到"在证据面上可分辨）；
6. 019 运行记录的 entry 字段集**增删即红**（链式摘要输入）；第三腿**不落 entry**。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.pilot import run_report
from agents.pilot import stages as stages_module
from agents.pilot.pilot import PilotInputs, run_pilot
from agents.pilot.stages import PILOT_STAGE_IDS
from core.billing import runlog
from core.orchestration.ledger import summarize_cost
from core.orchestration.models import (
    ProductRef,
    RunRecord,
    RunStatus,
    StageState,
    StageStatus,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FORM = "shortdrama"
# 019 运行记录 entry 的字段集（测试侧独立字面量：实现增删即红——它是链式摘要的输入）
RUN_ENTRY_FIELDS = (
    "at",
    "stage",
    "source",
    "adapter_ref",
    "profile_id",
    "result",
    "cost_source",
    "fallback_reason",
)


def _demo_config(tmp_path: Path) -> Path:
    text = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    assert "root: billing" in text
    text = text.replace("root: billing", f"root: {tmp_path / 'billing'}")
    for old, new in (
        ("target_duration_s: 120.0", "target_duration_s: 30.0"),
        ("script_target_minutes: 2.0", "script_target_minutes: 0.5"),
        ("production_marks: {min: 1, max: 2}", "production_marks: {min: 1, max: 1}"),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "configs" / "shortdrama-demo.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=0.5,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


@pytest.fixture(scope="module")
def pilot_run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("cost-three-way")
    config_path = _demo_config(tmp_path)
    original = stages_module.build_runtime
    captured: dict = {}

    def _capture(**kwargs):
        runtime = original(**kwargs)
        captured["runtime"] = runtime
        return runtime

    stages_module.build_runtime = _capture
    try:
        result = run_pilot(
            form=FORM,
            config_path=config_path,
            inputs=_inputs(),
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
            run_id="run-cost",
            clock=lambda: "2026-01-01T00:00:00+00:00",
        )
    finally:
        stages_module.build_runtime = original
    assert result.record.status.value == "done"
    return {
        "result": result,
        "runtime": captured["runtime"],
        "config_path": config_path,
        "tmp": tmp_path,
    }


def _cost(pilot_run) -> dict:
    return json.loads((pilot_run["result"].package_dir / "cost.json").read_text(encoding="utf-8"))


class Test三方一致:
    def test_cost_覆盖七环节且逐项零差异(self, pilot_run):
        cost = _cost(pilot_run)
        assert set(cost["by_stage"]) == set(PILOT_STAGE_IDS)
        assert {line["stage_id"] for line in cost["lines"]} == set(PILOT_STAGE_IDS)
        for line in cost["lines"]:
            assert abs(float(line["recorded_usd"]) - float(line["ledger_usd"])) <= 1e-9
            assert float(line["delta_usd"]) == 0.0
        assert cost["reconciled"] is True
        assert abs(float(cost["total_usd"]) - sum(cost["by_stage"].values())) <= 1e-9
        recorded_total = float(pilot_run["result"].record.total_cost_usd)
        assert abs(float(cost["total_usd"]) - recorded_total) <= 1e-9

    def test_第三方腿逐环节增量入包(self, pilot_run):
        gateway = _cost(pilot_run)["gateway"]
        assert set(gateway["by_stage"]) == set(PILOT_STAGE_IDS)
        assert gateway["reconciled"] is True
        assert gateway["comparability"] == run_report.STAGE_COST_LEG
        assert gateway["comparisons"] == run_report.STAGE_GATEWAY_COMPARISON
        assert abs(gateway["total_usd"] - sum(gateway["by_stage"].values())) <= 1e-9
        # 第三方腿的确定性段（无墙钟/无路径）
        payload = json.dumps(gateway, ensure_ascii=False, sort_keys=True)
        for banned in ("created_at", "timestamp", "started_at", "finished_at", "/tmp"):
            assert banned not in payload, banned

    def test_逐环节可比性分级判定(self, pilot_run):
        gateway = _cost(pilot_run)["gateway"]
        lines = {line["stage_id"]: line for line in gateway["lines"]}
        for stage_id in PILOT_STAGE_IDS:
            line = lines[stage_id]
            leg = run_report.STAGE_COST_LEG[stage_id]
            if leg == "llm_only":
                llm_spend = line["recorded_usd"] + line["evaluator_usd"]
                assert abs(llm_spend - line["gateway_usd"]) <= 1e-9
            elif leg == "mixed":
                assert line["recorded_usd"] >= line["gateway_usd"] - 1e-9
            else:
                assert line["gateway_usd"] == 0.0
        # script 的等式落在"阶段成本 + 评估器计费增量"（运营表只记生成，judge 只进树节点）
        assert lines["script"]["evaluator_usd"] > 0.0
        assert lines["dev"]["evaluator_usd"] == 0.0
        assert lines["sound"]["gateway_usd"] == 0.0


class Test账本腿:
    def test_真实运行的窗口累计如实备注(self, pilot_run):
        profile_ledger = run_report.ledger_leg(pilot_run["result"].record, pilot_run["runtime"])
        # 形态配置声明的是 `day` 窗口（019 缺省实例 = 渠道本地日）⇒ 窗口累计、不作逐项比对
        assert profile_ledger["comparability"] == "window_accumulated"
        assert profile_ledger["reconciled"] is None
        assert "不作逐项比对" in profile_ledger["note"]
        assert profile_ledger["by_tier"]  # 逐环节账目如实登记（不是跳过）
        assert profile_ledger["channel_id"]

    def test_run_窗口逐项可比(self):
        deltas = {stage_id: 0.01 for stage_id in PILOT_STAGE_IDS}
        record = _record_with_deltas(deltas, run_id="run-window")
        runtime = _stub_runtime({"dev": 0.01, "promo": 0.06}, run_id="run-window")
        ledger = run_report.ledger_leg(record, runtime)
        assert ledger["comparability"] == "run_window"
        assert ledger["reconciled"] is True
        assert "逐项可比" in ledger["note"]
        assert ledger["total_usd"] == pytest.approx(0.07)

    def test_run_窗口不一致即报错(self):
        deltas = {stage_id: 0.01 for stage_id in PILOT_STAGE_IDS}
        record = _record_with_deltas(deltas, run_id="run-window")
        runtime = _stub_runtime({"dev": 0.02, "promo": 0.06}, run_id="run-window")
        with pytest.raises(run_report.ReportError, match="账本腿与第三方腿不一致"):
            run_report.ledger_leg(record, runtime)

    def test_未接门禁即如实标注(self):
        record = _record_with_deltas(
            {stage_id: 0.0 for stage_id in PILOT_STAGE_IDS}, run_id="run-x"
        )
        runtime = SimpleNamespace(gateway=SimpleNamespace())
        ledger = run_report.ledger_leg(record, runtime)
        assert ledger["comparability"] == "not_available"
        assert ledger["reconciled"] is None


class Test入账语义:
    def test_FAILED_照常入账(self):
        """原则二：失败环节的成本照常入账（不因失败而消失）。"""
        record = _record_with_deltas(
            {stage_id: 0.0 for stage_id in PILOT_STAGE_IDS},
            run_id="run-failed",
            failed="visual",
            failed_cost=0.75,
        )
        ledger = summarize_cost(record, {state.stage_id: state.cost_usd for state in record.stages})
        assert ledger.by_stage["visual"] == pytest.approx(0.75)
        assert ledger.total_usd == pytest.approx(0.75)
        assert {line.stage_id for line in ledger.lines} == set(PILOT_STAGE_IDS)

    def test_缺采样即报错不静默取零(self):
        """缺采样（没采到）与零花费（未发生）必须可分辨：缺采样即报错。"""
        record = _record_with_deltas(
            {stage_id: 0.0 for stage_id in PILOT_STAGE_IDS}, run_id="run-x"
        )
        stripped = record.stages[0]
        object.__setattr__(stripped, "detail", {"spent_usd": 0.0})
        with pytest.raises(run_report.ReportError, match="缺网关记账增量采样"):
            run_report.stage_gateway_deltas(record)

    def test_成本腿分级缺项即拒绝(self, monkeypatch):
        """分级/比较式缺一处即拒绝（不静默放过；两张声明表键集须覆盖七环节）。"""
        partial = {
            stage_id: leg
            for stage_id, leg in run_report.STAGE_COST_LEG.items()
            if stage_id != "dev"
        }
        monkeypatch.setattr(run_report, "STAGE_COST_LEG", partial)
        record = _record_with_deltas(
            {stage_id: 0.0 for stage_id in PILOT_STAGE_IDS}, run_id="run-x"
        )
        with pytest.raises(run_report.ReportError, match="未声明"):
            run_report.gateway_leg(record)


class Test运行记录字段面:
    def test_entry_字段集冻结(self):
        assert tuple(runlog.RUN_ENTRY_FIELDS) == RUN_ENTRY_FIELDS

    def test_第三腿不落_entry(self, pilot_run):
        root = pilot_run["config_path"].parent.parent / "billing"
        files = sorted((root / "llm" / "runs").glob("*.json"))
        assert files
        for path in files:
            payload = runlog.load_run(path.stem, channel_id="llm", root=root)
            for entry in payload["entries"]:
                assert set(entry) == {*RUN_ENTRY_FIELDS, "head_digest"}
                assert "gateway" not in entry and "by_stage" not in entry


def _stub_runtime(tier_spend: dict[str, float], *, run_id: str):
    """账本腿的桩（只提供 `gateway.spend_guard.ledger.read()` 的形状，不构造网关）。"""

    class _Ledger:
        def read(self) -> dict:
            return {
                "revision": 1,
                "tiers": {
                    tier_id: {
                        "spent_usd": spent,
                        "reserved_usd": 0.0,
                        "refusals": 0,
                        "window_key": ["run", run_id],
                    }
                    for tier_id, spent in tier_spend.items()
                },
            }

    guard = SimpleNamespace(channel_id="llm", ledger=_Ledger())
    return SimpleNamespace(gateway=SimpleNamespace(spend_guard=guard))


def _record_with_deltas(
    deltas: dict[str, float],
    *,
    run_id: str,
    failed: str | None = None,
    failed_cost: float = 0.0,
) -> RunRecord:
    """合成运行记录：逐环节带网关增量采样（第三方腿机检用）。

    `failed` 给出失败环节时，其**下游一律 `skipped`**（不静默降级：RunRecord 的既有约束）。
    """
    failing_index = PILOT_STAGE_IDS.index(failed) if failed else -1
    stages = []
    for index, stage_id in enumerate(PILOT_STAGE_IDS):
        skipped = failing_index >= 0 and index > failing_index
        status = StageStatus.FAILED if stage_id == failed else StageStatus.DONE
        if skipped:
            status = StageStatus.SKIPPED
        stages.append(
            StageState(
                stage_id=stage_id,
                status=status,
                input_fingerprint="ab" * 32,
                products=(ProductRef(kind="reel", ref="ref", content_hash="cd" * 32),),
                cost_usd=failed_cost if stage_id == failed else 0.0,
                started_at="2026-01-01T00:00:00+00:00",
                finished_at=None if skipped else "2026-01-01T00:00:01+00:00",
                failure_reason="注入失败" if stage_id == failed else "",
                detail={
                    "spent_usd": failed_cost if stage_id == failed else 0.0,
                    "gateway_delta_usd": deltas[stage_id],
                },
            )
        )
    return RunRecord(
        run_id=run_id,
        form=FORM,
        config_fingerprint="ef" * 32,
        input_fingerprint="ab" * 32,
        stages=tuple(stages),
        status=RunStatus.FAILED if failed else RunStatus.DONE,
        failure_stage=failed or "",
        failure_reason="注入失败" if failed else "",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:02+00:00",
    )
