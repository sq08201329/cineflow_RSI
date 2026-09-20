"""人工策略提交通道（合同 screenplay-degraded.md C13，FR-009）。

**策略 = 人编写的代码**（澄清 Q2：参数调整走 configs，不产生策略版本）：
`submit_policy(source_text, submitter, cfg)` → 版本 = 源码 BLAKE3 前 12 位
（复用 `policies.versioning`）→ 落 `policies/history/screenplay/{version}.py` +
`{version}.meta.json`（谱系 meta 布局复用 005 `dreaming.lineage`：parent_version /
提交人 / 时间 / 静态检查结果 / 名单审计）。

拒绝语义（未过即不进历史，**不静默放过**）：
- 静态检查（002 `policies.static_check`，沙箱第一道防线；禁私有属性访问）；
- 接口签名（T914 定案：`Policy.plan(self, inputs, config)`，AST 校验不执行源码）。

草稿（draft=True）仅校验与算版本，**不入历史、不参与回放**；同源码重复提交幂等
（同版本号、不重复落盘、谱系只增不改——首位提交人的 provenance 不被后来者覆盖）。

本模块是剧本策略版本的**唯一产生入口**：产出执行器（loop）不写策略历史，
不存在任何自动生成/自动部署路径（宪章原则六：策略仅由人工提交）。
"""

import ast
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dreaming.lineage import read_meta, write_meta
from policies.static_check import find_violations
from policies.versioning import policy_version, record_policy

AGENT_ID = "screenplay"
DEFAULT_POLICY_HISTORY_ROOT = Path("policies/history")
# 人工策略接口签名（T914 定案）：plan(inputs, config)
_PLAN_SIGNATURE = ("self", "inputs", "config")
_EMPTY_REWARD = {"pareto_auc": 0.0, "parallel_penalty": 0.0, "lambda": 0.0, "reward": 0.0}


class PolicySubmissionError(Exception):
    """策略提交被拒（输入缺失/静态检查不过/接口签名不符）。"""


@dataclass(frozen=True)
class HumanPolicyVersion:
    """人工策略版本（C13）：版本号 / 谱系父版本 / 提交人 / 静态检查结果 / 落盘路径。"""

    version: str
    parent_version: str | None
    submitter: str
    submitted_at: str
    static_check: str  # "passed"（未过检查的提交直接拒绝，不产生版本）
    violations: tuple[str, ...] = ()
    recorded: bool = False  # 草稿 False（不入历史、不参与回放）
    source_path: Path | None = None
    meta_path: Path | None = None
    source: str = "manual"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "parent_version": self.parent_version,
            "submitter": self.submitter,
            "submitted_at": self.submitted_at,
            "static_check": self.static_check,
            "violations": list(self.violations),
            "recorded": self.recorded,
            "source": self.source,
            "source_path": None if self.source_path is None else str(self.source_path),
            "meta_path": None if self.meta_path is None else str(self.meta_path),
        }


def _interface_violations(source_text: str) -> list[str]:
    """接口签名 AST 校验（不执行源码）：必须定义 `Policy.plan(self, inputs, config)`。"""
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:  # 语法错误已在静态检查阶段拒绝（此处兜底）
        return [f"语法错误：{exc}"]
    policy = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Policy"),
        None,
    )
    if policy is None:
        return ["缺少 Policy 类（人工策略接口：plan(inputs, config)）"]
    plan = next(
        (
            node
            for node in policy.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "plan"
        ),
        None,
    )
    if plan is None:
        return ["Policy 缺少 plan 方法（人工策略接口：plan(inputs, config)）"]
    arguments = [argument.arg for argument in plan.args.args]
    if tuple(arguments) != _PLAN_SIGNATURE:
        return [
            f"plan 签名必须为 {list(_PLAN_SIGNATURE)}，实际为 {arguments}"
            "（策略接口在 T914 定案：plan(inputs, config) 产分阶段计划）"
        ]
    return []


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _existing_meta(history_root: Path, version: str, agent_id: str) -> dict | None:
    return read_meta(history_root, agent_id, version)


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
    """提交人工策略版本（C13）。

    cfg：做梦形态配置（duck-typed）——用于谱系 meta 的 reward 占位 λ 与
    **禁自动进化名单审计**（`no_auto_evolve` 字段留痕：该版本是降级模式产物）。
    draft=True：仅静态检查 + 接口校验 + 算版本，不落历史（草稿不入回放）。
    """
    if not isinstance(source_text, str) or not source_text.strip():
        raise PolicySubmissionError("策略源码不能为空（人工策略 = 人编写的代码）")
    if not isinstance(submitter, str) or not submitter:
        raise PolicySubmissionError("提交人不能为空（谱系 provenance 必填）")

    violations = find_violations(source_text)
    if violations:
        raise PolicySubmissionError(
            "策略静态检查未通过（未过检查不入历史、不进入回放）：\n"
            + "\n".join(f"- {item}" for item in violations)
        )
    interface_violations = _interface_violations(source_text)
    if interface_violations:
        raise PolicySubmissionError(
            "策略接口不符（未过检查不入历史）：\n"
            + "\n".join(f"- {item}" for item in interface_violations)
        )

    version = policy_version(source_text)
    history_root = Path(history_root)
    submitted_at = _now_iso()
    if draft:
        return HumanPolicyVersion(
            version=version,
            parent_version=parent_version,
            submitter=submitter,
            submitted_at=submitted_at,
            static_check="passed",
            recorded=False,
        )

    existing = _existing_meta(history_root, version, agent_id)
    record_policy(source_text, agent_id, history_root=history_root)  # 幂等落盘（同内容零变化）
    source_path = history_root / agent_id / f"{version}.py"
    if existing is not None:
        # 幂等：同源码重复提交同版本号——谱系只增不改（不覆盖首位提交人的 provenance）
        return _record_of(existing, history_root, agent_id, version)

    lambda_ = float(getattr(cfg, "lambda_", 0.0))
    no_auto_evolve_agents = tuple(getattr(cfg, "no_auto_evolve_agents", ()) or ())
    meta = {
        "version": version,
        "parent_version": parent_version,
        "created_round": "manual-submit",  # 人工提交（非做梦轮次）
        "reward": {**_EMPTY_REWARD, "lambda": lambda_},  # 占位：对比回放尚未产出
        "source": "manual",
        "static_check": {"result": "passed", "violations": [], "checked_at": submitted_at},
        "submission": {"submitter": submitter, "at": submitted_at},
        # 名单审计（宪章原则六）：提交时该 Agent 是否在禁自动进化名单内
        "no_auto_evolve": agent_id in no_auto_evolve_agents,
    }
    meta_path = write_meta(history_root, agent_id, meta)
    return HumanPolicyVersion(
        version=version,
        parent_version=parent_version,
        submitter=submitter,
        submitted_at=meta["submission"]["at"],
        static_check="passed",
        recorded=True,
        source_path=source_path,
        meta_path=meta_path,
    )


def _record_of(meta: dict, history_root: Path, agent_id: str, version: str) -> HumanPolicyVersion:
    """由既有 meta 还原版本记录（幂等重提路径：不重写任何文件）。"""
    return HumanPolicyVersion(
        version=version,
        parent_version=meta.get("parent_version"),
        submitter=(meta.get("submission") or {}).get("submitter", ""),
        submitted_at=(meta.get("submission") or {}).get("at", ""),
        static_check=(meta.get("static_check") or {}).get("result", "passed"),
        violations=tuple((meta.get("static_check") or {}).get("violations", [])),
        recorded=True,
        source_path=history_root / agent_id / f"{version}.py",
        meta_path=history_root / agent_id / f"{version}.meta.json",
    )


def load_policy_source(
    version: str,
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> str:
    """按版本读取策略源码（回放对比/审计用；不存在即报错，不静默返回空）。"""
    path = Path(history_root) / agent_id / f"{version}.py"
    if not path.is_file():
        raise PolicySubmissionError(f"策略版本 {version!r} 的源码不存在：{path}")
    return path.read_text(encoding="utf-8")


def read_policy_meta(
    version: str,
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> dict | None:
    """按版本读取谱系 meta（不存在返回 None，如实不伪造）。"""
    meta = _existing_meta(Path(history_root), version, agent_id)
    if meta is None:
        return None
    return json.loads(json.dumps(meta))  # 值语义副本（调用方不可回写谱系）


@dataclass(frozen=True)
class PolicyHistoryEntry:
    """策略历史条目（列表视图：版本 + meta）。"""

    version: str
    meta: dict = field(default_factory=dict)


def list_policy_versions(
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    agent_id: str = AGENT_ID,
) -> tuple[str, ...]:
    """已版本化的人工策略版本（按版本号升序；草稿不在其列）。"""
    directory = Path(history_root) / agent_id
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.stem for path in directory.glob("*.py")))
