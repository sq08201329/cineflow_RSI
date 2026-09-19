# 实现计划：外环周校准（人评锚点 → calibration → 权重再拟合）

**分支**: `010-weekly-calibration` | **日期**: 2026-09-19 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/010-weekly-calibration/spec.md` 的功能规格说明（含 2026-09-19 澄清会话四条决议）

## 概要

交付 `core/calibration/` 包 + 迁移 0004：盲评清单生成（top-k、序列化键白名单零泄露）、
人评录入通道（`calibration_anchors` 表，INSERT-only 双方言触发器，同键幂等拒绝）、
平台真值锚点适配（promo 回流 → platform_truth 锚点）、偏差计算（均值偏移 + Pearson r，
judge 用 Kendall τ 序一致，复用 002 实现）、append-only 文件化校准台账与每周信度报告
（≥0.6 达标口径）、权重再拟合提案（约束岭回归自动拟合候选权重，人工仅确认/搁置、
禁止编辑）、生效管线（composite 新版本 `+w{权重哈希}` + configs 权重段定点改写保注释 +
失败回滚）。澄清决议贯穿：校准写台账不升版、promo 不盲评、防自循环配对剔除。
**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——复用现有栈（SQLAlchemy Core、pyyaml、numpy（约束岭回归）、
002 的 Kendall τ-b 实现）

**存储**: DB 新表 `calibration_anchors`（迁移 0004，INSERT-only 触发器，唯一键
`(node_id, reviewer, round_id)`）；派生产物文件化——`calibration/rounds|ledger|reports|
proposals/`（同 005 谱系文件化惯例，随 git 版本化）

**测试**: pytest；注入已知偏移数据集双向断言偏差数学；契约测试守清单零泄露/提案门禁/
版本不变；集成测试 PG 触发器；demo 走全流程

**目标平台**: Linux（WSL2 + CI）

**项目类型**: core 新增子包 `core/calibration/`（业务无关）+ `agents/promo/anchors.py`
（业务侧适配）——单向依赖不破

**性能目标**: 周级批处理，单轮 < 1 分钟（k≤5 锚点 × 个位数评估器，无性能敏感路径）

**约束**: 锚点存储层冻结（原则一/二）；盲评零泄露（机检）；防自循环配对；人工确认门禁
且禁止编辑候选权重（原则六）；历史得分永不重算（immutable 审计口径）；覆盖率 ≥85%

**规模/范围**: 1 个 core 子包（8 模块）+ 1 张表 + 1 个 promo 适配 + CLI/demo；不含
评审 UI（F8 前端只读，录入永远走 CLI）、judge 漂移检测（F7，本特性只供数）、
剧本/开发 Agent 接入

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | 校准写 append-only 台账、评估器版本不变；仅权重再拟合升版本（`+w{哈希}`，calibration 填台账快照）；锚点写入即冻结（human 类语义） | ✅ 满足 |
| 原则二：不可变与谱系 | `calibration_anchors` INSERT-only 触发器 + 唯一键；历史节点 score/eval_breakdown 永不重算（契约 C8 场景 4） | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 校准全程只读树/对象存储 + 读 promo 运营表，零生成调用（FR-011） | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 不涉及策略执行；盲评清单键白名单 + 契约测试是同一"防泄露"哲学在 UX 层的延伸 | ✅ 满足 |
| 原则五：单向依赖与配置化 | `agents/promo/anchors.py → core/calibration`；k/最小样本/阈值/目标值/λ_ridge/自循环排除全走 configs | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 人 = 稀疏 top-k 锚点（不逐条）；提案人工确认门禁、禁止编辑候选权重；负相关只告警不自动反向调权 | ✅ 满足 |
| 测试纪律 | TDD；注入数据集双向断言；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/010-weekly-calibration/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── anchors.md       # 盲评清单/录入/冻结/平台真值适配
│   ├── calibration.md   # 配对/偏差数学/台账/信度报告
│   └── refit.md         # 提案生成/确认生效/版本语义
└── tasks.md             # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在 001-005 结构上增量）

```text
core/calibration/
├── models.py            # AnchorScore/CalibrationRound/PairingRecord/BiasRecord/WeightProposal（frozen）
├── config.py            # calibration 段解析（top_k/min_samples/阈值/λ_ridge/自循环排除）
├── selection.py         # top-k 盲评清单（树只读查询 + 序列化键白名单）
├── anchors.py           # 录入校验 + INSERT 落库（幂等拒绝计数）
├── pairing.py           # 锚点×eval_breakdown 配对（自循环剔除，配置驱动）
├── bias.py              # mean_shift + Pearson r；judge 走 Kendall τ（复用 002）
├── ledger.py            # append-only 台账 + 最新快照读取
├── report.py            # 每周信度报告 JSON（达标标记 + 告警条目）
└── refit.py             # 约束岭回归拟合 + 提案生命周期 + 生效管线（注册/定点改写/回滚）
agents/promo/anchors.py  # 平台回流真值 → platform_truth 锚点（业务侧适配）
ops/migrations/versions/0004_calibration_anchors.py  # 表 + 双方言 INSERT-only 触发器 + 唯一键
ops/calibrate.py         # CLI：round / intake / report / propose / confirm / shelve
ops/demo_calibration.py  # 端到端演示（quickstart 六步）
configs/movie.yaml       # 追加 calibration 段（top_k/min_samples/bias_threshold/
                         # reliability_target/ridge_lambda/self_pairing_exclusions）
calibration/             # 数据目录（rounds/ledger/reports/proposals，git 版本化）
tests/unit/test_calibration_*.py；tests/contract/test_calibration_contracts.py；
tests/integration/test_calibration_pg.py
```

**结构决策**: 偏差数学/台账/提案管线属业务无关机制 → `core/calibration/`；平台真值
适配含 promo 业务语义 → `agents/promo/anchors.py`（单向依赖方向不变）。原始锚点入 DB
（存储层冻结是 FR-003 硬要求），派生产物全文件化（research 决策 1/2）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 锚点存 DB 表 + INSERT-only 触发器（存储层冻结只有 DB 给得了）；派生产物文件化
2. 偏差 = 均值偏移 + Pearson r；judge 用 Kendall τ（复用 002）；样本不足不产偏差
3. 自循环剔除配置显式声明（`self_pairing_exclusions`），拒绝命名约定推断
4. 权重拟合 = 约束岭回归（非负/和为一/向现权重收缩），numpy 实现零新依赖；
   小样本下收缩项主导，拟合天然保守
5. 生效 = composite 新版本（`+w{权重哈希}`，calibration 填台账快照）+ configs
   权重段定点改写保注释（与 WS3 遗留共用实现）+ 失败回滚
6. 盲评零泄露 = 序列化键白名单 + 契约测试断言

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：calibration_anchors 表 schema、五个 frozen 领域模型、
  四类文件 schema、轮次/提案状态机
- [contracts/anchors.md](contracts/anchors.md)（C1~C3）、
  [contracts/calibration.md](contracts/calibration.md)（C4~C6）、
  [contracts/refit.md](contracts/refit.md)（C7~C9）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步端到端场景 + 里程碑验收口径

## 宪章复核（阶段 1 后）

锚点 INSERT-only 触发器 + 唯一键（原则一/二）；配对自循环剔除与盲评白名单均有契约
测试承载（可证伪）；提案门禁"确认/搁置两键、无编辑路径"在 CLI 层物理成立（原则六）；
拟合算法收缩保守性与诚实边界同向。**无新增违规，门禁通过。**
