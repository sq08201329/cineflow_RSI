"""通用编排模型（功能 015 阶段 2 / T1504，data-model 第一道工序）。

frozen dataclass + 枚举，模型层承载**可机检的硬约束**（在数据产生的最早一刻生效）：

- 状态机：阶段 `pending → running → done | failed`，失败后续跑 `failed → running`；
  完成态与跳过态不得重跑（幂等），续跑重排队只对 `failed` / `skipped` 成立；
- 产物引用非空：`done` 阶段的产物引用（工件哈希 + 节点/树引用）不可为空；
- 指纹格式：素材/配置指纹一律 BLAKE3 十六进制（64 位小写），格式非法即拒绝；
- 候选判 0 必须给理由（诚实边界：判 0 不许无据）；
- RunRecord 自洽：全部阶段 done 才可置 done；`failed` 必须有失败点，且**其后阶段
  skipped**（不做静默降级）。

**零业务概念**（宪章原则五）：本模块只认识 stage_id、依赖、执行入口引用、产物引用与
形态值（`form` 仅如实透传与记录，**不参与任何分支判断**）；形态差异全部在
`configs/*.yaml`。时间一律 ISO 字符串（与 005/010/012/014 留痕惯例一致）。
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

import blake3

from core.evaluators.errors import ValidationError

# stage_id 口径：小写字母开头的小写字母/数字/下划线（零业务语义，仅作键约束）
_STAGE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# 指纹口径：BLAKE3 十六进制全量摘要（64 位小写）
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FINGERPRINT_SEPARATOR = "\x1f"  # 分量分隔符（不产生歧义的拼接口径）


def fingerprint_of(payload: str | bytes) -> str:
    """内容的 BLAKE3 十六进制摘要（素材/配置/工件哈希的统一口径）。"""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return blake3.blake3(data).hexdigest()


def combine_fingerprints(*parts: str) -> str:
    """多分量指纹：分量须为合法指纹，按给定顺序以分隔符拼接后取摘要（顺序敏感）。"""
    for part in parts:
        _require_fingerprint("fingerprint", part)
    return fingerprint_of(_FINGERPRINT_SEPARATOR.join(parts))


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{name} 必须为非空字符串，实际为 {value!r}")


def _require_stage_id(name: str, value: object) -> None:
    if not isinstance(value, str) or not _STAGE_ID_PATTERN.match(value):
        raise ValidationError(f"{name} 必须匹配 {_STAGE_ID_PATTERN.pattern}，实际为 {value!r}")


def _require_fingerprint(name: str, value: object) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.match(value):
        raise ValidationError(f"{name} 必须为 64 位小写十六进制指纹（BLAKE3），实际为 {value!r}")


def _require_non_negative_usd(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValidationError(f"{name} 必须为非负数值（美元），实际为 {value!r}")


def _require_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} 必须为布尔值，实际为 {value!r}")


class StageStatus(StrEnum):
    """阶段状态：待执行 / 执行中 / 完成 / 失败 / 跳过（失败点的下游）。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    """运行状态：执行中 / 全部阶段完成 / 有阶段失败（其后阶段跳过）。"""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# 合法迁移表（其余组合一律拒绝——状态机可机检，不靠调用方自觉）
_LEGAL_TRANSITIONS: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.PENDING: frozenset({StageStatus.RUNNING}),
    StageStatus.RUNNING: frozenset({StageStatus.DONE, StageStatus.FAILED}),
    StageStatus.DONE: frozenset(),
    StageStatus.FAILED: frozenset({StageStatus.RUNNING}),
    StageStatus.SKIPPED: frozenset({StageStatus.PENDING}),
}


@dataclass(frozen=True)
class ProductRef:
    """产物引用：工件类型 + 引用（节点/树/文件路径）+ 内容哈希（非空约束）。"""

    kind: str
    ref: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_non_empty("kind", self.kind)
        _require_non_empty("ref", self.ref)
        _require_fingerprint("content_hash", self.content_hash)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping) -> "ProductRef":
        return cls(
            kind=payload.get("kind"),
            ref=payload.get("ref"),
            content_hash=payload.get("content_hash"),
        )


@dataclass(frozen=True)
class CandidateOutcome:
    """候选判定记录：判 0 必须给理由（失败时记录**全部**候选的判 0 理由）。"""

    candidate_id: str
    score: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("candidate_id", self.candidate_id)
        score = self.score
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise ValidationError(f"score 必须为数值，实际为 {score!r}")
        if not 0.0 <= float(score) <= 1.0:
            raise ValidationError(f"score 必须 ∈ [0, 1]，实际为 {score!r}")
        reasons = tuple(self.reasons)
        for reason in reasons:
            _require_non_empty("reasons 项", reason)
        if float(score) == 0.0 and not reasons:
            raise ValidationError("判 0 候选必须给出理由（诚实边界：判 0 不许无据）")
        object.__setattr__(self, "reasons", reasons)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping) -> "CandidateOutcome":
        return cls(
            candidate_id=payload.get("candidate_id"),
            score=payload.get("score", 0.0),
            reasons=tuple(payload.get("reasons") or ()),
        )


@dataclass(frozen=True)
class StageOutcome:
    """阶段执行入口的返回值：产物引用 + 累计成本 + 候选判定 + 透传明细。

    执行器只搬运与记录，**不解释** `detail` 的语义（业务侧自行约定键集）。
    """

    products: tuple[ProductRef, ...] = ()
    cost_usd: float = 0.0
    candidates: tuple[CandidateOutcome, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_negative_usd("cost_usd", self.cost_usd)
        object.__setattr__(self, "products", tuple(self.products))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "detail", dict(self.detail))


@dataclass(frozen=True)
class StageInput:
    """阶段执行入口的入参视图：本阶段标识 + 形态值 + 上游输出 + 输入契约映射 + 共享数据。

    零业务概念：执行器不认识任何阶段语义，只把上游输出按 stage_id 打包（业务侧用
    `handoff` 契约映射出 `handoff_input`）；`form` 与 `shared` 只如实透传，
    不参与分支判断。
    """

    stage_id: str
    form: str
    upstream: Mapping[str, StageOutcome]
    handoff_input: Any = None
    shared: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_stage_id("stage_id", self.stage_id)
        _require_non_empty("form", self.form)
        object.__setattr__(self, "upstream", dict(self.upstream))
        object.__setattr__(self, "shared", dict(self.shared))


@dataclass(frozen=True)
class StageSpec:
    """阶段定义：标识 + 依赖 + 执行入口引用 + 输入契约 + 输出工件类型（零业务语义）。

    `entrypoint` 为业务侧函数引用（阶段输入 → 阶段输出），`handoff` 为可选的输入契约
    函数（上游输出 → 本阶段输入），二者都由业务侧装配（`agents/pilot/stages.py`）。
    """

    stage_id: str
    entrypoint: Callable[[StageInput], StageOutcome]
    depends_on: tuple[str, ...] = ()
    handoff: Callable[[Mapping[str, StageOutcome]], Any] | None = None
    title: str = ""
    output_kind: str = ""

    def __post_init__(self) -> None:
        _require_stage_id("stage_id", self.stage_id)
        if not callable(self.entrypoint):
            raise ValidationError(f"entrypoint 必须可调用，实际为 {self.entrypoint!r}")
        depends = tuple(self.depends_on)
        for dep in depends:
            _require_stage_id("depends_on 项", dep)
            if dep == self.stage_id:
                raise ValidationError(f"阶段 {self.stage_id!r} 不得依赖自身")
        if self.handoff is not None and not callable(self.handoff):
            raise ValidationError(f"handoff 必须可调用或为 None，实际为 {self.handoff!r}")
        object.__setattr__(self, "depends_on", depends)


@dataclass(frozen=True)
class StageState:
    """阶段状态：状态 + 输入指纹 + 产物引用 + 候选判定 + 成本 + 起止时间 + 调用计数。"""

    stage_id: str
    status: StageStatus = StageStatus.PENDING
    input_fingerprint: str = ""
    products: tuple[ProductRef, ...] = ()
    candidates: tuple[CandidateOutcome, ...] = ()
    cost_usd: float = 0.0
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    failure_reason: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_stage_id("stage_id", self.stage_id)
        object.__setattr__(self, "status", _as_status("status", self.status, StageStatus))
        if self.input_fingerprint:
            _require_fingerprint("input_fingerprint", self.input_fingerprint)
        _require_non_negative_usd("cost_usd", self.cost_usd)
        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or (self.attempts < 0)
        ):
            raise ValidationError(f"attempts 必须为非负整数，实际为 {self.attempts!r}")
        object.__setattr__(self, "detail", dict(self.detail))
        products = tuple(self.products)
        candidates = tuple(self.candidates)
        if self.status is StageStatus.DONE:
            if not products:
                raise ValidationError(
                    f"阶段 {self.stage_id!r} 为 done 时必须带产物引用（非空约束）"
                )
            if not self.input_fingerprint:
                raise ValidationError(
                    f"阶段 {self.stage_id!r} 为 done 时必须有输入指纹（产物可追溯）"
                )
            if self.finished_at is None:
                raise ValidationError(f"阶段 {self.stage_id!r} 为 done 时必须有结束时间")
        if self.status is StageStatus.FAILED:
            _require_non_empty("failure_reason", self.failure_reason)
            if self.finished_at is None:
                raise ValidationError(f"阶段 {self.stage_id!r} 为 failed 时必须有结束时间")
        if self.status in (StageStatus.DONE, StageStatus.FAILED, StageStatus.RUNNING):
            if self.started_at is None:
                raise ValidationError(f"阶段 {self.stage_id!r} 为 {self.status} 时必须有开始时间")
        if self.status in (StageStatus.PENDING, StageStatus.SKIPPED) and self.finished_at:
            raise ValidationError(f"阶段 {self.stage_id!r} 为 {self.status} 时不得有结束时间")
        object.__setattr__(self, "products", products)
        object.__setattr__(self, "candidates", candidates)

    def can_transition_to(self, status: StageStatus) -> bool:
        return _as_status("status", status, StageStatus) in _LEGAL_TRANSITIONS[self.status]

    def transition_to(
        self,
        status: StageStatus,
        *,
        at: str | None = None,
        products: tuple[ProductRef, ...] | None = None,
        cost_usd: float | None = None,
        candidates: tuple[CandidateOutcome, ...] | None = None,
        failure_reason: str = "",
        input_fingerprint: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> "StageState":
        """状态迁移（非法组合即 `ValidationError`）；`at` 为 ISO 时间戳。

        - → running：调用计数 +1、开始时间刷新、上一次尝试的产物与失败原因清空；
        - → done：必须带产物引用与结束时间；产品成本为**累计绝对值**（由调用方给全）；
        - → failed：必须带失败原因（候选判 0 理由走 `candidates`）；
        - `detail` 为业务侧透传明细（执行器不解释），缺省沿用既有值。
        """
        status = _as_status("status", status, StageStatus)
        if not self.can_transition_to(status):
            raise ValidationError(f"阶段 {self.stage_id!r} 非法状态迁移：{self.status} → {status}")
        # 目标状态一次成型（frozen 模型逐字段校验，分两步会撞中间态约束）
        fields: dict[str, Any] = {
            "status": status,
            "input_fingerprint": (
                self.input_fingerprint if input_fingerprint is None else input_fingerprint
            ),
            "cost_usd": self.cost_usd if cost_usd is None else cost_usd,
            "detail": dict(self.detail if detail is None else detail),
        }
        if status is StageStatus.PENDING:
            # 续跑重排队：清跳过标记，调用计数与既有花费保留
            fields.update(
                products=(),
                candidates=(),
                started_at=None,
                finished_at=None,
                failure_reason="",
            )
            return replace(self, **fields)
        _require_non_empty("at", at)
        if status is StageStatus.RUNNING:
            fields.update(
                attempts=self.attempts + 1,
                started_at=at,
                finished_at=None,
                products=(),
                candidates=(),
                failure_reason="",
            )
        elif status is StageStatus.DONE:
            fields.update(
                products=tuple(products or ()),
                candidates=tuple(candidates or ()),
                finished_at=at,
                failure_reason="",
            )
        else:  # FAILED
            _require_non_empty("failure_reason", failure_reason)
            fields.update(
                products=tuple(products or ()),
                candidates=tuple(candidates or ()),
                finished_at=at,
                failure_reason=failure_reason,
            )
        return replace(self, **fields)

    def requeue(self) -> "StageState":
        """续跑重排队：`failed` / `skipped` → `pending`（已完成阶段不得重跑）。"""
        if self.status not in (StageStatus.FAILED, StageStatus.SKIPPED):
            raise ValidationError(
                f"阶段 {self.stage_id!r} 当前为 {self.status}，不得重排队"
                "（仅 failed/skipped 可续跑）"
            )
        return replace(
            self,
            status=StageStatus.PENDING,
            products=(),
            candidates=(),
            started_at=None,
            finished_at=None,
            failure_reason="",
        )

    def to_dict(self) -> dict:
        return {
            "stage_id": self.stage_id,
            "status": self.status.value,
            "input_fingerprint": self.input_fingerprint,
            "products": [product.to_dict() for product in self.products],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "cost_usd": self.cost_usd,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reason": self.failure_reason,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "StageState":
        return cls(
            stage_id=payload.get("stage_id"),
            status=_as_status("status", payload.get("status"), StageStatus),
            input_fingerprint=payload.get("input_fingerprint", ""),
            products=tuple(ProductRef.from_dict(item) for item in payload.get("products") or ()),
            candidates=tuple(
                CandidateOutcome.from_dict(item) for item in payload.get("candidates") or ()
            ),
            cost_usd=payload.get("cost_usd", 0.0),
            attempts=payload.get("attempts", 0),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            failure_reason=payload.get("failure_reason", ""),
            detail=payload.get("detail") or {},
        )


@dataclass(frozen=True)
class RunRecord:
    """一次运行的记录：运行标识 + 形态值 + 配置/输入指纹 + 各阶段状态 + 失败点。"""

    run_id: str
    form: str
    config_fingerprint: str
    input_fingerprint: str
    stages: tuple[StageState, ...]
    started_at: str
    status: RunStatus = RunStatus.RUNNING
    failure_stage: str = ""
    failure_reason: str = ""
    finished_at: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("run_id", self.run_id)
        _require_non_empty("form", self.form)
        _require_fingerprint("config_fingerprint", self.config_fingerprint)
        _require_fingerprint("input_fingerprint", self.input_fingerprint)
        _require_non_empty("started_at", self.started_at)
        stages = tuple(self.stages)
        if not stages:
            raise ValidationError("RunRecord 至少一个阶段（空运行无意义）")
        stage_ids = [state.stage_id for state in stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValidationError(f"RunRecord 阶段标识必须唯一，实际为 {stage_ids}")
        status = _as_status("status", self.status, RunStatus)
        if status is RunStatus.RUNNING:
            if self.finished_at is not None:
                raise ValidationError("运行中（running）不得带结束时间")
            if self.failure_stage or self.failure_reason:
                raise ValidationError("运行中（running）不得带失败点")
        else:
            if self.finished_at is None:
                raise ValidationError(f"运行状态 {status} 必须有结束时间")
        if status is RunStatus.DONE:
            unfinished = [s.stage_id for s in stages if s.status is not StageStatus.DONE]
            if unfinished:
                raise ValidationError(f"全部阶段 done 才可置 done，未完成：{unfinished}")
            if self.failure_stage or self.failure_reason:
                raise ValidationError("成功记录不得带失败点")
        if status is RunStatus.FAILED:
            _require_non_empty("failure_reason", self.failure_reason)
            _require_stage_id("failure_stage", self.failure_stage)
            by_id = {state.stage_id: state for state in stages}
            failing = by_id.get(self.failure_stage)
            if failing is None:
                raise ValidationError(f"失败点 {self.failure_stage!r} 不在阶段集合内")
            if failing.status is not StageStatus.FAILED:
                raise ValidationError(
                    f"失败点 {self.failure_stage!r} 的状态为 {failing.status}（须为 failed）"
                )
            downstream = stages[stage_ids.index(self.failure_stage) + 1 :]
            not_skipped = [
                state.stage_id for state in downstream if state.status is not StageStatus.SKIPPED
            ]
            if not_skipped:
                raise ValidationError(
                    f"失败点之后的阶段必须为 skipped（不静默降级），实际未跳过：{not_skipped}"
                )
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "status", status)

    @property
    def stage_ids(self) -> tuple[str, ...]:
        return tuple(state.stage_id for state in self.stages)

    @property
    def completed_stages(self) -> tuple[str, ...]:
        return tuple(state.stage_id for state in self.stages if state.status is StageStatus.DONE)

    @property
    def total_cost_usd(self) -> float:
        return sum(state.cost_usd for state in self.stages)

    def stage(self, stage_id: str) -> StageState:
        for state in self.stages:
            if state.stage_id == stage_id:
                return state
        raise ValidationError(f"运行记录中不存在阶段 {stage_id!r}")

    def with_stage(self, state: StageState) -> "RunRecord":
        if state.stage_id not in self.stage_ids:
            raise ValidationError(f"运行记录中不存在阶段 {state.stage_id!r}")
        return replace(
            self,
            stages=tuple(state if old.stage_id == state.stage_id else old for old in self.stages),
        )

    def with_stages(self, stages: tuple[StageState, ...], **overrides) -> "RunRecord":
        return replace(self, stages=tuple(stages), **overrides)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "form": self.form,
            "config_fingerprint": self.config_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "status": self.status.value,
            "failure_stage": self.failure_stage,
            "failure_reason": self.failure_reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stages": [state.to_dict() for state in self.stages],
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "RunRecord":
        return cls(
            run_id=payload.get("run_id"),
            form=payload.get("form"),
            config_fingerprint=payload.get("config_fingerprint"),
            input_fingerprint=payload.get("input_fingerprint"),
            stages=tuple(StageState.from_dict(item) for item in payload.get("stages") or ()),
            started_at=payload.get("started_at"),
            status=_as_status("status", payload.get("status"), RunStatus),
            failure_stage=payload.get("failure_stage", ""),
            failure_reason=payload.get("failure_reason", ""),
            finished_at=payload.get("finished_at"),
        )

    def dump_json(self) -> str:
        """规范化 JSON（键排序 + 2 空格缩进）：运行记录落盘的可复现口径。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _as_status(name: str, value: object, enum_class: type[StrEnum]):
    if isinstance(value, enum_class):
        return value
    try:
        return enum_class(value)
    except ValueError as exc:
        raise ValidationError(
            f"{name} 必须为 {enum_class.__name__} 成员，实际为 {value!r}"
        ) from exc
