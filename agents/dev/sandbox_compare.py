"""回放沙盘对比报告（薄适配：契约 C14，FR-008）。

机制在通用件 `core/degraded/compare.py`（业务无关，服务所有降级 Agent）；本模块只注入
开发线的业务件与默认目录——`slate_match_key`（结构键：策略可复现部分）、单一阶段序列
`STAGES`（本环节无阶段划分，唯一交付物即一个阶段）与 `dev/comparisons`；导出名、签名、
关键字默认值与异常类与 009 侧同构。

人工提交的新版本 vs 当前部署版本在**模拟器池上回放对比**（零 LLM、零生成——回放只读
历史节点，原则三）：逐树得分、分项评估器差异（命中历史节点的 `eval_breakdown` 均值）、
pareto_auc 曲线、UNKNOWN 覆盖说明；报告字段集与 009 同构（可比对树数 = `per_tree` 行数，
由 CLI/演示读出——不得另立一份报告 schema）。

**策略计划形态**（`agents/dev/loop.py:slate_plan`）：本环节单一产出、无阶段划分，故本模块以
`single_stage=SLATE_STAGE` **显式声明唯一阶段**——回放按"计划整体即该阶段的结构标记"取标记，
两种计划形态都可回放：单一阶段形态 `{slate: {entries, production_marks}}` 与**扁平形态**
`{entries, production_marks}`（仓库引导树策略 `policies/history/dev/34525518074d.py` 即扁平）。
不声明单一阶段而计划又与阶段序列对不上的调用会被通用件判错（不静默产出全 UNKNOWN 报告）。

**最小池门槛（前置，SC-011）**：可比对树数 < `dev.min_comparable_trees` ⇒ **拒绝产出报告**
（错误含实测树数与门槛值）；与"未过无偏性不得产出报告"并列。门槛取自形态配置：缺项即报错，
不在码内取默认。

诚实边界（原则六）：未命中的阶段不给分（UNKNOWN = 零信息）；整树无命中 → 该树记 0 分并
提示扩大线上记录；新版本全劣 → 报告如实呈现（`verdict=deployed_better`）并建议保留现版本；
**未过无偏性验收不得产出对比报告**（FR-013 发布阻塞）；报告只增不改。

策略执行隔离的两项落地义务（宪章 v2.0.0 原则四例外条款）在通用件内实现，本路径同享：
① 策略执行带执行超时（策略内死循环判 `CompareError`，不挂死宿主）；② 断言守护"不向策略
执行交付任何环境对象"（策略仅 `plan(inputs, config)`，探测由宿主代执行）。
"""

from pathlib import Path

from agents.dev.artifact import simulated_signal_sources
from agents.dev.loop import SLATE_STAGE, slate_match_key
from core.degraded import compare as _core_compare
from core.degraded.compare import (
    UNKNOWN_NOTE,
    CompareError,
    ReplayComparison,
    UnbiasednessAttestation,
)
from core.replay.pool import SimulatorPool

# 导出面（与 009 侧同构；机制全在 `core/degraded/compare.py`）
__all__ = [
    "AGENT_ID",
    "DEFAULT_COMPARISON_DIR",
    "STAGES",
    "UNKNOWN_NOTE",
    "CompareError",
    "ReplayComparison",
    "UnbiasednessAttestation",
    "compare_versions",
    "load_comparison",
    "min_comparable_trees_of",
    "parse_created_at",
    "replay_policy",
    "signal_sources_of",
]

AGENT_ID = "dev"
DEFAULT_COMPARISON_DIR = Path("dev/comparisons")
# 本环节无阶段划分（澄清第 8 条：单一产出）——回放按**一个阶段**取结构键（计划形态见
# `agents/dev/loop.py:slate_plan`）：阶段序列是该业务的注入项，通用件内无阶段名
STAGES = (SLATE_STAGE,)


def _slate_match_key(stage, *, policy_version: str, inputs: dict, config, markers=None) -> dict:
    """结构键绑定：忽略阶段标记（dev 的匹配键只含策略可复现的结构键，不含产物摘要，C13）。"""
    return slate_match_key(policy_version=policy_version, inputs=inputs, config=config)


def min_comparable_trees_of(cfg) -> int:
    """最小可比对树数门槛：取自形态配置（缺项即报错，不在码内取默认）。"""
    floor = getattr(cfg, "min_comparable_trees", None)
    if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
        raise CompareError(
            f"形态配置缺少最小可比对树数（dev.min_comparable_trees）：实际为 {floor!r}"
            "（门槛是形态项，缺项不得静默放行）"
        )
    return floor


def signal_sources_of(cfg) -> tuple[dict, ...]:
    """对比报告侧的模拟数据源标记载荷（SC-009 第二处）：与产物/判据材料**同源**。

    单一事实源 = `agents/dev/artifact.simulated_signal_sources`（不另写一份标注口径）；
    报告的数取自命中节点的 `eval_breakdown`（诊断已带标注），此处把同一载荷也挂在报告
    取出面（CLI/演示载荷）——模拟信号不得被读成真实商业数据。
    """
    sources = getattr(cfg, "signals", None)
    if not isinstance(sources, dict) or not sources:
        raise CompareError("形态配置缺少模拟数据源参数（dev.signals）：不得无源出对比报告")
    return simulated_signal_sources(sources)


def replay_policy(source: str, trees, *, cfg, inputs: dict, store):
    """回放一个策略：按结构键在每棵树的历史节点上 probe（零生成、零 LLM）。"""
    return _core_compare.replay_policy(
        source,
        trees,
        cfg=cfg,
        inputs=inputs,
        store=store,
        stages=STAGES,
        match_key=_slate_match_key,
        single_stage=SLATE_STAGE,  # 单一产出：计划整体即该阶段的结构标记
    )


def compare_versions(
    new_version: str,
    deployed_version: str,
    pool: SimulatorPool,
    cfg,
    *,
    store,
    inputs: dict,
    unbiasedness=None,
    history_root: str | Path = "policies/history",
    agent_id: str = AGENT_ID,
    comparison_dir: str | Path = DEFAULT_COMPARISON_DIR,
) -> ReplayComparison:
    """回放对比新版本 vs 部署版本（C14）：机制、前置门禁与最小池门槛见 `core.degraded.compare`。"""
    return _core_compare.compare_versions(
        new_version,
        deployed_version,
        pool,
        cfg,
        store=store,
        inputs=inputs,
        unbiasedness=unbiasedness,
        match_key=_slate_match_key,
        stages=STAGES,
        single_stage=SLATE_STAGE,
        min_comparable_trees=min_comparable_trees_of(cfg),
        history_root=history_root,
        agent_id=agent_id,
        comparison_dir=comparison_dir,
    )


def load_comparison(
    comparison_id: str, *, comparison_dir: str | Path = DEFAULT_COMPARISON_DIR
) -> dict:
    """按 id 读取对比报告（采纳的依据引用；不存在即报错，不静默返回空）。"""
    return _core_compare.load_comparison(comparison_id, comparison_dir=comparison_dir)


def parse_created_at(comparison: dict) -> float:
    """报告创建时间（秒级；审计与排序用）。"""
    return _core_compare.parse_created_at(comparison)
