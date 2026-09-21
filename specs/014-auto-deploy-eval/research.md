# 阶段 0 调研：策略部署评估自动化（014-auto-deploy-eval）

> 每个决策 = 分歧点 → 备选 → 结论 → 依据。规格：[spec.md](spec.md)（含 2026-09-21 澄清三条）。

## 决策 1：存放位置——core/deployment（业务无关部署机制）

- **结论**：落 `core/deployment/`：`evidence.py`（证据包）、`gate.py`（门槛判定）、
  `mode.py`（模式状态机 + 影子计时 + 重标定标记）、`shadow.py`（影子事件与对照报告）、
  `auto_deploy.py`（自动部署执行 + 指针一致性检测）、`spot_check.py`（渐进抽检 + 否决回滚）
- **依据**：部署机制是全线基建（业务无关，原则五）；与 005 的 approve/谱系、011 的池、
  012 的证据接口衔接。

## 决策 2：证据包前置与三要件（澄清 Q1 落地）

- **结论**：`evidence.py` 产 `EvidenceBundle`——**前置** = 无偏性验收结论（011/007 口径，
  未通过即整体"证据不足"）；三要件：①回放 reward 对比（011 池化口径 vs 现部署版本）
  ②validation 排名（005 口径）③漂移 verdict（012 `deploy_evidence_verdict` 对全部相关
  judge 版本）
- **依据**：澄清决议；口径全部复用既有实现，本特性不新造对比/排名/漂移逻辑。

## 决策 3：门槛判定的"缺证据即拦截"实现

- **结论**：`gate.py` 的判定把"证据缺失"表示为 `None` + 判定 `insufficient_evidence`
  （**不是异常**）——与"不满足"区分开（理由是"证据不足"而非"要件不满足"），
  但**行为一致**：拦截；禁止名单 Agent（009 `no_auto_evolve_agents`）判
  `forbidden_agent`（优先级最高）
- **依据**：规格 FR-002（宁可拦截）；区分理由便于影子对照与误入率归因。

## 决策 4：模式状态机与影子计时

- **结论**：`mode.py` 维护 `deployment/mode.json`（当前模式 + 变更历史）；
  影子期计时 = **从变更历史推导的模式区间累计**（切换即暂停/恢复，切换留痕）；
  `shadow_days` 与 `candidate_count` 双下限（配置，默认 14 天）；**重标定标记**
  （`recalibration_required`）存在时 auto 一律拒绝
- **依据**：规格 FR-004~006/FR-009；边界情况（影子期跨越模式变更按区间累计）。

## 决策 5：触发与三种行为（澄清 Q3 落地）

- **结论**：`evaluate_candidate(...)` 是**唯一入口**，由做梦轮次收口后调用（一处接线）；
  `manual` → 只产证据快照（不部署，现状不变）；`shadow` → 产影子事件 + 对照数据
  （不部署）；`auto` → 满足门槛则部署
- **依据**：澄清决议（一处入口三种行为）；与技术方案 §4.1"次日部署"内环语义一致。

## 决策 6：自动部署的执行与留痕

- **结论**：`auto_deploy.py`——满足门槛 → ①证据快照落盘 ②**指针一致性检测**
  （当前指针与 `deployment/deploys/` 留痕一致，不一致即拒绝 + 告警——防外部绕过）
  ③更新 `configs/movie.yaml` 的 `deployment.{agent}.current_policy_version`（**复用
  `core/yaml_edit.upsert_section_entries` 定点改写**，005 approve 同款）④部署事件留痕
  （谱系 `source=auto`）；同周期多候选按 reward 择一
- **依据**：规格 FR-007/FR-011；原则一（历史不回改）+ 原则二（留痕）。

## 决策 7：渐进抽检与否决回滚

- **结论**：`spot_check.py`——部署序号 ≤ `first_n`（默认 5）**全量**产复核任务，
  之后按比例（默认 1/5）产任务；**否决回滚 = 一个逻辑事务三件事**：①指针切回前一版本
  ②模式回 `manual` ③写 `recalibration_required` 标记；任一失败**显式报错并保持人工
  审批**（绝不停留在不确定状态）；回滚事件留痕
- **依据**：澄清 Q2 + 规格 FR-008 + 宪章原则六（抽检否决立即回滚并恢复全人工）。

## 决策 8：误入率的可重算口径

- **结论**：`shadow.py` 与 `spot_check.py` 共同定义——**生产期**：分子 = 抽检否决数 +
  部署后经回滚评估判为误放行的数；分母 = 自动部署总数；**影子期**：分子 = "会放行但
  人工拒绝"数 + "放行样本中被判定不可接受"数（影子期无真实部署，分母 = 影子放行数）；
  提供 `recompute_misadmission_rate(...)` 从留痕重算（机检 SC-007）
- **依据**：规格 FR-012 + 宪章"指标口径必须可被证伪"。

## 决策 9：无 judge 层的 Agent 默认保守

- **结论**：漂移要件对无 judge 的 Agent（promo/sound）标记 `not_applicable`，但**整体
  门槛判不满足**（默认保守：无 judge 的 Agent 仍要求人工 approve）；配置
  `deployment.gate.allow_without_judge: false` 可显式放开（默认 false）
- **依据**：规格假设 + 边界情况（不得因不适用而放宽其他要件）。
