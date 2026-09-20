# 契约：分镜探索执行器（ShotList 校验 + 预算门禁 + 两段式落盘）

> 对应规格 US1 / FR-001~005。实现：`agents/storyboard/loop.py`、`shotlist.py`、`script.py`、`config.py`。

## C1 ShotList 合法性（执行前三层校验）

```
validate_shotlist(shotlist, script, grammar_rules) -> None  # 违规即 ValidationError
```

- ① 承接的剧本行存在（covers 的每个 line_id 在剧本内）；② 场景承接（每场景 ≥1 镜；
  key=True 行逐条被承接）；③ 景别/机位/运动档位在配置枚举内
- **违规一律执行前拒绝**：0 渲染调用、0 成本（FR-002 / 边界情况）

### 场景

1. 合法 ShotList（3 场景 9 镜）→ 通过
2. 引用不存在行 / 场景无镜头 / 关键行未承接 / 档位越界 → 四类各拒绝，适配器 0 调用

## C2 一轮分镜探索

```
run_storyboard_round(engine, store, gateway, policy, script, cfg, round_id) -> StoryboardRoundResult
```

- 流程：策略产 ShotList 组合 → C1 校验 → 预算门禁（申请前 + 事务复核）→ 预演渲染
  （昂贵动作仅此阶段）→ 内容寻址 → 五评估器 → quantize → 节点一次性 INSERT
- 观测落**双键**（`shotlist` + `gen_params`）——002 回放匹配槽零特判接入（007 教训复用）
- 幂等：唯一键 `(round_id, shotlist_hash)`，二次触发重建首轮结果
- 剧本不足预检（空场景列表 / 空行）→ 执行前拒绝注明

### 场景

1. 3 组 ShotList → 3 预演落树，成本入账（镜头数 × 价目）
2. 预算超界 → 超额拒绝，已执行照常入账
3. 同 round_id 二次触发 → 重建，渲染调用计数不增
4. 渲染失败条目 → failed + 成本照计，轮次继续
5. 剧本为空/无行 → 执行前拒绝注明"剧本不足"

## C3 配置

`agents/storyboard/config.py` 读 configs `storyboard` 段：预算、景别/机位/运动档位枚举、
镜头语法规则库、轴规则、情绪向量表、价目、judge 提示词/锚点集；缺规则库/价目即报错
（不允许静默放过门禁或零成本）。
