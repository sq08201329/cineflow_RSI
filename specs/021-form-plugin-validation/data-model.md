# 数据模型：形态插件扩展性验证（021-form-plugin-validation）

> **存储分层**：**配置**（`configs/*.yaml` 新增顶层段 `evaluators` + 顶层别名键 `form_aliases`）
> → **派生面**（形态名派生、插件装配清单；纯函数、无落盘、无缓存）
> → **产物面**（接入改动清单 / 守卫报告 / 登记报告 / 离线演示输出；文件 **append-only**）。
> **本特性零 DB 变更、零迁移**（无 DDL、无既有行回改）：发现树、`CostRecord`、`eval_breakdown`、
> 得分**一律不动**（原则一/二）。本特性的"数据"只有三类载体：**配置键**、**运行期派生视图**、
> **文件产物**。
>
> 字段级约定见 [contracts/plugin-config.md](contracts/plugin-config.md)（**C1~C4**）与
> [contracts/zero-form-branch.md](contracts/zero-form-branch.md)（**C5~C8**）；登记点与 020 口径完备性的
> 契约在 `contracts/form-registration.md`（C9~C11，**他人撰写**）、接入改动清单/CLI/离线演示的契约在
> `contracts/onboarding-ops.md`（C12~C14，**他人撰写**）——本文件只登记其**实体面**，
> **不重复**它们的机检断言（020 `data-model.md:291` 的同一分工）。本文件定名的键路径是另两份契约的
> **对齐基准**（拼写一字不改）。

## 现状（本次勘查确认的代码事实，逐条带引用）

- **装配零实现引用、硬编码在六个函数**：全仓 `plugin` / `entry_point` / 评估器侧 `import_module` 命中数为 0；
  装配是逐个 `new` 并喂进 Agent 配置字段——`agents/visual/loop.py:207`（`build_evaluators`）、
  `agents/dev/evaluators/__init__.py:31`（`build_dev_evaluators`，保留 `registry` 关键字符参）、
  `agents/screenplay/evaluators/__init__.py:40`（`build_screenplay_evaluators`，7 评估器）、
  `agents/storyboard/evaluators/__init__.py:26`（`build_storyboard_evaluators`，5）、
  `agents/sound/evaluators/__init__.py:24`（`build_sound_evaluators`，**返回扁平列表**）、
  `agents/editing/evaluators/__init__.py:27`（`build_editing_evaluators`，5）。配置里**只有**
  `evaluator_weights` 的 id → 权重映射（`configs/movie.yaml:6`、`configs/shortdrama.yaml:8`）。
- **参数面硬编码**：参数经各 Agent 自己的配置 dataclass 逐字段读取校验（先例 `agents/visual/config.py:24`
  的 `_require_judge`、`:54` 的 `from_dict` 逐键 `_require`；`agents/sound/evaluators/__init__.py:26-30`
  直接取 `config.loudness` / `config.av_sync_threshold_ms` / `config.asr["cer_cap"]`）⇒ 新评估器的新参数
  今天**必须**改既有 `agents/<agent>/config.py` 与装配函数。
- **形态名是人工常量且三副本**：`tests/unit/test_form_switch.py:413` 的
  `BANNED_LITERALS = ("shortdrama", '"movie"', "'movie'")` 与 `:414` 的 `BANNED_PATTERNS`、
  `tests/unit/test_billing_core_purity.py:31-35` 的 `FORMS` / `FORM_LITERALS` / `FORM_PATTERNS`、
  `tests/unit/test_dev_core_degraded_purity.py:28-29` 的同款两常量 ⇒ 新形态名**天然逃逸**。
- **字面量扫描面显式排除 `agents/pilot`**：`tests/unit/test_form_switch.py:423` 的
  `if "pilot" not in path.parts`，而 `agents/pilot/backends.py` 恰是后端**装配点**
  （`agents/pilot/stages.py:248` 的 `build_backends`，`agents/pilot/backends.py:298` 的渠道反查）；
  判断分支扫描面（`tests/unit/test_form_switch.py:431-436`）才覆盖全部。
- **五处登记点中三处两形态硬编码、一处"恰好两份"**：① `tests/unit/test_form_switch.py:30` 的 `FORMS`
  与 `:341-379` 的固定 **15** 键差异集；② `tests/unit/test_config_integrity.py:19-20` 的 `SHORTDRAMA`/`MOVIE`
  两常量 + `:23-40` 的 `CONFIG_CLASSES` + `:48-98` 的 `REQUIRED_PATHS` + `:153-154` 的参数化面；
  ③ `tests/contract/test_pilot_contracts.py:418-477` 的固定差异集（`:434-453`）与**写死的禁用元组**
  （`:469-477`）；④ `agents/pilot/pilot.py:377` 的 `config_completeness`（**按配置路径通用**，调用点 `:507`）；
  ⑤ `tests/conftest.py:2854` 的 `PILOT_FORMS = ("movie", "shortdrama")` 与 `:3054-3079` 的
  `pilot_form_config_path`（未知形态 `:3072` 直接 `raise ValueError`）。
  另"恰好两份"断言在 `tests/unit/test_form_switch.py:438-441`（`configs == ["movie.yaml", "shortdrama.yaml"]`）
  ⇒ 新增第三份配置**必红**。
- **`core/` + `agents/` 内的既有形态字面量只有两处**：`core/deployment/evidence.py:97` 的
  `source="configs/movie.yaml deployment.gate.require_unbiasedness=false"`（**配置文件路径**字面量）与
  `agents/pilot/pilot.py:613` 的 docstring `（movie ⇒ 5400 s）`（**裸形态词、非路径**）。
- **版本号含实现文件字节**：`agents/sound/evaluators/_versioning.py:12` 的 `implementation_version`
  → `:22-23` 把 `inspect.stack()[2].filename`（＝调用方评估器**实现文件**）的**全部字节** `hasher.update(...)`
  （`agents/dev/evaluators/_versioning.py:13` 同款）⇒ 改一字节实现文件即改版本 ⇒ 改 `eval_breakdown`。
- **`form` 是不受枚举约束的自由字符串**：`core/orchestration/models.py:197` 只校验非空、
  `ops/pilot.py:250` 的 `--form` 只作透传；`core/evaluators/{registry,weights,composite}.py`
  （`:18`/`:59`、`:18`、`:37`）与 `core/evaluators/base.py:42` 的 `EvaluatorSpec` 均为**配置驱动、通用**
  ⇒ 真正的缺口只在**装配面与参数面**。
- **cadence 取值域**：`core/calibration/periods.py:30` 的 `SUPPORTED_CADENCES = (1, 7)`、`:31` 的
  `CADENCE_UNIT`；加载入口 `core/calibration/config.py:149` 的 `CalibrationConfig.period_days` 今天只校验
  "≥1 的整数"，真正拦 cadence 的是 `core/calibration/drift_config.py:130-143` 与 `agents/promo/config.py:43`。

## 领域模型

### 1. 插件声明项（PluginSpec，新增；**配置面**）

**路径形状（全文一致的拼写，供另两份契约对齐）**：

```
evaluators:                                  # 新增顶层段（形态配置）
  plugins:                                   # 声明面唯一入口
    <agent>:                                 # {screenplay, storyboard, visual, sound, editing, dev}
      <slot>:                                # 见实体 3 的槽位表
        <evaluator_id>:                      # 逐字等于 evaluator_weights.<agent> 的键
          impl: "<module>:<attr>"
          version: "<与 spec.version 逐字相等>"
          params: {<参数名>: <形态无关取值>}
```

| 字段 | 类型 | 约束 | 来源 |
| --- | --- | --- | --- |
| `<agent>` | 键 | 取值域 = **六个评估器装配点**的 Agent 名（实体 3 的表）；`promo`/`pilot` **不入**本声明面（`promo` 的构造点不在本特性改动面内、其 `human.platform_metrics` 是校准锚点，见 `plan.md:498-501`） | 配置作者 |
| `<slot>` | 键 | ∈ 该 Agent 的槽位取值域（实体 3）；五个 dict 返回的 Agent 下 **`all` 不可声明** | 配置作者 |
| `<evaluator_id>` | 键 | 非空字符串；**逐字等于** `evaluator_weights.<agent>` 的某个键；**前缀↔kind 一致**（`rule.`→`rule`、`proxy.`→`proxy_model`、`judge.`→`judge`、`human.`→`human`，镜像既有权重键命名法） | 配置作者 |
| `impl` | str | 恰含**一个** `:`；左侧非空点分模块路径，右侧非空点分属性路径（逐段 `getattr`）；**唯一解析点** = `core/evaluators/plugin.py` | 配置作者 |
| `version` | str | **必须显式**（缺键即声明期报错）；**逐字等于**实例产出的 `spec.version`；**不覆盖实现** | 配置作者（由 `ops/form_plugin.py sync-versions --check` 核对） |
| `params` | Mapping[str, Any] | **形态无关**：键名必须是 `impl` 目标签名里的参数名；值可为标量/列表/映射；**装配点不按形态解释任何 params 值** | 配置作者 |

**叶子键恰好三键**（`impl` / `version` / `params`）；出现第四键 ⇒ `PluginDeclarationError`。
**形态无关性**的精确含义（三条，均不含"配置值里不得出现形态名"——形态配置**本来就**承载形态名）：
① 声明面 **schema 不随形态变化**（同一组键、同一解析规则、同一校验顺序）；
② 解析与装配路径**零形态名/零形态分支**（`core/evaluators/plugin.py` 不 import 任何形态概念、不读 `form:`）；
③ 同一份插件声明可被**多份**形态配置声明（两形态共用同一份插件代码，差异只在**其它段的值**）。

**不变量（可机检）**：`<evaluator_id>` 集合（各槽位并集）== `evaluator_weights.<agent>` 键集；
`params` 键集 == `impl` 目标签名中无默认值参数集 **减** 被注入槽位集；`version` == `spec.version`；
`impl` 目标可调用且返回/实例的 `spec.evaluator_id` == `<evaluator_id>`。

### 2. 插件装配清单（PluginManifest，新增；**派生视图**）

- **形状**：`PluginManifest = {agent, slots: ((slot_name, (PluginSpec, ...)), ...)}`——槽位**保序**，
  各槽位内 `<evaluator_id>` **保序**（`yaml.safe_load` 保序 ⇒ **声明顺序即装配顺序**）。
- **产出**：`core/evaluators/plugin.py` 的 `assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None)`。
- **配置声明集 = 可用插件全集**：清单**只**由声明派生；目录扫描/`entry_points`/导入即注册**均不存在**。
- **一一对应**：`assemble` 返回的槽位映射经各装配函数组装后，其 `<evaluator_id>` 并集
  **逐字等于** `evaluator_weights.<agent>` 键集（缺项/多项即拒绝装配；沿用
  `agents/dev/evaluators/__init__.py:52-58` 与 `agents/screenplay/evaluators/__init__.py:64-71` 的既有中文文案口径）。
- **`all` 键的两义（必须分清）**：五个 dict 返回的 Agent，`all` 是**派生汇总键**（= 按该 Agent 的
  `SLOT_LAYOUT` 顺序拼接各槽位装配结果），**不可声明**；`sound` 是**扁平列表**返回，其**唯一可声明槽位**
  名为 `all`（= 列表本体）。两者同名不同物，判定由 `parse_manifest(..., slots=SLOT_LAYOUT[agent])` 的
  `slots` 入参区分（**无人工特例分支**）。
- **不变量**：① 每槽位装配序列 == 该槽位声明序列；② `all`（五个 dict Agent）== 按 `SLOT_LAYOUT` 顺序
  拼接，且**与改造前逐字相同**（对照机检）；③ `sound` 的返回值是 `list[Evaluator]` 且下标顺序 == 声明顺序
  （先例 `tests/unit/test_sound_composite.py:129` 按下标取用）。

### 3. 装配注入槽位（InjectionSlot，新增；**装配期契约**）

**四个注入槽位（固定、形态无关）**：`agent_config`（该 Agent 已解析的配置对象）、
`gateway`（`core/llm_gateway/gateway.py:140` 的 `LLMGateway`）、`artifacts`（工件库句柄）、
`registry`（`core/evaluators/registry.py:12` 的 `Registry`）。**按签名 opt-in**：装配点只传目标签名**声明了**的槽位。

| agent | 装配函数（**符号名锚点**） | 返回形状 | 可声明槽位 `SLOT_LAYOUT[agent]` | 由既有签名导出的注入槽位 |
| --- | --- | --- | --- | --- |
| `screenplay` | `build_screenplay_evaluators`（`agents/screenplay/evaluators/__init__.py:40`） | dict | `gates`, `proxies`, `judge` | `agent_config`, `gateway` |
| `storyboard` | `build_storyboard_evaluators`（`agents/storyboard/evaluators/__init__.py:26`） | dict | `gates`, `alignment`, `judge` | `agent_config`, `gateway` |
| `visual` | `build_evaluators`（`agents/visual/loop.py:207`） | dict | `compliance`, `proxies`, `judge` | `agent_config`, `gateway`, `artifacts` |
| `sound` | `build_sound_evaluators`（`agents/sound/evaluators/__init__.py:24`） | **list** | `all`（单槽） | `agent_config` |
| `editing` | `build_editing_evaluators`（`agents/editing/evaluators/__init__.py:27`） | dict | `gates`, `pacing`, `judge` | `agent_config`, `gateway` |
| `dev` | `build_dev_evaluators`（`agents/dev/evaluators/__init__.py:31`） | dict | `gates`, `proxies` | `agent_config`, `registry` |

- **槽位来源（零代码侧表）**：`SLOT_LAYOUT` 是**每 Agent 一份**常量，落 `agents/<agent>/evaluators/plugins.py`
  （业务侧新文件），由该 Agent 的装配函数（函数体改动、**签名与返回形状不变**）传入装配点的
  `parse_manifest(document, agent, *, slots=SLOT_LAYOUT)`；`core/evaluators/plugin.py` **零 Agent 名**。
- **`all` 的拼接顺序即 `SLOT_LAYOUT` 顺序**（`dev` = gates→proxies、`screenplay` = gates→proxies→judge、
  `storyboard` = gates→alignment→judge、`visual` = compliance→proxies→judge、`editing` = gates→pacing→judge）
  ⇒ 与既有 `agents/*/evaluators/__init__.py` 的 `all` 列表**逐字相同**。
- **声明承载**：各 `*Config` 新增属性 **`plugin_declarations`**（= 文档中 `evaluators.plugins.<本 Agent>`
  子树的**逐字拷贝**；缺 `evaluators` 段 / 缺 `plugins` / 缺本 Agent 子键 ⇒ 该属性为 `None`，**原样保留"缺失"
  事实、不补默认**）⇒ 装配期由 `parse_manifest` 见到 `None` 即抛 `PluginDeclarationError`。
- **`params` 的严格性规则（"缺声明即报错、不取码内默认"的可机检形式）**：`impl` 目标签名中
  **无默认值**的参数名集合，必须**恰好等于** `params` 键集 ∪ 被注入槽位名集（多一个/少一个 ⇒ `PluginAssemblyError`）；
  目标签名含 `*args`/`**kwargs` ⇒ 报错（禁止用可变参数吸收未知声明）。⇒ 在插件路径上**码内默认值不生效**。

**不变量**：① 被注入槽位名 ⊆ `{agent_config, gateway, artifacts, registry}` 且其值非 `None`；
② 注入槽位集 ⊆ 实体 3 表格给出的该 Agent 可用集（表外槽位 ⇒ 报错）；③ 四个槽位的**顺序无关**（纯关键字调用，无位置参数）。

### 4. 评估器插件（EvaluatorPlugin，新增；**代码面**）

- **落点两处**：`core/evaluators/plugins/**`（**业务无关**的通用件，新目录）与
  `agents/<agent>/evaluators/plugins.py`（语义与某 Agent 绑定时；**六个新文件**）。**两类都必须经 `impl` 声明**。
- **形态**：`impl` 目标可以是**类**（callable，构造即产出评估器实例）或**工厂函数**（返回评估器实例）；
  两者统一由 `impl(**params, **槽位)` 调用。
- **必须满足**：`Evaluator` + `EvaluatorSpec`（`core/evaluators/base.py:91`/`:42`，含 `cost_per_call`）；
  确定性（`core/evaluators/registry.py:29-33` 的非确定性拒绝原样生效，`human` 类例外）；零形态 import、零形态字面量/分支、
  零环境变量/配置路径/网络读取（文本 + AST 双层机检）。
- **孤立插件即不可用**：目录/模块里存在但**未被任何** `configs/*.yaml` 的 `impl` 引用 ⇒ 机检报错
  （机检粒度：`core/evaluators/plugins/*.py` 与 `agents/<agent>/evaluators/plugins.py` 的**模块级公开函数**
  ——即顶层 `def` 且名不以 `_` 开头者；`_` 前缀的私有辅助不计）。
- **不变量**：插件对 `agents.*`/`dreaming.*` 的 import 次数恒 **0**；插件内形态字面量/分支出现次数恒 **0**；
  插件读环境变量/文件路径/外部网络次数恒 **0**；插件参数来自 `params` 注入的比率 **100%**。

### 5. 形态登记（FormRegistration，扩展；**配置面 + 形态名派生面**）

```
form: <id>                     # 既有顶层键（取值域：非空字符串；自由字符串，不受枚举约束）
form_aliases: [<名称>, ...]    # 新增顶层键（与 form 同级）：该形态的**其余名称**（英文同义词 + 中文别名）
```

| 字段 | 约束 |
| --- | --- |
| `form` | 每份 `configs/*.yaml` **必须**存在且为非空字符串；**文件名 stem == `form` 取值**（`configs/<id>.yaml`）；跨文件的 `form` 取值**两两唯一** |
| `form_aliases` | 每份 `configs/*.yaml` **必须**存在（**缺键即报错**）；值必须为字符串列表（**可为显式空列表** `[]`）；每项非空、去重、**不得**等于本配置的 `form` 取值；跨文件的**全部名称**（id 面 ∪ 别名面）**两两唯一** |

- **两条派生面（不得混用）**：
  - `declared_forms(configs_dir) -> tuple[str, ...]` = **id 面**（全部 `configs/*.yaml` 的 `form:` 取值，
    去重前两两唯一）⇒ 供**登记完备**（C9 的三条并列断言）与**形态 → 配置路径**反查使用；
  - `form_literals(configs_dir) -> tuple[str, ...]` = **名称面** = id 面 ∪ 全部 `form_aliases` 项 ⇒
    **两层扫描面的唯一输入**（新增一份形态配置即**自动**纳入禁令面，无法静默逃逸）。
  这两条本文件按 `research.md` 决策 5 与决策 7 的**共同要求**细化为两个函数（id 面必须能与登记面做
  `⊆` 比较，名称面必须覆盖中文别名）——这是**细化命名，不是改口径**；文件名 stem == `form` 值这条
  不变量使登记点 ⑤ 的 `pilot_form_config_path(form)` 能在**零人工常量**下把形态 id 解析回唯一配置路径。
- **该形态的配置段完备性（逐项可机检）**：① 顶层段集合**逐字等于**基线段集合（形态差异靠**值**不靠删段，
  镜像 `tests/unit/test_config_integrity.py:122-126`）；② 新增段 `evaluators`（含 `plugins` 声明面）
  与新增键 `form_aliases` 在**全部**形态配置中存在；③ 020 全部新增口径逐项声明
  （`calibration.period_days ∈ {1,7}` / `calibration.window_semantics`（取值域单元素 `half_open`，
  `configs/movie.yaml:397`）/ `calibration.window_semantics_change_date`（`:398`）/
  `budget.channels.<id>.tiers`（`configs/movie.yaml:531`、`configs/shortdrama.yaml:534`）/
  `promo.attribution_date_required_since`（`configs/movie.yaml:83`、`configs/shortdrama.yaml:83`）/
  `calibration.transfer` 六键（`tests/unit/test_config_integrity.py:85-91` 的 `REQUIRED_PATHS` 条目）/
  `budget.runs.min_window_days` 与
  `gap_tolerance_days`（`configs/movie.yaml:620-621`、`configs/shortdrama.yaml:658-659`））；
  ④ `pilot.rehearsal.status ∈ ("declared", "unstandardized")`（`agents/pilot/pilot.py:53`）；
  ⑤ 承载业务数字的段带**非空** `note`（含"未标定"字样，沿用 `configs/movie.yaml:603` 的既有用法）；
  ⑥ 无占位符（`tests/unit/test_config_integrity.py:174-178` 的 `TODO`/`FIXME`/`PLACEHOLDER`/`xxx` 禁列）；
  ⑦ **"不适用"的显式声明面**：段内键 **`not_applicable`**（只允许出现在 `budget` 段与
  `calibration.transfer` 段——`budget.not_applicable`、`calibration.transfer.not_applicable`），
  缺项/留空即报错（**不得**用"不适用"逃避填值）；
  ⑧ **cadence 近似关系登记**：`calibration.cadence_note`（字符串）——未标定形态（`pilot.rehearsal.status
  == "unstandardized"`）**必须**给出，且**不得**出现「已标定」「已达标」「已投产」字样。
  ③ 的键名、以及 ⑦⑧ 两个名/键的**规则与取值域**均以 `contracts/form-registration.md` **C11** 为权威
  （本文件只登记**实体面与名**，不复述其规则）；⑦⑧ 的机检断言同样归 **C11**。
- **登记完备三条（替代"恰好两份"，禁止删除）**：① `form` 取值**两两唯一**（唯一数 == 配置文件数）；
  ② `declared_forms(configs_dir)` **⊆** 已登记形态派生集；③ 配置数 **≥ 2**（**下界保留**）。
  **禁止**把下界提到 3（那会把"机制可用"与"本次接入了几个形态"耦合，违反 FR-013 的机制/接入分离）。
- **不变量**：`form_aliases` 缺项率恒 0；名称面内两两唯一率 100%；`configs/*.yaml` 数 ==
  `declared_forms()` 长度；`declared_forms() ⊆ registered_forms()`；配置数 ≥ 2；
  "恰好两份"断言的**删除次数恒 0**。

### 6. 零形态分支守卫（FormBranchGuard，扩展；**单一实现**）

- **模块**：`ops/form_guard.py`（业务无关静态守卫，**单一实现**）。对外只暴露派生面与判定面：

```python
def declared_forms(configs_dir: Path) -> tuple[str, ...]        # id 面（实体 5）
def form_literals(configs_dir: Path) -> tuple[str, ...]         # 名称面（id ∪ 别名）——扫描面唯一输入
def form_branch_patterns() -> tuple[str, ...]                   # 形态无关的**语法模式**（既有六条，不变）
def iter_sources(roots=("core", "agents")) -> tuple[Path, ...]  # **含 agents/pilot**；排除 __pycache__
def literal_violations(...) -> tuple[FormHit, ...]              # 字面量层违规点
def branch_violations(...) -> tuple[FormHit, ...]               # 判断分支层违规点
def classify_exception(hit: FormHit) -> str | None              # 例外三条（E1/E2/E3）
```

- **违规点形状 `FormHit`**：`{path（相对仓库根）, symbol（所属 FunctionDef/ClassDef 名；模块级 ⇒ "<module>"）,
  line（仅人读辅助）, hit（命中的形态名或模式）, layer（"literal" | "branch"）}`——
  **锚点以符号名为准**（行号只作定位辅助，抗漂移：020 登记的行号已漂移，`specs/020-shortdrama-real-feedback/tasks.md:692`）。
- **扫描面（两层共用）**：`core/**/*.py` + `agents/**/*.py`（**全覆盖，含 `agents/pilot`**），
  排除 `__pycache__`。`tests/**`、`ops/**`、`web/**`、`dreaming/**`、`policies/**` **不在**扫描面内
  （这是**扫描面定义**，不是逐条豁免）。
- **判定口径**：ASCII 命名面按**词边界** `(?<![0-9A-Za-z_])<name>(?![0-9A-Za-z_])`（先例
  `tests/unit/test_billing_core_purity.py:54-56`）；中文命名面按**子串**。理由：短 id（如 `ad`）子串判定
  会命中 `read`/`load`/`head` 一类常见标识符，假阳性会逼出无意义的改写（先例同文件 `:222-224` 的
  "`dev` 不得误命中 `deviation`"）。
- **例外三条（逐条带可判定谓词，全仓库**只允许**这三条）**：
  - **E1 配置 `文件路径` 字面量**：命中所在字符串同时含 `configs/` 与 `.yaml`，且形态名**仅**作为路径片段出现；
  - **E2 测试夹具**：`tests/**` 不在扫描面（扫描面定义，非逐条豁免）；
  - **E3 docstring 中性描述**：命中落在模块/类/函数 docstring 内，**且该命中行不含** `=`、`⇒`、`→`
    或任一分支模式（即只列举/说明、不绑定取值）。
- **声明的字面量集合（派生，非人工常量）**：`form_literals(configs_dir)`；**人工形态常量清单数恒 0**。
- **不变量**：扫描面内字面量违规数恒 **0**（例外三条逐条可机检）；判断分支违规数恒 **0**；形态名常量表的
  **副本数恒 1**（判定 = **I-9 的两条并列断言**：反向扫描"任意 `ast.Tuple`/`List`/`Set`/`Dict` 字面量
  （**含函数体内与 `@pytest.mark.parametrize(...)` 装饰器实参**）含名称面字符串"的命中数恒 **0**，
  **且** `declared_forms` / `form_literals` 的**定义点唯一**）；锚点按符号名定位率 100%；
  `tests/unit/test_billing_core_purity.py`
  与 `tests/unit/test_dev_core_degraded_purity.py` 的既有断言体**逐字保留**。

### 7. 登记点登记项（RegistrationSite，复用 + 扩展；**不得新造第六处**）

`ops/form_onboarding.py` 维护常驻白名单 `REGISTRATION_SITES`（**恰好五处**，含每处的符号名与判定函数），
并**反向扫描**"形态清单被枚举/写死"的代码点，断言其集合 == 白名单面（新增一处即红）。
该反向扫描的**覆盖面同 I-9/C7**：**任意** `ast.Tuple`/`List`/`Set`/`Dict` 字面量，**含函数体内与
`@pytest.mark.parametrize(...)` 装饰器实参**（只看模块级赋值会**空跑假绿**：今天至少五处形态枚举
落在装饰器实参与函数体元组里，见 I-9 与 `contracts/zero-form-branch.md` C7 的机检断言①）。
登记项逐处（**符号名锚点**，行号只作辅助）：

| # | 登记点 | 登记项（该形态必须在此处"已登记"） |
| --- | --- | --- |
| ① | `tests/unit/test_form_switch.py` | 模块级 `FORMS`（`:30`，改派生）自动含该形态；`Test差异逐项可归因.test_全量差异都被配置文件承载`（`:341`）的固定差异集**原样保留**，另立"逐形态对"断言（每对差异集非空、必含 `form`、必**不含** `web`/`cost_regression`） |
| ② | `tests/unit/test_config_integrity.py` | `CONFIG_CLASSES`（`:23`）新增 `evaluators` 段清单解析器条目；`REQUIRED_PATHS`（`:48`）新增插件声明面必需键条目；参数化面（`:153-154`）由 `declared_forms()` 驱动 ⇒ 每份 `configs/*.yaml` 都跑**全部加载器**与**全部"缺项即红"条目** |
| ③ | `tests/contract/test_pilot_contracts.py` | `TestC10到C13试水运行.test_c13_两套配置差异可归因且无形态分支`（`:418`）的固定差异集（`:434-453`）**一字不改** + 另立逐对断言；`:469-477` 的写死禁用元组改由 `ops/form_guard.py` 派生（该处**本就覆盖 `agents/pilot`**） |
| ④ | `agents/pilot/pilot.py` | `config_completeness`（`:377`，调用点 `:507`）**保住按配置路径通用的性质**，并追加 `form_clause_completeness`（同模块新函数，由它收口调用）：020 口径逐项机检 + `evaluators` 段清单解析器入预检清单（返回元组变长，`tests/unit/test_pilot_chain_seven.py:118-121` 按扩展更新） |
| ⑤ | `tests/conftest.py` | `PILOT_FORMS`（`:2854`）改由 `declared_forms()` 派生；`pilot_form_config_path`（`:3054-3079`）对**任意已声明形态**返回该形态**真实配置的派生副本**（只改 `budget.ledger.root`；既有派生点断言 `assert "root: billing" in source` 保留；`:3072` 的报错行为保留给**未声明**形态） |

- **同族"两形态枚举"副本的逐处处置**（断言体不删，只换常量来源）：`tests/unit/test_form_switch.py:30`、
  `tests/unit/test_billing_core_purity.py:31`、`tests/unit/test_billing_channels.py:52`、
  `tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`。
- **不变量**：**新造第六处登记点次数恒 0**；缺任一处 ⇒ 报错并点名是哪一处；反向扫描的枚举点集合 ==
  `REGISTRATION_SITES` 对应面；既有断言的**删除/放宽次数恒 0**。

### 8. 接入改动清单（OnboardingChangeManifest，新增；**产物面**）

**落盘**（文件名**以 `contracts/onboarding-ops.md` C12/C14 为权威**）：`--out` 目录下
`onboarding-<form>-<seq:04d>.json`（**写后不回改**；同内容重跑产生**新序号**文件、旧的不覆盖）
\+ `index.jsonl` **追加**一行。

- **A. 清单文件字段**（`onboarding-<form>-<seq:04d>.json`；**字段集的权威面是 C12**，下表只登记实体面，
  与 C12 **逐键对齐、不另定同义键**；C12 另含 `mechanism_ledger_ref` / `mechanism_changes_included` /
  `zero_code_onboarding` / `counts` 等键）：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `baseline_ref` | str | **机制落地后、新形态接入前**的提交（`--baseline` 必填）；本特性的机制侧总账（实体 9）**不计入**接入改动 |
| `form` | str | 该次接入的形态 id（= 配置的 `form:` 取值） |
| `config_path` | str | 形态配置路径（相对仓库根） |
| `config_fingerprint` | str | 该形态配置文件的 BLAKE3 前 12 位（沿用 `core/orchestration/models.py:36` 的 `fingerprint_of` 口径） |
| `changes[]` | 列表 | 每条 = `{path, status ∈ {"A","M","D"}, category, violation, reason}`；**派生式 = `git diff --name-status <baseline_ref>` **∪** `git ls-files --others --exclude-standard`**（含工作区未提交改动，**并含未跟踪的新增文件**——后者正是"新增配置 + 插件"整类不被漏掉的唯一机制）⇒ 与 git 实际改动集**一致率 100%**（"自报漏项"在机制上不可能发生） |
| `category` | 枚举（**英文**） | `config`（**新增** `configs/*.yaml`）/ `plugin`（**新增** `core/evaluators/plugins/**` 或 `agents/<agent>/evaluators/**`，且**仍须经 `impl` 声明**才生效）/ `test_doc`（`tests/**`、`docs/**`、`specs/**` 的新增或修改）/ `out_of_scope`（**越界**）；**取值域的权威面是 C12**，**不得**用中文类别名 |
| `violation` | bool | `category == "out_of_scope"`：任何**既有文件**的修改/删除位于 `core/`/`agents/`/`ops/`/`web/`/`dreaming/`/`policies/`，或四类之外的新增文件 |
| `violations[]` | 列表 | 越界路径的**逐条点名**（不得只报总数） |
| `exit_code` | int | 0 = 全在界内；1 = 越界或判定失败；2 = 用法或配置错误（与既有工具一致） |

- **B. `index.jsonl` 的每次接入一行——**恰六键**（与 A 的清单文件字段**不是同一张表**，
  两者不得混列）**（键名以 `research.md` 决策 9 已钉死的六键为准，C12 的 `index.jsonl` 行同义）：

```
{baseline_ref, form, config_fingerprint, change_count, violations[], exit_code}
```

- **append-only**：`index.jsonl` 只追加；清单文件名含形态 id 与序号（`<seq:04d>`）⇒ 改写既有清单行的
  尝试被"文件名唯一 + 追加行"排开。
- **字段归属**：清单机制与 CLI 的契约在 `contracts/onboarding-ops.md`（**C12/C13**，他人撰写）与
  **C14**（CLI 与离线演示）；本文件只登记实体面并**引用**其编号。
- **不变量**：`change_count == len(changes)`；既有模块被修改的文件数恒 **0**（在机制 ref 为基线的正确用法下）；
  越界判出率 100% 且退出码非 0；"只报总数不报路径"次数恒 0；清单条目与
  `git diff --name-status <baseline_ref>` ∪ `git ls-files --others --exclude-standard` 的路径集合**逐字相等**
  （**未跟踪的新增文件必须在内**——漏掉它，"新形态接入 = 仅新增配置 + 插件"的整类新增就会被漏出账）。

### 9. 机制侧总账（MechanismLedger，新增；**留痕面**）

本特性自身对 `core/`/`agents/` 的**机制改动**（六项，逐项可枚举，`plan.md:12-17`）：① 配置驱动的插件
声明与唯一装配点（`core/evaluators/plugin.py` + 通用参数通道）；② 扫描面补面（两层均覆盖 `core/` +
`agents/` 含 `agents/pilot`，锚点改符号名）；③ 形态名由 `configs/*.yaml` 派生、三副本收敛为单一实现；
④ `agents/pilot/pilot.py:613` 裸形态词收敛；⑤ `tests/unit/test_form_switch.py:438-441` 升级为登记完备口径；
⑥ 接入改动清单机检。

- **两个常驻名（登记项，规则与机检归 `contracts/onboarding-ops.md` **C13**，本文件只登记名）**：
  - **`MECHANISM_LEDGER`** —— 上列六项的**常驻登记表**（落 `ops/form_onboarding.py`）；每项含其改动路径集。
  - **`MECHANISM_LEDGER_PATHS`** —— 上表各 `items` 路径的**并集**（去重）；**判定口径 = 集合相等**
    （文档表/`quickstart.md` 的路径面与常量**互为子集**即通过；**不写死条数**——任何"恰好 N 条"的
    硬编码计数都**不作判据**，防止机制面增删路径时被迫改数字）。
  - **穷举要求**：ledger **必须穷举机制侧的全部改动路径**，含 **① 夹具同步面**
    （`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、
    `tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、`tests/unbiasedness/*`
    等为内联配置字典补 `evaluators` 段的位置）与 **② 新增基线夹具**
    **`tests/unit/fixtures/evaluator_assembly_baseline.json`**（"改造前后两形态装配序列逐字相同"
    的对照机检所依赖的基线快照）——**漏登任一机制侧路径即 red**（那会让它以"接入改动"的名义
    混进 `violations`，判据自相矛盾）。
- **不变量**：机制侧总账**六项逐项在变更说明里可枚举**；把机制改动表述为"零代码改动"的次数恒 **0**；
  改造后既有两形态（`movie`/`shortdrama`）的**装配序列** `[(slot, evaluator_id@version), ...]`（含顺序）
  **逐字相同**；`movie["evaluators"] == shortdrama["evaluators"]`（逐字相等）；
  既有评估器**实现文件**（`agents/*/evaluators/*.py` 的既有文件）改动字节数恒 **0**。

### 10. 未标定标注（UncalibratedNotice，复用 019/020 口径；**配置面 + 产物面**）

- **形态层**：`pilot.rehearsal.status: unstandardized`（取值域 `agents/pilot/pilot.py:53`；
  在预检/运行报告里出现，`agents/pilot/run_report.py:79`）。
- **段层**：承载业务数字的段（`promo` / `budget` / `calibration` / `pilot` / `evaluators`）带**非空** `note`
  且含"未标定"字样（沿用 `configs/movie.yaml:603` 的既有用法）。
- **产物层**：接入改动清单与离线演示输出复现同一标注——`uncalibrated: true` + `uncalibrated_reason`
  （点名"受众/指标口径/素材规格/预算档属业务侧输入，未给定"）→ **键名以 `contracts/onboarding-ops.md` C14
  为权威**（`research.md` 决策 11 已钉死该两键）。
- **不变量**：未标定标注缺失次数恒 **0**；"以模拟冒充真实标定"次数恒 **0**；最小可行形态**不声明 judge**
  （结构性零花费：无 judge ⇒ 离线端到端**不构造** `LLMGateway`，`tests/unit/test_billing_core_purity.py:359`
  的 `len(sites) == 14` 本项**不动**）。

## 状态机

- **插件声明**：`声明（配置文档）→ 解析（parse_manifest）→ 装配（assemble）→（可选）注册（registry.register）→ 冻结`；
  任一步失败即**报错拒绝**（不回落、不取码内默认）；同 id 同 version 在同一注册中心**重复注册即被拒**
  （`core/evaluators/registry.py:35-38`）；非确定性插件注册即被拒（`:29-33`，`human` 例外）。
- **形态配置**：`新增 configs/<id>.yaml → declared_forms() 纳入派生面 → 两层扫描面自动纳入名称面 →
  五处登记点由派生面自动覆盖 → config_completeness 逐项预检 → 装配可跑 → 离线端到端退出码 0`。
- **接入改动**：`取基线 ref → git diff 派生改动集 → 逐条类别判定 → 越界即逐条点名 + 非 0 退出码 →
  append-only 落盘（含基线 ref 与配置指纹）`；清单**写后不回改**。
- **未标定标注**：业务侧给定前恒为 `unstandardized` + 段 `note` + 产物字段；**不得**因机制跑通而被
  改写为"已标定"（标注**不随运行次数变化**）。

## 不变量总表（全部可机检）

- **I-1 声明面一致性**：对六个装配点 Agent，`evaluators.plugins.<agent>` 各槽位 `<evaluator_id>` 的并集
  **逐字等于** `evaluator_weights.<agent>` 键集；缺项与多项均 100% 拒绝装配。
- **I-2 槽位取值域**：每个声明的 `<slot>` ∈ `SLOT_LAYOUT[agent]`；五个 dict 返回的 Agent 下声明 `all`
  的次数恒 **0**；`sound` 下声明非 `all` 槽位的次数恒 **0**；`SLOT_LAYOUT[agent]` == 实际装配返回的
  dict 键集 − `{"all"}`（`sound` ⇒ `("all",)`）。
- **I-3 参数零默认**：每个 `impl` 目标签名中无默认值的参数名集 == `params` 键集 ∪ 被注入槽位集；
  签名含 `*args`/`**kwargs` 的次数恒 **0**；在插件路径上"码内默认值生效"的次数恒 **0**。
- **I-4 版本冻结**：每个插件实例 `spec.version` == 声明 `version`（逐字）；不一致率 **0**；
  声明覆盖实现的次数恒 **0**；装配后 `registry` 内该实例的 `spec.version` 仍 == 实现产出值。
- **I-5 标识唯一与元数据**：`<evaluator_id>` == `spec.evaluator_id`（逐字）；缺 `evaluator_id`/`version`/
  `kind`/`deterministic`/`cost_per_call`（`core/evaluators/base.py:42`）即报错率 **100%**；
  同 id 同 version 重复注册 100% 被拒；非确定性（非 `human`）注册 100% 被拒。
- **I-6 保序**：每槽位装配序列 == 该槽位声明序列；`all`（五个 dict Agent）== 按 `SLOT_LAYOUT` 顺序拼接；
  改造前后两形态装配序列 `[(slot, id@version), ...]`（含顺序）**逐字相同**。
- **I-7 声明即可用（无目录自动生效）**：未被任何 `configs/*.yaml` 的 `impl` 引用的模块级公开插件函数数
  恒 **0**；"目录里存在即自动生效"次数恒 **0**；`evaluators` 段缺失时"回落到硬编码装配"的次数恒 **0**。
- **I-8 插件业务无关**：插件（`core/evaluators/plugins/**` 与 `agents/<agent>/evaluators/plugins.py`）
  对 `agents.*`/`dreaming.*` 的 import 次数恒 **0**（AST）；插件内形态字面量/分支次数恒 **0**（文本层，
  由扫描面覆盖）；插件读环境变量/文件路径/外部网络的次数恒 **0**。
- **I-9 形态名派生（副本数恒 1）**：判定口径为**两条并列断言**：
  ① **反向扫描命中数恒 0**（AST：`tests/**`、`ops/**`、`core/**`、`agents/**`、`web/**`、`dreaming/**` 中
  **任意** `ast.Tuple`/`List`/`Set`/`Dict` **字面量**——**含函数体内与 `@pytest.mark.parametrize(...)`
  装饰器实参**——含 `form_literals()` 中任一字符串的命中数）；② **定义点唯一**（全仓 `declared_forms` /
  `form_literals` 的同名 `FunctionDef` 各恰好一处，即 `ops/form_guard.py` 的派生面）；
  两条**缺一不可**（单看 ① 的"0"与"定义点是否为 1"不可区分）；
  `form_literals()` 的条目数 == id 面 ∪ 别名面去重后的条目数；`declared_forms()` 长度 == `configs/*.yaml` 数；
  缺 `form:` 键或值非字符串则**报错率 100%**。
- **I-10 零形态字面量**：扫描面（`core/` + `agents/`，**含 `agents/pilot`**）内的形态字面量违规数恒 **0**；
  例外只允许 E1/E2/E3 三条，例外率 → 违规数逐条可机检；`core/`+`agents/` 内 E1 的命中点集合
  == {(`core/deployment/evidence.py`, `_unbiasedness_result`, `:97`)}（**恰好一处**）。
- **I-11 零形态分支**：同一扫描面内 `form ==`/`form==`/`form !=`/`form!=`/`form is `/`form in ` 六种模式
  的出现次数恒 **0**；形态→值的字典分派在同一面内出现次数恒 **0**（键必是形态名 ⇒ 被 I-10 同时捉住）。
- **I-12 锚点按符号名**：`FormHit.symbol` 非空率 **100%**（模块级为 `"<module>"`）；机检定位不依赖行号。
- **I-13 副本数恒 1**：判定 = **两条并列断言**（与 I-9 同一口径）——① 反向扫描"任意容器字面量
  （**含函数体内与装饰器实参**）含 `form_literals()` 字符串"的**命中数恒 0**；② `declared_forms` /
  `form_literals` 的**定义点唯一**（全仓同名 `FunctionDef` 各恰好一处）；三处委派点（`tests/unit/test_form_switch.py`
  的 `Test零形态分支静态断言` 两个扫描用例、`tests/unit/test_billing_core_purity.py` 的
  `Test零形态与厂商字面量`、`tests/unit/test_dev_core_degraded_purity.py` 的
  `Test零形态与厂商字面量`）的**循环体与断言体逐字保留**（只换常量来源）。
- **I-14 登记完备**：`form` 取值两两唯一率 100%；`declared_forms() ⊆ registered_forms()`；
  配置数 ≥ 2；"恰好两份"断言的**删除次数恒 0**；`tests/unit/test_form_switch.py:438-441` 升级为三条并列。
- **I-15 五处登记点**：新形态在五处（实体 7 的表）逐一登记的完备率 100%；缺项即报错并**点名是哪一处**；
  **新造第六处登记点次数恒 0**；`config_completeness` 对新形态**无需改代码即生效**（返回段清单随扩展变长）。
- **I-16 020 口径与 cadence**：新形态逐项声明实体 5 的 ③；`calibration.period_days ∉ {1,7}` 时
  装配/预检报错率 **100%**；放宽取值域（`core/calibration/periods.py:30`）的次数恒 **0**；
  "按周近似 + 如实标注"式含糊兜底的次数恒 **0**；"不适用"**显式声明率 100%**（留空/省略即报错）；
  既有两形态既有取值改动次数恒 **0**（`configs/movie.yaml:620` 的 7 与 `configs/shortdrama.yaml:658` 的 14 逐字节不变）。
- **I-17 接入清单一致率**：清单路径集合 == 「`git diff --name-status <baseline_ref>` **∪**
  `git ls-files --others --exclude-standard`」的路径集合（一致率 **100%**；**未跟踪的新增文件必须在内**——
  那是"新增配置 + 插件"整类不被漏掉的唯一机制）；
  既有模块被修改的文件数恒 **0**；越界逐条点名率 **100%**；"只报总数不报路径"次数恒 **0**；
  `change_count == len(changes)`；越界时退出码非 0。
- **I-18 产物 append-only**：既有清单行/`index.jsonl` 既有行的改写次数恒 **0**；每条 `index.jsonl` 行
  含 `baseline_ref` 与 `config_fingerprint`（非空率 100%）。
- **I-19 机制/接入分离**：`baseline_ref` == 机制落地提交时，越界数为 **0**；把机制侧六项总账计入接入改动的
  次数恒 **0**；把机制改动表述为"零代码改动"的次数恒 **0**。
- **I-20 诚实边界**：未标定标注缺失次数恒 **0**；"以模拟冒充真实标定"次数恒 **0**；最小可行形态声明
  judge 的次数恒 **0**（⇒ 零真实花费/零凭证为**结构性事实**）；新形态触发的真实投放/真实素材生成次数恒 **0**。
- **I-21 零回改**：既有评估器**实现文件**改动字节数恒 **0**；既有 `eval_breakdown`/得分/成本入账改写次数
  恒 **0**；既有断言的删除/放宽次数恒 **0**；`ops/`/`web/`/`dreaming/` 的既有配置路径默认值改动次数恒 **0**。

## 磁盘布局（本特性全部产物在 `--out` 目录下）

```
<out>/onboarding-<form>-<seq:04d>.json  # 接入改动清单（写后不回改；含基线 ref 与配置指纹）
<out>/index.jsonl                      # 清单索引（append-only；每行**恰六键**，见实体 8 的 B 表）
<out>/guard-report.json                # 零分支守卫报告（两层违规点列表；FormHit 形状）
<out>/registration-report.json         # 五处登记点逐一判定 + 登记完备三条 + 无第六处
<out>/demo-report.json                 # 离线端到端演示输出（含 uncalibrated / uncalibrated_reason）
```

- **文件名以 `contracts/onboarding-ops.md` C12/C14 为权威**（上列五个名字与 C12 的产物表逐字一致）；
  本文件**不**另定文件名（早前版本的 `manifest-*` / `guard-<时间戳>.json` / `registration-<form>.json` /
  `demo-<form>.json` 命名**已废弃**）。

- **零新增落盘文件**于仓库权威目录：新形态配置落 `configs/`，插件落 `core/evaluators/plugins/` 或
  `agents/<agent>/evaluators/`，其余一律落 `--out`（**禁止**写入其它特性目录与仓库权威配置）。
- **CLI 只读 git 与文件、只写 `--out`**：不 `git add`/`git commit`、不改工作区（`research.md` 决策 9 末条）。

## 与 `plan.md` / `research.md` 的对齐（并行写就，逐条登记）

- **一致**：声明键 `evaluators.plugins.<agent>.<slot>.<evaluator_id>.{impl, version, params}`（`plan.md:13`、
  `research.md:32`）；四个注入槽位与按签名 opt-in（`research.md:96-98`）；`all` 由装配点派生、不可声明
  （`research.md:39-40`）；**既有参数不搬迁**（`params` 全为 `{}`，`research.md:99-102`）；
  **既有评估器实现文件零改动**（`research.md:183-193` 的落点表）；`declared_forms` /`form_literals` /
  `iter_sources` /`literal_violations` /`branch_violations` /`classify_exception` 六个接口名
  （`research.md:227-234`）；例外三条与词边界判定（`research.md:292-311`）；"恰好两份"→三条并列
  （`research.md:340-347`）；五处登记点逐处改法（`research.md:374-395`）；接入清单六键与 append-only
  （`research.md:423-439`）；最小可行形态与三层"未标定"标注（`research.md:512-530`）。
- **本文件的定名（供另两份契约对齐，拼写一字不改）**：顶层段 `evaluators`、声明面 `evaluators.plugins`、
  形态别名键 **`form_aliases`**、各 `*Config` 的声明承载属性 **`plugin_declarations`**、
  每 Agent 槽位布局常量 **`SLOT_LAYOUT`**、错误类型 **`PluginDeclarationError`** / **`PluginAssemblyError`**
  （`core/evaluators/errors.py:8` 的 `EvaluatorError` 之下）、派生函数 **`declared_forms`**（id 面）与
  **`form_literals`**（名称面）、违规点形状 **`FormHit`**（`{path, symbol, line, hit, layer}`）、
  类别取值域 **`{"config", "plugin", "test_doc", "out_of_scope"}`**（**英文**，权威面 `onboarding-ops.md` C12）；
  **机制侧总账的两个常驻名 `MECHANISM_LEDGER` / `MECHANISM_LEDGER_PATHS`**（规则归 `onboarding-ops.md` **C13**；
  `MECHANISM_LEDGER_PATHS` **只判集合相等、不写死条数**）；**"不适用"段内键 `not_applicable`** 与
  **cadence 近似登记键 `calibration.cadence_note`**（两者的规则与取值域归 `form-registration.md` **C11**）；
  **五个产物文件名**（权威面 `onboarding-ops.md` **C12/C14**）：`onboarding-<form>-<seq:04d>.json` /
  `index.jsonl` / `guard-report.json` / `registration-report.json` / `demo-report.json`。
- **本文件相对 plan/research 的两处**细化（不改口径，只把两条既有要求落成可判定的函数/不变量）：
  ① `declared_forms`（id 面）与 `form_literals`（名称面）**分两个函数**——`research.md:227` 的
  "`form:` 取值 + 别名键"与 `research.md:343` 的"`declared_forms() ⊆ registered_forms()`"必须同时成立，
  而登记面只有 id；② **文件名 stem == `form:` 取值**——登记点 ⑤ 必须能在零人工常量下把形态 id 反查回
  唯一配置路径（`research.md:382` 的"任意已声明形态返回该形态真实配置的派生副本"）。
