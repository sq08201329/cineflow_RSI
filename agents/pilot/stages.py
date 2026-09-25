"""试水链的**七环节**定义与执行入口（功能 015 US3 / T1518 + 功能 018 链首插入）。

七环节（**选题立项 → 剧本 → 分镜 → 视觉 → 声音 → 剪辑 → 宣发**）各调用**对应 Agent 的既有
loop 入口**（`run_dev_round` / `run_screenplay_round` / `run_storyboard_round` / `run_round` /
`run_sound_round` / `run_editing_round` / `run_round`）——预算门禁、幂等、评估器版本、落树路径
全部沿用既有实现，**编排层不新增落树路径**（FR-011）；本模块只负责装配（引擎/树库/工件库/
网关/模拟平台）与"上游产物 → 下游输入"的交接接线（交接口径全在 `handoffs.py`）。

**七处集中声明点的一致性**（功能 018 / 契约 C1：漏一处即红）：阶段元组、阶段表、`AgentConfigs`、
运行时装配（含建表）、预检四处清单（`pilot.py`）、产物 kind 登记（`package.py`）、两形态声明
由 `declaration_mismatches`/`assert_stage_declarations` 相互绑定（装配期即校验）；`stage_id` 与
配置段名/轮次树前缀的映射是**单一映射声明**（`STAGE_CONFIG_SECTION`/`STAGE_TREE_PREFIX`）——
**不得**以 `stage_id` 直推（`script` 的配置段是 `screenplay`、轮次树前缀是 `screenplay-round-`）。

**链路本身不认识档位**（功能 018 / 契约 C10）：体量缩档只经形态配置 `pilot.rehearsal` 声明，
由 `PilotConfig` **单点解析**后随配置覆盖进各环节配置（链路拓扑/交接契约/门禁/评估器组合一行
不动）；场景数与每场景行数同样由 `PilotConfig` 唯一解析、经参数注入（码内常量退役）。

**漂移门禁在 runtime 装配一次并透传（012 → 015 接线）**：`build_runtime` 用
`DriftGate.load(<校准数据根>, DriftConfig.from_yaml(config))` 建**一个**实例存进
`PilotRuntime.drift_gate`，再由四个有 judge 层的阶段入口（script/storyboard/visual/editing）
原样传给各 loop 的 `drift_gate` 形参——judge 处于 `suspect`/`confirmed_drift` 时，
合成前一处降权/排除才在**生产路径**上真正生效（promo/sound 无 judge 层，其 loop 也不接受
该参数，故不接线：不硬塞语义不符的门禁）。校准数据根取形态配置 `web.data_dirs.calibration`
（与 web 只读视图、012/014 CLI 同源，不另立目录约定），缺声明即拒绝装配（不静默回落）。

候选语义（澄清 Q1 落地）：每个环节由既有 loop 一轮产多候选并择优（**环节内换候选**），
环节的全部候选都未达标 → `StageFailedError`（带**全部候选判 0 理由**）→ 运行终止、
下游 skipped（不静默降级）。判 0 理由取自冻结树节点的评估分量明细（判 0 的具体来源）。

**形态无关**（宪章原则五）：配置全部来自 `configs/*.yaml`（预算/规格/时长/节拍表），
本模块不做形态分支；`form` 只作参数透传。

**后端由配置装配**（A → B 一行切换）：本模块**不再出现任何实现类字面量**——六个后端
（网关 + 五环节平台适配器）全部由 `agents/pilot/backends.py` 的 `build_backends` 按形态配置
`pilot` 段**一次装配**并放进 `PilotRuntime.backends`，各阶段从 runtime 取用
（`runtime.gateway` 为 `backends.gateway` 的只读视图）。声明 `http` 而凭证缺失即在装配期
显式拒绝（先于落树/生成，零成本失败、不静默回落模拟）。

**诚实边界**：默认（`pilot` 段缺失或 `backend: simulated`）为**确定性模拟生成器**产出
（"模拟生成"标注见样片包清单），零真实凭证、零真实投放；编排产物 JSON 化后随运行记录落盘
（续跑据此恢复）。
"""

import ast
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from agents.dev.config import DevConfig
from agents.dev.db import create_jobs_schema as create_dev_jobs_schema
from agents.dev.export_slate import export_slate
from agents.dev.loop import run_dev_round
from agents.editing.config import EditingConfig
from agents.editing.db import create_render_jobs_schema as create_editing_jobs_schema
from agents.editing.edl import EditDecisionList
from agents.editing.loop import run_editing_round
from agents.editing.shots import ShotLibrary
from agents.pilot import handoffs, run_report
from agents.pilot import package as package_module
from agents.pilot.backends import (
    LLM_SLOT,
    OVERRIDE_KEYS,
    PLATFORM_SLOTS,
    STAGE_BACKEND_SLOT,
    PilotBackends,
    build_backends,
)
from agents.pilot.scale import derived_shot_count
from agents.promo.config import PromoConfig
from agents.promo.db import create_campaigns_schema
from agents.promo.loop import run_round as run_promo_round
from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.db import create_jobs_schema as create_screenplay_jobs_schema
from agents.screenplay.loop import run_screenplay_round
from agents.sound.audio import decode_wav_samples, synthesize_wav
from agents.sound.config import SoundConfig
from agents.sound.db import create_gen_jobs_schema as create_sound_jobs_schema
from agents.sound.evaluators.loudness import measure_loudness_lufs
from agents.sound.loop import run_sound_round
from agents.sound.timing import TimingSheet
from agents.storyboard.config import StoryboardConfig, require_index_capacity
from agents.storyboard.db import create_render_jobs_schema as create_storyboard_jobs_schema
from agents.storyboard.loop import run_storyboard_round
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotEntry as BoardShotEntry
from agents.storyboard.shotlist import ShotList
from agents.visual.config import VisualConfig
from agents.visual.db import create_gen_jobs_schema as create_visual_jobs_schema
from agents.visual.loop import run_round as run_visual_round
from core.billing.budget import gateway_budget_snapshot  # 019：档位快照透传
from core.calibration.drift_config import DriftConfig
from core.calibration.drift_gate import DriftGate
from core.evaluators.base import ArtifactRef
from core.llm_gateway.gateway import LLMGateway
from core.orchestration.errors import StageFailedError
from core.orchestration.models import (
    CandidateOutcome,
    ProductRef,
    StageInput,
    StageOutcome,
    StageSpec,
    fingerprint_of,
)
from core.tree.artifacts import LocalArtifactStore
from core.tree.db import create_schema
from core.tree.store import create_tree_store

if TYPE_CHECKING:  # 仅类型标注：pilot.py 运行期 import 本模块（避免循环 import）
    from agents.pilot.pilot import PilotConfig

# 七环节顺序即依赖顺序（结构上视觉与声音可并行；本特性按串行执行，依赖声明支持并行）
# 链首是 `dev`（选题立项产出 → 剧本输入交接），`script` 因此依赖 `dev`（契约 C1）。
PILOT_STAGE_IDS = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")

# 单一映射声明：stage_id → 形态配置段名（**不得**以 stage_id 直推：`script` 的段是 `screenplay`）
STAGE_CONFIG_SECTION = {
    "dev": "dev",
    "script": "screenplay",
    "storyboard": "storyboard",
    "visual": "visual",
    "sound": "sound",
    "editing": "editing",
    "promo": "promo",
}

# 单一映射声明：stage_id → 轮次树前缀（各 Agent loop 的 `round_tree_id` 口径；**不得**直推）
STAGE_TREE_PREFIX = {
    "dev": "dev",
    "script": "screenplay",
    "storyboard": "storyboard",
    "visual": "visual",
    "sound": "sound",
    "editing": "editing",
    "promo": "promo",
}

# 环节 id → 该环节的 Agent loop 模块（树前缀的**实测对照面**：声明必须与实现一致）
STAGE_LOOP_MODULES = {
    "dev": "agents.dev.loop",
    "script": "agents.screenplay.loop",
    "storyboard": "agents.storyboard.loop",
    "visual": "agents.visual.loop",
    "sound": "agents.sound.loop",
    "editing": "agents.editing.loop",
    "promo": "agents.promo.loop",
}

_SHOT_SIZE_CYCLE = ("close_up", "medium", "full", "close_up", "medium")
_DISSOLVE_MS = 400  # 同区连续镜头的叠化时长（转场规则库容差内）


@dataclass(frozen=True)
class AgentConfigs:
    """七环节各自的形态配置（同一份 `configs/*.yaml` 逐段加载；形态差异全在配置）。

    字段名即**配置段名**（`STAGE_CONFIG_SECTION` 的值域）：`script` 环节的配置段是
    `screenplay`，链首 `dev` 环节的配置段是 `dev`。
    """

    dev: DevConfig
    screenplay: ScreenplayConfig
    storyboard: StoryboardConfig
    visual: VisualConfig
    sound: SoundConfig
    editing: EditingConfig
    promo: PromoConfig


@dataclass(frozen=True)
class PilotRuntime:
    """试水运行运行时：既有基建装配（一次性）+ 各 Agent 配置 + 后端装配 + 配置指纹。"""

    form: str
    config_path: Path
    data_dir: Path
    artifacts_root: Path
    engine: Engine
    store: Any
    artifacts: LocalArtifactStore
    backends: PilotBackends
    configs: AgentConfigs
    config_fingerprint: str
    shot_plan: tuple[dict, ...]
    calibration_dir: Path
    drift_gate: DriftGate
    pilot: "PilotConfig"

    @property
    def gateway(self) -> LLMGateway:
        """LLM 网关视图（后端由 `backends` 装配；各阶段只读，不另造网关）。"""
        return self.backends.gateway


def build_runtime(
    *,
    form: str,
    config_path: str | Path,
    data_dir: str | Path,
    artifacts_root: str | Path,
    calibration_dir: str | Path | None = None,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> PilotRuntime:
    """装配运行时：SQLite 引擎（含全部既有 schema）+ 树库 + 工件库 + 后端 + 漂移门禁。

    `calibration_dir`：判据类数据根（漂移状态登记与报表同根）；缺省按形态配置
    `web.data_dirs.calibration` 解析（见 `calibration_data_dir`），显式传入可覆盖（测试用）。

    `backend` / `llm_backend`：运行时全局覆盖（CLI `--backend` / `--llm-backend`）；
    缺省取形态配置 `pilot` 段（缺段即 `simulated`/`mock`）。**后端在落树/生成之前装配**：
    声明真实后端而凭证缺失时在这里就失败（零成本、零落树）。

    形态配置 `pilot` 段的**体量档与体量键**在这里单点生效（功能 018 / C10）：`pilot` 段由
    `PilotConfig` 唯一解析（缺项即拒绝装配），排练档生效时覆盖成片时长/剧本目标/单镜时长，
    场景数与每场景行数经参数注入到镜头计划与剧本计划。索引块网格的容量下界（C8）也在此按
    **形态原值**校验——派生镜头数只读 `agents/pilot/scale.py` 的唯一公式。装配末尾校验七处
    集中声明的一致性（`assert_stage_declarations`：漏一处即拒绝启动）。
    """
    from agents.pilot import pilot as pilot_module  # 延迟导入：pilot.py 运行期 import 本模块

    config_path = Path(config_path)
    data_dir = Path(data_dir)
    artifacts_root = Path(artifacts_root)
    resolved_calibration = (
        Path(calibration_dir) if calibration_dir is not None else calibration_data_dir(config_path)
    )
    raw_configs = AgentConfigs(
        dev=DevConfig.from_yaml(config_path),
        screenplay=ScreenplayConfig.from_yaml(config_path),
        storyboard=StoryboardConfig.from_yaml(config_path),
        visual=VisualConfig.from_yaml(config_path),
        sound=SoundConfig.from_yaml(config_path),
        editing=EditingConfig.from_yaml(config_path),
        promo=PromoConfig.from_yaml(config_path),
    )
    # 唯一后端装配点（配置驱动；缺凭证即在此显式拒绝，先于任何落树/生成）。
    # 后端装配只读后端槽位与模拟器参数（不含体量键），故在排练档解析**之前**——`pilot` 段
    # 形状非法时给出的仍是后端装配的既有报错（口径不变）
    backends = build_backends(raw_configs, config_path, backend=backend, llm_backend=llm_backend)
    # 体量档单点解析（缺项即拒绝装配）+ 生效体量覆盖（链路与门禁一行不动，只换取值）
    pilot_config = pilot_module.PilotConfig.from_yaml(config_path)
    configs = apply_rehearsal_scale(raw_configs, pilot_config)
    # 索引网格容量下界（功能 018 / C8）：按**形态原值**派生镜头数校验（排练档缩的是本轮的
    # 运行体量，"该形态要能编码多少镜"这一容量义务不随之缩）；派生镜头数只读
    # `agents/pilot/scale.py` 的唯一公式——容量不足即拒绝装配并点名实测值。
    require_index_capacity(
        raw_configs.storyboard.render,
        scene_count=pilot_config.scene_count,
        target_duration_s=raw_configs.editing.target_duration_s,
        clip_duration_seconds=raw_configs.visual.clip_spec["duration_seconds"],
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)  # 001 发现树
    create_dev_jobs_schema(engine)  # 017 开发 Agent 作业表（链首环节自己的表）
    create_screenplay_jobs_schema(engine)
    create_storyboard_jobs_schema(engine)
    create_visual_jobs_schema(engine)
    create_sound_jobs_schema(engine)
    create_editing_jobs_schema(engine)
    create_campaigns_schema(engine)
    config_fingerprint = fingerprint_of(config_path.read_bytes())
    # 漂移门禁**装配一次**（012 → 015 接线）：同一实例透传给各 judge 阶段的 loop
    drift_gate = DriftGate.load(resolved_calibration, DriftConfig.from_yaml(config_path))
    runtime = PilotRuntime(
        form=form,
        config_path=config_path,
        data_dir=data_dir,
        artifacts_root=artifacts_root,
        engine=engine,
        store=create_tree_store(engine),
        artifacts=LocalArtifactStore(artifacts_root),
        backends=backends,
        configs=configs,
        config_fingerprint=config_fingerprint,
        shot_plan=build_shot_plan(configs, scene_count=pilot_config.scene_count),
        calibration_dir=resolved_calibration,
        drift_gate=drift_gate,
        pilot=pilot_config,
    )
    # 七处集中声明点的一致性（漏一处即拒绝启动，不是"少一环也能跑"）
    assert_stage_declarations(build_stage_specs(runtime), configs)
    return runtime


def apply_rehearsal_scale(configs: AgentConfigs, pilot: "PilotConfig") -> AgentConfigs:
    """排练档生效体量的单点覆盖（功能 018 / C10）：只覆盖**体量键**，链路一行不动。

    `status=declared` 且 `work_kind=rehearsal` 时按档位覆盖成片时长/剧本目标分钟/页数容差/
    单镜时长；`unstandardized`（未标定）或 `real_work`（真实作品用形态原值）**不覆盖**
    （形态原值在 force）。

    **分钟 → 整页折算**（页数门禁 `rule.page_minutes` 是整数页口径，而档位分钟键是浮点、
    可表达 30 秒演示档）：目标向上折算到 ≥1 页、容差向上取整（**整分钟档恒等**，非整分钟档
    取覆盖该档位的最近整页窗口）。**时长口径的一致性校验不拿折算后的整数页当分钟用**——
    一律按档位声明的浮点分钟比对（见 `pilot._require_duration_consistency`）。
    """
    if not pilot.rehearsal_in_force:
        return configs
    scale = pilot.scale
    return replace(
        configs,
        screenplay=replace(
            configs.screenplay,
            target_duration_min=_page_target(scale.script_target_minutes),
            page_tolerance=_page_tolerance(scale.script_tolerance_minutes),
        ),
        visual=replace(
            configs.visual,
            clip_spec={**configs.visual.clip_spec, "duration_seconds": scale.clip_duration_seconds},
        ),
        editing=replace(configs.editing, target_duration_s=scale.target_duration_s),
    )


def _page_target(minutes: float) -> int:
    """剧本目标页数（整页口径）：整分钟档恒等，非整分钟档向上折算到 ≥1 页。"""
    return max(1, math.ceil(float(minutes) - 1e-9))


def _page_tolerance(minutes: float) -> int:
    """页数容差（整页口径）：向上取整（整分钟档恒等）。"""
    return max(0, math.ceil(float(minutes) - 1e-9))


# ---------------------------------------------------------------------------
# 七处集中声明点的一致性机检（契约 C1：漏一处即红，不靠人记得）
# ---------------------------------------------------------------------------

# `.chat(` 调用点的扫描域（与 `tests/unit/test_no_vendor_literals.py` 同域：含 dreaming/、
# 不含 ops/）；网关自身是定义处，不在扫描域内
_CHAT_SCAN_ROOTS = ("agents", "dreaming", "core")
_CHAT_SCAN_EXCLUDE = ("core/llm_gateway",)


def repo_root() -> Path:
    """仓库根（本文件位于 `agents/pilot/`）。"""
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=4)
def _scan_chat_call_sites(root: str) -> tuple[tuple[str, str | None], ...]:
    """扫描实现（进程内缓存：调用点是源码静态声明，同一进程不随运行变化）。"""
    base = Path(root)
    sites: list[tuple[str, str | None]] = []
    for prefix in _CHAT_SCAN_ROOTS:
        for path in sorted((base / prefix).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(base).as_posix()
            if any(relative.startswith(excluded) for excluded in _CHAT_SCAN_EXCLUDE):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "chat"
                ):
                    continue
                declared = next((kw for kw in node.keywords if kw.arg == "stage"), None)
                value = (
                    declared.value.value
                    if declared is not None and isinstance(declared.value, ast.Constant)
                    else None
                )
                sites.append((f"{relative}:{node.lineno}", value))
    return tuple(sites)


def chat_call_sites(root: str | Path | None = None) -> tuple[tuple[str, str | None], ...]:
    """扫描域内全部 `.chat(` 调用点 → (文件:行, 声明的 `stage=` 取值 | None)。

    环节归属由**调用点**声明（网关只有四个 `role`，`judge` 一个角色覆盖四个环节）——
    本函数只做**如实扫描**，不发明 agent ↔ 环节映射。
    """
    base = Path(root) if root is not None else repo_root()
    return _scan_chat_call_sites(str(base.resolve()))


def chat_stage_ids(root: str | Path | None = None) -> tuple[str, ...]:
    """调用点声明的环节 id 取值集（去重排序；未声明或非字面量的调用点不在其列）。"""
    return tuple(sorted({value for _, value in chat_call_sites(root) if value}))


def round_tree_id_of(stage_id: str, round_id: str) -> str:
    """某环节轮次树的确定性标识（`STAGE_TREE_PREFIX` 的**单一**消费口径）。

    **不得**以 `f"{stage_id}-round-"` 直推：`script` 环节的轮次树实为
    `screenplay-round-{round_id}`（`agents/screenplay/loop.py`）。
    """
    try:
        prefix = STAGE_TREE_PREFIX[stage_id]
    except KeyError as exc:  # 未知环节即报错（不静默拼一个前缀出来）
        raise StageFailedError(f"未知环节 {stage_id!r}：无可用的轮次树前缀声明") from exc
    return f"{prefix}-round-{round_id}"


def declaration_mismatches(specs: Sequence[StageSpec], configs: AgentConfigs) -> list[str]:
    """七处集中声明点的一致性机检：返回不一致清单（空列表 = 通过）。

    ① 阶段表拓扑序 == `PILOT_STAGE_IDS`（顺序链：每环依赖前一环、链首无依赖）；
    ② `STAGE_CONFIG_SECTION` 键域 == 阶段元组、值域 == `AgentConfigs` 字段名集；
    ③ 阶段表 `output_kind` 的全部取值 ∈ 产物 kind 登记表（`package._KIND_CONTENT_TYPE`）；
    ④ `STAGE_TREE_PREFIX` 键域 == 阶段元组，且**实测前缀**与该环节 Agent loop 的
       `round_tree_id` 一致（不得以 stage_id 直推）。
    """
    mismatches: list[str] = []
    stage_ids = tuple(spec.stage_id for spec in specs)
    if stage_ids != PILOT_STAGE_IDS:
        mismatches.append(
            f"阶段表拓扑序 {stage_ids} != 阶段元组 {PILOT_STAGE_IDS}（漏同步即有环缺失）"
        )
    if set(STAGE_CONFIG_SECTION) != set(PILOT_STAGE_IDS):
        mismatches.append(
            f"STAGE_CONFIG_SECTION 键域 {sorted(STAGE_CONFIG_SECTION)} != {sorted(PILOT_STAGE_IDS)}"
        )
    config_fields = {field.name for field in fields(AgentConfigs)}
    if set(STAGE_CONFIG_SECTION.values()) != config_fields:
        mismatches.append(
            f"STAGE_CONFIG_SECTION 值域 {sorted(set(STAGE_CONFIG_SECTION.values()))} "
            f"!= AgentConfigs 字段名集 {sorted(config_fields)}"
        )
    for spec in specs:
        if spec.output_kind not in package_module._KIND_CONTENT_TYPE:
            mismatches.append(
                f"阶段 {spec.stage_id} 的产物 kind {spec.output_kind!r} 未登记内容类型"
                "（新增 kind 须在 package._KIND_CONTENT_TYPE 声明）"
            )
    if set(STAGE_TREE_PREFIX) != set(PILOT_STAGE_IDS):
        mismatches.append(
            f"STAGE_TREE_PREFIX 键域 {sorted(STAGE_TREE_PREFIX)} != {sorted(PILOT_STAGE_IDS)}"
        )
    for stage_id in PILOT_STAGE_IDS:
        module_name = STAGE_LOOP_MODULES.get(stage_id)
        if module_name is None:
            mismatches.append(f"阶段 {stage_id} 未声明对应的 Agent loop 模块（树前缀无法实测）")
            continue
        module = __import__(module_name, fromlist=["round_tree_id"])
        probe = "probe"
        expected = module.round_tree_id(probe).removesuffix(f"-round-{probe}")
        if STAGE_TREE_PREFIX.get(stage_id) != expected:
            mismatches.append(
                f"阶段 {stage_id} 的轮次树前缀 {STAGE_TREE_PREFIX.get(stage_id)!r} "
                f"!= 该 Agent loop 实测 {expected!r}（不得以 stage_id 直推）"
            )
    mismatches.extend(_evidence_declaration_mismatches())
    return mismatches


def _evidence_declaration_mismatches() -> list[str]:
    """证据面的集中声明点（功能 018 / C12/C13）：逐环节后端槽位与成本腿分级。

    与"七处清单同步"同一纪律：键域必须覆盖七环节，取值必须落在装配面/结果词的取值域内，
    且"LLM 腿专属环节"（平台槽位之外的环节）必须与成本腿分级 `llm_only` **逐环节一致**
    ——漏一处即红（逐环节标注与第三方腿都按这两张表取数）。
    """
    mismatches: list[str] = []
    if set(STAGE_BACKEND_SLOT) != set(PILOT_STAGE_IDS):
        mismatches.append(
            f"STAGE_BACKEND_SLOT 键域 {sorted(STAGE_BACKEND_SLOT)} != {sorted(PILOT_STAGE_IDS)}"
            "（逐环节来源标注无槽位 ⇒ 静默缺标注或 KeyError）"
        )
    unknown_slots = sorted(set(STAGE_BACKEND_SLOT.values()) - set(OVERRIDE_KEYS))
    if unknown_slots:
        mismatches.append(f"STAGE_BACKEND_SLOT 出现装配面未知槽位 {unknown_slots}")
    llm_stages = sorted(stage for stage, slot in STAGE_BACKEND_SLOT.items() if slot == LLM_SLOT)
    expected_llm = sorted(set(PILOT_STAGE_IDS) - set(PLATFORM_SLOTS))
    if llm_stages != expected_llm:
        mismatches.append(
            f"LLM 腿专属环节 {llm_stages} != 平台槽位之外的环节 {expected_llm}"
            "（dev/script 无平台槽位，来源只能取 LLM 腿口径）"
        )
    if run_report.STAGE_COST_LEG.keys() != STAGE_BACKEND_SLOT.keys():
        mismatches.append(
            f"STAGE_COST_LEG 键域 {sorted(run_report.STAGE_COST_LEG)} != "
            f"{sorted(STAGE_BACKEND_SLOT)}（第三方腿可比性分级须覆盖同一批环节）"
        )
    unknown_legs = sorted(set(run_report.STAGE_COST_LEG.values()) - set(run_report.COST_LEGS))
    if unknown_legs:
        mismatches.append(f"STAGE_COST_LEG 出现未知分级 {unknown_legs}")
    if run_report.STAGE_GATEWAY_COMPARISON.keys() != STAGE_BACKEND_SLOT.keys():
        mismatches.append(
            f"STAGE_GATEWAY_COMPARISON 键域 {sorted(run_report.STAGE_GATEWAY_COMPARISON)} != "
            f"{sorted(STAGE_BACKEND_SLOT)}（第三腿比较式须逐环节声明，缺项不得静默放过）"
        )
    unknown_comparisons = sorted(
        set(run_report.STAGE_GATEWAY_COMPARISON.values()) - set(run_report.GATEWAY_COMPARISONS)
    )
    if unknown_comparisons:
        mismatches.append(f"STAGE_GATEWAY_COMPARISON 出现未知比较式 {unknown_comparisons}")
    llm_only = sorted(
        stage for stage, leg in run_report.STAGE_COST_LEG.items() if leg == "llm_only"
    )
    if llm_only != llm_stages:
        mismatches.append(
            f"成本腿分级 llm_only {llm_only} != LLM 腿专属环节 {llm_stages}"
            "（LLM 腿专属环节才可断言逐项相等）"
        )
    return mismatches


def assert_stage_declarations(specs: Sequence[StageSpec], configs: AgentConfigs) -> None:
    """一致性断言：不一致即拒绝启动（装配期，零成本零落树）。"""
    mismatches = declaration_mismatches(specs, configs)
    if mismatches:
        raise StageFailedError(
            "七环节集中声明不一致（漏一处即红，拒绝装配）：\n"
            + "\n".join(f"- {item}" for item in mismatches)
        )


# 校准数据根在形态配置中的声明位置：`web.data_dirs.calibration`（010/012 消费方同源）
_CALIBRATION_DIR_KEY = "calibration"


def calibration_data_dir(config_path: str | Path) -> Path:
    """判据类数据根（`drift/status/`、报表与快照所在目录）。

    与既有消费方（web 只读视图、012/014 CLI、demo 脚本）**同源**：取形态配置
    `web.data_dirs.calibration`；相对路径按"形态配置所在目录的上一级"（仓库根）解析——
    `configs/*.yaml` 里的 `calibration` 即仓库根下的判据数据目录。缺段/缺键即拒绝装配
    （不静默回落默认目录：读错数据根会让降权/排除凭空失效，且违反原则五"配置即形态"）。
    """
    path = Path(config_path)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StageFailedError(f"形态配置文件不可读：{path}（{exc}）") from exc
    web = payload.get("web") if isinstance(payload, Mapping) else None
    raw = web.get("data_dirs") if isinstance(web, Mapping) else None
    declared = raw.get(_CALIBRATION_DIR_KEY) if isinstance(raw, Mapping) else None
    if not isinstance(declared, str) or not declared:
        raise StageFailedError(
            f"形态配置缺少 web.data_dirs.calibration（判据类数据根，012 漂移门禁注册表来源）："
            f"{path}——缺声明时拒绝装配（不静默回落默认目录）"
        )
    root = path.resolve().parent.parent
    return Path(declared) if Path(declared).is_absolute() else (root / declared)


def build_shot_plan(configs: AgentConfigs, *, scene_count: int) -> tuple[dict, ...]:
    """由形态配置派生镜头计划：镜头数 = **派生镜头数**（`agents/pilot/scale.py` 的唯一公式）。

    `scene_count` 由 `PilotConfig` 唯一解析后注入（码内体量常量已退役）；成片时长与单镜时长
    取**排练档覆盖后**的生效配置值。
    """
    clip_ms = int(configs.visual.clip_spec["duration_seconds"] * 1000)
    total = derived_shot_count(
        scene_count=scene_count,
        target_duration_s=configs.editing.target_duration_s,
        clip_duration_seconds=configs.visual.clip_spec["duration_seconds"],
    )
    plan = []
    for index in range(total):
        size = _SHOT_SIZE_CYCLE[index % len(_SHOT_SIZE_CYCLE)]
        plan.append(
            {
                "shot_size": size,
                "camera": "eye_level" if index % 2 == 0 else "low_angle",
                "movement": "static" if index % 3 == 0 else "dolly",
                "side": "A",
                "est_duration_ms": clip_ms,
            }
        )
    return tuple(plan)


# ---------------------------------------------------------------------------
# 候选判定（判 0 必须给理由；全败才 failed）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateSet:
    """环节候选集：全部达标才算成功；任一不足或判 0 即给失败原因（含全部理由）。

    **判败逻辑在构造期**（`__post_init__`）而不是在 `candidate_set()` 里：真实故障（功能 016
    收尾）——某个阶段入口直接构造 `CandidateSet(candidates, expected=1)` 时 `failure_reason`
    恒为空，`_require_ok` 形同虚设，产物未成功也一路走到下游，抛出的却是与业务无关的
    `TypeError: expected string or bytes-like object, got 'NoneType'`（`artifacts.get(None)`），
    失败原因里没有任何业务信息。构造即判后，**任何构造点都无法绕过**。
    """

    candidates: tuple[CandidateOutcome, ...]
    expected: int
    failure_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if not self.failure_reason:
            object.__setattr__(
                self, "failure_reason", _failure_reason(self.candidates, self.expected)
            )

    @property
    def all_ok(self) -> bool:
        return not self.failure_reason


def _failure_reason(candidates: Sequence[CandidateOutcome], expected: int) -> str:
    """候选集判败原因（计数不足 / 有判 0 候选）：唯一口径，构造期与汇总函数共用。"""
    if len(candidates) < expected:
        return f"环节候选数量 {len(candidates)} != 期望 {expected}（不足即判败）"
    judged_zero = [
        f"{candidate.candidate_id}：{'；'.join(candidate.reasons)}"
        for candidate in candidates
        if candidate.score <= 0.0
    ]
    if judged_zero:
        return "候选全部未达标（判 0 理由）：" + "；".join(judged_zero)
    return ""


def candidate_set(nodes: Sequence[Any], *, expected: int | None = None) -> CandidateSet:
    """由冻结树节点汇总候选：score 与判 0 理由（分量级）逐条落记录。"""
    node_list = list(nodes)
    want = len(node_list) if expected is None else int(expected)
    candidates = tuple(_candidate(node) for node in node_list)
    # 判败原因由 CandidateSet 构造期统一给出（此处不再另写一份，避免两套口径）
    return CandidateSet(candidates=candidates, expected=want)


def _candidate(node: Any) -> CandidateOutcome:
    score = 0.0 if node.score is None else float(node.score)
    reasons: tuple[str, ...] = ()
    if score <= 0.0:
        zero_components = [
            f"{key} 判 0"
            for key, fragment in (node.eval_breakdown or {}).items()
            if isinstance(fragment, Mapping) and float(fragment.get("score", 0.0)) <= 0.0
        ]
        reasons = tuple(zero_components) or (f"节点状态 {node.status}（未达发布线）",)
    return CandidateOutcome(candidate_id=node.node_id, score=score, reasons=reasons)


def _round_candidates(runtime: PilotRuntime, tree_id: str, *, expected: int) -> CandidateSet:
    nodes = sorted(
        (node for node in runtime.store.nodes_of(tree_id) if node.depth >= 1),
        key=lambda node: node.node_id,
    )
    return candidate_set(nodes, expected=expected)


def _job_succeeded(job: Mapping) -> bool:
    """阶段作业是否成功：**以产物哈希为准**（不看状态词）。

    为什么不用状态词：各 Agent 的成功状态词不同（screenplay 用 `inserted`、声音/视觉用
    `ingested`/`generated`…），这里曾写死 `job.get("status") == "ok"`——**恒假**：成功作业
    被判 0，失败作业也判 0，而 `CandidateSet` 当时又不自带判败原因，于是"剧本阶段"门禁形同
    虚设，失败一路走到 `artifacts.get(None)`，抛出与业务无关的 `TypeError`（真实跑批故障）。
    判据改为"有没有产出可寻址工件"：与各 Agent 的状态词解耦，失败原因由 `reason` 给出。
    """
    return bool(job.get("artifact_hash"))


def _job_failure_text(job: Mapping) -> str:
    """作业失败的可读原因：优先 Agent 给的 `reason`，否则回落到状态词（不编造）。"""
    reason = str(job.get("reason") or "").strip()
    status = job.get("status")
    return (
        f"阶段产出未成功（status={status}）：{reason}"
        if reason
        else f"阶段产出未成功（status={status}）"
    )


def _job_label(job: Mapping) -> str:
    """环节明细项标识（各 Agent 的 job/clip/material 命名不同，统一取第一个可用键）。"""
    for key in ("clip_id", "job_id", "material_id"):
        if job.get(key):
            return str(job[key])
    return "?"


def _require_ok(label: str, outcome: CandidateSet, *, jobs: Sequence[Mapping] = ()) -> None:
    if outcome.all_ok:
        return
    detail = "；".join(
        f"{_job_label(job)}: {job.get('reason')}"
        for job in jobs
        if job.get("status") in {"failed", "rejected"} and job.get("reason")
    )
    suffix = f"（环节明细：{detail}）" if detail else ""
    raise StageFailedError(
        f"{label}：{outcome.failure_reason}{suffix}", candidates=outcome.candidates
    )


# ---------------------------------------------------------------------------
# 各阶段执行入口
# ---------------------------------------------------------------------------


def build_dag_for(runtime: PilotRuntime):
    """构建试水链依赖图（七环节顺序链）。"""
    from core.orchestration.dag import build_dag

    return build_dag(build_stage_specs(runtime))


def _runtime_of(stage_input: StageInput) -> PilotRuntime:
    runtime = stage_input.shared.get("runtime")
    if not isinstance(runtime, PilotRuntime):
        raise StageFailedError("阶段输入缺少运行时装配（shared['runtime']）")
    return runtime


def _round_id(runtime: PilotRuntime, stage_input: StageInput) -> str:
    return f"{stage_input.shared.get('run_id', 'run')}-{stage_input.stage_id}"


def _product(kind: str, ref: str, content_hash: str) -> ProductRef:
    return ProductRef(kind=kind, ref=ref, content_hash=content_hash)


# `dev`（立项）环节的运行级输入映射（**显式声明**：逐字段可追溯，禁止未声明直通）
# 键 = `agents/dev/loop.py` 的 `_validate_inputs` 要求，来源 = 运行级输入字段
DEV_INPUT_FIELDS = {
    "genre_bounds": "pilot_inputs.genre_bounds",
    "audience": "pilot_inputs.audience",
}


def dev_inputs_of(pilot_inputs: Mapping) -> dict:
    """立项环节输入：由**显式声明的运行级映射**产生（缺来源即拒绝，不静默补默认）。"""
    inputs: dict[str, Any] = {}
    for field, source in DEV_INPUT_FIELDS.items():
        value = pilot_inputs.get(field)
        if not value:
            raise StageFailedError(
                f"立项环节输入缺失：{field}（来源 {source}）——运行级输入未声明该字段即拒绝启动"
            )
        inputs[field] = list(value) if isinstance(value, (list, tuple)) else str(value)
    return inputs


def _dev_entry(stage_input: StageInput) -> StageOutcome:
    """链首立项环节：既有 `run_dev_round`（零新增落树路径，落树/幂等/对账全部沿用）。

    策略装载（静态检查前置 → 版本核验 → 实例化，带**执行超时**与**零环境对象守护**）在同一
    实现处 `agents/dev/policy_loader.py`；版本取形态配置部署指针
    `deployment.dev.current_policy_version`——缺指针或源码不存在 ⇒ 拒绝启动（**不**回落
    "最新/第一条"策略）。`artifact_hash` 为空（执行前拒绝/失败）即判**阶段失败**并点名原因，
    不带着空工件往下走。
    """
    from agents.dev.artifact import TopicSlate
    from agents.dev.policy_loader import load_deployed_policy

    runtime = _runtime_of(stage_input)
    round_id = _round_id(runtime, stage_input)
    loaded = load_deployed_policy(runtime.config_path)
    result = run_dev_round(
        round_id=round_id,
        policy=loaded,  # 只喂 plan(inputs, config)：不交付任何环境句柄（例外义务③）
        store=runtime.store,
        artifacts=runtime.artifacts,
        engine=runtime.engine,
        gateway=runtime.gateway,
        config=runtime.configs.dev,
        inputs=dev_inputs_of(stage_input.shared.get("pilot_inputs") or {}),
        evaluators=None,  # 真实四评估器装配（两门禁 + 两确定性代理）
    )
    job = dict(result.job)
    artifact_hash = job.get("artifact_hash")
    candidate_id = str(job.get("job_id") or round_id)
    outcome = CandidateSet(
        candidates=(
            CandidateOutcome(
                candidate_id=candidate_id,
                score=1.0 if artifact_hash else 0.0,
                reasons=() if artifact_hash else (_job_failure_text(job),),
            ),
        ),
        expected=1,
    )
    _require_ok("立项环节", outcome, jobs=(job,))
    slate_hash = str(artifact_hash)
    slate = TopicSlate.from_dict(json.loads(runtime.artifacts.get(slate_hash)))
    return StageOutcome(
        products=(_product("slate", slate_hash, slate_hash),),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "artifact_hash": slate_hash,
            "policy_version": result.policy_version,
            "entry_count": len(slate.entries),
            "production_marks": list(slate.production_marks),
            "cost_reconciliation": dict(result.cost_reconciliation),
            "spent_usd": result.spent_usd,
        },
    )


def _script_entry(stage_input: StageInput) -> StageOutcome:
    """剧本阶段：既有 `run_screenplay_round`（三阶段产出，节拍表与页数门禁沿用配置）。

    输入来源按上游**择一且显式声明**（FR-016 / 契约 C5）：上游有 `dev` ⇒ 取"本轮进入生产"
    标记指向条目的可移交要点（`topic ← genre` 改名承接，`constraints`/`characters` 同名承接）；
    无 `dev` ⇒ 回落运行级 `pilot_inputs`。生效来源与逐字段取数依据落 `StageState.detail`
    （随机读可见），**禁止静默择一**——传给剧本 loop 的输入视图**恰为声明读取集**
    （禁止未声明直通）。
    """
    runtime = _runtime_of(stage_input)
    config = runtime.configs.screenplay
    round_id = _round_id(runtime, stage_input)
    pilot_inputs = dict(stage_input.shared.get("pilot_inputs") or {})
    dev_detail = stage_input.handoff_input
    if dev_detail is None:
        view = handoffs.run_level_script_inputs(pilot_inputs)
    else:
        slate = _load_slate(runtime, dev_detail.get("artifact_hash"))
        view = handoffs.dev_to_script_inputs(export_slate(slate), pilot_inputs)
    inputs = dict(view.inputs)
    # 运行级分钟（浮点，可表达 30 秒档）→ 剧本环节的**整数分钟**口径：取生效剧本目标
    # （排练档折算后的整页口径，与页数门禁同源）；运行级浮点分钟与生效成片时长的一致性
    # 已在预检硬校验（`precheck` 的 `_require_duration_consistency`）
    inputs["target_duration_min"] = int(config.target_duration_min)
    result = run_screenplay_round(
        round_id=round_id,
        policy=_ScreenplayPolicy(
            config=config,
            inputs=inputs,
            scene_count=runtime.pilot.scene_count,
            lines_per_scene=runtime.pilot.lines_per_scene,
        ),
        store=runtime.store,
        artifacts=runtime.artifacts,
        engine=runtime.engine,
        gateway=runtime.gateway,
        config=config,
        inputs=inputs,
        drift_gate=runtime.drift_gate,  # 012 漂移门禁（装配一次、逐段透传）
    )
    script_jobs = [job for job in result.jobs if job.get("stage") == "script"]
    candidates = tuple(
        CandidateOutcome(
            candidate_id=job["job_id"],
            score=1.0 if _job_succeeded(job) else 0.0,
            reasons=() if _job_succeeded(job) else (_job_failure_text(job),),
        )
        for job in script_jobs
    )
    outcome = CandidateSet(candidates=candidates, expected=1)
    _require_ok("剧本阶段", outcome, jobs=script_jobs)
    # 剧本工件 → 008 段落（复用 009 导出 + 双向字段锁定）
    artifact_hash = script_jobs[-1]["artifact_hash"]
    artifact = _load_script_artifact(runtime, artifact_hash)
    segment = handoffs.script_to_segment(artifact)
    return StageOutcome(
        products=(_product("script", artifact_hash, artifact_hash),),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "artifact_hash": artifact_hash,
            "segment": segment.to_dict(),
            "line_ids": list(segment.line_ids()),
            "key_line_ids": list(segment.key_line_ids()),
            # 生成成本对账（运营表⨯树节点⨯评估器计费）：第三方腿的桥（judge 计费只进树节点）
            "cost_reconciliation": dict(result.cost_reconciliation),
            # 生效输入来源 + 逐字段取数依据（FR-016：两条来源都显式声明，随机读可见）
            **view.to_detail(),
            "spent_usd": result.spent_usd,
        },
    )


def _load_slate(runtime: PilotRuntime, artifact_hash: Any):
    """按哈希取回链首的立项组合工件（产物缺失即给业务化报错，不抛裸 TypeError）。"""
    from agents.dev.artifact import TopicSlate

    if not isinstance(artifact_hash, str) or not artifact_hash:
        raise StageFailedError(
            f"链首立项产物哈希缺失（artifact_hash={artifact_hash!r}）：交接取数入口不可用，"
            "按判败处理（不继续下游，也不对 None 做哈希/正则）"
        )
    return TopicSlate.from_dict(json.loads(runtime.artifacts.get(artifact_hash)))


def _load_script_artifact(runtime: PilotRuntime, artifact_hash: str):
    """按哈希取回剧本工件（**先校验哈希**）：产物缺失时给业务化报错，不抛裸 TypeError。"""
    from agents.screenplay.artifact import ScriptArtifact

    if not isinstance(artifact_hash, str) or not artifact_hash:
        raise StageFailedError(
            f"剧本阶段产物哈希缺失（artifact_hash={artifact_hash!r}）：该阶段未产出可寻址工件，"
            "按判败处理（不继续下游，也不对 None 做哈希/正则）"
        )
    return ScriptArtifact.from_dict(json.loads(runtime.artifacts.get(artifact_hash)))


def _storyboard_entry(stage_input: StageInput) -> StageOutcome:
    """分镜阶段：既有 `run_storyboard_round`（镜头语法/覆盖/轴规则门禁沿用配置）。

    产物两项、标签如实：`animatic` = 节点工件（renderer 产出的**预演 mp4**）；
    `shotlist` = 分镜清单 canonical JSON **另立内容寻址工件**（此前只留在 detail 里，
    且被误标为 shotlist 的其实是 mp4——标签与内容不符会误导评审）。
    """
    runtime = _runtime_of(stage_input)
    config = runtime.configs.storyboard
    round_id = _round_id(runtime, stage_input)
    segment: ScriptSegment = stage_input.handoff_input
    shotlist = build_shotlist(runtime, segment)
    result = run_storyboard_round(
        round_id=round_id,
        policy=_StoryboardPolicy(shotlists=(shotlist,)),
        store=runtime.store,
        artifacts=runtime.artifacts,
        adapter=runtime.backends.storyboard,
        engine=runtime.engine,
        config=config,
        inputs={"script": segment},
        gateway=runtime.gateway,
        drift_gate=runtime.drift_gate,  # 012 漂移门禁（装配一次、逐段透传）
    )
    outcome = _round_candidates(runtime, result.tree_id, expected=1)
    _require_ok("分镜阶段", outcome, jobs=result.jobs)
    node = _winner_node(runtime, result.tree_id)
    shotlist_payload = shotlist.to_dict()
    # 分镜清单落工件库（canonical JSON 内容寻址；与 008 `shotlist_hash()` 同口径）
    shotlist_hash = runtime.artifacts.put(shotlist.canonical_json().encode())
    return StageOutcome(
        products=(
            _product("animatic", node.artifact_hash, node.artifact_hash),
            _product("shotlist", shotlist_hash, shotlist_hash),
        ),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "artifact_hash": node.artifact_hash,
            "shotlist": shotlist_payload,
            "shot_count": len(shotlist.shots),
            "segment": segment.to_dict(),  # 剧本段落随明细透传（下游声音阶段取台词）
            "spent_usd": result.spent_usd,
        },
    )


def build_shotlist(runtime: PilotRuntime, segment: ScriptSegment) -> ShotList:
    """按剧本与配置派生分镜：每场景≥1 镜、关键行逐条承接、景别交替（语法规则内）。"""
    plan = runtime.shot_plan
    per_scene = max(1, len(plan) // max(1, len(segment.scenes)))
    shots: list[BoardShotEntry] = []
    index = 0
    for scene in segment.scenes:
        scene_lines = list(scene.line_ids())
        key_lines = list(scene.key_line_ids())
        for offset in range(per_scene):
            template = plan[index % len(plan)]
            # 每镜必须承接至少一行（覆盖率门禁）；首镜优先承接本场景全部关键行
            if offset == 0 and key_lines:
                covers = key_lines
            else:
                covers = [scene_lines[offset % len(scene_lines)]]
            shots.append(
                BoardShotEntry(
                    shot_id=f"shot-{index + 1}",
                    scene_id=scene.scene_id,
                    covers=covers,
                    shot_size=template["shot_size"],
                    camera=template["camera"],
                    side=template["side"],
                    movement=template["movement"],
                    est_duration_ms=template["est_duration_ms"],
                    alternatives=1,
                )
            )
            index += 1
    return ShotList(shots=tuple(shots))


def _visual_entry(stage_input: StageInput) -> StageOutcome:
    """视觉阶段：既有 `run_round`（格式合规门禁 + 生成预算 + 工件内容寻址）。"""
    runtime = _runtime_of(stage_input)
    config = runtime.configs.visual
    round_id = _round_id(runtime, stage_input)
    params_list = stage_input.handoff_input or ()
    adapter = runtime.backends.visual
    result = run_visual_round(
        round_id=round_id,
        policy=_VisualPolicy(params_list=params_list),
        store=runtime.store,
        artifacts=runtime.artifacts,
        adapter=adapter,
        gateway=runtime.gateway,
        engine=runtime.engine,
        config=config,
        drift_gate=runtime.drift_gate,  # 012 漂移门禁（装配一次、逐段透传）
    )
    outcome = _round_candidates(runtime, result.tree_id, expected=len(params_list))
    _require_ok("视觉阶段", outcome, jobs=result.clips)
    clips = []
    for node in _nodes_by_shot(runtime, result.tree_id):
        params = (node.observation_context or {}).get("gen_params", {})
        clips.append(
            {
                "shot_id": str(params.get("shot_id", node.node_id)),
                "scene_id": str(params.get("scene_id", "scene-1")),
                "artifact_hash": node.artifact_hash,
                "duration_ms": int(float(params.get("duration_s", 0.0)) * 1000),
                "gen_params": dict(params),
                "score": float(node.score or 0.0),
            }
        )
    return StageOutcome(
        products=tuple(
            _product("clip", clip["artifact_hash"], clip["artifact_hash"]) for clip in clips
        ),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "clips": clips,
            "clip_count": len(clips),
            "gen_params": [dict(params) for params in params_list],
            "segment": dict(stage_input.upstream["storyboard"].detail.get("segment") or {}),
            "spent_usd": result.spent_usd,
        },
    )


def _sound_entry(stage_input: StageInput) -> StageOutcome:
    """声音阶段：既有 `run_sound_round`（响度/口型同步门禁 + 三类型成本分账）。"""
    runtime = _runtime_of(stage_input)
    config = runtime.configs.sound
    round_id = _round_id(runtime, stage_input)
    timing_sheet = stage_input.handoff_input
    plans = build_sound_plans(runtime, timing_sheet)
    adapters = dict(runtime.backends.sound)  # tts/sfx/music 三类（装配点一次构造）
    # 功能 016：声音阶段无 LLM 调用（不改网关），但档案口径同样随快照冻结
    llm_profiles = runtime.gateway.profile_snapshot().to_dict()
    result = run_sound_round(
        round_id=round_id,
        policy=_SoundPolicy(plans=plans),
        store=runtime.store,
        artifacts=runtime.artifacts,
        adapters=adapters,
        engine=runtime.engine,
        config=config,
        llm_profiles=llm_profiles,
        budget_tiers=gateway_budget_snapshot(runtime.gateway),  # 019：档位随快照冻结
        inputs={"timing_sheet": timing_sheet},
    )
    outcome = _round_candidates(runtime, result.tree_id, expected=len(plans))
    _require_ok("声音阶段", outcome, jobs=result.jobs)
    tracks = []
    for node in _nodes_by_artifact(runtime, result.tree_id):
        gen_type = str((node.observation_context or {}).get("gen_type", "music"))
        tracks.append(
            {
                "gen_type": gen_type,
                "artifact_hash": node.artifact_hash,
                "at_ms": int((node.observation_context or {}).get("at_ms", 0)),
                "duration_ms": int(
                    float((node.observation_context or {}).get("duration_s", 0.0)) * 1000
                ),
                "score": float(node.score or 0.0),
            }
        )
    return StageOutcome(
        products=tuple(
            _product("audio", track["artifact_hash"], track["artifact_hash"]) for track in tracks
        ),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "tracks": tracks,
            "has_audio": bool(tracks),
            "cost_by_type": dict(result.cost_by_type),
            "clips": [dict(clip) for clip in stage_input.upstream["visual"].detail["clips"]],
            "spent_usd": result.spent_usd,
        },
    )


def build_sound_plans(runtime: PilotRuntime, timing_sheet: TimingSheet) -> tuple[dict, ...]:
    """由分镜/剧本时序派生声音参数：每句台词一 TTS（响度标定到分档目标）+ 配乐一轨。

    **响度标定的后端纪律（1.4.0 修复）**：`_calibrated_params` 用 `synthesize_wav`
    合成一遍**模拟**波形来测响度、反推增益——该口径只在 `sound` 后端为 `simulated` 时成立。
    切 `sound: http` 后本函数**如实拒绝**（`StageFailedError`，零生成零扣费），
    不用模拟合成的响度冒充真实平台的产出（不给真实链路塞一份假标定）。
    """
    _require_simulated_sound_backend(runtime)
    config = runtime.configs.sound
    plans: list[dict] = []
    for index, utterance in enumerate(timing_sheet.utterances):
        duration_s = max(0.5, (utterance.end_ms - utterance.start_ms) / 1000.0)
        params = _calibrated_params(
            config,
            "tts",
            seed=index + 1,
            duration_s=duration_s,
            event_times_ms=[float(utterance.start_ms)],
            cer_injected=0.0,
            emotion_vector=[0.5, 0.5],
        )
        plans.append({"gen_type": "tts", "gen_params": params})
    plans.append(
        {
            "gen_type": "music",
            "gen_params": _calibrated_params(
                config,
                "music",
                seed=101,
                duration_s=float(config.simulated_gen["duration_seconds"]),
                event_times_ms=[],
                cer_injected=0.0,
                emotion_vector=[0.5, 0.5],
            ),
        }
    )
    return tuple(plans)


def _require_simulated_sound_backend(runtime: PilotRuntime) -> None:
    """响度标定只在模拟后端成立：真实后端如实拒绝（写明原因与替代做法，不静默降级）。

    替代做法（二选一，属实现变动，需按其价格评估后另行接入）：
    ① 让真实平台按其响度规范产出（把目标 `loudness_gain_db` 作为生成参数下传，
       由平台侧保证响度）——适配器已原样透传 `params`，无需改协议；
    ② 两段式真实标定：先调一次真实生成 → 解码返回 wav 用
       `measure_loudness_lufs` 实测 → 按差值二次生成（**会真实扣费两次**）。
    """
    backend = str(runtime.backends.resolved.get("sound", "simulated"))
    if backend != "simulated":
        raise StageFailedError(
            "声音响度标定不支持真实后端（sound 后端 = "
            f"{backend}）：现有口径 `_calibrated_params` 用模拟合成器合成一遍波形来测响度"
            "反推增益，切真实后端后该口径不成立——此处如实拒绝（零生成零扣费），"
            "不用模拟响度冒充平台产出。"
            "替代做法：① 把目标响度作为参数下传、由平台侧按其规范保证（适配器已原样透传"
            "params，无需改协议）；② 两段式真实标定（先真实生成→实测→二次生成，会真实扣费两次）。"
            "两者都属实现变动，须先评估计费再接入（见 docs/二期升级路径-真实生成与投放.md）。"
        )


def _calibrated_params(config: SoundConfig, gen_type: str, *, seed: int, **extra) -> dict:
    """响度标定（两段式）：先合成测响度，再按分档目标反推增益（gate 可过）。"""
    tier = {"tts": "dialogue", "sfx": "sfx", "music": "music"}[gen_type]
    base = {"gen_type": gen_type, "seed": seed, **extra}
    wav0, _ = synthesize_wav(
        {**base, "loudness_gain_db": 0.0}, config.simulated_gen, config.sample_rate
    )
    gain = float(config.loudness[tier]["target_lufs"]) - measure_loudness_lufs(
        decode_wav_samples(wav0), config.sample_rate
    )
    return {**base, "loudness_gain_db": gain}


def _editing_entry(stage_input: StageInput) -> StageOutcome:
    """剪辑阶段：既有 `run_editing_round`（时长/镜头分布/转场门禁 + 节奏基准曲线）。"""
    runtime = _runtime_of(stage_input)
    config = runtime.configs.editing
    round_id = _round_id(runtime, stage_input)
    edits: handoffs.EditInputs = stage_input.handoff_input
    edl = build_edl(runtime, edits.shot_library)
    adapter = runtime.backends.editing
    result = run_editing_round(
        round_id=round_id,
        policy=_EditingPolicy(edls=(edl,)),
        store=runtime.store,
        artifacts=runtime.artifacts,
        adapter=adapter,
        engine=runtime.engine,
        config=config,
        inputs={
            "shot_library": edits.shot_library,
            "scene_structure": edits.scene_structure,
        },
        gateway=runtime.gateway,
        drift_gate=runtime.drift_gate,  # 012 漂移门禁（装配一次、逐段透传）
    )
    outcome = _round_candidates(runtime, result.tree_id, expected=1)
    _require_ok("剪辑阶段", outcome, jobs=result.jobs)
    node = _winner_node(runtime, result.tree_id)
    reel = {
        "artifact_hash": node.artifact_hash,
        "duration_ms": int(edl.total_duration_ms()),
        "width": int(config.render["width"]),
        "height": int(config.render["height"]),
        "fps": int(config.render["fps"]),
    }
    return StageOutcome(
        products=(_product("reel", node.artifact_hash, node.artifact_hash),),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={"reel": reel, "edl": edl.to_dict(), "spent_usd": result.spent_usd},
    )


def build_edl(runtime: PilotRuntime, library: ShotLibrary) -> EditDecisionList:
    """由镜头库派生 EDL：逐镜满时长排布、全 cut 转场（时长落在形态配置容差内）。"""
    shots = tuple(library.shots)
    clips = []
    for index, shot in enumerate(shots):
        # 转场为**出向**语义（本镜到下一镜的衔接）：下一镜同分区 → 叠化（禁同区跳切），
        # 跨分区或末镜 → 硬切（转场规则库第④层口径，edl.validate_edl 逐条对照）
        nxt = shots[index + 1] if index + 1 < len(shots) else None
        same_scene_next = nxt is not None and nxt.scene_id == shot.scene_id
        transition = (
            {"type": "dissolve", "duration_ms": _DISSOLVE_MS}
            if same_scene_next
            else {"type": "cut", "duration_ms": 0}
        )
        clips.append(
            {
                "shot_id": shot.shot_id,
                "in_ms": 0,
                "out_ms": int(shot.duration_ms),
                "transition": transition,
            }
        )
    return EditDecisionList.from_dict({"clips": clips, "audio": []})


def _promo_entry(stage_input: StageInput) -> StageOutcome:
    """宣发阶段：既有 `run_round`（物料合规门禁 + 预算门禁 + 模拟平台指标）。"""
    runtime = _runtime_of(stage_input)
    config = runtime.configs.promo
    round_id = _round_id(runtime, stage_input)
    material_specs: handoffs.PromoMaterials = stage_input.handoff_input
    briefs = build_material_briefs(runtime, material_specs)
    adapter = runtime.backends.promo
    result = run_promo_round(
        round_id=round_id,
        policy=_PromoPolicy(briefs=briefs),
        store=runtime.store,
        artifacts=runtime.artifacts,
        adapter=adapter,
        gateway=runtime.gateway,
        config=config,
        engine=runtime.engine,
        sleep=lambda _: None,
    )
    # 宣发是**两段式**落树（既有语义）：投递成功节点待指标回流后一次性冻结落盘——
    # 复用既有的回流入口（业务侧 `agents/promo/ingest.py`），不在编排层另造回流逻辑。
    from agents.promo.ingest import ingest_round

    ingest_report = ingest_round(round_id, runtime.store, adapter, runtime.engine, config)
    outcome = _round_candidates(runtime, result.tree_id, expected=len(briefs))
    _require_ok("宣发阶段", outcome, jobs=result.materials)
    materials = [
        {
            "material_id": node.node_id,
            "artifact_hash": node.artifact_hash,
            "score": float(node.score or 0.0),
        }
        for node in _nodes_by_artifact(runtime, result.tree_id)
    ]
    return StageOutcome(
        products=tuple(
            _product("material", item["artifact_hash"], item["artifact_hash"]) for item in materials
        ),
        cost_usd=result.spent_usd,
        candidates=outcome.candidates,
        detail={
            "materials": materials,
            "ingest": dict(ingest_report),
            "reel_ref": material_specs.reel_ref,
            "reel_hash": material_specs.reel_hash,
            "spent_usd": result.spent_usd,
        },
    )


def build_material_briefs(
    runtime: PilotRuntime, materials: handoffs.PromoMaterials
) -> tuple[dict, ...]:
    """成片 → 宣发简报（规格逐项取自 C8 交接结果，形态差异全在配置）。"""
    # 单物料预算 = 单轮预算门禁上限（promo_pilot_ratio × 单轮预算）按物料数均分
    cap = float(runtime.configs.promo.promo_pilot_ratio) * float(
        runtime.configs.promo.exploration_per_round_usd
    )
    per_material = cap / max(1, len(materials.materials))
    briefs = []
    for material in materials.materials:
        spec = dict(material["spec"])
        gen_params: dict = {"temperature": 0.3}
        if "poster_size" in spec:
            gen_params["poster_size"] = spec["poster_size"]
        if "duration_ms" in spec:
            gen_params["duration_seconds"] = spec["duration_ms"] / 1000.0
        briefs.append(
            {
                # 提示词必须**带上配置声明的硬约束**（最大字数 + 敏感词库）：真实 LLM 不会猜
                # 我们的合规门禁——真实单轮实测：无约束提示词产出 636/1288/1528 字且含"最/第一"，
                # 被 rule.material_compliance 判 0（门禁没错，是提示词没把规则交底）。
                "prompt": _material_prompt(material, materials.reel_ref, runtime.configs.promo),
                "gen_params": gen_params,
                "kind": material["kind"],
                "tags": ["试水", material["kind"]],
                "budget_usd": round(per_material, 4),
                "material_id": material["material_id"],
            }
        )
    return tuple(briefs)


def _material_prompt(material: Mapping, reel_ref: str, config) -> str:
    """物料提示词（由形态配置派生）：把**合规硬约束**写进提示词，让真实模型有据可依。

    约束取自 `promo.material_spec` 与 `promo.sensitive_words`（配置单一事实源）——
    提示词不交底规则，真实模型就会产出被 `rule.material_compliance` 判 0 的文案
    （真实单轮实测：636~1528 字 + 命中"最/第一"），那是"门禁对、提示词不对"。
    """
    limits: list[str] = []
    spec = dict(material.get("spec") or {})
    if "max_copy_chars" in spec:
        limits.append(f"全文不超过 {int(spec['max_copy_chars'])} 个字")
    if "poster_size" in spec:
        limits.append(f"海报尺寸 {spec['poster_size']}")
    sensitive = [str(word) for word in getattr(config, "sensitive_words", ()) or ()]
    if sensitive:
        limits.append("不得出现以下词：" + "、".join(sensitive))
    limits.append("只输出文案正文，不要标题、引号、说明或换行")
    return (
        f"写一条{material['kind']}宣发物料（素材引用 {reel_ref}）；硬性要求：{'；'.join(limits)}。"
    )


# ---------------------------------------------------------------------------
# 确定性策略（业务侧人工策略档：只定结构，不做形态分支）
# ---------------------------------------------------------------------------


class _ScreenplayPolicy:
    """剧本策略：按输入与形态配置生成三阶段结构化计划（节拍表取自配置）。

    场景数与每场景行数由 `PilotConfig` 唯一解析后**经参数注入**（码内体量常量已退役）。
    """

    policy_version = "pilot-screenplay-v1"

    def __init__(
        self, *, config: ScreenplayConfig, inputs: Mapping, scene_count: int, lines_per_scene: int
    ) -> None:
        self._plan = build_screenplay_plan(
            config, inputs, scene_count=scene_count, lines_per_scene=lines_per_scene
        )

    def plan(self, inputs: Mapping, config: ScreenplayConfig) -> dict:
        return self._plan


def build_screenplay_plan(
    config: ScreenplayConfig, inputs: Mapping, *, scene_count: int, lines_per_scene: int
) -> dict:
    """三阶段计划：场景数 × 每场景行数（注入值；对白占比与页数区间落在配置门禁内）。"""
    topic = str(inputs.get("topic", ""))
    cast = list(inputs.get("characters") or ()) or ["主角"]
    beats = [
        {
            "beat_id": beat["beat_id"],
            "act": beat["act"],
            "required": beat["required"],
            "description": beat["description"],
        }
        for beat in config.beat_sheet
        if beat["required"]
    ]
    emotions = ("tense", "sorrow", "calm", "joyful", "awe")
    scenes, lines = [], []
    for scene_index in range(scene_count):
        scene_id = f"scene-{scene_index + 1}"
        scenes.append(
            {
                "scene_id": scene_id,
                "heading": f"内景 - 场景{scene_index + 1} - 夜",
                "location": f"场景{scene_index + 1}",
                "time_marker": scene_index * 30,
                "characters": list(cast),
                "axis_base": "A",
            }
        )
        for offset in range(lines_per_scene):
            name = cast[offset % len(cast)]
            is_dialogue = offset % 3 != 2
            lines.append(
                {
                    "line_id": f"s{scene_index + 1}-l{offset + 1}",
                    "scene_id": scene_id,
                    "kind": "dialogue" if is_dialogue else "action",
                    "text": (
                        f"{name}说：{topic}这一段还没完。"
                        if is_dialogue
                        else f"{name}在场景{scene_index + 1}里转身。"
                    ),
                    "character": name if is_dialogue else None,
                    "key": offset == 0,
                    "emotion": emotions[(scene_index + offset) % len(emotions)],
                }
            )
    markers = {
        "beats": beats,
        "scenes": scenes,
        "characters": [
            {"name": name, "aliases": list(config.character_aliases.get(name, ()))} for name in cast
        ],
        "lines": lines,
    }
    return {
        "outline": json.loads(json.dumps(markers)),
        "scenes": json.loads(json.dumps(markers)),
        "script": json.loads(json.dumps(markers)),
    }


class _StoryboardPolicy:
    policy_version = "pilot-storyboard-v1"

    def __init__(self, *, shotlists) -> None:
        self._shotlists = tuple(shotlists)

    def plan(self, config: StoryboardConfig, inputs: Mapping) -> list:
        return list(self._shotlists)


class _VisualPolicy:
    policy_version = "pilot-visual-v1"

    def __init__(self, *, params_list) -> None:
        self._params = tuple(dict(params) for params in params_list)

    def plan_clips(self, config: VisualConfig) -> list[dict]:
        return [{"gen_params": dict(params)} for params in self._params]


class _SoundPolicy:
    policy_version = "pilot-sound-v1"

    def __init__(self, *, plans) -> None:
        self._plans = tuple(dict(plan) for plan in plans)

    def plan(self, config: SoundConfig, inputs: Mapping) -> list[dict]:
        return [dict(plan) for plan in self._plans]


class _EditingPolicy:
    policy_version = "pilot-editing-v1"

    def __init__(self, *, edls) -> None:
        self._edls = tuple(edls)

    def plan(self, config: EditingConfig, inputs: Mapping) -> list:
        return list(self._edls)


class _PromoPolicy:
    policy_version = "pilot-promo-v1"

    def __init__(self, *, briefs) -> None:
        self._briefs = tuple(dict(brief) for brief in briefs)

    def plan_materials(self, config: PromoConfig) -> list[dict]:
        return [dict(brief) for brief in self._briefs]


# ---------------------------------------------------------------------------
# 阶段表（依赖链 + 各段交接契约）
# ---------------------------------------------------------------------------


def build_stage_specs(runtime: PilotRuntime | None) -> list[StageSpec]:
    """七环节定义：依赖顺序 + 执行入口 + 输入契约（交接口径全在 handoffs）。

    链首是 `dev`（立项组合 → 剧本输入），`script` 因此依赖 `dev`（契约 C1）；本函数为
    **静态声明**（形态配置不参与阶段结构）。每环节入口在这里被**只读采样包装装饰一次**
    （功能 018 / C13：成本第三方腿取逐环节网关记账增量；`run_report.sample_entrypoint` 只读
    `total_cost_usd`，**不构造网关**，构造点普查仍 14 处）。`runtime=None` 时只做静态声明
    （一致性机检用，无采样）。
    """
    specs = [
        StageSpec(
            stage_id="dev",
            entrypoint=_dev_entry,
            depends_on=(),
            handoff=_handoff_dev,
            title="立项",
            output_kind="slate",
        ),
        StageSpec(
            stage_id="script",
            entrypoint=_script_entry,
            depends_on=("dev",),
            handoff=_handoff_script,
            title="剧本",
            output_kind="script",
        ),
        StageSpec(
            stage_id="storyboard",
            entrypoint=_storyboard_entry,
            depends_on=("script",),
            handoff=_handoff_storyboard,
            title="分镜",
            output_kind="shotlist",
        ),
        StageSpec(
            stage_id="visual",
            entrypoint=_visual_entry,
            depends_on=("storyboard",),
            handoff=_handoff_visual,
            title="视觉",
            output_kind="clip",
        ),
        StageSpec(
            stage_id="sound",
            entrypoint=_sound_entry,
            depends_on=("visual",),
            handoff=_handoff_sound,
            title="声音",
            output_kind="audio",
        ),
        StageSpec(
            stage_id="editing",
            entrypoint=_editing_entry,
            depends_on=("sound",),
            handoff=_handoff_editing,
            title="剪辑",
            output_kind="reel",
        ),
        StageSpec(
            stage_id="promo",
            entrypoint=_promo_entry,
            depends_on=("editing",),
            handoff=_handoff_promo,
            title="宣发",
            output_kind="material",
        ),
    ]
    if runtime is None:
        return specs
    gateway = runtime.gateway
    return [
        replace(
            spec,
            entrypoint=run_report.sample_entrypoint(
                spec.stage_id, spec.entrypoint, gateway=gateway
            ),
        )
        for spec in specs
    ]


def _handoff_dev(upstream: Mapping[str, StageOutcome]) -> dict:
    """链首立项环节：输入由**显式声明的运行级映射**产生（`dev_inputs_of`），故无上游。

    `dev` → 剧本的**字段级交接声明**（`reads` 三类 + `renames` + 独立 `dropped` + 取数依据
    `sources` 与 C6 断言 ①~⑥）落在 `agents/pilot/handoffs.py`（契约 C5/C6）；本函数只如实
    声明"链首无上游"。
    """
    del upstream
    return {}


def _handoff_script(upstream: Mapping[str, StageOutcome]) -> Mapping | None:
    """剧本阶段的输入契约（契约 C5/C6、FR-016 的**双来源显式声明**）：

    - 上游有 `dev`（七环节链）⇒ 交回链首产出的**落地证据**（入库哈希 + 组合级标记 + 条目数），
      入口据此走 `dev_script_handoff`（取被标记条目的可移交要点，`topic ← genre` 改名承接）；
    - 上游无 `dev`（既有短剧试水链）⇒ `None`，入口回落运行级 `pilot_inputs`。

    两条来源都显式声明、生效哪一条随机读可见（`StageState.detail["input_source"]`），
    **禁止静默择一**。
    """
    dev = upstream.get("dev")
    if dev is None:
        return None
    return {
        "artifact_hash": dev.detail.get("artifact_hash"),
        "production_marks": list(dev.detail.get("production_marks") or ()),
        "entry_count": int(dev.detail.get("entry_count") or 0),
        "mode": handoffs.script_input_mode(tuple(upstream)),
    }


def _handoff_storyboard(upstream: Mapping[str, StageOutcome]) -> ScriptSegment:
    return ScriptSegment.from_dict(upstream["script"].detail["segment"])


def _handoff_visual(upstream: Mapping[str, StageOutcome]) -> tuple[dict, ...]:
    runtime = _RUNTIME_HOLDER["runtime"]
    shotlist = ShotList.from_dict(upstream["storyboard"].detail["shotlist"])
    return tuple(handoffs.shotlist_to_gen_params(shotlist, config=runtime.configs.visual))


def _handoff_sound(upstream: Mapping[str, StageOutcome]) -> TimingSheet:
    """声音时序表：由剧本台词与镜头时长派生（台词按顺序排布在成片时间轴上）。"""
    runtime = _RUNTIME_HOLDER["runtime"]
    segment = ScriptSegment.from_dict(upstream["visual"].detail["segment"])
    clips = list(upstream["visual"].detail["clips"])
    return build_timing_sheet(runtime, segment, clips)


def _handoff_editing(upstream: Mapping[str, StageOutcome]) -> handoffs.EditInputs:
    runtime = _RUNTIME_HOLDER["runtime"]
    sound = upstream["sound"].detail
    clips = list(sound["clips"])
    audio = sound if sound.get("has_audio") else None
    return handoffs.av_to_edit_inputs(clips, audio, config=runtime.configs.editing)


def _handoff_promo(upstream: Mapping[str, StageOutcome]) -> handoffs.PromoMaterials:
    runtime = _RUNTIME_HOLDER["runtime"]
    reel = dict(upstream["editing"].detail["reel"])
    return handoffs.reel_to_promo_materials(reel, config=runtime.configs.promo)


def build_timing_sheet(
    runtime: PilotRuntime, segment: ScriptSegment, clips: Sequence[Mapping]
) -> TimingSheet:
    """台词 → 时序表：按镜头时间轴顺序给每句对白排 start/end（不超镜头时长）。"""
    clip_ms = int(runtime.configs.visual.clip_spec["duration_seconds"] * 1000)
    shot_count = len(clips)  # 时间轴以实际镜头数为界（每镜一句台词，不越过成片素材）
    utterances: list[dict] = []
    slot = 0
    for line_id in segment.line_ids():
        line = segment.line(line_id)
        if line.kind != "dialogue":
            continue
        if slot >= shot_count:
            break
        start = slot * clip_ms
        slot += 1
        utterances.append(
            {
                "text": line.text,
                "start_ms": start,
                "end_ms": start + clip_ms - 200,
            }
        )
    return TimingSheet(
        utterances=utterances,
        effects=[{"kind": "sting", "at_ms": 0}],
    )


def _winner_node(runtime: PilotRuntime, tree_id: str):
    nodes = _nodes_by_artifact(runtime, tree_id)
    return max(nodes, key=lambda node: (float(node.score or 0.0), node.node_id))


def _nodes_by_shot(runtime: PilotRuntime, tree_id: str) -> list:
    """按镜头序号排序的候选节点（节点 id 字典序不是镜头序，须按 shot_id 数字排序）。"""

    def _key(node) -> tuple[int, str]:
        shot_id = str((node.observation_context or {}).get("gen_params", {}).get("shot_id", ""))
        suffix = shot_id.rsplit("-", 1)[-1]
        return (int(suffix) if suffix.isdigit() else 0, node.node_id)

    return sorted(_nodes_by_artifact(runtime, tree_id), key=_key)


def _nodes_by_artifact(runtime: PilotRuntime, tree_id: str) -> list:
    return sorted(
        (node for node in runtime.store.nodes_of(tree_id) if node.depth >= 1),
        key=lambda node: node.node_id,
    )


# 交接函数需要 runtime（形态配置）而 `StageSpec.handoff` 只接收上游输出：
# 运行期单实例持有（pilot 在装配阶段写入，进程内唯一运行）。
_RUNTIME_HOLDER: dict[str, Any] = {}


def bind_runtime(runtime: PilotRuntime) -> None:
    """绑定本次运行的 runtime（交接函数据此读取形态配置；单进程单运行）。"""
    _RUNTIME_HOLDER["runtime"] = runtime


def runtime_namespace(runtime: PilotRuntime) -> SimpleNamespace:
    """运行时的只读视图（供 pilot 注入 shared）。"""
    return SimpleNamespace(
        form=runtime.form,
        config_path=str(runtime.config_path),
        config_fingerprint=runtime.config_fingerprint,
        data_dir=str(runtime.data_dir),
    )


def artifact_ref_of(content_hash: str) -> ArtifactRef:
    """工件引用视图（评估器/报告用；内容寻址哈希即引用）。"""
    return ArtifactRef(artifact_hash=content_hash)
