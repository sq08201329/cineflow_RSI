# 阶段 0 调研：做梦层与谱系报表

**日期**: 2026-09-19 | **关联计划**: [plan.md](plan.md)

## 决策 1：pareto_auc 的精确口径

**决策**: `pareto_auc(best_score_curve, probe_count)` = 逐轮最优得分曲线下的梯形面积
（Σ (s[i]+s[i+1])/2）除以满分红线面积（1.0 × (n−1)，n=曲线长度），归一化到 [0,1]；
n < 2 时 auc 取曲线首值（单点）或 0（空曲线）。曲线首点（初始观测）计入。
并行惩罚 = `effective_sequential_rounds / max(probe_count, 1)`（dev doc §5.2 原文）。

**理由**: dev doc 给了公式但没给 auc 精确定义；梯形归一化口径让"更快达到高分的策略"
得分更高（早收敛奖励），与探索效率的目标一致；归一化使不同预算的轮次可比较。

**已评估的替代方案**: 末值得分（不奖励收敛速度）；简单均值（对轮次长度敏感）。

## 决策 2：候选生成双实现

**决策**: `CandidateGenerator` 协议：`generate(champion_source, digest, m) -> list[str]`。

- `MutatorGenerator`（开发/CI 默认）：对当前最优策略代码做确定性模板变异
  （参数档扰动、探测顺序变换、预算分配比例扰动），种子 = blake3(champion + round_id)；
  输出是合法且各有差异的策略源码——明确标注为 LLM 占位；
- `LLMGenerator`：提示词 = 当前策略代码 + 最近 K 轮回放报告摘要 + 评估器诊断摘要
  （digest.py 组装），经 003 网关计费调用；响应按代码块切分为 M 套候选。

**理由**: 一期无真实 LLM 凭证（规格假设，原则六诚实边界）；变异器让做梦循环在 CI
完整可跑（机制验证与 LLM 能力解耦——这正是 dev doc §5 的分层思想）。

## 决策 3：谱系元数据文件化

**决策**: 谱系元数据存 `policies/history/{agent_id}/{version}.meta.json`：
`{parent_version, created_round, reward, approval: {approver, at, decision, reason} | null,
source: "dreaming|epsilon_random|manual"}`。**不建 DB 表**；谱系报表 = 文件体系
（父子/审批/reward）+ 树库（policy_version → tree_ids，001 已落库）两源汇聚。

**理由**: 策略即代码、代码即文件——元数据与代码同 lifecycle、随 git 版本化、审查可读；
DB 表需要新迁移且与文件体系双写漂移。immutable 纪律由 git 历史承担（文件只增不改）。

**已评估的替代方案**: 新 DB 表 approvals/lineage（双写漂移，被否）；纯树库派生
（审批记录无处安放，被否）。

## 决策 4：审批闸门与部署指针

**决策**: `approve.py` 生成审批单 JSON（胜出版本、与当期最优的 diff 摘要、双池 reward
对比、候选诊断），CLI 交互确认（`--approve-by <姓名> --reason <理由>` / `--reject`）；
审批记录写入 `{version}.meta.json`；**部署 = 更新 `configs/movie.yaml`（或轮次配置）
中的 `current_policy_version`**——未 approve 的版本不得出现在该指针（SC-005 机检：
断言指针版本必有 approval.decision=approved）。

**理由**: 宪章原则六要求一期保留人工 approve；文件化审批记录可审计、可进 git 评审。

## 决策 5：塌缩检测口径

**决策**: 进化曲线 = 逐轮胜出 reward 序列；塌缩判定：连续 N=3 轮 reward <
首轮基线 × 0.7（N 与阈值进 configs `dreaming.collapse_*`）。判定函数纯函数化，
合成序列双向测试（正常序列不误报、注入塌缩 100% 告警并指明起始轮次）。

**理由**: "无塌缩"需要可判定定义；N=3 与 0.7 是一期经验初值（配置可调，口径进 configs
即形态化，原则五）。连续判定的抗噪性好于单轮阈值。

## 决策 6：候选失败语义与轮次鲁棒性

**决策**: 候选回放超时/崩溃 → reward=0、diagnostics 注明原因、轮次继续；静态检查全灭
→ 轮次失败告警（不部署）；回放全 UNKNOWN → reward=0 且报告提示扩大线上探索
（宪章原则三：覆盖不足靠线上探索解决）。

**理由**: 做梦是天级例行任务，单候选失败不能阻断管线；全灭/全 UNKNOWN 是系统性信号
（生成器坏了 / 经验覆盖不足），必须显式告警而非静默产出垃圾胜者。

## 决策 7：候选回放的执行方式

**决策**: 一期候选回放**串行**执行（沙箱逐个跑）；M=128 × 单回放秒级 ≈ 分钟级，
远在 SC-001 的 30 分钟预算内。

**理由**: 沙箱并行需要容器编排与端口/资源管理，复杂度不值（YAGNI）；性能预算充足。
若日后 M 增大或池膨胀，再引入并行（配置项预留 `dreaming.replay_parallelism=1`）。
