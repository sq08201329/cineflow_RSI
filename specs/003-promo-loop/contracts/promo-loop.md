# 契约：宣发线上探索执行器

**模块**: `agents/promo/loop.py` | **消费方**: 运营触发（CLI/未来的调度器）、演示脚本

## 1. 执行入口

```python
def run_round(round_id: str, policy: ExplorationPolicy,
              store: TreeStore, artifacts: ArtifactStore,
              adapter: PlatformAdapter, gateway: LLMGateway,
              config: PromoConfig) -> RoundResult: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 幂等 | 同 `round_id` 二次触发：命中唯一约束，直接返回首轮 `RoundResult`，0 次重复投放、0 元重复扣费（FR-001/SC-004） |
| 预算门禁 | 投放申请前校验 `spent + 申请额 ≤ total_budget × pilot_ratio`；事务内校验+扣减；超限拒投并把拒投原因写入轮次结果（FR-002/SC-001） |
| 合规门禁 | 物料不过 `rule.material_compliance` 不得投放；拦截成本照常入账（FR-003） |
| 成本入账 | 生成/投放/LLM/耗时全部进节点 CostRecord；FAILED 节点不例外（FR-007） |
| 落树 | 指标回流后一次性构造完整节点 INSERT（research 决策 1）；节点状态 evaluated/failed |
| 对账 | 轮次结束时输出成本对账：树内节点成本合计 == 运营表扣减合计 == 网关/适配器账目（SC-003） |
| 失败 | 适配器调用失败按重试策略（上限 3 次退避）；**网关失败不再重试**（网关内部已退避 3 次，双层重试禁止叠加放大）；最终失败节点 FAILED，轮次继续其余物料 |

## 3. RoundResult（返回与 JSON 报告）

```json
{
  "round_id": "...", "tree_id": "...", "policy_version": "...",
  "materials": [{"material_id": "...", "status": "delivered|rejected|failed", "reason": "..."}],
  "spent_usd": 9.98, "budget_cap_usd": 10.0,
  "cost_reconciliation": {"tree_total": {...}, "ledger_total": {...}, "consistent": true}
}
```

## 4. 边界语义

- 零物料过门禁：轮次正常完成，`tree_id` 指向只含拦截记录的树；
- 预算恰好等于上限：允许（≤ 语义）；
- 缺物料规格/敏感词配置：合规评估器抛配置错误，**拒投**（不放行）；
- 树冻结（入池）由调用方在轮次完成后显式触发（`agents/promo` 不自行冻结）。
