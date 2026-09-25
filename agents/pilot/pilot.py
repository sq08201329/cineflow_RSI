"""试水运行编排（功能 015 US3 / T1520，契约 C10 + 功能 018 阶段 1/2/4）。

一次试水运行 = **启动前预检**（输入下限 / 配置完整性（全部加载器）/ 预算与并行度可用 /
**体量档与时长口径一致**，不合格即拒绝且零成本零落树）→ **七环节按 DAG 执行**
（`core/orchestration` 执行器，各段调用对应 Agent 既有 loop 入口，预算/幂等/落树/评估器
版本全部沿用）→ **样片包装配**（`package.py` 五件套）。

**体量档单点解析**（功能 018 / 契约 C10）：形态配置 `pilot` 段的 `scene_count`/
`lines_per_scene`/`rehearsal`/`performance` 由 `PilotConfig` **唯一解析**（缺段/缺键即
`PrecheckError`，**不取码内默认**）——`agents/*/config.py` 不读 `pilot` 段，消费者按注入取值；
排练档生效（`status=declared` 且 `work_kind=rehearsal`）时只覆盖**体量键**，链路拓扑/交接
契约/门禁/评估器组合一行不动。生效体量快照经 `effective_volume` 落预检报告的 `pilot_volume`
段（与包面 `manifest.work_kind`/`state.volume` 同一取值来源）；`unstandardized`（未标定）与
`work_kind=real_work`（真实作品）都**不覆盖**形态原值，并如实标注（不发明数字）。

**时长口径一致性**（功能 018 / SC-012①）：`screenplay.target_duration_min × 60`、
`editing.target_duration_s`、`pilot.rehearsal.scale.target_duration_s` 与运行级
`PilotInputs.target_duration_min × 60` 必须指向同一个成片时长（容差 1e-6）——不一致即
**拒绝启动并点名两处实测值**（不静默择一、不按其一取值）。

可复现（SC-001）：注入确定性时钟 + 全模拟链路 + 样片包不含墙钟/路径，同输入同配置
两次运行逐字节一致。

**证据面**（功能 018 / C11~C13）：样片包在五件套内补**逐环节评估分量**（取自树节点原文）、
**逐环节真实/模拟标注**与**成本第三方腿**（网关记账增量）；性能画像与账本窗口口径落**报告侧**
（`run_report.py` 的 `pilot/profiles/{run_id}.json`，墙钟只在此处）。

断点续跑（C10/FR-006）：运行记录落 `pilot/runs/{run_id}.json`（每次阶段落定即保存）；
续跑前校验输入指纹（素材哈希）与配置指纹，不一致即拒绝；已完成阶段零重跑——已完成时
**复用既有样片包**（复验通过才复用，理由见 `_verified_package`）。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agents.pilot import backends as backends_module
from agents.pilot import handoffs
from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.backends import BackendAssemblyError, BackendSelection
from core.orchestration.executor import ExecutionContext
from core.orchestration.executor import run as run_dag
from core.orchestration.models import RunRecord, RunStatus, fingerprint_of

RUNS_DIRNAME = "runs"
DEFAULT_PACKAGE_DIRNAME = "packages"
# 排练档状态与作品种类（配置声明取值域）
REHEARSAL_STATUSES = ("declared", "unstandardized")
WORK_KINDS = ("rehearsal", "real_work")
PERFORMANCE_STATUSES = ("declared", "unstandardized")
# 时长口径比较容差（只吸收浮点表示误差；分钟键与秒键的折算）
DURATION_TOLERANCE_S = 1e-6


class PilotError(Exception):
    """试水运行错误基类。"""


class PrecheckError(PilotError):
    """启动前预检不合格（拒绝启动：零成本、零落树）。"""


@dataclass(frozen=True)
class RehearsalScale:
    """排练档体量声明（`status=declared` 时逐键齐备）：缩档只改这里，链路一行不动。

    时长粒度（C-01 口径）：`target_duration_s` 为**秒级浮点**（可表达 30 秒演示档），分钟键
    为**浮点分钟**（`0.5` 合法）；不变量 `target_duration_s == script_target_minutes × 60`
    （容差 `DURATION_TOLERANCE_S`）。
    """

    target_duration_s: float
    script_target_minutes: float
    script_tolerance_minutes: float
    clip_duration_seconds: float

    def to_dict(self) -> dict:
        return {
            "target_duration_s": self.target_duration_s,
            "script_target_minutes": self.script_target_minutes,
            "script_tolerance_minutes": self.script_tolerance_minutes,
            "clip_duration_seconds": self.clip_duration_seconds,
        }


@dataclass(frozen=True)
class PilotConfig:
    """形态配置 `pilot` 段的**唯一解析者**（功能 018 / 契约 C10）。

    解析范围：体量键（`scene_count`/`lines_per_scene`）、排练档（`rehearsal`）、性能门禁阈值
    （`performance`）。**缺段/缺键即 `PrecheckError`**（不取码内默认——默认值会让"配置即形态"
    变成空话，也会让"缩档只改配置"在唯一的机检面上失效）。`backend`/`llm_backend`/`overrides`
    由 `BackendSelection` 另行解析（键集与语义不同，故不复用）。
    """

    scene_count: int
    lines_per_scene: int
    rehearsal_status: str
    work_kind: str
    scale: RehearsalScale | None
    performance_status: str
    stage_seconds: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "PilotConfig":
        path = Path(config_path)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrecheckError(f"形态配置文件不可读：{path}（{exc}）") from exc
        return cls.from_dict(payload, where=str(path))

    @classmethod
    def from_dict(cls, payload: Any, *, where: str = "<mapping>") -> "PilotConfig":
        if not isinstance(payload, Mapping):
            raise PrecheckError(f"形态配置根必须为键值映射：{where}")
        section = payload.get("pilot")
        if not isinstance(section, Mapping):
            raise PrecheckError(f"形态配置缺少 pilot 段（体量档与性能阈值不可用）：{where}")
        scene_count = _require_positive_int(section.get("scene_count"), "pilot.scene_count", where)
        lines_per_scene = _require_positive_int(
            section.get("lines_per_scene"), "pilot.lines_per_scene", where
        )
        rehearsal_status, work_kind, scale = _parse_rehearsal(section.get("rehearsal"), where)
        performance_status, stage_seconds = _parse_performance(section.get("performance"), where)
        return cls(
            scene_count=scene_count,
            lines_per_scene=lines_per_scene,
            rehearsal_status=rehearsal_status,
            work_kind=work_kind,
            scale=scale,
            performance_status=performance_status,
            stage_seconds=stage_seconds,
        )

    @property
    def rehearsal_in_force(self) -> bool:
        """排练档是否生效：`declared` 且作品种类为排练（真实作品用形态原值，不缩档）。"""
        return self.rehearsal_status == "declared" and self.work_kind == "rehearsal"

    def effective_target_duration_s(self, form_value: float) -> float:
        """生效成片时长（秒）：排练档生效时取档位值，否则取形态原值。"""
        if self.rehearsal_in_force and self.scale is not None:
            return float(self.scale.target_duration_s)
        return float(form_value)

    def effective_script_target_minutes(self, form_value: float) -> float:
        """生效剧本目标时长（浮点分钟）。"""
        if self.rehearsal_in_force and self.scale is not None:
            return float(self.scale.script_target_minutes)
        return float(form_value)

    def effective_script_tolerance_minutes(self, form_value: float) -> float:
        """生效页数容差（浮点分钟）。"""
        if self.rehearsal_in_force and self.scale is not None:
            return float(self.scale.script_tolerance_minutes)
        return float(form_value)

    def effective_clip_duration_seconds(self, form_value: float) -> float:
        """生效单镜时长（秒）。"""
        if self.rehearsal_in_force and self.scale is not None:
            return float(self.scale.clip_duration_seconds)
        return float(form_value)

    def annotations(self) -> dict:
        """档位标注（预检报告/包面用的确定性视图；未标定即如实标注，不发明数字）。"""
        return {
            "rehearsal_status": self.rehearsal_status,
            "work_kind": self.work_kind,
            "scale_in_force": self.rehearsal_in_force,
            "scale": None if self.scale is None else self.scale.to_dict(),
            "source": "declared_scale" if self.rehearsal_in_force else "form_original",
            "performance_status": self.performance_status,
            "stage_seconds": dict(self.stage_seconds),
        }


def _require_positive_int(value: Any, key: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PrecheckError(f"形态配置缺少 {key}（须为 ≥1 的整数）：{where}（实际 {value!r}）")
    return int(value)


def _require_positive_number(value: Any, key: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise PrecheckError(f"形态配置缺少 {key}（须为正数）：{where}（实际 {value!r}）")
    return float(value)


def _parse_rehearsal(value: Any, where: str) -> tuple[str, str, RehearsalScale | None]:
    """排练档：`status`/`work_kind` 必需；`status=declared` 时 `scale` 逐键齐备。"""
    if not isinstance(value, Mapping):
        raise PrecheckError(f"形态配置缺少 pilot.rehearsal 段：{where}")
    status = value.get("status")
    if status not in REHEARSAL_STATUSES:
        raise PrecheckError(
            f"pilot.rehearsal.status 取值非法（{status!r}）：只接受 "
            f"{' | '.join(REHEARSAL_STATUSES)}（缺项即拒绝启动）"
        )
    work_kind = value.get("work_kind")
    if work_kind not in WORK_KINDS:
        raise PrecheckError(
            f"pilot.rehearsal.work_kind 取值非法（{work_kind!r}）：只接受 "
            f"{' | '.join(WORK_KINDS)}（缺项即拒绝启动）"
        )
    raw_scale = value.get("scale")
    if status == "unstandardized":
        # 未标定：不覆盖（形态原值在 force）；档位数字属运营侧输入，本特性不发明
        return status, work_kind, None if raw_scale is None else _parse_scale(raw_scale, where)
    if not isinstance(raw_scale, Mapping):
        raise PrecheckError(f"pilot.rehearsal.scale 缺失（status=declared 时逐键齐备）：{where}")
    return status, work_kind, _parse_scale(raw_scale, where)


def _parse_scale(raw_scale: Mapping, where: str) -> RehearsalScale:
    scale = RehearsalScale(
        target_duration_s=_require_positive_number(
            raw_scale.get("target_duration_s"), "pilot.rehearsal.scale.target_duration_s", where
        ),
        script_target_minutes=_require_positive_number(
            raw_scale.get("script_target_minutes"),
            "pilot.rehearsal.scale.script_target_minutes",
            where,
        ),
        script_tolerance_minutes=_require_positive_number(
            raw_scale.get("script_tolerance_minutes"),
            "pilot.rehearsal.scale.script_tolerance_minutes",
            where,
        ),
        clip_duration_seconds=_require_positive_number(
            raw_scale.get("clip_duration_seconds"),
            "pilot.rehearsal.scale.clip_duration_seconds",
            where,
        ),
    )
    # 档位内部一致性：秒键与浮点分钟键必须指向同一时长（缺项/漂移即拒绝启动）
    _require_same_duration(
        "pilot.rehearsal.scale.target_duration_s",
        scale.target_duration_s,
        "pilot.rehearsal.scale.script_target_minutes × 60",
        scale.script_target_minutes * 60.0,
    )
    return scale


def _parse_performance(value: Any, where: str) -> tuple[str, dict[str, float]]:
    """性能门禁阈值：`status` 必需；`declared` 时七环节逐键齐备（未标定不发明数字）。"""
    if not isinstance(value, Mapping):
        raise PrecheckError(f"形态配置缺少 pilot.performance 段：{where}")
    status = value.get("status")
    if status not in PERFORMANCE_STATUSES:
        raise PrecheckError(
            f"pilot.performance.status 取值非法（{status!r}）：只接受 "
            f"{' | '.join(PERFORMANCE_STATUSES)}（缺项即拒绝启动）"
        )
    raw = value.get("stage_seconds")
    if not isinstance(raw, Mapping):
        raise PrecheckError(f"形态配置缺少 pilot.performance.stage_seconds 段：{where}")
    unknown = sorted(set(raw) - set(stages_module.PILOT_STAGE_IDS))
    if unknown:
        raise PrecheckError(
            f"pilot.performance.stage_seconds 出现未知环节 {unknown}："
            f"可声明环节为 {list(stages_module.PILOT_STAGE_IDS)}（拼错即拒绝，不静默忽略）"
        )
    thresholds = {
        stage_id: _require_positive_number(
            raw[stage_id], f"pilot.performance.stage_seconds.{stage_id}", where
        )
        for stage_id in raw
    }
    if status == "declared":
        missing = [
            stage_id for stage_id in stages_module.PILOT_STAGE_IDS if stage_id not in thresholds
        ]
        if missing:
            raise PrecheckError(
                f"pilot.performance.stage_seconds 缺环节阈值 {missing}"
                "（status=declared 时七环节逐键齐备）："
                f"{where}"
            )
    return status, thresholds


def _require_same_duration(label_a: str, value_a: float, label_b: str, value_b: float) -> None:
    """两处时长必须一致：不一致即拒绝启动并**点名两处实测值**（不静默择一）。"""
    if abs(float(value_a) - float(value_b)) > DURATION_TOLERANCE_S:
        raise PrecheckError(
            f"成片时长口径不一致：{label_a} = {float(value_a):g} s，"
            f"{label_b} = {float(value_b):g} s"
            f"（差额 {abs(float(value_a) - float(value_b)):g} s > 容差 {DURATION_TOLERANCE_S:g}s）"
            "——两处必须指向同一成片时长，此处拒绝启动（不静默择一）"
        )


@dataclass(frozen=True)
class PilotInputs:
    """试水运行输入：题材 + 目标时长（**浮点分钟**）+ 角色 + 约束 + 立项输入。

    素材面由模拟链路派生；立项输入（`genre_bounds`/`audience`）供链首环节使用。

    `target_duration_min` 为浮点分钟（功能 018 / C-01：`0.5` = 30 秒演示档合法），预检硬校验其
    ×60 等于**生效**成片时长；`genre_bounds`/`audience` 是链首立项环节的输入来源（`run_dev_round`
    的 `_validate_inputs` 要求），**缺项即预检拒绝**（不静默补默认）。

    **输入指纹口径变更留痕**（功能 018 / plan 缺口 2）：扩展字段后 `to_dict()`/`fingerprint()`
    随之变化，既有 run_id（由指纹派生）口径因此变更——同输入同配置的重跑仍逐字节一致。
    """

    topic: str
    target_duration_min: float
    characters: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    genre_bounds: tuple[str, ...] = ()
    audience: str = ""

    def to_dict(self) -> dict:
        # 浮点分钟的确定性格式化：整数分钟写整值（避免 2 与 2.0 生成两个指纹）
        minutes = float(self.target_duration_min)
        return {
            "topic": self.topic,
            "target_duration_min": int(minutes) if minutes.is_integer() else minutes,
            "characters": list(self.characters),
            "constraints": list(self.constraints),
            "genre_bounds": list(self.genre_bounds),
            "audience": self.audience,
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
    from core.billing.budget import BudgetConfig
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
        # 链首环节的配置段（功能 018）：`dev` 与其余六段同批登记（漏一处即红）
        ("dev", lambda: stages_module.DevConfig.from_yaml(path)),
        # `pilot` 段：体量档与性能阈值（功能 018 / C10：本加载器是 `scene_count`/
        # `lines_per_scene` 两个体量键的**唯一解析者**）
        ("pilot", lambda: PilotConfig.from_yaml(path)),
        ("screenplay", lambda: stages_module.ScreenplayConfig.from_yaml(path)),
        ("storyboard", lambda: stages_module.StoryboardConfig.from_yaml(path)),
        ("visual", lambda: stages_module.VisualConfig.from_yaml(path)),
        ("sound", lambda: stages_module.SoundConfig.from_yaml(path)),
        ("editing", lambda: stages_module.EditingConfig.from_yaml(path)),
        ("promo", lambda: stages_module.PromoConfig.from_yaml(path)),
        ("pooling", lambda: PoolingConfig.from_yaml(path)),
        ("dreaming", lambda: DreamConfig.from_yaml(path)),
        ("calibration", lambda: CalibrationConfig.from_yaml(path)),
        # 020：迁移口径与可比性条件（`calibration.transfer` 六键齐备；缺项即拒绝启动——
        # `CalibrationConfig` 已必需读取，此处**显式登记**使预检清单与契约 C15/C17 一一对应）
        ("transfer", lambda: CalibrationConfig.from_yaml(path).transfer),
        ("drift", lambda: DriftConfig.from_yaml(path)),
        ("deployment", lambda: DeploymentConfig.from_yaml(path)),
        # 019：预算门禁的档位声明（缺段/缺档即拒绝启动——"忘记声明额度"不得悄悄跑通）
        ("budget", lambda: BudgetConfig.from_yaml(path)),
        # 021（C1/C11）：插件声明面的清单解析器入预检清单——删掉 `evaluators` 段即**拒绝启动**
        # （"漏声明插件清单"不得静默逃逸；缺 Agent 子键 / 缺叶子三键同样即报错）
        ("evaluators", lambda: _declared_plugins(path)),
        ("web", lambda: WebConfig.from_yaml(path)),
    ):
        try:
            loader()
        except Exception as exc:  # noqa: BLE001 - 预检统一收口：缺项即拒绝启动
            raise PrecheckError(f"配置完整性预检失败（{name} 段）：{exc}") from exc
        checked.append(name)
    # 020 口径的逐项声明完备性（C11）：cadence / 窗口口径与生效日 / 渠道档位 / 归属日生效日 /
    # 迁移六键 / "不适用"显式声明——缺任一项即拒绝启动并点名段与键（不取码内默认）
    form_clause_completeness(path)
    checked.append("form_clauses")
    # 七个环节的权重键（环节 → 配置段名走单一映射声明，不得以 stage_id 直推）
    for agent in _stage_sections():
        try:
            load_evaluator_weights(path, agent)
        except Exception as exc:  # noqa: BLE001
            raise PrecheckError(f"配置完整性预检失败（{agent} 权重）：{exc}") from exc
        checked.append(f"weights:{agent}")
    return tuple(checked)


def _stage_sections() -> tuple[str, ...]:
    """链上环节的配置段名（按阶段顺序；`STAGE_CONFIG_SECTION` 的单一消费口径）。"""
    return tuple(
        stages_module.STAGE_CONFIG_SECTION[stage_id] for stage_id in stages_module.PILOT_STAGE_IDS
    )


def _declared_plugins(config_path: Path) -> tuple[str, ...]:
    """插件声明面的清单解析（六个 Agent 逐一；缺段 / 缺子键 ⇒ 报错，不回落硬编码装配）。

    六个绑定工厂模块按**符号名静态导入**（不是第二解析路径：本函数不解析 `module:attr`
    声明，只把 `AGENT`/`SLOT_LAYOUT` 两个布局常量取回来喂给唯一装配点）。
    """
    from agents.dev.evaluators import plugins as dev_plugins
    from agents.editing.evaluators import plugins as editing_plugins
    from agents.screenplay.evaluators import plugins as screenplay_plugins
    from agents.sound.evaluators import plugins as sound_plugins
    from agents.storyboard.evaluators import plugins as storyboard_plugins
    from agents.visual.evaluators import plugins as visual_plugins
    from core.evaluators.plugin import parse_manifest

    document = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PrecheckError(f"形态配置顶层不是映射：{config_path}")
    # 缺段**先点名**（否则 `parse_manifest` 会把整份文档当成声明子树，报出难懂的"槽位非法"）
    evaluators = document.get("evaluators")
    plugins = evaluators.get("plugins") if isinstance(evaluators, Mapping) else None
    if not isinstance(plugins, Mapping) or not plugins:
        raise PrecheckError("缺插件声明段 evaluators.plugins——缺段即拒绝启动，不回落硬编码装配")
    checked: list[str] = []
    for module in (
        screenplay_plugins,
        storyboard_plugins,
        visual_plugins,
        sound_plugins,
        editing_plugins,
        dev_plugins,
    ):
        parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT)
        checked.append(f"evaluators:{module.AGENT}")
    return tuple(checked)


# 迁移面的六键（020 C15/C17；缺任一即拒绝启动，不取码内默认）
_TRANSFER_KEYS: tuple[str, ...] = (
    "basis",
    "source_forms",
    "target_forms",
    "conditions",
    "storage",
    "adoption",
)
# "不适用"的显式声明面（020 C11）：只允许两处、闭合取值域（不新造第三处）
NOT_APPLICABLE_SITES: dict[str, tuple[str, ...]] = {
    "calibration.transfer": ("source_forms", "target_forms"),
    "budget": ("channels",),
}
# 三层"未标定"标注（C11）之**段层**：承载业务数字的五段——**未标定形态**必须逐段给出
# **非空** `note` 且含「未标定」字样（缺任一段即报错并点名段名）；已标定形态不作要求
# （对已裁决的形态强贴"未标定"标签是与形态层自相矛盾的）。
UNCALIBRATED_NOTE_SEGMENTS: tuple[str, ...] = (
    "promo",
    "budget",
    "calibration",
    "pilot",
    "evaluators",
)
# `calibration.cadence_note` 的禁列（该键只登记**近似关系**，不得用它宣称已达标/已投产）
CALIBRATION_CLAIM_WORDS: tuple[str, ...] = ("已标定", "已达标", "已投产")
# 020 口径逐项机检的**项名清单**（C11 的七项表 + "不适用"显式声明面）：返回清单必须逐字等于它
# （漏一项即"声明完备性"出现盲区）——常量与返回同处本模块，测试直接读常量比对。
FORM_CLAUSE_CHECKS: tuple[str, ...] = (
    "cadence",
    "window_semantics",
    "window_change_date",
    "channels",
    "attribution_date",
    "transfer",
    "runs",
    "not_applicable",
    "uncalibrated",
)


def _clause(document: Mapping, path: str) -> Any:
    """按**点分键路径**取 020 口径取值；段/键缺失即报错并**点名段与键**（不取码内默认）。"""
    cursor: Any = document
    walked: list[str] = []
    for key in path.split("."):
        if not isinstance(cursor, Mapping) or key not in cursor:
            raise PrecheckError(
                f"020 口径声明缺失：{path}（缺段/键 {'.'.join([*walked, key])}）"
                "——缺项即拒绝启动，不取码内默认"
            )
        cursor = cursor[key]
        walked.append(key)
    return cursor


def _check_not_applicable(
    document: Mapping, segment: str, keys: tuple[str, ...], *, apply: bool
) -> None:
    """'不适用'的双向无歧义：适用 ⇒ 不得声明不适用；不适用 ⇒ 必须给出非空理由且含「不适用」。"""
    section = document.get(segment.split(".", 1)[0])
    cursor: Any = section
    for key in segment.split(".")[1:]:
        cursor = cursor[key] if isinstance(cursor, Mapping) else None
    declared = cursor.get("not_applicable") if isinstance(cursor, Mapping) else None
    if apply:
        if declared is not None:
            raise PrecheckError(
                f"020 口径：{segment} 判为**适用**，不得声明 not_applicable（双向无歧义）"
            )
        return
    if not isinstance(declared, Mapping):
        raise PrecheckError(
            f"020 口径：{segment} 判为**不适用** ⇒ 必须在 {segment}.not_applicable 给出显式理由"
            f"（键取值域 {list(keys)}；留空/省略即报错，不得用「不适用」逃避填值）"
        )
    for key in keys:
        reason = declared.get(key)
        if not isinstance(reason, str) or not reason.strip() or "不适用" not in reason:
            raise PrecheckError(
                f"020 口径：{segment}.not_applicable.{key} 必须是非空理由且含「不适用」二字，"
                f"实测 {reason!r}"
            )
    extra = sorted(set(declared) - set(keys))
    if extra:
        raise PrecheckError(
            f"020 口径：{segment}.not_applicable 只允许键 {list(keys)}，实测多出 {extra}"
        )


def form_clause_completeness(config_path: str | Path) -> tuple[str, ...]:
    """020 口径的**逐项声明完备性**（读**原始文档**，不经模型；缺项即拒绝启动并点名段与键）。

    逐项（契约 C11 的逐项表）：`calibration.period_days ∈ SUPPORTED_CADENCES`、窗口口径
    取值域单元素、窗口口径生效日非空 ISO 日期、`budget.channels.<id>.tiers` 非空且**档位不跨
    渠道串用**、`promo.attribution_date_required_since` 存在、`calibration.transfer` **六键**
    齐备、`budget.runs` 两键为约定下限内的整数、"**不适用**必须显式声明"（留空/省略即报错），
    以及**三层"未标定"标注**（形态层取值域 / 段层五段 `note` / `calibration.cadence_note`
    的近似关系——未标定形态必填且须同时含「近似」与「未标定」，已标定形态给出时不得含「近似」）。

    **按配置路径通用**（形态值只作参数透传：不按形态名分支决定要不要检查，对新形态无需改代码
    即生效）；**不**给"缺段即回落到码内默认"的兜底——缺段/缺键一律拒绝启动。
    返回项逐字等于模块常量 `FORM_CLAUSE_CHECKS`（漏项即报错）。
    """
    from datetime import date

    from core.billing.budget import BudgetConfig, declared_channels, tiers_of
    from core.calibration.periods import SUPPORTED_CADENCES, WINDOW_SEMANTICS

    path = Path(config_path)
    if not path.is_file():
        raise PrecheckError(f"形态配置不存在：{path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise PrecheckError(f"形态配置顶层不是映射：{path}")

    checked: list[str] = []
    # ① cadence：取值域**唯一入口**（不发明"双周/月"等第三档量纲；越界即报错、不回落）
    period_days = _clause(document, "calibration.period_days")
    if isinstance(period_days, bool) or period_days not in SUPPORTED_CADENCES:
        raise PrecheckError(
            f"020 口径：calibration.period_days 必须 ∈ {SUPPORTED_CADENCES}"
            f"（实测 {period_days!r}）——越界即拒绝启动，不回落日级/周级"
        )
    checked.append("cadence")
    # ② 窗口口径：取值域单元素
    window_semantics = _clause(document, "calibration.window_semantics")
    if window_semantics != WINDOW_SEMANTICS:
        raise PrecheckError(
            "020 口径：calibration.window_semantics 取值域单元素"
            f"（唯一取值 {WINDOW_SEMANTICS!r}），实测 {window_semantics!r}"
        )
    checked.append("window_semantics")
    # ③ 窗口口径生效日：非空 ISO 日期
    change_date = _clause(document, "calibration.window_semantics_change_date")
    normalized = change_date.isoformat() if isinstance(change_date, date) else change_date
    if not isinstance(normalized, str) or not normalized.strip():
        raise PrecheckError(
            f"020 口径：calibration.window_semantics_change_date 必须为非空 ISO 日期"
            f"（实测 {change_date!r}）"
        )
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise PrecheckError(
            f"020 口径：calibration.window_semantics_change_date 必须为 ISO 日期（YYYY-MM-DD），"
            f"实测 {change_date!r}"
        ) from exc
    checked.append("window_change_date")
    # ④ 渠道命名空间：档位非空且**不跨渠道串用**；不登记投放渠道 ⇒ 必须显式声明"不适用"
    budget = _clause(document, "budget")
    if not isinstance(budget, Mapping):
        raise PrecheckError("020 口径：budget 段必须为映射")
    cfg = BudgetConfig.from_yaml(path)
    specs = declared_channels(cfg)
    promo_registered = False
    seen: dict[str, str] = {}
    for spec in specs:
        tiers = tiers_of(cfg, spec.channel_id)
        if not tiers:
            raise PrecheckError(
                f"020 口径：budget.channels.{spec.channel_id}.tiers 不得为空（缺档即拒绝启动）"
            )
        for tier_id in tiers:
            owner = seen.get(tier_id)
            if owner is not None and owner != spec.channel_id:
                raise PrecheckError(
                    f"020 口径：档位 {tier_id!r} 跨渠道串用（{owner} 与 {spec.channel_id}）"
                    "——档位在渠道命名空间内唯一"
                )
            seen[tier_id] = spec.channel_id
        if spec.adapter == backends_module.PROMO_CHANNEL_ADAPTER:
            promo_registered = True
    _check_not_applicable(document, "budget", ("channels",), apply=promo_registered)
    checked.append("channels")
    # ⑤ 归属日必填口径生效日（恒适用 ⇒ 不得声明"不适用"）
    required_since = _clause(document, "promo.attribution_date_required_since")
    if not str(required_since).strip():
        raise PrecheckError("020 口径：promo.attribution_date_required_since 不得为空")
    checked.append("attribution_date")
    # ⑥ 迁移面六键齐备（缺任一即拒绝启动）；不参与跨形态互通 ⇒ 必须显式声明"不适用"
    transfer = _clause(document, "calibration.transfer")
    if not isinstance(transfer, Mapping):
        raise PrecheckError("020 口径：calibration.transfer 必须为映射")
    missing = [key for key in _TRANSFER_KEYS if key not in transfer]
    if missing:
        raise PrecheckError(
            f"020 口径：calibration.transfer 缺键 {missing}"
            f"（六键 {list(_TRANSFER_KEYS)} 齐备才可启动）"
        )
    form = str(document.get("form", "")).strip()
    transfer_forms = (
        *_as_sequence(transfer["source_forms"]),
        *_as_sequence(transfer["target_forms"]),
    )
    declared_forms_in_transfer = {str(item).strip() for item in transfer_forms}
    unshared = not (declared_forms_in_transfer - {form})
    _check_not_applicable(
        document,
        "calibration.transfer",
        ("source_forms", "target_forms"),
        apply=not unshared,
    )
    checked.append("transfer")
    # ⑥′ 运行窗口下限与断档容差（C11 第 ⑦ 项）：`min_window_days >= 1`、`gap_tolerance_days >= 0`
    # （允许 0 = 不容断档）；缺项 / 留空 / 非整数即报错并点名键路径（不取码内默认）。
    if not isinstance(_clause(document, "budget.runs"), Mapping):
        raise PrecheckError(
            "020 口径：budget.runs 必须为映射（min_window_days / gap_tolerance_days）"
        )
    for runs_key, floor in (("min_window_days", 1), ("gap_tolerance_days", 0)):
        runs_value = _clause(document, f"budget.runs.{runs_key}")
        if isinstance(runs_value, bool) or not isinstance(runs_value, int) or runs_value < floor:
            raise PrecheckError(
                f"020 口径：budget.runs.{runs_key} 必须为 ≥ {floor} 的整数"
                f"（实测 {runs_value!r}）——缺项/留空即拒绝启动，不取码内默认"
            )
    checked.append("runs")
    # ⑦ "不适用"只允许上面两处（其它段出现即报错：防用"不适用"逃避填值）
    for segment in document:
        if segment in NOT_APPLICABLE_SITES:
            continue
        section = document[segment]
        if isinstance(section, Mapping) and "not_applicable" in section:
            raise PrecheckError(
                f"020 口径：{segment} 段不得出现 not_applicable"
                f"（只允许 {sorted(NOT_APPLICABLE_SITES)}）"
            )
    checked.append("not_applicable")
    # ⑧ 三层"未标定"标注（C11）：形态层取值域 + 段层 note + `calibration.cadence_note` 的近似关系
    rehearsal_status = _clause(document, "pilot.rehearsal.status")
    if rehearsal_status not in REHEARSAL_STATUSES:
        raise PrecheckError(
            f"未标定标注（形态层）：pilot.rehearsal.status 取值非法（{rehearsal_status!r}）："
            f"只接受 {' | '.join(REHEARSAL_STATUSES)}（缺项即拒绝启动）"
        )
    cadence_note = _clause_optional(document, "calibration.cadence_note")
    if cadence_note is not None:
        if not isinstance(cadence_note, str) or not cadence_note.strip():
            raise PrecheckError(
                "未标定标注：calibration.cadence_note 给出时必须为非空字符串"
                "（留空不是声明；近似关系与未标定状态都要写清）"
            )
        for word in CALIBRATION_CLAIM_WORDS:
            if word in cadence_note:
                raise PrecheckError(
                    f"未标定标注：calibration.cadence_note 不得出现「{word}」"
                    "——该键只登记近似关系，不得用它宣称已达标"
                )
    if rehearsal_status == "unstandardized":
        # 段层：承载业务数字的五段逐段点名（缺任一段 ⇒ 报错并点名段名）
        for segment in UNCALIBRATED_NOTE_SEGMENTS:
            note = _clause(document, f"{segment}.note")
            if not isinstance(note, str) or not note.strip() or "未标定" not in note:
                raise PrecheckError(
                    f"未标定标注（段层）：{segment}.note 必须为非空且含「未标定」字样的说明"
                    f"（实测 {note!r}）——缺任一段即拒绝启动"
                )
        if cadence_note is None or "近似" not in cadence_note or "未标定" not in cadence_note:
            raise PrecheckError(
                "未标定标注：未标定形态（pilot.rehearsal.status == 'unstandardized'）"
                "必须给出 calibration.cadence_note，且**同时**含「近似」与「未标定」两处字样"
                "——所取档位与业务侧真实节律的近似关系必须写明"
            )
    elif cadence_note is not None and "近似" in cadence_note:
        raise PrecheckError(
            "未标定标注：非未标定形态给出 calibration.cadence_note 时不得含「近似」"
            "（防给已裁决的节律贴近似标签）"
        )
    checked.append("uncalibrated")
    if tuple(checked) != FORM_CLAUSE_CHECKS:  # 逐项机检不得漏项（返回清单与常量同源）
        raise PrecheckError(
            f"020 口径机检项与声明清单不一致：{tuple(checked)} != {FORM_CLAUSE_CHECKS}"
        )
    return tuple(checked)


def _clause_optional(document: Mapping, path: str) -> Any:
    """可省略口径取值的读取（缺段/缺键 ⇒ `None`；与 `_clause` 的"缺项即报错"分工明确）。"""
    cursor: Any = document
    for key in path.split("."):
        if not isinstance(cursor, Mapping) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _as_sequence(value: Any) -> tuple[Any, ...]:
    """列表型口径取值（标量 ⇒ 单元素；空/缺 ⇒ 空元组，由调用点判定适用性）。"""
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,) if value is not None else ()


def _declared_tier_limits(config_path: Path) -> dict[str, float]:
    """LLM 腿的声明额度（键 = **环节 id**）：**LLM 渠道** `tiers` 的声明值，缺段/缺档即拒绝启动。

    **不发明 agent → 环节映射**（如实登记的口径张力）：环节 id 由**调用点**声明
    （`chat(..., stage=<环节 id>)`），与 Agent 名不必一一对应（judge 一个角色覆盖四个环节、
    一个 Agent 可有多个环节），故此处按环节 id 原样登记。渠道按**装配引用**解析（C12）：
    额度是**按渠道分派**的，预检只取 LLM 渠道的档位。
    """
    from agents.pilot.backends import LLM_CHANNEL_ADAPTER
    from core.billing.budget import (
        BudgetConfig,
        channel_for_adapter,
        tiers_of,
    )

    try:
        cfg = BudgetConfig.from_yaml(config_path)
        channel_id = channel_for_adapter(cfg, LLM_CHANNEL_ADAPTER).channel_id
        tiers = tiers_of(cfg, channel_id)
    except Exception as exc:  # noqa: BLE001 - 预检统一收口：缺额度不得启动
        raise PrecheckError(f"预算不可用（budget 段）：{exc}") from exc
    if not tiers:
        raise PrecheckError("预算不可用：budget.tiers 为空（缺档即拒绝启动）")
    return {tier_id: tier.limit_usd for tier_id, tier in tiers.items()}


def precheck(
    *,
    form: str,
    config_path: str | Path,
    inputs: PilotInputs,
    data_dir: str | Path,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> dict:
    """启动前预检：输入下限 + 配置完整性 + 预算/并行度可用 + **时长口径一致**（不合格即拒绝）。

    后端面（`pilot` 段）在此**只做取值校验与如实登记**：取值非法即拒绝（与配置完整性同
    口径），但**不验凭证**——`backend: http` 而凭证缺失由装配期（`build_runtime`）显式失败，
    本报告以 `credentials_checked: false` 明确标注这条边界。

    `budgets` 的键口径（019）：平台腿按 **agent 名**登记单轮预算；LLM 腿按**环节 id**登记
    `budget.tiers` 的声明额度（agent 名与环节 id 不一一对应，故不编造映射）——链首 `dev`
    环节没有单轮预算（`DevConfig` 只有模型价目），走 LLM 腿档位分支（**不为过预检发明
    单轮预算**）。

    功能 018 新增三处前置判定：① 调用点声明的环节 id 必须在档位键集内（缺档即拒绝启动）；
    ② 两处时长口径一致（`screenplay.target_duration_min × 60` == 生效 `editing.target_duration_s`，
    含排练档覆盖后的生效值与形态原值两处，见 `_require_duration_consistency`）；
    ③ 运行级 `target_duration_min × 60` == 生效成片时长。

    报告含两个档位视图（确定性段，包面与报告同源）：
    `pilot_volume` = **生效体量快照**（`effective_volume`：档位来源 + 覆盖后的生效取值 +
    `work_kind`，与阶段 6 的 `manifest.work_kind` 同一取值来源）；`pilot_scale` =
    `PilotConfig.annotations()`（档位声明与性能阈值面的如实登记）。
    """
    if not isinstance(inputs, PilotInputs):
        raise PrecheckError(f"试水输入必须为 PilotInputs，实际为 {inputs!r}")
    if not inputs.topic:
        raise PrecheckError("输入不足：缺少题材（topic 不得为空）")
    if float(inputs.target_duration_min) <= 0:
        raise PrecheckError("输入不足：目标时长必须为正分钟（支持浮点分钟）")
    if not inputs.characters:
        raise PrecheckError("输入不足：至少需要一个角色（角色表缺失即拒绝）")
    if not inputs.genre_bounds:
        raise PrecheckError("输入不足：缺少立项题材边界（genre_bounds 不得为空）")
    if not inputs.audience:
        raise PrecheckError("输入不足：缺少立项目标受众（audience 不得为空）")
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
    pilot_config = PilotConfig.from_yaml(path)
    raw_configs = stages_module.AgentConfigs(
        dev=stages_module.DevConfig.from_yaml(path),
        screenplay=stages_module.ScreenplayConfig.from_yaml(path),
        storyboard=stages_module.StoryboardConfig.from_yaml(path),
        visual=stages_module.VisualConfig.from_yaml(path),
        sound=stages_module.SoundConfig.from_yaml(path),
        editing=stages_module.EditingConfig.from_yaml(path),
        promo=stages_module.PromoConfig.from_yaml(path),
    )
    configs = stages_module.apply_rehearsal_scale(raw_configs, pilot_config)
    _require_duration_consistency(raw_configs, configs, pilot_config, inputs)
    budgets = {}
    declared_tiers = _declared_tier_limits(path)
    _require_declared_tiers(declared_tiers)
    for agent in _stage_sections():
        # 剧本 Agent 的预算面上限在模型价目表（按 token 计费），其余为单轮预算；
        # 链首 `dev` 环节同为"无单轮预算"的 LLM 腿（档位额度按环节 id 登记）
        config = getattr(configs, agent)
        budget = getattr(config, "exploration_per_round_usd", None)
        if budget is None:
            prices = getattr(config, "model_prices", {}) or {}
            if not prices:
                raise PrecheckError(f"预算不可用：{agent} 既无单轮预算也无模型价目表（拒绝启动）")
            # 019：LLM 腿的额度按**环节**分档声明（`budget.tiers` 的键 = 各 `.chat(` 调用点声明的
            # `stage=` 取值）。Agent 名与"环节 id"**并非一一对应**（judge 一个角色覆盖四个环节、
            # 一个 Agent 可有多个环节），故此处**不发明 agent → 环节映射**：按环节 id 登记声明额度，
            # 不再写 `0.0` 占位（占位与 FR-001"额度随配置快照冻结留痕"冲突）。
            budgets.update(declared_tiers)
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
        # 排练档的**生效体量快照**（确定性段）：档位来源 + 覆盖后的生效取值 + `work_kind`
        "pilot_volume": effective_volume(raw_configs, configs, pilot_config),
        "pilot_scale": pilot_config.annotations(),
        # 剧本阶段生效的**输入来源**（FR-016：两条来源都显式声明，禁止静默择一）
        "script_input_source": _script_input_source(),
    }


def _script_input_source() -> dict:
    """剧本阶段输入来源的声明视图（单一判定 = `handoffs.script_input_mode`）。

    本链（七环节）含 `dev` ⇒ 取交接结果；既有短剧试水链无 `dev` ⇒ 回落运行级
    `pilot_inputs`——两条来源都在声明里，"生效哪一条"由链上是否含 `dev` 决定并在运行记录的
    `StageState.detail` 随机读复核（`input_source`）。
    """
    ids = stages_module.PILOT_STAGE_IDS
    return {
        "mode": handoffs.script_input_mode(ids),
        "source": "stage_table",
        "chain": list(ids),
        "note": (
            "有 dev ⇒ dev_script_handoff（取被标记条目的可移交要点，topic ← genre 改名承接）；"
            "无 dev ⇒ run_level_pilot_inputs（四键全运行级）——禁止静默择一"
        ),
    }


def _require_declared_tiers(declared_tiers: Mapping[str, float]) -> None:
    """调用点声明的环节 id 必须在档位键集内（019 口径不放宽：缺档即拒绝启动）。

    **不发明 agent ↔ 环节映射**：环节 id 由调用点声明（`chat(..., stage=<环节 id>)`），
    此处按调用点清单**逐个核对**声明额度，未声明即拒绝（`sound` 环节无 LLM 调用，如实不在清单内）。
    """
    sites = stages_module.chat_call_sites()
    missing = sorted(
        {value for _, value in sites if value is not None and value not in declared_tiers}
    )
    undeclared = [f"{location}（未声明 stage=）" for location, value in sites if value is None]
    if missing or undeclared:
        raise PrecheckError(
            "预算不可用：以下调用的环节档位未声明额度（缺档即拒绝启动，"
            f"现有档位 {sorted(declared_tiers)}）：缺档 {missing}；{undeclared}"
        )


def _require_duration_consistency(
    raw_configs: stages_module.AgentConfigs,
    configs: stages_module.AgentConfigs,
    pilot_config: PilotConfig,
    inputs: PilotInputs,
) -> None:
    """两处时长口径一致（SC-012①）：形态原值、排练档生效值、运行级三处同口径。

    - 形态原值：`screenplay.target_duration_min × 60 == editing.target_duration_s`
      （形态值只作参数透传，不绑定具体形态的取值）；
    - 生效值：排练档覆盖后的 `script_target_minutes × 60 == effective_target_duration_s`；
    - 运行级：`inputs.target_duration_min × 60 == effective_target_duration_s`。

    任一不一致即拒绝启动并**点名两处实测值**（不静默择一、不按其一取值）。生效值一律按
    **档位声明的浮点分钟**比对（不拿页数门禁折算后的整数页当分钟用）。
    """
    _require_same_duration(
        "screenplay.target_duration_min × 60（形态原值）",
        float(raw_configs.screenplay.target_duration_min) * 60.0,
        "editing.target_duration_s（形态原值）",
        float(raw_configs.editing.target_duration_s),
    )
    effective_film_s = pilot_config.effective_target_duration_s(
        raw_configs.editing.target_duration_s
    )
    _require_same_duration(
        "pilot.rehearsal 生效 screenplay 目标 × 60",
        pilot_config.effective_script_target_minutes(raw_configs.screenplay.target_duration_min)
        * 60.0,
        "生效 editing.target_duration_s",
        float(configs.editing.target_duration_s),
    )
    _require_same_duration(
        "运行级 target_duration_min × 60",
        float(inputs.target_duration_min) * 60.0,
        "生效成片时长",
        effective_film_s,
    )


def effective_volume(
    raw_configs: stages_module.AgentConfigs,
    configs: stages_module.AgentConfigs,
    pilot_config: PilotConfig,
) -> dict:
    """生效体量快照（确定性段）：档位来源 + 覆盖后的生效取值 + `work_kind`。

    **单一取值来源**（契约 C10）：预检报告的 `pilot_volume` 段与包面（阶段 6 的
    `manifest.work_kind` / `state.volume`）读**同一份**快照——"排练产物不得被标为真实作品"
    与"生效体量随配置冻结"因此只有一处口径。

    确定性：只含档位标注与生效取值（无墙钟、无路径、无进程内顺序），故同输入同配置的重跑
    逐字节一致。`effective.script_target_pages` 是**整页口径**（页数门禁 `rule.page_minutes`
    的整数页），`effective.script_target_minutes` 是档位声明的**浮点分钟**——两者不是同一量，
    故并列登记、不互推。
    """
    in_force = pilot_config.rehearsal_in_force
    return {
        "work_kind": pilot_config.work_kind,
        "rehearsal_status": pilot_config.rehearsal_status,
        "scale_in_force": in_force,
        "source": "declared_scale" if in_force else "form_original",
        "note": _scale_note(pilot_config),
        "effective": {
            "scene_count": int(pilot_config.scene_count),
            "lines_per_scene": int(pilot_config.lines_per_scene),
            "lines_per_page": int(configs.screenplay.lines_per_page),
            "target_duration_s": float(configs.editing.target_duration_s),
            "script_target_minutes": float(
                pilot_config.effective_script_target_minutes(
                    raw_configs.screenplay.target_duration_min
                )
            ),
            "script_target_pages": int(configs.screenplay.target_duration_min),
            "page_tolerance": int(configs.screenplay.page_tolerance),
            "clip_duration_seconds": float(configs.visual.clip_spec["duration_seconds"]),
        },
    }


def _scale_note(pilot_config: PilotConfig) -> str:
    """档位标注（人读面）：生效来源与"未标定 / 真实作品"的处置口径（不发明数字）。"""
    if pilot_config.work_kind == "real_work":
        return "真实作品：用形态原值，排练档不生效（不缩档）"
    if pilot_config.rehearsal_in_force:
        return "排练档生效：仅覆盖体量键（链路拓扑/交接契约/门禁/评估器组合一行不动）"
    return "排练档未标定：不覆盖形态原值（档位数字属运营侧输入，本实现不发明数字）"


def _backend_report(selection) -> dict:
    """预检里的后端声明视图（如实登记取值；凭证面不在本函数职责内）。

    功能 018 / C12：新增逐环节来源标注 `stages: {stage_id: source}`——取值走**同一**的
    `STAGE_BACKEND_SLOT` 声明（不按 `resolved` 键名与 stage_id 同名匹配推断：`script`/`dev`
    无同名键，同名匹配会 KeyError 或静默缺标注），也**不由"是否有凭证"反推**。
    """
    resolved = selection.resolved()
    return {
        "backend": selection.backend,
        "llm_backend": selection.llm_backend,
        "overrides": dict(selection.overrides),
        "resolved": resolved,
        "stages": {
            stage_id: backends_module.normalize_source(resolved[slot])
            for stage_id, slot in backends_module.STAGE_BACKEND_SLOT.items()
        },
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
    """跑一次试水：预检 → 七环节 DAG 执行 → 样片包（含运行记录落盘）。

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
    package_dir = _assemble_if_done(
        record=record, runtime=runtime, package_root=Path(data_dir) / DEFAULT_PACKAGE_DIRNAME
    )
    return PilotRun(
        run_id=run_id,
        form=form,
        record=record,
        package_dir=package_dir,
        precheck_report=report,
    )


def _assemble_if_done(*, record: RunRecord, runtime: Any, package_root: Path) -> Path | None:
    """完成后装配样片包；未完成不装配（不产半包），失败原因在运行记录里如实可查。"""
    if record.status is not RunStatus.DONE:
        return None
    return package_module.assemble_from_run(
        record=record, runtime=runtime, package_root=package_root
    )


def _verified_package(data_dir: str | Path, run_id: str) -> Path:
    """已落盘的样片包（**复验通过才复用**；缺失或不合规即拒绝）。

    为什么续跑不重装配：逐环节分量面取自**树节点**，而运行时的树库是进程内的（`sqlite`
    内存库）——跨进程续跑时已完成的环节没有节点可读，"重装配"只会把完好的证据面降级成空
    分量。按"缺项即拒绝装配"的口径，正确做法是复用既有包（复验通过）或如实拒绝（不产
    证据面缺项的包、不静默降级）。
    """
    package_dir = Path(data_dir) / DEFAULT_PACKAGE_DIRNAME / run_id
    if not package_dir.is_dir():
        raise PilotError(
            f"运行已完成但样片包缺失（{package_dir}）：逐环节分量取自树节点（进程内树库不持久），"
            "无法从运行记录复算出完整证据面——请同输入同配置重跑（run_id 幂等）或从原件复验"
        )
    package_module.verify_package(package_dir)
    return package_dir


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
    """断点续跑：读运行记录 → 校验指纹 → 续跑未完成阶段 → 复用/重出样片包。

    后端覆盖口径与 `run_pilot` 一致（缺省取配置）：续跑必须**重装配**同一份后端声明，
    否则"续跑的阶段"与"已完成的阶段"可能出自不同渠道（配置指纹会先一步拒绝这种分叉）。

    样片包口径：**已完成**（幂等续跑）⇒ 复用既有包（`verify_package` 通过才复用）；**部分完成**
    则续跑后重装配——**登记边界（如实）**：逐环节评估分量取自树节点，而运行时的树库是
    **进程内**的（`sqlite` 内存库），故跨进程的部分续跑复算不出已完成环节的分量面——
    该情形按"缺项即拒绝装配"如实拒绝（不产证据面缺项的包），不静默降级。
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
    # 已完成（幂等续跑）⇒ 复用**既有样片包**（复验通过才复用）：逐环节分量取自树节点，
    # 而树库是进程内的——重装配会把完好的证据面降级成空分量（"缺项即拒绝装配"，不静默降级）
    package_dir = (
        _verified_package(data_dir, run_id)
        if previous.status is RunStatus.DONE
        else _assemble_if_done(
            record=record, runtime=runtime, package_root=Path(data_dir) / DEFAULT_PACKAGE_DIRNAME
        )
    )
    return PilotRun(
        run_id=run_id,
        form=form,
        record=record,
        package_dir=package_dir,
        precheck_report=report,
    )
