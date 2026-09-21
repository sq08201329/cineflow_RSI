# 数据模型：跨项目发现树合并回放（011-cross-project-replay）

> 本特性**只读**（无新 DB 表）——池构建/匹配/统计是读路径扩展；快照与报告文件化
> （git 版本化，同 005/010 惯例）。frozen dataclass 为应用层第一道工序。

## 领域模型（`core/replay/`）

- **MergedPool（合并池）**：pool_id / agent_id / form / version_groups: [VersionGroup] /
  tree_count / build_snapshot 引用 / 前置条件判定（min_trees 满足与否）
- **VersionGroup（版本分组）**：evaluator_versions_hash（config_snapshot 中评估器组合
  快照哈希）/ trees: [{tree_id, project_id, created_at, node_count, config_note}]
  ——同版本集的同语义单位；跨版本不混池
- **ScoreConflict（得分冲突）**：structure_key / hits: [{tree_id, project_id, score}] /
  note——多棵命中且得分不同的诊断（UNKNOWN 的理由）
- **HitDistribution（命中分布）**：per_project: [{project_id, hits, unknowns, hit_ratio,
  tree_count, tree_ratio}] / merged: {hits, unknowns} / conflicts: [ScoreConflict]
  ——per-project 与合并口径双报告
- **DilutionAlert（稀释告警）**：project_id / hit_ratio（判定口径）/ tree_ratio（参考）/
  threshold / note（含"单项目构成"标注）
- **PoolSnapshot（构建快照）**：分组、版本分组、树清单（project_id + created_at）、
  min_trees 判定、dreaming 开关状态、构建时间——文件化（`replay/pools/{agent}/{form}/{pool_id}.json`）
- **CrossProjectLineage（跨项目谱系报表）**：policy_version → trees: [{tree_id, project_id}] →
  child_versions: [{version, project_id}]——JSON 可机读，项目归属标注

## 关键约束

- **版本分组键** = config_snapshot 中评估器组合的快照哈希（`evaluator_versions_hash`）——
  同 hash = 同语义；跨 hash 不混池
- **匹配键** = 结构键（009 定案：策略可复现的结构键）+ 版本集 hash——跨项目同结构同
  版本集 → 命中；冲突（多棵命中得分不同）→ UNKNOWN
- **排序** = (created_at, project_id) 字典序稳定——池构建可重现的前提
- **分树** = 全局时间排序，最近树（跨项目）只做 validation
