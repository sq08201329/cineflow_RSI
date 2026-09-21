# 数据模型：策略部署评估自动化（014-auto-deploy-eval）

> 无新 DB 表——部署机制产物全部文件化（`deployment/`，git 版本化，只增不改惯例）；
> 唯一"写"是部署指针的定点改写（`configs/movie.yaml`，复用 `core/yaml_edit`）。
> frozen dataclass 为第一道工序。

## 领域模型（`core/deployment/`）

- **EvidenceBundle**：candidate_version / agent_id / deployed_version /
  unbiasedness（前置：结论与来源）/ reward_compare（候选 vs 现部署的 reward 与来源）/
  validation_rank（排名与 top_ratio 口径）/ drift_verdict（per judge 版本的 allow/拒绝 + 理由）/
  collected_at
- **GateVerdict**：判定（`eligible` / `blocked` / `insufficient_evidence` / `forbidden_agent`）/
  逐要件结果（satisfied / unsatisfied / not_applicable / missing）/ 理由 / 时间
- **EvidenceSnapshot**：一次判定的不可改写记录——EvidenceBundle 引用 + GateVerdict + 时间
  （`deployment/evidence/{agent}/{candidate_version}.{ts}.json`）
- **DeployModeState**：current（`manual`/`shadow`/`auto`）/ history: [{mode, since, by, reason}] /
  shadow_days_accumulated / shadow_candidate_count / recalibration_required（bool + 标记来源）
- **ShadowEvent**：candidate_version / verdict / 若 auto 会放行与否 / 同期人工决策
  （adopt | reject | none）/ 差异分类（`sys_pass_human_reject` / `human_pass_sys_block` /
  `agree` / `no_human_decision`）
- **ShadowReport**：period / shadow_days / candidate_count / passes / blocks /
  理由分布 / 差异分类计数 / 误入率（分子分母 + 口径说明）/ 达标标记
- **AutoDeployEvent**：candidate_version / from_version / evidence_snapshot 引用 /
  deployed_at / pointer_before / pointer_after / source=`auto`
- **SpotCheckRecord**：deploy_event 引用 / 序号 / 触发方式（`first_n` | `ratio`）/
  conclusion（pass | veto）/ by / at / reason
- **RollbackEvent**：from_version / to_version / trigger（`spot_check_veto` | `drift_assessment` | `manual`）/
  at / mode_after（`manual`）/ recalibration_required / 备注

## 文件 schema（`deployment/`）

```text
deployment/
├── mode.json                      # DeployModeState（当前态 + 变更历史，只增不改）
├── evidence/{agent}/{candidate}.{ts}.json
├── shadow/{period}.json           # ShadowReport
├── deploys/{ts}-{agent}.json      # AutoDeployEvent
├── spot_checks/{ts}-{agent}.json  # SpotCheckRecord
└── rollbacks/{ts}-{agent}.json    # RollbackEvent + 漂移回滚评估记录
```

## 状态机

- **部署模式**：`manual ⇄ shadow ⇄ auto`；`manual → auto` **禁止直连**（必须经 shadow
  且满足影子期下限）；`auto → manual` 由抽检否决/漂移评估/人工切换触发；
  `recalibration_required = true` 时 `→ auto` 一律拒绝（须重标定 + 重跑影子）
- **判定**：`forbidden_agent` > `insufficient_evidence` > `blocked`（逐要件）> `eligible`
  （优先级即拦截理由的确定性）

## 配置（configs `deployment` 段扩展）

```yaml
deployment:
  mode_default: manual
  gate: {validation_top_ratio: 0.2, require_unbiasedness: true, allow_without_judge: false}
  shadow: {min_days: 14, min_candidates: 20}
  spot_check: {first_n: 5, ratio: 0.2}
  # 既有：deployment.{agent}.current_policy_version（005 部署指针，定点改写）
```
