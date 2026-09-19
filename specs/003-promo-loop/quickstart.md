# 快速验证指南：宣发 Agent 全闭环

**目的**: 端到端验证本特性。验收场景编号对应 [spec.md](spec.md)。

## 前置条件

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres   # 集成测试与演示需要
```

## 验证 1：单元测试 + 覆盖率门禁（SC-006）

```bash
uv run pytest tests/unit --cov=core --cov=agents --cov-report=term-missing
```

**预期**: 全过；覆盖率 ≥ 85%（core + agents 合计口径）。

## 验证 2：适配器契约套件（SC-007）

```bash
uv run pytest tests/contract
```

**预期**: 模拟平台全过（花费上限、幂等键、状态机、指标 schema、错误映射）；真实实现
无凭证时按用例跳过。

## 验证 3：闭环端到端演示（US1/US2/US3 验收场景）

```bash
uv run python ops/demo_promo_loop.py
```

演示（模拟平台）：触发一轮探索（总预算 $500 → 上限 $10）→ 物料生成与合规门禁（含
敏感词拦截样例）→ 预算门禁（含超限拒投样例）→ 投放与回流 → 树落盘冻结 → 成本对账 →
同 round_id 二次触发验证幂等 → 树入池回放（零生成断言）→ 基线 vs 变体进化报告 JSON。

**预期**: 退出码 0；报告中 `cost_reconciliation.consistent == true`、二次触发
`duplicate == true` 且无新扣费、进化报告含双版本曲线与奖励分量。

## 验证 4：回流幂等与指标校验（边界情况）

```bash
uv run pytest tests/integration -m integration -k promo
```

**预期**: 重复回流 0 变更、越界指标（CTR>1）拒绝、FAILED 节点成本完整等集成用例全过。

## 说明

- 接口语义见 [contracts/](contracts/)；状态机与表结构见 [data-model.md](data-model.md)；
  设计决策理由见 [research.md](research.md)
- 任务分解见 `tasks.md`（由 `/skill:speckit-tasks` 生成）
