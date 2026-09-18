# 实现计划：宣发 Agent 全闭环（真实投放与数据回流）

**分支**: `003-promo-loop` | **日期**: 2026-09-18 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/003-promo-loop/spec.md` 的功能规格说明

## 概要

交付首个业务 Agent 包 `agents/promo`：线上探索执行器（策略生成物料 → 合规门禁 → 预算
门禁（≤2%）→ 平台适配器投放 → 指标回流 → 节点一次性落盘冻结 → 成本对账）+ 三个宣发
评估器（合规/CTR 代理/平台真值）+ 确定性模拟平台 + 首轮进化对比报告。配套新建
`core/llm_gateway` 最小版（计费/缓存/重试，宪章栈表已列、本期首次需要）与一张可变的
运营状态表（`promo_campaigns`——树节点保持只 INSERT，两段式状态存于运营表，见 research
决策 1）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 复用现有栈（SQLAlchemy/psycopg/blake3/uuid-utils/pyyaml/httpx 如需真实
适配器）；**不新增重依赖**（CTR 估计用纯 Python 分桶平滑）

**存储**: 复用 001 的 `tree_nodes`/`discovery_trees`（只 INSERT 不变）；新增**可变**
运营表 `promo_campaigns`（轮次幂等键、投放状态机、已耗金额）——运营状态可变性与树
immutable 互不冲突（research 决策 1）；Alembic 迁移 0002

**测试**: pytest；模拟平台确定性（物料哈希为种子）；适配器契约套件双实现同跑；
真实适配器无凭证时按用例跳过

**目标平台**: Linux（WSL2 + CI）

**项目类型**: monorepo 首个 `agents/` 包 + `core/` 增量（llm_gateway）

**性能目标**: 单轮闭环（模拟平台）端到端 < 5 分钟（SC-002）；回流写入幂等

**约束**: 一切 LLM 调用过网关（原则三）；评估器注册规范（原则一）；节点 immutable +
成本必入账（原则二）；agents → core 单向依赖（原则五）；单元覆盖率 ≥85%

**规模/范围**: 1 个 Agent 包 + 3 个评估器 + 1 张运营表 + 网关最小版 + 进化报告；
不含真实凭证接入、视频物料、ML 训练管线、做梦层

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 三评估器按 `evaluator_id@version` 注册；CTR 估计器以数据快照哈希版本化；平台真值写入即冻结 | ✅ 满足 |
| 原则二：节点不可变与谱系 | 树节点只 INSERT（回流后一次性完整落盘）；每步成本入账含 FAILED；谱系含策略版本 | ✅ 满足（两段式状态见 research 决策 1） |
| 原则三：昂贵动作仅限线上探索 | 投放/生成只发生在线上闭环；回放零生成；LLM 全走网关计费 | ✅ 满足 |
| 原则四：沙箱隔离 | 策略经 002 沙箱回放，本期不新增暴露面 | ✅ 不触及 |
| 原则五：单向依赖与配置化 | `agents/promo → core/`，反向禁止；预算比例/物料规格/敏感词全部走 configs | ✅ 满足 |
| 原则六：诚实边界 | 真实渠道不接凭证：适配器接口 + 确定性模拟实现 + 契约约束，不假装真实投放 | ✅ 满足 |
| 测试纪律 | TDD；覆盖率 ≥85%；契约套件双实现 | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/003-promo-loop/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── promo-loop.md        # 探索执行器契约（预算门禁/幂等/对账）
│   ├── platform-adapter.md  # 平台适配器契约（双实现同跑）
│   └── llm-gateway.md       # 网关契约（计费/缓存/重试）
└── tasks.md                 # 阶段 2 输出
```

### 源代码（仓库根目录，在 001/002 结构上增量）

```text
core/
└── llm_gateway/
    ├── gateway.py           # 统一入口：计费入账 CostRecord、内容哈希缓存、重试退避
    └── backends/
        ├── mock.py          # 确定性 mock（prompt 哈希为种子），开发/CI 用
        └── http.py          # OpenAI 兼容 HTTP 后端（凭证经环境变量注入）
agents/
└── promo/
    ├── material.py          # PromoMaterial 生成编排（网关调用 → 工件落内容寻址存储）
    ├── evaluators/
    │   ├── compliance.py    # rule.material_compliance（规格/敏感词，缺配置拒投）
    │   ├── ctr.py           # proxy.ctr_history（分桶贝塔平滑，快照哈希版本化）
    │   └── platform_metrics.py  # human.platform_metrics（回流真值，写入即冻结）
    ├── platform/
    │   ├── base.py          # PlatformAdapter Protocol + 错误类型
    │   ├── simulated.py     # 确定性模拟平台
    │   └── http_real.py     # 真实平台适配器（契约同构，凭证配置注入）
    ├── loop.py              # 线上探索执行器（预算门禁/幂等/状态机/落树）
    └── report.py            # 进化对比报告（复用 002 回放，两策略版本曲线）
configs/movie.yaml           # 追加 promo 段（试点比例、物料规格、敏感词、总预算）
ops/
├── ingest_metrics.py        # 回流管道：平台指标 → 校验 → 节点一次性落盘冻结
└── demo_promo_loop.py       # 端到端演示（模拟平台一轮闭环 → 回放 → 进化报告）
ops/migrations/versions/0002_promo_campaigns.py   # 可变运营表
tests/
├── unit/                    # 评估器/门禁/幂等/网关/估计器/报告
├── contract/                # 适配器契约套件（双实现同跑）
└── integration/             # 闭环 PG 端到端（Docker PG 可用时）
policies/history/promo/      # 基线与变体策略（手写版本，BLAKE3 版本号）
```

**结构决策**: 沿用 monorepo 既定布局；首个 `agents/` 包落地，目录边界严格遵循宪章
原则五。`policies/history/promo/` 两个手写策略版本随本特性提交（做梦层接入前的
人工策略来源）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. **immutable 与回流写入的张力**：节点延迟一次性落盘——投放/成本先存可变运营表，
   指标回流后构造完整节点单次 INSERT（含冻结真值）
2. **LLM 网关最小版**本期新建（宪章栈表已列，本期首次需要）
3. **模拟平台确定性**：物料哈希为种子的指标分布，可复现
4. **CTR 代理**：分桶贝塔平滑，数据快照哈希进版本元信息
5. **幂等**：`round_id` 唯一约束 + 状态机
6. **进化报告**：复用 002 回放，基线/变体两手写策略版本

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：实体与 `promo_campaigns` 表、评估明细键约定
- [contracts/promo-loop.md](contracts/promo-loop.md)：执行器/预算门禁/幂等/对账契约
- [contracts/platform-adapter.md](contracts/platform-adapter.md)：适配器四方法契约
- [contracts/llm-gateway.md](contracts/llm-gateway.md)：网关计费/缓存/重试契约
- [quickstart.md](quickstart.md)：端到端验证场景

## 宪章复核（阶段 1 后）

两段式落盘保证树 immutable 不被回流破坏（原则二）；网关为唯一 LLM 入口（原则三）；
promo 包单向依赖 core（原则五）；真实渠道以契约约束而非虚假接入（原则六）。
**无新增违规，门禁通过。**
