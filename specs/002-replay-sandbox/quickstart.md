# 快速验证指南：回放模拟器与沙箱化策略执行

**目的**: 按本指南可端到端验证本特性。验收场景编号对应 [spec.md](spec.md)。

## 前置条件

- Python 3.11+、uv；`uv sync`
- Docker（沙箱/对抗测试需要；本地 WSL 已启用 Docker Desktop 集成）
- PostgreSQL 开发库（构建历史树数据用）：`docker compose -f ops/dev.compose.yml up -d postgres`

## 验证 1：回放语义单元测试（对应 US1 验收场景）

```bash
uv run pytest tests/unit -k replay --cov=core --cov-report=term-missing
```

**预期**: 全过；覆盖精确匹配/UNKNOWN/预算拒绝/虚拟时钟 ⌈k/W⌉/零生成断言/轨迹产出。

## 验证 2：对抗测试套件（对应 US2 验收场景，CI 合并阻塞）

```bash
uv run pytest tests/adversarial -m adversarial
```

**预期**: 三类作弊策略（peek_latent / timing_side_channel / hash_oracle）全部被拦截；
任一加新的作弊变体失败即视为门禁失守。无 Docker 时本套件**报错而非跳过**（门禁不允许
静默豁免）；CI 上以 gVisor 后端运行。

## 验证 3：无偏性验收（对应 US3 验收场景）

```bash
uv run pytest tests/unbiasedness -m unbiasedness
```

**预期**: 一致轨迹对 τ ≥ 0.95 放行；注入偏差轨迹对 100% 拒绝；报告 JSON 字段符合
[data-model.md](data-model.md) §4。

## 验证 4：端到端演示（对应 SC-003/SC-006）

```bash
uv run python ops/demo_replay.py
```

演示：用 001 的 TreeStore 造一棵小型冻结历史树 → 构建模拟器 → 沙箱中回放手工策略 →
打印轨迹 JSON（得分曲线、probe 数、有效串行轮、虚拟成本、生成调用计数恒 0 的审计断言）。

**预期**: 退出码 0；报告中 `generation_calls == 0`。

## 说明

- 接口语义见 [contracts/](contracts/)；实体与校验规则见 [data-model.md](data-model.md)；
  运行时选型理由见 [research.md](research.md)
- 完整实现任务分解见 `tasks.md`（由 `/skill:speckit-tasks` 生成）
