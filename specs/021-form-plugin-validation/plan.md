# 实现计划：形态插件扩展性验证（配置化评估器插件接入 + 零形态分支静态断言补面 + 接入改动清单机检）

**分支**: `021-form-plugin-validation` | **日期**: 2026-09-25 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/021-form-plugin-validation/spec.md` 的功能规格说明（3 用户故事 / 14 FR / 9 SC / 14 条边界情况 / **13 项裁决** / 2 项开放问题）

## 概要

把 G5 的验收原文（`docs/三期立项书.md:167`）——"**广告/漫剧形态以配置化评估器插件接入（零代码改动验证）**"、
关键验收"**新形态接入 = 仅新增配置 + 评估器插件；静态断言无形态分支**"——落成**两块可分账的交付**：

- **A 机制侧总账（本特性的交付主体，FR-013 逐项可枚举）**：① 配置驱动的插件声明与**唯一装配点**
  （`evaluators.plugins.<agent>.<slot>.<evaluator_id>.{impl, version, params}` + `importlib` 解析 + 通用参数通道）；
  ② 扫描面**补面**（字面量层与判断分支层**都**覆盖 `core/` + `agents/` **含 `agents/pilot`**，锚点改**符号名**）；
  ③ 形态名**由 `configs/*.yaml` 的 `form:` 自动派生**、**三份副本收敛为单一实现（副本数 ⇒ 1）**；
  ④ `agents/pilot/pilot.py:613` 的裸形态词**收敛**；⑤ `tests/unit/test_form_switch.py:438-441` 的"恰好两份"
  升级为**登记完备**口径；⑥ **接入改动清单**机检（列清改了哪些文件 + 逐条越界判定）。
- **B 新形态接入（机制建成后的举正面）**：广告 / 漫剧形态**仅新增配置 + 插件**即可跑通（两形态**共用同一份
  插件代码**），并以"接入改动清单"证明本次接入**未触碰**任何既有模块逻辑。

**诚实分层（写进计划的硬约束，不得混淆）**：A 是**本特性的代码改动**（触及 `core/` 与六个 Agent 装配面、
测试与守卫面），**禁止**把 A 说成"零代码改动"；B 才是"仅新增配置 + 插件"的举正面，且其判据的基线必须取
"**机制落地后、接入前**"的提交（否则判据自相矛盾，FR-004 末句）。另一条硬约束是 **既有两形态的评估器组合、
权重键集、`eval_breakdown` 与得分逐字节不变**（FR-013）——本仓的**技术含义**比字面更硬：评估器版本号把
**实现文件字节**并入哈希（`agents/sound/evaluators/_versioning.py:22-23`），因此机制改动**一律不碰**
`agents/*/evaluators/*.py` 的既有实现文件（research.md 决策 4）。

**今天"仅新增配置 + 插件"不成立（本特性必须先补机制）**：配置里**只有** `evaluator_weights` 的 id → 权重映射
（`configs/movie.yaml:6`、`configs/shortdrama.yaml:8`），**零**实现引用；装配硬编码在六个函数里逐个 `new`
（`agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、`agents/screenplay/evaluators/__init__.py:40`、
`agents/storyboard/evaluators/__init__.py:26`、`agents/sound/evaluators/__init__.py:24`、
`agents/editing/evaluators/__init__.py:27`）；形态名清单是人工常量（`tests/unit/test_form_switch.py:413`）；
**五处登记点**有三处是两形态硬编码，新形态要么静默逃逸、要么硬失败（`tests/conftest.py:3072`）。

本特性**不发明**业务定义（广告/漫剧的受众、指标口径、素材规格、评估器组合的业务正确性属业务侧输入），
未给定期间按**最小可行形态**接入并如实标注"**未标定**"；**不扩 cadence 量纲**（`{1,7}` 之外即显式报错）；
**不新造第六处登记点**；**不做**真实投放、新形态真实素材生成、多租户/公网服务化。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: **零新增运行时依赖**——stdlib（`importlib` / `inspect` / `ast` / `subprocess`（git）/ `pathlib` /
`json` / `hashlib`）+ 既有 `blake3`（既有依赖，`core/orchestration/models.py:36` 的 `fingerprint_of` 口径）+
既有 `pyyaml`。**不引入**任何第三方插件框架（原则五与复杂度纪律，`.specify/memory/constitution.md:222`）。

**存储**: **零数据库变更、零迁移**——本特性的全部产物是**文件**：接入改动清单与其 append-only 索引
（`index.jsonl`，落 `--out` 指定目录）、守卫与登记点的机检报告（JSON）。发现树、`CostRecord`、
`eval_breakdown`、得分**一律不动**（原则一/二）。

**测试**: pytest（TDD，测试任务先于实现）。新增单元/契约套件；既有断言**按扩展更新、不削弱**（research.md
决策 12 的 19 项清单，**零删除、零放宽**）。全部用例离线（零真实花费、零外部网络、零凭证）；对抗（合并阻塞）、
无偏性（发布阻塞）、Immutable 审计与成本回归（每日）四道常驻门禁**不放松**；覆盖率 ≥85%（口径不降，含 web）。

**目标平台**: Linux（WSL2 + CI）。`git` 为本特性 CLI 的外部依赖（既有开发环境必备；CI 上同样可用）。

**项目类型**: `core/evaluators/`（**新 1 模块 + 新 1 目录**）+ 六个 Agent 的装配面（**机制改动，签名不变**）
+ `configs/*.yaml`（两形态加段 + B 阶段两份新形态配置）+ `ops/`（新 3 模块 + 新 1 演示）+ 测试与夹具同步。

**性能目标**: **不设性能门禁**——单次动作量级是"读若干 YAML + AST 解析数十个源文件 + 一条 `git diff`"，
源文件规模为数百个 `.py`；本特性的优先级是**判据的可机检性与不可逃逸性**（宁可拒绝、不可静默兜底），不是吞吐。

**约束**: `core/` 保持业务无关、依赖单向 `agents → core`（原则五，`.specify/memory/constitution.md:117-118`）：
`core/evaluators/plugin.py` 只认"声明 → callable → 参数注入"这一通用协议，**零形态名、零形态分支、零 Agent 名**；
形态名的读取与判定全部落在 `ops/form_guard.py`（读 `configs/` 的 `form:`，**不是**业务逻辑）；插件实现
（既有 + 新增）**零**形态字面量/分支、零 `agents.*`/`dreaming.*` import（文本 + AST 双层机检）。全部新参数
形态配置化、**缺项即报错、不取码内默认**。

**规模/范围**: 新 1 模块（`core/evaluators/plugin.py`）+ 新 1 目录（`core/evaluators/plugins/`）+ 6 个
`agents/<agent>/evaluators/plugins.py`（薄工厂）+ 6 个装配函数改委派 + 2 份既有配置加段 + 2 份新形态配置 +
新 3 ops 模块 + 新 1 演示 + 测试与夹具同步；**不含**: 真实投放（B/C 路径不变）、真实素材生成、多租户/公网
服务化、cadence 量纲扩展、promo 构造点的插件化（见"本特性不做什么"）。

## 现状勘查：四处真实缺口（先核实，后设计）

| # | 事实（逐条可核实） | 本特性处理 |
| --- | --- | --- |
| 1 | **装配无实现引用、硬编码在六个函数**：全仓 `plugin`/`entry_point`/评估器侧 `import_module` 命中 **0**；六个装配函数逐个 `new` 并喂 Agent 配置字段（`agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、`agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、`agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27`） | A1：声明面 + 唯一装配点 + 通用参数通道；六函数改委派（**签名与返回形状不变**） |
| 2 | **参数面硬编码**：参数经各 Agent 的 dataclass 逐字段读取校验（先例 `agents/visual/config.py:24` 的 `_require_judge`、`:54` 的 `from_dict`；`agents/sound/evaluators/__init__.py:26-30` 直取 `config.loudness`/`config.asr["cer_cap"]`）⇒ 新参数必须改既有 config 类 | A1：`params` 为唯一新增参数通道；既有参数**不搬迁**（单一事实源） |
| 3 | **扫描面盲区与人工常量清单**：字面量扫描面**显式排除 `agents/pilot`**（`tests/unit/test_form_switch.py:423` 的 `if "pilot" not in path.parts`），而 `agents/pilot/backends.py` 是装配点；形态名清单是人工常量且**三份副本**（`:413`/`:414`、`tests/unit/test_billing_core_purity.py:34-35`、`tests/unit/test_dev_core_degraded_purity.py:28-29`）⇒ 新形态名**天然逃逸**；020 登记的扫描面**行号已漂移**（`specs/020-shortdrama-real-feedback/tasks.md:692`） | A2：扫描面补到 `core/` + `agents/` 全覆盖（含 pilot）+ 形态名派生 + 三副本**委派收敛**（语义只增不减）+ 锚点改符号名 |
| 4 | **五处登记点中三处两形态硬编码、一处"恰好两份"**：① `tests/unit/test_form_switch.py:341-379` 固定 16 键差异集 + `:30` 的 `FORMS`；② `tests/unit/test_config_integrity.py:23-40`/`:48-98`/`:153-154`；③ `tests/contract/test_pilot_contracts.py:434-453`；④ `agents/pilot/pilot.py:377`（**按配置路径通用**，最靠得住）；⑤ `tests/conftest.py:2854` + `:3054-3079`（未知形态 `:3072` **硬失败**）；另"恰好两份"断言 `tests/unit/test_form_switch.py:438-441` 使第三份配置**必红** | A3：三处改**配置/形态派生**；④ 保住通用性并加 020 口径逐项机检；⑤ 改派生；"恰好两份"改**登记完备**口径 |

**口径澄清 A（最易误读）：机制改动 = 本特性的交付主体，不是"零代码改动"。** G5 的"零代码改动"是**接入侧**
的判据（B），其基线必须取机制落地后的提交；把 A 的改动混进 B 的账，会让判据自相矛盾（FR-004 末句、
`specs/021-form-plugin-validation/spec.md:87` 的场景 5）。计划里 A 与 B **分开编号**（A1~A5 / B1~B3）。

**口径澄清 B：两形态的 `evaluators` 段**逐字相同**，因此既有差异集断言一字不改。** `movie`/`shortdrama`
的评估器组合相同（既有断言 `tests/unit/test_form_switch.py:161` 的 `set(movie_w) == set(short_w)`），且既有
评估器的参数**不搬迁**（`params: {}`）⇒ 两形态新增的 `evaluators` 段完全一致 ⇒ 顶层差异集
（`tests/unit/test_form_switch.py:341-379`、`tests/contract/test_pilot_contracts.py:434-453`）**不进入差异集**、
断言原样保留。本结论**常驻机检**（新增断言：两形态 `evaluators` 段逐字相等）。

**口径澄清 C：`version` 是**校验**而非**覆盖**。** 既有版本号是派生值（`1.0.0+<实现文件与口径参数哈希前 12 位>`，
`agents/dev/evaluators/_versioning.py:13` 同款）；声明面的 `version` 必须与实例产出的 `spec.version` **逐字
相等**，不等即装配期报错。理由：允许声明覆盖 = 允许配置**谎报**版本 ⇒ 原则一"口径即版本"不可证伪
（research.md 决策 3）。

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| **原则一** 评估器确定性与版本冻结（`evaluator_id@version` 全局唯一、同 id 同 version 重复注册**必须**被拒、行为变更**必须**升版本号、`human` **仅**作校准锚点） | 插件声明的 `version` **必须显式**且与实现产出的 `spec.version` **逐字相等**（差别即装配期报错，**不得**以声明覆盖实现）；`<evaluator_id>` 键 == `spec.evaluator_id`；注册仍走唯一入口 `core/evaluators/registry.py:18`（`:29-33` 非确定性拒绝、`:35-38` 同键重复注册拒绝原样生效）；既有两形态的 `(id@version, ...)` 装配序列改造前后**逐字相同**（机检）；`human` 锚点不入装配集合（其语义不变） | ✅ 满足 |
| **原则二** 节点不可变与全量谱系；`CostRecord` 含 `FAILED` **必须**入账 | 发现树、`CostRecord`、`eval_breakdown`、得分**一律不动**；本特性产物为报告/清单/机检报告类文件（append-only），不写任何权威数据面；无 DB 迁移 | ✅ 满足 |
| **原则三** 昂贵动作仅限线上探索；**一切 LLM 调用必须经网关**、真实渠道**必须**受预算门禁与账单对账 | 本特性**零真实渠道调用**：最小可行形态**不声明 judge** ⇒ 离线端到端演示**不构造 LLMGateway**（若确需构造，则该演示点**必须**显式登记进 `OFFLINE_ASSEMBLIES`（`tests/unit/test_billing_core_purity.py:268-285`）并同步计数 `:359`，见阶段 B3 与 research.md 决策 12 的第 15 项）；插件**禁止**直连 LLM/厂商 API、禁止读凭证；**不新增**任何旁路门禁 | ✅ 满足 |
| **原则四** 沙箱隔离与前缀不可泄露（含 v2.0.0 的降级策略显式例外） | 本特性**不涉及**策略执行、不触模拟器、不引入新的执行路径 ⇒ **既不触发任何条款，也不适用例外**（无新增策略代码、无新的策略执行面） | ✅ 不适用/不触及 |
| **原则五** 单向依赖 + 形态差异**必须**经 `configs/*.yaml` 表达、切换形态**必须**零代码改动 + **例外必须是"新增配置项"而非"新增分支代码"** | ① 插件清单是**新增配置项**（顶层段 `evaluators`，`.specify/memory/constitution.md:121` 的直接落点），装配按声明实例化，**零形态分支**；② 唯一装配点 `core/evaluators/plugin.py` **业务无关**（只认"声明 → callable → 关键字注入"，零形态名/零 Agent 名/零分支），依赖方向 `agents → core` 单向；③ `ops/form_guard.py` 读形态名但**不引入形态分支**（形态名只作**数据**参与文本判定）；④ 新增参数全部配置声明、缺项即报错、不取码内默认；⑤ **新形态接入不得改动任何既有 Agent 的装配函数**，且装配集合 ↔ 权重键集一一对应（缺项/多项即拒） | ✅ 满足 |
| **原则六** 指标口径**必须**可被证伪；人**不得**参与逐条评估；Calibration 结论写入 `calibration` 字段 | 本特性的全部主张都落成**可独立复核**的机检物：接入改动清单（逐条路径 + 类别 + 越界判定，越界即非 0 退出码）、零分支两层断言（字面量 + 判断分支，注入即红的有牙齿自检）、登记完备性（五处逐一 + 不新造第六处的常驻清单）、020 口径声明完备（缺项即拒绝启动）、离线端到端（退出码 0 + 零花费/零网络/零凭证）。局限**如实标注**：广告/漫剧业务定义未标定（配置段 `note` + 产物 `uncalibrated_reason`）；cadence 近似关系如实登记；**不得**以模拟冒充标定 | ✅ 满足 |
| **技术栈与架构约束** Monorepo 边界：新增目录**必须**先归入 `core`（业务无关）或 `agents`（业务相关）之一 | 两个新目录各有归属：`core/evaluators/plugins/`（**业务无关**的通用评估器，接受参数、零形态概念）、`agents/<agent>/evaluators/plugins.py`（**Agent 绑定**的薄工厂，只在语义确实与某 Agent 绑定时落此处，且**同样必须经 `impl` 声明才生效**——目录不决定可用性） | ✅ 满足 |
| **开发工作流：测试纪律**（TDD；测试禁止依赖真实昂贵调用；**新增评估器必须同时提交**单元测试、注册元数据含 `cost_per_call`、与既有评估器的对比样本） | A1~A5 与 B1~B3 各阶段均按 TDD 序（先写测试使其因目标行为缺失而失败，再实现）；全部用例离线；B 阶段新增的通用插件**必须**同时提交单元测试 + 声明面（`version`/`cost_per_call` 由 `core/evaluators/base.py:42` 强制）+ 与既有评估器的对比样本（沿用既有对比样本放置约定，由 tasks 阶段细化） | ✅ 满足 |
| **门禁清单**（对抗 / 无偏性 / Immutable 审计 / 成本回归；覆盖率 ≥85%） | 四条常驻门禁**不放松**：对抗与无偏性套件用例体零改动；Immutable 审计因"节点与得分零改动"而无需改判据；成本回归不受影响（零真实调用）。覆盖率 ≥85% 口径不降（含 web）；新增模块与 ops 工具均带单元用例 | ✅ 满足 |
| **治理：复杂度必须被论证**（新增抽象/框架/依赖需说明为何既有能力不足，YAGNI） | 新增抽象逐项写入"复杂度跟踪"表：`core/evaluators/plugin.py`、`core/evaluators/plugins/`、6 个 Agent 绑定工厂文件、`ops/form_guard.py`、`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py`、`evaluators` 配置段；**零新增第三方依赖** | ✅ 满足 |

**门禁通过。** 三条**前置**是不可省的：(a) "既有两形态装配序列逐字不变"的对照机检必须**先于**装配函数改造
落地（否则原则一/二失守）；(b) 三副本收敛必须以"**委派**、断言语义保留原位"完成，**不得**借收敛之名删除或
削弱任一处断言；(c) `params` 的严格性规则（签名无默认参数集 == `params` 键集 ∪ 注入槽位集）必须**常驻**，
否则"缺声明即报错、不取码内默认"会退化为注释。三条分别由 **C2**/**C4**（唯一装配点与调用约定、`version` 一致性校验）、**C7**（三副本委派收敛与只增不减）、**C3**（插件目录与"声明才生效"、参数严格性）承载。

## 项目结构

### 文档（此功能）

```text
specs/021-form-plugin-validation/
├── spec.md / plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── plugin-config.md      # C1 声明段形状与解析 / C2 唯一装配点与调用约定 / C3 插件目录与"声明才生效" / C4 version 显式校验（C1~C4）
│   ├── zero-form-branch.md   # C5 字面量层扫描面 / C6 判断分支层与形态名派生 / C7 三副本委派收敛 / C8 裸形态词收敛与例外登记（C5~C8）
│   ├── form-registration.md  # C9 五处登记点改配置/形态派生 / C10 登记完备口径 / C11 020 口径声明完备与 cadence 收口（C9~C11）
│   └── onboarding-ops.md     # C12 接入改动清单 / C13 机制侧总账登记与"不得冒充零代码改动"机检 / C14 CLI 与离线端到端演示（C12~C14）
└── tasks.md                  # 阶段 2 输出（/skill:speckit-tasks）
```

**契约编号（固定，本计划引用时以此为准，与契约文件逐字一致）**：`contracts/plugin-config.md` ← **C1~C4**
（C1 声明段形状与解析｜C2 唯一装配点与调用约定 `impl(**params, **槽位)`｜C3 插件目录与"声明才生效"（缺声明即报错）｜
C4 `version` 显式校验与"不允许覆盖实现"）；`contracts/zero-form-branch.md` ← **C5~C8**（C5 字面量层扫描面含例外三条｜
C6 判断分支层扫描面 + 形态名派生｜C7 三副本委派收敛｜C8 裸形态词收敛与例外登记）；`contracts/form-registration.md`
← **C9~C11**（C9 五处登记点改配置/形态派生｜C10 登记完备口径｜C11 020 口径声明完备与 cadence 收口）；
`contracts/onboarding-ops.md` ← **C12~C14**（C12 接入改动清单｜C13 机制侧总账登记与"不得冒充零代码改动"机检｜
C14 CLI 与离线端到端演示）。本计划**不复述**契约已定的键清单，只引用编号。

### 源代码（仓库根目录，在既有结构上增量）

```text
core/evaluators/plugin.py          # 新（业务无关）：唯一装配点——清单解析 / importlib 解析 module:attr /
                                   #   关键字注入（params + 注入槽位）/ spec.evaluator_id 与 version 一致性校验 /
                                   #   装配集合 ↔ evaluator_weights.<agent> 键集一一对应 / 保序
core/evaluators/errors.py          # 既有：在 EvaluatorError（:8）之下增「声明/装配期」错误类型
core/evaluators/plugins/__init__.py# 新目录（业务无关）：通用评估器（B 阶段新形态的插件落点；
                                   #   同样必须经 impl 声明才生效——目录不决定可用性）
agents/visual/loop.py              # 既有：build_evaluators（:207）改委派唯一装配点；_judge_anchor_hashes（:103）
                                   #   迁至 agents/visual/evaluators/plugins.py（新文件）并保持语义（:138 调用点同步）
agents/sound/evaluators/__init__.py     # 既有：build_sound_evaluators（:24）改委派（返回**扁平列表**、保序不变）
agents/screenplay/evaluators/__init__.py# 既有：build_screenplay_evaluators（:40）改委派（dict 形状不变）
agents/storyboard/evaluators/__init__.py# 既有：build_storyboard_evaluators（:26）改委派（dict 形状不变）
agents/editing/evaluators/__init__.py   # 既有：build_editing_evaluators（:27）改委派（dict 形状不变）
agents/dev/evaluators/__init__.py       # 既有：build_dev_evaluators（:31）改委派（dict 形状与 registry 注入不变）
agents/<agent>/evaluators/plugins.py    # 新（6 个）：Agent 绑定薄工厂——一评估器一函数、纯关键字签名
                                   #   （既有参数从 agent_config 槽位读，单一事实源；新参数走 params）
agents/pilot/pilot.py              # 既有：config_completeness（:377）追加 020 口径逐项机检（cadence ∈ {1,7} /
                                   #   窗口口径与生效日 / 渠道命名空间 / 归属日生效日 / 迁移口径 / 运行窗口下限与
                                   #   断档容差）；"不适用"必须显式声明；:613 的裸形态词收敛为中性措辞
core/calibration/config.py         # 既有：CalibrationConfig.from_dict 增一道 cadence 校验（取值域收口，
                                   #   periods.py:30 的取值域**一字不改**）
ops/form_guard.py                  # 新（业务无关静态守卫，单一实现）：declared_forms / form_literals /
                                   #   form_branch_patterns / iter_sources（core+agents 全覆盖含 pilot）/
                                   #   literal_violations / branch_violations（符号名锚点）/ classify_exception（例外三条）
ops/form_onboarding.py             # 新：接入改动清单（git 派生 + 类别判定 + 越界逐条点名 + append-only 产物 +
                                   #   配置指纹）+ 五处登记点完备性 + 不新造第六处的常驻清单与反向扫描
ops/form_plugin.py                 # 新 CLI（门面）：guard / registration / onboarding / sync-versions
                                   #   （退出码与既有工具一致：0 通过 / 1 越界或判定失败 / 2 用法或配置错误）
ops/demo_form_plugin.py            # 新：离线端到端演示（新形态从配置跑到跑通；零花费/零网络/零凭证；退出码 0）
configs/movie.yaml                 # 既有：新增 `evaluators` 段（:6 的 evaluator_weights 之侧）；既有取值零改动
configs/shortdrama.yaml            # 既有：同上，且与 movie 的 `evaluators` 段**逐字相同**（口径澄清 B）
configs/<new-form>.yaml            # 新（B 阶段两份）：新形态配置（段集合与既有两形态一致 + 020 口径逐项声明 +
                                   #   "未标定"标注 + 新插件声明）
tests/…                            # 见阶段 A1~B3 与 research.md 决策 12 的变红清单
```

**结构决策**: 三块落点各有其必然性。① **唯一装配点必须落 `core/evaluators/`**：它是评估器框架的组成
（与 `registry.py` / `base.py` / `weights.py` / `composite.py` 同层），且**业务无关**——插件清单只谈
"声明 → callable → 参数"，与形态名/Agent 名无关（原则五）。② **Agent 绑定的薄工厂落
`agents/<agent>/evaluators/`**：工厂要读该 Agent 的配置对象（`agent_config` 槽位），这是**业务侧**语义；
但它**必须经 `impl` 在配置里声明**才生效（"目录决定不了可用性、配置声明才决定"）。③ **形态名派生与静态守卫
落 `ops/`**：守卫要读 `configs/*.yaml` 的 `form:` 值（形态配置概念），放 `core/` 会污染业务无关层，放 `tests/`
会让 CLI 依赖测试（`ops/` 被测试直接导入有先例：`tests/unit/test_audit.py:10`、`tests/unit/test_check_credentials.py:19`）。
④ **既有评估器实现文件零改动**：`implementation_version` 把调用方实现文件字节并入哈希
（`agents/sound/evaluators/_versioning.py:22-23`），改文件即改版本 ⇒ 改 `eval_breakdown` ⇒ 违反 FR-013
（research.md 决策 4；这是本计划最重要的落点约束）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要（**12 条**，与本计划逐条对齐）：

1. **插件声明形状与唯一装配点**——`evaluators.plugins.<agent>.<slot>.<evaluator_id>.{impl, version, params}`；
   `impl` 由唯一装配点 `importlib` 解析为 **callable**；装配集合 ↔ `evaluator_weights.<agent>` 键集**逐字相等**；
   声明顺序即装配顺序；**配置声明集 = 可用插件全集**（目录不决定可用性）。被否决：`entry_points`、装饰器/导入
   即自动注册、目录扫描自动生效、把 impl 塞进 `evaluator_weights`。
2. **通用参数通道**——`params` 是**唯一新增参数通道**（`impl(**params, **注入槽位)`，纯关键字）；既有评估器的
   既有参数**不搬迁**（由 Agent 绑定工厂经 `agent_config` 槽位读，单一事实源）；严格性规则 = 签名中无默认值的
   参数集 **恰好等于** `params` 键集 ∪ 注入槽位集（多/少即报错 ⇒ 缺声明即报错、码内默认不生效）。
3. **`version` 显式声明 = 校验一致**，**不以声明覆盖实现**；辅助 `sync-versions --check|--write`（默认只报差集）。
4. **既有评估器实现文件零改动**（版本哈希含实现文件字节）——机制改动全部落在新文件与装配函数体上。
5. **形态名一律由 `configs/*.yaml` 派生**；单一实现 `ops/form_guard.py`；三副本**委派**收敛（副本数 ⇒ 1，
   断言语义只增不减）；派生失败即报错。
6. **锚点以符号名为准**；例外**只有三条**（配置路径字面量 / 测试夹具（即 `tests/**` 不在扫描面）/ docstring
   中性描述）；`agents/pilot/pilot.py:613` 的裸形态词**收敛、不得加例外**；ASCII 形态名按**词边界**判定、
   中文别名按子串判定（避免短 id 假阳性）；守卫自带"有牙齿"合成反例。
7. **"恰好两份"→"登记完备"**：两两唯一 ∧ 配置集合 ⊆ 登记派生集 ∧ 下界 ≥2；**禁止删除**；② 的非平凡性与
   有牙齿自检。
8. **五处登记点逐处改法**（三处两形态硬编码改派生、④ 保通用性并加 020 口径机检、⑤ 改派生）+ **不新造第六处**
   的常驻清单与反向扫描。
9. **接入改动清单**：基线 = 机制落地后、接入前；改动集合由 `git diff --name-status` **派生**（一致率 100% 由
   构造保证）；判定按**类别**（配置 / 插件 / 测试与文档 / 越界）而非路径前缀；越界逐条点名 + 非 0 退出码；
   产物 append-only 并含基线 ref 与配置指纹。
10. **cadence ∈ `{1,7}` 的显式报错落点**：`CalibrationConfig.from_dict` 收口 + `config_completeness` 预检可见；
    `core/calibration/periods.py:30` **一字不改**；近似关系必须如实登记（与"按周近似兜底"的区别写清）。
11. **最小可行形态的定义与"未标定"标注口径**：段集合与既有两形态一致 + 每 Agent ≥1 门禁 + **不声明 judge**
    （结构性零花费）+ 尽量复用既有插件；标注落 `pilot.rehearsal.status: unstandardized`（`agents/pilot/pilot.py:53`）
    + 承载业务数字的段带非空 `note`（含"未标定"字样）+ 产物复现标注。
12. **会变红的既有测试与夹具清单（19 项）**及处理方式：**按扩展更新、不削弱；零删除、零放宽**。

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：形态插件声明与插件装配清单（声明 → 装配集合 → 权重键集的一一对应）、
  `evaluators` 段的 schema 形状与取值规则、守卫判定的数据结构（`(path, symbol, line, hit)`）、登记点完备性
  报告结构、接入改动清单产物结构（含基线 ref 与配置指纹）（**由并行任务产出，已与本计划逐条对齐**）
- [contracts/plugin-config.md](contracts/plugin-config.md)（**C1~C4**）：C1 声明段形状与解析（`evaluators.plugins`
  的层级与叶子键 `{impl, version, params}`、**配置声明集 = 可用插件全集**、缺项即报错）；C2 唯一装配点与调用约定
  （`impl(**params, **槽位)`、`importlib` 解析 `module:attr`、注入槽位 opt-in、`evaluator_id` 一致性、保序、
  同键重复注册拒绝、错误类型）；C3 插件目录与"声明才生效"（`core/evaluators/plugins/` 与
  `agents/<agent>/evaluators/` 的归属、**目录不决定可用性**、缺声明即装配期报错、插件业务无关的**文本 + AST
  双层**机检）；C4 `version` 显式校验与"**不允许覆盖实现**"（声明值 == 实例 `spec.version`，不等即报错）
- [contracts/zero-form-branch.md](contracts/zero-form-branch.md)（**C5~C8**）：C5 字面量层扫描面（符号名锚点、
  `core/` + `agents/` 全覆盖含 `agents/pilot`、**例外三条**、ASCII 词边界 + 中文子串判定）；C6 判断分支层扫描面
  + **形态名派生**（`declared_forms(configs_dir)` 的 id 面与 `form_literals(configs_dir)` 的名称面、`form:` +
  别名键 `form_aliases`、零人工常量清单、派生失败即报错）；C7 **三副本委派收敛**（单一实现、副本数 ⇒ 1、
  断言语义保留原位、只增不减）；C8
  `agents/pilot/pilot.py:613` 裸形态词收敛 + `core/deployment/evidence.py:97` 的例外登记（**不得**为裸词加例外）
- [contracts/form-registration.md](contracts/form-registration.md)（**C9~C11**）：C9 五处登记点从"两形态硬编码"改
  "配置/形态派生"的具体改法与逃逸风险（含不新造第六处）；C10 **登记完备**口径（两两唯一 ∧ 配置集合 ⊆ 登记派生集
  ∧ 下界 ≥2；禁止删除；含 `configs/<id>.yaml` 的 stem == `form:` 取值）；C11 020 全部新增口径的声明完备 +
  cadence ∈ `{1,7}` 显式报错 + "不适用"显式声明 + 既有两形态取值零改动
- [contracts/onboarding-ops.md](contracts/onboarding-ops.md)（**C12~C14**）：C12 接入改动清单（基线 ref、与 git
  一致、逐条类别与越界判定、越界 100% 报出、退出码语义）；C13 机制侧总账的登记与"**不得冒充零代码改动**"机检
  （基线取机制落地后；六项总账逐项可枚举；产物 append-only 与可回溯）；C14 CLI 与离线端到端演示（退出码 0/1/2、
  零花费/零网络/零凭证、"未标定"标注机检）
  零凭证、"未标定"标注机检）
- [quickstart.md](quickstart.md)：验证命令（`pytest` 面 + 三个 CLI 子命令 + 演示）+ 接入方流程（写配置 →
  写插件 → 声明 `impl/version/params` → `sync-versions --check` → `registration` → `onboarding --baseline`）+
  验收口径 + 验证记录回填区

**FR → 契约落点对照**（不新增需求，只做覆盖核对；**14/14 全覆盖**）：

| FR | 承载契约 | 说明 |
| --- | --- | --- |
| FR-001 配置驱动的插件装配面：`evaluators.plugins.<plugin_id>.{impl,version,params}`、唯一装配点 `importlib` 解析、缺声明即报错、配置声明集 = 全集、参数经通用通道、装配集合 ↔ 权重键集一一对应 | **C1/C2/C3** | 声明段形状与"全集"语义 → C1；`module:attr` 解析、注入槽位、`params` 为唯一新增参数通道、一一对应与保序 → C2；"声明才生效、目录不决定可用性" → C3 |
| FR-002 插件业务无关（零 `agents.*`/`dreaming.*` import、零环境变量/配置路径/网络、零形态字面量/分支），**文本 + AST 双层**机检 | **C3**（+ C5/C6 扫描面口径） | 插件本体的业务无关纪律 → C3；形态字面量/分支的判定口径 → C5（字面量层）/C6（判断分支层）；AST 层复用 017 先例 `tests/unit/test_dev_core_degraded_purity.py:167-179` 的 import 扫描法 |
| FR-003 "仅新增配置 + 插件"的机检判据：类别判定（`core/evaluators/plugins/` 与 `agents/<agent>/evaluators/` 均属插件类且**必须经 `impl` 声明**）、既有模块修改一律越界、**新形态接入不得改动任何既有 Agent 装配函数** | **C12**（+ C3 目录与声明口径、C2 装配面） | 类别判定而非前缀判定 → C12；"目录不决定可用性" → C3；"既有装配函数零改动"两条腿分别由 C2（机制侧已把六处改成委派、装配集合由声明决定）与 C12（接入侧改动集为 0）举证 |
| FR-004 接入改动清单机检：基线 ref（机制落地后、接入前）、完整改动集、逐条类别/越界、既有模块修改数恒 0、越界即非 0 退出码 + 逐条点名、与 git 一致 | **C12/C13** | 清单机制 → C12；"机制侧总账不计入接入改动" → C13 |
| FR-005 扫描面补面（字面量 + 判断分支两层均覆盖 `core/` + `agents/` 含 `agents/pilot`）、锚点以符号名、既有断言只增不减、例外只三条、`agents/pilot/pilot.py:613` 收敛 | **C5/C6/C8** | 字面量层扫描面与锚点/例外三条 → C5；判断分支层 → C6；裸形态词收敛与例外登记 → C8 |
| FR-006 零字面量 + 零判断分支两层常驻、形态名由配置派生（零人工常量、副本数 1、派生失败即报错）、"恰好两份"升级为登记完备 | **C6/C7/C10** | 形态名派生（id 面 / 名称面）→ C6；三副本委派收敛（副本数 1、只增不减）→ C7；登记完备口径 → C10 |
| FR-007 走既有五处登记点、不新造第六处、缺项即报错、提供登记完备性机检 | **C9/C10** | 五处登记点改配置/形态派生（含逐一机检与不新造第六处）→ C9；登记完备三条件与下界 → C10 |
| FR-008 020 全部新增口径逐项声明（cadence ∈ `{1,7}` 且超出显式报错 / 窗口口径与生效日 / 渠道命名空间 / 归属日生效日 / 迁移六键 / 运行窗口下限与断档容差）、"不适用"显式声明、既有两形态取值零改动 | **C11** | `config_completeness`（`agents/pilot/pilot.py:377`）为唯一收口点；cadence 另在 `core/calibration/config.py` 收口 |
| FR-009 形态插件契约测试常驻（业务无关双层 / 一一对应 / 同键重复注册被拒 / 非确定性被拒 / 必需元数据缺失即报错 / 参数只来自 `params` / 只能经 `impl` 生效） | **C1/C2/C3/C4** | 声明形状与解析 → C1；装配与调用约定（一一对应、参数只来自 `params`）→ C2；目录与"只能经 `impl` 生效" → C3；`version` 显式校验 → C4；注册侧断言沿用 `core/evaluators/registry.py:29-33`/`:35-38` 与 `core/evaluators/base.py:42` 的既有语义 |
| FR-010 至少一条**离线端到端**证据（配置 → 装配 → 评估 → `eval_breakdown` 带 `id@version` → 合成分数 → 留痕/报告，退出码 0）、零花费/零网络/零凭证、不得触发真实投放/生成 | **C14** | 演示脚本 `ops/demo_form_plugin.py`；最小形态不声明 judge ⇒ 结构性零花费 |
| FR-011 诚实边界机检：业务定义未给定期间按最小可行形态接入 + 如实标注"未标定"、不得发明、不做真实投放/真实素材生成/多租户 | **C14** | "未标定"标注在配置（段 `note` + `rehearsal.status`）与产物（`uncalibrated` 字段）两处机读可见 |
| FR-012 复用而不新造：复用五处登记点、注册中心、`weights`/`composite` 配置驱动口径、cadence 口径、渠道命名空间、`config_completeness` 收口点；不引入第三方插件框架或新运行时依赖；不旁路门禁；**唯一装配点** | **C2/C1**（+ C9 不新造第六处） | "唯一装配点是 `impl` 的唯一解析点" → C2；声明面复用（不新增注册表配置）→ C1；"复用清单" → C9 的登记点表；零新增依赖 → 复杂度跟踪表 |
| FR-013 机制侧总账与接入改动分离并留痕（六项总账逐项可枚举）、**不得**把机制改动写成"零代码改动"、既有两形态 `eval_breakdown`/得分逐字节不变、既有断言只增不减 | **C13**（+ C7 守卫改动只增不减、C9 登记点改动只增不减） | 六项总账与"不得冒充零代码改动" → C13；两形态 `evaluators` 段逐字相同与装配序列不变 → C4（`version`/`evaluator_id` 一致性校验）+ C13（对照举证） |
| FR-014 CLI 与产物（接入改动清单 / 插件与登记点校验 / 离线端到端演示）、退出码语义与既有工具一致、产物可机检/append-only/可回溯到基线 ref 与形态配置指纹 | **C14**（+ C12 CLI 退出码） | CLI 四子命令形状 → C14；清单产物的回溯字段 → C12 |

**实现阶段编号（阶段 2~9，A 侧与 B 侧分开编号）**：**A 机制侧** = 阶段 2（A1 插件声明面与唯一装配点）、
阶段 3（A2 扫描面补面与形态名派生）、阶段 4（A3 五处登记点派生与登记完备）、阶段 5（A4 020 口径完备与 cadence
收口）、阶段 6（A5 接入改动清单与 CLI）；**B 接入侧** = 阶段 7（B1 广告形态接入）、阶段 8（B2 漫剧形态接入）、
阶段 9（B3 离线端到端、登记与门禁同步、交付留痕）。**A 是机制侧总账（本特性的代码改动主体），B 才是"仅新增
配置 + 插件"的举正面；两者在变更说明里必须分开陈述**（FR-013）。

## 阶段 A1（实现阶段 2）：插件声明面与唯一装配点（FR-001/FR-002/FR-012 的机制主体）

**TDD 序**：先写 `tests/unit/test_evaluator_plugin_assembly.py`（新：声明形状、`impl` 解析、参数注入严格性、
`evaluator_id`/`version` 一致性、一一对应与保序、缺声明即报错、目录不决定可用性）与
`tests/contract/test_plugin_contracts.py`（新：契约测试 C1~C4 的可执行面），使它们**因目标行为缺失而失败**，再实现。

1. **新增 `core/evaluators/plugin.py`（唯一装配点，业务无关，签名定名）**：
   `parse_manifest(document, agent, *, slots) -> PluginManifest`（读 `evaluators.plugins.<agent>`；**`slots` = 业务侧
   `SLOT_LAYOUT`**——槽位名不在 `core` 里硬编码，故本模块保持**零 Agent 名、零形态名**；声明段形状与叶子键以
   `contracts/plugin-config.md` C1 为准）；`assemble(manifest, *, agent_config, gateway=None, artifacts=None,
   registry=None) -> dict[str, list[Evaluator]]`：`importlib` 解析 `impl`（`module:attr`）→ 校验 callable →
   **按签名**决定传入哪些注入槽位 → `impl(**params, **槽位)` → 校验 `spec.evaluator_id` == 声明键、
   `spec.version` == 声明值（**C4：不允许以声明覆盖实现**）、`EvaluatorSpec`（`core/evaluators/base.py:42`）与注册
   校验（`core/evaluators/registry.py:18`/`:29-33`/`:35-38`，仅在提供 `registry` 时注册）→ **按声明顺序**返回槽位映射。
   **零形态名/零 Agent 名/零分支**（C2 的调用约定 + C3 的"目录不决定可用性"）。
   `slots` 的**唯一映射声明** `SLOT_LAYOUT` 落**业务侧** `agents/<agent>/evaluators/plugins.py`（定名与落点以
   `contracts/plugin-config.md` C2 为准；与 `agents/pilot/stages.py:126-134` 的 `STAGE_CONFIG_SECTION` 同款
   "单一映射声明"风格），并与该 Agent 装配函数的返回槽位一一对齐（`SLOT_LAYOUT[agent]` == 返回键集 − `{"all"}`；
   `sound` ⇒ `("all",)`，`all` 由装配点按该顺序拼接）。
2. **错误类型**：`core/evaluators/errors.py` 在 `EvaluatorError`（`:8`）之下新增"声明/装配期"错误
   （缺声明、`impl` 不可解析、`params` 键集不符、`evaluator_id`/`version` 不一致、集合与权重键集不匹配）。
3. **六个 Agent 绑定工厂**（**新文件** `agents/<agent>/evaluators/plugins.py`）：一评估器一函数，纯关键字签名。
   既有参数从 `agent_config` 槽位读（单一事实源）；judge 类工厂额外声明 `gateway` 槽位；visual 的 judge 工厂
   额外声明 `artifacts` 槽位（`_judge_anchor_hashes` 从 `agents/visual/loop.py:103` **整体迁入**该新文件，
   语义逐字保持，`agents/visual/loop.py:138` 与 `:222` 的调用点同步）。**既有评估器实现文件一律不碰**
   （research.md 决策 4）。
4. **六个装配函数改委派**（函数体替换，**签名与返回形状不变**）：
   - `agents/visual/loop.py:207` → `{"compliance": [...], "proxies": [...], "judge": [...], "all": [...]}`；
   - `agents/dev/evaluators/__init__.py:31`（保留 `registry` 关键字符参）→ `{"gates", "proxies", "all"}`；
   - `agents/screenplay/evaluators/__init__.py:40` → `{"gates", "proxies", "judge", "all"}`；
   - `agents/storyboard/evaluators/__init__.py:26` → `{"gates", "alignment", "judge", "all"}`；
   - `agents/sound/evaluators/__init__.py:24` → **扁平列表**（保序，`tests/unit/test_sound_composite.py:129` 按下标取用）；
   - `agents/editing/evaluators/__init__.py:27` → `{"gates", "pacing", "judge", "all"}`。
   **一一对应校验**沿用各函数的既有中文文案与语义（`agents/dev/evaluators/__init__.py:52-58`、
   `agents/screenplay/evaluators/__init__.py:64-71`），新增形态下同样生效。
5. **两份既有配置新增 `evaluators` 段**，且两形态**逐字相同**（口径澄清 B）：为全部既有评估器写
   `impl`（指向 A1 第 3 步的新工厂）/ `version`（= 实现产出的 `spec.version`，由 `sync-versions` 生成后人工核对）/
   `params: {}`。**既有取值零改动**（含 `configs/movie.yaml:620` 的 7 与 `configs/shortdrama.yaml:658` 的 14）。
6. **对照机检（前置，必须先落地）**：导出改造前后的两形态装配序列 `[(slot, id@version), ...]`（含顺序）并断言
   **逐字相同**；同时断言 `movie["evaluators"] == shortdrama["evaluators"]`（保护既有差异集断言不被动）。
7. **夹具同步**（research.md 决策 12 第 14 项，本阶段最大风险）：为所有**内联配置字典**构造的夹具补
   `evaluators` 段（`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、
   `tests/unbiasedness/*`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py` 等）。
   **禁止**在实现里加"缺段即回落到硬编码装配"的兜底（那会留下影子装配路径，违反 FR-012）。

## 阶段 A2：扫描面补面、形态名派生、三副本收敛与裸词收敛（FR-005/FR-006 的机制主体）

**TDD 序**：先写 `tests/unit/test_form_guard.py`（新：派生面、两层扫描、符号名锚点、例外三条的判定、有牙齿自检），
使"注入即红"三类反例**先失败**，再实现。

1. **新增 `ops/form_guard.py`（单一实现，业务无关）**：`declared_forms(configs_dir)`（**id 面**：读全部
   `configs/*.yaml` 的 `form:` 取值；**两两唯一**、非空字符串；缺键/类型错/重复 ⇒ **报错**；决策 7 的
   `declared_forms() ⊆ registered_forms()` **只在 id 面上成立**）、`form_literals(configs_dir)`（**名称面** = id 面
   ∪ 全部 `form_aliases` 项，专供字面量扫描）、`form_branch_patterns()`、`iter_sources(roots=("core", "agents"))`
   （**含 `agents/pilot`**）、
   `literal_violations()` / `branch_violations()`（返回 `(相对路径, 所属符号名, 行号, 命中内容)`——**符号名由 AST
   求所属 `FunctionDef`/`ClassDef`**，行号仅辅助）、`classify_exception(hit)`（例外三条）。
   **配置命名纪律**：`configs/<id>.yaml` 的**文件名 stem 必须等于**该文件的 `form:` 取值（登记点⑤ 的
   `pilot_form_config_path(form)` 要能零人工常量反查唯一配置路径；stem 与 `form:` 不一致即报错）。
   判定口径：ASCII 形态名按**词边界**、中文别名按**子串**（research.md 决策 6 的理由与先例）。
2. **三副本委派收敛**：`tests/unit/test_form_switch.py:413`/`:414`、`tests/unit/test_billing_core_purity.py:34-35`、
   `tests/unit/test_dev_core_degraded_purity.py:28-29` 的形态名常量改为从 `ops/form_guard.py` 取；
   **循环体与断言体原位保留**；`tests/unit/test_form_switch.py:423` 的 `if "pilot" not in path.parts` **删去**
   （补面方向是变严）；`tests/contract/test_pilot_contracts.py:469-477` 的写死元组改派生（该处**本就覆盖 pilot**，
   与补面后口径必然一致）。
3. **`agents/pilot/pilot.py:613` 的裸形态词收敛**：改中性措辞（去掉形态名，改为"形态原值：
   `screenplay.target_duration_min × 60 == editing.target_duration_s`"），保留"任一不一致即拒绝启动并点名两处
   实测值"的既有语义；**不**为它开例外。
4. **例外三条的机检边界落地**（E1 配置路径字面量 / E2 `tests/**` 不在扫描面 / E3 docstring 中性描述——只列举
   不绑定取值）；现有命中处理：`core/deployment/evidence.py:97` 走 **E1**；`ops/` / `web/` / `dreaming/` 的既有
   配置路径默认值与演示形态值**不在扫描面内、也不改写**。
5. **有牙齿证据**：`tests/unit/test_form_guard.py` 用**派生值**合成三类反例并逐条断言被判违规（含"注入
   `agents/pilot/backends.py` 即红"这条 SC-003 的举证）。合成字面量必须由派生值构造 ⇒ 守卫模块与测试文件
   自身**零人工形态常量**。

## 阶段 A3：五处登记点改配置/形态派生 + 登记完备口径（FR-006③/FR-007 的机制主体）

**TDD 序**：先写 `tests/unit/test_form_registration.py`（新：五处逐一判定、缺项点名、不新造第六处、登记完备
三条、故意越界取证），再改五处。

1. **① `tests/unit/test_form_switch.py`**：模块级 `FORMS`（`:30`）与 `_pair` 改由 `declared_forms()` 派生；
   **保留** `:341-379` 的固定差异集断言（口径澄清 B 保证它不变）；**新增**"逐形态对"常驻断言（每对差异集
   **非空**、必含 `form`、必不含 `web` / `cost_regression`）。
2. **② `tests/unit/test_config_integrity.py`**：`SHORTDRAMA`/`MOVIE` 两常量（`:19-20`）与参数化面（`:153-154`）
   改由派生面驱动（每份 `configs/*.yaml` 都跑**全部加载器**与**全部"缺项即红"条目**）；`CONFIG_CLASSES`（`:23-40`）
   新增 `evaluators` 段的清单解析器条目；`REQUIRED_PATHS`（`:48-98`）新增插件声明面的必需键条目
   （`impl` / `version` / `params` 与槽位、`<evaluator_id>` 键与 `evaluator_weights` 对应关系）。
3. **③ `tests/contract/test_pilot_contracts.py`**：差异集断言（`:434-453`）**一字不改**（口径澄清 B）+ 新增逐对
   断言；`:469-477` 的扫描改共用实现（同 A2 第 2 步）。
4. **④ `agents/pilot/pilot.py:377`**：**保住通用性**（按配置路径通用），**追加** `form_clause_completeness()`（同模块
   新函数，由 `config_completeness` 收口调用）：cadence ∈ `{1,7}`、`window_semantics` 取值域单元素、
   `window_semantics_change_date` 非空、`budget.channels.<id>.tiers` 非空且档位不跨渠道串用、
   `promo.attribution_date_required_since` 存在、`calibration.transfer` 六键齐备、**"不适用"必须显式声明**
   （留空/省略即报错）；**并把 `evaluators` 段的清单解析器加进预检清单**（缺段即拒绝启动 ⇒ "漏声明插件清单"
   不得静默逃逸）。返回段清单随之变长（`tests/unit/test_pilot_chain_seven.py:118-121` 按扩展更新）。
5. **⑤ `tests/conftest.py`**：`PILOT_FORMS`（`:2854`）改派生；`pilot_form_config_path`（`:3054-3079`）对**任意已
   声明形态**返回该形态**真实配置的派生副本**（保留 `assert "root: billing" in source` 派生点断言与
   `:3072` 对**未声明**形态的报错）；反查依赖**配置命名纪律**——`configs/<id>.yaml` 的 stem == `form:` 取值
   （见 A2 第 1 步），故零人工常量即可定位唯一配置路径；`_MINIMAL_MOVIE_CONFIG`（`:3196`）保留其"精简副本"职责。
6. **"恰好两份"→登记完备**（决策 7）：`tests/unit/test_form_switch.py:438-441` 改三条并列（两两唯一 ∧
   配置集合 ⊆ 登记派生集 ∧ 下界 ≥2），**禁止删除**。
7. **不新造第六处**：`ops/form_onboarding.py` 落地 `REGISTRATION_SITES`（五处常驻白名单：`tests/unit/test_form_switch.py`
   的差异集与 `FORMS`、`tests/unit/test_config_integrity.py`、`tests/contract/test_pilot_contracts.py`、
   `agents/pilot/pilot.py` 的预检、`tests/conftest.py` 夹具）+ **反向扫描**"形态清单被写死"的代码点，
   断言其集合 == 白名单面（新增一处即红）。
8. **同族"两形态枚举"副本逐处改派生**：`tests/unit/test_billing_core_purity.py:31`、
   `tests/unit/test_billing_channels.py:52`、`tests/contract/test_billing_contracts.py:97`、
   `tests/unit/test_pilot_rehearsal.py:34`；断言体不删，形态特定假设改"逐形态声明期望值"。

## 阶段 A4：020 口径声明完备与 cadence 显式报错（FR-008 的机制主体）

**TDD 序**：先写 `tests/unit/test_form_clause_completeness.py`（新：逐项缺失即报错、`{1,7}` 之外即报错、
"不适用"留空即报错、既有两形态取值零改动），再实现。

1. **cadence 收口**：`core/calibration/config.py` 的 `CalibrationConfig.from_dict` 增一道校验（取值域取自
   `core/calibration/periods.py:30`，**该文件一字不改**），错误文案点名取值域；与既有
   `core/calibration/drift_config.py:130-143`、`agents/promo/config.py:43` 同源同口径（同取值域，不新造量纲）。
2. **"不适用"的显式声明面**：机检读**原始文档**（不经模型），要求 `calibration.transfer` 的
   `source_forms`/`target_forms` 按真实适用性声明（020 的模型要求非空列表，`core/calibration/config.py:82-93`），
   若某形态仅声明自身 ⇒ **必须**在该段给出非空的"不适用 + 理由"说明（键名以 `contracts/form-registration.md` C11
   为权威）；`budget.runs.min_window_days` / `gap_tolerance_days` 同理（缺失或留空即报错）。
3. **迁移口径联动**（`core/calibration/transfer.py:456` 的 `_registered_ids` 取 `evaluator_weights`、
   `:497-505` 对未声明形态显式拒绝、`:289-298` 的 `evaluator_registered` 要求目标形态登记同 id 同 version）
   ⇒ 新形态必须同步声明这两处，否则**空声明 = 沉默失效**（由 A4 的机检挡住）。
4. **既有两形态取值零改动**：`configs/movie.yaml:620` 的 7 与 `configs/shortdrama.yaml:658` 的 14 **逐字节不变**
   （常驻断言：读原文件比对，改动即红）。

## 阶段 A5：接入改动清单与 CLI（FR-003/FR-004/FR-014 的机制主体）

**TDD 序**：先写 `tests/unit/test_form_onboarding.py`（新：git 派生一致率、类别判定、故意越界 100% 报出、
append-only、配置指纹、基线取错时的行为），再实现。

1. **`ops/form_onboarding.py`**：`changed_files(baseline_ref)` 由 `git diff --name-status <ref>` **派生**（含工作区
   改动）；`classify(path, status)` 按**类别**判定（配置 / 插件 / 测试与文档 / 越界，表见 research.md 决策 9）；
   `build_manifest(config_path, baseline_ref)` 产出完整清单（逐条路径 + 类别 + 越界标记 + 计数）；`write_manifest(...)`
   append-only 落盘（文件名含形态 id 与序号 + `index.jsonl` 追加行，含基线 ref 与配置指纹
   `core/orchestration/models.py:36` 的 `fingerprint_of`）；越界 ⇒ 返回码 1 并**逐条点名**（不得只报总数）。
2. **`ops/form_plugin.py`（CLI 门面，薄转发）**：`guard`（跑两层扫描并打印 `(path, symbol, line, hit)`）、
   `registration`（五处逐一判定 + 登记完备三条 + 不新造第六处）、`onboarding --baseline <ref> --config <路径>
   --out <目录>`、`sync-versions --check|--write --config <路径>`；退出码与既有工具一致（0 / 1 / 2，先例
   `ops/transfer.py` 的退出码文档块与 `ops/billing.py` 的用法面）。**只读 git 与文件、只写 `--out`**（不触碰工作区、
   不 `git add`/`commit`）。
3. **契约测试**：`tests/contract/test_form_onboarding_contracts.py`（新：C12/C13 的可执行面——基线语义、
   一致率、越界判定、append-only 与回溯字段）。
4. **机制侧总账的分离举证**：在变更说明与 quickstart 里逐项列出 FR-013 的六项总账（本特性 A1~A5 的产物），
   并写明"**此后**新形态接入才真的仅新增配置 + 插件"——**不得**把 A 写成"零代码改动"。

## 阶段 B1：广告形态接入（FR-003/FR-010/FR-011 的举正面之一）

**TDD 序**：先写 `tests/integration/test_new_form_onboarding_offline.py`（新：装配、评估、合成分数、`eval_breakdown`
带 `id@version`、退出码 0、零花费/零网络/零凭证、清单越界为空），再写配置与插件。

1. **新增 `configs/<ad-form>.yaml`**：段集合与既有两形态一致（含新 `evaluators` 段）；逐项声明 020 全部口径
   （cadence ∈ `{1,7}` + 近似关系如实登记；窗口口径与生效日；渠道命名空间 `budget.channels.<id>.tiers`；
   归属日生效日；迁移六键 + "不适用"显式说明；运行窗口下限与断档容差）；`pilot.rehearsal.status: unstandardized`；
   承载业务数字的段带非空 `note`（含"未标定"字样）；**不声明 judge**（结构性零 LLM 花费）。
2. **插件**：评估器组合**尽量复用**既有 Agent 绑定工厂的 `impl`（这就是"两形态共用同一份插件代码"的举正面）；
   本形态特有的通用件落 **`core/evaluators/plugins/`**（新目录），并**必须**在该配置里经 `impl` 声明才生效
   （目录不决定可用性）。插件实现：零形态字面量/分支、零 `agents.*`/`dreaming.*` import（**C3** 插件本体的
   业务无关机检 + **C5/C6** 扫描面口径）、参数全来自 `params`、携带 `cost_per_call` 与 `version`。
3. **演示**：`ops/demo_form_plugin.py`——临时目录派生新形态配置（账本根落 tmp，先例 `tests/conftest.py:3054-3079`）
   → `config_completeness` → 装配全部声明（含注册中心注册）→ 对合成功件跑一次评估 → `composite_score_versioned`
   （`core/evaluators/composite.py:37`）→ 落一个带 `eval_breakdown`（键为 `id@version`）的节点与一份运行记录
   → 打印清单与登记点结果 → **退出码 0**；真实花费 0 / 外部网络 0 / 凭证读取 0（演示内断言）。
4. **接入改动清单**：以 A5 落地的机制 ref 为基线跑 `onboarding`，断言 `violations == []`（**既有模块被修改的
   文件数恒为 0**）；清单落盘（append-only + 配置指纹）。

## 阶段 B2：漫剧形态接入（第二个举正面：证明机制可复用、插件代码共用）

1. **新增第二份形态配置**，口径与标注要求同 B1（**不得**把 B1 的形态配置复制后只改名字——业务侧未标定的部分
   同样如实标注，且**不得**发明数字）。
2. **声明复用同一批插件**：至少一条 `impl` 与 B1（或既有两形态）**完全相同**的声明 ⇒ 直接举证"同一插件被两个
   形态配置声明、两形态共用同一份插件代码、形态差异只在配置值"（US1 场景 3）；同 id 同 version 在**同一注册
   中心**内重复注册即被拒（`core/evaluators/registry.py:35-38`）由契约测试守住。
3. **接入改动清单**：同样以机制 ref 为基线，断言越界为空；清单落盘。
4. **两形态并跑**：一条用例跑完 B1+B2 两形态（各自装配 → 评估 → 合成分数），断言两形态**共用插件实例类型**
   而得分/明细差异**全部来自配置值**。

## 阶段 B3：离线端到端、登记与门禁同步、交付留痕

1. **离线端到端证据**（FR-010/SC-008）：`ops/demo_form_plugin.py` 对 B1（与 B2）形态各跑一次，退出码 0；
   零真实花费、零外部网络、零凭证三项由演示内计数断言 + 用例再断言一遍。
2. **登记点同步（逐处，缺一即逃逸）**：五处按 A3 的派生面自动纳入新形态 ⇒ 本阶段只需**验证**（`registration`
   子命令逐处判定已登记 + 登记完备三条 + 无第六处），并把"新形态的取值差异登记"补进 A3 新增的逐对断言面。
3. **产物与留痕**：接入改动清单 + 守卫报告 + 登记报告 + 演示输出落 `--out`（append-only；含基线 ref 与形态
   配置指纹）；quickstart 回填区登记实测结论（含"未标定"口径与开放问题 1/2 的上缴）。
4. **门禁同步**：覆盖率口径不降（≥85%，含 web）；对抗 / 无偏性 / Immutable 审计 / 成本回归四条常驻门禁零放松；
   **若**演示构造了 `LLMGateway`，则把 `ops/demo_form_plugin.py` 登记进 `OFFLINE_ASSEMBLIES`
   （`tests/unit/test_billing_core_purity.py:268-285`）并同步 `:359` 的计数（按扩展更新）；**推荐**路径是最小形态
   不声明 judge ⇒ **不构造网关**，该测试**不动**。

## 复杂度跟踪（新增抽象论证）

| 新增抽象 | 为什么需要 | 为什么不选更简方案 |
| --- | --- | --- |
| 新模块 `core/evaluators/plugin.py`（唯一装配点） | FR-001/FR-012 要求"配置声明是唯一注册面"且"唯一装配点是 `impl` 的唯一解析点"；装配逻辑被**六个**装配函数共用，必须是唯一口径 | *在六个装配函数里各写一份 `importlib` 解析*：六份口径必然漂移，且"唯一装配点"这条 FR 直接失效（第六个形态接入时会出现六种解析行为）；*放进 `core/evaluators/registry.py`*：注册中心是"已实例化对象的唯一命名空间"（键 = `id@version`），把"声明解析/实例化"混进去会让"注册"与"构造"两个纪律纠缠，且 `registry.py` 是 001 交付的冻结模块 |
| 新目录 `core/evaluators/plugins/` | 裁决 2 定名"业务无关的通用评估器"落点；B 阶段新形态需要业务无关的通用插件 | *让所有插件落 `agents/<agent>/evaluators/`*：通用件（与任何 Agent 无关）无处安放，且会诱发"为了放进去而假装它与某 Agent 相关"；*新增顶层目录插件/插件库*：违反 Monorepo 边界（新增目录必须先归 `core` 或 `agents`，`.specify/memory/constitution.md:165-166`） |
| 6 个 `agents/<agent>/evaluators/plugins.py`（薄工厂，约 30 个函数） | 既有评估器的**既有参数不搬迁**（单一事实源）且**实现文件零改动**（版本哈希）⇒ 需要一个**新文件**承担"从 Agent 配置对象取参数并构造既有评估器"的适配 | *把既有参数拷进 `params`*：双事实源（同阈值两处，`tests/unit/test_form_switch.py:166-169` 读 dataclass 路径、装配读 params 路径 ⇒ 漂移即假绿）；*改既有评估器构造签名*：会改实现文件字节 ⇒ 改版本 ⇒ 改 `eval_breakdown`（违反 FR-013，research.md 决策 4）；*`{from_config: <路径>}` 参考 DSL*：多一套解析规则，且对象型参数（`agents/dev/evaluators/__init__.py:47` 的 `SimulatedSignalSource`）仍需工厂 ⇒ 两套机制并存 |
| 新顶层配置段 `evaluators` | 原则五要求形态差异是"**新增配置项**而非新增分支代码"（`.specify/memory/constitution.md:121`）；插件声明必须有权威载面 | *扩展 `evaluator_weights` 的值形状*：会让权重读取（`core/evaluators/weights.py:18`）被迫放宽取值域（数值或 `gate`），削弱既有门禁；*另建独立配置文件*：多一份权威面，且"配置声明集 = 全集"要在两份文件间求交 |
| 新模块 `ops/form_guard.py`（形态名派生 + 两层扫描，单一实现） | 裁决 3/4 要求"形态名一律由配置派生、禁止人工常量、副本数 ⇒ 1"，且 CLI 与测试要用**同一实现** | *保留三份副本*：副本数 ⇒ 1 是 SC-003 的机检项，历史已证明副本必漂移（020 登记过引用漂移，`specs/020-shortdrama-real-feedback/tasks.md:692`）；*放 `core/`*：形态概念属配置面，`core/` 必须业务无关；*放 `tests/conftest.py`*：`ops/` 不能依赖 `tests/` |
| 新模块 `ops/form_onboarding.py` + CLI `ops/form_plugin.py` | FR-004/FR-014 要求"接入改动清单"可机检、append-only、可回溯，且退出码语义与既有工具一致 | *写成一个测试*：无法作为交付物被接入方使用（FR-014 明确要 CLI 与产物）；*把清单塞进 `ops/pilot.py`*：`pilot.py` 是链路装配面，塞入 git 审计会让"运行链路"与"仓库审计"混淆（且 `ops/pilot.py` 已有 `--form` 透传语义） |
| 新演示 `ops/demo_form_plugin.py` | FR-010 要求"至少一条离线端到端证据"，且必须可被独立复跑 | *把演示做成用例*：用例是门禁、不是交付物；*复用既有 `ops/demo_*_loop.py`*：那些演示绑定既有两形态与既有评估器组合，改它们会触碰既有断言面（且"新形态从配置跑到跑通"要有独立入口） |

## 风险与回滚

| 风险 | 等级 | 缓解 / 回滚 |
| --- | --- | --- |
| **改到既有评估器实现文件 ⇒ 版本哈希变化 ⇒ `eval_breakdown` 变（FR-013 失守）** | **高** | 硬性前置：机制改动**只**落新文件与装配函数体（阶段 A1 的结构决策 ④）；落地时先做"改造前后装配序列逐字相同"的对照机检；回滚 = 六个装配函数恢复原函数体（新文件与配置段可保留不用，`evaluators` 段不影响任何既有路径） |
| **内联配置字典夹具大面积变红 ⇒ 有人用"实现兜底"绕过（留下影子装配路径）** | **高** | 阶段 A1 第 7 步把这批夹具**显式列清单**并逐处补齐；契约测试常驻断言"缺 `evaluators` 段即装配期报错"（任何兜底都会让该断言红）；回滚不适用（这是必须做对的一条） |
| **扫描面补面后 `core/` + `agents/` 出现新的假阳性** | 中 | 判定口径按"ASCII 词边界 + 中文子串"（先例 `tests/unit/test_billing_core_purity.py:54-56`）；假阳性的处置是**中性化措辞**，**不得**加例外（例外只三条，加例外须走契修订流程）；回滚 = 逐条中性化（不回退扫描面） |
| **五处登记点派生化改动触碰既有断言（尤其 `tests/unit/test_pilot_rehearsal.py:34` 的形态特定假设）** | 中 | 处理原则只有一条："按扩展更新、**不削弱**"（research.md 决策 12 逐项结论）；形态特定期望值改为"逐形态声明/登记"，**不得**把新形态从派生面排除、**不得**删断言；回滚 = 逐处回退该文件（机制面不动） |
| **cadence 校验收口后既有夹具里出现 `{1,7}` 之外的值 ⇒ 变红** | 低 | 已核实：既有测试与夹具的 `period_days` 取值只有 `1` 与 `7`（`tests/unit/test_calibration_config.py:51`、`tests/unit/test_drift_cadence.py:102`）；若新增用例需要越界值 ⇒ 用临时文件注入（不回退校验） |
| **接入改动清单基线取错 ⇒ 判据自相矛盾**（把机制侧总账算成越界） | 中 | 契约 C13 写明基线的定义与取证流程（机制提交 → 接入提交 → 以机制 ref 为基线跑）；CLI 在清单头部落 `baseline_ref`，评审可复核；用例断言"机制 ref 为基线时越界为 0" |
| **新形态的最小可行形态被误读为"已达标的形态"** | 中 | 三层"未标定"标注（`pilot.rehearsal.status: unstandardized` + 段 `note` + 产物 `uncalibrated_reason`）常驻机检；产物与文档一律写"机制已就绪 / 业务定义未标定"；"以模拟冒充标定"的次数恒为 0（机检） |
| **广告/漫剧真实节律确需 `{1,7}` 之外量纲** | 中（业务侧，不可在本特性内消除） | 本特性**不扩量纲**（裁决 6）：在 `{1,7}` 内取最接近一档并**如实登记近似关系**；真实需求记入开放问题 2，由业务侧另立特性扩展 `core/calibration/periods.py:30` 的取值域（本特性不代劳、也不得含糊兜底） |
| **两形态的 `evaluators` 段出现分叉（例如有人给短剧态单独加了插件）⇒ 既有差异集断言变红** | 中 | 常驻机检"两形态 `evaluators` 段逐字相等"；若真需要按形态声明插件组合，则该形态应新增配置（B 路由）而不是改动既有两形态的段——**不得**为过断言而改断言 |

## 与既有特性的兼容性

| 既有特性 | 兼容面 | 本特性的处理 |
| --- | --- | --- |
| **015-pilot-shortdrama** | 双形态同链运行、形态差异逐项归因、零形态分支静态断言（`tests/unit/test_form_switch.py` 整节） | **兼容性扩展**：与既有两形态的 `evaluators` 段逐字相同 ⇒ `test_全量差异都被配置文件承载`（`:341-379`）**一字不改**；三副本**委派**收敛（断言保留原位、语义只增不减）；"恰好两份"按裁决 5 升级为登记完备（**禁止删除**） |
| **018-feature-film-pipeline** | `pilot` 段、`config_completeness` 预检、六处装配点的一致性（`agents/pilot/stages.py:126-134` 的单一映射声明） | 六个装配函数**签名与返回形状不变** ⇒ `agents/pilot/backends.py` 与 `agents/pilot/stages.py:237-244` 的装配调用零改动；`config_completeness`（`agents/pilot/pilot.py:377`）**保住通用性**并追加 020 口径机检（返回段清单变长 ⇒ 按扩展更新其用例） |
| **017-dev-agent-degraded** | `core/degraded/` 纯净性机检的 AST import 扫描先例（`tests/unit/test_dev_core_degraded_purity.py:167-179`）、其形态字面量副本（`:28-29`） | 复用其 **AST 双层机检法**作为插件业务无关的机检手段（FR-002）；其形态字面量副本**委派**到单一实现（断言体原位保留） |
| **019-real-channel-billing** | `budget.channels` 渠道命名空间、`SpendGuard`、账本与对账；`tests/unit/test_billing_core_purity.py` 的 `FORMS` 与门禁注入普查 | 新形态**必须**声明 `budget.channels.<id>.tiers`（FR-008 ③），沿用 019/020 的渠道口径（不新造）；`FORMS` 改派生（断言体不删）；**若**演示构造 `LLMGateway` ⇒ 登记进 `OFFLINE_ASSEMBLIES` 并同步计数（按扩展）；**不**为插件新造旁路门禁 |
| **020-shortdrama-real-feedback** | 五处登记点、cadence `{1,7}`（`core/calibration/periods.py:30`）、窗口口径、归属日生效日、迁移六键、`budget.runs` 窗口下限 | **复用不新造**（FR-012）：五处登记点三处改**派生**（不新造第六处）；cadence 取值域**一字不改**，只在加载入口补一道同源校验；迁移六键按真实适用性声明 + "不适用"显式说明；既有两形态取值（`configs/movie.yaml:620` 的 7 / `configs/shortdrama.yaml:658` 的 14）**逐字节不变** |
| **014-deployment / 016-* / 012-drift** | `deployment.spot_check.pending_alert_days`（形态差异键）、`llm.profiles`、`calibration.drift` | 本特性不改这些段；`core/deployment/evidence.py:97` 的配置路径字面量走**例外 E1**（**不得**为过断言改写它）；`drift.period_days` 的既有校验（`core/calibration/drift_config.py:130-143`）与本特性新增的 cadence 收口**同源同取值域** |
| **001-tree-evaluators / 010-weekly-calibration** | 注册中心、`EvaluatorSpec`、权重/合成读取面、版本冻结口径 | 插件声明**必须**与实现产出的 `spec.version` 逐字一致（不覆盖、不回落）；注册仍走唯一入口；`weights.py:18` 与 `composite.py:37` 的配置驱动口径**原样复用**，不改取值域 |

## 本特性不做什么（写进计划与规格防回潮）

- **不把机制改动写成"零代码改动"**（FR-013 末句、`specs/021-form-plugin-validation/spec.md:124`）：A1~A5 是本
  特性的**代码改动主体**，只有 B1/B2 的接入是"仅新增配置 + 插件"；
- **不做 cadence 量纲扩展**：`{1,7}` 之外显式报错；真实节律需求记入开放问题 2，另立特性（裁决 6）；
- **不做真实投放与真实素材生成**：B/C 路径不变（`docs/三期立项书.md:212` 的 `not_delivered` 口径不放宽）；
- **不做多租户、不做公网服务化**（三期不做项不变，`specs/020-shortdrama-real-feedback/spec.md:167` 同一口径）；
- **不发明广告/漫剧的业务定义**：受众、指标口径、素材规格、评估器组合的业务正确性、平台名、预算数字一律
  由业务侧给定；未给定期间按最小可行形态接入并如实标注"未标定"；
- **不做 promo 构造点的插件化**：规格只点名**六个**装配点（`specs/021-form-plugin-validation/spec.md:93`）；
  `agents/promo/loop.py:127-128`、`agents/promo/anchors.py:83`、`agents/promo/ingest.py:72` 的构造点**不在**本特性
  改动面内（其中 `human.platform_metrics` 是校准锚点、本就不在装配集合内）；但**零形态字面量/分支守卫覆盖
  `agents/promo/`**（扫描面 = `core/` + `agents/` 全覆盖）；
- **不新造第六处登记点、不另造门禁、不引入第三方插件框架或新运行时依赖**（FR-012）；
- **不改既有评估器实现文件**（版本哈希，research.md 决策 4）；**不改既有两形态的既有取值**（FR-008 末句）；
- **不为过断言改写 `ops/`/`web/`/`dreaming/` 的既有配置路径默认值**（它们不在扫描面内，规格边界情况明文禁止）；
- **不削弱任何既有断言**：本特性对既有测试的改动只有三种——**委派**（换常量来源）、**扩展**（新增条目）、
  **改口径**（"恰好两份"→ 登记完备，且下界保留）；**零删除、零放宽**。

## 宪章复核（阶段 1 后）

**原则五**由三件事共同落实：① 形态差异的新增面是 `evaluators` 配置段（"新增配置项"而非"新增分支代码"，
`.specify/memory/constitution.md:121`）；② 唯一装配点 `core/evaluators/plugin.py` **业务无关**（只认声明 →
callable → 参数注入，零形态名/零 Agent 名/零分支），Agent 绑定薄工厂落 `agents/` 并由 `impl` 声明生效
（目录不决定可用性）；③ 新形态接入**不得**改动任何既有 Agent 装配函数（A1 已把六处改成委派 ⇒ 接入侧改动集为 0，
由接入改动清单机检举证）。**原则一/二**由"声明 `version` == 实现 `spec.version`"的一致性校验、注册链原样生效、
以及"既有两形态装配序列改造前后逐字相同 + `eval_breakdown`/得分零改动"承载（C2/C4 + C13）。**原则六**由三处
可独立复核的机检物承载：接入改动清单（逐条路径与越界判定）、零分支两层断言（含注入即红的有牙齿自检）、
登记与 020 口径完备性（缺项即拒绝启动）；局限**逐条如实标注**（业务定义未标定、cadence 近似关系、
真实回流/真实投放不在本特性）。**原则三**不受影响（零真实渠道调用；若演示构造网关则按其既有门禁纪律显式登记）。
**原则四不适用**（无新增策略执行面）。

**门禁通过**；三项前提——(a) "既有两形态装配序列逐字不变"的对照机检必须**先于**装配函数改造落地；
(b) 三副本收敛必须以"**委派**、断言语义保留原位"完成，**不得**借收敛之名删除或削弱任一处断言；
(c) `params` 的严格性规则（签名无默认参数集 == `params` 键集 ∪ 注入槽位集）与"缺 `evaluators` 段即装配期
报错"必须常驻，**不得**以"实现兜底"换取夹具全绿。三项分别由 **C2**/**C4**（唯一装配点与调用约定、`version` 一致性校验）、**C7**（三副本委派收敛与只增不减）、**C3**（插件目录与"声明才生效"、参数严格性）承载。
