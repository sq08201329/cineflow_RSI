# 契约：接入改动清单、机制侧总账与 CLI/离线演示（C12~C14）

> 对应规格 FR-003 / FR-004 / FR-010 / FR-011 / FR-013 / FR-014、SC-001 / SC-002 / SC-008、
> US1 场景 2 / 3、US3 场景 1 / 2 / 5、边界情况「"仅新增配置 + 插件"今天不成立」「迁移口径…」
> 「插件不得成为绕过门禁的后门」「诚实边界」；裁决 1~6 的口径经本契约落成**证据面**。
> 依据：`research.md` 决策 9（清单由 git 派生、按类别判定、append-only、与机制侧分账）与决策 11
> （最小可行形态 + "未标定"标注）、`plan.md` 阶段 A5 / B1 / B3。
>
> **实现面（真实文件）**：新增 `ops/form_onboarding.py`（清单 + 登记点完备性 + 机制侧总账清单）、
> 新增 `ops/form_plugin.py`（CLI 门面，薄转发）、新增 `ops/demo_form_plugin.py`（离线端到端演示）、
> 复用 `ops/form_guard.py`（形态名派生与两层扫描，见 `contracts/zero-form-branch.md` C5~C8）、
> `configs/<new-form>.yaml`（新形态配置）、`core/orchestration/models.py:36` 的 `fingerprint_of`（配置指纹口径）。
>
> **性质**：本契约是"**零代码改动**"这句口号的**可证伪面**——"仅"这个字只有靠完整改动清单才能被证明或证伪；
> 它同时守住**机制侧与接入侧的分账**（FR-013）：把本特性自身的机制改动混进接入账，会让判据自相矛盾。
> **不新造第二个端到端演示入口、不新增运行时依赖、不写任何权威数据面**（产物是文件、append-only）。
>
> **锚点口径**：以**符号名**（函数名 / 常量名 / 字段名）锚定，行号只作定位辅助；`git` 为本契约面的外部依赖
> （既有开发环境与 CI 均有，见 `.github/workflows/ci.yml`）。

---

## C12 接入改动清单（基线派生 / 类别判定 / append-only / 指纹 / 越界即红）

**目的**：把 G5 验收原文里那个"**仅**"字变成一份**可审计的清单**：一次新形态接入之后，跑一次机检即得到
"本次一共改了哪些文件"的**完整**清单，并**逐条**判定是否越界（配置 / 插件 / 测试与文档 ⇒ 放行；
改动 `core/` / `agents/` / `ops/` / `web/` 既有模块逻辑 ⇒ **越界**）；越界即**非 0 退出码 + 逐条点名**，
且清单**与 git 实际改动集一致**（FR-003 / FR-004；SC-002）。

**形状·接口（`ops/form_onboarding.py`，新增）**：

| 符号 | 签名 | 职责 |
| --- | --- | --- |
| `changed_files` | `(baseline_ref: str) -> tuple[ChangedFile, ...]` | 由 git **派生**改动集合：`git diff --name-status <baseline_ref>` **∪** `git ls-files --others --exclude-standard`（未跟踪的新增文件也必须在内） |
| `classify` | `(path: str, status: str) -> str` | 按**类别**判定（表见下），**不是**按路径前缀 |
| `config_fingerprint` | `(config_path: str \| Path) -> str` | 该形态配置文件的 BLAKE3 十六进制摘要**前 12 位**（口径沿用 `core/orchestration/models.py:36` 的 `fingerprint_of`，**不改**该函数本身） |
| `build_manifest` | `(config_path, baseline_ref, *, mechanism_ledger_ref=None) -> dict` | 产出完整清单（逐条路径 + 类别 + 越界标记 + 计数 + 回溯字段） |
| `write_manifest` | `(manifest: dict, out_dir: str \| Path) -> Path` | **append-only** 落盘：清单文件 + `index.jsonl` 追加一行 |
| `REGISTRATION_SITES` | 常量（**恰好五处**） | 五处登记点的常驻白名单（`contracts/form-registration.md` C9.7 的实现面） |
| `site_registration_status` | `(site) -> dict` | 逐处判定"已登记 + 已委派"（`contracts/form-registration.md` C9 / C10 的实现面） |
| `sixth_site_scan` | `() -> tuple[str, ...]` | 反向扫描"形态清单被写死"的代码点，断言其集合 == 白名单面 |
| `MECHANISM_LEDGER` | 常量（**恰好六项**） | 机制侧总账的常驻登记（C13 的实现面） |

**改动集合的派生（一致率 100% 由构造保证）**：改动集合**直接由 git 派生**、**不手工列举**
⇒ "自报漏项"在机制上不可能发生。两条纪律：
① **必须含未跟踪的新增文件**（新形态配置与新插件在接入时通常尚未 `git add`——漏了它们，清单会把
"新增"整类漏掉、越界计数反而"看起来干净"）；
② CLI **只读** git 与文件、**只写** `--out`：**不得** `git add` / `commit` / `reset`，**不得**触碰工作区
（本项目的纪律：本特性的工具**不得**成为"改代码来过门禁"的通道）。

**判定按类别而非路径前缀（FR-003 原文）**：

| 类别（`category` 枚举） | 判定 | 结论 |
| --- | --- | --- |
| **配置** = `config` | **新增** `configs/*.yaml` | 放行 |
| **插件** = `plugin` | **新增** `core/evaluators/plugins/**`（业务无关的通用件）或 `agents/<agent>/evaluators/**`（语义与某 Agent 绑定时） | 放行；两类都**必须经 `impl` 声明才生效**（"目录不决定可用性、配置声明才决定"，机检在 `contracts/plugin-config.md` C1） |
| **测试与文档** = `test_doc` | **新增或修改** `tests/**`、`docs/**`、`specs/**`，**以及仓库根 `README.md`**（交付文档，裁决 2026-09-26） | 放行（登记点同步与文档必然要改这些；规格裁决把"改动面 = 配置 + 插件 + 测试与文档"逐字写定）。**放行面只含文档路径**——六目录的既有文件修改与"三类之外的新增文件"仍是越界 |
| **越界** = `out_of_scope` | **任何既有文件的修改或删除**位于 `core/` / `agents/` / `ops/` / `web/` / `dreaming/` / `policies/`；**或**上述三类之外的**新增**文件（例如新增 core 机制模块、新增 ops CLI） | **越界**：退出码 **1** + **逐条点名**路径与类别（**不得**只报总数、**不得**静默放行） |

- **为什么必须是"类别"而不是"前缀"**：`agents/<agent>/evaluators/` 前缀下，**新增**插件文件是放行的、
  而**修改**该前缀下的既有文件是越界的——前缀判定**无法区分这两件事**（规格裁决 2 的理由）。
  机检因此对每个改动同时看 `status`（`A` 新增 / `M` 修改 / `D` 删除）与**类别**，
  `M` / `D` 落在上述六个目录即越界（`tests/**` / `docs/**` / `specs/**` 与仓库根 `README.md` 除外）。
  **路径解码**：清单由 `git -c core.quotePath=false` 派生 ⇒ 非 ASCII 路径（中文文件名）**原样 UTF-8**
  进入 `path` 字段（默认的 C 转义引号会让前缀判定失配、把中文文档误判越界；机检 = 清单路径与真实路径逐字相同）。
- **`ops/` 不设放行面（"新增 ops CLI = 越界"的牙齿必须保住）**：`ops/**` 的**任何**改动
  （新增或修改）都判 **`out_of_scope`**——**不得**把 `ops/demo_*.py`、`ops/form_*.py` 一类文件加进放行面
  （一旦加，本清单就再也证明不了"新形态接入 = 仅新增配置 + 插件"）。
- **演示脚本 `ops/demo_form_plugin.py` 的归属（I-09 裁决，必须写清，否则 B1/B2 验收永远红）**：
  该脚本**属机制侧资产**——**由阶段 A5 创建**（与清单/CLI 同批），且**形态无关**（遍历 `declared_forms()`
  对**已声明的每一个**形态都成立，对新形态**零改动**即可演示）。⇒
  **它位于 `baseline_ref` 之前**，**不进**新形态接入的改动清单；以机制 ref 为基线跑 `onboarding` 时，
  它**既不在 `changes[]` 里、也不计入 `violations`**。**B1/B2 两个举正面阶段只新增配置与插件**
  （外加测试与文档）——不得把演示脚本记成接入侧改动，也不得把它当"越界"来判（那是**基线取错**
  的症状：基线若早于 A5，`ops/demo_form_plugin.py` 会作为"新增 ops 文件"被正确地判越界——
  这正是"基线必须取机制落地后"的可见后果）。
- **产物目录 `--out` 必须是临时目录（I-07，不得落仓库内）**：`--out` **必须**指向 `tmp_path` 类
  **临时目录**（CLI 默认即 `tempfile.TemporaryDirectory`；显式传参时也必须落在临时目录内）。
  理由（实测）：`.gitignore` **没有** `.specify` 规则 ⇒ 把产物写进 `.specify/onboarding` **必然**让
  仓库根的未跟踪文件集合发生变化，与"**仓库根零新增文件 / 未跟踪集合前后相等**"这条断言**直接矛盾**。
  ⇒ 判据统一为：**跑完后 `git ls-files --others --exclude-standard` 的输出集合前后逐条相等**；
  产物一律只落临时目录（`--keep-work-dir` 只影响演示的临时目录是否保留，**不影响**该判据）。

**清单形状（JSON，字段名权威在本 C12）**：

```json
{
  "schema": 1,
  "baseline_ref": "<接入前的提交 ref>",
  "mechanism_ledger_ref": "<机制落地后的提交 ref>",
  "form": "<新形态 id，由配置的 form: 派生>",
  "config_path": "configs/<new-form>.yaml",
  "config_fingerprint": "<BLAKE3 前 12 位>",
  "changes": [
    {"path": "configs/<new-form>.yaml", "status": "A", "category": "config", "violation": false,
     "reason": "新增形态配置"},
    {"path": "core/evaluators/plugins/<plugin>.py", "status": "A", "category": "plugin", "violation": false,
     "reason": "新增业务无关通用插件（经 impl 声明才生效）"},
    {"path": "tests/unit/test_form_registration.py", "status": "M", "category": "test_doc", "violation": false,
     "reason": "登记点同步"}
  ],
  "counts": {"配置": 1, "插件": 1, "测试与文档": 1, "越界": 0, "既有模块被修改": 0},
  "violations": [],
  "mechanism_changes_included": false,
  "zero_code_onboarding": true,
  "exit_code": 0
}
```

**字段 → 类型 → 取值域（权威表；C12 是唯一权威，`contracts/onboarding-ops.md` 之外的文件引用本表）**：

| 字段 | 类型 | 取值域 / 规则 |
| --- | --- | --- |
| `schema` | int | **当前取值 `1`**（本表即语义版本 1）。**演进规则**：**只增不改**——新增字段必须可选且**带默认**（旧清单仍可读）；**既有字段的语义/取值域不得改写**（改写 ⇒ `schema` 必须升到 `2`）。消费者：**未知的更高 `schema` ⇒ 拒绝采信并报错**（不得猜测、不得静默降级） |
| `baseline_ref` | str | **非空**且可被 `git rev-parse` 解析；**必须 ≠ `mechanism_ledger_ref`** |
| `mechanism_ledger_ref` | str | **非空**且可解析；= 机制落地后的提交（见 C13） |
| `form` | str | 非空；**逐字等于**该配置的 `form:` 取值（`contracts/form-registration.md` C10 的"文件名 stem == `form:` 取值"） |
| `config_path` | str | 非空；相对仓库根；= `configs/<form>.yaml` |
| `config_fingerprint` | str | 64 位小写十六进制**前 12 位**（`core/orchestration/models.py:36` 的 `fingerprint_of` 前缀） |
| `changes[]` | list | 每条 = `{path, status, category, violation, reason}`；条目与 git 派生集合**逐条相等** |
| `changes[].path` | str | 非空；相对仓库根（与 git 的路径拼写逐字一致） |
| `changes[].status` | str | **枚举 `{"A", "M", "D"}`**（= `git diff --name-status` 的取值；未跟踪新增文件恒为 `"A"`） |
| `changes[].category` | str | **枚举 `{"config", "plugin", "test_doc", "out_of_scope"}`**（**英文，权威**）：`config` = **新增** `configs/*.yaml`；`plugin` = **新增** `core/evaluators/plugins/**` 或 `agents/<agent>/evaluators/**`（且仍须经 `impl` 声明才生效）；`test_doc` = `tests/**`、`docs/**`、`specs/**` 的新增或修改，**以及仓库根 `README.md`**（交付文档；裁决 2026-09-26）；`out_of_scope` = **越界**（既有文件的修改/删除落在 `core/`/`agents/`/`ops/`/`web/`/`dreaming/`/`policies/`，或四类之外的新增文件） |
| `changes[].violation` | bool | `== (category == "out_of_scope")`（**恒等式**，机检） |
| `changes[].reason` | str | 非空的人可读理由（**不得**为空串；越界条必须点名**具体规则**，如"修改既有模块 `core/evaluators/composite.py`"） |
| `counts` | dict | **键名（中文，权威）**：`{"配置", "插件", "测试与文档", "越界", "既有模块被修改"}`；值为 int；前四项分别 = `changes` 中四个 `category` 的条数，末项 = `violation is True` 且 `status ∈ {"M","D"}` 的条数（SC-001① 的落点）。**键名与 `category` 的英文枚举是两套标识**：`counts` 的**中文键**只作报表人读面，**判定一律走 `category` 的英文枚举**（不得按中文键判定） |
| `violations[]` | list[str] | 越界路径的**逐条点名**（= `changes` 中 `violation is True` 的 `path`，顺序与 `changes` 一致；**不得**只报总数） |
| `mechanism_changes_included` | bool | **恒为 `false`**（为 `true` ⇒ 报错：机制改动被混进接入账，见 C13） |
| `zero_code_onboarding` | bool | **只对基线之后的接入侧成立**（机制侧另有总账，见 C13） |
| `exit_code` | int | `0` 全在界内 / `1` 越界或判定失败 / `2` 用法或配置错误 |
| `change_count` | int | **`index.jsonl` 行内的字段**（清单本体用 `counts` 汇总）；恒 `== len(changes)`。⇒ `index.jsonl` 每行的字段集**恰好七键**：`baseline_ref` / `form` / `config_path` / `config_fingerprint` / `change_count` / `violations[]` / `exit_code`（与 `data-model.md` 的行样例逐字一致） |

- **不变量（机检）**：`change_count == len(changes)`；`len(violations) == counts["越界"]`；
  `changes[].violation == (category == "out_of_scope")`（逐条）；`schema == 1`。
- **产物名（权威在本 C12 / C14，其余文件引用之）**：清单 = `<out>/onboarding-<form>-<seq:04d>.json`；
  `guard` / `registration` / 演示的报告 = `<out>/guard-report.json` / `<out>/registration-report.json` /
  `<out>/demo-report.json`（**不是** `manifest-<form>-<序号>.json` 一类另一套写法——
  `data-model.md` 里的该写法以**本条**为准）。
- **append-only**：清单文件名 `<out>/onboarding-<form>-<seq:04d>.json`（写后**不回改**；同内容重跑产生**新序号**
  文件，旧的**不覆盖**），`<out>/index.jsonl` **追加**一行
  `{baseline_ref, form, config_path, config_fingerprint, change_count, violations[], exit_code}`。
- **退出码**（与既有工具一致，常量符号 `EXIT_OK` / `EXIT_FAILED` / `EXIT_USAGE` 见 `ops/transfer.py`）：
  `0` 全部改动在界内｜`1` 有越界或判定失败（**逐条点名**）｜`2` 用法或配置错误
  （缺 `--baseline`、基线 ref 不可解析、配置不可读、`declared_forms()` 派生失败）。

### 机检断言（C12）

- **一致率 100%（SC-002）**：`changed_files(ref)` 的路径集合与由同一 git 派生面解析出的集合**逐条相等**
  （同一派生面的双重取证，不靠人写）；**未跟踪新增文件不遗漏**——在临时工作区造一个未跟踪新文件后，
  清单**必须**含它（这是最常见的漏项模式，单列一条反例）。
- **越界判定 100% 且逐条点名**：对一份**故意越界**的改动（例如在 `core/evaluators/composite.py` 里加一行注释）
  ⇒ 判 `out_of_scope`、退出码 **1**、清单里**点名该路径与类别与理由**；"只报总数不报路径"的次数**恒 0**。
- **`counts["既有模块被修改"] == 0`** 对**接入侧**清单（基线 = 机制落地后）恒成立（SC-001①）。
- **字段权威表的不变量逐条机检**：`schema == 1`；`len(violations) == counts["越界"]`；
  逐条 `changes[].violation == (category == "out_of_scope")`；`changes[].category` ∈
  **`{"config", "plugin", "test_doc", "out_of_scope"}`**（英文枚举；出现中文类别值或第五个取值 ⇒ 红）；
  `changes[].reason` 去空白后非空；`counts` 的**键名**恒为那五个中文键（多/少键 ⇒ 红）。
  **判定一律走 `category` 英文枚举**：把中文键当作判定输入的实现 ⇒ 红（两套标识不得混用）。
- **`--out` 必须落临时目录（I-07 的机检形式）**：跑完后 `git ls-files --others --exclude-standard`
  的输出集合**前后逐条相等**；把产物写进仓库内路径（含 `.specify/**`——`.gitignore` **没有**该规则）⇒
  该断言**必然**变红。
- **append-only 与可回溯**：同目录（临时目录）连跑两次 ⇒ `index.jsonl` 行数 `+2`、已有清单文件字节**不变**；
  每份清单含 `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_fingerprint` 四个回溯字段。
- **只读纪律**：`ops/form_onboarding.py` 与 `ops/form_plugin.py` 内**零** `git add` / `commit` / `reset` /
  `checkout` 子命令字符串（文本 + AST 双层）；跑完后仓库根的未跟踪文件集合**前后相等**。
- **两类新增的放行是类别判定**：新增 `agents/<agent>/evaluators/plugins.py` 判 `plugin`（放行），
  而**修改** `agents/<agent>/evaluators/__init__.py` 判 `out_of_scope`（同前缀、相反结论——机检必须复现这一对）。
- **`ops/**` 无放行面**：`ops/` 下的**新增与修改**一律 `out_of_scope`（把 `ops/demo_*.py` 或
  `ops/form_*.py` 加进放行面 ⇒ 红）。
- **演示脚本不进接入账（I-09 的机检形式）**：以**机制 ref**（A5 之后）为基线跑 `onboarding` 时，
  `ops/demo_form_plugin.py` **不在** `changes[]` 里；以 **A5 之前**的 ref 为基线 ⇒ 它被正确地判
  `out_of_scope`（这正是"基线必须取机制落地后"的可见后果，也是反例 9 的取证）。

### 反例（C12）

1. 清单靠人写或靠测试静态枚举 ⇒ 一致率无法证明、且"自报漏项"恰是 FR-004 点名要排除的情形 ⇒ 红。
2. 只报总数不报路径 ⇒ 红（SC-002 的"只报总数次数恒 0"）。
3. 把 `tests/**` 的修改也计为越界 ⇒ 与"改动面 = 配置 + 插件 + 测试与文档"直接冲突（登记点同步必然要改测试）⇒ 红。
4. 基线取 `main` 或仓库初始提交 ⇒ 机制侧总账被一起算进越界 ⇒ 判据自相矛盾 ⇒ 红。
5. 漏掉未跟踪的新增文件（只跑 `git diff --name-status <ref>`）⇒ "新增配置 + 插件"整类消失、越界计数"看起来干净" ⇒ 红。
6. CLI 自动改写配置或自动 `git commit` 以求"清单干净" ⇒ 红（工具不得成为"改代码来过门禁"的通道）。
7. 同一次接入重复跑 `onboarding` 时**覆盖**上一份清单 ⇒ 红（append-only 失守）。
8. 按路径前缀判定（凡 `agents/` 下改动一律越界、或一律放行）⇒ 红（前缀判定无法区分"新增插件"与"修改既有模块"）。
9. 把 `ops/demo_form_plugin.py` 记成**接入侧**改动、或把它的创建挪回 B1 ⇒ 以机制 ref 为基线时清单凭空多出一条
   `ops/**` 新增 ⇒ 按本契约的 `ops/` 规则**必然**判 `out_of_scope` ⇒ B1/B2 验收**永远红**（I-09）。
10. 把 `ops/demo_*.py` / `ops/form_*.py` 加进放行面以"救"第 9 条 ⇒ 红（"新增 ops CLI 即越界"的牙齿被拆）。
11. `--out` 写成 `.specify/onboarding`（仓库内路径）⇒ 未跟踪集合前后不等 ⇒ 红（I-07）。
12. `changes[].category` 用中文（`"配置"` 等）或另立第五个取值 ⇒ 红（字段权威表的英文枚举是唯一权威）。

### 兼容规则（C12）

- 新增产物只落 `--out`，且 `--out` **必须**是临时目录（`tmp_path` 类；演示与验收默认走临时目录），
  仓库根**零残留**（判据 = 未跟踪集合前后逐条相等）；不改任何既有产物与既有 CLI 的参数/退出码语义。
- 清单的"测试与文档"类别把 `specs/**` 也算放行——本特性自身的设计件变更因此**不会**污染接入账。
- **机制侧资产（演示脚本 / 守卫 / 清单 / CLI）一律属 `baseline_ref` 之前**：它们是机制落地的一部分，
  不进新形态接入的改动清单（见上"I-09"与 C13 的分账规则）。
- 本契约**不引入**新的权威数据面（无 DB 变更、无迁移、无发现树写入）；发现树 / `CostRecord` /
  `eval_breakdown` / 得分**一律不动**（原则一 / 二）。
- `git` 是本契约面**唯一**的外部依赖，与既有开发环境一致（`.github/workflows/ci.yml` 的 CI 上同样可用）。

---

## C13 机制侧总账的登记与"不得冒充零代码改动"的机检

**目的**：把 FR-013 的"两类改动**不得混同**"落成**可机检事实**：本特性自身对 `core/` / `agents/` / `ops/`
与既有配置的机制改动**必须**逐项登记成总账，且"**以本特性的机制改动冒充'零代码改动'**"这个具体错误
**必然被抓**（FR-013 末句：**不得**把机制改动本身写成"零代码改动"）。

**形状·接口（`ops/form_onboarding.py` 的 `MECHANISM_LEDGER`，常驻清单）**：

- `MECHANISM_LEDGER`：**恰好六项**（**不新造第 7 项**），每项形如
  `{"step": "<A1~A5>", "items": [{"path": "...", "kind": "new|modified"}]}`，**逐项与 FR-013 的六项总账一一对应**
  （编号即 FR-013 的序号）：

| # | 总账项（FR-013 逐项） | `items` 落在哪一组路径 |
| --- | --- | --- |
| ① | 配置驱动的插件声明与唯一装配点（声明面 + `importlib` 解析 + 通用参数通道 + 既有装配面改委派 + 声明面承载 + 测试侧夹具与存根 + **A1 夹具同步面**） | **新增**：`core/evaluators/plugin.py`、六个薄工厂 `agents/visual/evaluators/plugins.py` / `agents/dev/evaluators/plugins.py` / `agents/screenplay/evaluators/plugins.py` / `agents/storyboard/evaluators/plugins.py` / `agents/sound/evaluators/plugins.py` / `agents/editing/evaluators/plugins.py`、六份 `agents/<agent>/config.py`（声明面承载）、`tests/unit/test_evaluator_plugin_assembly.py`、`tests/contract/test_plugin_contracts.py`、`tests/plugin_fixtures.py`、`tests/plugin_stubs.py`、`tests/unit/fixtures/evaluator_assembly_baseline.json`（**装配序列对照基线夹具**）、`tests/unit/test_form_no_new_dependency.py` 与 `tests/unit/fixtures/dependency_baseline.json`、**A1 夹具同步面**（**口径与纠正见下**）；**修改**：`core/evaluators/errors.py`、`core/calibration/config.py`（cadence 收口的加载面）、六个装配函数所在文件（`agents/visual/loop.py`、`agents/dev/evaluators/__init__.py`、`agents/screenplay/evaluators/__init__.py`、`agents/storyboard/evaluators/__init__.py`、`agents/sound/evaluators/__init__.py`、`agents/editing/evaluators/__init__.py`）、`configs/movie.yaml` 与 `configs/shortdrama.yaml`（新增 `evaluators` 段） |
| ② | 扫描面补面（字面量与判断分支两层均覆盖 `core/` + `agents/` **含 `agents/pilot`**、锚点改符号名） | **新增**：`ops/form_guard.py`、`tests/unit/test_form_guard.py`；**修改**：`tests/unit/test_form_switch.py`、`tests/unit/test_billing_core_purity.py`、`tests/unit/test_dev_core_degraded_purity.py` |
| ③ | 形态名由 `configs/*.yaml` 派生 + 三副本收敛为单一实现（副本数 ⇒ 1） | **新增**：`ops/form_guard.py`（与②同一路径，并集去重）；**修改**：`tests/contract/test_pilot_contracts.py`、`tests/unit/test_billing_channels.py`、`tests/contract/test_billing_contracts.py`、`tests/unit/test_pilot_rehearsal.py` |
| ④ | `agents/pilot/pilot.py` 的裸形态词收敛（`:613`）+ 020 口径逐项机检（`form_clause_completeness`） | **修改**：`agents/pilot/pilot.py`；**新增**：`tests/unit/test_form_clause_completeness.py` |
| ⑤ | "恰好两份"升级为**登记完备**口径（禁止删除） | **新增**：`tests/unit/test_form_registration.py`；**修改**：`tests/unit/test_form_switch.py`（与②同一路径，并集去重）、`tests/unit/test_config_integrity.py`、`tests/conftest.py`、`tests/unit/test_pilot_chain_seven.py` |
| ⑥ | 接入改动清单机检与 CLI/演示（**A5 同批创建**） | **新增**：`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py`（**形态无关**：遍历 `declared_forms()`，对新形态零改动即可演示——见 C12 的 I-09 条）、`tests/unit/test_form_onboarding.py`、`tests/contract/test_form_onboarding_contracts.py` |

**A1 夹具同步面（口径 + 纠正登记；归入总账项 ①）**——**成员 = 实际被修改的路径**，不是"符号命中面"。
**权威在本契约之外**：成员清单 = `MECHANISM_LEDGER_PATHS` 中归入总账项 ① 的、真实的 `tests/**` 夹具
改动路径（实现面 `ops/form_onboarding.py` 的常量 + 交付件 `quickstart.md` 的"机制侧总账"表，
两处**集合相等**即判据）；**本契约不复抄清单**——同一份清单两处副本必然漂移，契约只引用口径。

- **口径（含本次纠正，如实登记）**：此前本契约写的是"**凡在 `tests/**` 内调用六个 `build_*_evaluators`
  的测试文件**"（符号命中面，`grep` 实测 **16 个文件**），并据此登记了 **15 条**（`tests/conftest.py`
  归 ⑤）——**口径错**：ledger 的成员必须是**实际被修改**的路径，而 021 实现的实测改动集里，
  该口径**只兑现 5 条**（`kind = "modified"`、用**内联配置字典**构造评估器因而**真的**需要补
  `evaluators` 声明段的 `tests/**` 文件）。**纠正后的口径**：成员 = 权威常量里归入 ① 的真实改动夹具路径。
- **为什么符号面 ⊋ 改动面**：① 其余命中文件从**真实形态配置**取声明段（不是内联字典）⇒ 无需改动；
  ② 原因之一（本次纠正的连带面）：**A3 把三副本委派收敛**（形态名常量改由 `declared_forms()` 派生、
  断言体与原循环体保留原位）后，部分夹具**不再需要逐处改写**。⇒ 机检只以**实测改动集**为准，
  不拿"符号调用点"充数。
- **不存在的路径一律不登记、更不为凑数保留**（该结论仍然成立且有用）：`plan.md` 阶段 A1 第 7 步以
  `test_{…,visual}_composite.py` 与 `tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`
  的**形式**给出该面——**实测** `tests/unit/test_visual_composite.py` **不存在**（visual 的装配调用点在
  `tests/unit/test_visual_consistency.py`），`tests/contract/` 下**只有** `test_dev_contracts.py` 命中
  （`test_screenplay_contracts.py` 等**不存在**）；且这两个**真实路径本身在本特性里也未改动** ⇒
  一律不入账，并在 `NOT_LEDGER_ITEMS` 的剔除面里逐条登记。
- **机检（可派生、不靠人工维护）**：① 反向扫描 `tests/**` 的六个 `build_*_evaluators` 调用点得到
  符号命中面；② 断言 `MECHANISM_LEDGER_PATHS` 与 `quickstart.md` 的机制侧总账表**集合相等**；
  ③ 符号命中面与改动面之间的**差集**必须逐条出现在剔除面（`NOT_LEDGER_ITEMS`）——两处口径
  **都**有牙齿（漏登记 ⇒ 红；拿符号面充数 ⇒ 剔除面里对不上 ⇒ 红）。

- **`MECHANISM_LEDGER_PATHS`** = 六项 `items`（含 A1 夹具同步面的真实改动集）的路径**并集**，**不写死条数**
  （当前实测 **69 条**，仅作对照、**不是判据**）。**判据 = 文档表与常量的集合相等**（见机检断言 4/5）。
  **按 FR-013 六项归属的当前实测分解（仅作对照，非判据；重复路径只在其**首次归属项**计数）**：
  ① = **35**（新增 20 / 修改 15，含 A1 夹具同步面 5）；② = **5**（2 / 3）；③ = **18**（0 / 18，含 T2146/T2196
  普查后逐处委派的同族副本）；④ = **2**（1 / 1）；⑤ = **4**（1 / 3）；⑥ = **5**（5 / 0）——合计 **69**
  （新增 29 / 修改 40）。**穷举口径（权威）= git 派生实测改动集**：
  `git diff --name-status 6579766 -- . ':(exclude)specs'` ∪ `git ls-files --others --exclude-standard`
  （排除 `specs/**` 与 B 阶段接入侧件）——权威集合实测 **72** 条，其中 **69** 条入账、**3** 条按
  `NOT_LEDGER_ITEMS` 的规则剔除并逐条给出理由（注释/文档面，不服务 FR-013 任一项）。
  归属与去重纪律：
  - **计数只按 FR-013 的六项归属**（①~⑥），**不按实现阶段号**（A1~A5）——`items` 里**不带阶段号语义**，
    阶段号只是实现顺序；A4 的路径按"服务哪一项总账"归入（`core/calibration/config.py` → ①；
    `agents/pilot/pilot.py` 与 `tests/unit/test_form_clause_completeness.py` → ④），A5 的清单/CLI/演示 → ⑥；
  - **`core/evaluators/plugins/**`（含其 `__init__.py`）不属机制侧**：该目录是 **B 阶段（接入侧）** 的新增面
    （清单里恒为 `category = "plugin"` ⇒ 放行）——机制侧只交付"唯一装配点认声明"这一机制
    （`core/evaluators/plugin.py`）。把它塞进 ledger 就会与 C12 的 `plugin` 放行面**自相矛盾**（同一路径
    既算机制侧、又算接入侧），故**明确排除**；
  - `ops/form_guard.py` 同时服务 ② 与 ③（**只算一条路径**）；`tests/unit/test_form_switch.py` 同时服务 ② 与 ⑤（同上）；
  - cadence 越界的**新增用例**落 `tests/unit/test_form_clause_completeness.py`（**新文件**），
    `tests/unit/test_calibration_config.py` 的既有"缺项即红"参数化**不动**（`research.md` 决策 12 第 19 项）；
  - 上表单元格内另出现的 `plan.md` / `research.md` / `quickstart.md` 是**设计件的行文引用**、
    `tests/unit/test_calibration_config.py` 与 `tests/unit/test_visual_composite.py` 一类是
    **"明确不动"或"根本不存在"的引用**——**都不属于** `items`（逐条见下方 `NOT_LEDGER_ITEMS`）；
  - **ledger 覆盖代码与配置路径**；`specs/**` / `docs/**` 的机制侧文档变更**不重复登记**
    （它们在清单里恒属 `test_doc` 放行类），避免"文档塞满总账"。

**`NOT_LEDGER_ITEMS`（排除面；机检在做集合比较前**必须逐条剔除**，否则"文档表与常量集合相等"会被
行文引用污染——这正是"66/56 条"一类虚高计数的来源）**：

- **真·条目来源只有一处**：①~⑥ 表的 `items` 单元格（`A1 夹具同步面`的成员**就在 ① 的 `items` 里**，
  由 `MECHANISM_LEDGER_PATHS` 常量给出；**本契约不另列子表**——同一份清单两处副本必然漂移）。
  **除该处之外的任何路径都不算条目。**
- 下列路径**在正文里以反引号形式出现、但**不是**条目**（逐条实测判定，含"路径根本不存在"与"符号命中
  但**未改动**"两类情形——按指令**不得为凑数保留不存在的路径、也不得拿"符号调用点"充改动面**）：

| 正文里被反引号写出的路径 | 存在性（实测） | 处置（含真实路径） |
| --- | --- | --- |
| `tests/unit/test_visual_composite.py` | **不存在** | **不作为条目**——视觉面的**真实路径 = `tests/unit/test_visual_consistency.py`**（`plan.md` 阶段 A1 第 7 步给的是 `test_{…,visual}_composite.py` 的**形式**，实测 visual 无 `*_composite` 文件）；该真实路径在本特性里**也未改动** ⇒ 同样**不入账**（见剔除面） |
| `tests/contract/test_screenplay_contracts.py`（及 storyboard / editing / sound 同族；**正文里的写法是裸片段 `test_screenplay_contracts.py`**） | **不存在** | **不作为条目**——`tests/contract/` 下真正的命中**只有** `tests/contract/test_dev_contracts.py`，而它在本特性里**未改动** ⇒ 同样**不入账**（见剔除面） |
| `tests/contract/test_dev_contracts.py` / `tests/unbiasedness/test_*_unbiased.py` / `tests/unit/test_{dev,screenplay}_compare_adopt.py` / `tests/unit/test_sound_composite.py` / `tests/unit/test_visual_consistency.py` | 存在 | **符号命中但实测未改动**（它们从**真实形态配置**取声明段）⇒ **不作为条目**（旧口径"凡调用六个 `build_*_evaluators` 的文件都在账上"已按实测更正，见上"A1 夹具同步面"段） |
| `agents/pilot/stages.py` / `agents/pilot/run_report.py` / `docs/三期立项书.md` | 存在且**已改动** | **注释/文档面**（019 构造点普查事实 13 → 14 的更正，无语义变更、**不服务 FR-013 任一项**；且 `docs/**` 的机制侧文档变更按本文规则**不重复登记**）⇒ **不作为条目**（如实登记在 `NOT_LEDGER_ITEMS`） |
| `tests/unit/test_calibration_config.py` | 存在 | **明确不动的既有文件**（`research.md` 决策 12 第 19 项；cadence 越界新用例落在 `tests/unit/test_form_clause_completeness.py`）⇒ **不作为条目** |
| `plan.md` / `research.md` / `quickstart.md` / `data-model.md` / `spec.md` | 存在 | **设计件的行文引用**（§决策来源、§与另两份契约的分工、§文档面一致机检的比较对象等）⇒ **一律不作为条目**。**注意 `plan.md` 是"第 56 条"一类**旧版虚高计数**的元凶**：它在本文里以反引号出现（如 `plan.md` 阶段 A1 第 7 步），朴素抽取会把它算成一条 ledger 路径 ⇒ **必须剔除** |
| **片段形**：`__init__.py` / `test_dev_contracts.py` / `test_screenplay_contracts.py` / `test_{…,visual}_composite.py` 一类**不含目录前缀的裸文件名** | 见上 | 反引号写出的**片段**（正文引用时的简写）⇒ **也一律不作为条目**（否则片段会被当成独立路径，计数虚高 1~3 条）。判定规则：**token 形如"不含 `/` 的 `.py`"⇒ 一律剔除** |

- **机检口径（唯一）**：`MECHANISM_LEDGER_PATHS` = ①~⑥ 表 `items` 单元格的路径**并集**（`A1 夹具同步面`
  的成员就在 ① 的 `items` 里）**−** `NOT_LEDGER_ITEMS`；**集合相等即通过，不比较条数、不比较顺序**。
  当前实测：常量 **69 条**，与 `quickstart.md` 的"机制侧总账"表**逐路径相等**（**双向差集为空**）；
  权威**穷举口径 = git 派生实测改动集**（`git diff --name-status 6579766 -- . ':(exclude)specs'`
  ∪ `git ls-files --others --exclude-standard`，排除 `specs/**` 与 B 阶段接入侧件）实测 **72** 条，
  其中 **69** 条入账、**3** 条注释/文档面按 `NOT_LEDGER_ITEMS` 逐条给出理由剔除。
  **条数从来不是判据**：虚高是**抽取口径**的问题，不是两侧表集合不一致。

**分账规则（本契约的核心，不得含糊）**：

- **两个 ref 并排落产物头部**：`mechanism_ledger_ref`（机制落地后的提交）与 `baseline_ref`（新形态接入前的提交，
  **必须显式给出**）。**接入改动** = 以 `baseline_ref` 为基线的改动集合；**机制侧总账** = `baseline_ref` **之前**
  的那些改动（即 `MECHANISM_LEDGER` 的路径），**不计入** `violations`。
- **验收流程（三步，写进 quickstart 与变更说明）**：① 机制支线提交（打一个可引用的 ref）→ ② 以 ① 为基线做
  新形态接入（只新增配置 + 插件 + 测试文档）→ ③ 跑 `onboarding --baseline <①>` 必须 `violations == []`。

### 机检断言（C13，"不得冒充零代码改动"的六条）

1. **结构性反证（最强的一条）**：`MECHANISM_LEDGER` 中**至少一项** `kind == "modified"` 且路径前缀 ∈
   {`core/`、`agents/`、`ops/`}——本特性的机制改动**必然**修改既有模块逻辑（六个装配函数、`agents/pilot/pilot.py`、
   `core/calibration/config.py`、两份既有配置）。⇒ 以 **`MECHANISM_LEDGER` 之前的 ref** 为基线跑 `onboarding` 时，
   `violations` **必然非空**且**逐条命中** ledger 中的路径。**这条就是"机制侧不是零代码改动"的机器证明**：
   谁把机制侧改动写成"零代码改动"，这条反证立刻打红。
2. **基线双字段齐备**：清单头部 `baseline_ref` 与 `mechanism_ledger_ref` **都必须非空且两者不相等**；
   `baseline_ref == mechanism_ledger_ref` ⇒ 报错（基线取错；这会让机制侧改动被算成接入越界，判据自相矛盾）。
3. **冒充禁列（字段面）**：清单与演示产物必须带 `mechanism_changes_included: false`（为 `true` ⇒ 报错：
   机制改动被混进接入账）与 `zero_code_onboarding: true`——后者**只对基线之后的接入侧**成立。
4. **文档面一致（**集合相等**，**不写死条数**）**：`quickstart.md` 的"机制侧总账"表（其成员含 ① 组的
   A1 夹具同步面，**该表是唯一的清单副本**——本契约不复抄）里的**路径集合**与 `MECHANISM_LEDGER_PATHS` **相等**
   （机检读 quickstart 的表格，提取 `` `路径` `` token 后
   与常量做**集合相等**比较；**不**比较条数、**不**比较顺序）——防"文档说机制改动只是新增文件、
   而实际改了既有模块"，也防"文档表与常量表悄悄分叉"。条数只作运行期报表，
   **新增/删除路径 ⇒ 两侧同时改才算通过**（单侧改 ⇒ 红）。
5. **常驻清单纪律**：`len(MECHANISM_LEDGER) == 6`（与 FR-013 的六项一一对应；**不新造第 7 项**）。
   `MECHANISM_LEDGER_PATHS` **不设固定条数**（新增/删除路径即红，须显式登记到对应的总账项；
   纪律与 `tests/unit/test_billing_core_purity.py:268-285` 的 `OFFLINE_ASSEMBLIES` 常驻清单同款）。
6. **夹具同步面的可派生性（口径已按实测更正）**：`tests/**` 内调用六个 `build_*_evaluators` 的**符号命中面**
   （去掉已在 ⑤ 登记的 `tests/conftest.py`）**减去实际改动面**，其**差集必须逐条出现在剔除面
   `NOT_LEDGER_ITEMS`**——即：两处口径**都要有牙齿**（漏登记真实改动 ⇒ 集合相等判定红；
   拿符号调用点充改动面 ⇒ 与剔除面对不上 ⇒ 红）。**不得**再断言"符号命中面 == 改动面"（那是旧口径的错）。
   `tests/unit/fixtures/evaluator_assembly_baseline.json` 必须存在
   （A1 的"装配序列逐字相同"对照基线），缺失 ⇒ 红。
5. **常驻清单纪律**：`len(MECHANISM_LEDGER) == 6`（与 FR-013 的六项对应）且
   `MECHANISM_LEDGER_PATHS` **不设固定条数**（新增/删除路径即红，须显式登记到对应总账项
   ——纪律与 `tests/unit/test_billing_core_purity.py:268-285` 的 `OFFLINE_ASSEMBLIES` 常驻清单同款）。

### 反例（C13）

1. 把机制侧改动（六个装配函数改委派、`agents/pilot/pilot.py`、`core/calibration/config.py`、两份既有配置加段）
   说成"零代码改动"或"只是新增配置项"⇒ 断言 1 的结构性反证打红（`kind == modified` 项存在）。
2. `--baseline` 取 `main` / 仓库初始提交 ⇒ 机制侧改动全被算作越界 ⇒ 红（判据自相矛盾）。
3. 清单头部只落 `baseline_ref`、省掉 `mechanism_ledger_ref` ⇒ 评审无法复核"基线取对了没有" ⇒ 红。
4. `quickstart.md` 的机制侧总账表只列**新增**文件、隐去被修改的既有模块 ⇒ 文档面一致机检红。
5. 把 `mechanism_changes_included` 写成 `true`（或省掉该字段）以"完整起见"⇒ 红（机制改动与接入改动混同）。
6. 为让本轮"接入账干净"而把机制侧改动的路径从 `MECHANISM_LEDGER` 里删掉 ⇒ 红（常驻清单纪律 + 反证失效）。
7. 只改文档表（quickstart 的总账表）而不改常量、或只改常量而不改文档表 ⇒ **两表集合不等** ⇒ 红
   （断言 4 是**集合相等**；条数相同但路径不同也算红）。
8. 把 `core/evaluators/plugins/**`（B 阶段的接入侧新增面）登记进机制侧 ledger ⇒ 同一路径既算机制侧、
   又按 C12 判 `plugin` 放行 ⇒ 自相矛盾 ⇒ 红。
9. 漏登记 A1 夹具同步面的某个**真实改动**文件（或反过来：把"符号命中但未改动"的文件当成条目充数）
   ⇒ 断言 6 的差集与剔除面对不上 ⇒ 红。
10. 省掉 `tests/unit/fixtures/evaluator_assembly_baseline.json`（装配序列对照基线）却声称"逐字相同"⇒ 红
    （没有基线就没有对照）。

### 兼容规则（C13）

- **计数口径**：总账**只按 FR-013 的六项归属**（①~⑥）计数与陈述，**不按实现阶段号**（A1~A5）；
  阶段号只表示**实现顺序**，不承载账目语义（否则同一件改动会在多个"阶段项"里被重复计数）。
- 机制侧总账的**逐项**改动都遵守 `contracts/form-registration.md` C9 / C10 / C11 的"**只增不减**"口径
  （委派 / 扩展 / 改口径三种，**零删除、零放宽**）。
- 机制侧的**逐字节不变**约束由 `contracts/plugin-config.md` C2 的"声明 `version` == 实现 `spec.version`"
  与"既有两形态装配序列改造前后逐字相同"承载；本契约**不重复定义**，只引用。
- **既有评估器实现文件零改动**（`agents/*/evaluators/*.py` 的既有实现）：评估器版本号把**调用方实现文件字节**
  并入哈希（`agents/sound/evaluators/_versioning.py:22-23`、`agents/dev/evaluators/_versioning.py:13` 同款），
  改文件即改版本 ⇒ 改 `eval_breakdown` ⇒ 违反 FR-013。本契约的清单面因此**必然**出现
  `agents/<agent>/evaluators/plugins.py`（**new**，放行）而**不出现**任何既有实现文件的 `M`（除机制侧总账那几项）。
- 本契约**不引入**新依赖、不新造门禁、不改既有产物路径。

---

## C14 CLI 与离线端到端演示

**目的**：给出本特性全部**可复制执行**的入口与**退出码语义**（0 / 1 / 2，与既有工具一致），并固定**唯一**一个
**离线端到端演示**（零真实花费、零外部网络、零凭证）作为机制层的常驻证据（FR-010 / FR-011 / FR-014；SC-008）。

**形状·接口（CLI = `ops/form_plugin.py`，新增；薄转发，判定全在 `ops/form_guard.py` / `ops/form_onboarding.py`）**：

```bash
uv run python ops/form_plugin.py guard        [--configs-dir configs] [--roots core agents]
uv run python ops/form_plugin.py registration --config configs/<new-form>.yaml
uv run python ops/form_plugin.py onboarding   --baseline <ref> --config configs/<new-form>.yaml --out <临时目录>
uv run python ops/form_plugin.py sync-versions --check|--write --config configs/<new-form>.yaml
uv run python ops/demo_form_plugin.py         [--form <形态 id>] [--out <临时目录>] [--baseline <ref>] [--keep-work-dir]
```

- **`--out` 必须指向临时目录（I-07）**：所有写盘入口（`onboarding` 与演示）的 `--out` **必须**落在
  `tmp_path` 类**临时目录**（CLI 默认即 `tempfile.TemporaryDirectory`）；**禁止**写进仓库内路径
  （含 `.specify/**`——`.gitignore` **没有**该规则，实测会立刻让"仓库根零新增文件"这条断言失真）。
  判据 = **跑完后 `git ls-files --others --exclude-standard` 的输出集合前后逐条相等**。
- **演示脚本的归属与"形态无关"（I-09，必须写清）**：`ops/demo_form_plugin.py` **属机制侧资产**，
  **由阶段 A5 创建**（与 `ops/form_onboarding.py` / `ops/form_plugin.py` 同批），**不是 B1 的产物**；
  并且它**必须形态无关**——`--form` 只是"选哪一个**已声明**形态来演示"，脚本内部**遍历
  `declared_forms()`**（新增一份 `configs/<new-form>.yaml` 后，**对该形态零改动**即可演示；脚本内
  **零形态字面量、零形态分支**，与 C5/C6 的扫描面口径一致）。⇒ 它位于 `baseline_ref` **之前**，
  **不进**新形态接入的改动清单（见 C12 的 I-09 条）；B1/B2 **只新增配置与插件**（外加测试与文档）。

- **退出码语义**（沿用既有工具口径；常量符号 `EXIT_OK` / `EXIT_FAILED` / `EXIT_USAGE` 见 `ops/transfer.py`，
  用法面风格见 `ops/billing.py` 与 `ops/pilot.py`）：
  - **`0` 通过**——`guard` 两层扫描零违规；`registration` 五处逐一已登记 + 登记完备三条成立 + 无第六处；
    `onboarding` `violations == []`；`sync-versions --check` 无差集；演示全步 ok；
  - **`1` 判定失败或越界**——有越界路径 / 五处缺项 / 登记完备三条任一不成立 / 新增第六处 /
    `sync-versions --check` 有差集 / 演示任一步失败；**逐条点名**（不得只报总数）；
  - **`2` 用法或配置错误**——缺 `--baseline`、基线 ref 不可解析、配置不可读、`declared_forms()` 派生失败。
- **`sync-versions`**：`--check`（**默认**）只报差集**不回写**；`--write` 才回写，且**只改**
  `evaluators.plugins` 下的 `version` 一个叶子键（定点改写，风格与 019 的扩量改写同源）。
  **门禁只跑 `--check`**——**不得**以改写权威配置换取绿灯。**判据永远是装配期一致性校验**
  （"声明的 `version` == 实现产出的 `spec.version`"，`contracts/plugin-config.md` C2），**不是** sync 的产物。
- **薄转发纪律**：CLI 只解析参数与打印 JSON，**不写权威数据面**、**不触碰工作区**、**不** `git add`/`commit`。

**离线端到端演示 `ops/demo_form_plugin.py`（唯一入口，镜像 `ops/demo_shortdrama_feedback.py` 的风格；九步，退出码 0 = 全步 ok）**：

| 步 | 内容 | 对应契约 |
| --- | --- | --- |
| ① | **新形态配置加载与预检**：以 `configs/<new-form>.yaml` 的派生副本（账本根落临时目录，先例 `tests/conftest.py:3054-3079`）跑 `config_completeness` ⇒ C11 的 020 口径七项 + `evaluators` 段清单解析器全部通过 | C11 / C9④ |
| ② | **缺项即拒绝**：逐项删掉一个 020 口径键、再删掉 `evaluators` 段 ⇒ 预检**拒绝启动**并点名段与键（**不取码内默认**） | C11 / C9④ |
| ③ | **插件装配**：按**配置声明**实例化并注册——`impl`（`module:attr`）由**唯一装配点**解析；`spec.evaluator_id` == 声明键；`spec.version` == 声明值；装配集合 ↔ `evaluator_weights.<agent>` 键集**逐字相等**且**保序**；同 id 同 version **重复注册被拒**（`core/evaluators/registry.py:35-38`）；非确定性插件被拒（`:29-33`）；缺 `cost_per_call` 等必需元数据被拒（`core/evaluators/base.py:42`）；插件目录里存在但**未声明** ⇒ 装配期不可用 | C1~C4 |
| ④ | **评估与合成分数**：对合成功件跑一次评估 ⇒ `eval_breakdown` 的键为 `evaluator_id@version` ⇒ `composite_score_versioned`（`core/evaluators/composite.py:37`）出分 | C2 / C3 |
| ⑤ | **留痕**：落一个带 `eval_breakdown` 的节点与一份运行记录到**临时目录**（仓库根零残留） | C2 |
| ⑥ | **两形态共用同一份插件代码**：至少一条 `impl` 与另一形态（或既有两形态之一）**逐字相同** ⇒ 举证"形态差异**只在配置值**"；两形态各自的得分 / 明细差异**全部来自配置值** | C1 / C3 |
| ⑦ | **静态守卫**：跑两层扫描（字面量 + 判断分支，覆盖 `core/` + `agents/` **含 `agents/pilot`**）⇒ 零违规；并用**由派生值构造**的合成反例举证"**注入即红**"（有牙齿，不空跑） | C5~C8 |
| ⑧ | **登记点完备**：五处逐一判定 + 登记完备三条（两两唯一 / 双向集合相等 / 下界 ≥ 2）+ 无第六处 | C9 / C10 |
| ⑨ | **诚实分层与零成本**：三层"未标定"标注机检通过（`pilot.rehearsal.status: unstandardized` + 五段段级 `note` 含「未标定」+ 产物 `uncalibrated: true` 与 `uncalibrated_reason` 非空）；真实花费 = **0**、外部网络 = **0**、凭证读取 = **0** | C11 / 本 C14 |

**零真实花费 / 零外部网络 / 零凭证是结构性事实，不是承诺**：

- **零凭证**：最小可行形态**不声明 judge** ⇒ 演示**不构造** `LLMGateway` ⇒ 无凭证面；机检
  `ops/demo_form_plugin.py` **不含** `os.environ` / `os.getenv` / `environ` 读取（文本 + AST 双层）。
- **零外部网络**：演示**不 import** 任何 HTTP 客户端（文本 + AST 双层机检其 import 面：零 `http.client` /
  `urllib.request` / `requests` / `httpx`）；产物固定字段 `network: "none"`
  （沿用 `ops/demo_shortdrama_feedback.py:893` 的输出字段口径）。
- **零真实花费**：演示内计数断言（网关调用 **0** 次 / 账本总额 **0** / `CostRecord` **零新增**）+
  跑完**仓库根零新增文件**。**推荐**路径是不构造网关 ⇒ `tests/unit/test_billing_core_purity.py`
  的构造点普查面（`:355-359`）**不动**（仍为 14）；**若**确需构造网关，则该演示点**必须**显式登记进
  `OFFLINE_ASSEMBLIES`（`tests/unit/test_billing_core_purity.py:268-285`）并同步计数
  （**按扩展更新**；**不得**改成"只数真实渠道"来绕过——清单 + 计数常驻是 019/020 的既有纪律，变严不变松）。
- **零真实投放 / 零真实素材生成**：B/C 路径不变（`docs/三期立项书.md:212` 的 `not_delivered` 口径不放宽）；
  演示不装配投放适配器、不调用任何生成厂商、不做多租户 / 公网服务化。

**产物（逐条，落 `--out`——**临时目录**——append-only、可机检）**：

| 产物 | 路径 | 看什么 |
| --- | --- | --- |
| 接入改动清单 | `<out>/onboarding-<form>-<seq:04d>.json` | `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]`（逐条路径 + `status` + 类别 + 越界标记）/ `counts` / `violations[]` / `mechanism_changes_included` / `zero_code_onboarding` / `exit_code` |
| 清单索引 | `<out>/index.jsonl` | 每行一次接入的固定字段（append-only 追加，**永不回改**） |
| 守卫报告 | `<out>/guard-report.json` | 逐条 `(相对路径, 所属符号名, 行号, 命中内容)`（**符号名锚点**）；两层扫描分别计数；例外三条的判定结果 |
| 登记报告 | `<out>/registration-report.json` | 五处逐一 `{site, symbol, kind, forms, delegated, missing[]}` + 登记完备三条结论 + 第六处反向扫描结论 |
| 演示报告 | `<out>/demo-report.json` | `steps`（九步逐条 `ok`）/ `ok` / `network: "none"` / `credentials_required: false` / `uncalibrated: true` / `uncalibrated_reason` / `elapsed_seconds` |
| 形态配置指纹 | 清单内 `config_fingerprint` | BLAKE3 前 12 位——"哪一次接入用的是哪一份配置"可指认 |

### 机检断言（C14）

- 各 CLI 入口 `--help` 退出码 **0**；缺 `--baseline` ⇒ **2**；基线 ref 不可解析 ⇒ **2**；`onboarding` 有越界 ⇒ **1**
  且**逐条点名**；`guard` 零违规 ⇒ **0**；`sync-versions --check` 有差集 ⇒ **1**。
- `ops/demo_form_plugin.py` **退出码 0** 且九步全 `ok`；跑完**仓库根零新增文件**（前后未跟踪文件集合相等）；
  产物里 `network == "none"`、`credentials_required is False`、`uncalibrated is True`、
  `uncalibrated_reason` 去空白后非空。
- 演示内"真实花费 0 / 外部网络 0 / 凭证读取 0"三项逐条断言成立（任一不成立 ⇒ 演示退出码 **1**）。
- **诚实分层结论文案取值域固定**：未标定期间**不得**输出"该形态已标定 / 已达标 / 已投产"一类结论
  （出现即红）；"以模拟冒充标定"的次数**恒 0**。
- **`--out` 落临时目录（I-07）**：`onboarding` 与演示跑完后 `git ls-files --others --exclude-standard`
  的输出集合**前后逐条相等**；`--out` 写成仓库内路径（含 `.specify/**`）⇒ 红。
- **演示形态无关（I-09）**：`ops/demo_form_plugin.py` 内**零形态字面量、零形态分支**（C5/C6 的扫描面口径）；
  它按 `declared_forms()` **遍历已声明形态** ⇒ 新增一份 `configs/<new-form>.yaml` 后**对该形态零改动**
  即可演示（`--form` 只用于挑选演示对象，缺省时对全部已声明形态各跑一遍）。
- 演示入口**唯一**（不新造第二个端到端演示脚本）；命名与既有 `ops/demo_*.py` 一致。
- 命令行示例里 `<...>` 包裹的取值**一律是示例参数**，须替换为实际取值；它们**不是**配置取值，
  **不得**出现在 `configs/*.yaml` 里（配置里出现 `<`/`>` 占位即视为非法取值——缺项/非法即报错、不取码内默认）。

### 反例（C14）

1. 演示用真实凭证 / 真实平台跑通 ⇒ 红（零凭证红线；且最小形态不声明 judge ⇒ 无网关可构造）。
2. 演示把"机制已就绪"写成"该形态已投产 / 已标定" ⇒ 红（诚实分层；"以模拟冒充标定"次数恒 0）。
3. 演示把节点 / 账本 / 运行记录落进仓库根 ⇒ 红（跑完仓库根必须零残留）。
4. 缺 `--baseline` 时默认取 `HEAD`（静默兜底）⇒ 红（基线必须**显式**给出；取错即判据自相矛盾）。
5. 把 `sync-versions --write` 用作门禁（改配置来通过）⇒ 红（门禁只跑 `--check`）。
6. 演示构造 `LLMGateway` 却不登记进 `OFFLINE_ASSEMBLIES` / 不更新构造点计数 ⇒ 红。
7. 演示用第二个脚本名（与既有 `ops/demo_*.py` 命名不一致）⇒ 命名判断项红（命令与文档须逐字一致）。
8. 把演示脚本挪回 B1 创建（而不是 A5）、或把它记进接入侧的 `changes[]` ⇒ 以机制 ref 为基线时
   `ops/**` 新增被判 `out_of_scope` ⇒ B1/B2 验收永远红（I-09）。
9. 演示脚本按形态分支（`if form == "ad": …`）或写死形态名 ⇒ 红（形态无关失守；且违反 C5/C6）。
10. `--out` 写进仓库内路径（如 `.specify/onboarding`）⇒ 未跟踪集合前后不等 ⇒ 红（I-07）。

### 兼容规则（C14）

- CLI 与演示**全部离线**；不改任何既有 CLI 的参数与退出码语义；`ops/` / `web/` / `dreaming/` 的既有配置路径
  默认值**不改**（它们不在扫描面内，也**不得**为过断言而改写——`ops/billing.py:97` 的 `DEFAULT_CONFIG` 即一例）。
- 演示的产物只落**临时目录**（默认演示结束即清理，`--keep-work-dir` 才保留），仓库根零残留
  （沿用 `ops/demo_shortdrama_feedback.py` 的演示纪律）；**`--out` 不接受仓库内路径**（I-07）。
- **演示脚本是机制侧资产**：由 **A5** 创建、与 `ops/form_onboarding.py` / `ops/form_plugin.py` 同批；
  **形态无关**（遍历 `declared_forms()`）；⇒ 它**不进**新形态接入的改动清单，B1/B2 只新增配置与插件（I-09）。
- **不引入**运行时依赖（stdlib + 既有 `blake3` / `pyyaml`）；**不新造**旁路门禁、**不绕过** LLM 网关口径
  （原则三不因本特性放宽：本特性**零真实渠道调用**，最小形态不声明 judge）。
