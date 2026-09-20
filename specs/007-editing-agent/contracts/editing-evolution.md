# 契约：回放接入、无偏性验收与做梦进化

> 对应规格 US3 / FR-010~012。实现：复用 002/005/010 泛化能力 + 剪辑侧策略与配置。

## C14 回放接入

- 剪辑树冻结入池（`freeze_round_tree`：job 未到终态拒绝冻结，004/006 同构）；
  observed/probe、规范化精确匹配（EDL 规范化 JSON 相等）、UNKNOWN 语义复用 002
- **回放零渲染零生成**：审计断言（适配器 render/generate 调用计数恒 0 + 网关零调用）
- train/validation 分树：最近树永远只做 validation（005 口径）

## C15 无偏性验收（发布阻塞）

- 回放打分 vs 真实重跑（模拟渲染器重执行 + 五评估器重算）得分序列 Kendall τ ≥ 0.95
- 注入偏差（篡改评估器版本/judge 胜率/节奏口径）100% 拒绝
- 实现：复用 `core/replay/unbiasedness.py` 套件口径 + 剪辑夹具池

## C16 做梦进化

- champion 策略 `policies/history/editing/{version}.py`（BLAKE3 前 12 位）+ meta.json 谱系；
  dreaming 管线 agent_id="editing" 零改动运行（006 已实证泛化）
- 一轮做梦（演示档 M=8）：候选静态检查 → 沙箱回放 → reward 排名 → 首轮基线落盘
- 部署沿用人工 approve（F9 未交付）
- 剪辑纳入 010 盲评（`build_blind_list(agent_id="editing")` 正常产出；静态证明
  dreaming/010 无 editing 特判）

### 场景

1. 剪辑池 2 树 → τ ≥ 0.95 通过；注入偏差 → 拒绝
2. 回放全程 → render/网关调用计数 0
3. 演示档做梦一轮 → reward 排名 + 首轮基线落盘
4. 010 盲评对 editing 正常产出；promo 特判回归不破
