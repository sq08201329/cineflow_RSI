# 数据模型：外环周校准（010-weekly-calibration）

> 存储分两层：**DB 表**（原始锚点，INSERT-only 触发器强制冻结）+ **文件**（派生产物：
> 轮次/台账/报告/提案，随 git 版本化）。frozen dataclass 为应用层第一道工序。

## DB：`calibration_anchors`（迁移 0004，双方言 INSERT-only 触发器）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| anchor_id | TEXT PK | UUID |
| node_id | TEXT NOT NULL | 关联发现树节点（外键逻辑约束，不跨库强约束） |
| artifact_hash | TEXT NOT NULL | 64 位小写十六进制（BLAKE3） |
| agent_id | TEXT NOT NULL | promo / visual / ... |
| source | TEXT NOT NULL | `human_blind` / `platform_truth` |
| score | REAL NOT NULL | CHECK (score BETWEEN 0 AND 1) |
| reviewer | TEXT NOT NULL | 人评=评审人；平台=来源渠道标识 |
| round_id | TEXT NOT NULL | 所属校准轮次 |
| created_at | TEXT NOT NULL | ISO8601 UTC |

唯一约束：`(node_id, reviewer, round_id)`——同键重复录入在 DB 层拒绝（FR-003 幂等）。
UPDATE/DELETE 触发器拒绝（PostgreSQL + SQLite 双方言，复用 0001 模式）。

## 领域模型（`core/calibration/models.py`，frozen dataclass）

- **AnchorScore**：anchor_id / node_id / artifact_hash / agent_id / source /
  score ∈ [0,1] / reviewer / round_id / created_at —— DB 行的内存形态
- **CalibrationRound**：round_id / agent_id / period（起止日期）/ top_k /
  清单节点列表 / 状态（open → intake → closed）/ 样本量备注
- **PairingRecord**：anchor_id / evaluator_key（`id@version`）/ anchor_score /
  auto_score / 剔除标记（自循环剔除时注明剔除分量）
- **BiasRecord**：evaluator_key / period / 样本量 / mean_shift / pearson_r /
  或 judge 口径的 kendall_tau / 备注（"样本不足"等）
- **WeightProposal**：proposal_id / agent_id / based_version / 偏差证据（BiasRecord 引用）/
  current_weights / candidate_weights / fit_objective（拟合目标：岭回归目标函数与收缩强度说明）/
  ridge_lambda / 状态（pending → confirmed | shelved | failed）/
  confirmed_by / confirmed_at

## 文件 schema

**台账** `calibration/ledger/{agent_id}/{evaluator_id}.jsonl`：每行一条 BiasRecord JSON，
append-only（应用层只追加；冻结语义由 DB 锚点与 git 历史共同保证）。

**信度报告** `calibration/reports/{period}.json`：
```json
{
  "period": "2026-W39",
  "agents": {
    "visual": {
      "judge.cinematic@1.0.0+...": {"pearson_r": 0.62, "samples": 5, "meets_target": true},
      "...": {}
    },
    "promo": {"proxy.ctr_history@...": {"pearson_r": 0.81, "samples": 12, "meets_target": true}}
  },
  "target": 0.6,
  "alerts": [{"evaluator": "...", "reason": "negative_correlation"}]
}
```

**提案** `calibration/proposals/{proposal_id}.json`：WeightProposal 全字段序列化。

## 状态机

- CalibrationRound：`open`（清单已发）→ `intake`（录入中）→ `closed`（偏差计算完成，
  台账/报告落盘）；样本不足照样 closed 并注明
- WeightProposal：`pending` → `confirmed`（生效完成）| `shelved`（人工搁置）|
  `failed`（生效中途失败，已回滚）；终态不可逆
