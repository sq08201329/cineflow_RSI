"""人工题材方向探索策略提交通道（薄适配：契约 C14，FR-008）。

机制在通用件 `core/degraded/policy.py`（业务无关，服务所有降级 Agent）；本模块只注入
开发线的 `agent_id="dev"` 与默认目录 `policies/history`——导出名、签名、关键字默认值与
异常类与 009 侧逐字同构（同一份实现服务两个降级 Agent，纪律修正不再改两遍）。

**策略 = 人编写的代码**（参数调整走 configs，不产生策略版本）：`submit_policy(source_text,
submitter, cfg)` → 版本 = 源码 BLAKE3 前 12 位（复用 `policies.versioning`）→ 落
`policies/history/dev/{version}.py` + `{version}.meta.json`（谱系 meta：parent_version /
提交人 / 时间 / 静态检查结果 / `no_auto_evolve` 名单审计位——开发 Agent 恒在名单内）。

拒绝语义（未过即不进历史，**不静默放过**）：静态检查（002）+ 接口签名
（`Policy.plan(self, inputs, config)`，AST 校验不执行源码）。草稿（draft=True）仅校验与算版本，
**不入历史、不参与回放**；同源码重复提交幂等（谱系只增不改）。

本模块是开发策略版本的**唯一产生入口**：产出执行器（loop）不写策略历史，不存在任何自动
生成/自动部署路径（宪章原则六：策略仅由人工提交）。
"""

from pathlib import Path

from core.degraded import policy as _core_policy
from core.degraded.policy import (
    DEFAULT_POLICY_HISTORY_ROOT,
    HumanPolicyVersion,
    PolicyHistoryEntry,
    PolicySubmissionError,
)

# 导出面（与 009 侧同构；机制全在 `core/degraded/policy.py`）
__all__ = [
    "AGENT_ID",
    "DEFAULT_POLICY_HISTORY_ROOT",
    "HumanPolicyVersion",
    "PolicyHistoryEntry",
    "PolicySubmissionError",
    "list_policy_versions",
    "load_policy_source",
    "read_policy_meta",
    "submit_policy",
]

AGENT_ID = "dev"


def submit_policy(
    source_text: str,
    submitter: str,
    cfg,
    *,
    parent_version: str | None = None,
    draft: bool = False,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> HumanPolicyVersion:
    """提交人工策略版本（C14）：机制与拒绝语义见 `core.degraded.policy.submit_policy`。"""
    return _core_policy.submit_policy(
        source_text,
        submitter,
        cfg,
        agent_id=agent_id,
        history_root=history_root,
        parent_version=parent_version,
        draft=draft,
    )


def load_policy_source(
    version: str,
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> str:
    """按版本读取策略源码（回放对比/审计用；不存在即报错，不静默返回空）。"""
    return _core_policy.load_policy_source(version, agent_id=agent_id, history_root=history_root)


def read_policy_meta(
    version: str,
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> dict | None:
    """按版本读取谱系 meta（不存在返回 None，如实不伪造）。"""
    return _core_policy.read_policy_meta(version, agent_id=agent_id, history_root=history_root)


def list_policy_versions(
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> tuple[str, ...]:
    """已版本化的人工策略版本（按版本号升序；草稿不在其列）。"""
    return _core_policy.list_policy_versions(agent_id=agent_id, history_root=history_root)
