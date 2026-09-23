"""采纳门禁与决策留痕（薄适配：契约 C14，FR-008/FR-009）。

机制在通用件 `core/degraded/adoption.py`（业务无关，服务所有降级 Agent）；本模块只注入
开发线的默认目录——`dev/comparisons`、`dev/adoptions`；导出名、签名、关键字默认值与异常类
与 009 侧同构。

**人工采纳才动部署指针**（SC-002：未采纳策略更新部署指针的次数恒 0——机检依据 = 指针与
采纳记录双留痕）：

- `adopt(comparison_id, decision, by, reason)`：decision ∈ {adopt, reject}；
  **两种决定都留痕**（人/时间/依据 comparison_id/理由，理由必须非空）；
- `adopt` → 更新 `configs` 的部署指针（`deployment.dev.current_policy_version`，定点改写
  保留注释与其他段）；
- `reject` → 指针逐字节不变；
- 采纳前机检：对比报告必须存在（依据引用）+ 报告中的部署版本必须与当前指针一致
  （防止在漂移的基线上采纳——需重新对比）+ 待采纳版本必须在策略历史内。

谱系 meta 保持"只增不改"（提交时的来源记录），本模块的采纳记录是决策留痕的权威载体
（`{comparison_id}.{decision}.json`，只增不改）——开发 Agent 的部署指针同样只有本模块能移动
（无任何自动部署路径，原则六）。
"""

from pathlib import Path

from agents.dev.sandbox_compare import DEFAULT_COMPARISON_DIR
from core.degraded import adoption as _core_adoption
from core.degraded.adoption import (
    DECISIONS,
    AdoptionError,
    AdoptionRecord,
)

# 导出面（与 009 侧同构；机制全在 `core/degraded/adoption.py`）
__all__ = [
    "AGENT_ID",
    "DEFAULT_ADOPTION_DIR",
    "DECISIONS",
    "AdoptionError",
    "AdoptionRecord",
    "adopt",
    "deployed_version",
    "load_adoption_record",
]

AGENT_ID = "dev"
DEFAULT_ADOPTION_DIR = Path("dev/adoptions")


def deployed_version(config_path: str | Path, *, agent_id: str = AGENT_ID) -> str | None:
    """部署指针读取（未配置返回 None——如实不伪造，调用方自行判定）。"""
    return _core_adoption.deployed_version(config_path, agent_id=agent_id)


def adopt(
    comparison_id: str,
    decision: str,
    by: str,
    reason: str,
    *,
    config_path: str | Path,
    comparison_dir: str | Path = DEFAULT_COMPARISON_DIR,
    adoption_dir: str | Path = DEFAULT_ADOPTION_DIR,
    history_root: str | Path = "policies/history",
    agent_id: str = AGENT_ID,
) -> AdoptionRecord:
    """人工采纳/拒绝（C14）：仅 adopt 更新部署指针；两种决定都留痕（理由非空）。"""
    return _core_adoption.adopt(
        comparison_id,
        decision,
        by,
        reason,
        config_path=config_path,
        agent_id=agent_id,
        comparison_dir=comparison_dir,
        adoption_dir=adoption_dir,
        history_root=history_root,
    )


def load_adoption_record(
    comparison_id: str, decision: str, *, adoption_dir: str | Path = DEFAULT_ADOPTION_DIR
) -> dict:
    """读取采纳记录（审计用；不存在即报错，不静默返回空）。"""
    return _core_adoption.load_adoption_record(comparison_id, decision, adoption_dir=adoption_dir)
