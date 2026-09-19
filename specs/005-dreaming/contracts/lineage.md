# 契约：谱系报表与进化曲线

**模块**: `dreaming/lineage.py` | **消费方**: 负责人报表、里程碑验收、运营审计

## 1. 接口

```python
def build_lineage(agent_id: str, store: TreeStore,
                  history_dir: Path) -> LineageReport: ...
def build_curve(agent_id: str, history_dir: Path,
                collapse_window: int, collapse_threshold: float) -> EvolutionCurve: ...
def detect_collapse(rewards: list[float], *, window: int,
                    threshold: float) -> CollapseResult: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 谱系汇聚 | meta.json（父子/审批/reward）+ 树库（policy_version → tree_ids）两源汇聚；冲突报错不静默 |
| 全链路 | 任一版本给出 parent_version、tree_ids、child_versions、reward、approval（FR-009/SC-006：字段缺失为 0） |
| 曲线 | rounds 按 created_round 升序；baseline_reward = 首轮胜出 reward |
| 塌缩 | 连续 window 轮（默认 3）reward < baseline × threshold（默认 0.7）→ collapsed=true 并指明 start_round；参数来自 configs |
| 边界 | 轮次数 < window 不可能塌缩；空历史 → 空曲线不报错 |

## 3. 验收语义（里程碑验收线）

连续 5 轮进化演示（ops/demo_dreaming.py）：EvolutionCurve 产出且
`collapse.collapsed == false`（SC-004）；注入塌缩序列的回归用例 100% 告警（CI 常驻）。
