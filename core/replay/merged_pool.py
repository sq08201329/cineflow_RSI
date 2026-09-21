"""跨项目合并池构建（功能 011 US1，contracts/pooling.md C1）。

- 分组：按 (Agent, 形态) —— 读多项目发现树（001 三维索引的跨项目查询）；
- 版本分组键：config_snapshot 中**评估器组合**快照的规范化哈希——同 hash = 同语义；
  跨 hash 不混池（原则一：评估器版本是得分语义的前提）；
- 前置条件：同 Agent 同形态树 ≥ `replay.pooling.min_trees`（不足即拒绝并注明，FR-002）；
- 可重现：树按 (created_at, project_id) 字典序稳定排序；pool_id = 分组输入的确定性哈希；
- 跨形态：未显式 `allow_cross_form` 即拒绝并入其他形态的树（FR-007）。

池化是读路径扩展（无树写入、无新 DB 表）；分组规模在立项书前置条件下可控。
"""

import json
from dataclasses import replace

import blake3

from core.replay.errors import PoolError, ValidationError
from core.replay.pooling_models import (
    MergedPool,
    PoolingConfig,
    PoolTreeRef,
    VersionGroup,
)
from core.replay.simulator import ensure_frozen

# 评估器版本集在 config_snapshot 中的记录键（各 Agent 轮次树统一写入）
EVALUATOR_VERSIONS_KEY = "evaluator_versions"

# 形态标注在 config_snapshot 中的记录键（既有轮次树可不写；未写即按池形态归入并注明）
POOL_FORM_KEY = "form"


def normalized_evaluator_versions(config_snapshot: dict) -> dict:
    """取 config_snapshot 的评估器版本集（归一为非空 {评估器 ID: 版本} 字符串映射）。

    缺失/非映射/空映射/非字符串条目即拒绝——未知版本集不得静默混进同一版本组。
    """
    if not isinstance(config_snapshot, dict):
        raise ValidationError(
            f"config_snapshot 必须为 dict，实际为 {type(config_snapshot).__name__}"
        )
    versions = config_snapshot.get(EVALUATOR_VERSIONS_KEY)
    if not isinstance(versions, dict) or not versions:
        raise ValidationError(
            f"config_snapshot 缺少非空 {EVALUATOR_VERSIONS_KEY}（评估器组合快照）：无法版本分组"
        )
    normalized = {}
    for evaluator_id, version in versions.items():
        if not isinstance(evaluator_id, str) or not evaluator_id:
            raise ValidationError(
                f"{EVALUATOR_VERSIONS_KEY} 的评估器 ID 必须为非空字符串，实际为 {evaluator_id!r}"
            )
        if not isinstance(version, str) or not version:
            raise ValidationError(
                f"{EVALUATOR_VERSIONS_KEY}[{evaluator_id!r}] 的版本必须为非空字符串，"
                f"实际为 {version!r}"
            )
        normalized[evaluator_id] = version
    return normalized


def evaluator_versions_hash(config_snapshot: dict) -> str:
    """版本分组键：评估器组合快照（version map）的规范化 BLAKE3。

    - 只取 evaluator_versions（评估器组合）——config_snapshot 其余键（权重/观测白名单/
      形态标注/合成口径等配置微调）不参与：**版本集一致即语义一致**，
      微调由池内 config_note 如实标注，不影响分组（澄清口径）；
    - 规范化：评估器 ID 字典序 + 紧凑分隔符 → 同语义必得同 hash；
    - 返回值 = 64 位小写十六进制（BLAKE3）。
    """
    versions = normalized_evaluator_versions(config_snapshot)
    payload = json.dumps(versions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return blake3.blake3(payload.encode("utf-8")).hexdigest()


def _stable_key(ref: PoolTreeRef) -> tuple:
    """稳定排序键：(created_at, project_id) 字典序 + tree_id 末位 tie-break。

    前两段是规格口径（跨项目时间重叠按 project_id 定序）；末段只用于
    同项目同时间戳的兜底，保证任何输入都得到唯一顺序（可重现）。
    """
    return (ref.created_at, ref.project_id, ref.tree_id)


def _codec_payload(value) -> str:
    """规范化 JSON（键排序 + 紧凑分隔符）——确定性哈希的序列化口径。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tree_ref_payload(ref: PoolTreeRef) -> dict:
    return {
        "tree_id": ref.tree_id,
        "project_id": ref.project_id,
        "created_at": ref.created_at,
        "node_count": ref.node_count,
    }


def pool_id_for(
    *,
    agent_id: str,
    form: str,
    min_trees: int,
    enabled_for_dreaming: bool,
    version_groups: tuple,
) -> str:
    """pool_id = 分组输入的确定性哈希（同输入必得同 id，幂等落盘的前提）。

    输入 = (Agent, 形态) + min_trees + 做梦开关 + 版本分组（含每组的树清单与顺序）；
    树集合/顺序/版本集任一变化即新 id——池内容变则标识必变（不得复用旧标识）。
    """
    groups = tuple(version_groups)
    payload = {
        "agent_id": agent_id,
        "form": form,
        "min_trees": min_trees,
        "enabled_for_dreaming": enabled_for_dreaming,
        "version_groups": [
            {
                "evaluator_versions_hash": group.evaluator_versions_hash,
                "trees": [_tree_ref_payload(ref) for ref in group.trees],
            }
            for group in groups
        ],
    }
    return blake3.blake3(_codec_payload(payload).encode("utf-8")).hexdigest()


def _declared_form(tree) -> str | None:
    """树自报形态（config_snapshot["form"]）；未标注即 None（按池形态归入）。"""
    declared = tree.config_snapshot.get(POOL_FORM_KEY)
    if declared is None:
        return None
    if not isinstance(declared, str) or not declared:
        raise ValidationError(
            f"config_snapshot[{POOL_FORM_KEY!r}] 必须为非空字符串，实际为 {declared!r}"
        )
    return declared


def _config_note(reference: dict, snapshot: dict) -> str:
    """同版本组内的 config 微调附注（版本集以外的键差异不影响分组，如实标注）。"""
    differing = sorted(
        key
        for key in set(reference) | set(snapshot)
        if key != EVALUATOR_VERSIONS_KEY and reference.get(key) != snapshot.get(key)
    )
    if not differing:
        return ""
    return (
        "config_snapshot 与组内基准不同（" + "、".join(differing) + "）：不影响分组——"
        "评估器版本集一致即语义一致"
    )


def build_merged_pool(
    store,
    agent_id: str,
    form: str,
    cfg: PoolingConfig,
    *,
    enforce_min_trees: bool = True,
) -> MergedPool:
    """构建跨项目合并池（C1）：分组 → 版本分组 → 前置条件 → 稳定排序 → 池标识。

    - 读入：`store.trees_by(agent_id=...)`（001 三维索引的跨项目查询，project_id 随树保留）；
    - 版本分组：按 evaluator_versions_hash 分组（跨版本不混池）；组内树按
      (created_at, project_id, tree_id) 稳定排序；版本组按组内最早树时间排序；
    - 前置条件：树数 ≥ cfg.min_trees；不足即拒绝（`enforce_min_trees=False` 时改为
      返回 conditions_met=False 的池并注明——供做梦开关/验收路径读取前置判定，不抛错）；
    - 跨形态：树自报形态 ≠ 池形态且未开 allow_cross_form 即整池拒绝（不留半成品）；
    - 入池校验：未冻结（根节点未落盘）拒绝；缺评估器版本集拒绝。
    """
    if not isinstance(agent_id, str) or not agent_id:
        raise ValidationError(f"agent_id 必须为非空字符串，实际为 {agent_id!r}")
    if not isinstance(form, str) or not form:
        raise ValidationError(f"form 必须为非空字符串，实际为 {form!r}")
    if not isinstance(cfg, PoolingConfig):
        raise ValidationError(f"cfg 必须为 PoolingConfig，实际为 {type(cfg).__name__}")
    if not isinstance(enforce_min_trees, bool):
        raise ValidationError(f"enforce_min_trees 必须为 bool，实际为 {enforce_min_trees!r}")

    # 跨形态拒绝：先整批判定（拒绝即无池，不留"只并了同形态"的半成品）
    rejected: list[tuple[str, str]] = []
    admitted = []
    unlabeled = 0
    for tree in store.trees_by(agent_id=agent_id):
        declared = _declared_form(tree)
        if declared is None:
            unlabeled += 1  # 未标注形态：按池形态归入（池附注如实说明）
            admitted.append((tree, declared))
        elif declared != form and not cfg.allow_cross_form:
            rejected.append((tree.tree_id, declared))
        else:
            admitted.append((tree, declared))
    if rejected:
        detail = "；".join(f"{tree_id}（自报形态 {declared}）" for tree_id, declared in rejected)
        raise PoolError(
            f"跨形态合并需显式配置 replay.pooling.allow_cross_form=true，拒绝并入：{detail}"
        )

    # 版本分组：冻结校验 + 树引用（时间戳取根节点 created_at）
    grouped: dict[str, list[PoolTreeRef]] = {}
    snapshots: dict[str, dict] = {}
    for tree, _declared in admitted:
        ensure_frozen(store, tree)
        nodes = store.nodes_of(tree.tree_id)
        root = next((node for node in nodes if node.parent_id is None), None)
        if root is None:
            raise PoolError(f"树缺少根节点（无法确定池内时间戳）：{tree.tree_id}")
        ref = PoolTreeRef(
            tree_id=tree.tree_id,
            project_id=tree.project_id,
            created_at=root.created_at,
            node_count=len(nodes),
        )
        key = evaluator_versions_hash(tree.config_snapshot)
        grouped.setdefault(key, []).append(ref)
        snapshots.setdefault(key, {}).setdefault(tree.tree_id, tree.config_snapshot)

    tree_count = sum(len(refs) for refs in grouped.values())
    if tree_count == 0:
        raise PoolError(
            f"合并池构建拒绝：{agent_id}/{form} 无可用发现树"
            f"（前置条件不足：0 棵 < min_trees={cfg.min_trees}）"
        )

    version_groups = []
    for key in sorted(grouped, key=lambda k: (min(ref.created_at for ref in grouped[k]), k)):
        refs = sorted(grouped[key], key=_stable_key)
        reference = snapshots[key][refs[0].tree_id]
        annotated = []
        for ref in refs:
            note = _config_note(reference, snapshots[key][ref.tree_id])
            annotated.append(replace(ref, config_note=note) if note else ref)
        version_groups.append(
            VersionGroup(
                evaluator_versions_hash=key,
                trees=tuple(annotated),
                evaluator_versions=normalized_evaluator_versions(reference),
            )
        )

    notes = []
    cross_form_notes = [
        f"跨形态并入（allow_cross_form=true）：{tree.tree_id}（{declared} → {form}）"
        for tree, declared in admitted
        if declared is not None and declared != form
    ]
    notes.extend(sorted(cross_form_notes))
    if unlabeled:
        notes.append(f"形态未标注的树按池形态归入：{unlabeled} 棵（config_snapshot 无 form 键）")
    conditions_met = tree_count >= cfg.min_trees
    if not conditions_met:
        insufficient = (
            f"前置条件不足：同 Agent 同形态树 {tree_count} 棵 < min_trees={cfg.min_trees}"
        )
        if enforce_min_trees:
            raise PoolError(f"合并池构建拒绝：{insufficient}")
        notes.append(insufficient)

    groups = tuple(version_groups)
    return MergedPool(
        pool_id=pool_id_for(
            agent_id=agent_id,
            form=form,
            min_trees=cfg.min_trees,
            enabled_for_dreaming=cfg.enabled_for_dreaming,
            version_groups=groups,
        ),
        agent_id=agent_id,
        form=form,
        version_groups=groups,
        min_trees=cfg.min_trees,
        conditions_met=conditions_met,
        tree_count=tree_count,
        note="；".join(notes),
    )
