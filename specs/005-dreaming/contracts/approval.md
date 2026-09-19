# 契约：人工审批闸门

**模块**: `dreaming/approve.py` | **消费方**: 做梦流水线尾部、运营 CLI

## 1. 接口

```python
def create_approval_ticket(dream_round: DreamRound, champion_version: str,
                           pool: SimulatorPool) -> ApprovalTicket: ...
def decide(ticket_path: Path, *, approver: str, decision: str,
           reason: str) -> ApprovalRecord: ...
def current_policy_version(agent_id: str, config_path: Path) -> str: ...   # 部署指针读取
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 审批单 | 含胜出版本、与当期最优的 diff 摘要、train/validation 双池 reward 对比、候选诊断；JSON 落盘 |
| 双池筛选 | 池按时间分 train/validation（**最近一棵树永远只做 validation**）；train 第一但 validation 跌出前 20% → 过拟合丢弃（不进审批单）；首轮无 validation → 跳过判定并注明 |
| 审批 | `decide` 只接受 `approved/rejected`；记录 approver/at/decision/reason 写入 `{version}.meta.json` |
| 部署指针 | 仅当 `approval.decision == "approved"` 才更新 configs 的 `current_policy_version`；SC-005 机检：指针版本必须有 approved 记录，否则报错 |
| 拒绝 | rejected 记录照常落盘；当期指针不变；轮次正常结束 |

## 3. 谱系落盘

胜出且 approved 的版本：`policies/history/{agent_id}/{version}.py` 幂等落盘
（复用 002 versioning）+ `{version}.meta.json`（parent_version=champion、
created_round、reward 分解、approval、source="dreaming"）。

ε 随机预算产出的策略版本照常入谱系，`source="epsilon_random"`（FR-011）。
