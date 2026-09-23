"""回放沙盘对比报告（薄适配：合同 screenplay-degraded.md C14，FR-009）。

机制在通用件 `core/degraded/compare.py`（业务无关，服务所有降级 Agent）；本模块只注入
剧本线的业务件与默认目录——`stage_match_key`（结构键：策略可复现部分）、`STAGES`（阶段序列）
与 `screenplay/comparisons`；导出名、签名、关键字默认值与异常类逐字不变（行为等价）。

人工提交的新版本 vs 当前部署版本在**模拟器池上回放对比**（零 LLM、零生成——回放只读
历史节点，原则三）：逐树得分、分项评估器差异（命中历史节点的 eval_breakdown 均值）、
pareto_auc 曲线（复用 005 权威口径）、UNKNOWN 覆盖说明。

诚实边界（原则六）：未命中的阶段不给分（UNKNOWN = 零信息）；整树无命中 → 该树记 0 分并
提示扩大线上记录；新版本全劣 → 报告如实呈现（`verdict=deployed_better`）并建议保留现版本；
**未过无偏性验收不得产出对比报告**（FR-013 发布阻塞）；报告只增不改。

策略执行隔离的两项落地义务（宪章 v2.0.0 原则四例外条款）在通用件内实现，本路径同享：
① 策略执行带执行超时（策略内死循环判 `CompareError`，不挂死宿主）；② 断言守护"不向策略执行
交付任何环境对象"（策略仅 `plan(inputs, config)`，探测由宿主代执行）。
"""

from pathlib import Path

from agents.screenplay.artifact import STAGES
from agents.screenplay.loop import stage_match_key
from core.degraded import compare as _core_compare
from core.degraded.compare import (
    UNKNOWN_NOTE,
    CompareError,
    ReplayComparison,
    UnbiasednessAttestation,
)
from core.replay.pool import SimulatorPool

# 导出面（009 公共 API 逐字不变；机制全在 `core/degraded/compare.py`）
__all__ = [
    "AGENT_ID",
    "DEFAULT_COMPARISON_DIR",
    "UNKNOWN_NOTE",
    "CompareError",
    "ReplayComparison",
    "UnbiasednessAttestation",
    "compare_versions",
    "load_comparison",
    "parse_created_at",
    "replay_policy",
]

AGENT_ID = "screenplay"
DEFAULT_COMPARISON_DIR = Path("screenplay/comparisons")
# 009 形态配置未声明"最小可比对树数"下限（该门槛是 017 开发 Agent 侧的形态项
# `dev.min_comparable_trees`）：此处取 0，保持现有行为不变（不引入新的拒绝路径）
MIN_COMPARABLE_TREES = 0


def replay_policy(source: str, trees, *, cfg, inputs: dict, store):
    """回放一个策略：按结构键在每棵树的历史节点上 probe（零生成、零 LLM）。"""
    return _core_compare.replay_policy(
        source,
        trees,
        cfg=cfg,
        inputs=inputs,
        store=store,
        stages=STAGES,
        match_key=stage_match_key,
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
    """回放对比新版本 vs 部署版本（C14）：机制与前置门禁见 `core.degraded.compare`。"""
    return _core_compare.compare_versions(
        new_version,
        deployed_version,
        pool,
        cfg,
        store=store,
        inputs=inputs,
        unbiasedness=unbiasedness,
        match_key=stage_match_key,
        stages=STAGES,
        min_comparable_trees=MIN_COMPARABLE_TREES,
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
