# 数据模型：视觉 Agent 闭环

**日期**: 2026-09-18 | **关联**: [spec.md](spec.md) / [plan.md](plan.md)

## 1. 领域实体

### VideoClip（视频片段）

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| clip_id | str | uuid7 |
| gen_params | dict | 生成参数（分辨率/时长/帧率/风格提示词/镜头数/种子档）——回放匹配键 |
| artifact_hash | str | 生成 mp4 的 BLAKE3（内容寻址） |
| probe_meta | dict | ffprobe 实测元数据（编码/分辨率/帧率/时长），评估器输入 |

### GenJob（生成任务，可变运营状态）

| 字段 | 类型 | 校验 |
| --- | --- | --- |
| job_id | str | uuid7 |
| round_id | str | 所属轮次（幂等键一部分） |
| params_hash | str | 规范化 gen_params 的 BLAKE3 |
| node_id | str \| None | 落盘后回填（回填即终态） |
| status | 枚举 | `submitted → generating → completed → ingested`；`failed` 失败终态 |
| cost_usd | float | ≥ 0，事务内扣减 |
| external_id | str \| None | 平台任务标识 |

### FrameSamples（帧采样序列，评估器输入）

`frames_gray: np.ndarray (N,64,64)`、`frames_rgb: np.ndarray (N,64,64,3)`、
`frame_indices: list[int]`、`sampling_spec: dict`（N=8、尺寸、灰度规则——进评估器版本元信息）。
**不变量**：同一片段同一规则采样逐字节一致。

### ConsistencyReport（一致性验收报告，JSON 产出）

```json
{
  "tree_id": "...", "checked_nodes": 12, "consistent_rate": 1.0,
  "drift_nodes": [], "tau": 0.98, "threshold": 0.95,
  "verdict": "pass | reject", "notes": ["..."]
}
```

## 2. 存储层增量：迁移 0003 `visual_gen_jobs`（**可变**运营表）

| 列 | 类型 | 约束 |
| --- | --- | --- |
| job_id | text | PRIMARY KEY |
| round_id | text | NOT NULL；与 params_hash 组成唯一键（幂等） |
| params_hash | text | NOT NULL |
| node_id | text | NULL |
| status | text | CHECK ∈ ('submitted','generating','completed','ingested','failed') |
| cost_usd | double precision | NOT NULL DEFAULT 0, CHECK ≥ 0 |
| external_id | text | NULL |
| created_at / updated_at | double precision | NOT NULL |

同 003 约定：运营表**不适用** immutable 触发器；树节点 `ingested` 后一次性 INSERT。

## 3. 视觉树形态

- **根节点 = 探索轮次锚点**：`agent_id="visual"`、`policy_version`=当期策略版本、
  `eval_breakdown={}`、`score=0.0`（锚点，同 003 §1.5 约定）；
- **片段尝试 = 根的子节点**：`observation_context["gen_params"]`= 规范化生成参数
  （002 回放匹配键）；`artifact_hash` = 片段内容哈希；
- `eval_breakdown` 键约定（版本号示例）：

```json
{
  "rule.format_compliance@1.0.0": {"score": 1.0, "diagnostics": {...}},
  "proxy.aesthetic@1.0.0+<实现哈希12位>": {"score": 0.71, "diagnostics": {...}},
  "proxy.identity_consistency@1.0.0+<实现哈希12位>": {"score": 0.83, ...},
  "proxy.flicker@1.0.0+<实现哈希12位>": {"score": 0.9, ...},
  "judge.cinematic@1.0.0+<提示词与锚点哈希12位>": {"score": 0.66, "diagnostics": {"votes": [...]}}
}
```

- 合成权重来自 `configs/movie.yaml` 的 `evaluator_weights.visual`（rule 为 gate，
  冻结进 config_snapshot）；得分入库前经 `quantize_score`（6 位小数，FR-012）。

## 4. 闭环状态机

```text
轮次触发（round_id）
  → 策略产出生成参数组合（observation_context["gen_params"]）
  → 预算门禁（spent + 申请 ≤ exploration_per_round_usd，事务内校验扣减）
  → GenJob submitted → generating（适配器提交）
  → completed（工件取回 → 内容寻址落库 → ffprobe 探测）
  → 五评估器打分（rule 门禁：不合规 score=0；单评估器崩溃 → 节点 FAILED，轮次继续）
  → 合成得分 → 一次性完整节点 INSERT（ingested，冻结）
任一步失败 → failed（成本照常入账）
```

## 5. 一致性验收流程

1. 读取冻结树的全部已评估节点与配置快照（五评估器版本组合）；
2. 用快照指定的冻结版本评估器对同一工件哈希重算得分；
3. 逐节点对照落盘值：全部一致 → consistent_rate=1.0；任一漂移 → 漂移清单；
4. τ 门禁按树数量分档（与 002 unbiasedness 契约"回放池不得含源树"对齐）：
   - **首轮（池内 < 2 棵树）**：只执行重算一致率判定，`tau = null`，报告注明
     "待第二棵树入池后启用 τ 门禁"——首轮验收线由重算一致率 100% 单独承载；
   - **≥ 2 棵树**：真实轨迹与回放轨迹得分序列 → 复用 002 `verify_unbiasedness`
     （threshold 默认 0.95），τ 参与判定；
5. 产出 ConsistencyReport：一致率 100% 且（启用时）τ 合格 → pass，否则 reject。
