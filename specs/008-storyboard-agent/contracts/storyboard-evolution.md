# 契约：回放接入、无偏性验收、做梦进化与下游交接

> 对应规格 US3 / FR-010~012。实现：复用 002/005/010 泛化能力 + 分镜侧策略与配置。

## C14 回放接入

- 分镜树冻结入池（`freeze_round_tree` 终态门禁，004/006/007 同构）
- 观测双键（`shotlist` + `gen_params`）→ 002 规范化精确匹配零特判命中；UNKNOWN 语义复用
- **回放零渲染零生成**：审计断言（适配器 render 调用计数恒 0 + 网关零调用）
- train/validation 分树：最近树永远只做 validation（005 口径）

## C15 无偏性验收（发布阻塞）

- 回放打分 vs 真实重跑（模拟渲染器重执行 + 五评估器重算）得分序列 Kendall τ ≥ 0.95
- 注入偏差（篡改评估器版本 / judge 胜率 / 对齐口径等 ≥3 形态）100% 拒绝

## C16 做梦进化与 010 接入

- champion 策略 `policies/history/storyboard/{version}.py`（BLAKE3 前 12 位）+ meta.json；
  dreaming agent_id="storyboard" 零改动运行；演示档 M=8 首轮基线落盘
- 分镜纳入 010 盲评（`build_blind_list(agent_id="storyboard")` 正常产出）
- 部署沿用人工 approve（F9 未交付）

## C17 下游 schema 稳定性（FR-012）

- ShotList 序列化 schema 带 `schema_version`，字段名/枚举值文档化；契约测试断言
  schema 快照稳定（防后续漂移）——视觉线（004）与剪辑线（007）的接入留给各自特性

### 场景

1. 分镜池 2 树 → τ ≥ 0.95 通过；注入偏差 → 拒绝
2. 回放全程 → render/网关调用计数 0
3. 演示档做梦一轮 → reward 排名 + 首轮基线落盘
4. 010 盲评对 storyboard 正常产出；dreaming/010 无 storyboard 特判（静态证明）
5. ShotList schema 快照断言通过（字段名/枚举值与文档一致）
