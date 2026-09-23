# 任务列表：开发 Agent 降级模式（选题/IP 评估 —— 记录-回放 + 人工策略 + 回放沙盘）

**输入**: 来自 `specs/017-dev-agent-degraded/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 **v2.0.0**（原则四已含"人工编写的降级模式策略"例外条款；**原则六为核心**：评估信号过弱的环节禁止强行自动进化；**原则一为核心**：数据源即行为口径、版本冻结）；功能 001/002/005/010/012/015 已交付；**009 已交付且必须行为等价**（改造前基线：`uv run pytest tests/unit tests/contract tests/unbiasedness -k screenplay` = 449 passed）；规格含 2026-09-23 澄清五条（不引入 judge/锚点、不补齐 010、立项组合为交付物、最小池门槛、全量阈值声明）

**测试说明**: 宪章要求 TDD——**测试任务与实现任务分列**（同 001/002/004/009 惯例），先写测试并确认失败再实现；覆盖率 ≥85%；本特性的关键是**三重机检常驻**、**证据来源缺失如实登记**、**最小池门槛**、**009 行为等价**、**策略执行隔离的两项义务**；本地验证命令与 `.github/workflows/ci.yml` 逐字一致。

**组织方式**: 按用户故事分组（US1 立项组合产出与全量记录 → US2 四评估器与合成评分 → US3 回放沙盘与人工改策略闭环）；阶段 2 的通用件抽取是三条故事线的共同前置。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 0：例外条款的落地要求（原动工闸门，已裁决）

- [X] T1701 **宪章 v2.0.0 原则四例外条款的两项落地义务**（原 D1 动工闸门，已裁决走路径 (b)：宪章已升 v2.0.0，为"人工编写的降级模式策略"新增显式例外）。本任务把两项义务变成可实现、可验证的验收项：① 回放对比的策略执行**必须**带**执行超时**（静态检查不禁循环，防策略内死循环占用宿主；009 现行无超时）② **必须**有断言守护"**不向策略执行交付任何环境对象**"（策略仅 `plan(inputs, config)`，既不 `observed()` 也不 `probe()`，探测由宿主代执行——防后续改动把模拟器或 observation 通道引入策略）。测试落点 T1707、实现落点 T1708、薄适配落点 T1741、聚合断言落点 T1739。**不得**以"沿用 009 现状"为由略过这两项

## 阶段 1：搭建（共享基础设施）

- [X] T1702 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 新增 `dev` 段（`slate.{min,max}`、`production_marks.{min,max}`、`combination.max_direction_repeat_rate`、`min_comparable_trees`、`signals.*`、`upgrade_criteria.*`）与 `evaluator_weights.dev`（2 gate + 2 proxy，两形态取值不同）；**同步登记既有门禁清单**：`tests/unit/test_form_switch.py` 顶层差异键集字面量与权重差异循环、`tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES`（加载器）、`WEIGHT_AGENTS`（权重）、`REQUIRED_PATHS`（至少一条 dev 缺项样例），以及 `tests/contract/test_pilot_contracts.py` 的 C13 差异键集（实现时发现的第三处钉死清单——三处漏一处即红）；并判定 `tests/conftest.py` 的 `_MINIMAL_MOVIE_CONFIG` 是否需补 `dev` 段（结论：不需，其只服务 pilot 侧加载器清单，已在注释中记录理由）（契约 C10；研究决策 8、9）
- [X] T1703 [P] conftest 夹具扩展：`dev` 配置片段工厂（合规 / 缺项 / 越界 / 权重缺失）+ 立项组合工件工厂（合规 / 方向重复 / 缺要点 / 条目数越界 / 空组合 / 标记越界 / 标记指向不存在条目）+ 人工策略源码夹具（含"死循环策略"用于超时用例）+ 模拟数据源夹具 + `dev` 临时库与数据目录夹具；不改坏既有夹具
- [X] T1704 [P] `policies/history/dev/` 人工策略首版（题材方向探索策略，作为引导树与谱系根）+ `.meta.json`（`no_auto_evolve=true` 审计标记）

## 阶段 2：基础（core/degraded 通用件抽取 + 009 薄化）

**⚠️ 关键**: 此阶段完成前不能开始任何用户故事的工作；本阶段完成后 009 必须逐条行为等价。

- [X] T1705 [P] `tests/unit/test_core_degraded_policy.py`（先写）：`submit_policy` 以 `agent_id="screenplay"` 与 `"dev"` 调用各落 `{history_root}/{agent_id}/`；版本 = 源码 BLAKE3 前 12 位；未过静态检查即拒绝且不入历史；meta 含 `no_auto_evolve` 审计位（契约 C1/C2）
- [X] T1706 [P] 实现 `core/degraded/policy.py`（`submit_policy(source_text, submitter, cfg, *, agent_id, history_root, parent_version=None, draft=False)`；静态检查接线；不含任何 agent 名分支）
- [X] T1707 [P] `tests/unit/test_core_degraded_compare.py`（先写；落实 T1701 的两项义务）：逐树得分/分项差异/pareto_auc/UNKNOWN 说明；**可比对树数 < 门槛 ⇒ 拒绝产出报告且错误含实测树数与门槛值**（SC-011）；append-only 拒绝重写；`match_key` 以可调用注入；**策略执行超时**（注入死循环策略 ⇒ 超时判 `CompareError` 而非挂死）；**"不向策略交付环境对象"断言**（策略只被喂 `plan(inputs, config)`，探测由宿主代执行）（契约 C1/C14）
- [X] T1708 [P] 实现 `core/degraded/compare.py`（落实 T1701 的两项义务：执行超时 + 无环境对象守护）（契约 C1/C14）
- [X] T1709 [P] `tests/unit/test_core_degraded_adoption.py`（先写）：采纳留痕（人/时间/依据/理由）+ 部署指针定点改写、注释与其他段逐字节保留；拒绝同样留痕；未采纳 ⇒ 指针逐字节不变（SC-002）
- [X] T1710 [P] 实现 `core/degraded/adoption.py`（复用 `core/yaml_edit.py`）
- [X] T1711 [P] `tests/unit/test_core_degraded_evidence.py`（先写）：**判据项抽象**逐项输出"实测值 / 无法评价（来源缺失）+ 原因"；漏配任一阈值项即产出报错；写"达标"或改写系统字段被拒；推翻留痕后系统字段逐字节不变；同周期 append-only（契约 C16/C17；SC-006）
- [X] T1712 [P] 实现 `core/degraded/evidence.py`（判据项 `{key, threshold_key, provider}` + 全量阈值快照 + 系统字段不可改写）
- [X] T1713 `agents/screenplay/{policy_versions,sandbox_compare,adoption,upgrade_evidence}.py` 改为薄适配：导出名、签名、关键字默认值、异常类与默认目录**逐字不变**；009 侧同步补齐 T1701 的两项义务（执行超时 + 无环境对象守护）（契约 C2）
- [X] T1714 判据材料路径迁移为 `calibration/upgrade-events/{agent_id}/{period}.json`（**不做 legacy 读取**）；同步 `tests/unit/test_screenplay_evidence.py` 与 `test_screenplay_cli.py` 的路径断言、`ops/screenplay.py`、`ops/demo_screenplay_loop.py`、`README.md` 的路径字面量；009 `quickstart.md` 验证记录**追加**"路径迁移复核"条目（契约 C3）
- [X] T1715 [P] `tests/unit/test_dev_core_degraded_purity.py`：`core/degraded/**/*.py` 无形态字面量 / 厂商字面量 / Agent 名 / 业务词，且零 `from agents.`（新增断言，仿 `tests/unit/test_orchestration_executor.py:317-336`）（契约 C1）
- [X] T1716 回归门禁：`uv run pytest tests/unit tests/contract tests/unbiasedness -k screenplay` 全绿且计数不低于 449；`uv run python ops/demo_screenplay_loop.py` 退出码 0、六步 ok

**检查点**: ✅ 通用件四模块就位并被 009 复用（无第二份实现）；判据材料分目录；`core/degraded/` 纯净性断言绿；009 回归基线不降；T1701 的两项义务在通用件层可验证

## 阶段 3：用户故事 1 - 立项组合产出与全量记录（优先级：P1）🎯 MVP

**目标**: 人工策略驱动一轮产出 → 立项组合工件内容寻址落树 → 成本入账 → 幂等与失败路径俱全。

**独立测试**: 确定性 Mock 网关 + 人工策略跑一轮：工件落库、分量入 `eval_breakdown`、成本三方对账、重复触发不重复扣费、FAILED 成本照计（不接回放即可交付价值）。

### 用户故事 1 的测试（先写，确认失败后再实现）

- [X] T1717 [P] [US1] `tests/unit/test_dev_artifact.py`：schema 版本化 / canonical JSON 与 `slate_hash` 逐位一致 / `to_dict↔from_dict` 往返 / 缺必填标记即拒绝 / 标记规则（区间 + 指向存在条目）/ 模拟源标注随产物落盘（契约 C4/C5；SC-009/010）
- [X] T1718 [P] [US1] `tests/unit/test_dev_config.py`：缺任一项即报错矩阵（条目数区间 / 标记区间 / 最小树数 / 模拟源参数 / 判据阈值）+ 两形态取值差异 + 权重来自 `evaluator_weights.dev`（契约 C10）
- [X] T1719 [P] [US1] `tests/integration/test_dev_loop.py`（真实 PG，`-m integration`）：迁移 `0010` 字段与枚举 CHECK、唯一键 `(round_id, params_hash)`、两段式落盘、幂等重建、FAILED 成本照计（契约 C11/C12）

### 用户故事 1 的实现

- [X] T1720 [US1] 实现 `agents/dev/artifact.py`（`TopicSlate` / `SlateEntry` / `SCHEMA_VERSION` / `canonical_json` / `slate_hash` / `produce_ids`；建模自 `agents/storyboard/shotlist.py`）
- [X] T1721 [P] [US1] 实现 `agents/dev/config.py`（`dev` 段解析与校验；缺项即报错，不取码内默认）
- [X] T1722 [P] [US1] 实现 `agents/dev/db.py` + `ops/migrations/versions/0010_dev_jobs.py`（单一产出、无 stage 列，唯一键 `(round_id, params_hash)`；`CHECK (actual ≤ estimated)`）
- [X] T1723 [US1] 实现 `agents/dev/loop.py`（`run_dev_round` + **`slate_match_key`**：只含策略可复现结构键、不含生成产物摘要；确定性 id、两段式落盘、`_reconcile` 三方对账、`DuplicateError → _reconstruct` 幂等重建、FAILED 路径）（契约 C11~C13）
- [X] T1724 [P] [US1] `agents/dev/policy_versions.py` 薄适配 + `ops/dev.py` 的 `produce` / `submit` 子命令（退出码语义同 `ops/screenplay.py`）
- [X] T1725 [US1] `ops/demo_dev_loop.py` 步①（立项组合产出落树与成本对账）②（结构/组合门禁短路重算——先用 Mock 断言，评估器在 US2 补齐）

**检查点**: ✅ `pytest tests/unit -k dev` 与 `-m integration` 绿；立项组合工件可落树可回放；成本对账一致

## 阶段 4：用户故事 2 - 四评估器与合成评分（优先级：P2）

**目标**: 2 gate + 2 proxy 四分量、gate 短路、定点归一；模拟数据源标注三处可见。

**独立测试**: 结构/组合违规 100% 判 0 并点名违规项；同输入重算逐位一致；改模拟源参数 ⇒ 新评估器版本且历史 breakdown 不变。

### 用户故事 2 的测试（先写，确认失败后再实现）

- [X] T1726 [P] [US2] `tests/unit/test_dev_evaluators.py`：`rule.slate_structure` 与 `rule.slate_combination` 违规矩阵 + 诊断点名具体违规项 + 不抛异常（`EvalResult(score=0.0, diagnostics.violations)`）（契约 C7/C8）
- [X] T1727 [P] [US2] `tests/unit/test_dev_signals.py`：模拟数据源确定性（同输入逐位一致）+ 来源标注与"非真实商业数据"可机读 + 参数变更 ⇒ 新 `evaluator_id@version` 且旧节点 breakdown 逐字节不变（契约 C9；SC-009）
- [X] T1728 [P] [US2] `tests/unit/test_dev_composite.py`：gate 短路（`rule.*` 判 0 ⇒ 总分 0、代理不参与）+ 适用权重归一（缺席分量不伪造 0 分拖底）+ quantize 定点 6 位 + 无适用连续分量 ⇒ 0.0（契约 C10）

### 用户故事 2 的实现

- [X] T1729 [P] [US2] 实现 `agents/dev/evaluators/_versioning.py`（按包复制，不可跨包导入）+ `slate_structure.py`
- [X] T1730 [P] [US2] 实现 `agents/dev/evaluators/slate_combination.py`（组合层核心门禁）
- [X] T1731 [P] [US2] 实现 `agents/dev/signals.py`（确定性模拟数据源 + 来源标注；参数化自 `dev.signals`）
- [X] T1732 [P] [US2] 实现 `agents/dev/evaluators/genre_regression.py` + `buzz_heat.py`（数据源参数入版本号）
- [X] T1733 [US2] 实现 `agents/dev/evaluators/composite.py` + `__init__.py` 装配（`build_dev_evaluators(config)`）
- [X] T1734 [US2] 接线 `agents/dev/loop.py`：装配四评估器、`config_snapshot` 冻结权重与阈值、三处（产物 / 对比报告 / 判据材料）落模拟源标注
- [X] T1735 [US2] `ops/demo_dev_loop.py` 步②补全为真实评估器重算

**检查点**: ✅ 四分量齐全且可复算；门禁违规 100% 判 0 并点名；标注三处可机读

## 阶段 5：用户故事 3 - 回放沙盘与人工改策略闭环（优先级：P3）

**目标**: 人工改策略 → 静态检查 → 最小池门槛下的回放对比 → 人工采纳才移动指针；判据材料如实登记来源缺失；三重机检常驻。

**独立测试**: 候选三路径（过检查且优/劣/未过检查）+ 树数不足拒绝产出；dreaming 候选 0 次；判据材料每项有"实测值或无法评价"。

### 用户故事 3 的测试（先写，确认失败后再实现）

- [X] T1736 [P] [US3] `tests/unit/test_dev_compare_adopt.py`：逐树得分/分项差异/pareto_auc/UNKNOWN 说明 + **最小池门槛拒绝产出**（错误含实测树数与门槛）+ 未采纳指针逐字节不变 + 采纳留痕 + append-only 拒绝重写（契约 C14）
- [X] T1737 [P] [US3] `tests/unit/test_dev_evidence.py`：全量阈值快照 + 每项"实测值 / 无法评价（来源缺失）+ 缺失原因" + 无空白无省略 + 漏配阈值项即报错 + 写"达标"或改写系统字段被拒 + 推翻留痕后系统字段逐字节不变 + 落 `calibration/upgrade-events/dev/{period}.json` 且同周期重产被拒（契约 C16/C17/C18）
- [X] T1738 [P] [US3] `tests/contract/test_dev_no_auto_evolve.py`：三重机检 dev 侧（名单实值含 `dev`、dreaming 候选 0 调用/0 计费/0 落盘、策略 meta 审计位、部署门禁对 `dev` 返回禁止名单且优先于"证据不足"）（契约 C15；SC-002）
- [X] T1739 [P] [US3] `tests/contract/test_dev_contracts.py`：C1~C18 聚合断言（含回放路径零 LLM、`max_generation_calls == 0`、匹配键只含结构键、零形态分支静态断言，以及 T1701 的两项义务：**策略执行超时**与**"无环境对象"守护**）（SC-005）
- [X] T1740 [P] [US3] `tests/unbiasedness/test_dev_unbiased.py`：回放 vs 真实重跑 Kendall τ ≥ 0.95 + 注入偏差 100% 拒绝 + 未达标不得产出对比报告（SC-008）

### 用户故事 3 的实现

- [X] T1741 [US3] `agents/dev/sandbox_compare.py` + `agents/dev/adoption.py` 薄适配（绑定 `slate_match_key` 与 `dev.min_comparable_trees`、`dev/comparisons/`、`dev/adoptions/`；落实 T1701 的两项义务）
- [X] T1742 [US3] `agents/dev/upgrade_evidence.py` 薄适配：登记两类来源缺失（无 judge ⇒ 无 012 漂移材料；010 排除 ⇒ 无信度数据）并输出待补齐阈值项清单
- [X] T1743 [US3] `ops/dev.py` 的 `compare` / `adopt` / `reject` / `evidence` 子命令
- [X] T1744 [US3] `ops/demo_dev_loop.py` 步③（无偏性凭证 + 回放对比，含门槛拒绝分支）④（采纳/拒绝与指针留痕）⑤（三重机检拒绝语义）⑥（判据材料）

**检查点**: ✅ 人工策略闭环可用；少样本不出报告；采纳是唯一移动指针的动作；判据材料结论非"达标"且每项有取值形态；三重机检常驻全绿

## 阶段 6：打磨与横切关注点

- [X] T1745 [P] `README.md` 新增"开发 Agent 降级模式（功能 017）"章节（命令 / 诚实边界 / 判据材料新路径），并同步既有路径字面量
- [X] T1746 [P] `agents/dev/export_slate.py` + `tests/unit/test_dev_export.py`：导出确定性、门禁违规不丢条目、导出含 `schema_version` 且**不**预写 `FieldParity`（属 G2）（契约 C6）
- [X] T1747 `quickstart.md` 验证记录逐条回填（各套件通过数、实测 τ、demo 退出码与耗时、覆盖率、ruff 双绿、人工策略首版与部署指针）
- [X] T1748 门禁复核：`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`、`uv run pytest tests/contract`、`tests/adversarial -m adversarial`（**沿用既有对抗面、不新增用例**——隔离面由 T1701 的两项义务与 T1739 的断言保证）、`ruff check .` + `ruff format --check .`（与 ci.yml 逐字一致）
- [X] T1749 `docs/三期立项书.md` §3.1 G1 行与 §4 里程碑标注交付状态，并把"017 引入 `core/degraded/` 通用件、009 已迁移复用"与"原则四 v2.0.0 例外条款 + 两项落地义务"记入该特性的交付说明

## 依赖关系与执行顺序

### 阶段依赖

- **阶段 0（T1701）**: 已裁决（宪章 v2.0.0 例外条款）；其两项义务由 T1707/T1708/T1713/T1739/T1741 落实，**不再阻塞**其他任务
- **阶段 1（搭建）**: 无依赖，可立即开始
- **阶段 2（通用件抽取）**: 依赖阶段 1；**阻塞全部用户故事**（009 行为等价是后续一切的前提）
- **阶段 3（US1）**: 依赖阶段 2；产出工件与轮次循环
- **阶段 4（US2）**: 依赖阶段 3（节点与 `eval_breakdown` 落点）
- **阶段 5（US3）**: 依赖阶段 3、4（回放打分需要评估器与树池）
- **阶段 6（打磨）**: 依赖全部用户故事完成

### 并行机会

- T1703/T1704、T1705/T1709/T1711（测试）、T1706/T1710/T1712（实现）互相独立，可并行
- 每条故事内标 [P] 的测试与实现文件互不重叠，可并行；**测试先于实现**
- US2 的四个评估器文件（T1729~T1732）互相独立，可并行；`composite.py`/装配（T1733）需其后
- US3 的五份测试文件（T1736~T1740）互相独立，可并行

## 实现策略

### MVP 优先（用户故事 1）

1. 阶段 1 → 阶段 2（**不可跳过**：009 行为等价是硬前置）
2. 阶段 3（US1）→ 独立验证：`pytest tests/unit -k dev` + `-m integration` + demo 步①②
3. 此时即交付"选题产出可记录"的价值（立项组合落树可回放 + 成本可对账）

### 增量交付

1. US1 → 记录（MVP）
2. US2 → 评估与合成（信号可测量）
3. US3 → 回放沙盘与判据材料（降级模式闭环 + 三重机检）
4. 阶段 6 → 契约聚合 / 验证记录 / 文档

---

## 备注

- **T1701 是原则四例外的落地闸门**：宪章 v2.0.0 已为"人工编写的降级模式策略"给出显式例外（策略为纯规划、不交互模拟器），但**附两项必须落地的义务**——策略执行超时、"不向策略执行交付环境对象"的守护断言。009 现行两者皆无，改造时一并补齐；未落实即视为原则四未通过
- **原则六落点（本特性核心）**：T1738 三重机检 + T1737 的"结论恒不达标 + 来源缺失逐项标注" + T1742 的两类缺失登记——**不引入伪 judge、不引入锚点、不擅改 010**，是规格与计划的既定选择，实现时不得"顺手补齐"
- **原则一落点**：T1732 的"改模拟源参数即新版本、历史 breakdown 不变"断言 + T1721 的配置快照冻结——数据源即行为口径
- **原则三落点**：T1739 的回放零 LLM / `max_generation_calls == 0` / 匹配键只含结构键
- **有意的 009 改造**（T1713/T1714）：公共 API 与默认目录逐字不变，回归基线 449 passed 不降；判据材料路径迁移是修既有缺陷（同周期双写互相覆盖），须同步 009 的测试、ops、demo、README 与 quickstart 记录
- **不做**（规格已明确，防止顺手扩大）：真实票房/舆情数据源接入（属 G3）、judge 层、人类锚点、010 接入、pilot 阶段插入与 017→018 字段级交接、数据源可插拔接口、开发 Agent 自动进化
- **SC 映射**：SC-001→T1744/T1747；SC-002→T1738；SC-003/004→T1726~T1728；SC-005→T1739；SC-006→T1737；SC-007→T1748；SC-008→T1740；SC-009→T1727/T1734；SC-010→T1717/T1726；SC-011→T1736
- 本地验证纪律：命令与 `.github/workflows/ci.yml` 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
