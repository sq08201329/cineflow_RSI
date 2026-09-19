"""谱系元数据读写（research 决策 3，T408）。

meta.json 存于 policies/history/{agent_id}/{version}.meta.json——
与策略代码同 lifecycle、随 git 版本化、文件只增不改（审计由 git 承担）。
schema 见 data-model §1 ApprovalRecord。
"""

import json
from dataclasses import asdict, dataclass
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


# ============================================================
# US3：谱系报表与进化曲线（T419，contracts/lineage.md）
# ============================================================


@dataclass(frozen=True)
class LineageReport:
    """谱系报表：meta.json（父子/审批/reward）+ 树库（policy_version→tree_ids）两源汇聚。"""

    agent_id: str
    versions: list[dict]

    def to_dict(self) -> dict:
        return {"agent_id": self.agent_id, "versions": self.versions}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def build_lineage(agent_id: str, store, history_dir: str | Path) -> LineageReport:
    """两源汇聚谱系：文件体系（meta.json）+ 树库只读查询；冲突报错不静默。"""
    history_dir = Path(history_dir)
    metas: dict[str, dict] = {}
    directory = history_dir / agent_id
    if directory.is_dir():
        for path in sorted(directory.glob("*.meta.json")):
            meta = json.loads(path.read_text(encoding="utf-8"))
            filename_version = path.name[: -len(".meta.json")]
            # 两源冲突：文件名版本（= 代码内容哈希）与 meta 内容版本不一致
            if meta.get("version") != filename_version:
                raise LineageConflictError(
                    f"谱系冲突：文件 {path.name} 的版本字段 {meta.get('version')!r} "
                    f"与文件名 {filename_version!r} 不一致（谱系完整性优先，不静默）"
                )
            metas[meta["version"]] = meta

    # 树库：policy_version → tree_ids（001 只读查询）
    tree_ids_by_version: dict[str, list[str]] = {}
    try:
        trees = store.trees_by(agent_id=agent_id)
    except Exception:  # noqa: BLE001 - 无该 agent 的树 → 空映射
        trees = []
    for tree in trees:
        tree_ids_by_version.setdefault(tree.policy_version, []).append(tree.tree_id)

    versions: list[dict] = []
    for version in sorted(set(metas) | set(tree_ids_by_version)):
        meta = metas.get(version, {})
        versions.append(
            {
                "version": version,
                "parent_version": meta.get("parent_version"),
                "tree_ids": sorted(tree_ids_by_version.get(version, [])),
                "child_versions": sorted(
                    m["version"] for m in metas.values() if m.get("parent_version") == version
                ),
                "reward": (meta.get("reward") or {}).get("reward"),
                "approval": meta.get("approval"),
                "source": meta.get("source"),
                "created_round": meta.get("created_round"),
            }
        )
    return LineageReport(agent_id=agent_id, versions=versions)


@dataclass(frozen=True)
class CollapseResult:
    """塌缩判定结果（决策 5）。"""

    collapsed: bool
    start_round: int | None  # 1-based 轮次号
    threshold: float
    window: int


def detect_collapse(rewards: list[float], *, window: int, threshold: float) -> CollapseResult:
    """塌缩判定：连续 window 轮 reward < 首轮基线 × threshold（抗噪连续判定）。"""
    if len(rewards) < window or not rewards:
        return CollapseResult(False, None, threshold, window)
    baseline = rewards[0]
    limit = baseline * threshold
    for start in range(1, len(rewards) - window + 1):
        if all(r < limit for r in rewards[start : start + window]):
            return CollapseResult(True, start + 1, threshold, window)
    return CollapseResult(False, None, threshold, window)


@dataclass(frozen=True)
class EvolutionCurve:
    """进化曲线（data-model §1 EvolutionCurve schema）。"""

    agent_id: str
    rounds: list[dict]
    baseline_reward: float | None
    collapse: dict
    plateau_note: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def build_curve(
    agent_id: str,
    history_dir: str | Path,
    collapse_window: int,
    collapse_threshold: float,
) -> EvolutionCurve:
    """进化曲线：逐轮胜出 reward 序列 + 塌缩检测 + 平台期备注。"""
    directory = Path(history_dir) / agent_id
    rounds: list[dict] = []
    if directory.is_dir():
        for seq, path in enumerate(sorted(directory.glob("dream-*.json")), start=1):
            payload = json.loads(path.read_text(encoding="utf-8"))
            winner = payload.get("winner_version")
            if payload.get("status") != "completed" or not winner:
                continue  # 失败轮次跳过（不进入曲线与塌缩序列）
            reward = next(
                (
                    c["reward"]["reward"]
                    for c in payload.get("candidates", [])
                    if c.get("version") == winner and c.get("reward")
                ),
                None,
            )
            rounds.append({"round": seq, "winner_version": winner, "reward": reward})

    rewards = [r["reward"] for r in rounds if r["reward"] is not None]
    baseline = rewards[0] if rewards else None
    collapse = detect_collapse(rewards, window=collapse_window, threshold=collapse_threshold)

    # 平台期备注：连续两轮胜出同一版本
    plateau_note = None
    winners = [r["winner_version"] for r in rounds]
    for i in range(len(winners) - 1):
        if winners[i] == winners[i + 1] and winners[i]:
            plateau_note = (
                f"第 {rounds[i]['round']}、{rounds[i + 1]['round']} 轮连续胜出同一版本 "
                f"{winners[i]}（平台期观察）"
            )
            break

    return EvolutionCurve(
        agent_id=agent_id,
        rounds=rounds,
        baseline_reward=baseline,
        collapse={
            "collapsed": collapse.collapsed,
            "start_round": collapse.start_round,
            "threshold": collapse.threshold,
            "window": collapse.window,
        },
        plateau_note=plateau_note,
    )
