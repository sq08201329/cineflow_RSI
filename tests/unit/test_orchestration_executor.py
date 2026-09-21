"""功能 015 阶段 2（T1507）：`core/orchestration/executor.py` 执行器测试（契约 C2/C3）。

覆盖：阶段状态机（失败→其后 skipped）、断点续跑不重跑已完成阶段（**调用计数机检**：
桩调用次数 + 阶段 attempts）、输入/配置指纹不一致拒绝续跑、完成后续跑幂等、
阶段集合变化拒绝、非预期异常如实记失败、持久化钩子、输入契约（handoff）接线、
上游输出进入下游输入，以及**执行器零环节/形态字面量**的静态断言（宪章原则五）。
"""

import json
from pathlib import Path

import pytest

from core.orchestration import executor as executor_module
from core.orchestration.dag import build_dag
from core.orchestration.errors import ResumeRejectedError, StageFailedError
from core.orchestration.executor import ExecutionContext, resume, run
from core.orchestration.models import (
    ProductRef,
    RunRecord,
    RunStatus,
    StageOutcome,
    StageSpec,
    StageStatus,
    fingerprint_of,
)

FORM = "form-x"  # 中性形态值：执行器只透传，不认识具体形态
CONFIG_FP = fingerprint_of("config")
INPUT_FP = fingerprint_of("materials")
OTHER_FP = fingerprint_of("materials-changed")


def _clock():
    """确定性时钟工厂：单调递增的伪 ISO 时间戳（同一测试内可预期）。"""
    counter = {"n": 0}

    def _tick() -> str:
        counter["n"] += 1
        return f"2026-09-21T00:00:{counter['n']:02d}+00:00"

    return _tick


class _Store:
    """持久化钩子桩：记录每次保存的记录快照（模拟 pilot/runs/{run_id}.json 落盘）。"""

    def __init__(self) -> None:
        self.snapshots: list[RunRecord] = []

    def save(self, record: RunRecord) -> None:
        json.loads(record.dump_json())  # 落盘口径即 JSON：记录可脱离内存复现
        self.snapshots.append(record)


def stub_calls(stubs) -> int:
    """桩调用总次数（"不重跑"的机检口径）。"""
    return sum(stub.calls for stub in stubs.values())


def _chain(stage_entrypoint_stub, count: int, **overrides):
    """中性阶段链工厂：`count` 段线性依赖（a1 → a2 → ...），返回 (dag, stubs)。"""
    stubs = {}
    specs = []
    for index in range(1, count + 1):
        stage_id = f"a{index}"
        stub = stage_entrypoint_stub(stage_id, cost_usd=0.5, **dict(overrides.get(stage_id, {})))
        stubs[stage_id] = stub
        specs.append(
            StageSpec(
                stage_id=stage_id,
                entrypoint=stub,
                depends_on=() if index == 1 else (f"a{index - 1}",),
                output_kind="artifact",
            )
        )
    return build_dag(specs), stubs


def _ctx(input_fingerprint: str = INPUT_FP, **overrides) -> ExecutionContext:
    fields = {
        "run_id": "run-1",
        "form": FORM,
        "config_fingerprint": CONFIG_FP,
        "input_fingerprint": input_fingerprint,
    }
    fields.update(overrides)
    return ExecutionContext(**fields)


class Test成功路径:
    def test_全阶段成功(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 3)
        record = run(dag, _ctx(), clock=_clock())
        assert record.status is RunStatus.DONE
        assert record.completed_stages == ("a1", "a2", "a3")
        assert record.failure_stage == "" and record.failure_reason == ""
        assert record.finished_at is not None
        assert record.total_cost_usd == pytest.approx(1.5)
        for stage_id, stub in stubs.items():
            assert stub.calls == 1
            state = record.stage(stage_id)
            assert state.status is StageStatus.DONE
            assert state.attempts == 1
            assert state.products and state.input_fingerprint
            assert state.started_at and state.finished_at
        assert record.form == FORM  # 形态值如实透传（执行器不解释）

    def test_上游输出进入下游输入(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 2)
        run(dag, _ctx(), clock=_clock())
        downstream_input = stubs["a2"].inputs[0]
        assert downstream_input.stage_id == "a2"
        assert set(downstream_input.upstream) == {"a1"}
        assert downstream_input.upstream["a1"].products[0].kind == "stub"

    def test_上游透传明细对下游可见(self, stage_entrypoint_stub):
        stubs = {stage_id: stage_entrypoint_stub(stage_id) for stage_id in ("a1", "a2")}
        payload = {"成片引用": "reel.mp4"}
        product = ProductRef(kind="reel", ref="reel.mp4", content_hash=fingerprint_of("reel"))

        def _first(stage_input):
            return StageOutcome(products=(product,), detail=payload)

        dag = build_dag(
            [
                StageSpec(stage_id="a1", entrypoint=_first),
                StageSpec(stage_id="a2", entrypoint=stubs["a2"], depends_on=("a1",)),
            ]
        )
        run(dag, _ctx(), clock=_clock())
        assert stubs["a2"].inputs[0].upstream["a1"].detail == payload  # 透传明细随记录流转

    def test_输入契约_handoff_接线(self, stage_entrypoint_stub):
        a1 = stage_entrypoint_stub("a1")
        a2 = stage_entrypoint_stub("a2")
        seen: list = []

        def _handoff(upstream):
            seen.append(dict(upstream))
            return {"mapped_from": sorted(upstream)}

        dag = build_dag(
            [
                StageSpec(stage_id="a1", entrypoint=a1),
                StageSpec(stage_id="a2", entrypoint=a2, depends_on=("a1",), handoff=_handoff),
            ]
        )
        run(dag, _ctx(), clock=_clock())
        assert seen and list(seen[0]) == ["a1"]  # 契约函数收到上游输出
        assert a2.inputs[0].handoff_input == {"mapped_from": ["a1"]}

    def test_持久化钩子每次阶段落定即保存(self, stage_entrypoint_stub):
        dag, _ = _chain(stage_entrypoint_stub, 3)
        store = _Store()
        record = run(dag, _ctx(), store=store, clock=_clock())
        assert len(store.snapshots) == 3
        assert store.snapshots[-1] == record
        assert store.snapshots[0].stage("a1").status is StageStatus.DONE
        assert store.snapshots[0].stage("a2").status is StageStatus.PENDING

    def test_共享透传数据进入阶段输入(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 1)
        ctx = _ctx(shared={"workspace": "/tmp/ws", "config_path": "/tmp/configs/form.yaml"})
        run(dag, ctx, clock=_clock())
        assert stubs["a1"].inputs[0].shared["workspace"] == "/tmp/ws"


class Test失败与跳过:
    def test_失败点的下游一律_skipped_且零调用(self, stage_entrypoint_stub):
        dag, stubs = _chain(
            stage_entrypoint_stub,
            4,
            a2={"fail": "两候选均判 0", "candidates": ["门禁违规", "预算超限"]},
        )
        record = run(dag, _ctx(), clock=_clock())
        assert record.status is RunStatus.FAILED
        assert record.failure_stage == "a2"
        assert record.failure_reason == "两候选均判 0"
        failed = record.stage("a2")
        assert failed.status is StageStatus.FAILED and failed.finished_at
        assert [reason for candidate in failed.candidates for reason in candidate.reasons] == [
            "门禁违规",
            "预算超限",
        ]
        for stage_id in ("a3", "a4"):
            assert record.stage(stage_id).status is StageStatus.SKIPPED
            assert stubs[stage_id].calls == 0  # 拒绝语义：下游不启动
        assert stubs["a1"].calls == 1 and stubs["a2"].calls == 1
        assert record.stage("a3").attempts == 0

    def test_未预期异常如实记失败(self, stage_entrypoint_stub):
        def _boom(stage_input):
            raise RuntimeError("底层崩了")

        dag = build_dag(
            [
                StageSpec(stage_id="a1", entrypoint=stage_entrypoint_stub("a1")),
                StageSpec(stage_id="a2", entrypoint=_boom, depends_on=("a1",)),
            ]
        )
        record = run(dag, _ctx(), clock=_clock())
        assert record.status is RunStatus.FAILED
        assert record.failure_stage == "a2"
        assert "RuntimeError" in record.failure_reason and "底层崩了" in record.failure_reason


class Test断点续跑:
    def test_续跑不重跑已完成阶段(self, stage_entrypoint_stub):
        broken_dag, broken = _chain(stage_entrypoint_stub, 4, a2={"fail": "候选全败"})
        first = run(broken_dag, _ctx(), clock=_clock())
        assert first.status is RunStatus.FAILED

        # 修复失败阶段（换成成功的入口），重建同一张图后从运行记录续跑
        fixed_dag, fixed = _chain(stage_entrypoint_stub, 4)
        resumed = run(fixed_dag, _ctx(), resume_from=first, clock=_clock())
        assert resumed.status is RunStatus.DONE
        assert broken["a1"].calls == 1 and fixed["a1"].calls == 0  # 已完成阶段零重跑
        assert resumed.stage("a1").attempts == 1
        assert fixed["a2"].calls == 1 and resumed.stage("a2").attempts == 2  # 失败阶段续跑
        assert fixed["a3"].calls == 1 and fixed["a4"].calls == 1
        assert resumed.stage("a3").attempts == 1  # 跳过阶段重排队后重跑

    def test_续跑跨落盘往返(self, stage_entrypoint_stub):
        broken_dag, broken = _chain(stage_entrypoint_stub, 3, a2={"fail": "候选全败"})
        first = run(broken_dag, _ctx(), clock=_clock())
        assert stub_calls(broken) == 2  # a1 完成 + a2 失败；a3 跳过未调用
        reloaded = RunRecord.from_dict(json.loads(first.dump_json()))

        fixed_dag, fixed = _chain(stage_entrypoint_stub, 3)
        store = _Store()
        resumed = run(fixed_dag, _ctx(), resume_from=reloaded, store=store, clock=_clock())
        assert resumed.status is RunStatus.DONE
        assert fixed["a1"].calls == 0 and fixed["a2"].calls == 1 and fixed["a3"].calls == 1
        # 续跑只对真正推进的阶段落盘（已完成阶段不重跑也不重复落盘）
        assert len(store.snapshots) == 2
        assert store.snapshots[-1] == resumed

    def test_输入指纹不一致拒绝续跑且零调用(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 3, a2={"fail": "候选全败"})
        first = run(dag, _ctx(), clock=_clock())
        before = stub_calls(stubs)
        with pytest.raises(ResumeRejectedError) as excinfo:
            run(dag, _ctx(OTHER_FP), resume_from=first, clock=_clock())
        assert OTHER_FP in str(excinfo.value)
        assert stub_calls(stubs) == before  # 拒绝即零副作用

    def test_配置指纹不一致拒绝续跑(self, stage_entrypoint_stub):
        dag, _ = _chain(stage_entrypoint_stub, 2, a1={"fail": "候选全败"})
        first = run(dag, _ctx(), clock=_clock())
        other = _ctx(config_fingerprint=fingerprint_of("config-changed"))
        with pytest.raises(ResumeRejectedError) as excinfo:
            run(dag, other, resume_from=first, clock=_clock())
        assert "配置指纹" in str(excinfo.value)

    def test_续跑入口与运行标识校验(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 2, a1={"fail": "候选全败"})
        first = run(dag, _ctx(), clock=_clock())
        fixed_dag, fixed = _chain(stage_entrypoint_stub, 2)
        resumed = resume(first, fixed_dag, _ctx(), clock=_clock())
        assert resumed.status is RunStatus.DONE
        assert fixed["a1"].calls == 1  # 失败阶段续跑
        with pytest.raises(ResumeRejectedError) as excinfo:
            run(dag, _ctx(run_id="run-2"), resume_from=first, clock=_clock())
        assert "运行标识" in str(excinfo.value)
        assert stub_calls(stubs) == 1  # 拒绝即零调用

    def test_阶段集合变化拒绝续跑(self, stage_entrypoint_stub):
        dag, _ = _chain(stage_entrypoint_stub, 3, a1={"fail": "候选全败"})
        first = run(dag, _ctx(), clock=_clock())
        other_dag, _ = _chain(stage_entrypoint_stub, 4, a1={"fail": "候选全败"})
        with pytest.raises(ResumeRejectedError):
            run(other_dag, _ctx(), resume_from=first, clock=_clock())

    def test_完成后再续跑幂等(self, stage_entrypoint_stub):
        dag, stubs = _chain(stage_entrypoint_stub, 3)
        done = run(dag, _ctx(), clock=_clock())
        store = _Store()
        again = run(dag, _ctx(), resume_from=done, store=store, clock=_clock())
        assert again == done  # 逐字段一致（幂等）
        assert stub_calls(stubs) == 3  # 零重跑
        assert store.snapshots == []  # 无副作用（不重复落盘）

    def test_失败与跳过阶段都会重排队(self, stage_entrypoint_stub):
        broken_dag, _ = _chain(stage_entrypoint_stub, 3, a2={"fail": "候选全败"})
        first = run(broken_dag, _ctx(), clock=_clock())
        assert sorted(s.stage_id for s in first.stages if s.status is StageStatus.SKIPPED) == ["a3"]
        fixed_dag, fixed = _chain(stage_entrypoint_stub, 3)
        resumed = run(fixed_dag, _ctx(), resume_from=first, clock=_clock())
        assert resumed.status is RunStatus.DONE
        assert fixed["a2"].calls == 1 and fixed["a3"].calls == 1


class Test静态断言:
    """宪章原则五：执行器**零业务概念**——源码不得出现环节/形态字面量与形态分支。"""

    BANNED_LITERALS = (
        # 形态值字面量
        "shortdrama",
        '"movie"',
        "'movie'",
        # 环节名字面量（英/中）
        "screenplay",
        "storyboard",
        "visual",
        "sound",
        "editing",
        "promo",
        "剧本",
        "分镜",
        "视觉",
        "声音",
        "剪辑",
        "宣发",
    )
    BANNED_PATTERNS = ("form ==", "form==", "form !=", "form!=", "form is ", "form in ")

    def _sources(self):
        root = Path(executor_module.__file__).parent
        return sorted(root.glob("*.py"))

    def test_包内源码无环节或形态字面量(self):
        assert self._sources(), "未找到 core/orchestration 源码"
        for path in self._sources():
            source = path.read_text(encoding="utf-8")
            for banned in self.BANNED_LITERALS:
                assert banned not in source, f"{path.name} 不得出现业务字面量：{banned}"
            for banned in self.BANNED_PATTERNS:
                assert banned not in source, f"{path.name} 不得出现形态分支：{banned}"

    def test_执行器不做形态分支_行为面(self, stage_entrypoint_stub):
        # 同一张图、同一入口，仅形态值不同 → 阶段行为逐字段一致（形态不进执行逻辑）
        first_dag, first_stubs = _chain(stage_entrypoint_stub, 2)
        other_dag, other_stubs = _chain(stage_entrypoint_stub, 2)
        first = run(first_dag, _ctx(), clock=_clock())
        other = run(other_dag, _ctx(form="form-y"), clock=_clock())
        assert stub_calls(first_stubs) == stub_calls(other_stubs)
        assert [s.status for s in first.stages] == [s.status for s in other.stages]
        assert first.to_dict()["stages"] == other.to_dict()["stages"]
        assert other.form == "form-y"


class Test运行时约束:
    def test_阶段失败理由与候选随记录落盘(self, stage_entrypoint_stub):
        dag, _ = _chain(
            stage_entrypoint_stub, 2, a1={"fail": "候选全败", "candidates": ["判 0 理由 A"]}
        )
        record = run(dag, _ctx(), clock=_clock())
        payload = json.loads(record.dump_json())
        failed = next(item for item in payload["stages"] if item["status"] == "failed")
        assert failed["failure_reason"] == "候选全败"
        assert failed["candidates"][0]["reasons"] == ["判 0 理由 A"]

    def test_StageFailedError_携带候选(self):
        error = StageFailedError("全部判 0")
        assert error.reason == "全部判 0" and error.candidates == ()

    def test_阶段成本累计进记录(self, stage_entrypoint_stub):
        dag, _ = _chain(stage_entrypoint_stub, 2)
        record = run(dag, _ctx(), clock=_clock())
        assert [state.cost_usd for state in record.stages] == [0.5, 0.5]

    def test_默认时钟产出_iso_时间戳(self, stage_entrypoint_stub):
        from datetime import datetime

        dag, _ = _chain(stage_entrypoint_stub, 2)
        record = run(dag, _ctx())
        for stamp in (record.started_at, record.finished_at):
            assert stamp and datetime.fromisoformat(stamp).tzinfo is not None
