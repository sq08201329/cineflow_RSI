# 契约：合并池构建与快照（分组/版本/前置条件/可重现）

> 对应规格 US1 / FR-001~003、FR-007。实现：`core/replay/merged_pool.py`、`pool_snapshot.py`。

## C1 合并池构建

```
build_merged_pool(store, agent_id, form, cfg) -> MergedPool
```

- 读多项目发现树（001 三维索引的跨项目查询）；按 (Agent, 形态) 分组
- 评估器版本集分组（`evaluator_versions_hash`）；跨版本不混池
- 前置条件：树数 ≥ `replay.pooling.min_trees`（默认 3）；不足即拒绝并注明
- **可重现**：同输入产同池（树集合与顺序确定——(created_at, project_id) 字典序）
- 跨形态：未显式 `allow_cross_form` 即拒绝把其他形态树并入

### 场景

1. 项目 A/B 各 2 棵同 Agent 同形态树 → 池含 4 棵（归属可追溯）
2. 评估器版本不同的树 → 按版本集分组，不混池
3. 树 < 3 → 拒绝并注明"前置条件不足"
4. 同输入构建两次 → 池内容一致（快照哈希相等）
5. 短剧形态树未显式开启并入电影形态池 → 拒绝

## C2 构建快照

```
persist_pool_snapshot(pool, cfg) -> PoolSnapshot  # replay/pools/{agent}/{form}/{pool_id}.json
```

- 不可变记录：分组、版本分组、树清单、min_trees 判定、dreaming 开关状态、构建时间
- 只增不改（重复构建同输入 → 同快照，幂等）

### 场景

1. 首次构建 → 快照落盘（字段齐全）
2. 重复构建同输入 → 幂等（快照一致，不重复落盘）
3. dreaming 开关开启/关闭 → 快照如实记录开关状态与前置判定
