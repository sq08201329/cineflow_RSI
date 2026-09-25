# 契约：五处登记点的配置/形态派生与 020 口径声明完备（C9~C11）

> 对应规格 FR-006③ / FR-007 / FR-008、SC-006 / SC-007、US1 场景 5、US2 场景 3~4、US3 场景 3~4、
> 边界情况「五处登记点的"逃逸门禁"风险（新形态今天要么静默逃逸、要么硬失败）」「既有"形态以配置文件
> 为唯一载体"的断言与新形态冲突」「迁移口径的形态集合必须按该形态真实适用性声明，且与"登记面"联动」
> 「"不适用"必须显式声明而非留空」「cadence 取值域不允许发明第三档量纲」；裁决 4 / 5 / 6。
> 依据：`research.md` 决策 7 / 8 / 10、`plan.md` 阶段 A3 / A4 与"口径澄清 B"。
>
> **实现面（真实文件）**：五处登记点 = `tests/unit/test_form_switch.py`、`tests/unit/test_config_integrity.py`、
> `tests/contract/test_pilot_contracts.py`、`agents/pilot/pilot.py`、`tests/conftest.py`；
> 派生实现 = `ops/form_guard.py`（形态名派生，**单一实现**）与 `ops/form_onboarding.py`（登记点白名单与完备性）；
> cadence 收口 = `core/calibration/config.py`；配置面 = `configs/movie.yaml` / `configs/shortdrama.yaml` /
> `configs/<new-form>.yaml`（新形态）。
>
> **性质**：对 020 既有资产的**兼容性扩展**——不是新造第六处登记点、不是第二套校验、不引入第三方插件框架。
> **零删断言、零放宽、零新造登记点**；既有两形态（`movie` / `shortdrama`）的**既有取值零改动**
> （`configs/movie.yaml:620` 的 `7` 与 `configs/shortdrama.yaml:658` 的 `14` 逐字节不变）。
> 插件声明面的**层级名与叶子键拼写以 `contracts/plugin-config.md` C1 为权威**（C1~C4 = 声明形状 /
> 唯一装配点 / 通用参数通道 / 版本冻结）；**零形态分支守卫面逐条以 `contracts/zero-form-branch.md` 为权威、
> 且四条各自成号（不得合并引用）**——**C5** = 字面量层扫描面（含**例外三条**）、
> **C6** = 判断分支层扫描面 + **形态名派生**、**C7** = **三副本委派收敛**、
> **C8** = `agents/pilot/pilot.py:613` 裸形态词收敛 + `core/deployment/evidence.py:97` 的 E1 例外登记；
> 清单产物与 CLI 面以 `contracts/onboarding-ops.md` C12~C14 为权威——**本文件不复述它们的键清单，只引用编号**。
>
> **锚点口径（本契约的判断项）**：本文一律以**符号名**（函数名 / 类名 / 常量名）锚定，行号**只作定位辅助**。
> 020 登记扫描面时用的行号**已漂移**（`specs/020-shortdrama-real-feedback/tasks.md:424`、`:692`）——
> 本契约**不以行号承载语义**。
>
> **编号映射（与 `plan.md` 阶段 1 的一致；本文件的区间不变）**：`plan.md` 阶段 1 与本文件的落点**逐项一致**——
> **C9** = 五处登记点改配置/形态派生（含逐一机检、缺项点名、**不新造第六处**）；
> **C10** = **登记完备**口径（两两唯一 ∧ 配置集合 ⊆ 登记派生集 ∧ 下界 ≥2）；
> **C11** = 020 口径声明完备与 cadence 收口。本文件的组织方式是把"五处登记点**逐处**：
> 今天形状 → 改成什么 → 机检断言 → 反例"写在 **C9**（裁决 5、`research.md` 决策 7、裁决 6 的口径落在 C10/C11）。
> **本文件只占 C9~C11**，不占用 `contracts/plugin-config.md` 的 **C1 / C2 / C3 / C4**，也不占用
> `contracts/zero-form-branch.md` 的 **C5 / C6 / C7 / C8**（四条各自成号：C5 = 字面量层扫描面与例外三条 /
> C6 = 判断分支层与形态名派生 / C7 = 三副本委派收敛 / C8 = 裸词收敛与 E1 例外登记——见本文头"守卫面"段）。
> **跨文件编号协调（已对齐，父代理 2026-09-25 复核）**：`contracts/zero-form-branch.md` 的 C7 兼容规则
> 现已指向本文件的 **C10**（与本文落点一致，无第二套判定、无残留歧义）。评审与实现按**本质**定位：
> 登记完备 = 本文件 C10；五处登记点派生改法 = 本文件 C9；020 口径与 cadence = 本文件 C11。
>
> **派生面口径（不得混用，定名以 `contracts/zero-form-branch.md` C6 为权威）**：
> `declared_forms(configs_dir)` 是**登记面 / id 面**（全部 `configs/*.yaml` 的 `form:` 取值，**两两唯一**）；
> `form_literals(configs_dir)` 才是**名称面**（id 面 ∪ 全部 `form_aliases` 项，含中文别名）。
> **本文件引用 `declared_forms()` 时一律指 id 面**——别名键 `form_aliases`（每份形态配置**必须存在**，
> 可为显式空列表）只进扫描面、**不进**登记面（否则登记完备的 `⊆` 比较会被别名打破）。

---

## C9 五处登记点从"两形态硬编码"改"配置/形态派生"

**目的**：把"新形态必须走既有全部五处登记点"从**叙述**变成**可机检的逐一判定**。今天这五处里有三处是
**两形态硬编码枚举**（①②③），一处是**硬失败**（⑤ 对未知形态直接报错），只有一处（④）天然通用——
⇒ 新形态接入时**要么静默逃逸、要么硬失败**，两种情形都是门禁失效。本契约把 ①②③⑤ 改成
**由 `configs/*.yaml` 的 `form:` 派生**（形态名派生面 = `ops/form_guard.py` 的 `declared_forms()`，
其定名与派生规则以 `contracts/zero-form-branch.md` C6 为权威），④ 保住通用性并加 020 口径逐项机检，
**不新造第六处**（FR-007 / FR-012）。

**形状·接口（五处逐处）**：

| # | 登记点（符号名锚点） | 今天形状 | 改成什么 | 缺项即报错的形态 |
| --- | --- | --- | --- | --- |
| ① | `tests/unit/test_form_switch.py`：模块级 `FORMS`（`:30`）、`Test差异逐项可归因._pair`（`:144`）、`Test差异逐项可归因.test_全量差异都被配置文件承载`（`:341-379`）、`Test零形态分支静态断言.BANNED_LITERALS` / `BANNED_PATTERNS`（`:413` / `:414`）、`test_core_与_agents_无形态字面量`（`:421`，排除行 `:423`）、`test_形态切换只经配置文件`（`:438`，断言 `:441`） | `FORMS = ("movie", "shortdrama")` 硬编码；扫描面**显式排除 `agents/pilot`**；`configs == ["movie.yaml", "shortdrama.yaml"]` **恰好两份** | `FORMS` 与 `_pair` 改由 `declared_forms()` 驱动；`:423` 的排除**删去**（补面方向是**变严**）；禁用清单**委派**到 `form_literals()` / `form_branch_patterns()`；`:341-379` 的固定差异集**原位一字不改**；**新增**"逐形态对"断言；`:441` 按 C10 升级为**登记完备**口径 | 新形态在 `FORMS` 里**缺席**（用例不覆盖它）；或该处退回字面量元组（委派证明失败） |
| ② | `tests/unit/test_config_integrity.py`：`SHORTDRAMA` / `MOVIE`（`:19` / `:20`）、`CONFIG_CLASSES`（`:23-40`）、`REQUIRED_PATHS`（`:48-98`）、`Test形态标识与段完整性.test_形态标识为短剧`（`:118`）、`test_与电影配置段集合一致`（`:122-126`）、`Test全部配置类加载器` 的参数化（`:133`）、`test_缺项即红` 的参数化（`:153-154`） | 配置集合只有 `SHORTDRAMA` / `MOVIE` 两份；参数化面写死这两份 | 配置集合改由 `declared_forms()` 驱动（**每份** `configs/*.yaml` 都跑**全部加载器**与**全部"缺项即红"条目**）；`SHORTDRAMA` / `MOVIE` 两常量**保留**（既有符号不删，作为"对照面"）；`CONFIG_CLASSES` **新增** `evaluators` 段清单解析器条目；`REQUIRED_PATHS` **新增**插件声明面的必需键与"`<evaluator_id>` 键集 == `evaluator_weights.<agent>` 键集"条目；`:122-126` 原位保留 + **新增**"全部形态段集合一致" | 新形态配置**不被任何加载器覆盖**（静默逃逸）；或新增加载器/必需键条目漏登 |
| ③ | `tests/contract/test_pilot_contracts.py`：`TestC10到C13试水运行.test_c13_两套配置差异可归因且无形态分支`（`:418`，差异集断言 `:434-453`，全量扫描 `:468-477`，写死禁用元组 `:474`） | 固定两形态 + 固定 **15** 键差异集；扫描用**写死的禁用元组** | 差异集断言 `:434-453` **一字不改**（见"口径澄清 B"）；`:468-477` 的禁用元组改由 `form_literals()` / `form_branch_patterns()` 派生（该处扫描面**本就覆盖 `agents/pilot`**）；**新增**逐对形态断言（同 ①） | 两处扫描面口径**分叉**（该处用派生面、`test_form_switch.py` 用字面量）；或差异集被改成弱断言 |
| ④ | `agents/pilot/pilot.py`：`config_completeness`（`:377`，调用点 `:507`）、新增同模块函数 `form_clause_completeness`、`_require_duration_consistency` 的 docstring（`:613`） | `config_completeness` **按配置路径通用**（对新形态无需改代码即生效，是本特性最靠得住的一处），但只逐段跑加载器 + 七个环节权重键；`:613` 有**裸形态词** `（movie ⇒ 5400 s）` | **保住通用性**；**追加** `form_clause_completeness(config_path)` 并由 `config_completeness` 收口调用（C11 的 020 口径逐项机检，读**原始文档**不经模型）；**并把 `evaluators` 段清单解析器加进预检清单**（缺段即拒绝启动）；`:613` 的裸形态词**收敛**为中性措辞（保留"任一不一致即拒绝启动并点名两处实测值"的既有语义），**不得**为它加例外 | 新形态缺任一 020 口径键却**照常启动**（取码内默认）；或"漏声明插件清单"静默逃逸；或为 `:613` 开例外 |
| ⑤ | `tests/conftest.py`：`PILOT_FORMS`（`:2854`）、`pilot_form_config_path`（`:3054` 装饰器 / `:3055` 函数，`movie` 分支用 `_MINIMAL_MOVIE_CONFIG`（`:3196`），未知形态 `:3072` 直接 `raise ValueError`，派生点断言 `:3073`） | 写死两形态；`movie` 用精简副本、`shortdrama` 用真实配置派生副本；未知形态**硬失败** | `PILOT_FORMS` 改由 `declared_forms()` 派生；`pilot_form_config_path(form)` 对**任意已声明形态**返回**该形态真实配置的派生副本**（只改 `budget.ledger.root`；`:3073` 的派生点断言保留）；`movie` 分支**继续**用 `_MINIMAL_MOVIE_CONFIG`（其"精简副本"职责保留——它是形态**无关性**的举证面，不属形态枚举）；对**未声明**形态仍报错（`:3072` 行为保留：派生面之外的形态就是未知形态） | 新形态**硬失败**（`ValueError`）而无法运行任何双形态用例；或为了让新形态过而返回 movie 的精简副本（**假绿**，比变红危险得多） |

**口径澄清 B（两形态的 `evaluators` 段逐字相同，因此既有差异集断言一字不改）**：`movie` / `shortdrama` 的
评估器组合相同（既有断言 `tests/unit/test_form_switch.py:161` 的 `set(movie_w) == set(short_w)`），且既有评估器的
参数**不搬迁**（声明面 `params` 为 `{}`，单一事实源在各自 Agent 的 config）⇒ 两形态新增的 `evaluators` 段
**逐字相同** ⇒ 顶层差异集（`tests/unit/test_form_switch.py:341-379`、`tests/contract/test_pilot_contracts.py:434-453`）
**不进入差异集**、**原样保留**。本结论**常驻机检**（新增形态无关断言：`movie["evaluators"] == shortdrama["evaluators"]`）。

**逐处机检断言与反例（五处各自成条，缺一即该处没有牙齿）**：

- **① 机检断言**：`FORMS` 的取值集合 **== `declared_forms()`**（双向）；`FORMS` 的赋值表达式是**调用**
  而非字面量元组（AST 判定）；扫描集合**包含** `agents/pilot/backends.py` 且该文件被注入派生字面量时**变红**；
  `:341-379` 的固定差异集断言**仍在且一字未改**（按符号名定位同一 `FunctionDef`）；`:438` 的用例**仍存在**且含
  C10 的三条并列断言；禁用面 == `form_literals()` ∪ `form_branch_patterns()`。
  **① 反例**：保留 `:423` 的排除；把 `:341-379` 弱化为"差异集非空"；删掉 `:441`；把 `FORMS` 写成第三份常量表。
- **② 机检断言**：`test_缺项即红` 的配置参数化面 **== 全部 `configs/*.yaml` 的 stem 集合**（逐份都跑）；
  `Test全部配置类加载器` 的参数化面含 `declared_forms()` 的每个取值；`CONFIG_CLASSES` 含 `evaluators` 段的清单
  解析器条目；`REQUIRED_PATHS` 含插件声明面的必需键与"`<evaluator_id>` 键集 == `evaluator_weights.<agent>` 键集"条目；
  `:118` 与 `:122-126` 的原位断言**仍存在**。
  **② 反例**：把新形态写成第三份模块常量（`AD = …`）；新形态只加进 `SHORTDRAMA` 常量而派生面未动；
  为让新形态过而删 `REQUIRED_PATHS` 任一既有条目。
- **③ 机检断言**：`:434-453` 的固定 **15** 键集合**逐字未改**（**实测纠错**：两处差异集各 **15** 键
  ——`tests/unit/test_form_switch.py:341-379` 与 `tests/contract/test_pilot_contracts.py:434-453` 的字面集合
  **逐字相同**，键为 `form` / `evaluator_weights` / `replay` / `promo` / `visual` / `sound` / `editing` /
  `storyboard` / `screenplay` / `dev` / `pilot` / `calibration` / `dreaming` / `deployment` / `budget`；
  此前文档里的键数表述偏大，已按 `yaml.safe_load` 实测更正）；`:468-477` 的禁用面 == `form_literals()` ∪
  `form_branch_patterns()`（与 ① 的禁用面**逐字相等**）；新增的逐对形态断言存在；`:469` 的
  `for root in ("core", "agents")` 全覆盖循环**仍在**（含 `agents/pilot`）。
  **③ 反例**：把固定集合换成派生集合（削弱 015 的证据）；把扫描面缩回 `core/` + `agents/`（排除 pilot）；
  用两套不同的禁用清单让该处与 ① 分叉。
- **④ 机检断言**：`config_completeness(<新形态真实配置>)` 不抛错，返回项**含** `form_clauses` 与 `evaluators`
  两项、且既有项（`dev`/`pilot`/`screenplay`/`storyboard`/`visual`/`sound`/`editing`/`promo`/`pooling`/`dreaming`/
  `calibration`/`transfer`/`drift`/`deployment`/`budget`/`web` 与 `weights:<段>`）**一个不少**；
  逐项删掉一个 020 口径键 ⇒ `PrecheckError` 且**点名段与键**；`:613` 在扫描面内**零命中**。
  **④ 反例**：给 `config_completeness` 加"缺段即回落到码内默认"的兜底；按形态名分支决定要不要检查；
  为 `:613` 加 docstring 例外；把 020 口径机检另立成第二个 CLI。
- **⑤ 机检断言**：`PILOT_FORMS` 的取值集合 **== `declared_forms()`**（双向）且其赋值表达式是**调用**（AST）；
  对 `declared_forms()` 的每个取值，`pilot_form_config_path(form)` 返回的副本与
  `configs/<form>.yaml` 逐字相同（**仅** `budget.ledger.root` 一行不同）；`:3072` 对派生面之外的取值
  `pytest.raises(ValueError)`；`movie` 分支仍返回 `_MINIMAL_MOVIE_CONFIG`。
  **前提断言（常驻，缺它则本处的"零人工常量反查"不成立）**：逐份断言
  **`configs/<id>.yaml` 的 stem == 该配置的 `form:` 取值**（定名以 `contracts/zero-form-branch.md` C6 为权威）
  ⇒ 形态 id 可**唯一**映射回配置路径；不一致即报错。
  **⑤ 反例**：让 `pilot_form_config_path` 对新形态返回 movie 的精简副本；删掉 `:3072` 的报错（静默兜底）；
  保留两形态元组（新形态硬失败）；把新形态配置命名成与 `form:` 取值不同的 stem（反查不到唯一路径）。

**逐处改法的落地顺序（TDD 序，先写测试使其因目标行为缺失而失败）**：① 的"逐形态对"断言 → ② 的派生化与
新增条目 → ③ 的委派 → ④ 的 `form_clause_completeness` → ⑤ 的派生。**每一处的断言体与循环体都保留原位**，
只换常量来源（**委派**），并**新增**更严的断言；**不得**以删除/放宽换取通过。

### C9.6 同族"两形态枚举"副本的逐处处置（连带面，**不是**第六处登记点）

规格边界情况第 7 条末点名了同族副本，逐处改为 `declared_forms()` 派生、**断言体不删**：

| 位置（符号名锚点） | 今天形状 | 处置 |
| --- | --- | --- |
| `tests/unit/test_billing_core_purity.py` 的 `FORMS`（`:31`）与 `FORM_LITERALS` / `FORM_PATTERNS`（`:34`） | 写死两形态 + 人工常量表 | **委派**到 `declared_forms()` / `form_literals()` / `form_branch_patterns()`；`:145` 的 `test_无形态字面量与形态分支` 与 `:210-224` 的"有牙齿"自检**原位保留** |
| `tests/unit/test_billing_channels.py` 的 `FORMS`（`:52`） | 写死两形态 | **委派**；断言体不删 |
| `tests/contract/test_billing_contracts.py` 的 `FORMS`（`:97`） | 写死两形态 | **委派**；断言体不删 |
| `tests/unit/test_pilot_rehearsal.py` 的 `FORMS`（`:34`） | 写死两形态（次序与它处不同） | **委派**；用例里的**形态特定取值假设**（排练档与形态原值对照）按裁决写死为"**期望值入配置 + 断言读配置**"：期望值落进该形态的配置（`configs/<form>.yaml` 的既有段/键，或 `pilot` 段的既有声明），断言**从配置读出**后与该形态的实际产出比较——**否决**第二条路径"建'形态 → 期望'的显式登记表"（形态→配置/期望的映射表与 C6 的"零人工常量"、C7/C9.7 的"副本数 ⇒ 1、禁止形态映射"**直接冲突**）；**不得**删除断言、**不得**把新形态从派生面排除 |
| `tests/unit/test_dev_core_degraded_purity.py` 的 `FORM_LITERALS` / `FORM_PATTERNS`（`:28`） | 人工常量表（第三份副本） | **委派**；`:101` 的断言体与 `:167-179` 的 AST `import` 扫描**原位保留** |

### C9.7 "不新造第六处"的机检

`ops/form_onboarding.py` 落地 `REGISTRATION_SITES`——**常驻白名单，恰好五处**，每项含
`{path, symbol, kind}`：`kind = "form_set"`（①③⑤ 与 ② 的配置参数化面）或 `kind = "clause_list"`（④）。
机检两条：

1. **反向扫描**：扫全仓的"形态清单被枚举/写死"代码点——即**字面量元组/列表**形态的形态集合常量、
   "形态 → 配置路径"映射、或 `configs/{form}.yaml` 硬编码拼装的模块级常量；断言其**集合 ==
   `REGISTRATION_SITES` 的 `form_set` 面**（新增一处即红，须显式登记）。
2. **白名单常驻**：`len(REGISTRATION_SITES) == 5` 且其 `path` 集合 == 上表五处的 `path` 集合；
   新增第六处（哪怕只是再加一个测试文件的形态元组）⇒ 红。
   纪律与 `tests/unit/test_billing_core_purity.py:268-285` 的 `OFFLINE_ASSEMBLIES` 常驻清单同款。

### 机检断言（C9）

- **逐处登记率 100%**：对 `declared_forms()` 的**每一个**取值，五处**逐一**判定已登记：
  - ①③ 与 ②的参数化面、⑤：该处形态集合取值 **== `declared_forms()`**（`⊆` 与 `⊇` 双向）；
  - ④：`config_completeness(<该形态的真实配置>)` 不抛错，且其返回项覆盖 C11 表逐项。
- **委派证明 100%**：每处登记点的形态集合表达式**不得**是字面量元组/列表（AST 判定：`ast.Tuple`/`ast.List`
  的元素全为 `ast.Constant` ⇒ 红），**必须是调用表达式**（指向 `declared_forms()` 等派生面）。
  该条是"新形态静默逃逸"的**根因守卫**：只要有一处退回字面量，新形态就在那处缺席。
- **缺项点名**：任一登记点未覆盖某形态 ⇒ 报错并**点名是哪一处、缺哪个形态**（不得只报总数）。
- **补面举证（SC-003）**：`test_core_与_agents_无形态字面量`（`tests/unit/test_form_switch.py:421`）的扫描集合
  **包含** `agents/pilot/backends.py`；把**由派生值构造**的形态字面量注入该文件 ⇒ 该断言**变红**
  （"注入即红率 100%"，证明断言有牙齿不是空跑）。
- **只增不减（逐条对号，守卫面四条各自成号）**：`tests/unit/test_form_switch.py:421-429` 的**字面量层**扫描
  （⇒ `contracts/zero-form-branch.md` **C5**）、`:431-436` 的**判断分支层**扫描（⇒ **C6**）、
  `tests/unit/test_billing_core_purity.py` / `tests/unit/test_dev_core_degraded_purity.py` 两处副本的
  **断言全部保留原位**（三副本委派收敛 ⇒ **C7**）；断言"形态名常量表**副本数恒为 1**"（三副本已委派收敛）。
- **两处扫描面口径一致**：③（`tests/contract/test_pilot_contracts.py:468-477`）与 ①
  （`tests/unit/test_form_switch.py:421` / `:431`）的禁用面**逐字相等**（消除"两处扫描面口径分叉"这一既有隐患）。
- `agents/pilot/pilot.py:613` 的裸形态词在扫描面内**零命中**（收敛率 100%，⇒ **C8**）；为其加例外的次数恒 0。

### 反例（C9）

1. 保留 `tests/unit/test_form_switch.py:423` 的 `if "pilot" not in path.parts` ⇒ 装配点仍在盲区，红。
2. 把 `:341-379` 的固定 **15** 键差异集换成"差异集非空"一类弱断言 ⇒ 丢失 015 的核心证据，红。
3. 删掉 `:441` 的"恰好两份"断言以求新形态通过 ⇒ 红（C10 明文禁止删除）。
4. 在 `tests/conftest.py` 重新写死 `("movie", "shortdrama")` ⇒ ⑤ 对新形态硬失败 ⇒ 红（委派证明）。
5. 让 `pilot_form_config_path` 对新形态返回 `_MINIMAL_MOVIE_CONFIG` ⇒ 用例在**错误前提**下通过（假绿），红。
6. 为新形态新造第六处登记点（例如新增一份"形态注册表"文件或再加一个测试文件的形态元组）⇒ 红。
7. 把 `:613` 的裸形态词留在 docstring 里并给它加 docstring 例外 ⇒ 红（裁决 4 明文禁止）。
8. 为让 020 口径机检通过而在实现里"缺段即回落到码内默认" ⇒ 红（缺项即报错的口径被反转）。

### 兼容规则（C9，对 015 / 018 / 019 / 020 零回改）

- 五处登记点是 020 建立、本特性**复用不新造**的资产：本契约只把三处**改派生**、一处**保通用性并加机检**、
  一处**改派生**，**不新增第六处**、不另造门禁。
- 015 的差异集证据（`:341-379` 与 `:434-453`）**一字不改**；既有断言按"**只增不减**"更新——
  本特性对既有测试的改动只有三种：**委派**（换常量来源）、**扩展**（新增条目）、**改口径**
  （"恰好两份"→ 登记完备且下界保留）；**零删除、零放宽**。
- 018 的 `config_completeness` **通用性保住**（新形态无需改代码即生效）；其返回段清单**变长**属
  **扩展**——`tests/unit/test_pilot_chain_seven.py:88` 的 `Test清单同步不变量`（`:117`）按扩展更新，
  既有项**一个不少**。
- ①③⑤ 的行号引用在文件变动后会漂移 ⇒ 机检一律按**符号名**定位（本契约的判断项），行号只作人读辅助。
- 既有两形态配置的**既有取值零改动**：`configs/movie.yaml:620` 的 `7`、`configs/shortdrama.yaml:658` 的 `14`、
  `configs/movie.yaml:621` / `configs/shortdrama.yaml:659` 的 `gap_tolerance_days: 0` 逐字节不变（常驻断言）。

---

## C10 "登记完备"口径（三条件 + 下界）

**目的**：把 `tests/unit/test_form_switch.py` 的 `test_形态切换只经配置文件`（`:438`）从"**configs 目录恰好两份**"
（`:441` 的 `assert configs == ["movie.yaml", "shortdrama.yaml"]`）升级为"**登记完备**"口径——原断言的**原意**
（"形态以配置文件为唯一载体、代码侧无形态枚举/映射表"）**完整保留**，只是把"数量形状"换成"结构形状"，
使新增形态不再**必红**、而"漏登记"仍**必红**（裁决 5；`research.md` 决策 7）。**禁止简单删除该断言。**

**形状·接口（三条件并列，缺一条即红）**：

| # | 条件 | 机检形式 | 判据来源 |
| --- | --- | --- | --- |
| ① | **两两唯一** | `len({<每份 configs/*.yaml 的 form: 取值>}) == len(configs/*.yaml)`（重复即红） | `ops/form_guard.py` 的 `declared_forms()`（`form:` 取值两两唯一、非空字符串；缺 `form:` / 值非字符串 / 重复 ⇒ 派生失败即报错） |
| ② | **配置集合 ⊆ 登记派生集**（**双向断言**） | 对**每一处**登记点：`declared_forms() ⊆ registered_forms(site)` **且** `registered_forms(site) ⊆ declared_forms()`（即集合相等） | `ops/form_onboarding.py` 的 `REGISTRATION_SITES` + `site_registration_status()`（含 C9 的**委派证明**：该处集合表达式必须是调用表达式）。`registered_forms(site)` = 该处的形态取值集合；`contracts/zero-form-branch.md` C6 引用的同名量与此**是同一面**（同一实现、不各写一份） |
| ③ | **下界保留** | `len(configs/*.yaml) >= 2`（**不**提高到 3） | 同 ① |

- **① 与 ② 用哪一面（分述，实现者不得混用）**：三条件里的**全部形态集合比较**（①②）一律用
  **id 面** `declared_forms(configs_dir)`（= 全部 `configs/*.yaml` 的 `form:` 取值，**两两唯一**）；
  **名称面** `form_literals(configs_dir)`（= id 面 ∪ 全部 `form_aliases` 项，含中文别名）**只**用于
  **字面量扫描**（`contracts/zero-form-branch.md` C5 的扫描面输入），**绝不**进入登记完备的比较——
  否则登记完备的 `declared_forms() ⊆ registered_forms()` 会被中文别名打破（别名不是形态 id、也没有
  对应的 `configs/<别名>.yaml`）。一句话：**登记完备 = id 面的事；字面量扫描 = 名称面的事**。
- **文件名与形态 id 一致（①/② 能成立的前提，常驻机检）**：逐份断言
  **`configs/<id>.yaml` 的 stem（文件名去扩展名）== 该配置的 `form:` 取值**（不一致即报错，
  定名与理由以 `contracts/zero-form-branch.md` C6 为权威）。这一条使 `declared_forms()` 的每个取值都能在
  **零人工常量**下反查到**唯一**配置路径 ⇒ 登记点 ⑤ 的 `pilot_form_config_path(form)`（C9 的 ⑤）
  与 ② 的"配置集合 ↔ 派生形态集"之间才有一一对应；缺了它，"派生形态集"就只是名字集合、反查不到配置。
- **②为什么非平凡（必须写清）**：它把**派生面**（来自 `configs/`）与**登记面**（五处登记点的实际取值）绑在一起。
  若某处登记点**退回人工枚举**（例如有人在 `tests/conftest.py` 重新写死两形态元组），则
  `declared_forms()` 含新形态而 `registered_forms(site)` 不含 ⇒ **②立刻变红**。这正是"新形态静默逃逸"的
  结构性守卫。**反向**（`registered_forms(site) ⊆ declared_forms()`）挡住"登记了没有配置的形态"，
  使②不是恒真断言。
- **③为什么必须保留**：否则把 `configs/` 清空（零份配置）也能"通过"，守卫失去牙齿。
  **不得**提高到 3——下界若写 3，就把"机制可用"与"本次接入了几个形态"耦合，违反 FR-013 的
  "机制侧改动与接入侧改动分离"（机制落地时仓库仍只有两份配置）。
- **禁止删除的形式化机检**：按**符号名**断言 `tests/unit/test_form_switch.py` 的
  `Test零形态分支静态断言.test_形态切换只经配置文件` **仍存在**（AST 求该 `FunctionDef`），
  且其函数体内含**三条并列断言**（两两唯一 / 双向集合 / 下界）；"该用例被删除"次数恒 0。
- **有牙齿自检（常驻，不得空跑）**：用例注入"一份临时第三形态配置 + 一处人工枚举"的组合 ⇒ 断言 **②必红**；
  注入"两份临时配置的 `form:` 取值相同" ⇒ 断言 **①必红**；把 `configs/` 指向空目录 ⇒ 断言 **③必红**。

### 机检断言（C10）

- 三条件**逐条**可机检且**逐条**有反例自检（见上"有牙齿自检"）。
- **id 面 / 名称面不混用**：三条件的形态集合比较一律用 `declared_forms()`（id 面）；`form_literals()`
  （名称面）在**本 C10 的任何断言里都不出现**（AST/文本机检：本 C10 对应的用例与实现面引用 `form_literals`
  的次数 == 0）——防"登记完备被别名打破"。
- **文件名与形态 id 一致**：逐份断言 `configs/<id>.yaml` 的 **stem == `form:` 取值**；不一致 ⇒ 红
  （这是 ⑤ 的零人工常量反查与 ② 的一一对应的前提）。
- `test_形态切换只经配置文件` 的**符号存在** + 三条件覆盖（AST 判定，不依赖行号）。
- 下界恒为 **2**（断言该常量/字面量 == 2，写成 3 即红——防"把机制与接入数量耦合"）。
- 该用例**未被删除**的离线证据：`uv run pytest tests/unit/test_form_switch.py -q` 全绿且用例数**不减少**。

### 反例（C10）

1. **保留"恰好两份"**：新增第三份配置必红 ⇒ 等于用守卫阻断本次交付（且会诱使实现者删断言），红。
2. 改为"`>=2` 且代码侧无形态枚举/映射表"：后半句**不可机检**（它只是一句注释），红。
3. 下界提到 **3**：机制面因此带上"必须至少三个形态"的伪约束，红。
4. 改为"配置数 >= 已登记形态数"：两个派生面**同源**（都来自 `configs/`）⇒ 恒真、空断言，红。
5. 把 `test_形态切换只经配置文件` 整个删掉（"反正有反向扫描了"）⇒ 红（明文禁止简单删除）。
6. 只断言①③而省掉②：新形态"静默逃逸"这一**本特性要消灭的东西**不再被机检，红。
7. 用 `form_literals()`（名称面）做登记完备的集合比较 ⇒ 中文别名（无对应 `configs/<别名>.yaml`）
   使 `registered_forms(site) ⊆ declared_forms()` 恒假 ⇒ 红（面混用）。
8. 把新形态配置写成 `configs/<new-form>.yaml` 而 `form:` 取值另起一个名字（或反之）⇒ 文件名与 id 不一致 ⇒ 红
   （⑤ 反查不到唯一配置路径、② 的一一对应断裂）。

### 兼容规则（C10：与 `zero-form-branch.md` 的**逐条编号**分工）

- **守卫面四条各自成号（不得把四件事合并成一个编号引用）**：
  - **C5 承载**：**字面量层扫描面**——符号名锚点、覆盖 `core/` + `agents/` **含 `agents/pilot`**、
    **例外三条**、ASCII 词边界判定与中文子串判定；
  - **C6 承载**：**判断分支层扫描面** + **形态名派生**——`configs/*.yaml` 的 `form:` + 别名键 `form_aliases`；
    `declared_forms` = **id 面**、`form_literals` = **名称面**；`configs/<id>.yaml` 的 stem == `form:` 取值；
  - **C7 承载**：**三副本委派收敛**（单一实现、断言体与原循环体保留原位、副本数 ⇒ 1）；
  - **C8 承载**：`agents/pilot/pilot.py:613` 的**裸形态词收敛** + `core/deployment/evidence.py:97` 的
    **E1 例外登记**。
- **本 C10 承载登记面**：三条件在**五处登记点**上的落法（哪一处、如何判"已登记"、缺项如何点名）。
  两处**共用同一实现**（`ops/form_guard.py` 的 `declared_forms()` / `form_literals()` /
  `form_branch_patterns()`），**不重复定义**、**不各写一份**。
- **与 C7 的编号协调**：`zero-form-branch.md` 的 C7 兼容规则把"登记完备"的机检项指向
  「`contracts/form-registration.md` C9」（其写作时沿用 `plan.md` 阶段 1 的编号假设）；本文件最终落在
  **C10**。**口径完全相同、无第二套判定**——实现与评审按本 C10 定位，不因此产生两条口径。
- 原断言的**原意**（代码侧无形态枚举/映射表）由 ①+②+C9.7 的反向扫描**共同**承载，**强度只增不减**。

---

## C11 020 口径声明完备与 cadence ∈ `{1,7}` 收口

**目的**：把"新形态必须**逐项**声明 020 的全部新增口径"落成可机检的**声明完备性**，并把 cadence 的取值域
收口到唯一入口；**不适用时必须显式声明"不适用"而非留空**（FR-008 / SC-007；裁决 6；`research.md` 决策 10）。

**形状·接口（逐项表：口径键路径 → 权威来源 → 适用性 → 收口点）**：

| # | 口径键路径 | 权威来源（取值域） | 适用性谓词 | 收口点（真实文件·符号名） |
| --- | --- | --- | --- | --- |
| ① | `calibration.period_days` | `core/calibration/periods.py:30` 的 `SUPPORTED_CADENCES = (1, 7)`（`:31` 的 `CADENCE_UNIT`；`:89` 的 `period_label` 与 `:68` 的 `cadence_of` 在 `{1,7}` 上双射） | **恒适用**（**不得**写"不适用"） | `core/calibration/config.py` 的 `CalibrationConfig.from_dict`（`:161`，新增一道 cadence 校验；取值域取自 `core/calibration/periods.py:30`）+ `agents/pilot/pilot.py` 的 `form_clause_completeness` |
| ② | `calibration.window_semantics` | 取值域**单元素** `half_open`（`configs/movie.yaml:397` 注释明写"其它值装配报错"） | **恒适用** | `core/calibration/config.py:210-215`（既有读取点，不改） |
| ③ | `calibration.window_semantics_change_date` | ISO 日期（`YYYY-MM-DD`），非空 | **恒适用** | `core/calibration/config.py:216-231`（既有读取点，不改） |
| ④ | `budget.channels.<id>.tiers`（渠道命名空间） | `specs/020-shortdrama-real-feedback/contracts/channel-budget.md` C11（`adapter` 与档位的定名与取值域） | 该形态**是否登记投放渠道**（投放渠道 = `adapter` 取值 `promo_platform` 的渠道，权威定名见 020 C11）。**不登记** ⇒ **必须**在 `budget.not_applicable` 给出 `channels` 的非空理由 | `core/billing/budget.py` 的 `_parse_channels`（`:1128`；既有校验：缺 `tiers` / 缺 `adapter` / 档内缺三键 ⇒ `BudgetConfigError`） |
| ⑤ | `promo.attribution_date_required_since` | ISO 日期，非空（`configs/movie.yaml:83` / `configs/shortdrama.yaml:83`） | **恒适用**（**不得**写"不适用"） | `agents/promo/config.py` 的 `PromoConfig.from_dict`（`:70`；`:43` 的 cadence 同取值域校验不改） |
| ⑥ | `calibration.transfer.{basis, source_forms, target_forms, conditions, storage, adoption}` **六键** | `contracts/transfer-ops.md` C15 / C17 的取值域（`basis` 与 `adoption` 单元素；`source_forms` / `target_forms` 非空字符串列表；`conditions` 键集 = 判定项清单） | `source_forms ∪ target_forms ⊆ {该形态自身}`（即"既不作为来源、也不作为目标与其它形态互通"）⇒ **必须**在 `calibration.transfer.not_applicable` 给出 `source_forms` / `target_forms` 的非空理由；否则**不得**声明"不适用" | `core/calibration/config.py` 的 `TransferConfig.from_dict`（`:60`）；迁移面的形态集合判定 `core/calibration/transfer.py:497-505`（未声明形态**显式拒绝**）、`:456` 的 `_registered_ids`（取自 `evaluator_weights`）、`:289-298` 的 `_c_evaluator_registered`（要求目标形态登记同 id 同 version）⇒ **空声明 = 沉默失效**，故必须显式声明 |
| ⑦ | `budget.runs.min_window_days` / `budget.runs.gap_tolerance_days` | 非负整数（`min_window_days >= 1`；`gap_tolerance_days` 允许 `0` = 不容断档，`configs/movie.yaml:621` / `configs/shortdrama.yaml:659`） | **恒适用**（缺失或留空即报错） | `core/billing/budget.py` 的 `_parse_runs`（`:1345`） |

**"不适用"的显式声明面（键名权威在**本 C11**；全文只此一处定名）**：

1. **`not_applicable`（段内键，映射）**：键 = **该段内的相对键路径**，值 = **非空字符串理由**，且理由
   **必须含「不适用」二字**（纯空白 / `null` / 空列表 ⇒ 报错；留空/省略 ⇒ 报错）。
2. **只允许出现两处**（闭合取值域，**不新造第三处**）：
   - `calibration.transfer.not_applicable`，相对键取值域 = `{source_forms, target_forms}`；
   - `budget.not_applicable`，相对键取值域 = `{channels}`。
   其它段出现 `not_applicable` ⇒ 报错（防止有人用"不适用"**逃避填值**）。
3. **双向无歧义**：适用性谓词判为"适用"的键 ⇒ 该段**必须**给出取值，且 `not_applicable` 中**不得**出现该键
   （判为适用却声明不适用 ⇒ 报错）；判为"不适用"的键 ⇒ 该段**必须**在 `not_applicable` 中给出非空理由，
   且**不得**再给出该键的取值（防双事实源）。
4. **与 C1 的接口声明（不冲突，如实对齐）**：`not_applicable` 是 `budget` / `calibration.transfer` 段内的
   **标注键**，**不参与**插件清单解析。C1 定义的叶子键恰好三键
   （`evaluators.plugins.<agent>.<slot>.<evaluator_id>` 的 `impl` / `version` / `params`）位于
   `plugins` **子树内**；而 C11 的段级 `note` 是 `evaluators` 段内、`plugins` 子树**之上**的标注键
   （`plugin_declarations` 的"逐字拷贝面"因此**不含**它）⇒ 二者**层级不同、不碰撞**。
   本契约在此**声明该需求**（`evaluators` 段须容许一个段级标注键），但**不复述** C1 的拼写与取值规则。

**cadence 的近似关系登记（键名权威在本 C11）**：

- **`calibration.cadence_note`（字符串）**：当该形态 `pilot.rehearsal.status == "unstandardized"`
  （形态级"未标定"既有取值域，`agents/pilot/pilot.py:53`）时**必填**，且**必须同时含**「近似」与「未标定」
  两处字样，写明"所取档位与业务侧真实节律的**近似关系** + 未标定"。
- **非未标定形态**（既有两形态的 `pilot.rehearsal.status` 均为 `declared`）⇒ 该键可省略；给出时
  **不得**含「近似」（防给已裁决的节律贴近似标签）。
- **与被禁的"按周近似兜底"的区别（必须写清，否则该键会变成兜底通道）**：本键只允许"**落在 `{1,7}` 内**并
  **声明近似关系**"；**禁止**"用整周去代表一个非整周的真实节律并**宣称已达标**"。机检：
  `calibration.cadence_note` **不得**出现「已标定」「已达标」「已投产」三处字样；出现即红。
- **不扩量纲**：`core/calibration/periods.py:30` 的取值域**一字不改**（常驻断言：读原文件比对
  `SUPPORTED_CADENCES == (1, 7)`；改写即红）。真实节律确需其他量纲 ⇒ 属**开放问题 2**（业务侧输入），
  另立特性扩展，本特性**不代劳、也不得含糊兜底**。

**三层"未标定"标注的声明面（复用既有词汇，不发明）**：

| 层 | 落点（键路径 / 符号名） | 机检断言 |
| --- | --- | --- |
| 形态层 | `pilot.rehearsal.status: unstandardized`（取值域 `agents/pilot/pilot.py:53`；既有语义"不覆盖形态原值 + 如实标注未标定"，出现于预检/运行报告） | 新形态该取值 == `unstandardized`；「未标定」结论文案取值域固定（结论文案机检见 C14） |
| 段层 | 承载业务数字的段（`promo` / `budget` / `calibration` / `pilot` / `evaluators`）**必须**带**非空** `note`，且其中**必须**出现「未标定」字样（沿用配置既有 `note` 用法，先例 `configs/movie.yaml:603` 一带） | 五段逐一：`note` 存在 + 去空白后非空 + 含「未标定」；缺任一段 ⇒ 报错并点名段名 |
| 产物层 | 清单与演示产物的固定字段 `uncalibrated: true` + `uncalibrated_reason`（非空，点名"受众 / 指标口径 / 素材规格 / 预算档属业务侧输入，未给定"）——**键名权威在 C14** | 字段存在 + `uncalibrated is True` + `uncalibrated_reason` 非空；"未标定标注缺失"次数恒 0 |

### 机检断言（C11）

- **声明完备率 100%**：七项口径逐项在形态配置中声明；缺任一项 ⇒ `config_completeness`
  （`agents/pilot/pilot.py:377`）**拒绝启动**并**逐条点名**（段名 + 键路径），**不取码内默认**。
- **cadence 越界即显式报错（100%）**：注入 `calibration.period_days: 14`（或 `0` / `2` / `"7"`）⇒
  `CalibrationConfig.from_dict`（`core/calibration/config.py:161`）**报错**、错误文案**点名取值域**
  `(1, 7)`、退出码非 0；**不回落**到日级/周级。既有 `core/calibration/drift_config.py:130-143` 与
  `agents/promo/config.py:43` 两处同取值域校验**保持原样**（同源同口径，不新造量纲）。
- **放宽取值域次数恒 0**：`core/calibration/periods.py:30` 的取值域**未被改写**（读原文件比对断言）。
- **"不适用"显式声明率 100%**：留空/省略次数恒 0；判为适用却在 `not_applicable` 里声明不适用 ⇒ 100% 报错；
  在 `budget` / `calibration.transfer` 之外的段出现 `not_applicable` ⇒ 100% 报错。
- **近似关系登记**：未标定形态（`pilot.rehearsal.status == "unstandardized"`）缺 `calibration.cadence_note`
  或其为空 ⇒ 报错；`cadence_note` 出现「已标定」/「已达标」/「已投产」⇒ 报错。
- **既有两形态取值零改动**：`configs/movie.yaml:620` 的 `7`、`configs/shortdrama.yaml:658` 的 `14`、
  `configs/movie.yaml:621` / `configs/shortdrama.yaml:659` 的 `0` 逐字节不变（常驻断言：读原文件比对，改动即红）。
- **迁移口径不沉默失效**：新形态若未在 `source_forms` / `target_forms` 声明自己而**又没**给"不适用 + 理由"
  ⇒ 报错（否则其结论既不能迁出、也接不进任何目标形态，`core/calibration/transfer.py:497-505`）。
- **`evaluators` 段纳入预检**：`config_completeness` 的返回项含 `evaluators` 段的清单解析器结论；
  删掉该段 ⇒ **拒绝启动**（"漏声明插件清单"不得静默逃逸）。

### 反例（C11）

1. 新形态把 `period_days` 写成 `14`（"双周"）并声称"按周近似 + 如实标注"⇒ 红（裁决 6 明文禁止的含糊兜底）。
2. 为了让新形态过而把 `SUPPORTED_CADENCES` 改成 `(1, 7, 14)` ⇒ 红（放宽取值域；`core/calibration/periods.py:30` 一字不改）。
3. `calibration.transfer.source_forms: [ad]`（仅自身）却不给 `calibration.transfer.not_applicable` 理由 ⇒ 红
   （沉默失效）。
4. 把 `calibration.transfer.not_applicable` 写成 `{source_forms: ""}` 或 `null` ⇒ 红（留空不是声明）。
5. 在 `promo` 段写 `not_applicable: {attribution_date_required_since: "不适用"}` 以求省事 ⇒ 红
   （该键恒适用；`not_applicable` 不允许出现在 `promo`）。
6. 新形态缺 `promo.attribution_date_required_since`（或写空串）却照常启动 ⇒ 红（缺项即报错、不取码内默认）。
7. 承载业务数字的段省略段级 `note`，或 `note` 里不写「未标定」⇒ 红（三层标注缺失）。
8. 把机制可跑通所需的**新数字**写进配置却不标注"未标定"⇒ 红（"以模拟冒充标定"同源禁列）。
9. 为让新形态的预检通过而给 `config_completeness` 加"缺段即回落到码内默认"的兜底 ⇒ 红。

### 兼容规则（C11，对 010 / 012 / 019 / 020 零回改）

- **cadence 取值域**复用 `core/calibration/periods.py:30`（`(1, 7)`）**不新造量纲**：`core/calibration/config.py`
  只**新增一道同源校验**，`periods.py` **一字不改**；既有 `core/calibration/drift_config.py` 与
  `agents/promo/config.py` 两处校验**原样保留**（同一口径的多个检查点，不是三套口径）。
- **020 的键形状与取值域逐字不变**：`calibration.window_semantics` 单元素、`budget.channels.<id>.tiers` 的
  档位三键、`calibration.transfer` 六键、`budget.runs` 两键的**读取点与错误文案**均不改；本契约只**新增**
  "适用性谓词 + 不适用声明面"这一层，不改任何既有键的语义。
- **既有两形态配置的既有取值零改动**；新增键一律为**追加**（不改写既有键的取值）。
- **不新造第六处登记点**（C9.7）、**不另造门禁**、**不引入第三方插件框架或新运行时依赖**
  （`plugin-config.md` C1/C2 承载声明与装配；本契约只引用）。
- **诚实边界**：广告 / 漫剧的真实业务定义（受众 / 指标口径 / 素材规格 / 预算档）属**业务侧输入**——
  未给定期间按**最小可行形态**接入并如实标注"未标定"，**不得发明**业务数字；`calibration.cadence_note`
  只登记**近似关系**，**不得**用它宣称已达标。
