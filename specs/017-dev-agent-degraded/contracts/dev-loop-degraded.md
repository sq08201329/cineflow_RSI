# 契约：开发 Agent 降级轮次循环（单产出记录 + 成本对账 + 人工策略闭环 + 三重机检）

> 对应规格 US1/US3 / FR-001~002、007~008、014。实现：`agents/dev/{loop,db,config}.py`，治理件
> `core/degraded/{policy,compare,adoption}.py`（通用件抽取见 core-degraded.md）。

## C11 单产出轮次与幂等

`run_dev_round(round_id, policy, store, artifacts, engine, gateway, config, inputs, evaluators=None) -> DevRoundResult`

- 输入 = 立项约束（题材边界/受众/形态参数）+ 人工策略版本；输出 = 单一**立项组合工件**（澄清第 8 条：不设多阶段、不拆组合与单选两套 schema），节点含 `policy_version`
- 确定性 id：`round_tree_id(round_id) = f"dev-round-{round_id}"`、`_round_root_id`、`_job_id`（沿用 009 口径 `agents/screenplay/loop.py:114/119/123`）
- `dev_jobs`（迁移 0010）唯一键 **`(round_id, params_hash)`**；009 为 `(round_id, stage, params_hash)`（`specs/009-script-agent-degraded/data-model.md:25`、`agents/screenplay/db.py:60`）——本环节单一产出、**无 stage 列**，故删去该列而非置空
- 重复触发：撞树锚点 → 幂等重建（`DuplicateError` → `_reconstruct`，`loop.py:917`），0 重复节点、0 重复扣费；节点一次性 INSERT（含 FAILED）

## C12 成本与对账

- **全部** LLM 经网关（缓存/重试/计费）；FAILED 节点成本照常入账——每节点 `CostRecord` 必填（`core/tree/models.py:39`：每个节点必须入账，含 FAILED）
- 每轮对账 `tree_total == ledger_total + evaluator_cost`（镜像 `agents/screenplay/loop.py:478` `_reconcile`）：本环节无 judge，`evaluator_cost` 为评估器侧计费增量，代理确定性无 LLM 调用故恒 0——**字段仍落盘，不得省略**；重建路径对账字段**留空**而非伪造"一致"（`:917` 口径）

## C13 回放纪律

- 选题树接入模拟器池（`core/replay/pool.py:26`/`:36`）；信息入口仍只有 observed/probe + 规范化精确匹配（`core/replay/matching.py:18`/`:28`），未命中即 UNKNOWN（记 0 分 + 提示扩大记录，不得据其得出"谁更好"——不编造、不放宽）
- 匹配键 `slate_match_key`（`agents/dev/loop.py`）**只含策略可复现的结构键**（立项约束摘要 + 组合区间 + 策略版本 + 模型 + 温度 + 输出预算），**不含**生成产物摘要（沿用 `agents/screenplay/loop.py:139` 键口径）
- 回放路径零 LLM 与 `max_generation_calls == 0`：装配强制归零（`policies/base.py:32`）+ 第二道断言（`:37`）；审计断言测试 `tests/unit/test_simulator.py:40`（原则三）

## C14 人工策略通道与最小池门槛

`submit_policy(...) -> HumanPolicyVersion`（版本 = 源码 BLAKE3 前 12 位）；`compare_versions(...) -> ReplayComparison`（`agents/dev/sandbox_compare.py` 薄适配：绑定 `slate_match_key` 与最小树数门槛；通用件签名含 `match_key`/`min_comparable_trees` 注入，见 core-degraded.md C1）；`adopt(comparison_id, decision, by, reason) -> AdoptionRecord`

- 策略 = **人编写的代码**（版本 = `policies/versioning.py:17` + `parent_version`）：静态检查（`policies/static_check.py`）+ 接口签名 AST（`agents/screenplay/policy_versions.py:71`）；未过检查即拒绝、不入历史、不进回放；参数调整走 configs，不产生策略版本
- 回放对比报告（`core/degraded/compare.py`；009 同位 `agents/screenplay/sandbox_compare.py:206`）：逐树得分 / 分项评估器差异 / pareto_auc 曲线 / UNKNOWN 说明
- **最小池门槛（前置）**：可比对树数 < 形态配置下限 ⇒ **拒绝产出报告并报错**（错误含实测树数与门槛值，SC-011）；与"未过无偏性不得产出报告"（`sandbox_compare.py:227`）并列（计划研究决策 6）
- 报告与采纳记录 append-only；**采纳是唯一能移动部署指针的动作**：定点改写 `deployment.dev.current_policy_version`（`core/yaml_edit.py:155`，注释与其他段逐字节保留；009 同位 `agents/screenplay/adoption.py:67`）；拒绝同样留痕（理由非空），未采纳 ⇒ 指针逐字节不变（SC-002）

## C15 禁止自动进化三重机检

- ① 配置名单 `dreaming.no_auto_evolve_agents: [screenplay, dev]` **已含 `dev`**（`configs/movie.yaml:386`、`configs/shortdrama.yaml:393`）
- ② 显式拒绝：dreaming 守卫在所有副作用前抛 `AutoEvolutionForbiddenError`（`dreaming/pipeline.py:147`，异常类 `:29`）；部署门禁对 `dev` 返回 `FORBIDDEN_AGENT` 且**优先级最高**（`core/deployment/gate.py:82`，先于"证据不足"分支 `:88`）
- ③ 审计留痕：人工策略 meta 字段 `no_auto_evolve`（`agents/screenplay/policy_versions.py:175`）
- 既有契约测试 `tests/contract/test_screenplay_no_auto_evolve.py:30` 的 `FORBIDDEN_NAMES = ("screenplay", "dev")` 已按 `:109` 参数化覆盖 dev——**本特性只补 dev 侧断言**（`tests/contract/test_dev_no_auto_evolve.py`），不新增 plumbing、不另立第二份名单。

### 场景

1. 同 round_id 二次触发 → 唯一键幂等重建，0 重复节点 / 0 重复扣费
2. 网关失败 → FAILED 节点 + 成本照计；节点可回溯 `policy_version`
3. 一轮对账 `consistent=True`；重建路径对账字段留空；回放对比全程网关调用 0 且 `max_generation_calls == 0`
4. 可比对树数低于门槛 → 拒绝产出报告（错误含实测树数与门槛值）
5. 采纳 → 指针更新 + 记录落盘；拒绝或未决策 → 指针逐字节不变
6. `run_dream_round(agent_id="dev")` → 拒绝且 0 候选 0 计费 0 落盘；门禁返回禁止名单
