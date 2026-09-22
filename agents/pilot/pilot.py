"""试水运行编排（功能 015 US3 / T1520，契约 C10）：预检 → DAG 执行 → 样片包。

一次试水运行 = **启动前预检**（输入下限 / 配置完整性（全部加载器）/ 预算与并行度可用，
不合格即拒绝且零成本零落树）→ **六阶段按 DAG 执行**（`core/orchestration` 执行器，
各段调用对应 Agent 既有 loop 入口，预算/幂等/落树/评估器版本全部沿用）→
**样片包装配**（`package.py` 五件套）。

可复现（SC-001）：注入确定性时钟 + 全模拟链路 + 样片包不含墙钟/路径，同输入同配置
两次运行逐字节一致。

断点续跑（C10/FR-006）：运行记录落 `pilot/runs/{run_id}.json`（每次阶段落定即保存）；
续跑前校验输入指纹（素材哈希）与配置指纹，不一致即拒绝；已完成阶段零重跑。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.backends import BackendAssemblyError, BackendSelection
from core.orchestration.executor import ExecutionContext
from core.orchestration.executor import run as run_dag
from core.orchestration.models import RunRecord, RunStatus, fingerprint_of

RUNS_DIRNAME = "runs"
DEFAULT_PACKAGE_DIRNAME = "packages"


class PilotError(Exception):
    """试水运行错误基类。"""


class PrecheckError(PilotError):
    """启动前预检不合格（拒绝启动：零成本、零落树）。"""


@dataclass(frozen=True)
class PilotInputs:
    """试水运行输入：题材 + 目标时长 + 角色 + 约束（素材面由模拟链路派生）。"""

    topic: str
    target_duration_min: int
    characters: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "target_duration_min": self.target_duration_min,
            "characters": list(self.characters),
            "constraints": list(self.constraints),
        }

    def fingerprint(self) -> str:
        return fingerprint_of(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True))


@dataclass(frozen=True)
class PilotRun:
    """一次试水运行的产物视图：运行记录 + 样片包目录 + 预检结论。"""

    run_id: str
    form: str
    record: RunRecord
    package_dir: Path | None
    precheck_report: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "form": self.form,
            "status": self.record.status.value,
            "package_dir": None if self.package_dir is None else str(self.package_dir),
            "stages": [state.to_dict() for state in self.record.stages],
        }


class FileRunStore:
    """运行记录持久化：`pilot/runs/{run_id}.json`（只增不改语义由 run_id 唯一性保证）。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.runs_dir = Path(data_dir) / RUNS_DIRNAME
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def path_of(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def save(self, record: RunRecord) -> None:
        self.path_of(record.run_id).write_text(record.dump_json() + "\n", encoding="utf-8")

    def load(self, run_id: str) -> RunRecord:
        path = self.path_of(run_id)
        if not path.is_file():
            raise PilotError(f"运行记录不存在：{path}（先跑一次再续跑）")
        return RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def config_completeness(config_path: str | Path) -> tuple[str, ...]:
    """配置完整性：全部加载器逐个通过（缺项即抛错，不静默回退）。"""
    from core.calibration.config import CalibrationConfig
    from core.calibration.drift_config import DriftConfig
    from core.deployment.config import DeploymentConfig
    from core.evaluators.weights import load_evaluator_weights
    from core.replay.pooling_models import PoolingConfig
    from dreaming.config import DreamConfig
    from web.queries import WebConfig

    path = Path(config_path)
    checked = []
    for name, loader in (
        ("screenplay", lambda: stages_module.ScreenplayConfig.from_yaml(path)),
        ("storyboard", lambda: stages_module.StoryboardConfig.from_yaml(path)),
        ("visual", lambda: stages_module.VisualConfig.from_yaml(path)),
        ("sound", lambda: stages_module.SoundConfig.from_yaml(path)),
        ("editing", lambda: stages_module.EditingConfig.from_yaml(path)),
        ("promo", lambda: stages_module.PromoConfig.from_yaml(path)),
        ("pooling", lambda: PoolingConfig.from_yaml(path)),
        ("dreaming", lambda: DreamConfig.from_yaml(path)),
        ("calibration", lambda: CalibrationConfig.from_yaml(path)),
        ("drift", lambda: DriftConfig.from_yaml(path)),
        ("deployment", lambda: DeploymentConfig.from_yaml(path)),
        ("web", lambda: WebConfig.from_yaml(path)),
    ):
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - 预检统一收口：缺项即拒绝启动
            raise PrecheckError(f"配置完整性预检失败（{name} 段）：{exc}") from exc
        checked.append(name)
    for agent in ("screenplay", "storyboard", "visual", "sound", "editing", "promo"):
        try:
            load_evaluator_weights(path, agent)
        except Exception as exc:  # noqa: BLE001
            raise PrecheckError(f"配置完整性预检失败（{agent} 权重）：{exc}") from exc
        checked.append(f"weights:{agent}")
    return tuple(checked)


def precheck(
    *,
    form: str,
    config_path: str | Path,
    inputs: PilotInputs,
    data_dir: str | Path,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> dict:
    """启动前预检：输入下限 + 配置完整性 + 预算/并行度可用（不合格即拒绝）。

    后端面（`pilot` 段）在此**只做取值校验与如实登记**：取值非法即拒绝（与配置完整性同
    口径），但**不验凭证**——`backend: http` 而凭证缺失由装配期（`build_runtime`）显式失败，
    本报告以 `credentials_checked: false` 明确标注这条边界。
    """
    if not isinstance(inputs, PilotInputs):
        raise PrecheckError(f"试水输入必须为 PilotInputs，实际为 {inputs!r}")
    if not inputs.topic:
        raise PrecheckError("输入不足：缺少题材（topic 不得为空）")
    if inputs.target_duration_min <= 0:
        raise PrecheckError("输入不足：目标时长必须为正整数分钟")
    if not inputs.characters:
        raise PrecheckError("输入不足：至少需要一个角色（角色表缺失即拒绝）")
    path = Path(config_path)
    if not path.is_file():
        raise PrecheckError(f"形态配置不存在：{path}")
    loaders = config_completeness(path)
    try:
        selection = BackendSelection.from_yaml(path).with_runtime_overrides(
            backend=backend, llm_backend=llm_backend
        )
    except BackendAssemblyError as exc:
        raise PrecheckError(f"配置完整性预检失败（pilot 后端声明）：{exc}") from exc
    configs = stages_module.AgentConfigs(
        screenplay=stages_module.ScreenplayConfig.from_yaml(path),
        storyboard=stages_module.StoryboardConfig.from_yaml(path),
        visual=stages_module.VisualConfig.from_yaml(path),
        sound=stages_module.SoundConfig.from_yaml(path),
        editing=stages_module.EditingConfig.from_yaml(path),
        promo=stages_module.PromoConfig.from_yaml(path),
    )
    budgets = {}
    for agent in ("screenplay", "storyboard", "visual", "sound", "editing", "promo"):
        # 剧本 Agent 的预算面上限在模型价目表（按 token 计费），其余五段为单轮预算
        config = getattr(configs, agent)
        budget = getattr(config, "exploration_per_round_usd", None)
        if budget is None:
            prices = getattr(config, "model_prices", {}) or {}
            if not prices:
                raise PrecheckError(f"预算不可用：{agent} 既无单轮预算也无模型价目表（拒绝启动）")
            budgets[agent] = 0.0
            continue
        if float(budget) <= 0:
            raise PrecheckError(f"预算不可用：{agent} 单轮预算 {budget} 必须为正（拒绝启动）")
        budgets[agent] = float(budget)
    replay = _replay_section(path)
    if int(replay.get("worker_count", 0)) < 1:
        raise PrecheckError("并行度不可用：replay.worker_count 必须 ≥ 1（拒绝启动）")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    return {
        "form": form,
        "config_path": str(path),
        "config_fingerprint": fingerprint_of(path.read_bytes()),
        "input_fingerprint": inputs.fingerprint(),
        "loaders": list(loaders),
        "budgets": budgets,
        "pilot_backend": _backend_report(selection),
    }


def _backend_report(selection) -> dict:
    """预检里的后端声明视图（如实登记取值；凭证面不在本函数职责内）。"""
    return {
        "backend": selection.backend,
        "llm_backend": selection.llm_backend,
        "overrides": dict(selection.overrides),
        "resolved": selection.resolved(),
        "credentials_checked": False,
        "note": (
            "precheck 只验配置完整性，**不验凭证**：声明 http 而凭证缺失将在装配期"
            "（落树/生成之前）显式失败，不静默回落模拟"
        ),
    }


def _replay_section(config_path: Path) -> dict:
    import yaml

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    replay = payload.get("replay")
    if not isinstance(replay, dict):
        raise PrecheckError("配置缺 replay 段（并行度预检不可用，拒绝启动）")
    return replay


def run_pilot(
    *,
    form: str,
    config_path: str | Path,
    inputs: PilotInputs,
    data_dir: str | Path,
    artifacts_root: str | Path | None = None,
    run_id: str | None = None,
    clock=None,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> PilotRun:
    """跑一次试水：预检 → 六阶段 DAG 执行 → 样片包（含运行记录落盘）。

    `backend` / `llm_backend`：运行时后端覆盖（缺省取形态配置 `pilot` 段）；装配在预检
    之后、DAG 之前，声明真实后端而凭证缺失即在此失败（零成本、零落树）。
    """
    report = precheck(
        form=form,
        config_path=config_path,
        inputs=inputs,
        data_dir=data_dir,
        backend=backend,
        llm_backend=llm_backend,
    )
    run_id = run_id or (
        "pilot-" + fingerprint_of(report["config_fingerprint"] + report["input_fingerprint"])[:12]
    )
    root = Path(artifacts_root) if artifacts_root is not None else Path(data_dir) / "artifacts"
    runtime = stages_module.build_runtime(
        form=form,
        config_path=config_path,
        data_dir=data_dir,
        artifacts_root=root,
        backend=backend,
        llm_backend=llm_backend,
    )
    stages_module.bind_runtime(runtime)
    store = FileRunStore(data_dir)
    ctx = ExecutionContext(
        run_id=run_id,
        form=form,
        config_fingerprint=runtime.config_fingerprint,
        input_fingerprint=inputs.fingerprint(),
        shared={"runtime": runtime, "run_id": run_id, "pilot_inputs": inputs.to_dict()},
    )
    record = run_dag(
        stages_module.build_dag_for(runtime),
        ctx,
        store=store,
        clock=clock,
    )
    package_dir = (
        package_module.assemble_from_run(
            record=record,
            runtime=runtime,
            package_root=Path(data_dir) / DEFAULT_PACKAGE_DIRNAME,
        )
        if record.status is RunStatus.DONE
        else None  # 未完成不装配（不产半包）；失败原因在运行记录里如实可查
    )
    return PilotRun(
        run_id=run_id,
        form=form,
        record=record,
        package_dir=package_dir,
        precheck_report=report,
    )


def resume_pilot(
    *,
    form: str,
    config_path: str | Path,
    inputs: PilotInputs,
    data_dir: str | Path,
    artifacts_root: str | Path | None = None,
    run_id: str,
    clock=None,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> PilotRun:
    """断点续跑：读运行记录 → 校验指纹 → 续跑未完成阶段 → 重出样片包。

    后端覆盖口径与 `run_pilot` 一致（缺省取配置）：续跑必须**重装配**同一份后端声明，
    否则"续跑的阶段"与"已完成的阶段"可能出自不同渠道（配置指纹会先一步拒绝这种分叉）。
    """
    report = precheck(
        form=form,
        config_path=config_path,
        inputs=inputs,
        data_dir=data_dir,
        backend=backend,
        llm_backend=llm_backend,
    )
    root = Path(artifacts_root) if artifacts_root is not None else Path(data_dir) / "artifacts"
    runtime = stages_module.build_runtime(
        form=form,
        config_path=config_path,
        data_dir=data_dir,
        artifacts_root=root,
        backend=backend,
        llm_backend=llm_backend,
    )
    stages_module.bind_runtime(runtime)
    store = FileRunStore(data_dir)
    previous = store.load(run_id)
    ctx = ExecutionContext(
        run_id=run_id,
        form=form,
        config_fingerprint=runtime.config_fingerprint,
        input_fingerprint=inputs.fingerprint(),
        shared={"runtime": runtime, "run_id": run_id, "pilot_inputs": inputs.to_dict()},
    )
    record = run_dag(
        stages_module.build_dag_for(runtime),
        ctx,
        resume_from=previous,
        store=store,
        clock=clock,
    )
    package_dir = (
        package_module.assemble_from_run(
            record=record,
            runtime=runtime,
            package_root=Path(data_dir) / DEFAULT_PACKAGE_DIRNAME,
        )
        if record.status is RunStatus.DONE
        else None  # 未完成不装配（不产半包）
    )
    return PilotRun(
        run_id=run_id,
        form=form,
        record=record,
        package_dir=package_dir,
        precheck_report=report,
    )
