# 契约：零形态分支扫描面、形态名派生与副本收敛（C5~C8）

> 对应规格 FR-005 / FR-006（裁决 3/4/5）、SC-003、US2 场景 1~6、边界情况第 3/4/5/7 条。
> **实现落点**：**新增** `ops/form_guard.py`（形态名派生 + 两层扫描，**单一实现**，业务无关）
> + **新增** `tests/unit/test_form_guard.py`（派生面自检 + "有牙齿"合成反例）
> + 三处既有测试**只换常量来源**（委派）+ `tests/unit/test_form_switch.py:423` 的 `agents/pilot` 排除**删去**
> + `agents/pilot/pilot.py:613` 的 docstring 措辞**收敛** + `core/deployment/evidence.py:97` 登记为**例外 E1**。
> 数据模型见 [../data-model.md](../data-model.md) 实体 5/6。
>
> **现状三处硬事实**：形态名是**人工常量且三副本**（`tests/unit/test_form_switch.py:413-414`、
> `tests/unit/test_billing_core_purity.py:34-35`、`tests/unit/test_dev_core_degraded_purity.py:28-29`）
> ⇒ 新形态名**天然逃逸**；字面量扫描面**显式排除 `agents/pilot`**（`tests/unit/test_form_switch.py:423`）
> 而 `agents/pilot/backends.py` 恰是后端**装配点**（`agents/pilot/stages.py:248` 的 `build_backends`）；
> 锚点只按**行号**（020 登记的行号**已漂移**，`specs/020-shortdrama-real-feedback/tasks.md:692`）。
> `core/` + `agents/` 今天**只有两处**命中形态词：`core/deployment/evidence.py:97`（配置**文件路径**字面量，
> 属例外 E1）与 `agents/pilot/pilot.py:613`（**裸形态词、非路径**，按收紧后的口径**属违规**）。
>
> **本文定名的键路径/函数名是权威**（与 `data-model.md`、`contracts/plugin-config.md` C1 逐字一致）：
> 派生函数 `declared_forms`（id 面）、`form_literals`（名称面）、`form_branch_patterns`、
> `iter_sources`、`literal_violations`、`branch_violations`、`classify_exception`；违规点形状
> **`FormHit`** = `{path, symbol, line, hit, layer}`。
>
> **编号权威映射（父代理 2026-09-25 裁决；全文引用一律以此为准）**：本文件 **`C5`** 字面量层扫描面
> （**含例外三条**）/ **`C6`** 判断分支层扫描面 + **形态名派生** / **`C7`** **三副本委派收敛** /
> **`C8`** **`agents/pilot/pilot.py:613` 裸词收敛 + `core/deployment/evidence.py:97` 例外登记**；
> 相邻契约 `contracts/form-registration.md` 为 **`C9` = 五处登记点派生改法 / `C10` = 登记完备口径 /
> `C11` = 020 口径与 cadence 收口**；`contracts/onboarding-ops.md` 为 **`C12~C14`**。
> 凡涉及"登记完备"的引用，权威落点一律是 **`form-registration.md` C10**（**不是** C9）。
> 注：`TestC10到C13试水运行`（`tests/contract/test_pilot_contracts.py:360`）是**测试类名**
> （取 020 的契约编号），与上述 C 编号体系**无关**，不得据此改写编号。
>
> **三条定名已由父代理确认（逐条按此落盘、全文一致）**：① `declared_forms`（**id 面** = 各
> `configs/*.yaml` 的 `form:` 取值）与 `form_literals`（**名称面** = id 面 ∪ 全部 `form_aliases` 项）
> **分两个函数**（分工与理由见 **C6**）；② **`configs/<id>.yaml` 的文件名 stem 必须等于 `form:` 取值**
> （使登记点 ⑤ 的 `pilot_form_config_path(form)` 能在**零人工常量**下反查到唯一配置路径，见 **C6**）；
> ③ 装配入口 = `core/evaluators/plugin.py` 的 `parse_manifest(document, agent, *, slots)` 与
> `assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None)`
> ——签名与键路径的权威在 `contracts/plugin-config.md` **C1/C2**，本文件只引用、不另定。

## C5 字面量层扫描面（符号名锚点、覆盖含 `agents/pilot`、例外只三条、词边界判定）

**目的**：把"**零形态字面量**"从"覆盖两个形态、按行号引用、清单写死"升级为"**由配置派生形态名、
全覆盖 `core/` + `agents/`（含此前被排除的 `agents/pilot`）、按符号名锚定**"，并让例外**只**剩三条
各自带可判定谓词——例外越多，守卫越快退化为注释（020 的 `:423` 排除就是一条**过宽的例外**造成的盲区）。

**形状/接口**（`ops/form_guard.py`；业务无关，不 import 任何形态概念）：

```python
@dataclass(frozen=True)
class FormHit:
    path: str      # 相对仓库根（posix）
    symbol: str    # 所属 FunctionDef/ClassDef 名；模块级 ⇒ "<module>"（**锚点以此为准**）
    line: int      # 仅作人读辅助（**不得**作为机检锚点：020 登记的行号已漂移）
    hit: str       # 命中的形态名或分支模式
    layer: str     # "literal" | "branch"

def iter_sources(roots: tuple[str, ...] = ("core", "agents")) -> tuple[Path, ...]: ...
def literal_violations(configs_dir: Path) -> tuple[FormHit, ...]: ...    # 字面量层
def classify_exception(hit: FormHit) -> str | None: ...                  # "E1" | "E2" | "E3" | None
```

- **扫描面（定义，不是逐条豁免）**：`core/**/*.py` + `agents/**/*.py`，**排除 `__pycache__`**，
  **不排除 `agents/pilot`**（C5 的核心补面）。`tests/**`、`ops/**`、`web/**`、`dreaming/**`、`policies/**`
  **不在**扫描面内。
- **扫描面的符号名锚点（既有断言，一律按符号名引用、不引行号）**：

| 层次 | 文件 | 符号名锚点 | 今天的状态 |
| --- | --- | --- | --- |
| 字面量 | `tests/unit/test_form_switch.py` | `Test零形态分支静态断言.test_core_与_agents_无形态字面量` | 扫描面**排除 `agents/pilot`**（`:423`）⇒ 本契约**删去该排除**（补面方向是**变严**） |
| 字面量 | `tests/unit/test_form_switch.py` | `Test零形态分支静态断言.test_渠道解析不得出现形态字面量或形态判断` | 020 新增的补面断言（单文件），保留 |
| 字面量 | `tests/unit/test_billing_core_purity.py` | `Test零形态与厂商字面量.test_无形态字面量与形态分支` | 常量来源改派生（断言体原位保留） |
| 字面量 | `tests/unit/test_dev_core_degraded_purity.py` | `Test零形态与厂商字面量.test_无形态字面量与形态分支` | 同上 |
| 字面量 | `tests/contract/test_pilot_contracts.py` | `TestC10到C13试水运行.test_c13_两套配置差异可归因且无形态分支` | 该处**本就覆盖 `agents/pilot`**；写死的禁用元组改派生 |
| 分支 | `tests/unit/test_form_switch.py` | `Test零形态分支静态断言.test_全仓_agents_与_core_无形态判断分支` | 常量来源改派生（见 C6） |

- **判定规则（两层共用）**：
  - **ASCII 命名面按词边界**：`(?<![0-9A-Za-z_])<name>(?![0-9A-Za-z_])`。先例
    `tests/unit/test_billing_core_purity.py:54-56`（标识符边界判定）与 `:222-224`
    （`dev` 不得误命中 `deviation`）。**理由**：新形态 id 可能是**短词**（如 `ad`），子串判定会命中
    `read`/`load`/`head` 一类常见标识符，**假阳性会逼出无意义的改名**（那是最坏的副作用）；
    而边界判定仍能捉住 `"movie"` / `'movie'` / `configs/movie.yaml`（引号、`/`、`.` 都是边界）。
  - **中文命名面按子串**：中文别名（如"广告"/"漫剧"）无标识符边界概念，按子串判定。
  - **命中即违规**，除非 `classify_exception(hit)` 返回 E1/E2/E3。

- **例外只三条（逐条带可判定谓词；全仓库**只允许**这三条）**：

| 代号 | 例外 | 判定谓词（可机检） |
| --- | --- | --- |
| **E1** | 配置的**文件路径**字面量 | 命中所在**字符串字面量**同时含 `configs/` 与 `.yaml`，且形态名**仅**作为路径片段出现（例：`core/deployment/evidence.py:97` 的 `source="configs/movie.yaml deployment.gate.require_unbiasedness=false"`） |
| **E2** | **测试夹具** | `tests/**` **不在扫描面**（这是**扫描面定义**，不是逐条豁免）；`ops/**`/`web/**`/`dreaming/**` 的既有配置路径默认值与演示形态值同理**不在**扫描面内 |
| **E3** | docstring 中形态的**中性描述** | 命中落在模块/类/函数 **docstring 内**，**且该命中行不含** `=`、`⇒`、`→` 或任一分支模式（即"只列举/说明、不绑定取值"） |

  三条之外的**任何**例外（文件白名单、目录豁免、行号豁免、`# noqa` 式逐条放行）**一律不存在**；
  新增例外**必须**走契约修订流程。**假阳性的唯一处置是中性化措辞，不得加例外**
  （与 `plan.md:469` 的风险处置一致）。

- **判定顺序（例外三条 与 词边界判定的关系——两层共用，实现者照此顺序做）**：

  1. **① 取名字面量集合**：`names = form_literals(configs_dir)`（名称面，C6）——**唯一**输入，
     零人工常量、派生失败即报错。
  2. **② 逐文件在扫描面上找候选命中**：对 `iter_sources(("core", "agents"))` 的每个文件文本，
     对每个 `name` 按**判定规则**找命中——**ASCII 名按词边界** `(?<![0-9A-Za-z_])<name>(?![0-9A-Za-z_])`，
     **中文名按子串**。词边界这一步**只解决"是不是这个形态名"**：短 id（如 `ad`）用子串判定会命中
     `read`/`load`/`head` 一类常见标识符 ⇒ **必须**用词边界，否则会逼出无意义的改名（先例
     `tests/unit/test_billing_core_purity.py:54-56` 与 `:222-224` 的"`dev` 不得误命中 `deviation`"）；
     而 `"movie"` / `'movie'` / `configs/movie.yaml` 里的引号、`/`、`.` **本来就是边界** ⇒ 词边界
     不放过真命中。中文别名的子串判定**不**引入假阳性这类风险（中文不承担标识符角色）。
  3. **③ 候选逐个过例外谓词**：对步骤 ② 的每个候选 `FormHit`，依次调
     `classify_exception(hit)`——命中 **E1**（配置**文件路径**字面量：同一字符串含 `configs/` 与
     `.yaml` 且形态名仅作路径片段）或 **E3**（**docstring 中性描述**且该行不含 `=`/`⇒`/`→`/分支模式）
     即**合法**；否则**计入违规**。**E2 不是逐条豁免**：`tests/**`、`ops/**`、`web/**`、`dreaming/**`
     在步骤 ② 的**扫描面定义**里就已经不在，无需（也不允许）在谓词里再判一次。
  4. **④ 违规即红**：`literal_violations()` 返回的违规点集合必须为空（当前仓库下 == 例外命中数：
     E1 恰好 1 处、E3 为 0 处，见 C8）。
  5. **口径纪律**：**词边界是"判定"，例外是"放行"——两者不得互相顶替**：不得为了避开假阳性而放宽
     判定（改成子串），也不得为了让真命中变绿而扩张例外（那会把守卫退化成注释，020 的 `:423` 排除
     就是一条过宽例外的教训）；例外**只**允许 E1/E2/E3 三条，命中面与谓词都是**冻结**的。

**机检断言**：

- **扫描面覆盖装配点（SC-003 的举证）**：`agents/pilot/backends.py` ∈ `iter_sources("agents")`；
  向该文件注入派生形态名（如 `"ad"`）⇒ 字面量层**必须变红**（注入即红率 100%）。
- **覆盖面完整性**：`iter_sources("agents")` 含 `agents/pilot/**` 全部 `.py`；
  `iter_sources("core") + iter_sources("agents")` 的路径集合 == 两个根下的 `.py` 全集（排除 `__pycache__`），
  集合差为空（即"零排除"本身可机检）。
- **违规数恒 0**：当前仓库在字面量层的违规数 == 例外三条的命中数（E1 恰好 1 处，见 C8；E3 为 0 处）。
- **锚点可机检**：每条 `FormHit.symbol` 非空（模块级为 `"<module>"`）；机检输出的定位**不依赖行号**
  （同一违规在文件中上下移动若干行后仍被同一 `symbol` 定位）。
- **派生面而非常量**：`literal_violations` 的形态名输入**只能**来自 `form_literals(configs_dir)`
  （C6）；守卫模块与 `tests/unit/test_form_guard.py` 自身**零人工形态常量**（合成反例必须由**派生值**构造）。
- **有牙齿自检常驻**：`tests/unit/test_form_guard.py` 用**派生值**合成三类反例并逐条断言被判违规——
  ① 裸字面量写在 `core/` 某文件（判违规）；② `f'if form == "{forms[0]}"'` 写在 `agents/pilot/backends.py`
  （判违规，同时举证"装配点被覆盖"）；③ docstring 里 `f"（{forms[0]} ⇒ 30 s）"` 形式的**取值绑定**（判违规）。
  先例：`tests/unit/test_billing_core_purity.py:210-224` 的合成源码自检。
- **例外判定不被滥用**：E1 只认"字符串同时含 `configs/` 与 `.yaml`"；把形态名写进普通字符串
  （如 `FORM_LABEL = "movie"`）**不得**被判为 E1；E3 的"该行不含 `=`/`⇒`/`→`"是**硬**条件。

**反例**（必须被判违规 / 必须为空）：

- `core/` 或 `agents/` 任一 `.py` 里出现派生名称面中的任一名字（含中文别名）且不属 E1/E3 ⇒ 违规。
- `agents/pilot/**` 内出现形态字面量 ⇒ 违规（**该目录不再是盲区**）。
- 以文件白名单豁免某文件（尤其再次豁免 `agents/pilot`）⇒ 红（把 `:423` 的排除换个写法也是如此）。
- 把形态名写进 `# 注释`（非 docstring）⇒ 违规（注释不受 E3 保护）。
- 用子串判定短 id（`ad` 命中 `read`）⇒ 视为**判定口径错误**（假阳性会逼出无关改名）。
- 在守卫里保留一份"默认形态清单"兜底（`configs/` 为空时使用）⇒ 红（人工常量清单复活，
  且"配置缺失即静默全绿"是最坏的假绿）。

**兼容规则（零回改承诺）**：

- **015 两形态切换**：既有守卫的**断言语义只增不减**——`tests/unit/test_form_switch.py:421-429` 的
  字面量扫描与 `:443-461` 的渠道解析补面断言**保留原位**；`:423` 的排除**删去**属**补面**
  （方向是变严，不是削弱）；三处委派只为**换常量来源**（C7）。
- **010 / 019 / 020 的既有配置路径默认值不改写**：`web/server.py:320`、`web/export.py:301`、
  `dreaming/deploy_hook.py:25`、`ops/billing.py:97`、`ops/ingest_metrics.py:121` 的 `configs/movie.yaml`
  默认值，以及 `ops/demo_merged_pool.py:55`、`ops/smoke_llm.py:358` 的演示形态值，
  **不在**扫描面内、也**不得**为过断言而改写（规格边界情况明文禁止）。
- **020 登记点零回改**：本契约不新增登记点；`tests/contract/test_pilot_contracts.py:469-477` 的写死元组
  改由本守卫派生后，两处扫描面（该处与 `tests/unit/test_form_switch.py`）的口径**必然一致**
  ——这消除了"两处扫描面口径分叉"这一既有隐患（该处**本就覆盖 `agents/pilot`**，与补面后口径相同）。
- **既有 015 的 `BANNED_PATTERNS` 六个模式字面不变**（见 C6）：模式集是**形态无关的语法模式**，
  不属形态名常量表，可留作常量。

## C6 判断分支层扫描面 + 形态名派生（`form:` + 别名键；零人工常量、缺键即报错）

**目的**：两层都要断言、都要常驻机检（裁决 4）：① 零**字面量**（C5）；② 零**判断分支**（本契约）。
且形态名清单**一律由 `configs/` 目录派生**（裁决 3）——新增一份形态配置即**自动**纳入禁令面、
无法静默逃逸；派生失败（缺键 / 类型错 / 重名）即**报错**，不静默跳过。

**形状/接口**：

```yaml
# 每份 configs/*.yaml 的顶层（与 form 同级）：别名键**必须存在**（可为显式空列表 ⇒ 缺键即报错）
form: <id>                     # 既有键（非空字符串；自由字符串，不受枚举约束）
form_aliases: []               # 新增键：该形态的**其余名称**（英文同义词 + 中文别名）
```

```python
# ops/form_guard.py
def declared_forms(configs_dir: Path) -> tuple[str, ...]: ...   # **id 面**：全部 configs/*.yaml 的 form: 取值
def form_literals(configs_dir: Path) -> tuple[str, ...]: ...    # **名称面**：id 面 ∪ 全部 form_aliases 项（去重）
def form_branch_patterns() -> tuple[str, ...]: ...              # 形态无关的**语法模式**（六条，字面不变）
def branch_violations(configs_dir: Path) -> tuple[FormHit, ...]: ...
```

- **两条派生面的分工（不得混用；父代理已确认分两个函数）**：`declared_forms` 是**登记面**（**id 面**，
  各 `configs/*.yaml` 的 `form:` 取值，两两唯一）——**登记完备**的"**配置集合 ⊆ 派生形态集**"这一条
  用的**就是 id 面**（权威落点 `contracts/form-registration.md` **C10**），并供"形态 → 配置路径"反查；
  `form_literals` 是**扫描面唯一输入**（**名称面** = id 面 ∪ 全部 `form_aliases` 项，含中文别名）——
  **只**用于 C5/C6 两层扫描的"名字面量集合"，**不得**用于登记面或任何 `⊆`/计数比较。
  **一句话分述**：**登记面的量是"形态 id 的个数"（`declared_forms`）；扫描面的量是"形态名（含中文别名）
  的个数"（`form_literals`）**——两者**不得互相顶替**。这是 `research.md` 决策 5 与决策 7 两条要求的
  **共同落点**（细化命名，非改口径）——若把别名并进 `declared_forms`，登记完备的
  `declared_forms() ⊆ registered_forms()` 就会被别名打破（登记点里登记的是**形态**，不是别名）。
- **派生失败即报错（四条）**：① 任一 `configs/*.yaml` 缺顶层 `form` 键 ⇒ 报错（不静默跳过该文件）；
  ② `form` 值非字符串或为空 ⇒ 报错；③ 任一配置缺 `form_aliases` 键 ⇒ 报错（**可为显式空列表**）；
  ④ 跨文件的 `form` 取值**两两唯一**、跨文件的**全部名称**（id 面 ∪ 别名面）**两两唯一** ⇒ 重复即报错。
- **文件名与形态 id 一致（父代理已确认）**：`configs/<id>.yaml` 的 `form` 取值必须**逐字等于** `<id>`
  （文件名 stem）——理由：登记点 ⑤ 的 `pilot_form_config_path(form)`（`tests/conftest.py:3054-3079`）
  必须能在**零人工常量**下把形态 id 反查回**唯一**配置路径（`research.md:382` 的"任意已声明形态返回
  该形态真实配置的派生副本"）；不一致即报错。⇒ 形态 id → 配置路径的映射是**派生**的，
  不得另建一份"形态注册表"映射（那会立刻成为新的漂移源）。
- **判断分支层的模式集（六条，字面不变）**：`"form =="`、`"form=="`、`"form !="`、`"form!="`、
  `"form is "`、`"form in "`（= 既有 `tests/unit/test_form_switch.py:414` 与两处副本的同款六条）。
  **形态→值的字典分派**（`{"movie": ..., "shortdrama": ...}`）**不属于**本层新增模式：
  其键**必是形态名**（字面量）⇒ 已被 **C5 的字面量层**捉住 ⇒ 不新增模式（不改既有语义）。
- **形态值只作参数透传**（正例，既有）：`core/orchestration/models.py:197` 只校验 `form` 非空、
  `ops/pilot.py:250` 的 `--form` 只透传、`agents/pilot/backends.py:1-25` 的模块 docstring 明写
  "只看 `pilot` 段取值（形态值只作透传），不含任何形态判定分支"。

**机检断言**：

- **零判断分支**：同一扫描面（`core/` + `agents/` **全覆盖含 `agents/pilot`**）内六条模式的出现次数
  恒 **0**（`branch_violations()` 返回空）。
- **派生自配置（新增形态自动纳入）**：新增一份 `configs/<new-form>.yaml`（含 `form` 与 `form_aliases`）
  ⇒ `form_literals()` 的返回值**自动**包含该形态的 id 与全部别名（条目数 == 名面去重后条目数 ==
  `declared_forms()` 长度 + 全部 `form_aliases` 项去重后的和）；把该形态名字面量写进
  `core/` 或 `agents/` **即变红**。
- **零人工常量清单（判定口径 = C7 的两条并列断言，缺一不可）**：① **反向扫描命中数恒 0**（任意
  `ast.Tuple`/`List`/`Set`/`Dict` 字面量——**含函数体内与 `@pytest.mark.parametrize(...)` 装饰器实参**
  ——含名称面字符串的命中数）；② `declared_forms` / `form_literals` 的**定义点唯一**（全仓同名
  `FunctionDef` 各恰好一处 = `ops/form_guard.py` 的派生面）。该模块与
  `tests/unit/test_form_guard.py` 的源码内**不出现**任何形态名字面量（合成反例由派生值构造）。
- **派生失败即报错**：删掉某配置的 `form` 键 / `form_aliases` 键、把 `form` 改成非字符串、
  给两份配置填同一个 `form`、或让某别名等于另一配置的 `form` ⇒ **四条各自**报错率 100%
  （不静默跳过、不"取第一份"）。
- **两条派生面不得混用（id 面 vs 名称面，父代理已确认分两个函数）**：
  ① 登记完备（`form-registration.md` **C10**）的 `⊆`/计数比较**只**接受 `declared_forms()` 的输出——
  把 `form_literals()`（含中文别名）喂进该比较的调用点数恒 **0**；
  ② `form_literals()` 的输出**只**进 C5/C6 两层的"名字面量集合"——把它当"形态个数"使用的次数恒 **0**；
  ③ `len(declared_forms()) == len(configs/*.yaml)` 且 `set(declared_forms()) ⊆ set(form_literals())`
  （名称面**至少**覆盖全部 id：别名只增不减、不得改名）。
- **文件名 stem == `form` 取值（父代理已确认）**：对每份 `configs/*.yaml`，
  `Path(p).stem == <该文件 form: 取值>`（逐字）；不一致率恒 **0**；据此"形态 id → 配置路径"的
  反查命中**唯一**（零常量、零歧义），`pilot_form_config_path(form)` 对**未声明**形态仍报错
  （`tests/conftest.py:3072` 行为保留）。
- **`agents/pilot` 在两层都被覆盖**：`iter_sources` 的返回集合在两层的用例里**同一**（`branch_violations`
  与 `literal_violations` 用同一扫描面函数，不得各写一份）。
- **有牙齿**：`tests/unit/test_form_guard.py` 注入两条分支反例——① `f'if form == "{forms[0]}":'` 写在
  `agents/pilot/backends.py`；② 形态→值字典分派写在 `core/` 某文件——两条**各自**必须变红。

**反例**：

- 在 `core/`/`agents/` 里写 `if form == "movie": ...` / `if form in ("movie", "shortdrama"):` ⇒ 违规。
- 形态→值的字典分派或映射表（`FORM_PATHS = {"movie": "configs/movie.yaml"}`）⇒ 违规（字面量层同时捉住）。
- 形态名清单退回人工常量（例如在某测试里重新写死 `("movie", "shortdrama")`）⇒ 红（C7 的副本数机检 ①）。
- **反向扫描只看模块级赋值**（跳过函数体与装饰器实参）⇒ 红：那是**空跑假绿**——五处枚举点
  （`tests/unit/test_billing_gateway_cells.py:305`、`tests/contract/test_transfer_contracts.py:328`、
  `tests/unit/test_calibration_transfer.py:739`、`tests/unit/test_no_vendor_literals.py:43` 与 `:159`）
  会**全部逃逸**，而它们恰是"新形态名静默逃逸"的同一类位置。
- 在 `ops/form_guard.py` 之外另起一份 `declared_forms` / `form_literals` 实现（或在守卫内部复制一份
  同义函数、或在别的模块里再写一次同名派生）⇒ 红（C7 的副本数机检 ②：**定义点唯一**）。
- **诚实边界（不作反例、也不得冒充已覆盖）**：字符串拼接 / 转义 / 运行期拼名
  （`"movi" + "e"`、`\x6dovie`、从环境变量拼名）**不在**两条断言的判定面内——容器字面量 AST 扫描扫不到它，
  C5 的文本层也扫不到（源码里没有完整形态名）。这是**有意保留的判据边界**：守卫是"不得写死形态名"的
  **可机检近似**，拼接式规避靠代码评审兜住。**不得**因这两条断言全绿就宣称"零形态分支已被完全证明"；
  也不得为覆盖它而放宽判定（例如改成模糊匹配会引入假阳性）。
- 缺 `form_aliases` 键而静默按 `[]` 处理 ⇒ 红（"缺键即报错"，不留空）。
- `form` 取值与文件名 stem 不一致（`configs/ad.yaml` 写 `form: adx`）⇒ 报错。
- 把中文别名写进**代码**（`if form == "漫剧"`）⇒ 违规（中文按子串判定，同样在扫描面内）。

**兼容规则（零回改承诺）**：

- **015**：`tests/unit/test_form_switch.py:431-436` 的
  `Test零形态分支静态断言.test_全仓_agents_与_core_无形态判断分支` **断言体与循环体原位保留**，
  只把禁用模式的来源改为 `form_branch_patterns()`（**模式集字面不变** ⇒ 本用例改造前后判定等价）；
  `tests/unit/test_form_switch.py:161` 的 `set(movie_w) == set(short_w)` 等权重断言不受影响。
- **019 / 020**：`budget.channels.<id>.adapter` 的取值域**不变**（既有 `pilot_llm` 等原样保留、不重命名），
  渠道解析**不得**按形态分支（`agents/pilot/backends.py:74` 的 `channel_for_adapter` 反查口径不变）；
  020 的 `calibration.transfer.source_forms` / `target_forms` 是**形态 id 的配置声明**——
  配置里出现形态名**合法**（配置不在扫描面内），`core/calibration/transfer.py:456` 的
  `_registered_ids` 与 `:497-505` 的未声明形态显式拒绝口径不变。
- **既有两形态配置**：新增顶层键 `form_aliases` 是**两形态一致的新增键** ⇒ 段集合仍相等
  （`tests/unit/test_config_integrity.py:122-126`）、差异集不含它（`tests/unit/test_form_switch.py:341-379`
  的固定集合与 `tests/contract/test_pilot_contracts.py:434-453` 均**一字不改**）；**既有取值零改动**。
- **`form` 的自由字符串性质不变**：不新增形态白名单/枚举/路由表；
  `core/orchestration/models.py:197` 与 `ops/pilot.py:250` 的既有语义**不动**。

## C7 三副本委派收敛（单一实现、断言语义保留原位）

**目的**：形态名常量表的**副本数 ⇒ 1**（SC-003 的机检项），且收敛**必须**以"**委派**到单一实现、
各处断言语义保留原位"完成——**不得**借收敛之名删除或削弱任一处断言（裁决 4、
`specs/021-form-plugin-validation/spec.md:30`）。历史已证明副本必漂移（020 登记过一次引用漂移，
`specs/020-shortdrama-real-feedback/tasks.md:692`）。

**形状/接口**（三处**只换常量来源**，循环体与断言体**一字不改**）：

```python
# 单一实现（业务无关静态守卫）
# ops/form_guard.py
def form_literals(configs_dir: Path) -> tuple[str, ...]: ...
def form_branch_patterns() -> tuple[str, ...]: ...

# ① tests/unit/test_form_switch.py（原 :413-414 的两常量改为从守卫取；:421-429 / :431-436 的用例体保留）
class Test零形态分支静态断言:
    def test_core_与_agents_无形态字面量(self):
        # 扫描面 = ops/form_guard.iter_sources("core") + iter_sources("agents")（**含 agents/pilot**）
        ...   # 循环体与断言体**原位保留**

# ② tests/unit/test_billing_core_purity.py（原 :31 的 FORMS 与 :34-35 的两常量）
FORM_LITERALS = form_literals(configs_dir)          # 原先 = ("shortdrama", '"movie"', "'movie'")
FORM_PATTERNS = form_branch_patterns()

# ③ tests/unit/test_dev_core_degraded_purity.py（原 :28-29）
FORM_LITERALS = form_literals(configs_dir)
FORM_PATTERNS = form_branch_patterns()
```

- **`tests/unit/test_form_switch.py:423` 的 `if "pilot" not in path.parts` 删去**：那是**补面**
  （方向是**变严**），不是"为过断言而改守卫"；`:421-429` 的循环体与断言语句本身**保留**。
- **副本数的定义（可机检；由下面两条并列断言给出，缺一不可）**：副本数 = **形态名清单的定义点**数，
  必须恒 **1**（= `ops/form_guard.py` 的派生面）；三处测试是**委派点**（引用者），不计入定义点。
  **判定口径**：① **反向扫描**（任意容器字面量，含函数体与装饰器实参）含名称面字符串的**命中数恒 0**；
  ② `declared_forms` / `form_literals` 的**定义点唯一**（全仓同名 `FunctionDef` 各恰好一处）。
  单看 ① 的"0"与"定义点是否为 1"**不可区分**（0 与 1 无法用一个数字表达），故两条必须并列。

**机检断言**：

- **副本数恒 1（两条并列断言；单看任一条都不可判）**：
  - **① 反向扫描命中数恒 0（AST）**：反向扫描 `tests/**`、`ops/**`、`core/**`、`agents/**`、`web/**`、
    `dreaming/**` 中**任意** `ast.Tuple` / `ast.List` / `ast.Set` / `ast.Dict` **字面量**是否含**派生名称面**
    （`form_literals()`）中的任一字符串 ⇒ 命中数恒 **0**。**扫描面不得只看模块级赋值**：
    **函数体内**的容器与**装饰器实参**（`@pytest.mark.parametrize("form", ("movie", "shortdrama"))`
    一类）**同样**在扫描面内——否则会**空跑假绿**。今天至少五处形态枚举落在这些位置：
    `tests/unit/test_billing_gateway_cells.py:305`、`tests/contract/test_transfer_contracts.py:328`、
    `tests/unit/test_calibration_transfer.py:739`、`tests/unit/test_no_vendor_literals.py:43` 与 `:159`
    （前者为装饰器实参、后者为函数体内 `for ... in (...)` 元组）——**只扫模块级赋值时这五处全都扫不到**。
    这五处是**待转换的枚举点**（与 `tests/unit/test_form_switch.py:30` 等同族）：A3/A4 的"同族副本逐处
    改派生"处置面**随之扩展到这些位置**，转换（改为由 `declared_forms()` 驱动）后命中数恒 **0**。
    守卫模块与 `tests/unit/test_form_guard.py` 自身的"零人工形态常量"由同一扫描面保证：
    其合成反例由**派生值**在运行期构造 ⇒ 不是容器字面量。
  - **② `declared_forms` / `form_literals` 的定义点唯一**：AST 断言全仓（`core/**`、`agents/**`、
    `ops/**`、`tests/**`、`web/**`、`dreaming/**`）中**同名 `FunctionDef` 各恰好一处**
    （即 `ops/form_guard.py` 的派生面）：`len(defs("declared_forms")) == 1` 且
    `len(defs("form_literals")) == 1`；**≥2 处即红**（第二份派生面 / 第二份副本）。
  - **为什么必须两条并列**：① 的命中数本来就是 **0**，"把形态名写死成容器字面量"与
    "存在第二份派生实现"这两件事**无法用同一个数字区分**（0 与 1 不可区分）——① 管前者、② 管后者，
    两条合起来才等价于"**副本数恒 1**"。
- **三处委派点存在**：`tests/unit/test_form_switch.py` 的 `Test零形态分支静态断言`（字面量 + 分支两个用例）、
  `tests/unit/test_billing_core_purity.py` 的 `Test零形态与厂商字面量.test_无形态字面量与形态分支`、
  `tests/unit/test_dev_core_degraded_purity.py` 的 `Test零形态与厂商字面量.test_无形态字面量与形态分支`
  ——**三处都必须仍在**且都调用守卫的派生函数（引用者数 ≥ 3，派生面的**定义点数 == 1**，见上 ②）。
- **断言语义只增不减（逐条可机检）**：
  ① 三处的**循环体**必须仍遍历扫描面并逐文件读文本、**断言**必须仍为"违例集合为空 / 逐条报出
  `f"{path} 不得出现形态字面量：{banned}"` 形态的失败信息"（删除=红、把断言改成 `pass`=红）；
  ② 派生面**严格覆盖**原常量表：原表的三个字面量（`shortdrama`、`"movie"`、`'movie'`）在派生面下
  **全部**仍被判违规（`movie` 按**词边界**命中 `"movie"`/`'movie'`；`shortdrama` ∈ id 面）
  ⇒ 派生面**更严**（原表只禁 `"movie"`/`'movie'`，派生面禁**裸词** `movie`）；
  ③ 三处的既有"有牙齿"自检（`tests/unit/test_billing_core_purity.py:210-224`、
  `tests/unit/test_form_switch.py:443-461` 的调用点存在断言）**保留**。
- **注入即红（三处各自）**：把派生面中任一形态名作为裸词写进 `core/` 与 `agents/`（含
  `agents/pilot/backends.py`）⇒ 三处的字面量用例**各自**变红。

**反例**：

- 把三处合并成一处（删掉两处断言）⇒ 红（正是规格明文禁止的"借收敛之名删断言"）。
- 把三处的断言体换成"调用守卫 → `assert True`"⇒ 红（断言语义被削弱）。
- 保留三份副本、只加注释"以后一起改"⇒ 红（副本数 ≠ 1）。
- 在守卫模块里保留一份人工形态常量清单（哪怕与派生面"暂时一致"）⇒ 红（新形态仍会逃逸）。
- 把 `agents/pilot` 从新扫描面再次排除（换个写法：白名单、`if path.name == "backends.py": continue`）⇒ 红。
- 为让新形态过断言而把某处断言改成"仅对既有两形态生效"⇒ 红（新形态又回到静默逃逸）。

**兼容规则（零回改承诺）**：

- **015**：`tests/unit/test_form_switch.py` 的 `Test差异逐项可归因.test_全量差异都被配置文件承载`
  （`:341-379`）与 `Test零形态分支静态断言.test_形态切换只经配置文件`（`:438-441`）**不在**本契约的
  委派范围内——前者**原样保留**，后者按"**登记完备**"口径升级（**禁止删除**）：该口径的权威落点是
  `contracts/form-registration.md` **C10**（C9 = 五处登记点派生改法；**C10 = 登记完备口径**；
  C11 = 020 口径与 cadence），机检项另见 `data-model.md` I-14；本契约**只**提供它所需的
  **id 面** `declared_forms()`（C6），**不**承载该口径的断言。
- **017**：`tests/unit/test_dev_core_degraded_purity.py` 的 `Test零反向依赖`（`:163-179` 的
  AST import 扫描）**语义与断言体一字不改**；本契约只换其 `FORM_LITERALS`/`FORM_PATTERNS` 的
  **常量来源**，其扫描面（`core/degraded` 包）不变。
- **019**：`tests/unit/test_billing_core_purity.py` 的 `FORMS`（`:31`，驱动跑账本用例）改派生后，
  断言体**不删**；`OFFLINE_ASSEMBLIES`（`:268-285`）与 `:359` 的 `len(sites) == 14` 计数常驻
  （**仅在**离线演示真的构造 `LLMGateway` 时才同步登记与计数——最小可行形态不声明 judge ⇒
  推荐路径是**不构造**、本项**不动**）。
- **同族"两形态枚举"副本**（`tests/unit/test_billing_channels.py:52`、
  `tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`、
  `tests/unit/test_billing_core_purity.py:31`、`tests/unit/test_form_switch.py:30`）逐处改派生，
  **断言体不删**；形态特定取值假设改"逐形态声明期望值"，**不得**把新形态从派生面排除。

## C8 `agents/pilot/pilot.py:613` 裸形态词收敛 + `core/deployment/evidence.py:97` 例外登记

**目的**：把 `core/` + `agents/` 今天**仅有的两处**形态词命中处理干净——**一处收敛**（裸形态词，
按收紧后的口径属违规，**不得加例外**）、**一处登记**（配置文件路径字面量，属例外 E1，
**不得为过断言改写**）。这两处一起把"例外只有三条"从口号变成**恰好一处 E1 命中**的可机检事实。

**形状/接口**：

- **收敛点（唯一的既有违规）**：`agents/pilot/pilot.py` 的
  **`_require_duration_consistency`**（`:604`）docstring 中 `:613` 的 `（movie ⇒ 5400 s）`
  ——该行把**形态名绑定到取值**，恰恰**违反** E3 的界定（"只列举/说明、不绑定取值"），
  **不是**路径字面量 ⇒ 不适用 E1。收敛后的中性措辞：

  ```
  - 形态原值：`screenplay.target_duration_min × 60 == editing.target_duration_s`
    （形态值只作参数透传，不绑定具体形态的取值）；
  ```

  **保留**其后"任一不一致即拒绝启动并**点名两处实测值**（不静默择一、不按其一取值）"的**既有语义**；
  **`_require_duration_consistency`（`:604`）** 体内的两个比较点（`:620-625` 的形态原值比较，与 `:629`
  起的生效值/运行级比较；两处各自调用被比较工具 `_require_same_duration`，后者定义在 `:289`）
  **逻辑零改动** ——本收敛**只改 docstring 措辞**，比较点与被比较工具**都不动**（`:613` 的裸形态词
  属 `_require_duration_consistency` 的 docstring，**不是** `_require_same_duration` 的说明）。

- **例外登记（E1 的唯一既有命中点）**：

| 字段 | 值 |
| --- | --- |
| 文件 | `core/deployment/evidence.py` |
| 所属符号名 | `_unbiasedness_result`（`:90`） |
| 行 | `:97`（仅定位辅助） |
| 命中内容 | `source="configs/movie.yaml deployment.gate.require_unbiasedness=false"` |
| 例外代号 | **E1**（配置文件路径字面量：同一字符串含 `configs/` 与 `.yaml`，形态名仅作路径片段） |
| 处置 | **登记为 E1，原样保留**；**不得**为过断言改写该行（改写即改变既有证据的来源标注语义） |

- **不入扫描面的既有字面量（登记以免"误补"）**：`web/server.py:320`、`web/export.py:301`、
  `dreaming/deploy_hook.py:25`、`ops/billing.py:97`、`ops/ingest_metrics.py:121` 的 `configs/movie.yaml`
  默认值；`ops/demo_merged_pool.py:55` 的 `FORM = "movie"`、`ops/smoke_llm.py:358` 的缺省回落
  `"shortdrama"`——**不在**扫描面（E2：扫描面只有 `core/` + `agents/`），**不得**为过断言而改写。

**机检断言**：

- **裸词收敛率 100%**：`agents/pilot/pilot.py` 全文（含 docstring 与注释）在**派生名称面**下的违规数
  == **0**；为该处**开例外**的次数恒 **0**（E3 的判定**不得**被放宽到"该行含 `⇒` 也算中性描述"）。
- **E1 恰好一处**：`core/` + `agents/` 范围内 `classify_exception(hit) == "E1"` 的命中点集合
  == {(`core/deployment/evidence.py`, `_unbiasedness_result`, `:97`, `configs/movie.yaml …`)}；
  **新增**任一 E1 命中点 ⇒ 红（例外面不得扩张）。
- **E3 既有命中数为 0**：收敛后 `core/` + `agents/` 内 `classify_exception(hit) == "E3"` 的命中点数 == 0
  （docstring 里**不得**有形态名与取值的绑定；docstring 里出现形态名本身即需中性化）。
- **收敛不改语义**：`_require_duration_consistency` 的**函数体逻辑**（两处比较与容差
  `DURATION_TOLERANCE_S`，`agents/pilot/pilot.py:57`）逐字不变；"任一不一致即拒绝启动并点名两处实测值"
  的错误语义由既有用例继续守住（改造前后行为对照）。
- **既有两处之外为零**：`core/` + `agents/` 内**未被例外覆盖**的形态词命中数 == 0（即"只有两处命中"
  这条勘查事实在收敛后变成"零违规 + 恰好一处 E1"）。
- **不改写既有默认值**：上表"不入扫描面的既有字面量"7 处的**文件字节**（相关行）在本次改造前后
  逐字不变（改动即红）。

**反例**：

- 为 `agents/pilot/pilot.py:613` 开 E3 例外（例如放宽到"docstring 一律豁免"）⇒ 红（规格明文"不得为它加例外"）。
- 把 `:613` 的形态名换成另一个形态名（仍绑定取值）⇒ 红（换名不解决问题，`（shortdrama ⇒ 5400 s）`
  同样是"绑定取值"）。
- 删掉该 docstring 行以求通过 ⇒ 红（既有语义"形态原值口径"的说明被删除 ⇒ 属"借收敛之名削弱"）。
- 为过断言改写 `core/deployment/evidence.py:97` 的 `source` 字符串（例如改成不含路径的中性措辞）⇒ 红
  （该行是既有证据的来源标注，属 E1 例子面，**不得**改写）。
- 把 `ops/`/`web/`/`dreaming/` 的既有配置路径默认值改成不含形态名的写法以求"全绿"⇒ 红
  （它们**不在**扫描面内，改写属越界）。
- 新增一处 E1 风格的 `source="configs/<new-form>.yaml ..."` 并声称"同理属例外"⇒ 红
  （E1 的既有命中面**恰好一处**，新增须走契约修订流程）。

**兼容规则（零回改承诺）**：

- **015 / 018**：`agents/pilot/pilot.py` 的 `config_completeness`（`:377`，调用点 `:507`）与
  `precheck` 链路**语义不变**；本收敛只改一个 docstring 行 ⇒ 运行记录的形态值透传与
  `pilot.rehearsal.status`（`:53` 的取值域）口径零影响；`agents/pilot/stages.py:126-134` 的
  单一映射声明**不动**。
- **014**：`core/deployment/evidence.py` 的该行只**登记不改**⇒ 既有证据来源标注逐字保留；
  `deployment.spot_check.pending_alert_days` 的形态差异键口径（`tests/unit/test_form_switch.py:376-379`）
  **不动**。
- **既有断言的引用锚点改符号名**：本契约涉及的既有守卫一律按**符号名**引用
  （`Test零形态分支静态断言.test_core_与_agents_无形态字面量`、
  `Test零形态与厂商字面量.test_无形态字面量与形态分支`、
  `TestC10到C13试水运行.test_c13_两套配置差异可归因且无形态分支`、
  `_require_duration_consistency`、`_unbiasedness_result`），行号只作人读辅助——020 的引用漂移教训
  （`specs/020-shortdrama-real-feedback/tasks.md:692`）**不再重演**。
- **例外面冻结**：E1/E2/E3 三条的**谓词**与**命中面**（E1 恰好一处、E3 零处）一旦被修订，
  必须同步修订本契约与 `data-model.md` I-10；**不得**在实现里就地放宽。

### 场景

1. 新增第三份形态配置（`form:` + `form_aliases`）⇒ `form_literals()` 自动含该形态名与中文别名，
   把该名字写进 `agents/pilot/backends.py` ⇒ 字面量层与分支层**各自**变红（注入即红率 100%）
2. 三处委派后逐处注入裸词形态名 ⇒ 三处**各自**变红；删除任一处断言 ⇒ 副本/语义机检变红
3. 收敛 `agents/pilot/pilot.py:613` 后跑两层扫描 ⇒ 违规数 0、E1 命中数 1（`core/deployment/evidence.py:97`）、
   E3 命中数 0；`ops/`/`web/`/`dreaming/` 的既有默认值相关行逐字不变
4. 派生面失效取证：临时移走某配置的 `form` 键 / `form_aliases` 键 ⇒ 派生**报错**（不静默跳过、
   不静默全绿）
