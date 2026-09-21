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

import blake3

from core.replay.errors import ValidationError

# 评估器版本集在 config_snapshot 中的记录键（各 Agent 轮次树统一写入）
EVALUATOR_VERSIONS_KEY = "evaluator_versions"


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
