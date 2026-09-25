# 契约：插件声明形状、唯一装配点与版本冻结（C1~C4）

> 对应规格 FR-001 / FR-002 / FR-003（机制侧）/ FR-009 / FR-012、SC-004、SC-005、US1 场景 1/3/4/5、
> 边界情况第 1/2 条、Clarifications 裁决 1/2/3。
> **实现落点**：**新增** `core/evaluators/plugin.py`（唯一装配点，**业务无关**）+ 修
> `core/evaluators/errors.py`（在 `EvaluatorError:8` 之下新增两个错误类型）+ **新增**六份
> `agents/<agent>/evaluators/plugins.py`（Agent 绑定薄工厂）+ 六个装配函数的**函数体**改委派
> （**签名与返回形状不变**）+ `configs/movie.yaml`、`configs/shortdrama.yaml` 新增顶层 `evaluators` 段
> （**既有取值零改动**）。数据模型见 [../data-model.md](../data-model.md) 实体 1/2/3/4/9。
>
> **现状三处硬事实**：配置里**零**实现引用（只有 `evaluator_weights` 的 id → 权重映射，
> `configs/movie.yaml:6`、`configs/shortdrama.yaml:8`）；装配**硬编码**在六个函数里逐个 `new`
> （`agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、
> `agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、
> `agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27`）；
> 参数经各 Agent 的配置 dataclass 逐字段读取（先例 `agents/visual/config.py:24` 的 `_require_judge`、
> `:54` 的 `from_dict`）。⇒ "新形态接入 = 仅新增配置 + 插件"**今天不成立**，本契约是这条路径的机制面。
>
> **本文定名的键路径是权威**（`data-model.md` 的同名条目与本文件逐字一致，另两份契约
> （`form-registration.md` C9~C11、`onboarding-ops.md` C12~C14）**必须**复用、不得另定同义键）。

## C1 `evaluators.plugins` 声明段形状与解析（配置声明集 = 可用插件全集）

**目的**：把"评估器组合与实现引用"从**代码**搬到**配置**——形态差异的新增面必须是原则五要求的
"**新增配置项**"而非"新增分支代码"（`.specify/memory/constitution.md:121`）；并使"新形态接入不得改动
任何既有 Agent 装配函数"（裁决 2）在机制上成立（槽位由**声明**决定，不由代码侧表决定）。

**形状/接口**（拼写全文一致）：

```yaml
# configs/<form>.yaml（形态配置顶层段；两既有形态的该段**逐字相同**，见"兼容规则"）
evaluators:
  plugins:
    <agent>:                          # {screenplay, storyboard, visual, sound, editing, dev}
      <slot>:                         # 见下表（该 Agent 的槽位取值域）
        <evaluator_id>:               # 逐字等于 evaluator_weights.<agent> 的键
          impl: "<module>:<attr>"     # module:attr；唯一解析点 = core/evaluators/plugin.py
          version: "<与 spec.version 逐字相等>"   # 必须显式；缺键即声明期报错
          params: {}                  # 形态无关参数（既有评估器一律 {}，见 C2）
```

**槽位取值域（`SLOT_LAYOUT`，由既有装配函数的返回形状导出，落 `agents/<agent>/evaluators/plugins.py`）**：

| `<agent>` | 装配函数（**符号名锚点**） | 返回形状 | 可声明 `<slot>` |
| --- | --- | --- | --- |
| `screenplay` | `build_screenplay_evaluators`（`agents/screenplay/evaluators/__init__.py:40`） | dict | `gates`, `proxies`, `judge` |
| `storyboard` | `build_storyboard_evaluators`（`agents/storyboard/evaluators/__init__.py:26`） | dict | `gates`, `alignment`, `judge` |
| `visual` | `build_evaluators`（`agents/visual/loop.py:207`） | dict | `compliance`, `proxies`, `judge` |
| `sound` | `build_sound_evaluators`（`agents/sound/evaluators/__init__.py:24`） | **list** | `all`（单槽） |
| `editing` | `build_editing_evaluators`（`agents/editing/evaluators/__init__.py:27`） | dict | `gates`, `pacing`, `judge` |
| `dev` | `build_dev_evaluators`（`agents/dev/evaluators/__init__.py:31`） | dict | `gates`, `proxies` |

- **`all` 的两义（不可混用）**：五个 dict 返回的 Agent，`all` 是装配点按 `SLOT_LAYOUT` 顺序拼接的
  **派生汇总键**、**不可声明**（声明即 `PluginDeclarationError`）；`sound` 是扁平列表返回，其**唯一**
  可声明槽位名为 `all`（= 列表本体），声明其它槽位即报错。
- **`<evaluator_id>` 的前缀↔kind 一致**（镜像既有 `evaluator_weights` 键命名法）：`rule.`→`rule`、
  `proxy.`→`proxy_model`、`judge.`→`judge`、`human.`→`human`；不符即装配期报错。
  `promo`/`pilot` **不入**声明面：`promo` 的构造点不在本特性改动面内，其 `human.platform_metrics`
  是校准锚点、本就不在装配集合内（`plan.md:498-501`）。
- **解析入口（唯一）**：`core/evaluators/plugin.py` 的
  `parse_manifest(document, agent, *, slots) -> PluginManifest`——`document` 为形态配置文档（`yaml.safe_load`
  结果，**保序**），`slots` 为该 Agent 的 `SLOT_LAYOUT`（业务侧常量，由装配函数传入 ⇒ `core/` 零 Agent 名）。
  返回的清单保留**槽位首现顺序**与**槽位内声明顺序**；叶子键**恰好三键**（`impl`/`version`/`params`）。
- **声明承载**：各 `*Config` 新增属性 `plugin_declarations` = 文档中 `evaluators.plugins.<本 Agent>` 子树的
  **逐字拷贝**；缺 `evaluators` 段 / 缺 `plugins` / 缺本 Agent 子键 ⇒ 该属性为 `None`（**不补默认**），
  由装配期报错（见 C3）。
- **唯一装配点**：`assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None)`
  （`core/evaluators/plugin.py`）。**任何其它解析/注册路径不得存在**（FR-012）：不得在六个装配函数里
  各写一份 `importlib` 解析，不得另建插件注册表文件；`registry.register` 仍是注册的**唯一入口**
  （`core/evaluators/registry.py:59`）。

**机检断言**：

- **声明集 = 全集**：六个装配点 Agent 的 `evaluators.plugins.<agent>`（各槽位 `<evaluator_id>` 的并集）
  **逐字等于** `evaluator_weights.<agent>` 键集——这是"配置声明集 = 可用插件全集"的可机检形式。
- **零实现引用 ⇒ 零可用插件**：把某 `<evaluator_id>` 的声明整块删除而权重键保留 ⇒ 装配期报错
  （缺项）；把权重键删除而声明保留 ⇒ 装配期报错（多项）。两个方向都必须红。
- **槽位合法性**：`SLOT_LAYOUT[agent]` == 实际装配返回的 dict 键集 − `{"all"}`（`sound` ⇒ `("all",)`）；
  声明表外槽位、或对五个 dict Agent 声明 `all`，均 100% 报错。
- **叶子键恰好三键**：出现第四键或缺失任一键 ⇒ `PluginDeclarationError`（不静默忽略多余键）。
- **保序**：`assemble` 的返回序列（每槽位内）== 该槽位的声明顺序；`yaml.safe_load` 保序是其前提。
- **两形态 `evaluators` 段逐字相同**（关键兼容结论，常驻断言）：`movie["evaluators"] == shortdrama["evaluators"]`
  ——因为两形态评估器组合相同（既有断言 `tests/unit/test_form_switch.py:161` 的 `set(movie_w) == set(short_w)`）
  且既有参数不搬迁（`params` 全为 `{}`）⇒ 差异集断言（`tests/unit/test_form_switch.py:341-379`、
  `tests/contract/test_pilot_contracts.py:434-453`）**一字不变**。
- **既有参数的单一事实源**：`tests/unit/test_form_switch.py:166-169` 读的仍是 dataclass 路径
  （`SoundConfig.from_yaml(...).av_sync_threshold_ms`），装配面**不得**另存一份同值（双事实源 ⇒ 漂移即假绿）。

**反例**（必须被拒）：

- `evaluators.plugins.<agent>` 缺 `impl` / `version` / `params` 任一叶子键 ⇒ `PluginDeclarationError`。
- `impl` 不是字符串、或含 0 个 / 2 个 `:` ⇒ `PluginDeclarationError`（不猜、不截断）。
- `<evaluator_id>` 与 `evaluator_weights.<agent>` 的键不一致（多/少/拼写不同）⇒ 装配期报错。
- 在 `evaluators.plugins.<agent>.all.*` 下为五个 dict Agent 声明插件 ⇒ 报错（`all` 是派生键）。
- 把形态名写进 `slot` 名（例如 `<slot>: movie`）⇒ 槽位非法报错。
- **把声明面塞进 `evaluator_weights`**（例如 `{id: {weight: 0.3, impl: ...}}`）⇒ 视为绕过：
  `core/evaluators/weights.py:18` 的取值域（数值或 `"gate"`）**不得**放宽。
- 新增第七个"插件注册表"配置文件 ⇒ 违反"配置声明集 = 全集"（多一份权威面即冲突）。

**兼容规则（零回改承诺）**：

- 既有两形态配置的**既有取值零改动**（含 `configs/movie.yaml:620` 的 `7` 与
  `configs/shortdrama.yaml:658` 的 `14`）；`evaluators` 段是**新增段**，且两形态**逐字相同**⇒
  顶层差异集不变 ⇒ 015 的差异集断言（`tests/unit/test_form_switch.py:341-379`、
  `tests/contract/test_pilot_contracts.py:434-453`）**原样保留**。
- 六个装配函数的**签名与返回形状不变** ⇒ `agents/pilot/backends.py`、`agents/pilot/stages.py`
  （`build_runtime` 链路）与 30 余处调用点（`tests/unbiasedness/*`、`ops/demo_*_loop.py` 等）**零改动**；
  既有 `all` 列表顺序经 `SLOT_LAYOUT` 派生后**逐字相同**。
- **既有评估器实现文件零改动**（`agents/*/evaluators/*.py` 的既有文件字节不变）：版本号把调用方实现文件
  的**字节**并入哈希（`agents/sound/evaluators/_versioning.py:12` 与 `:22-23`、
  `agents/dev/evaluators/_versioning.py:13`）⇒ 改一字节即改 `eval_breakdown`（违背 FR-013）。
- 内联配置字典夹具（`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、
  `tests/contract/test_*_contracts.py`、`tests/unbiasedness/*`）**必须补齐 `evaluators` 段**；
  **禁止**在实现里加"缺段即回落到硬编码装配"的兜底（那会留下影子装配路径，违反 FR-012）。

## C2 唯一装配点与调用约定（`impl(**params, **槽位)` + `importlib` + 严格性 + 错误类型）

**目的**：把"声明 → callable → 关键字注入"收成**一处**口径（六份解析必然漂移，且"唯一装配点"这条 FR
会当场失效），并让"**缺声明即报错、不取码内默认**"从注释变成**语法层面的严格性规则**。

**形状/接口**：

```python
# core/evaluators/plugin.py（业务无关；零形态名 / 零 Agent 名 / 零分支）
def parse_manifest(document: Mapping, agent: str, *, slots: tuple[str, ...]) -> PluginManifest: ...

def assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None) -> dict[str, list[Evaluator]]: ...

# 注入槽位（固定四个、形态无关；**按签名 opt-in**：只传目标签名声明了的槽位）
INJECTION_SLOTS = ("agent_config", "gateway", "artifacts", "registry")
```

- **`impl` 解析**：`importlib.import_module(<module>)` + 右侧**逐段 `getattr`**；结果为 callable
  （类或工厂函数皆可）——**类也是 callable**，故既有 30 个评估器**无需改构造签名**即可成为插件目标。
  解析失败（模块不存在 / 属性不存在 / 非 callable）⇒ `PluginAssemblyError`，文案点名
  `evaluators.plugins.<agent>.<slot>.<id>.impl` 与实测 `impl` 字符串。
- **调用约定**：**`impl(**params, **注入槽位)`（纯关键字，禁止位置参数）**。
- **参数严格性规则（"不取码内默认"的机检形式）**：令 `required(impl)` = 目标签名中**无默认值**的参数名集合，
  `injected` = 实际被注入的槽位名集，则必须
  **`required(impl) == set(params) | injected`**（多一个 / 少一个 ⇒ `PluginAssemblyError`）。
  ⇒ ① 漏声明即报错；② 在插件路径上**码内默认值不生效**（声明了带默认值的参数即报错 ⇒ 逼着实现方去掉
  默认值或把值搬进声明面）；③ 声明面写了签名不接收的键也报错（防"声明了没人用"）；
  ④ 目标签名含 `*args` / `**kwargs` ⇒ 报错（禁止用可变参数吸收未知声明）。
- **既有参数不搬迁（单一事实源）**：既有评估器的既有参数留在 `configs/*.yaml` 的**原段**
  （`sound.loudness` / `editing.pacing_baseline` / `visual.judge.prompts` 等），由
  `agents/<agent>/evaluators/plugins.py` 的**薄工厂**经 `agent_config` 槽位读取并构造既有评估器：
  `def loudness_compliance(*, agent_config) -> Evaluator: return LoudnessComplianceEvaluator(agent_config.loudness)`；
  其声明面 `params: {}`。**不得**把既有参数逐值拷进 `params`（双事实源 ⇒ 与
  `tests/unit/test_form_switch.py:166-169` 的 dataclass 读路径漂移即假绿）。
- **一致性校验（三项，全部装配期）**：① `spec.evaluator_id` == `<evaluator_id>`（逐字）；
  ② `<evaluator_id>` 的前缀 == `spec.kind`（C1 的前缀↔kind 口径）；③ `spec.version` == 声明 `version`
  （见 C4）。任一不符 ⇒ `PluginAssemblyError`，文案**同时点名两侧实测值**。
- **注册**：仅在提供 `registry` 时逐实例 `registry.register`（`core/evaluators/registry.py:18`；
  `:29-33` 非确定性拒绝、`:35-38` 同键重复注册拒绝**原样生效**）；不传则不注册（每次装配得到独立实例，
  沿用 `agents/dev/evaluators/__init__.py:31` 的既有语义）。
- **一一对应与保序**：装配得到的 `<evaluator_id>` 并集必须**逐字等于** `agent_config.evaluator_weights`
  的键集（缺项/多项即拒绝装配；沿用 `agents/dev/evaluators/__init__.py:52-58` 与
  `agents/screenplay/evaluators/__init__.py:64-71` 的**既有中文文案口径**）；每槽位装配序列 == 声明序列。
- **错误类型与文案口径**（`core/evaluators/errors.py`，均继承 `EvaluatorError:8`）：

| 错误类型 | 触发面 | 文案必须点名 |
| --- | --- | --- |
| `PluginDeclarationError` | 声明面：缺 `evaluators`/`plugins`/本 Agent 子键；叶子键缺/多；`impl` 非字符串或 `:` 数不为 1；`version` 非字符串；`params` 非映射 | 声明路径 `evaluators.plugins.<agent>.<slot>.<id>.<leaf>` + 实测值 |
| `PluginAssemblyError` | 装配期：`impl` 不可解析/非 callable；`required(impl) != set(params) \| injected`；`evaluator_id`/前缀/`version` 不一致；注入槽位越界（∉ `INJECTION_SLOTS`）；集合与权重键集不匹配；`EvaluatorSpec`（`core/evaluators/base.py:42`）或注册校验失败 | 声明路径 + **两侧实测值**（不静默取任一侧） |

  **禁止**只报"插件装配失败"一类无定位信息的文案；`RegistrationError`（`registry.py` 抛出的重复键/
  非确定性）与 `ValidationError`（`base.py` 的模型校验）**保持既有语义与文案**，不得改写。

**机检断言**：

- `impl` 目标签名中无默认值参数集 == `params` 键集 ∪ 被注入槽位集（对上/对下两个方向各注入一个反例，
  必须各自报错）；签名含 `*args`/`**kwargs` ⇒ 报错。
- 纯关键字：以位置参数调用插件的次数恒 **0**（AST：装配点的 `impl(...)` 调用不得出现位置实参）。
- 注入槽位集 ⊆ `INJECTION_SLOTS` 且其值非 `None`；出现表外槽位 ⇒ 报错。
- 装配集合与权重键集不匹配（缺项/多项）**两个方向**均 100% 报错，且报错文案含两处差集。
- 同 id 同 version 在两个形态各自装配但**同一注册中心**内注册 ⇒ 第二次 `RegistrationError`
  （`core/evaluators/registry.py:35-38`）；非确定性插件（非 `human`）注册 ⇒ `RegistrationError`（`:29-33`）。
- 缺必需元数据（`evaluator_id`/`version`/`kind`/`deterministic`/`cost_per_call`）⇒ 报错率 100%
  （`core/evaluators/base.py:42` 的模型校验，`cost_per_call` 由宪章门禁要求，`.specify/memory/constitution.md:187-188`）。
- **`assemble` 是 `impl` 的唯一解析点**：全仓 `importlib.import_module` / `entry_points` 的
  **插件解析**命中点集合 == {`core/evaluators/plugin.py`}（"第二装配路径"出现即红）。

**反例**：

- 目标签名 `def f(*, a, b=1)` 而声明 `params: {a: 1}` ⇒ 报错（`b` 有默认值却被签名暴露 ⇒ 声明面必须
  覆盖或实现方必须去掉默认值；**禁止**静默用码内 `1`）。
- 声明 `params: {unknown: 1}` 而签名不接收 `unknown` ⇒ 报错（不静默忽略）。
- 目标签名 `def f(**kwargs)` ⇒ 报错（可变参数吸收未知声明）。
- 用 `inspect.signature` **静默补齐 / 忽略未知键** ⇒ 红（"缺声明即报错"退化为"能跑就行"）。
- 每个 Agent 写一份"id → 槽位"的代码侧映射表、由装配点查表分派 ⇒ 红（新插件必须回来改表 ⇒ 门禁失效）。
- 按 `plugin_id` 分派到"每 Agent 一个总工厂"（总工厂内部 `if plugin_id == ...`）⇒ 红（第二注册面）。
- `{from_config: "<配置路径>"}` 参考型 DSL（`params` 里写路径让装配点去读配置）⇒ 红（多一套解析规则，
  且对象型参数仍需工厂 ⇒ 两套机制并存）。

**兼容规则（零回改承诺）**：

- 六个装配函数的**函数体替换、签名与返回形状逐字不变**（`{"compliance","proxies","judge","all"}` /
  `{"gates","proxies","all"}` / `{"gates","proxies","judge","all"}` / `{"gates","alignment","judge","all"}` /
  **扁平列表** / `{"gates","pacing","judge","all"}`）；`agents/visual/loop.py:138` 的
  `build_evaluators(config, gateway, artifacts)` 调用点与 `:222` 的 `_judge_anchor_hashes` 调用点
  **语义逐字保持**（`_judge_anchor_hashes` 从 `agents/visual/loop.py:103` 整体迁入
  `agents/visual/evaluators/plugins.py`，语义不变）。
- **改造前后对照机检（前置，必须先落地）**：导出两形态的装配序列
  `[(slot, evaluator_id@version), ...]`（**含顺序**）并断言**逐字相同**——若顺序漂移，
  `tests/unit/test_sound_composite.py:129` 这类**按下标取用**的既有用例**必须**变红（它们就是保序的守卫）。
- 既有两形态的 `eval_breakdown`、得分、权重键集**逐字节不变**（原则一/二）；
  既有 `human` 锚点不入装配集合（`core/evaluators/base.py:20` 的 `EvaluatorKind.HUMAN`，语义不变）。
- 与 019/020 的零回改：`core/evaluators/weights.py:18` 的取值域（数值或 `"gate"`）、
  `core/evaluators/composite.py:37` 的裸键对齐口径**原样保留**，不改取值域、不新增分支。

## C3 插件目录与"声明才生效"（缺声明即报错；目录不决定可用性）

**目的**：裁决 1/2 的落点——**配置声明是插件的唯一注册面**；"把文件放进目录就生效"这条**不存在**。
它同时是"新形态接入 = 仅新增配置 + 插件"的唯一可实现形式（否则新插件必然要回来改代码）。

**形状/接口**：

```
core/evaluators/plugins/                 # 新目录（业务无关的通用评估器；B 阶段新形态的插件落点）
agents/<agent>/evaluators/plugins.py     # 新文件 × 6（Agent 绑定薄工厂：一评估器一函数，纯关键字签名）
```

- **两个落点的判定口径**（FR-003：按**类别**而非路径前缀判定）：落 `core/evaluators/plugins/**`
  （业务无关通用件）或 `agents/<agent>/evaluators/**`（语义确实与某 Agent 绑定）的**新增**文件均属
  "插件"类别，**均放行**；但**两类都必须经 `impl` 在配置里声明**才生效——**目录不决定可用性**。
  同一前缀下的**既有文件修改**则属**越界**（前缀判定无法区分这两件事，故判据必须是"新增 vs 修改 × 类别"）。
- **插件实现的硬性属性**（文本 + AST **双层**，AST 层复用 017 先例
  `tests/unit/test_dev_core_degraded_purity.py:167-179` 的 import 扫描法）：
  ① 零 `import agents.*` / `import dreaming.*`（AST；`core/` 必须业务无关）；
  ② 零形态字面量与零形态判断分支（文本层，扫描面见 C5/C6）；
  ③ 零 `os.environ` / `os.getenv` 读取，零对配置文件路径或任何文件的读取，零网络调用
  （`socket`/`urllib`/`requests`/`httpx` 等 import 或字面 URL）；
  ④ 零 LLM/厂商 SDK 直连（宪章原则三的网关口径不因插件放宽）；
  ⑤ 参数**全部**来自 `params` 注入（C2 的严格性规则）。
- **缺声明即报错（三条，逐条可机检）**：① 缺 `evaluators` 段 / 缺 `plugins` 子段 / 缺本 Agent 子键
  ⇒ `PluginDeclarationError`；② 某槽位并集缺 `<evaluator_id>`（相对权重键集）⇒ `PluginAssemblyError`；
  ③ 权重键集有多余键（无声明）⇒ `PluginAssemblyError`。**不取码内默认、无任何回落路径**。
- **孤立插件即不可用（且机检报错）**：机检粒度 = `core/evaluators/plugins/*.py` 与
  `agents/<agent>/evaluators/plugins.py` 的**模块级公开函数**（顶层 `def` 且名不以 `_` 开头者），
  每个必须**至少被一份 `configs/*.yaml` 的 `impl` 以 `<module>:<attr>` 形态引用**；零引用 ⇒ 报错
  （防"放在了目录里就以为生效"）。`_` 前缀的私有辅助（如迁入的 `_judge_anchor_hashes`）**不计入**该扫描面。

**机检断言**：

- **目录不决定可用性**：把一个插件文件放进 `core/evaluators/plugins/` 而**不**在配置里声明 ⇒
  装配期该评估器**不可用**（装配集合缺项 ⇒ 报错），且"目录里存在即自动生效"次数恒 **0**。
- **缺 `evaluators` 段即装配期报错**（常驻）：用一份删掉 `evaluators` 段的派生配置装配 ⇒ 报错，
  退出码非 0；**禁止**以"缺段即回落到硬编码装配"换取夹具全绿（那是第二装配路径）。
- 插件业务无关四条的违规数**逐条恒 0**（AST 层与文本层各出一份报告，二者都不得空跑）。
- `agents/<agent>/evaluators/plugins.py` 的 `SLOT_LAYOUT` 与该 Agent 装配函数实际返回形状一致
  （C1 的槽位机检）；`core/evaluators/plugin.py` 的文件内容**不含**任何 Agent 名与形态名。
- 新增评估器**必须**同时提交单元测试、注册元数据（含 `cost_per_call`）与既有评估器对比样本
  （宪章工作流门禁，`.specify/memory/constitution.md:187-188`）。

**反例**：

- 目录扫描自动装配（`glob("core/evaluators/plugins/*.py")` 后逐个 import）⇒ 红（目录决定可用性）。
- 装饰器 / 导入即自动注册、`entry_points`（安装元数据）决定可用性 ⇒ 红（可用性退化为"谁被导入过"）。
- 在 `core/evaluators/plugin.py` 里写"若某 Agent 则用某工厂"⇒ 红（`core/` 必须业务无关）。
- 插件文件内 `import os; os.environ.get("FORM")` 或 `open("configs/movie.yaml")` ⇒ 红（插件自行取数）。
- 为让内联配置夹具免于补 `evaluators` 段而在实现里加"缺段回落"⇒ 红（影子装配路径）。
- 插件里出现形态字面量（如 `if agent_config.form == "movie"`）⇒ 红（C5/C6 两层扫描面同时捉住）。

**兼容规则（零回改承诺）**：

- 新增文件不改任何既有文件的**字节**（⇒ 不影响任何评估器的版本哈希）；`core/evaluators/plugins/` 是
  **新目录**，其归属满足 Monorepo 边界"新增目录必须先归 `core`（业务无关）或 `agents`（业务相关）"
  （`.specify/memory/constitution.md:165-166`）。
- **不引入**任何第三方插件框架或新运行时依赖（仅 stdlib 的 `importlib`/`inspect`/`ast` + 既有 `pyyaml`），
  复杂度跟踪见 `plan.md:451-461`。
- 既有装配函数的返回形状不变 ⇒ 下游（`agents/*/loop.py`、`agents/pilot/*`、`ops/*`、`web/*`）**零改动**。

## C4 `version` 显式校验与"不允许覆盖实现"（与 010 `id@version` 冻结口径的关系）

**目的**：把 010 的 `id@version` 冻结口径（`core/evaluators/base.py:42` 的 `EvaluatorSpec`）在**形态配置面**
显式化：评审者只看配置即可枚举该形态用到的全部 `id@version`；同时**禁止**配置**谎报**版本
（否则原则一"任何行为变更必须升版本号"当场不可证伪）。

**形状/接口**：

- **声明值与派生值的关系只有一种：逐字相等**。既有版本号是**派生值**
  （`1.0.0+<实现文件与口径参数哈希前 12 位>`，`agents/dev/evaluators/_versioning.py:13`、
  `agents/sound/evaluators/_versioning.py:12` 同款，且 `:22-23` 把调用方实现文件**全部字节**并入哈希）：
  - 相等 ⇒ 装配通过，`evaluator_id@version` 即冻结标识，进节点 `eval_breakdown`；
  - 不等 ⇒ **`PluginAssemblyError`**，文案点名"声明的 version 与实现的 version 不一致"并给出**两侧实测值**
    （不静默取任一侧、不回落、不告警后放行）。
- **不覆盖实现的三条禁止**：① 不得把声明值写回 `spec`（`EvaluatorSpec` 是 `frozen=True`，
  `core/evaluators/base.py:41`）；② 不得用 `dataclasses.replace` / 反射在注册前改写 `spec.version`
  或 `spec.key`；③ 不得在注册前"归一化"版本字符串（去空格、截断哈希、补 `+` 等）。
  机检：装配后 `registry.list_all()` 中该实例的 `spec.version` **== 实现产出值 == 声明值**（三方相等）。
- **辅助工具（判据不是它）**：`ops/form_plugin.py sync-versions --check|--write --config <路径>`
  ——`--check`（**默认**）只报差集、退出码非 0；`--write` 才回写，且**只改**
  `evaluators.plugins.*.*.*.version` 一个键（走既有 `core/yaml_edit.py` 定点改写，与 019 的扩量改写同风格）。
  **CI 门禁只跑 `--check`**：**禁止**以 `--write` 改写配置来换取绿灯。
- **声明面 Agent 的 `version` 条数**：六个装配点 Agent 各评估器一条（按 `agents/*/evaluators/__init__.py`
  的装配函数逐个枚举：visual 5 / dev 4 / screenplay 7 / storyboard 5 / sound 4 / editing 5），
  且两形态配置的这 30 条**逐字相同**（C1 的"两形态 `evaluators` 段逐字相同"）。

**机检断言**：

- 声明 `version` 与 `spec.version` 逐字相等率 **100%**；不等时的报错率 **100%**（注入一个改过一位的
  声明值即变红）；报错文案含两侧实测值。
- 缺 `version` 叶子键 ⇒ `PluginDeclarationError`（**不得**装配时回填、不得取码内默认）。
- 装配前后 `spec.version` 的字节不变（声明不覆盖实现）：装配后实例的 `spec.version` 仍 == 实现产出值。
- `evaluators.plugins.*.*.*.version` 是**字符串**且非空；非字符串或空串 ⇒ 声明期报错。
- `--check` 与 `--write` 的差集集合相等；`--check` 在差集非空时退出码非 0；`--write` **只**改
  `version` 一个键（其余字节逐字不变，含注释与行序）；`--write` 不 `git add`/`commit`、不触碰工作区其余文件。
- **改造前后两形态装配序列逐字相同**（C2 的对照机检）⇒ 既有 `eval_breakdown` 键与得分**逐字节不变**。

**反例**：

- 声明 `version: "1.0.0"`（只写 base 号）而实例产出 `1.0.0+<12 位哈希>` ⇒ 报错（版本冻结不得退化为
  "基础号"，否则"任何行为变更必须升版本号"不可机检）。
- 声明覆盖实现（改实现文件而声明不动）⇒ 报错（此即"配置谎报版本"，历史得分看起来可复现 ⇒ 原则一崩塌）。
- 把 `sync-versions --write` 当 CI 门禁（以改写权威配置换取通过）⇒ 红。
- 用 `dataclasses.replace(spec, version=declared)` 让不一致"消失" ⇒ 红。
- 把 `version` 写进 `params`（当普通参数传）而不写叶子键 ⇒ 报错（叶子键恰好三键）。

**兼容规则（零回改承诺）**：

- **010 侧零回改**：`EvaluatorSpec`（`core/evaluators/base.py:42`）的字段与校验、`Registry` 的键语义
  （`core/evaluators/registry.py:18`/`:35-38`）、`composite_score_versioned` 的裸键对齐口径
  （`core/evaluators/composite.py:37`）**一律不动**；本契约只**增加**配置侧的显式冻结点与一道一致性校验。
- **既有版本号派生规则不变**（含实现文件字节）⇒ 既有评估器的版本值**逐字不变** ⇒ 既有节点的
  `eval_breakdown` 与得分零影响；**既有评估器实现文件零改动**是这条承诺的前提（C1 的兼容规则）。
- **015 两形态切换**：因两形态 `evaluators` 段逐字相同，声明面**不进入差异集** ⇒
  `tests/unit/test_form_switch.py:341-379` 与 `tests/contract/test_pilot_contracts.py:434-453` **一字不改**。
- **020 登记点零回改**：本契约不新增登记点（五处见 C9）；`agents/pilot/pilot.py:377` 的
  `config_completeness` 预检只**追加** `evaluators` 段清单解析器条目（缺段即拒绝启动），
  既有返回项与语义**只增不减**（`tests/unit/test_pilot_chain_seven.py:118-121` 按扩展更新）。
