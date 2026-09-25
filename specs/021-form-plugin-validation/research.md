# 调研：形态插件扩展性验证（021-form-plugin-validation）

> 阶段 0 产出。每条决策 = **问题 → 决策 → 依据/理由 → 被否决 → 影响面**。规格依据见 [spec.md](spec.md)，
> 架构落点见 [plan.md](plan.md)。本特性与 020 的关系是**复用其登记点与口径**（五处登记点、cadence
> `{1,7}`、`budget.channels`、迁移口径，见 `specs/020-shortdrama-real-feedback/research.md:529` 的决策 10
> 清单），与 015 的关系是**把两形态硬编码的静态守卫升级为配置派生**（`tests/unit/test_form_switch.py:30`
> 起的整节）。
>
> **命名纪律**：本文**不发明**字段名与业务数字。凡涉及"插件清单的叶子键叫什么"，一律指向
> `contracts/plugin-config.md`（C1~C4）；凡涉及"扫描面/例外口径的机检边界"，一律指向
> `contracts/zero-form-branch.md`（C5~C8）；凡涉及"五处登记点与 020 口径声明"，一律指向
> `contracts/form-registration.md`（C9~C11）；凡涉及"清单产物、CLI、演示与诚实分层"，一律指向
> `contracts/onboarding-ops.md`（C12~C14）。本文与 plan **不复述契约已定的键清单**。
>
> **两条硬约束贯穿全部决策**（先摆在这里，后面不再重复论证）：
> ① **机制改动 ≠ 零代码改动**——本特性**必须**先补机制（裁决 1~6），机制改动面（`core/` 与六个 Agent
> 装配面）**不**计入"接入改动"（FR-004/FR-013、`specs/021-form-plugin-validation/spec.md:115`/`:124`）；
> ② **既有两形态的 `eval_breakdown`/得分逐字节不变**（原则一/二）——这条约束在本仓的**技术含义**比字面
> 更硬：评估器版本号是把**实现文件字节**并入哈希得来的（见决策 4），所以"改不改评估器实现文件"是一个
> 决定整个机制落点的开关。

## 决策 1：插件声明的形状与**唯一装配点**——配置声明集 = 可用插件全集

**问题**：今天"仅新增配置 + 插件"**不成立**：配置里**只有** `evaluator_weights` 的评估器 id → 权重映射
（`configs/movie.yaml:6`、`configs/shortdrama.yaml:8`），**零**实现引用；全仓 `plugin` / `entry_point` /
评估器侧 `import_module` 命中数为 0；装配是**硬编码**在六个函数里逐个 `new` 并喂进 Agent 配置字段——
`agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、
`agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、
`agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27`。规格已裁决声明形状
与"配置声明集 = 可用插件的全集"（裁决 1，`specs/021-form-plugin-validation/spec.md:27`）。

**决策**：新增顶层段 `evaluators.plugins.<agent>.<slot>.<evaluator_id> = {impl, version, params}`（叶子键
集**逐字** = 规格冻结的三键，层级名以 `contracts/plugin-config.md` C1 为准）：

- `<agent>` ∈ `{screenplay, storyboard, visual, sound, editing, dev}`（= 六个既有**装配点**的 Agent 名，
  也是 `evaluator_weights` 的键）；
- `<slot>` = **既有装配函数返回 dict 的槽位名**（`gates` / `proxies` / `judge` / `alignment` / `pacing` /
  `compliance`）——`agents/sound/evaluators/__init__.py:24` 返回**扁平列表**，其槽位固定为单槽 `all`；
  `all`（既有 dict 的汇总键）由装配点**派生**、**不可声明**；
- `<evaluator_id>` **逐字等于** `evaluator_weights.<agent>` 的键（`rule.` / `proxy.` / `judge.` 前缀口径）；
- **声明顺序即装配顺序**（`yaml.safe_load` 保序）：`build_sound_evaluators` 的返回值被按下标取用
  （`tests/unit/test_sound_composite.py:129`），故装配点**必须**保持声明顺序，机检 = 装配序列 == 声明序列；
- **唯一装配点** = 新模块 `core/evaluators/plugin.py`：`importlib` 解析 `impl`（`module:attr`），
  实例化、校验、按槽位组装；**任何其它解析/注册路径不得存在**（FR-012）。

**不变量（装配期硬校验，缺项/多项即拒绝，不取码内默认）**：

1. 各槽位下 `<evaluator_id>` 的**并集** == `evaluator_weights.<agent>` 的键集（沿用
   `agents/dev/evaluators/__init__.py:52-58` 与 `agents/screenplay/evaluators/__init__.py:64-71` 的既有口径）；
2. `impl` 解析结果必须为 callable，且其实例/返回值的 `spec.evaluator_id` == 声明的 `<evaluator_id>`；
3. 每个插件实例的 `spec.version` **逐字等于**声明的 `version`（见决策 3）；
4. 实例必须过 `core/evaluators/base.py:42` 的 `EvaluatorSpec` 校验（含 `cost_per_call`）与
   `core/evaluators/registry.py:18` 的注册校验链（含 `:29-33` 的非确定性拒绝、`:35-38` 的同键重复注册拒绝）。

**依据/理由**：① 槽位来自**声明**这一条是"新形态接入不得改动任何既有 Agent 装配函数"（裁决 2）的**唯一
可实现形式**——若槽位由代码侧表决定（例如每个 Agent 写一份"id → 槽位"映射），新插件就必须回来改那张表，
门禁当场失效；② `<evaluator_id>` 与权重键**逐字相同**使"装配集合 ↔ 权重键集一一对应"不需要任何映射表就
可机检（这正是 FR-001 末句的诉求）；③ 声明顺序即装配顺序让既有返回值（扁平列表、dict 内顺序）**语义不变**
（`agents/sound/evaluators/__init__.py:26-30` 的顺序是既有断言面）；④ 顶层段而非塞进 `evaluator_weights`：
权重是**数值面**，把实现引用混进去会让一一对应校验**失去独立校验面**（自己对自己）。

**两形态 `evaluators` 段**逐字相同**（关键兼容结论）**：`movie` 与 `shortdrama` 的评估器组合相同（既有断言
`tests/unit/test_form_switch.py:161` 的 `set(movie_w) == set(short_w)`），且既有评估器的参数**不搬迁**
（决策 2：`params` 全为 `{}`）⇒ 两形态的 `evaluators` 段**逐字节相同** ⇒ 顶层差异集断言
（`tests/unit/test_form_switch.py:341-379`、`tests/contract/test_pilot_contracts.py:434-453`）**一字不变**。
该结论**常驻机检**（新增形态无关断言：`movie["evaluators"] == shortdrama["evaluators"]`）。

**被否决**：
- *`entry_points`（打包元数据注册）*：本仓不打包、不发布，`entry_points` 的可用性由**安装状态**决定；
  更要命的是它违背规格裁决 1 的"**插件目录/安装元数据不决定可用性、配置声明才决定**"；
- *装饰器 / 导入即自动注册*：可用性退化为"谁被导入过"，与 `core/evaluators/registry.py` 的"显式注册、
  同键拒绝"纪律冲突，且"目录里存在即生效"正是规格明文禁止的形态；
- *扫描 `core/evaluators/plugins/` 目录自动装配*：同上（目录决定可用性）；
- *把插件清单塞进 `evaluator_weights.<agent>` 的值里*（例如 `{id: {weight: 0.3, impl: ...}}`）：会让评分
  权重与实现引用耦合，`core/evaluators/weights.py:18` 的取值域（数值或 `gate`）被迫放宽 ⇒ 削弱既有门禁；
- *新增第七个"插件注册表"配置文件*：多一份权威面 ⇒ 与"配置声明集 = 全集"冲突，且孤儿配置无人校验。

**影响面**：新增 `core/evaluators/plugin.py`、`core/evaluators/errors.py` 增错误类型（`errors.py:8` 的
`EvaluatorError` 之下）；`configs/movie.yaml` 与 `configs/shortdrama.yaml` 新增 `evaluators` 段；六个装配
函数改委派（签名与返回形状**不变**，`agents/*/loop.py`、`ops/*.py` 的 30 余处调用点**零改动**——调用点清单
见 `specs/021-form-plugin-validation/spec.md:114` 的"不得改动既有 Agent 装配函数"与 plan 阶段 A1）；
契约落点 `contracts/plugin-config.md` **C1**（声明段形状与解析）、**C2**（唯一装配点与调用约定）、**C3**（插件目录
与"声明才生效"）。**装配入口签名（定名）**：`parse_manifest(document, agent, *, slots)`（`slots` = **业务侧**
`SLOT_LAYOUT` ⇒ `core/evaluators/plugin.py` 保持**零 Agent 名、零形态名**）与
`assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None)`。

## 决策 2：通用参数通道——`params` 承载**新增**参数；既有参数**不搬迁**，由 Agent 绑定工厂读取

**问题**：第二处缺口（`specs/021-form-plugin-validation/spec.md:94`）：评估器参数没有通用通道——参数经各 Agent
自己的配置 dataclass 逐字段读取并校验（先例 `agents/visual/config.py:24` 的 `_require_judge`、`:54` 的
`from_dict` 逐键 `_require`；`agents/sound/evaluators/__init__.py:26-30` 直接取
`config.loudness` / `config.av_sync_threshold_ms` / `config.asr["cer_cap"]`）。⇒ 新评估器要新参数，今天就
**必须**改既有 `agents/<agent>/config.py` 与装配函数（**越界**）。

**决策**：

1. `params` 是**唯一的新增参数通道**：装配点的调用约定为
   **`impl(**params, **注入槽位)`（纯关键字注入，不用位置参数）**；`params` 的键名即目标签名里的参数名。
2. **注入槽位**固定且**形态无关**，共四个：`agent_config`（该 Agent 已解析的配置对象）、`gateway`
   （`core/llm_gateway/gateway.py:147` 的 `LLMGateway`）、`artifacts`（工件库句柄）、`registry`
   （`core/evaluators/registry.py:12` 的 `Registry`）。**按签名 opt-in**：装配点只传目标签名**声明了**的槽位。
3. **既有评估器的既有参数不搬迁**：每个既有评估器在 `agents/<agent>/evaluators/plugins.py`（**新文件**）里
   有一个薄工厂，例如 `def loudness_compliance(*, agent_config) -> Evaluator:
   return LoudnessComplianceEvaluator(agent_config.loudness)`；其声明面 `params: {}`。理由：既有参数留在
   `configs/*.yaml` 的**原段**（`sound.loudness` 等）里 ⇒ **单一事实源、零拷贝**。
4. **严格性规则（"缺声明即报错、不取码内默认"的机检形式）**：impl 目标签名中**无默认值的参数名集合**，
   必须**恰好等于** `params` 的键集 ∪ 被注入的槽位名集（多一个 / 少一个 ⇒ 装配期报错）。⇒ ① 漏声明即报错；
   ② 在插件路径上**码内默认值不生效**（声明了带默认值的参数即报错 ⇒ 逼着实现方去掉默认值或把值搬进声明面）；
   ③ 声明面写了一个签名不接收的键也报错（防"声明了没人用"）。
5. **新形态的新插件**：直接在自己的 `params` 里写字面量（`impl` 指向通用插件工厂，落
   `core/evaluators/plugins/`，见裁决 2）⇒ **零改动**任何既有 `agents/<agent>/config.py`。

**依据/理由**：① `impl(**params, **槽位)` 这一条统一了"类是插件"与"工厂是插件"两种写法（类也是
callable），所以既有 30 个评估器**不需要改构造签名**就能成为插件目标（决策 4 会说明为什么"不能改"是硬
约束）；② 槽位而非"把整份配置喂进去"：槽位是**注入面**，白名单固定 ⇒ 插件拿不到它没声明的环境
（"插件不得自行取数"，FR-002）；③ 既有参数不搬迁是为了**避免双事实源**——`tests/unit/test_form_switch.py:166-169`
读的是 `SoundConfig.from_yaml(...).av_sync_threshold_ms`，若装配面另有一份 params 值，两侧漂移即**假绿**；
④ "既有参数经工厂读配置对象"**不**违反 FR-001——FR-001 的诉求是"**新参数**零改动既有配置类"，不是
"既有参数必须搬进新键"。

**被否决**：
- *把既有参数逐值拷进 `params`*：双事实源（同一阈值在配置里出现两处），且 `judge.prompts` / `pacing_baseline` /
  `anchor_gen_params` 这类**大结构**会被整份复制 ⇒ 配置膨胀、漂移不可检；
- **改既有评估器的构造签名**（例如统一成 `(params: dict)`）：**致命**——评估器版本号把**调用方实现文件的
  字节**并入哈希（`agents/sound/evaluators/_versioning.py:12` 的 `implementation_version`、
  `:22-23` 的 `hasher.update(Path(caller_file).read_bytes())`），改文件即改版本 ⇒ `eval_breakdown` 键变化，
  直接违反 FR-013 的"逐字节不变"（详见决策 4）；
- *`params.<name> = {from_config: "<配置路径>"}` 参考型 DSL*：多一套解析规则（路径语法、缺失语义、跨段
  读取），且**对象型参数**（`agents/dev/evaluators/__init__.py:47` 的
  `SimulatedSignalSource(SOURCE_BOX_OFFICE, config.signals)`）仍需一个工厂 ⇒ 等于两套机制并存；
- *让装配点按 `plugin_id` 分派到"每 Agent 一个总工厂"*：总工厂内部的 `if plugin_id == ...` 就是**第二注册面**
  （新增插件仍要改代码），与决策 1 的"声明即注册面"矛盾；
- *用 `inspect.signature` 静默补齐 / 忽略未知键*：会让"缺声明即报错"退化为"能跑就行"，SC-005 的
  "100% 报错"失去牙齿。

**影响面**：新增 6 个 `agents/<agent>/evaluators/plugins.py`（薄工厂，一评估器一函数）；六个装配函数改委派；
既有评估器实现文件**零改动**；契约落点 `contracts/plugin-config.md` **C2**（唯一装配点与调用约定、参数通道）、**C3**（插件目录与"声明才生效"）。

## 决策 3：`version` 显式声明 = **校验一致**，**不以声明覆盖实现**

**问题**：规格要求 `version` **必须显式声明**（裁决 1；沿用 010 的 `id@version` 冻结口径，
`core/evaluators/base.py:42`）。但既有评估器的版本号是**派生值**：`1.0.0+<实现文件与口径参数哈希前 12 位>`
（`agents/dev/evaluators/_versioning.py:13`、`agents/sound/evaluators/_versioning.py:12` 同款）。声明值与
派生值的关系必须先定，否则"显式声明"会变成一场静默改写。

**决策**：声明面 `version` 与实例产出的 `spec.version` 之间只允许一种关系——**逐字相等**：

- 相等 ⇒ 装配通过（`evaluator_id@version` 即冻结标识，进 `eval_breakdown`）；
- 不等 ⇒ **装配期报错**，错误文案点名"声明的 version 与实现的 version 不一致"（不静默取任一侧）；
- 辅助工具 `ops/form_plugin.py sync-versions --check|--write`：`--check`（**默认**）只报差集不回写；
  `--write` 才回写，且**只改** `evaluators.plugins.*.*.*.version` 一个键（走既有 `core/yaml_edit.py` 定点改写，
  与 019 的扩量改写同风格）。**判据永远是装配期一致性校验，不是 sync 的产物**。

**依据/理由**：① 既有版本号是"口径即版本"的**唯一实现**（改实现文件或改口径参数即新版本）——它是 010/012/017
等特性的证据基础（漂移 `detector_version`、无偏性对照都靠它）；② 若允许声明**覆盖**实现，配置就能**谎报**
版本：把实现改了、声明不动，历史得分看起来仍可复现 ⇒ 原则一"版本冻结"的可证伪性当场崩塌；③ 若只声明
`base`（`1.0.0`）不声明哈希，版本冻结退化为"基础号"，同样使"任何行为变更必须升版本号"不可机检；
④ 显式声明的**收益**真实存在：版本从"代码派生、配置不知情"变成"配置里可见的冻结点"——评审者只看配置就能
枚举该形态用到的全部 `id@version`（这恰是 FR-011 的场景 5 与原则一在形态配置面的体现）。

**被否决**：
- *声明覆盖实现*：可谎报（见理由 ②）；
- *只声明 base 号、运行时拼哈希*：版本冻结退化为不可机检；
- *声明可省略、装配时回填*：FR-001 明文"缺声明即装配期报错、不取码内默认"；
- *用 `sync-versions --write` 作为 CI 门禁（改配置来通过）*：那是"以改写权威配置换取绿灯"，与"不得为过
  断言而改写既有取值"同源被禁——门禁只跑 `--check`。

**影响面**：配置面 30 条 `version`（六个 Agent × 各自评估器数：visual 5 / dev 4 / screenplay 7 / storyboard 5 /
sound 4 / editing 5；按 `agents/*/evaluators/__init__.py` 逐个装配函数枚举）；`core/evaluators/plugin.py` 的
一致性校验；`ops/form_plugin.py sync-versions`；契约落点 `contracts/plugin-config.md` **C4**（`version` 显式校验与"不允许覆盖实现"）。

## 决策 4：既有评估器实现文件**零改动**——这条约束决定整个机制的落点

**问题**：FR-013 要求改造后既有两形态（`movie` / `shortdrama`）的评估器组合、权重键集、`eval_breakdown`
与得分**逐字节不变**（`specs/021-form-plugin-validation/spec.md:124`）。而评估器版本号是这样算出来的
（`agents/sound/evaluators/_versioning.py:12`）：

- `hasher.update(Path(__file__).parent.parent.name.encode())`（包名，**只是名字**）；
- `_version_with_caller` 里 `inspect.stack()[2].filename` = **调用 `implementation_version` 的那个文件**，
  即评估器**实现文件本身**，并把它的**全部字节** `hasher.update(Path(caller_file).read_bytes())`
  （`agents/sound/evaluators/_versioning.py:22-23`），再加口径参数。

⇒ **改一个字节的评估器实现文件（哪怕是改注释、加一个空行）就会改版本号 ⇒ 改 `eval_breakdown` 的键 ⇒
违反"逐字节不变"。**

**决策**：本特性的机制改动**全部**落在下列位置，**不碰** `agents/*/evaluators/*.py` 的既有实现文件
（唯一例外是各包**新增** `plugins.py` 工厂文件，新增文件不改任何既有文件的字节）：

| 落点 | 文件 | 是否影响既有版本哈希 |
| --- | --- | --- |
| 唯一装配点 | `core/evaluators/plugin.py`（新）、`core/evaluators/errors.py`（加错误类） | 否（`core/` 不参与哈希） |
| Agent 绑定工厂 | `agents/<agent>/evaluators/plugins.py`（**新文件** × 6） | 否（新文件不是任何既有评估器的 caller） |
| 装配函数 | `agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、`agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、`agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27` 的**函数体** | 否（`__init__.py` / `loop.py` 不是任何评估器的 caller；包名哈希只取 `Path(__file__).parent.parent.name` = 包名字符串，与本文件内容无关） |
| 形态配置 | `configs/movie.yaml`、`configs/shortdrama.yaml` 新增 `evaluators` 段 | 否（参数不搬迁，评估器行为不变） |
| 守卫/登记/CLI | `ops/form_guard.py`、`ops/form_onboarding.py`、`ops/form_plugin.py`（新）与既有测试/夹具 | 否（`ops/` 与 `tests/` 不参与哈希） |
| 通用插件目录 | `core/evaluators/plugins/`（新，B 阶段） | 否 |

**机检（本决策的举证）**：机制改造前后各导出一份"两形态装配序列"快照
`[(slot, evaluator_id@version), ...]`（含顺序），逐条比对**逐字相等**；同时沿用既有逐字节断言
（`tests/contract/test_pilot_film_contracts.py` 的包面成本/账目机检、
`tests/contract/test_calibration_contracts.py` 的"权重生效后历史节点逐字节一致"）继续常驻。

**依据/理由**：① 这是"FR-013 的逐字节不变"与"机制必须补"这两个要求**同时**成立的唯一路径——任何"顺手把
评估器构造函数统一一下"的重构都会打穿版本哈希；② 反过来看，这条约束也是**好事**：它把"机制改动面"钉在
**新增文件 + 装配函数体**上，使"机制侧总账"的改动清单**可枚举、可评审**（FR-013 的六项总账逐项落在
上表内）。

**被否决**：
- *把版本号算法改成不含实现文件字节*（让版本与文件内容解耦）：那会把"口径即版本"降级为人工维护，且
  010/012 的既有证据（`detector_version`、漂移判定口径）全部失去一致性依据；
- *改评估器文件并同步改配置里的 `version` 声明*：版本号变了 ⇒ 历史 `eval_breakdown` 不可复现（原则一），
  且既有断言面（大量 `startswith` / 逐字断言）会大面积变红——**不是"按扩展更新"，是改写历史**；
- *保留硬编码装配、插件机制只服务新形态*：造出**两条装配路径**（旧 Agent 一批、新形态一批），违反 FR-012
  的"唯一装配点是 `impl` 的唯一解析点"，且"零分支"守卫会立刻在新旧两条路径的差异上失去意义。

**影响面**：上表；契约落点 `contracts/plugin-config.md` **C4**（`version` 一致性校验与不允许覆盖实现）+ `contracts/onboarding-ops.md` **C13**
（机制侧总账与接入改动分离的举证）。

## 决策 5：形态名一律由 `configs/*.yaml` 派生；三份副本收敛为**单一实现**（`ops/form_guard.py`）

**问题**：形态名今天写在**人工常量**里，三份副本：`tests/unit/test_form_switch.py:413` 的
`BANNED_LITERALS = ("shortdrama", '"movie"', "'movie'")` 与 `:414` 的 `BANNED_PATTERNS`、
`tests/unit/test_billing_core_purity.py:34-35`、`tests/unit/test_dev_core_degraded_purity.py:28-29`。⇒ 新形态名
**天然逃逸**（常量表里没有它）；且"恰好两份"的同类枚举另有五处（决策 8 的清单）。规格裁决 3 要求"一律由
`configs/*.yaml` 的 `form:` 自动派生、禁止任何人工常量清单、副本数 ⇒ 1"（`specs/021-form-plugin-validation/spec.md:29`）。

**决策**：新增**单一实现** `ops/form_guard.py`（业务无关的静态守卫模块），对外只暴露派生面与判定面：

- `declared_forms(configs_dir) -> tuple[str, ...]`：**只**读全部 `configs/*.yaml` 的 `form:` 取值（**id 面**，
  不含别名），**两两唯一**、非空字符串；缺 `form:` / 值非字符串 / 重复 ⇒ **报错**（派生失败即报错，不静默跳过）；
- `form_literals(configs_dir) -> tuple[str, ...]` / `form_branch_patterns() -> tuple[str, ...]`：**名称面** =
  id 面 ∪ 全部 `form_aliases` 项；由 `declared_forms()`
  派生（**零人工形态常量**；`BANNED_PATTERNS` 的六个模式是**形态无关的语法模式**，可留作常量）；
- `iter_sources(roots=("core", "agents"))`：扫描面 = `core/` + `agents/` **全覆盖、含 `agents/pilot`**
  （把 `tests/unit/test_form_switch.py:423` 的 `if "pilot" not in path.parts` 这条排除**补掉**）；
- `literal_violations()` / `branch_violations()`：返回 `(相对路径, 所属符号名, 行号, 命中内容)`（**锚点以符号名
  为准**，见决策 6）；
- `classify_exception(hit) -> str | None`：例外三条的判定（决策 6）。

**形态名的两个面（定名，不得混用）**：`form:` 只能给英文 id。⇒ 守卫对外分**两个函数**：`declared_forms(configs_dir)`
（**id 面**：`configs/*.yaml` 的 `form:` 取值，**两两唯一**；决策 7 的 `declared_forms() ⊆ registered_forms()`
**只在 id 面上成立**）与 `form_literals(configs_dir)`（**名称面** = id 面 ∪ 全部 `form_aliases` 项，专供**字面量
扫描**使用）。中文别名与英文同义词由**每份形态配置的别名键 `form_aliases`** 声明（键名与取值域以
`contracts/zero-form-branch.md` C6 为权威）；该键**必须在每份形态配置中存在**（可为显式空列表——缺键即报错，
不留空）。**不得用名称面做登记完备的集合比较**（中文别名没有对应的 `configs/<别名>.yaml`，用名称面会让
`registered_forms()`  ⊆ `declared_forms()` 恒假）。

**配置文件命名纪律（登记点⑤反查的前提）**：`configs/<id>.yaml` 的**文件名 stem 必须等于**该文件的 `form:`
取值——这样 `tests/conftest.py` 的 `pilot_form_config_path(form)` 才能在**零人工常量**下由形态 id 反查到唯一
配置路径（`configs/{form}.yaml`）；"stem 与 `form:` 不一致"即报错（属登记完备机检的一部分，落 C10）。

**位置理由**：放 `ops/` 而不是 `tests/`（CLI 的 `guard` 子命令与"接入改动清单"都要用同一实现；`tests/` 不应
成为 `ops/` 的依赖）也不是 `core/`（守卫必然要读 `configs/` 的形态概念，`core/` 必须业务无关，
宪章 `.specify/memory/constitution.md:117-118`）。`ops/` 被测试直接导入有充分先例：
`tests/unit/test_audit.py:10`（`ops.audit_immutable`）、`tests/unit/test_check_credentials.py:19`
（`from ops import check_credentials`）。

**三份副本的收敛方式（**保住断言语义**的关键）**：**委派**，不搬迁——三处既有测试的**循环体与断言体原位保留**，
只把"形态名常量"改为从 `ops/form_guard.py` 取：

```python
# tests/unit/test_billing_core_purity.py（③ 既有断言体一字不改，只换常量来源）
from ops.form_guard import form_branch_patterns, form_literals
FORM_LITERALS = form_literals()      # 原先 = ("shortdrama", '"movie"', "'movie'")
FORM_PATTERNS = form_branch_patterns()
```

⇒ 三处的 `test_无形态字面量与形态分支`（`tests/unit/test_billing_core_purity.py:145`）、
`tests/unit/test_dev_core_degraded_purity.py:101` 与 `tests/unit/test_form_switch.py:421`/`:431` 的**断言全部
保留原位**，且**语义只增不减**：派生面比原常量表**更严**（原表只禁 `"movie"`/`'movie'`，派生面禁**裸词**
`movie`——今天 core/+agents/ 只有两处命中，见决策 6 的例外与收敛项）。

**依据/理由**：① "派生失败即报错"是必需的：若某份配置忘写 `form:` 而派生面静默跳过，新形态就又能逃逸
（SC-003 的"人工常量清单数恒为 0"与"派生失败即报错"两句话合起来才构成可证伪的守卫）；② 单一实现 + 委派是
"副本数恒为 1"与"断言语义只增不减"**同时**成立的唯一形式——把三处合并成一处（删掉两处）就是规格明文禁止的
"借收敛之名删断言"（裁决 4，`specs/021-form-plugin-validation/spec.md:30`）。

**被否决**：
- *保留三份副本、"以后一起改"*：副本数 ⇒ 1 是 SC-003 的机检项，且历史证明副本必漂移（020 已登记过一次
  引用漂移，`specs/020-shortdrama-real-feedback/tasks.md:692`）；
- *把派生面放进 `core/`*：`core/` 要保持业务无关（形态概念属配置面）；
- *放进 `tests/conftest.py` 夹具*：CLI 与本特性的"接入改动清单"机检要用，夹具不可被 `ops/` 复用；
- *在守卫里保留一个"默认形态清单"兜底*（`configs/` 为空时用）：等于人工常量清单复活，且"配置缺失即静默
  全绿"是最坏的假绿。

**影响面**：新增 `ops/form_guard.py` 与 `tests/unit/test_form_guard.py`（自检/有牙齿）；三处既有测试改常量
来源（断言体不动）；`contracts/zero-form-branch.md` **C5**（字面量层扫描面）、**C6**（形态名派生）、
**C7**（三副本委派收敛）。

## 决策 6：扫描面锚点以**符号名**为准；例外三条的机检边界

**问题**：020 登记扫描面时写的是**行号**（`tests/unit/test_form_switch.py:309`/`:310-312`/`:321`），而今天
该区间是 `test_竖屏规格`（`:308-326`），真正的扫描面在 `:421-429`/`:431-436` ⇒ **引用已漂移**
（`specs/020-shortdrama-real-feedback/tasks.md:424`/`:692` 已登记该教训）。规格裁决 4 要求锚点**以符号名
（函数名/类名）为准**，并给例外**只允许三条**（`specs/021-form-plugin-validation/spec.md:30`）。

**决策**：

1. **锚点 = 符号名**：违规定位三元组 `(相对路径, 所属符号名, 行号)`；符号名由 AST 求"包含该命中点的
   `FunctionDef` / `ClassDef`"（模块级 ⇒ `<module>`）；行号只作人读辅助。既有断言在文档与机检里按**符号名**
   引用：字面量扫描面 = `tests/unit/test_form_switch.py` 的
   `Test零形态分支静态断言.test_core_与_agents_无形态字面量`（`:421`）+ 020 新增的
   `test_渠道解析不得出现形态字面量或形态判断`（`:443`）；分支扫描面 =
   `test_全仓_agents_与_core_无形态判断分支`（`:431`）。
2. **例外三条的机检边界**（逐条可判、可自检）：
   - **E1 配置 `文件路径` 字面量**：命中所在字符串同时含 `configs/` 与 `.yaml`，且形态名**仅**作为路径片段
     出现。先例（也是今天唯一命中）：`core/deployment/evidence.py:97` 的
     `source="configs/movie.yaml deployment.gate.require_unbiasedness=false"`；
   - **E2 测试夹具**：`tests/**` **不在扫描面**（这是**扫描面定义**，不是逐条豁免——扫描面只有
     `core/` + `agents/`）。`ops/` / `web/` / `dreaming/` 的既有配置路径默认值同理**不在**扫描面内、也
     **不得**为过断言而改写（`web/server.py:320`、`web/export.py:301`、`dreaming/deploy_hook.py:25`、
     `ops/billing.py:97`、`ops/ingest_metrics.py:121`；`ops/demo_merged_pool.py:55` 与
     `ops/smoke_llm.py:358` 的演示形态值同理）；
   - **E3 docstring 中性描述**：命中落在模块/类/函数 docstring 内，**且该命中行不含** `=`、`⇒`、`→` 或任一
     分支模式——即"只列举/说明、不绑定取值"。
3. **`agents/pilot/pilot.py:613` 的裸形态词必须收敛、不得适用例外**：该行是
   `（movie ⇒ 5400 s）`——恰恰**违反** E3 的界定（把形态名绑定到取值）。收敛方式：改为中性措辞（去掉形态名，
   表述为"形态原值：`screenplay.target_duration_min × 60 == editing.target_duration_s`"），并保留其后
   "任一不一致即拒绝启动并点名两处实测值"的既有语义。**不碰**同函数的逻辑。
4. **判定口径**：ASCII 形态名按**词边界**判定（`(?<![0-9A-Za-z_])<name>(?![0-9A-Za-z_])`），中文别名按
   **子串**判定。先例与理由见 `tests/unit/test_billing_core_purity.py:54-56`（标识符边界判定）与
   `:222-224`（`dev` 不得误命中 `deviation`）：新形态 id 可能是短词（如 `ad`），子串判定会命中 `read` /
   `load` / `head` 一类常见标识符，**假阳性会逼出无意义的改名**，而边界判定仍能捉住 `"movie"` / `'movie'` /
   `configs/movie.yaml`（引号、`/`、`.` 都是边界）。
5. **"有牙齿"的自检常驻**：`tests/unit/test_form_guard.py` 用**派生值**合成三类反例——① 裸字面量写在
   `core/` 某文件（判违规）；② `f'if form == "{forms[0]}"'` 写在 `agents/pilot/backends.py`（判违规，同时举证
   "装配点被覆盖"）；③ docstring 里 `f"（{forms[0]} ⇒ 30 s）"` 形式的取值绑定（判违规）。
   先例：`tests/unit/test_billing_core_purity.py:210-224` 的合成源码自检。**注意**：合成字面量必须由派生值
   构造 ⇒ 守卫模块与测试文件本身**零人工形态常量**（否则它们自己就成了第五处常量表）。

**依据/理由**：① 行号锚点已被证明会漂移，而符号名是**语义锚点**（改名会同时让机检失效——但那属于"守卫被
改动"，会在评审里可见）；② 例外只剩三条且各自带**可判定的谓词**，是"零字面量"能长期站住的前提：例外越多，
守卫越快退化为注释（020 的 `:423` 排除 `agents/pilot` 就是一条**过宽的例外**造成的盲区）；
③ 词边界判定是"不制造假阳性"的关键——假阳性会诱使实现者去改**无关代码**讨好守卫，那是最坏的副作用。

**被否决**：
- *只引行号*：已漂移（020 教训）；
- *按文件白名单豁免*：等于给 `agents/pilot` 再开一次口子（020 的 `:423` 正是此类）；
- *对 `agents/pilot/pilot.py:613` 加 docstring 例外*：规格裁决 4 明文"不得为它加例外"；
- *子串判定一刀切*：短 id 假阳性爆炸（见理由 ③）；
- *把扫描面扩到 `ops/` / `web/` / `dreaming/`*：那会让 5+ 处既有配置路径默认值变红，逼出"为过断言改写既有
  默认值"——规格边界情况明文禁止（`specs/021-form-plugin-validation/spec.md:97`）。

**影响面**：`ops/form_guard.py`、`tests/unit/test_form_guard.py`、`agents/pilot/pilot.py:613`（仅 docstring
措辞）、三处委派点；契约落点 `contracts/zero-form-branch.md` **C5**（字面量层扫描面与例外三条）、**C8**（裸形态词收敛与例外登记）。

## 决策 7：既有"`configs` 恰好两份"→**登记完备**口径

**问题**：`tests/unit/test_form_switch.py:438-441` 断言 `configs == ["movie.yaml", "shortdrama.yaml"]` ⇒ 新增
第三份配置**必红**（`specs/021-form-plugin-validation/spec.md:99`）。规格裁决 5 要求改为"登记完备"口径并
**禁止简单删除**（`specs/021-form-plugin-validation/spec.md:31`）。

**决策**：该断言改为三条并列（实现委派 `ops/form_guard.py` + `ops/form_onboarding.py`）：

1. **两两唯一**：`configs/*.yaml` 的 `form:` 取值数 == 配置文件数（重复即红）；
2. **配置集合 ⊆ 登记派生集**：`declared_forms()`（来自 `configs/`）⊆ `registered_forms()`（来自**登记面**：
   `tests/conftest.py:2854` 的夹具形态集合改为派生后、与 `tests/unit/test_config_integrity.py:153-154` 的
   参数化面一致）；两者不等即红；
3. **下界保留**：配置数 ≥ **2**（**不**提高到 3——下界若写 3，就把"机制可用"与"本次接入了几个形态"耦合，
   违反 FR-013 的"机制与接入分离"）。

**②为什么非平凡**：它把"派生面"与"登记面"绑在一起。若某处登记点退回人工枚举（例如有人在 `tests/conftest.py`
重新写死两形态元组），②立刻变红。**有牙齿**：用例注入"一份临时第三形态配置 + 一处人工枚举"的组合，断言 ②
必红（自检不得空跑）。

**依据/理由**：① 原断言的原意是"形态以配置文件为唯一载体、代码侧无形态枚举/映射表"——三条并列**完整保留**
该原意（唯一性 + 派生一致 + 下界），并把它从"恰好两份"这个**数量**形状升级为"登记完备"这个**结构**形状；
② 下界必须保留：否则把 `configs/` 清空（零份配置）也能"通过"，守卫会失去牙齿；③ 禁止删除是规格明文：
删掉即等于把"形态载体"这条纪律让位。

**被否决**：
- *保留"恰好两份"*：新形态必红，等于用守卫阻断本次交付（且那会诱使实现者删断言）；
- *改为"≥2 且代码侧无形态枚举"*：后半句不可机检；
- *下界提到 3*：把机制与本次接入数量耦合（本特性 B 阶段恰好接入两形态 ⇒ 表面可过，但机制面会因此带上
  "必须至少三个形态"的伪约束）；
- *改为"配置数 >= 已登记形态数"*：两个派生面同源（都来自 `configs/`）⇒ 恒真，空断言。

**影响面**：`tests/unit/test_form_switch.py:438-441` 改委派；`ops/form_guard.py` / `ops/form_onboarding.py`
提供实现；契约落点 `contracts/form-registration.md` **C10**（登记完备口径：三条件 + 下界 ≥2）与 **C9**（五处登记点改配置/形态派生）。

## 决策 8：五处登记点从"两形态硬编码"改"配置/形态派生"（逐处改法 + 逃逸风险）

**问题**：五处今天的形状是**两形态硬编码枚举**，而没有一处能自动容纳第三形态（
`specs/021-form-plugin-validation/spec.md:98` 逐处点名）。⇒ 新形态在 ①②③ 处**静默逃逸**（不报错、门禁不覆盖），
在 ⑤ 处**硬失败**（`tests/conftest.py:3072` 对未知形态直接 `raise ValueError`）。

**决策（逐处改法）**：

| # | 登记点（符号名优先） | 今天的硬编码 | 改法 | 逃逸风险处置 |
| --- | --- | --- | --- | --- |
| ① | `tests/unit/test_form_switch.py` 的 `Test差异逐项可归因.test_全量差异都被配置文件承载`（`:341-379`）与模块级 `FORMS`（`:30`） | 固定两形态 + 固定 **15 键**差异集 | `FORMS` 改由 `declared_forms()` 派生；**movie×shortdrama 那条固定差异集断言原位保留**；**新增**"逐形态对"常驻断言：每对形态的顶层差异集**非空**、必含 `form`、必**不含** `web` / `cost_regression`（形态无关基建段逐字相同） | 新形态自动进入逐对断言；"差异靠值不靠删段"的性质对第三形态同样成立 |
| ② | `tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES`（`:23-40`）/ `REQUIRED_PATHS`（`:48-98`）/ 参数化面（`:153-154`） | `SHORTDRAMA` / `MOVIE` 两个模块常量 + 两形态参数化 | 配置集合改由 `declared_forms()` 派生（每份 `configs/*.yaml` 都跑**全部加载器**与**全部"缺项即红"条目**）；新增 `evaluators` 段的加载器条目与必需键条目（**新增加载器名**：`core/evaluators/plugin.py` 的清单解析器） | 新形态自动被全部加载器与全部缺项条目覆盖 |
| ③ | `tests/contract/test_pilot_contracts.py` 的 `test_c13_两套配置差异可归因且无形态分支`（`:434-453` 差异集；`:469-477` 的 `core/`+`agents/` 全量扫描） | 固定两形态 + 固定 **15 键**差异集；扫描用**写死的禁用元组** | 同 ①（逐对断言），扫描的禁用清单改由 `ops/form_guard.py` 派生（该处扫描面**本就覆盖 `agents/pilot`**，与决策 5 的补面口径一致） | 该处是今天**唯一**已覆盖 pilot 的扫描面；改为共用实现后两处口径**必然一致**（消除"两处扫描面口径分叉"这一既有隐患） |
| ④ | `agents/pilot/pilot.py` 的 `config_completeness`（`:377`，调用点 `:507`） | **按配置路径通用**（对新形态无需改代码即生效）——本特性最靠得住的一处 | **不改其通用性**；**追加** 020 口径逐项机检（`form_clause_completeness`，与 `config_completeness` 同模块、由它收口调用）：cadence ∈ `{1,7}`、`window_semantics` 取值域单元素、`window_semantics_change_date` 非空、`budget.channels.<id>.tiers` 非空且档位不跨渠道串用、`promo.attribution_date_required_since` 存在、`calibration.transfer` 六键齐备、"不适用"必须显式声明（禁留空）；**并把 `evaluators` 段清单解析器加进预检清单**（与 020 把 `transfer` 加进预检同款：缺段即拒绝启动，否则"漏声明插件清单"会静默逃逸） | 新形态缺任一项 ⇒ **拒绝启动**（不取码内默认）；返回段清单随之扩展（`config_completeness` 的返回元组变长） |
| ⑤ | `tests/conftest.py` 的 `PILOT_FORMS`（`:2854`）与 `pilot_form_config_path`（`:3054-3079`，未知形态 `:3072` 直接报错） | 写死两形态；`movie` 用精简副本 `_MINIMAL_MOVIE_CONFIG`（`:3196`）、`shortdrama` 用真实配置派生副本 | `PILOT_FORMS` 改由 `declared_forms()` 派生；`pilot_form_config_path(form)` 对**任意已声明形态**返回**该形态真实配置的派生副本**（只改 `budget.ledger.root`，既有派生点断言 `assert "root: billing" in source` 保留）；未知形态仍然报错（保留该行为：派生面之外的形态就是未知形态） | 新形态自动可用（不再硬失败）；`_MINIMAL_MOVIE_CONFIG` 继续承担"精简副本"职责（它是形态**无关性**的举证面，不属形态枚举） |

**"不新造第六处"的机检**：`ops/form_onboarding.py` 维护 `REGISTRATION_SITES`（**常驻白名单**，恰好五处，含
每处的符号名与判定函数），并**反向扫描**仓库中"形态清单被枚举/写死"的代码点——即出现"两形态元组字面量"、
"形态→配置路径映射"或 `configs/{form}.yaml` 硬编码拼装的模块级常量——断言其集合 == `REGISTRATION_SITES` 的
对应面（新增一处即红，须显式登记；与
`tests/unit/test_billing_core_purity.py:268-285` 的 `OFFLINE_ASSEMBLIES` 常驻清单同款纪律）。

**同族"两形态枚举"副本的逐处处置**（规格边界情况第 7 条末点名）：
`tests/unit/test_form_switch.py:30`、`tests/unit/test_billing_core_purity.py:31`、`tests/unit/test_billing_channels.py:52`、
`tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`
——**逐处改为 `declared_forms()` 派生**，断言体不删。其中 `test_pilot_rehearsal.py:34` 的用例含形态特定取值
假设（排练档与形态原值对照），若某条断言在第三形态下语义不成立 ⇒ 按"**逐形态声明期望值**"扩展（把期望值
搬进配置或建"形态 → 期望"的显式登记），**不得**删除断言、**不得**把该形态从派生面里排除。

**依据/理由**：① 五处登记点是 020 建立、本特性**复用不新造**的资产（`specs/019-real-channel-billing/quickstart.md:99`
的"五处全部落地"原文），"复用"的正确形式是**把三处硬编码改成派生**，使"漏登记"从静默逃逸变成报错；
② ①③ 两处的差异集断言是 015 的**核心证据**（同链双形态、差异逐项可归因）——对第三形态泛化时必须**保留**
原对的强断言，避免"泛化即弱化"；③ ④ 处的通用性要**显式保住**（它是"新形态无需改代码即生效"的现场证据），
并在其上加 020 口径的完备机检，使"新形态必须声明全部 020 口径"（FR-008）有唯一收口点。

**被否决**：
- *新造第六处登记点（例如新增一份"形态注册表"文件）*：规格明文禁止（FR-007/FR-012），且第六处会立刻成为
  新的漂移源；
- *把 ①③ 的固定差异集断言换成"逐对派生 + 弱断言"*：会丢掉 015 的强证据 ⇒ 属"借泛化之名削弱"；
- *让 `pilot_form_config_path` 对新形态返回 movie 的精简副本*：等于让门禁在**错误前提**下通过（假绿），
  比变红危险得多（020 决策 10 第 13 项同款论证）；
- *把 020 口径完备机检另立一个 CLI（不挂 `config_completeness`）*：会造成"两个预检口径"，且 `specs/021-form-plugin-validation/spec.md:86`
  明确把 `config_completeness` 定为缺项收口点。

**影响面**：五处登记点文件 + `tests/unit/test_pilot_chain_seven.py:118-121`（`config_completeness` 返回段清单
变长 ⇒ 按扩展更新）+ `ops/form_onboarding.py`；契约落点 `contracts/form-registration.md` **C9/C10**（C9 = 五处登记点改配置/形态派生；C10 = 登记完备口径）。

## 决策 9：接入改动清单机检的**基线**与判定规则（越界即红）

**问题**：G5 验收原文是"**仅**新增配置 + 评估器插件"——"仅"这个字只有靠改动清单才可证伪
（`specs/021-form-plugin-validation/spec.md:77`）。同时 FR-004 要求"本特性自身的机制侧总账改动**不**计入接入
改动"（`:115`）——基线取错，判据自相矛盾。

**决策**：

1. **基线 = 形态插件机制落地后、新形态接入前的提交**（`--baseline <ref>` 必填）。实现取法：改动集合 =
   `git diff --name-status <ref>`（含工作区未提交改动），**直接由 git 派生**，不手工列举 ⇒ 与 git 实际改动集
   **一致率 100%** 由构造保证（"自报漏项"在机制上不可能发生）。
2. **判定按类别而非路径前缀**（FR-003 明文）：
   | 类别 | 判定 | 结论 |
   | --- | --- | --- |
   | 配置 | **新增** `configs/*.yaml` | 放行 |
   | 插件 | **新增** `core/evaluators/plugins/**`（业务无关通用件）或 `agents/<agent>/evaluators/**`（语义与某 Agent 绑定时，且**仍须经 `impl` 声明**才生效） | 放行 |
   | 测试与文档 | **新增或修改** `tests/**`、`docs/**`、`specs/**` | 放行（含登记点同步与文档） |
   | 越界 | **任何既有文件的修改或删除**位于 `core/` / `agents/` / `ops/` / `web/` / `dreaming/` / `policies/`；或上述四类之外的**新增**文件（如新增 core 机制模块、新增 ops CLI） | **越界**：退出码非 0 + **逐条点名**路径与类别（不得只报总数） |
3. **"既有模块被修改"的文件数恒为 0**（SC-001①）：`tests/**`/`docs/**`/`specs/**` 之外的既有文件一律计入
   越界计数并报出。
4. **产物 append-only 且可回溯**：清单落 `--out` 目录，文件名含形态 id 与序号（写后不回改），并在
   `index.jsonl` **追加**一行：`{baseline_ref, form, config_fingerprint, change_count, violations[], exit_code}`
   其中 `config_fingerprint` = 该形态配置文件的 BLAKE3 前 12 位（沿用
   `core/orchestration/models.py:36` 的 `fingerprint_of` 口径）。⇒ "哪次接入、基于哪个基线、改了哪些文件"
   可回溯；改写既有清单行的尝试被"文件名唯一 + append-only 行"排开。
5. **机制侧总账的分离举证**（FR-013）：机制落地提交打一个可引用的 ref（或由人显式给出 `--baseline`），
   验收流程 = ① 机制支线提交 → ② 以 ① 为基线做新形态接入 → ③ 跑 `onboarding --baseline <①>` 必须
   `violations == []`。**禁止**用本特性自己的机制改动去宣称"零代码改动"（`:124` 原文）。
6. **越界取证用例**：制造一份**故意越界**的改动（例如在 `core/evaluators/composite.py` 里加一行注释），
   断言 **判越界率 100%**、退出码非 0、逐条点名该路径（SC-002）。

**依据/理由**：① "由 git 派生"是"清单与 git 一致"这条 SC 的**唯一**可靠实现（任何自算清单都有漏项空间）；
② 按**类别**而非前缀判定是规格裁决 2 的原文（`specs/021-form-plugin-validation/spec.md:28`）：`agents/<agent>/evaluators/`
下**新增**插件是放行的，而同一前缀下**修改既有文件**是越界的——前缀判定无法区分这两件事；
③ 产物 append-only + 配置指纹：可审计物必须能被独立复核（原则六"可被证伪"），
"这次接入的配置是哪一个"必须可指认。

**被否决**：
- *基线取"仓库初始提交"或 `main`*：会把机制侧总账一起算进越界（判据自相矛盾，且与实际交付顺序不符）；
- *清单靠人写/靠测试静态枚举*：一致率无法证明，且"自报漏项"恰是 FR-004 点名要排除的情形；
- *只报总数不报路径*：规格明文禁止（SC-002 的"只报总数次数恒为 0"）；
- *把 `tests/**` 的修改也计为越界*：与裁决的"改动面 = 配置 + 插件 + 测试与文档"直接冲突，且登记点同步
  必然要改测试；
- *让 CLI 自动改写配置或自动 `git commit`*：CLI 只读 git 与文件、只写自己的产物目录（`--out`），**不**触碰
  工作区（本项目的纪律：本特性的工具不得成为"改代码来过门禁"的通道）。

**影响面**：新增 `ops/form_onboarding.py` + `ops/form_plugin.py`（CLI 门面，退出码 0/1/2 与既有工具一致，
先例 `ops/transfer.py` 的退出码文档块）；契约落点 `contracts/onboarding-ops.md` **C12/C13**。

## 决策 10：cadence ∈ `{1,7}` 的显式报错落点与"不扩量纲"边界

**问题**：`core/calibration/periods.py:30` 的 `SUPPORTED_CADENCES = (1, 7)`、`:31` 的
`CADENCE_UNIT = {1: "day", 7: "week"}`、`:37-42` 的 `_check_cadence` 对其它值一律 `ValidationError`；
但**加载入口**今天并不都过这道校验：`core/calibration/config.py:149` 的 `CalibrationConfig` 只校验
`period_days` 是 ≥1 的整数（`_INT_FIELDS`），真正拦 cadence 的是 `core/calibration/drift_config.py:130-143`
与 `agents/promo/config.py:43`。规格裁决 6 要求"新形态必须声明 ∈ `{1,7}`、超出即**显式报错**、不得放宽、
不得'按周近似 + 如实标注'含糊兜底"（`specs/021-form-plugin-validation/spec.md:32`）。

**决策**：

1. **入口收口**：`CalibrationConfig.from_dict` 显式调用 `periods` 的 cadence 校验（把 `_check_cadence` 提为
   公开 `check_cadence` 或经 `period_label` 等价校验），错误文案点名取值域 —— 使"cadence 越界"在**唯一**
   的 `calibration` 段加载入口上就报错，而不是等到 drift/promo 各自那一道。
2. **预检收口**：`config_completeness`（`agents/pilot/pilot.py:377`）的 020 口径逐项机检里包含
   cadence ∈ `{1,7}`（决策 8 的 ④）⇒ 预检报告里**可见**该项已检查（本特性最靠得住的落点）。
3. **不放宽取值域**：`core/calibration/periods.py:30` **一字不改**；不新增量纲；不做"整周近似"。
4. **近似关系必须如实登记**：业务侧未确认期间，新形态在 `{1,7}` 中取**最接近真实节律**的一档，并在配置与
   产物里**显式登记该近似关系**（键名以 `contracts/form-registration.md` C11 为权威；机检要求"取了近似档却
   无近似登记"⇒ 报错）。**这**是本决策里唯一允许的"如实标注"，它与被禁止的"按周近似兜底"的区别在于：
   前者是"落在 `{1,7}` 内并声明近似关系"，后者是"用整周去代表一个非整周的真实节律并宣称已达标"。
5. **确实需要其它量纲 ⇒ 另立特性**（开放问题 2）：`docs/三期立项书.md` 的 G5 行不含量纲扩展，本特性
   **不代劳**（`specs/021-form-plugin-validation/spec.md:173`）。

**依据/理由**：① 三处校验（drift / promo / 新的 CalibrationConfig）并存但**取值域同源**（都 import
`SUPPORTED_CADENCES`）⇒ 是"同一口径的多个检查点"，不是三套口径；② 显式报错的**语义**是"拒绝启动"而不是
"回落到周级"——`specs/020-shortdrama-real-feedback/research.md:12` 的决策 1 已立此先例（"其他值显式拒绝"是
原则六的直接推论：不发明第三档量纲、不静默退化）；③ 为何不改 `periods.py`：它是 020 交付的**已冻结口径
实现**，本特性的两条硬约束（逐字节不变、既有断言只增不减）都指向"不动它"。

**被否决**：
- *放宽 `{1,7}` 以容纳新形态*：规格明文禁止，且会同时改动 020 的取值域实现与断言；
- *新形态不声明 cadence、由代码回落*：违反"缺项即报错、不取码内默认"；
- *"按周近似 + 如实标注"*：规格明文禁止的含糊兜底（它让"日级/周级"的量纲名不副实重演）；
- *只在新形态的配置注释里写"暂按日级"*：注释不是产物、不可机检（020 决策 2 已就"CHANGELOG 不是产物"作过
  同一判断）。

**影响面**：`core/calibration/config.py`（加一道 cadence 校验）、`agents/pilot/pilot.py`（020 口径机检）、
新形态配置的 cadence 声明与近似登记；契约落点 `contracts/form-registration.md` **C11**。

## 决策 11：广告/漫剧"最小可行形态"的定义与"未标定"如实标注口径

**问题**：两个新形态的**真实业务定义**（受众、指标口径、素材规格、评估器组合的业务正确性）属**业务侧输入**
（开放问题 1，`specs/021-form-plugin-validation/spec.md:172`）；未给定期间要"按最小可行形态接入并如实标注
'未标定'，不得发明"（FR-011、`:122`）。

**决策**：

1. **最小可行形态 = "预检可过 + 装配可跑 + 离线端到端退出码 0"的最小完整形态配置**，具体门槛：
   - 与既有两形态**顶层段集合一致**（`evaluator_weights` / 七个 Agent 段 / `replay` / `promo` / `calibration` /
     `budget` / `dreaming` / `deployment` / `web` / `llm` / `pilot` / `cost_regression` + 本特性新增的
     `evaluators`）——"形态差异靠值不靠删段"（015 口径，`tests/unit/test_config_integrity.py:123`），
     且这样 `config_completeness`（逐段加载）才过得了；
   - 每个 Agent **至少一条 `rule.` 门禁** + 至少一条 `proxy.` 分量（权重键集里必须有 `gate`，先例
     `tests/unit/test_config_integrity.py:144-150`），且**不声明 judge**（新形态最小档**零 LLM 花费**——
     这也让离线端到端演示"零凭证、零网络"变成结构性事实而不是承诺）；
   - 评估器组合**尽量复用**既有插件（经同一份工厂代码 ⇒ 这就是 US1 场景 3"两形态共用同一份插件代码"的
     举正面），**新增**业务无关的通用评估器**只**落 `core/evaluators/plugins/`（新目录）并经 `impl` 声明。
2. **"未标定"的如实标注（复用既有标记词汇，不发明）**：
   - 形态**层**：`pilot.rehearsal.status: unstandardized`（取值域 `agents/pilot/pilot.py:53`）——既有语义
     "不覆盖形态原值 + 如实标注未标定"，且会在预检/运行报告里出现（`agents/pilot/run_report.py:79`）；
   - 段**层**：承载业务数字的段（`promo` / `budget` / `calibration` / `pilot` / `evaluators`）必须带**非空**
     `note`，且其中必须出现"未标定"字样（沿用配置里既有 `note` 用法，先例 `configs/movie.yaml:603` 一带的
     `note: "未标定（…）：运营给定后只改本值"`）；
   - 产物**层**：接入改动清单与离线演示输出必须复现同一标注（固定字段，键名以 `contracts/onboarding-ops.md`
     C14 为权威）：`uncalibrated: true` + `uncalibrated_reason`（点名"受众/指标口径/素材规格/预算档属业务侧
     输入，未给定"）。⇒ "未标定标注缺失次数恒为 0"与"以模拟冒充标定次数恒为 0"两条 SC 才有落点。
3. **不发明**：平台名、指标口径、素材规格、预算数字、受众画像**一律不进配置**为"自有取值"——能复用既有形态
   取值的就复用并在 `note` 里标明"未标定（沿用 <形态> 现值，待业务侧给定）"；不能复用的（例如某插件需要的
   新阈值）取**机制可跑通所需的最小值**并同样标注"未标定"。**禁止**出现"看起来像业务标定值"的新数字而不标注。
4. **不做**：真实投放（B/C 路径不变，`docs/三期立项书.md:212` 的"平台侧 B/C 路径仍 `not_delivered`"口径不放宽）、
   新形态真实素材生成、多租户/公网服务化（`:122`）。

**依据/理由**：① "最小可行形态"必须有**可机检的门槛**，否则"最小"会退化为"随手写一份能过的配置"；这里把它
定义为"预检可过 ∧ 装配可跑 ∧ 离线端到端 0"三条**已有断言面**，不新造门槛；② 复用既有形态取值 + 显式标注
未标定，是"不得发明"与"配置必须完整"两条硬约束的**唯一交点**（配置的完整性由 015/018/019/020 的加载器
强制，业务正确性由标注挡在门外）；③ 形态层复用 `unstandardized` 而非新造"未标定"键：既有取值域
（`agents/pilot/pilot.py:53`）已经是机读词汇，多造一个同义词只会制造两套"未标定"。

**被否决**：
- *为新形态写一份"精简配置"（缺段、只在演示里补）*：`config_completeness` 会拒（缺段即报错），绕过它等于
  削弱既有门禁；
- *新形态声明 judge 以求"评估器组合更像真实形态"*：会引入 LLM 花费与凭证面，与"零真实花费/零凭证"的
  离线证据要求冲突（且不发明业务定义时，judge 提示词本身就无从写起）；
- *把业务数字留空让加载器报错*：加载器会拒 ⇒ 交付无法成立（"留空"不是诚实，是**不可运行**）；
- *在配置里写"TODO 待业务侧确认"*：既有断言明文禁占位符（`tests/unit/test_config_integrity.py:174-178`
  的 `TODO`/`FIXME`/`PLACEHOLDER`/`xxx` 禁列）。

**影响面**：新增两份形态配置（B 阶段）与其插件声明；`ops/demo_form_plugin.py` 的产物字段；契约落点
`contracts/onboarding-ops.md` **C14**。

## 决策 12：会变红的既有测试与夹具清单及处理方式（**按扩展更新、不削弱**）

**问题**：本特性同时触碰三组既有契约面——① 静态守卫的三份副本（决策 5/6）；② 五处登记点与"恰好两份"
（决策 7/8）；③ 六个装配函数的调用面（30 余处，决策 1/2）。规格边界情况第 13 条与 FR-013 要求"按**扩展**
更新、**不削弱**"（`specs/021-form-plugin-validation/spec.md:105`/`:124`）。

**决策：处理原则只有一条——"按扩展更新、不削弱；零删除、零放宽"**。逐条清单如下（"保留" = 不需改动且必须
继续通过；"扩展" = 断言面按新行为增补但不得删既有断言；"委派" = 只换常量来源，循环体与断言体原位保留）：

| # | 测试 / 夹具（符号名优先） | 位置 | 为什么会红 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | `Test零形态分支静态断言.test_core_与_agents_无形态字面量` | `tests/unit/test_form_switch.py:421-429` | 扫描面补 `agents/pilot`（去掉 `:423` 的排除）+ 禁用清单改派生（裸词）⇒ 会捉到 `agents/pilot/pilot.py:613` 与 `core/deployment/evidence.py:97` | **扩展 + 委派**：排除项 `:423` 删去（那是**补面**，方向是变严）；`agents/pilot/pilot.py:613` 收敛（决策 6）；`core/deployment/evidence.py:97` 走 E1 例外；循环体与断言体保留原位 |
| 2 | `test_全仓_agents_与_core_无形态判断分支` | `tests/unit/test_form_switch.py:431-436` | 禁用分支模式由派生面给出（模式集**不变**）⇒ 本身不红 | **委派**（常量来源） |
| 3 | `test_形态切换只经配置文件` | `tests/unit/test_form_switch.py:438-441` | 新增第三份配置必红 | **改口径**（决策 7：两两唯一 ∧ ⊆ 派生集 ∧ ≥2），**禁止删除** |
| 4 | `test_渠道解析不得出现形态字面量或形态判断`（020 新增） | `tests/unit/test_form_switch.py:443-461` | 与 #1 同源字面量清单 | **委派**（`_sources` 与禁用清单同源） |
| 5 | `Test差异逐项可归因.test_全量差异都被配置文件承载` | `tests/unit/test_form_switch.py:341-379` | 两形态 `evaluators` 段**逐字相同** ⇒ 差异集**不变**、本用例**不红** | **保留**（原样）；新增"逐形态对"断言**另立**（决策 8 的 ①） |
| 6 | `tests/unit/test_billing_core_purity.py` 的 `FORM_LITERALS` / `FORM_PATTERNS` | `tests/unit/test_billing_core_purity.py:34-35` | 常量改派生后，`core/billing/` 里若出现裸形态词即红（今天为零命中 ⇒ 不红） | **委派**；`:145` 的断言体与 `:210-224` 的"有牙齿"自检保留 |
| 7 | `tests/unit/test_dev_core_degraded_purity.py` 的 `FORM_LITERALS` / `FORM_PATTERNS` | `tests/unit/test_dev_core_degraded_purity.py:28-29` | 同上 | **委派** |
| 8 | `Test全部配置类加载器` 的参数化面 | `tests/unit/test_config_integrity.py:133-141` | 配置集合改派生、新增 `evaluators` 段的加载器条目 ⇒ 参数化条目数增加 | **扩展**（新增条目，不删既有） |
| 9 | `test_缺项即红` 的参数化面 | `tests/unit/test_config_integrity.py:153-171` | 同上（每份 `configs/*.yaml` 都跑全部条目） | **扩展**（`REQUIRED_PATHS` 增 `evaluators.plugins.*.*.*.impl`/`.version`/`params` 等新条目） |
| 10 | `test_与电影配置段集合一致` | `tests/unit/test_config_integrity.py:122-126` | 两形态都新增 `evaluators` 段 ⇒ **集合仍相等**、不红 | **保留**；派生化后新增"全部形态段集合一致"断言**另立** |
| 11 | `test_c13_两套配置差异可归因且无形态分支` | `tests/contract/test_pilot_contracts.py:418-477` | 差异集不变（同 #5）；`:469-477` 的写死禁用元组改派生 | **保留** + **委派**（禁用元组）；`:434-453` 的固定集合断言**一字不改** |
| 12 | `config_completeness` 的返回段清单 | `agents/pilot/pilot.py:377`（调用点 `:507`，断言 `tests/unit/test_pilot_chain_seven.py:118-121`） | 追加 020 口径机检 ⇒ 返回元组变长 | **扩展**（断言新增项，不删既有项） |
| 13 | `PILOT_FORMS` / `pilot_form_config_path` | `tests/conftest.py:2854`、`:3054-3079` | 派生化后 `raise ValueError`（`:3072`）只对**未声明**形态触发；`movie` 分支仍用 `_MINIMAL_MOVIE_CONFIG`（`:3196`） | **扩展**（`movie` 精简副本职责保留；新增形态走真实配置派生副本） |
| 14 | 六个装配函数的调用面（30 余处，含 `tests/unbiasedness/*`） | 见 plan 阶段 A1 的调用点清单 | 六个 `build_*_evaluators` **签名与返回形状不变** ⇒ **不红**；但调用方传入的配置对象必须携带 `evaluators` 段（由夹具构造的精简配置字典多数不带该段）⇒ **会红**："缺声明即装配期报错" | **必须扩展夹具**：`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py` 与 `tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py` 等处的**内联配置字典**补 `evaluators` 段（从真实 `configs/movie.yaml` 抄，或复用公共夹具构造）；**不得**为了让它们过而在实现里做"缺段即回落到硬编码装配"的兜底 |
| 15 | `Test门禁注入两层断言.test_构造点普查面有效` | `tests/unit/test_billing_core_purity.py:355-362`（`:359` 断言 `len(sites) == 14`） | 若离线端到端演示构造 `LLMGateway`（`core/llm_gateway/gateway.py:147`），普查会变成 15 处 | **扩展**：把 `ops/demo_form_plugin.py` 登记进 `OFFLINE_ASSEMBLIES`（`tests/unit/test_billing_core_purity.py:268-280` 一带）并把计数 `14 → 15`；**不得**改成"只数真实渠道"来绕过（计数与清单常驻是 019/020 的既有纪律，变严不变松）——**若演示不构造网关**（推荐：最小形态不声明 judge）则本项**不动**，届时 `len(sites)` 仍为 14 |
| 16 | `tests/unit/test_sound_composite.py:129` 等按**下标**取评估器的用例 | `tests/unit/test_sound_composite.py:98`/`:129`/`:244` | 装配顺序改由声明顺序决定 ⇒ 顺序必须与今天**逐字一致** | **保留**（装配点保序是硬要求，机检 = 声明顺序 == 装配顺序）；顺序若漂移，本组用例**必须**变红（它们就是保序的守卫） |
| 17 | `tests/unit/test_dev_compare_adopt.py:758`（断言实现源码里 **不出现** `build_dev_evaluators`） | `tests/unit/test_dev_compare_adopt.py:758` | 该断言针对"降级对比路径不得复用真实装配"，与本特性无关 | **保留**（本特性不改该文件；若因 #14 的公共夹具改造被牵连，只改**夹具**部分并保持该断言原样） |
| 18 | `tests/unit/test_billing_channels.py:52`、`tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`、`tests/unit/test_billing_core_purity.py:31`、`tests/unit/test_form_switch.py:30` 的 `FORMS` | 逐处 | 派生化后新形态自动进入这些用例的遍历面 ⇒ 可能因新形态配置不满足某些形态特定假设而红 | **扩展**：断言体不删；形态特定期望值改为"逐形态声明/登记"，**不得**把新形态从派生面排除 |
| 19 | `tests/unit/test_calibration_config.py:124` 的"缺项即红"参数化 | `tests/unit/test_calibration_config.py:124` | 新增 cadence 校验不影响该参数化（它测的是缺键） | **保留**；cadence 越界报错**另立**新用例（注入 `period_days: 14` 断言报错，且断言 `core/calibration/periods.py:30` 的取值域**未**被改写） |

**变红清单之外、明确不改的**：`tests/adversarial/`、`tests/unbiasedness/`（用例体零改动；只可能通过 #14 的
夹具同步受影响）、`tests/integration/`（无 DB 迁移 ⇒ 零改动）、`tests/contract/test_pilot_film_contracts.py`
（逐字节不变主张的举证面 ⇒ 必须继续通过）、`tests/contract/test_calibration_contracts.py`
（"权重生效后历史节点逐字节一致"与"昂贵动作调用计数为 0"两条机检继续成立）。

**依据/理由**：① 这些断言面正是"形态载体"与"版本冻结"的**唯一守卫**，守卫放宽则"零代码改动"会立刻退化为
口号；② 第 14 项被单列为**最大风险**：内联配置字典若被加上"缺 `evaluators` 段就回落到硬编码装配"的兜底，
等于在实现里留下一条**影子装配路径**（正是 FR-012 禁止的第二路径）——所以本项的处理是"**扩展夹具**"而不是
"实现兜底"；③ 第 15 项被单列是因为它会把"离线演示的存在"与"真实渠道装配点清单"耦合——处置方式是**显式登记**
（清单+计数常驻），不是放宽判定。

**被否决**：
- *删掉会红的断言（如"恰好两份"、`:423` 之外的任何既有断言）*：规格明文禁止；
- *用"实现兜底"让夹具不必补段*：制造第二装配路径（见理由 ②）；
- *把新形态临时排除出派生面以求本轮全绿*：这正是"新形态静默逃逸"，是本特性要消灭的东西；
- *等实现完再统一改测试*：违反 TDD 纪律（先写测试 → 确认测试有效 → 再实现），也会让"变红清单"变成事后追认
  （`specs/020-shortdrama-real-feedback/research.md:529` 的决策 10 同款论证）。

**影响面**：上表 **19 项**，覆盖 `tests/unit/`（约 12 个文件 + `conftest.py` 两处夹具）、`tests/contract/`
（3 个文件）；契约落点 `contracts/plugin-config.md` **C3**（插件本体的业务无关双层机检 + "声明才生效"）、
`contracts/zero-form-branch.md` **C5/C6**（字面量与判断分支两层扫描面）、**C7**（三副本委派收敛、只增不减）、
`contracts/form-registration.md` **C9**（五处登记点改配置/形态派生的改法与逃逸风险）、**C10**（登记完备口径）。

## 决策 13：离线演示**前移到 A5 创建**，且必须"**形态无关**"

**问题**：`ops/demo_form_plugin.py` 若在 B1（接入侧）创建，它属"**新增 `ops/` 文件**"——按 C12 的类别判定，
接入改动只允许"新增 `configs/*.yaml` + 插件 + 测试与文档"三类 ⇒ 该文件是**越界项**；而它又必须被机制侧
ledger 登记（它是本特性的交付物之一）⇒ 两边矛盾：**B1/B2 的接入清单永远非空越界**，验收永远红（I-09，CRITICAL；
正确性由构造保证：`ops/` 不在接入改动允许的三类内）。

**决策**：

1. **演示前移到 A5（机制侧）创建**：`ops/demo_form_plugin.py` 是**机制侧资产**，编入 `MECHANISM_LEDGER_PATHS`
   （决策 14），**不**编入任何接入清单的"新增"面（接入清单的基线是"机制落地后"，此文件已在基线之内）。
2. **演示必须形态无关**：入口**遍历 `declared_forms()`**，对**每个已声明形态**跑同一套九步
   （配置预检 → 缺项即拒绝 → 装配 → 评估与合成分数 → 留痕 → 两形态共用插件代码 → 静态守卫 → 登记点完备 →
   诚实分层与零成本）。⇒ 新增一个形态（`configs/<new>.yaml` + 插件 + 声明）后，演示**零改动**即可覆盖它
   （新形态自动进入遍历面）。
3. **形态值只作参数透传**：演示脚本内**零形态字面量**（写法先例 `ops/pilot.py:250` 的 `--form` 透传），
   否则它自己就成了"第五处形态常量表"（C7 的副本收敛会被它破坏）；脚本零 `os.environ`/`os.getenv`、
   **不 import** 任何 HTTP 客户端。
4. **B1/B2 只调用、不创建**：B1/B2 的改动面**只有**配置与插件（+ 测试与文档）；接入清单中 `ops/` 面路径数
   **恒为 0**（可机检事实）。

**依据/理由**：① "仅新增配置 + 插件"这条验收只有在"运行期入口面已经在机制侧就位"时才可能成立——演示是
**机制**（如何跑通一次），不是**形态内容**（本形态用什么评估器）；② 形态无关遍历是同一道理的实现形式：
演示的输入是"派生面"而不是某一个形态名（与决策 5/6 的派生纪律同源）；③ 让 B 侧创建一个 `ops/` 文件的另一
后果是**判据被放宽**：为了让它通过，实现者会去改 C12 的类别判定（最坏结果：接入改动清单失去牙齿）。

**被否决**：

- **在 B 阶段再建演示**（初稿方案，已由本次裁决作废）：B1 的产物立刻成为越界项 ⇒ B1/B2 验收永远红，且必然
  诱发"放宽类别判定"或"为演示单开一次例外"；
- *把演示记作"测试与文档"类别*：`ops/` 是运行期入口面而非测试面；把 `ops/` 下的**新增**记成"测试"等于给
  `ops/` 开一条旁路（既有文件的修改按 C12 是越界，新增同判才自洽）；
- *为演示单独豁免一次越界*：例外口径"只允许三条"（C5）不含此项，开例外须走契约修订；
- *让演示只服务某一个形态（例如把 `ad` 写进脚本）*：会把形态名写进 `ops/` 文件，第二形态接入时又要改演示 ⇒
  直接把"仅新增配置 + 插件"打穿。

**影响面**：`ops/demo_form_plugin.py`（**A5 创建**、形态无关；plan 的 A5 与 B1/B2/B3 已按此改写）；
接入改动清单的"`ops/` 面零改动"成为可机检事实；契约落点 `contracts/onboarding-ops.md` **C12**（类别判定）、
**C14**（CLI 与离线端到端演示）。

## 决策 14：机制侧总账 `MECHANISM_LEDGER` / `MECHANISM_LEDGER_PATHS` 的**穷举**与计数口径

**问题**：ledger 若只登记"主要文件"，会漏掉 A1 的**夹具同步面**（`tests/unit/test_{sound,screenplay,storyboard,
editing,dev,visual}_composite.py`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、
`tests/unbiasedness/*`）与**新增基线夹具**（`tests/unit/fixtures/evaluator_assembly_baseline.json`）——而"机制侧
改动全部登记"正是 C13 的机检对象（I-04，HIGH）；同时若"条数"被写死在文档与常量两处，两面必然漂移（I-05）。

**决策**：

1. **穷举**：`MECHANISM_LEDGER_PATHS` **必须穷举机制侧全部改动路径**——除 `core/` / `agents/` / `ops/` / `tests/`
   下的新增与修改件外，**含**：① A1 的夹具同步面（六个 `*_composite.py`、五个 `*_contracts.py`、`tests/unbiasedness/*`）；
   ② **新增基线夹具** `tests/unit/fixtures/evaluator_assembly_baseline.json`（"既有两形态装配序列改造前后逐字
   相同"的对照快照，见 plan 阶段 A1 第 6 步）；③ **A5 创建的** `ops/demo_form_plugin.py`（决策 13）。
   接入侧件（`configs/<new-form>.yaml` 与其插件）**不得**进 ledger。
2. **不写死条数（集合相等判据）**：文档表（quickstart 的"机制侧总账"表）与常量之间**集合相等**即通过
   （`set(文档表路径列) == set(MECHANISM_LEDGER_PATHS)`）；`len(...)` 只作展示与回归记录，**不得**成为与集合
   判据并列的第二个权威（两面各写一个数字必然漂移——020 的行号漂移已是同类教训）。
3. **计数分解按 FR-013 六项总账归属，不按实现阶段号**：`MECHANISM_LEDGER` 恰好**六项**，逐项 = FR-013 的六项
   总账（① 配置驱动的插件声明与唯一装配点｜② 扫描面补面（字面量层 + 判断分支层 + 副本委派归其下）｜
   ③ 形态名派生 + 三副本收敛｜④ `agents/pilot/pilot.py:613` 裸词收敛 + 020 口径逐项机检｜⑤ "恰好两份"升级为
   登记完备｜⑥ 接入改动清单机检）；每项带 `{step: <A1~A5>, items: [{path, kind: new|modified}]}`（`step` 是
   **归属标注**、不是计数维度）。**同一文件只按首要归属计一次**（去重纪律：`ops/form_guard.py` 归 ②③ 计一条、
   `tests/unit/test_form_switch.py` 归 ②⑤ 计一条），故 `MECHANISM_LEDGER_PATHS` = 六项 items 的路径并集去重。
4. **三个名的登记（只引用、不复述规则）**：`MECHANISM_LEDGER` / `MECHANISM_LEDGER_PATHS`（落
   `ops/form_onboarding.py`；C13）；`not_applicable`（**段内键（映射）**，只允许 `calibration.transfer.not_applicable`
   与 `budget.not_applicable`，值与取值域规则在 C11）；`calibration.cadence_note`（未标定形态必填、须含「近似」
   与「未标定」、不得宣称已达标；规则在 C11）。三者本计划与 research **只登记名与落点**。

**依据/理由**：① 穷举是 C13"机制侧改动全部登记"的**唯一可机检形式**——漏一类（夹具面）就会让"接入清单越界
为空"从一个真判据退化为部分覆盖（假绿比红灯危险）；② "集合相等"把两个权威面绑成同源：任何一侧漏条即红，
且新增机制改动时只需改常量（文档表随契约机检联动），不会出现"数字对不上但两边都自洽"；③ 按 **FR-013 六项**
计数而不是按阶段号：阶段号是实现编排细节（且同一文件会被多个阶段触碰 ⇒ 计数歧义），六项总账是**验收口径**，
评审可直接对表；④ 三个名必须先**登记**再引用——否则 plan/research 与契约面出现"用了没定义"的名字（I-13）。

**被否决**：

- *只登记"主要文件"（按目录粗粒度）*：漏夹具面 ⇒ "全部登记"不可机检（见理由 ①）；
- *写死条数并在文档表另算一遍*：两个权威面必然漂移；且条数会随夹具面补齐而变（本次已因漏夹具面而不准）；
- *按实现阶段号（A1~A5）计数*：同一文件跨阶段触碰 ⇒ 计数歧义，且与 C13 的六项对不上；
- *把接入侧新增件计入 ledger*：与"机制与接入分离"（FR-013/C13）直接冲突；
- *不登记这三个名、让实现者临场命名*：会出现同一键两个拼写（两套"不适用"/"未标定"口径），正是裁决要消灭
  的东西。

**影响面**：`ops/form_onboarding.py` 的两个常量（穷举面含夹具同步面与新基线夹具）+ 文档表（quickstart，
**由并行任务产出**）+ C13/C11 的机检用例；plan 的阶段 A1 第 7 步（夹具面）、A5（ledger 与演示）、A4（两个键）
与项目结构（`tests/unit/fixtures/` 新目录）已按此登记。

## 决策 15：并行面与**同文件串行点**（以文件为准），以及 B 侧交汇点口径

**问题**：初稿的并行声明与真实文件面不符（I-16）：① A1 与 A2 声明"可并行"，但两者**同改**
`configs/{movie,shortdrama}.yaml`（A1 加 `evaluators` 段、A2 加 `form_aliases` 键）；② `agents/pilot/pilot.py`
被 **A2（裸词收敛）**、A3（`config_completeness` 追加）与 A4（020 口径机检）**三处**触碰，只登记两处是漏的；
③ A3 的并列表一处写"五处"、一处写"七处"（前者是**登记点**数量、后者是**可并行文件**数量，两个量被混用）；
④ "两形态的唯一交汇点"不成立（I-17）。

**决策**：

1. **同文件串行点（硬，按文件写、不按阶段号）**：`configs/{movie,shortdrama}.yaml`（A1 → A2）；`agents/pilot/pilot.py`
   （A2 → A3 → A4）；`tests/unit/test_form_switch.py` 与 `tests/contract/test_pilot_contracts.py`（A2 → A3）；
   `core/calibration/config.py`（A4 内部单文件串行）。**A1 与 A2 的"可并行"仅在排除上述同文件面之后成立**。
2. **并列表口径统一**：A3 的**可并行文件面 = 七处**（五个登记点 + `ops/form_onboarding.py` 的 `REGISTRATION_SITES`
   + 同族枚举副本面）；"**五处**"一律**专指登记点数量**（C9/C10 的对象），**不得**用来描述可并行文件数。
3. **B 侧交汇点 = 两形态并跑用例 + "对两形态各跑一次"的清单/登记机检**（等价于任务面的 T2172 与 T2173/T2174）：
   两形态并跑需 B1 与 B2 都落地；"对 `ad` 与 `animated` **各跑一次**接入清单与登记完备机检"同样需两形态
   都就位 ⇒ **交汇点是这三条，不是单点**。
4. **演示不是 B 侧交汇点**：它已在 A5 落地且形态无关（决策 13），B1/B2 只调用它。

**依据/理由**：① 串行点是**文件**事实而不是编排概念——按文件写才不会随任务拆分漂移（阶段号会变、文件不会）；
② "五处 / 七处"混用的根因是把两个量（登记点数量 vs 可并行文件数）写成同一个词，明确各自含义即可消除；
③ 交汇点若只写一条，B2 的验收会在"只跑 animated"时**假绿**（漏掉"对两形态各跑一次"的两条机检）。

**被否决**：

- *照旧声明 A1/A2 全并行*：两处对同一配置文件并行改动会互相覆盖，且"两形态 `evaluators` / `form_aliases`
  逐字相同"的断言会**随机红**（最难查的一类红）；
- *用阶段号描述串行点*：见理由 ①；
- *把 B 侧交汇点写成单条任务*：见理由 ③；
- *把演示也算作交汇点*：演示形态无关，B 侧对它零改动（决策 13）。

**影响面**：plan 的"并行面与同文件串行点"段与 A1/A2/A3/A4/B1/B2/B3 的串行标注；契约落点：**不新增**
（编排面事实，落 plan 与 tasks；C13 的 ledger 不受影响）。
