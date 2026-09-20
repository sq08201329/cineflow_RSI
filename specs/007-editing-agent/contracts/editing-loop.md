# 契约：剪辑探索执行器（EDL 校验 + 预算门禁 + 两段式落盘）

> 对应规格 US1 / FR-001~005。实现：`agents/editing/loop.py`、`edl.py`、`shots.py`、`config.py`。

## C1 EDL 合法性（执行前四层校验）

```
validate_edl(edl, shot_library, scenes, transition_rules) -> None  # 违规即 ValidationError
```

- ① 引用存在（shot_id 在镜头库、音轨引用存在）；② 0 ≤ in_ms < out_ms ≤ 镜头时长；
  ③ 场景分区（clip 的 shot 归属分区正确、分区顺序不降）；④ 转场规则库
  （类型 ∈ allowed、叠化时长 ≤ max、同区跳切检测——配置驱动）
- **违规一律执行前拒绝**：0 渲染调用、0 成本（规格 FR-002 / 边界情况）

### 场景

1. 合法 EDL（6 镜头 3 分区）→ 通过
2. 引用不存在镜头 / 出点越界 / 跨分区选镜 / 场景乱序 / 非法转场 → 五类各拒绝，
   适配器 0 调用

## C2 一轮剪辑探索

```
run_editing_round(engine, store, gateway, policy, inputs, cfg, round_id) -> EditingRoundResult
```

- 流程：策略产 EDL 组合 → C1 校验 → 预算门禁（申请前校验 + 事务内复核）→ 渲染
  （昂贵动作仅此阶段）→ 内容寻址 → 五评估器 → quantize 定点 → 节点一次性 INSERT
- 幂等：job_id/tree_id 由 round_id 派生 + 唯一键 `(round_id, edl_hash)`，二次触发
  重建首轮结果（0 重复渲染、0 重复扣费）
- 素材可行性预检：素材总长 < 目标时长下限 → FAILED 节点如实落盘注明（规格边界情况）

### 场景

1. 3 组 EDL → 3 成片落树，成本入账（预估时长 × 价目）
2. 预算超界 → 超额拒绝，已执行照常入账
3. 同 round_id 二次触发 → 重建，渲染调用计数不增
4. 渲染失败条目 → failed + 成本照计，轮次继续
5. 镜头库空 / 不足最小镜头数 → 执行前拒绝注明"素材不足"

## C3 配置

`agents/editing/config.py` 读 configs `editing` 段：预算、时长容差、镜头限制、转场规则库、
pacing_baseline、render 价目与编码参数、judge 提示词/锚点集；缺基准曲线或价目即报错
（不允许静默无基准打分/零成本）。
