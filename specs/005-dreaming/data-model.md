# 数据模型：做梦层与谱系报表

**日期**: 2026-09-19 | **关联**: [spec.md](spec.md) / [plan.md](plan.md)

本特性**无新 DB 表**——谱系元数据文件化（research 决策 3）。全部记录型实体 frozen。

## 1. 领域实体

### Candidate（候选策略）

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| version | str | 源码 BLAKE3 前 12 位；同版本自动去重（FR-001 边界） |
| source_code | str | 策略源码 |
| static_check | "passed" \| "rejected" | rejected 附违规清单；不得进入回放（FR-003） |
| trajectory | ReplayTrajectory \| None | 002 轨迹；回放失败为 None |
| reward | RewardBreakdown \| None | 见下；回放失败/违规为 None（排名按 0 分） |

### RewardBreakdown（奖励分解）

`pareto_auc: float`（梯形归一化，research 决策 1）、`parallel_penalty: float`、
`lambda_: float`、`reward: float = pareto_auc − λ·parallel_penalty`。
校验：pareto_auc ∈ [0,1]；parallel_penalty ≥ 0。

### DreamRound（做梦轮次）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| round_id | str | `dream-{agent_id}-{序号}` |
| agent_id | str | promo / visual |
| champion_version | str | 本轮的当期最优（父版本） |
| digest | dict | 输入摘要（最近 K 轮报告 + 评估器诊断摘要的哈希与引用） |
| candidates | list[Candidate] | M 套（默认 128） |
| winner_version | str \| None | 筛选后胜出者；全灭为 None |
| status | 枚举 | `completed / failed_all_rejected / failed_all_unknown / aborted` |

### ApprovalRecord（审批记录，落盘 `{version}.meta.json`）

```json
{
  "version": "a1b2c3d4e5f6",
  "parent_version": "f6e5d4c3b2a1",
  "created_round": "dream-promo-3",
  "reward": {"pareto_auc": 0.62, "parallel_penalty": 0.18, "lambda": 0.5, "reward": 0.53},
  "source": "dreaming",
  "approval": {"approver": "张三", "at": "2026-09-19T10:00:00+08:00",
               "decision": "approved", "reason": "validation 泛化良好"}
}
```

`source` 枚举：`dreaming | epsilon_random | manual`；`approval` 为 null 表示未审批
（不得成为当期部署指针，FR-007/SC-005）。

### LineageReport（谱系报表，JSON 产出）

```json
{
  "agent_id": "promo",
  "versions": [{
    "version": "...", "parent_version": "...",
    "tree_ids": ["...（001 树库按 policy_version 汇聚）"],
    "child_versions": ["..."], "reward": 0.53,
    "approval": {"decision": "approved", ...}
  }]
}
```

### EvolutionCurve（进化曲线）

```json
{
  "agent_id": "promo",
  "rounds": [{"round": 1, "winner_version": "...", "reward": 0.41}, ...],
  "baseline_reward": 0.41,
  "collapse": {"collapsed": false, "start_round": null, "threshold": 0.7, "window": 3},
  "plateau_note": "连续两轮胜出同一版本时的观察备注（无平台期则为 null）"
}
```

## 1.5 做梦轮次落盘（digest 的数据源）

每轮做梦结束后，DreamRound（含候选明细与轨迹摘要）落盘为
`dreaming/history/{agent_id}/{round_id}.json`——只增不改（git 历史承担审计）。
`digest.py` 组装输入摘要时从此目录读取最近 K 轮（K 来自 configs `dreaming.recent_k`）；
首轮做梦目录为空 → digest 注明"无历史"。

## 2. 谱系数据的两个来源（汇聚规则）

| 来源 | 提供 | 约束 |
| --- | --- | --- |
| `policies/history/{agent}/{version}.meta.json` | parent_version、created_round、reward、approval、source | 文件只增不改（git 历史承担审计） |
| 树库（001） | policy_version → tree_ids | 只读查询（trees_by(policy_version=...)） |

汇聚冲突（文件与树库版本不一致）→ 报表报错而非静默（谱系完整性优先）。

## 3. 做梦流程状态机

```text
轮次发起（champion = 当期部署版本）
  → digest 组装（最近 K 轮回放报告 + 评估器诊断摘要）
  → 候选生成（M 套，LLM/变异器；哈希去重）
  → 静态检查（rejected 不回放；全灭 → failed_all_rejected 告警）
  → 沙箱全池串行回放（超时/崩溃记 0 分注明）
  → reward 计算与排名
  → 防过拟合筛选（train/validation；首轮无 validation 跳过并注明）
  → 胜出者 → 审批单 → 人工 approve/reject
  → approve：meta.json 落盘 + 部署指针更新；reject：轮次结束指针不变
  → 谱系报表与进化曲线更新（塌缩检测）
```
