# Quickstart：跨项目发现树合并回放（011-cross-project-replay）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "merged_pool or cross_match or hit_stats or cross_lineage"
uv run pytest tests/contract -k pooling              # 构建/快照/匹配/稀释契约
uv run pytest tests/unbiasedness -k merged           # 合并口径 τ ≥ 0.95 + 注入拒绝
uv run pytest tests/unit -k "pooling_acceptance"        # 做梦开关默认关闭 + 前置注明
```

## 端到端场景（demo 流程）

1. **构建合并池**：项目 A/B 各 ≥2 棵同 Agent 同形态树 → 分组合并、版本分组、快照落盘
2. **跨项目命中**：B 项目缺失的结构键命中 A 的历史得分（不再 UNKNOWN）
3. **冲突口径**：A/B 同结构得分不同 → UNKNOWN + 冲突诊断
4. **稀释控制**：命中分布双报告 + 占比超阈告警（含"单项目构成"标注）
5. **一致性验收**：同树双池回放得分序列一致；合并口径 τ ≥ 0.95
6. **做梦开关**：默认单项目池（快照注明）；开启后合并池可用

## 里程碑验收（立项书 WS2 / SC-001）

合并池构建可重现 + 跨项目命中 + 稀释告警可机检 + 合并口径 τ ≥ 0.95；覆盖率 ≥85% 不降。

## 验证记录（2026-09-21，T1024）

| 命令 | 结果 |
| --- | --- |
| `uv sync` | Resolved 35 packages / Checked 30 packages（零新增依赖） |
| `uv run pytest tests/unit -k "merged_pool or cross_match or hit_stats or cross_lineage"` | 67 passed, 1922 deselected |
| `uv run pytest tests/contract -k pooling` | 34 passed, 160 deselected（C1~C9 全场景 + SC-002/003/004/006 机检） |
| `uv run pytest tests/unbiasedness -k merged` | 5 passed（τ=1.0 放行；逆序/乱序/得分抹平/跨版本混池 4 形态 100% 拒绝） |
| `uv run pytest tests/unit -k "pooling_acceptance"` | 8 passed（同树双池一致 15 组 100%、开关默认关闭 + 前置不足回落注明） |
| `uv run pytest tests/unit tests/contract` | 2127 passed, 56 skipped（跳过为真实适配器无凭证用例） |
| `uv run pytest tests/unbiasedness` | 30 passed（含 002/006/007/008/009 既有门禁） |
| `uv run python ops/demo_merged_pool.py` | **退出码 0**（六步全 ok，约 0.43s；报告 JSON 落 stdout，`ok=true`） |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 366 files already formatted |
| `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-report=term-missing --cov-fail-under=85` | 1989 passed；**TOTAL 93%**（≥85% 达标）；`cross_lineage.py` 96% / `cross_match.py` 86% / `hit_stats.py` 93% / `merged_pool.py` 95% / `pool_snapshot.py` 94% / `pooled_replay.py` 90% / `pooling_models.py` 96% / `simulator.py` 98% |

**实测口径（demo 六步输出摘要）**

1. 构建：6 棵 / 2 个版本组（v1 组 5 棵跨 A/B/C、v2 组 1 棵）→ 前置条件满足；快照
   `replay/pools/.../{pool_id}.json` 落盘且幂等重放逐字节一致；
2. 跨项目命中：A 的根 + `b-only` → `ok` 得分 0.75 且归属 `project-b`（B 的根 + `a-only` → `unknown`，
   单项目池确实是 UNKNOWN）；
3. 冲突：A/B 同结构键同版本集不同分（0.6 vs 0.8）→ `unknown` + `ScoreConflict`
   （命中树/项目归属/得分/不取均值诊断齐全）；
4. 稀释：per-project 与合并口径双报告（命中占比 1.0 > 0.7 告警；树数占比 0.3333 仅作参考）；
   单项目构成 → 占比 1.0 告警 + note 含"单项目构成"；
5. 一致性 + 无偏性：同树双池逐键结果与得分曲线一致；合并口径 **τ = 1.0 ≥ 0.95（pass）**，
   注入偏差（逆序）**τ = −1.0（reject）**；
6. 做梦开关：默认档 `merged=False` + 零池化产物；开启档 `merged=True` +
   快照 `enabled_for_dreaming=True / conditions_met=True`；前置不足档回落单项目池并注明
   "未启用：前置条件不足"。

**一致性口径注**：SC-002 的一致率覆盖"池内同结构键**同分**"的键；**冲突键**在合并池中
按 C3 判 UNKNOWN（澄清 Q1 的刻意保守口径），故不计入一致率——契约测试中作为
"声明的差异"单独断言（`tests/contract/test_pooling_contracts.py::TestC7一致性验收`）。
