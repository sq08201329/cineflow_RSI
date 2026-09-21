"""阶段执行器（功能 015 阶段 2 / T1508，契约 C2/C3）：状态机 + 断点续跑 + 指纹校验。

**零业务概念**（宪章原则五，静态断言见 `tests/unit/test_orchestration_executor.py`）：
执行器只认识 stage_id、依赖、执行入口引用与产物引用；每阶段的业务语义由调用方经
`StageSpec.entrypoint`（阶段输入 → 阶段输出）与 `StageSpec.handoff`（上游输出 → 本阶段
输入映射）注入，执行器对二者**只调用、不解释**。执行器不产生落树路径——
各阶段落树沿用其既有入口（FR-011）。

语义（契约 C2/C3）：
- 逐阶段按拓扑序执行：`pending → running → done | failed`；某阶段失败 → 其**后**阶段
  一律 `skipped`（不做静默降级），运行状态置 `failed` 并记失败点与原因；
- 断点续跑：输入指纹（素材哈希）与配置指纹任一不一致即 `ResumeRejectedError`，
  拒绝时零副作用；已完成阶段不重跑（`attempts` 为累计调用计数，可机检）；
- 全部完成后再次续跑幂等（原记录原样返回，零调用、零落盘）；
- 可选的 `store` 钩子在每次阶段落定后保存记录（调用方决定落盘形式，
  如 `pilot/runs/{run_id}.json`）：**保存的记录状态始终自洽**（由阶段状态派生运行状态），
  故中断后从最近一次保存续跑即可。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from core.orchestration.dag import Dag
from core.orchestration.errors import OrchestrationError, ResumeRejectedError, StageFailedError
from core.orchestration.models import (
    RunRecord,
    RunStatus,
    StageInput,
    StageOutcome,
    StageSpec,
    StageState,
    StageStatus,
)

Clock = Callable[[], str]


class RunStore(Protocol):
    """运行记录持久化钩子（执行器只在每次阶段落定后调用一次 `save`）。"""

    def save(self, record: RunRecord) -> None: ...


@dataclass(frozen=True)
class ExecutionContext:
    """一次执行的上下文：运行标识 + 形态值 + 配置/输入指纹 + 业务侧共享数据。

    `input_fingerprint` = 素材哈希（+ 配置哈希）的合成指纹，续跑时逐字比对；
    `shared` 为业务侧透传数据（工作目录、配置路径等），执行器不解释其内容。
    """

    run_id: str
    form: str
    config_fingerprint: str
    input_fingerprint: str
    shared: Mapping[str, Any] = field(default_factory=dict)


def _default_clock() -> str:
    return datetime.now(UTC).isoformat()


def run(
    dag: Dag,
    ctx: ExecutionContext,
    *,
    resume_from: RunRecord | None = None,
    store: RunStore | None = None,
    clock: Clock | None = None,
) -> RunRecord:
    """执行（或续跑）依赖图，返回运行记录。`resume_from` 给出既有记录即进入续跑语义。"""
    tick = clock or _default_clock
    if resume_from is not None and resume_from.status is RunStatus.DONE:
        return resume_from  # 幂等：全部完成后再次续跑零副作用（不重跑、不落盘）

    if resume_from is None:
        order = dag.topological_order()
        states = {stage_id: StageState(stage_id=stage_id) for stage_id in order}
        record = RunRecord(
            run_id=ctx.run_id,
            form=ctx.form,
            config_fingerprint=ctx.config_fingerprint,
            input_fingerprint=ctx.input_fingerprint,
            stages=tuple(states[stage_id] for stage_id in order),
            started_at=tick(),
        )
    else:
        record = _resume_base(dag, ctx, resume_from)
        states = {state.stage_id: state for state in record.stages}

    outcomes: dict[str, StageOutcome] = {
        state.stage_id: _outcome_of(state)
        for state in states.values()
        if state.status is StageStatus.DONE
    }

    failure: StageState | None = None
    dependents = _dependents_map(dag)
    for stage_id in dag.topological_order():
        state = states[stage_id]
        if state.status is StageStatus.DONE:
            continue  # 已完成阶段不重跑（产物引用复用，调用计数不增）
        spec = dag.spec(stage_id)
        blocked = [
            dep
            for dep in spec.depends_on
            if states[dep].status in (StageStatus.FAILED, StageStatus.SKIPPED)
        ]
        if blocked or failure is not None:
            states[stage_id] = _skipped(state)
        else:
            state, outcome = _execute(spec, state, ctx, outcomes, tick)
            states[stage_id] = state
            if state.status is StageStatus.FAILED:
                failure = state
                # 失败点的下游（含传递）在保存前一次置为 skipped：任何落盘快照都自洽
                for downstream in dependents[stage_id]:
                    if states[downstream].status is not StageStatus.DONE:
                        states[downstream] = _skipped(states[downstream])
            if outcome is not None:
                outcomes[stage_id] = outcome
        record = _save(record, states, store, tick)

    if not record.stages or record.status is RunStatus.RUNNING:
        unfinished = [
            state.stage_id for state in record.stages if state.status is not StageStatus.DONE
        ]
        # 既无失败又有未完成阶段：执行器自身逻辑不自洽，如实报错（不静默收尾）
        raise OrchestrationError(
            f"执行结束但阶段既未完成也未有失败：{unfinished}（执行器内部不一致）"
        )
    return record


def _resume_base(dag: Dag, ctx: ExecutionContext, previous: RunRecord) -> RunRecord:
    """续跑前校验（拒绝时零副作用）：指纹一致 + 阶段集合一致；失败/跳过阶段重排队。"""
    if ctx.input_fingerprint != previous.input_fingerprint:
        raise ResumeRejectedError(
            "输入指纹不一致，拒绝续跑（素材已变，防止半新半旧产物）："
            f"记录 {previous.input_fingerprint}，本次 {ctx.input_fingerprint}"
        )
    if ctx.config_fingerprint != previous.config_fingerprint:
        raise ResumeRejectedError(
            "配置指纹不一致，拒绝续跑（形态配置已变）："
            f"记录 {previous.config_fingerprint}，本次 {ctx.config_fingerprint}"
        )
    if set(dag.stage_ids) != set(previous.stage_ids):
        raise ResumeRejectedError(
            "阶段集合与运行记录不一致，拒绝续跑："
            f"记录 {sorted(previous.stage_ids)}，本次 {sorted(dag.stage_ids)}"
        )
    stages = tuple(
        state.requeue() if state.status in (StageStatus.FAILED, StageStatus.SKIPPED) else state
        for state in previous.stages
    )
    return replace(
        previous,
        stages=stages,
        status=RunStatus.RUNNING,
        failure_stage="",
        failure_reason="",
        finished_at=None,
    )


def _execute(
    spec: StageSpec,
    state: StageState,
    ctx: ExecutionContext,
    outcomes: dict[str, StageOutcome],
    tick: Clock,
) -> tuple[StageState, StageOutcome | None]:
    """调用业务侧执行入口一次：成功 → done，失败 → failed（含理由与全部候选判 0 理由）。"""
    upstream = {dep: outcomes[dep] for dep in spec.depends_on if dep in outcomes}
    handoff_input = spec.handoff(upstream) if spec.handoff is not None else None
    stage_input = StageInput(
        stage_id=spec.stage_id,
        form=ctx.form,
        upstream=upstream,
        handoff_input=handoff_input,
        shared=ctx.shared,
    )
    running = state.transition_to(
        StageStatus.RUNNING,
        at=tick(),
        input_fingerprint=ctx.input_fingerprint,
    )
    try:
        outcome = spec.entrypoint(stage_input)
    except StageFailedError as exc:
        failed = running.transition_to(
            StageStatus.FAILED,
            at=tick(),
            candidates=exc.candidates,
            failure_reason=str(exc.reason),
        )
        return failed, None
    except Exception as exc:  # noqa: BLE001 - 未预期异常如实记为阶段失败，不吞不降级
        reason = f"{type(exc).__name__}: {exc}"
        failed = running.transition_to(StageStatus.FAILED, at=tick(), failure_reason=reason)
        return failed, None
    done = running.transition_to(
        StageStatus.DONE,
        at=tick(),
        products=outcome.products,
        cost_usd=running.cost_usd + outcome.cost_usd,
        candidates=outcome.candidates,
        detail=outcome.detail,
    )
    return done, outcome


def _is_failure(state: StageState) -> bool:
    return state.status is StageStatus.FAILED


def _skipped(state: StageState) -> StageState:
    # 跳过是"上游未成功"的结果（非状态机迁移）：直接置位并清空产物与透传明细
    return replace(state, status=StageStatus.SKIPPED, products=(), candidates=(), detail={})


def _outcome_of(state: StageState) -> StageOutcome:
    """由既有阶段状态重建输出视图（续跑时上游产物引用与透传明细从记录恢复）。"""
    return StageOutcome(
        products=state.products,
        cost_usd=state.cost_usd,
        candidates=state.candidates,
        detail=state.detail,
    )


def _save(
    record: RunRecord,
    states: dict[str, StageState],
    store: RunStore | None,
    tick: Clock,
) -> RunRecord:
    """按阶段状态派生运行状态（保存的记录始终自洽），可选落盘。"""
    ordered = tuple(states[stage_id] for stage_id in record.stage_ids)
    status, failure_stage, failure_reason, finished_at = _derive_status(ordered, tick)
    saved = replace(
        record,
        stages=ordered,
        status=status,
        failure_stage=failure_stage,
        failure_reason=failure_reason,
        finished_at=finished_at,
    )
    if store is not None:
        store.save(saved)
    return saved


def _dependents_map(dag: Dag) -> dict[str, tuple[str, ...]]:
    """每个阶段的传递下游（仅依赖可达者；用于失败后一次置 skipped）。"""
    direct: dict[str, list[str]] = {stage_id: [] for stage_id in dag.stage_ids}
    for stage_id in dag.stage_ids:
        for dep in dag.spec(stage_id).depends_on:
            direct[dep].append(stage_id)
    result: dict[str, tuple[str, ...]] = {}
    for stage_id in dag.stage_ids:
        seen: set[str] = set()
        frontier = list(direct[stage_id])
        while frontier:
            current = frontier.pop()
            if current in seen or current == stage_id:
                continue
            seen.add(current)
            frontier.extend(direct[current])
        result[stage_id] = tuple(sid for sid in dag.stage_ids if sid in seen)
    return result


def _derive_status(
    stages: tuple[StageState, ...], tick: Clock
) -> tuple[RunStatus, str, str, str | None]:
    """由阶段状态派生（运行状态, 失败点, 失败原因, 结束时间）。"""
    failed = next((state for state in stages if _is_failure(state)), None)
    if failed is not None:
        return RunStatus.FAILED, failed.stage_id, failed.failure_reason, tick()
    if all(state.status is StageStatus.DONE for state in stages):
        return RunStatus.DONE, "", "", tick()
    return RunStatus.RUNNING, "", "", None
