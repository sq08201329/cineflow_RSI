# 契约：视觉线上探索执行器

**模块**: `agents/visual/loop.py` | **消费方**: 运营触发（CLI）、演示脚本

## 1. 执行入口

```python
def run_round(round_id: str, policy: ExplorationPolicy,
              store: TreeStore, artifacts: ArtifactStore,
              adapter: VideoGenAdapter, gateway: LLMGateway,
              engine: Engine, config: VisualConfig) -> RoundResult: ...

def freeze_round_tree(round_id: str, ...) -> DiscoveryTree: ...  # 运营终态校验 + 返回树对象
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 幂等 | 同 `round_id` 二次触发：命中唯一约束返回首轮结果，0 次重复生成调用、0 元重复扣费 |
| 预算门禁 | 生成申请前校验 `spent + 申请 ≤ exploration_per_round_usd`；事务内校验+扣减；超限拒绝并记录原因（FR-003/SC-004） |
| 合规门禁 | `rule.format_compliance` 得 0 → 合成总分 0（不可行解），不进入 judge 评估（省 LLM 成本） |
| 崩溃隔离 | 任一评估器对某片段抛异常 → 该节点 FAILED（score=None，成本入账），轮次继续其余片段（SC-006） |
| 定点归一 | 所有评估器得分经 `quantize_score` 后落盘（FR-012） |
| 对账 | 轮次结束输出三方对账：树内成本合计 == 运营表扣减 == 适配器/网关账目 |
| 冻结 | `freeze_round_tree` 只在全部 GenJob 终态时成功；config_snapshot 在树创建时写全（五评估器版本组合 + 权重 + 观测白名单） |

## 3. RoundResult

```json
{
  "round_id": "...", "tree_id": "...", "policy_version": "...",
  "clips": [{"clip_id": "...", "status": "ingested|failed|rejected", "reason": "..."}],
  "spent_usd": 120.0, "budget_cap_usd": 500.0,
  "cost_reconciliation": {"tree_total": {...}, "ledger_total": {...}, "consistent": true}
}
```

## 4. 边界语义

- 零片段过合规门禁：轮次正常完成，树只含门禁拦截节点；
- 预算恰好等于上限：允许（≤ 语义）；
- ffmpeg 不可用/片段无法解码：评估器报错 → 节点 FAILED（不崩溃闭环）；
- 适配器失败重试上限 3 次（网关失败不再重试，同 003 分工）。
