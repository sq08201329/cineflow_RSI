# 契约：锚点通道（盲评清单 + 录入 + 冻结）

> 对应规格 US1 / FR-001~003。实现：`core/calibration/selection.py`、`anchors.py`，
> CLI 入口 `ops/calibrate.py`。

## C1 盲评清单生成

```
build_blind_list(tree_store, agent_id, period, top_k) -> BlindList
```

- 输入：`agent_id`、周期（起止日期）、`top_k`（配置 `calibration.top_k`，默认 5）
- 行为：按节点 score 降序取周期内 top-k；样本不足取实际数量并注明
- **硬约束**：清单条目序列化键白名单 = `{node_id, artifact_hash, round_id}`；
  任何 score / eval_breakdown / 得分相关键禁止出现（FR-002）
- **范围约束**：仅采用人评锚点的 Agent 可调用；对 promo 调用必须报错
  （其锚点为 platform_truth，不盲评）

### 场景

1. 周期内 12 个得分节点，k=5 → 清单 5 条，按 score 降序
2. 周期内 3 个节点，k=5 → 清单 3 条，round 备注"样本不足"
3. 清单 JSON 序列化 → 递归扫描无 `score`/`eval_breakdown` 键（契约测试断言）
4. 对 `agent_id="promo"` 调用 → ValidationError（不盲评）

## C2 人评录入

```
intake_anchors(conn, round_id, entries: list[{node_id, score, reviewer}]) -> int
```

- 校验：score ∈ [0,1]；reviewer 非空；round 存在且状态 ∈ {open, intake}；
  node_id 在该轮清单内（防录错节点）
- 写入：`calibration_anchors` INSERT（source=`human_blind`）；同
  `(node_id, reviewer, round_id)` 唯一冲突 → 该条拒绝并计数，整批不中断
- CLI：`uv run python ops/calibrate.py intake --round <round_id> --file anchors.json`

### 场景

1. 合法 3 条 → 全部入库，返回 3
2. score=1.2 → 该条拒绝，错误注明取值域
3. 重复提交同键 → 拒绝且不产生变更（幂等）
4. 对已入库锚点执行 UPDATE/DELETE → 触发器拒绝（SQLite 单测 + PG 集成双侧证明）

## C3 平台真值锚点适配

```
collect_platform_anchors(promo_db, period) -> list[AnchorScore]
```

- 实现于 `agents/promo/anchors.py`（业务侧适配，保 core 业务无关）
- 读 promo 运营回流表，把周期内回流真值归一化得分（003 既有 `metric_weights` 口径）
  转为 source=`platform_truth` 的 AnchorScore，reviewer 记渠道标识
- 写入同一 `calibration_anchors` 表，同等冻结纪律

### 场景

1. 周期内 12 条回流 → 12 条 platform_truth 锚点入库
2. 同轮重复采集 → 唯一键幂等拒绝，不产生重复锚点
