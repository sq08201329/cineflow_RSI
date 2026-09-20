# 契约：回放接入、无偏性验收与做梦进化

> 对应规格 US3 / FR-008~011。实现：复用 002/005 泛化能力 + 声音侧策略与配置。

## C13 回放接入

- 声音树冻结后入模拟器池（002 池化语义）；observed/probe、规范化精确匹配
  （SoundGenParams 规范化 JSON 相等）、UNKNOWN 语义全部复用——回放零生成（审计断言）
- train/validation 分树：最近树永远只做 validation（005 口径）

## C14 无偏性验收（发布阻塞）

- 回放打分 vs 真实重跑（模拟生成器重执行）得分序列 Kendall τ ≥ 0.95
- 注入偏差用例（篡改某评估器版本/得分）100% 被拒
- 实现：复用 `core/replay/unbiasedness.py` 套件口径 + 声音夹具池

## C15 做梦进化

- champion 策略 `policies/history/sound/{version}.py`（BLAKE3 前 12 位版本）+
  meta.json 谱系；dreaming 管线 agent_id="sound" 零改动运行（research 决策 8）
- 一轮做梦（演示档）：候选生成 → 静态检查 → 沙箱回放 → reward 排名 →
  首轮基线落盘（进化曲线起点，验收口径同 005）
- 部署沿用人工 approve（宪章 v1.1.0 下 F9 未交付，approve 流程不变）

## C16 周校准纳入

- sound 纳入 010 盲评范围（仅 promo 被特判拒绝，sound 配置即纳入）；
  proxy 层（asr/emotion）有人评锚点需求；契约测试防"sound 被拒"回归

### 场景

1. 声音池 2 棵树 → 无偏性 τ ≥ 0.95 通过；注入偏差 → 拒绝
2. 回放全程 → 适配器 generate 调用计数 0（审计断言）
3. 一轮做梦（演示档 M=8）→ reward 排名 + 首轮基线落盘，LLM/生成零调用可断
4. 010 build_blind_list(agent_id="sound") → 正常产出清单（不被 promo 特判拦截）
