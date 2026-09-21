# 契约：池化回放、匹配与稀释控制

> 对应规格 US2 / FR-004~008。实现：`core/replay/cross_match.py`、`hit_stats.py`。

## C3 跨项目匹配（含冲突口径，澄清 Q1）

```
cross_match(pool, structure_key, version_hash) -> MatchResult | UNKNOWN
```

- 规范化精确匹配（结构键 + 版本集 hash）在池内全部项目树中查找
- 单棵命中 → 返回该历史节点得分（跨项目复用，不再 UNKNOWN）
- **多棵命中且得分不同（冲突）→ UNKNOWN + ScoreConflict 诊断**（树清单/得分/归属）；
  不取均值、不取最新、不终止回放
- 跨版本集不命中（评估器版本是匹配的组成）

### 场景

1. 项目 A 有、项目 B 无的结构键 → 命中 A 的得分（跨项目复用）
2. A 与 B 都有且得分相同 → 命中（一致）
3. A 与 B 都有但得分不同 → UNKNOWN + 冲突诊断（含两树归属与各自得分）
4. 版本集不同的树 → 不命中

## C4 命中分布与稀释告警（澄清 Q2）

```
hit_stats(pool, replay_results, cfg) -> (HitDistribution, list[DilutionAlert])
```

- per-project 与合并口径命中/UNKNOWN 双报告
- **判定 = 命中占比**（项目命中数 / 总命中数）超阈（`dilution_hit_ratio_threshold`，
  默认 0.7）→ DilutionAlert；树数占比作参考维度同报告
- 单项目构成（占比 1.0）→ 必然超阈，标注"单项目构成"
- 告警如实可见（不阻止回放）

### 场景

1. 项目 A 命中 8 / 项目 B 命中 2 → 分布双报告；A 占比 0.8 > 0.7 → 告警
2. 单项目池 → 占比 1.0 告警 + "单项目构成"标注
3. 冲突发生的回放 → conflicts 字段含 ScoreConflict（供 010/F7 消费）

## C5 零生成与分树

- 池化回放全程：生成/渲染/网关调用计数恒 0（审计断言）
- train/validation 分树：全局时间排序（tie-break project_id），最近树只做 validation

### 场景

1. 回放全程 → 调用计数 0
2. 跨项目分树 → 全局排序、最近树只在 validation
