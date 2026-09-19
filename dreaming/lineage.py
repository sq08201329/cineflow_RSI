"""谱系元数据读写（research 决策 3，T408）。

meta.json 存于 policies/history/{agent_id}/{version}.meta.json——
与策略代码同 lifecycle、随 git 版本化、文件只增不改（审计由 git 承担）。
schema 见 data-model §1 ApprovalRecord。
"""

import json
from pathlib import Path

SOURCES = ("dreaming", "epsilon_random", "manual")
DECISIONS = ("approved", "rejected")
REQUIRED_FIELDS = ("version", "parent_version", "created_round", "reward", "source")


class MetaValidationError(Exception):
    """meta.json schema 校验失败。"""


class LineageConflictError(Exception):
    """同版本不同内容冲突（文件只增不改纪律）。"""


def validate_meta(meta: dict) -> None:
    """schema 校验：必填字段、source/decision 枚举、approval 结构。"""
    for field_name in REQUIRED_FIELDS:
        if field_name not in meta:
            raise MetaValidationError(f"meta 缺少字段 {field_name!r}")
    if meta["source"] not in SOURCES:
        raise MetaValidationError(f"source 必须为 {SOURCES} 之一，实际为 {meta['source']!r}")
    if not isinstance(meta["reward"], dict) or "reward" not in meta["reward"]:
        raise MetaValidationError("reward 必须为含 reward 键的奖励分解字典")
    approval = meta.get("approval")
    if approval is not None:
        for field_name in ("approver", "at", "decision", "reason"):
            if field_name not in approval:
                raise MetaValidationError(f"approval 缺少字段 {field_name!r}")
        if approval["decision"] not in DECISIONS:
            raise MetaValidationError(
                f"approval.decision 必须为 {DECISIONS} 之一，实际为 {approval['decision']!r}"
            )


def meta_path(history_root: str | Path, agent_id: str, version: str) -> Path:
    return Path(history_root) / agent_id / f"{version}.meta.json"


def write_meta(history_root: str | Path, agent_id: str, meta: dict) -> Path:
    """落盘 meta.json（先 schema 校验；幂等：同内容重写零变化，不同内容冲突报错）。"""
    validate_meta(meta)
    target = meta_path(history_root, agent_id, meta["version"])
    payload = json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True)
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != meta:
            raise LineageConflictError(
                f"版本 {meta['version']} 的 meta 已存在且内容冲突（文件只增不改）"
            )
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)  # 原子落盘
    return target


def read_meta(history_root: str | Path, agent_id: str, version: str) -> dict | None:
    target = meta_path(history_root, agent_id, version)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))
