"""跨项目谱系查询（功能 011 US3 / T1020，contracts/lineage-acceptance.md C6）。

- **只读**扩展（无新 DB 表）：树侧走 001 三维索引的跨项目读路径
  （`trees_by(policy_version=...)` / `trees_by(agent_id=..., policy_version=...)`）；
  子版本侧走 005 文件化 meta（`{history_root}/{agent_id}/{version}.meta.json` 的
  `parent_version` 父链，core 不依赖 dreaming——单向依赖，原则五）；
- 报表：policy_version → 跨项目产出的树（**project_id 标注**）→ 跨项目子策略版本
  （**项目归属标注**）；JSON 可机读（`to_dict` / `to_json`）；
- 诚实边界：单项目版本的跨项目字段为**空列表而非缺失**；子版本无产出树（归属未知）、
  未提供策略历史根目录、无产出树等一律如实进 note，不静默、不编造归属。
"""

import json
from pathlib import Path

from core.replay.errors import ValidationError
from core.replay.pooling_models import ChildVersionRef, CrossProjectLineage, LineageTreeRef
from core.tree.store import TreeStore

META_SUFFIX = ".meta.json"

# meta 读取所需字段（005 谱系 schema 的子集：core 只读父链，不重校验 dreaming schema）
_REQUIRED_META_FIELDS = ("version", "parent_version")


def _trees_of_version(store: TreeStore, policy_version: str, agent_id: str | None):
    """该策略版本产出的树（agent_id 给出即限该 Agent，避免跨 Agent 同版本号混淆）。"""
    if agent_id is None:
        return store.trees_by(policy_version=policy_version)
    return store.trees_by(agent_id=agent_id, policy_version=policy_version)


def _read_parent_links(history_root: Path, agent_id: str) -> dict[str, str | None]:
    """读策略历史的父链（version → parent_version）；缺字段即拒绝（谱系完整性优先）。"""
    links: dict[str, str | None] = {}
    directory = history_root / agent_id
    if not directory.is_dir():
        return links
    for path in sorted(directory.glob(f"*{META_SUFFIX}")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        for field in _REQUIRED_META_FIELDS:
            if field not in meta:
                raise ValidationError(f"谱系 meta 缺少字段 {field!r}：{path.name}")
        filename_version = path.name[: -len(META_SUFFIX)]
        if meta["version"] != filename_version:
            raise ValidationError(
                f"谱系冲突：{path.name} 的 version 字段 {meta['version']!r} 与文件名不一致"
            )
        links[meta["version"]] = meta["parent_version"]
    return links


def cross_lineage(
    store: TreeStore,
    policy_version: str,
    *,
    agent_id: str | None = None,
    history_root: str | Path | None = None,
) -> CrossProjectLineage:
    """跨项目谱系报表（C6）：该策略版本产出的树 + 其子策略版本（含项目归属）。

    - store：树存储（001 三维索引只读查询）；
    - agent_id：限定 Agent（给出时同时用于读树与读策略历史；不给 = 全库按版本号查）；
    - history_root：策略历史根目录（给出则解析子版本父链；缺省 → 子版本为空并注明）。
    """
    if not hasattr(store, "trees_by"):
        raise ValidationError(
            f"store 必须为可读树存储（TreeStore 协议：trees_by），实际为 {type(store).__name__}"
        )
    if not isinstance(policy_version, str) or not policy_version:
        raise ValidationError(f"policy_version 必须为非空字符串，实际为 {policy_version!r}")
    if history_root is not None and (not isinstance(agent_id, str) or not agent_id):
        raise ValidationError("提供 history_root 时必须同时给出 agent_id（策略历史按 Agent 分层）")

    notes: list[str] = []
    trees = tuple(
        LineageTreeRef(tree_id=tree.tree_id, project_id=tree.project_id)
        for tree in sorted(
            _trees_of_version(store, policy_version, agent_id),
            key=lambda tree: (tree.project_id, tree.tree_id),
        )
    )

    child_versions: list[ChildVersionRef] = []
    if history_root is None:
        notes.append("未提供策略历史根目录：跨项目子策略版本为空（树侧链路不受影响）")
    else:
        links = _read_parent_links(Path(history_root), str(agent_id))
        orphans: list[str] = []
        for child_version in sorted(
            version for version, parent in links.items() if parent == policy_version
        ):
            child_trees = _trees_of_version(store, child_version, agent_id)
            projects = sorted({tree.project_id for tree in child_trees})
            if not projects:
                orphans.append(child_version)
                continue  # 归属未知：不编造，进 note
            child_versions.extend(
                ChildVersionRef(version=child_version, project_id=project_id)
                for project_id in projects
            )
        if orphans:
            notes.append("子策略版本无产出树（项目归属未知，未列出）：" + "、".join(orphans))
    if not trees:
        notes.append(f"策略版本 {policy_version} 在树库中无产出树")

    return CrossProjectLineage(
        policy_version=policy_version,
        trees=trees,
        child_versions=tuple(child_versions),
        note="；".join(notes),
    )
