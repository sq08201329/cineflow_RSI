"""合并池构建快照（功能 011 US1，contracts/pooling.md C2）。

- 落盘：`replay/pools/{agent}/{form}/{pool_id}.json`（git 版本化，同 005/010 文件化惯例）；
- 只增不改：同 pool_id 已存在即返回既有快照（幂等——不重写文件，构建时间保持首写值）；
  既有内容与本轮构建不一致（池内容漂移）即拒绝覆盖；
- 内容哈希：分组/版本分组/树清单/前置判定/开关/附注的规范化 BLAKE3，
  **构建时间不入哈希**——同输入必得同哈希（幂等与"同输入同池"的机检落点）；
- 读取即机检：文件被改写（哈希不符）即拒绝，不给"看似可信"的快照。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import blake3

from core.replay.errors import PoolError, ValidationError
from core.replay.merged_pool import pool_id_for
from core.replay.pooling_models import (
    MergedPool,
    PoolingConfig,
    PoolSnapshot,
    PoolTreeRef,
    VersionGroup,
)

_TREE_REF_KEYS = ("tree_id", "project_id", "created_at", "node_count", "config_note")


def snapshot_path(pools_dir: str | Path, agent_id: str, form: str, pool_id: str) -> Path:
    """快照落盘路径：{pools_dir}/{agent}/{form}/{pool_id}.json（按 (Agent, 形态) 分层）。"""
    return Path(pools_dir) / agent_id / form / f"{pool_id}.json"


def _tree_ref_to_dict(ref: PoolTreeRef) -> dict:
    return {
        "tree_id": ref.tree_id,
        "project_id": ref.project_id,
        "created_at": ref.created_at,
        "node_count": ref.node_count,
        "config_note": ref.config_note,
    }


def _group_to_dict(group: VersionGroup) -> dict:
    return {
        "evaluator_versions_hash": group.evaluator_versions_hash,
        "evaluator_versions": dict(group.evaluator_versions),
        "trees": [_tree_ref_to_dict(ref) for ref in group.trees],
    }


def _content_payload(snapshot: PoolSnapshot) -> dict:
    """快照内容（不含构建时间与内容哈希自身）——哈希与落盘共用的唯一序列化口径。"""
    return {
        "pool_id": snapshot.pool_id,
        "agent_id": snapshot.agent_id,
        "form": snapshot.form,
        "version_groups": [_group_to_dict(group) for group in snapshot.version_groups],
        "trees": [_tree_ref_to_dict(ref) for ref in snapshot.trees],
        "min_trees": snapshot.min_trees,
        "conditions_met": snapshot.conditions_met,
        "enabled_for_dreaming": snapshot.enabled_for_dreaming,
        "note": snapshot.note,
    }


def snapshot_content_hash(snapshot: PoolSnapshot) -> str:
    """快照内容哈希（构建时间不入哈希）：同 pool_id 同内容必得同哈希。"""
    payload = json.dumps(
        _content_payload(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return blake3.blake3(payload.encode("utf-8")).hexdigest()


def snapshot_to_dict(snapshot: PoolSnapshot) -> dict:
    """快照 → JSON 可机读映射（落盘与外部比对口径）。"""
    return {
        **_content_payload(snapshot),
        "built_at": snapshot.built_at,
        "content_hash": snapshot.content_hash,
    }


def snapshot_from_dict(payload: dict) -> PoolSnapshot:
    """落盘映射 → PoolSnapshot；字段缺失/非法即拒绝，内容哈希不符即拒绝（只增不改机检）。"""
    if not isinstance(payload, dict):
        raise ValidationError(f"快照必须为映射，实际为 {type(payload).__name__}")
    try:
        version_groups = tuple(
            VersionGroup(
                evaluator_versions_hash=group["evaluator_versions_hash"],
                trees=tuple(
                    PoolTreeRef(**{key: ref[key] for key in _TREE_REF_KEYS})
                    for ref in group["trees"]
                ),
                evaluator_versions=dict(group.get("evaluator_versions", {})),
            )
            for group in payload["version_groups"]
        )
        snapshot = PoolSnapshot(
            pool_id=payload["pool_id"],
            agent_id=payload["agent_id"],
            form=payload["form"],
            version_groups=version_groups,
            trees=tuple(
                PoolTreeRef(**{key: ref[key] for key in _TREE_REF_KEYS}) for ref in payload["trees"]
            ),
            min_trees=payload["min_trees"],
            conditions_met=payload["conditions_met"],
            enabled_for_dreaming=payload["enabled_for_dreaming"],
            built_at=payload["built_at"],
            content_hash=payload.get("content_hash", ""),
            note=payload.get("note", ""),
        )
    except (KeyError, TypeError) as exc:
        raise ValidationError(f"快照字段缺失或非法：{exc!r}") from exc
    if snapshot.content_hash != snapshot_content_hash(snapshot):
        raise PoolError(f"快照内容哈希不一致（文件被改写？只增不改）：{snapshot.pool_id}")
    return snapshot


def load_pool_snapshot(path: str | Path) -> PoolSnapshot:
    """读取既有快照（不存在/不可解析/被改写即拒绝）。"""
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"构建快照不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"构建快照不可解析：{path}（{exc}）") from exc
    return snapshot_from_dict(payload)


def _with_hash(snapshot: PoolSnapshot) -> PoolSnapshot:
    """回填内容哈希（frozen：产出新实例）。"""
    return replace(snapshot, content_hash=snapshot_content_hash(snapshot))


def persist_pool_snapshot(
    pool: MergedPool,
    cfg: PoolingConfig,
    *,
    built_at: str | None = None,
) -> PoolSnapshot:
    """落盘构建快照（只增不改幂等），返回快照记录。

    - pool_id 机检：与分组输入（含落盘配置的做梦开关）不符即拒绝（不得张冠李戴）；
    - 首次落盘：写 {pools_dir}/{agent}/{form}/{pool_id}.json；
    - 重复落盘：同 id 已存在且内容一致 → 返回既有快照（不重写，构建时间保持首写值）；
      内容不一致（池内容漂移）→ 拒绝覆盖并报错（只增不改，绝不静默改写历史）。
    """
    if not isinstance(pool, MergedPool):
        raise ValidationError(f"pool 必须为 MergedPool，实际为 {type(pool).__name__}")
    if not isinstance(cfg, PoolingConfig):
        raise ValidationError(f"cfg 必须为 PoolingConfig，实际为 {type(cfg).__name__}")
    if built_at is not None and (not isinstance(built_at, str) or not built_at):
        raise ValidationError(f"built_at 必须为非空字符串或 None，实际为 {built_at!r}")

    expected_id = pool_id_for(
        agent_id=pool.agent_id,
        form=pool.form,
        min_trees=pool.min_trees,
        enabled_for_dreaming=cfg.enabled_for_dreaming,
        version_groups=pool.version_groups,
    )
    if expected_id != pool.pool_id:
        raise PoolError(
            "pool_id 与分组输入不一致（落盘配置的做梦开关/树清单与池不符）：拒绝落盘"
            f"（池 {pool.pool_id}，本轮输入 {expected_id}）"
        )

    path = snapshot_path(cfg.pools_dir, pool.agent_id, pool.form, pool.pool_id)
    snapshot = _with_hash(
        PoolSnapshot(
            pool_id=pool.pool_id,
            agent_id=pool.agent_id,
            form=pool.form,
            version_groups=pool.version_groups,
            trees=pool.trees,
            min_trees=pool.min_trees,
            conditions_met=pool.conditions_met,
            enabled_for_dreaming=cfg.enabled_for_dreaming,
            built_at=built_at or datetime.now(UTC).isoformat(timespec="seconds"),
            note=pool.note,
        )
    )
    if path.exists():
        existing = load_pool_snapshot(path)  # 被改写即在此拒绝（哈希机检）
        if existing.content_hash != snapshot.content_hash:
            raise PoolError(f"同 pool_id 快照内容不一致（只增不改，拒绝覆盖）：{path}")
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot
