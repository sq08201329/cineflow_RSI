# 实现计划：做梦层与谱系报表（策略自动进化）

**分支**: `005-dreaming` | **日期**: 2026-09-19 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/005-dreaming/spec.md` 的功能规格说明

## 概要

交付 `dreaming/` 包：做梦执行器（当前最优 + 池报告 + 诊断摘要 → M=128 候选 → 静态检查 →
沙箱全池回放 → reward 排名）、奖励函数（pareto_auc − λ·并行惩罚）、防过拟合筛选
（train/validation 分树，前 20% 判定）、人工审批闸门（审批单 + 落盘记录）、谱系报表
（版本 → 树 → 子版本全链路）与进化曲线（逐轮 reward + 塌缩检测）。候选生成经
CandidateGenerator 接口：LLM 实现走网关（真实凭证属运维配置），确定性变异实现为
开发/CI 默认。全部复用 001-004 基建，**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——复用现有栈（blake3、pyyaml、SQLAlchemy 只读谱系查询）

**存储**: 无新 DB 表——谱系元数据以 JSON 文件存于 `policies/history/{agent_id}/`
（`{version}.py` 旁的 `{version}.meta.json`：parent_version、审批记录、reward 摘要），
做梦轮次记录以 JSON 落盘 `dreaming/history/{agent_id}/{round_id}.json`（digest 数据源）；
文件体系与策略代码同 lifecycle、随 git 版本化；树侧谱系字段（policy_version）已在 001 落库

**测试**: pytest；确定性变异生成器 + promo/visual 池夹具；沙箱回放复用 002 后端
（本地 hardened、CI gVisor）；塌缩检测用合成序列双向断言

**目标平台**: Linux（WSL2 + CI）

**项目类型**: monorepo 第三个顶层包 `dreaming/`（→ core 单向依赖）

**性能目标**: 一轮做梦（M=128，全池回放）< 30 分钟（SC-001）；候选回放沙箱级并行
串行执行（一期串行，性能预算内即可）

**约束**: 静态检查 100% 前置（原则四延伸）；LLM 过网关（原则三）；人工 approve 闸门
（原则六）；谱系全链路（原则二）；dreaming → core 单向（原则五）；覆盖率 ≥85%

**规模/范围**: 1 个新包（dreaming/）+ 谱系元数据文件体系 + 演示脚本；不含真实 LLM
凭证、Web 审批 UI、发布系统对接

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 回放沿用冻结树与冻结评估器；谱系元数据含 reward 时的快照引用 | ✅ 满足 |
| 原则二：节点不可变与谱系 | 树零写入；谱系全链路（版本→树→子版本）报表化；策略版本 BLAKE3 前 12 位 | ✅ 满足 |
| 原则三：昂贵动作仅限线上探索 | 做梦零生成 API 调用（审计断言）；候选 LLM 生成是唯一 LLM 消耗且过网关入账 | ✅ 满足 |
| 原则四：沙箱隔离 | 候选回放 100% 经 002 沙箱；静态检查前置双保险 | ✅ 满足 |
| 原则五：单向依赖与配置化 | dreaming → core/agents 只读消费；M/K/λ/ε/塌缩阈值全走 configs | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 人工 approve 为一期强制闸门；LLMGenerator 占位如实标注 | ✅ 满足 |
| 测试纪律 | TDD；覆盖率 ≥85%；塌缩检测双向断言 | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/005-dreaming/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── dreaming.md      # 做梦执行器契约（候选生成/静态拦截/回放打分/排名）
│   ├── approval.md      # 人工审批闸门契约（审批单/记录/部署指针）
│   └── lineage.md       # 谱系报表与进化曲线契约（含塌缩检测）
└── tasks.md             # 阶段 2 输出
```

### 源代码（仓库根目录，在 001-004 结构上增量）

```text
dreaming/
├── candidates.py        # CandidateGenerator 协议 + MutatorGenerator（确定性变异占位）
│                        # + LLMGenerator（网关计费，真实凭证属配置）
├── reward.py            # pareto_auc + reward 分解（λ 来自 configs）
├── overfit.py           # train/validation 分树 + 前 20% 过拟合判定
├── pipeline.py          # 做梦执行器：生成 → 静态检查 → 沙箱回放 → 筛选 → 排名
├── approve.py           # 审批单生成 + CLI 审批 + ApprovalRecord 落盘 + 部署指针
├── lineage.py           # 谱系报表（版本→树→子版本汇聚）+ 进化曲线 + 塌缩检测
└── digest.py            # 输入摘要组装（最近 K 轮回放报告 + 评估器诊断摘要）
configs/movie.yaml       # 追加 dreaming 段（candidates_per_round/lambda/epsilon_random/
                         # recent_k/validation_top_ratio/collapse 阈值）
policies/history/{promo,visual}/  # {version}.meta.json 谱系元数据（父子/审批/reward）
ops/
└── demo_dreaming.py     # 端到端演示：5 轮做梦 → 审批 → 进化曲线 → 谱系报表
tests/unit/test_dreaming_*.py；tests/integration/test_dreaming_e2e.py
```

**结构决策**: `dreaming/` 按宪章既定顶层包落地（`dreaming → core` 单向）。谱系元数据
选文件体系而非新 DB 表（research 决策 3）：与策略代码同 lifecycle、随 git 版本化、
无需迁移；树侧谱系已在 001 落库，报表时两源汇聚。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. pareto_auc 口径：逐轮最优得分曲线的梯形面积 / 满分红线面积（归一化到 [0,1]）
2. 候选生成双实现：MutatorGenerator（参数扰动模板，确定性）+ LLMGenerator（网关）
3. 谱系元数据文件化（`{version}.meta.json`），不建新表
4. 审批闸门：审批单 JSON + CLI 确认，记录落盘；部署 = 当期指针配置更新
5. 塌缩检测：连续 N=3 轮 reward < 基线 × 阈值（0.7）（配置可调），双向断言
6. 候选回放的失败语义：超时/崩溃记 0 分注明，不阻断轮次

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：DreamRound/Candidate/RewardBreakdown/ApprovalRecord/
  LineageReport/EvolutionCurve 与谱系元数据 schema
- [contracts/dreaming.md](contracts/dreaming.md)、[approval.md](contracts/approval.md)、
  [lineage.md](contracts/lineage.md)
- [quickstart.md](quickstart.md)：端到端验证场景（含 5 轮无塌缩验收）

## 宪章复核（阶段 1 后）

候选回放 100% 经沙箱 + 静态检查前置（原则四）；LLM 生成走网关且回放零生成（原则三）；
审批闸门与版本谱系落盘（原则六/二）；配置全走 configs（原则五）。**无新增违规，
门禁通过。**
