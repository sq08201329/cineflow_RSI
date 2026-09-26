# Quickstart：形态插件扩展性验证（021-form-plugin-validation）

本文给出**可复制执行**的验证序列与期望结果。**全部命令离线、零真实花费、零外部网络、零凭证**；
命令与仓库既有工具链一致（`uv run …`，形态配置用 `configs/<form>.yaml`）。
契约落点：`contracts/plugin-config.md`（C1~C4，声明形状与唯一装配点 / 版本冻结）、
`contracts/zero-form-branch.md`（守卫面四条各自成号：**C5** 字面量层扫描面与例外三条 /
**C6** 判断分支层与形态名派生 / **C7** 三副本委派收敛 / **C8** 裸词收敛与 E1 例外登记）、
`contracts/form-registration.md`（C9~C11，五处登记点 / 登记完备 / 020 口径完备）、
`contracts/onboarding-ops.md`（C12~C14，接入改动清单 / 机制侧总账 / CLI 与离线演示）。

**当前状态如实标注（T2185④：落地态）**：本特性**机制侧（A1~A5）与接入侧（B1~B3）均已落地**——
B 组命令所依赖的实现（`core/evaluators/plugin.py`、`core/evaluators/plugins/`、六个
`agents/<agent>/evaluators/plugins.py`、`ops/form_guard.py`、`ops/form_onboarding.py`、
`ops/form_plugin.py`、`ops/demo_form_plugin.py` 与相应的新增测试文件）**已全部落地** ⇒ **B 组命令可跑**
（实测退出码与结论见文末"验证记录"）。A 组命令仍可跑：其 A3~A6 四条（机制缺口 / 守卫失明 / 词边界 /
基线派生）是**改造前**的事实基线，**保留原样、不回改**——它们正是"机制侧不是零代码改动"的对照面。
文末"验证记录"**只回填已实跑的结果**，不预填未跑的结论。

**"仅新增配置 + 插件"的成立范围（如实分层，FR-013 末句）**：**机制侧 A1~A5 是本特性的代码改动主体、
不是零代码改动**（六项总账见下文"机制侧总账"节）——只有**此后**的新形态接入（B1/B2）才是
"仅新增配置 + 插件"。A 组里第 5~7 条命令记录的是**改造前"机制不存在"**这一事实。

---

## A. 现在即可跑（既有面）

```bash
uv sync                                                     # 依赖与 uv.lock 一致（无新增依赖）
uv run ruff check .                                         # 静态检查
uv run ruff format --check .                                # 格式检查

# A1 既有守卫子集（本特性将"委派 + 扩展 + 改口径"，断言只增不减）
uv run pytest tests/unit/test_form_switch.py -q              # 两形态差异 + 零形态分支守卫（补面前）
uv run pytest tests/unit/test_config_integrity.py -q         # 015/018/019/020 的加载器与"缺项即红"
uv run pytest "tests/contract/test_pilot_contracts.py::TestC10到C13试水运行::test_c13_两套配置差异可归因且无形态分支" -q

# A2 "恰好两份"与"五处登记点"的今天形状
ls configs/*.yaml                                            # 恰好两份：movie.yaml / shortdrama.yaml
grep -n "PILOT_FORMS" tests/conftest.py                      # 写死两形态（登记点 ⑤）
grep -n "^FORMS" tests/unit/test_form_switch.py tests/unit/test_billing_core_purity.py \
      tests/unit/test_billing_channels.py tests/contract/test_billing_contracts.py \
      tests/unit/test_pilot_rehearsal.py                     # 同族"两形态枚举"副本（逐处待改派生）

# A3 机制缺口的取证：新形态今天无法以"配置 + 插件"接入
grep -rnE "plugin|entry_point|import_module" core agents --include=*.py | grep -v __pycache__   # 0 命中
ls core/evaluators/                                          # 无 plugins/ 目录（声明面与插件落点均未建立）

# A4 守卫失明的取证：新形态名"天然逃逸"（人工常量清单里没有它）
grep -rnE "(^|[^0-9A-Za-z_])movie([^0-9A-Za-z_]|$)" core agents --include=*.py | grep -v __pycache__
grep -rn "animated" core agents --include=*.py | grep -v __pycache__        # 0 命中（新形态名不被扫到）
grep -rn "漫剧" core agents --include=*.py | grep -v __pycache__            # 0 命中

# A5 词边界判定 vs 子串判定的必要性（research 决策 6 的量化依据）
grep -rhoE "ad" core agents --include=*.py | wc -l                          # 子串判 `ad`：数千处
grep -rhoE "(^|[^0-9A-Za-z_])ad([^0-9A-Za-z_]|$)" core agents --include=*.py | wc -l   # 词边界：0 处

# A6 基线派生的原语（C12 的"由 git 派生"）
git diff --name-status HEAD                                  # 已跟踪文件的改动（今天为空）
git ls-files --others --exclude-standard                     # 未跟踪的新增文件——**必须并入清单**
```

**A 组期望结果（已实跑，见文末"验证记录"）**：
`test_form_switch.py` 全绿（**17 passed**）；`test_config_integrity.py` 全绿（**125 passed**）；
`test_c13…` 单用例通过（**1 passed**）；`ruff check .` → `All checks passed!`；
`ruff format --check .` → `585 files already formatted`。
A3 的 `grep` **0 命中**（机制不存在）；A4 的 `movie` 命中**恰好 2 处**
（`core/deployment/evidence.py:97` 的配置路径字面量、`agents/pilot/pilot.py:613` 的裸形态词），
`animated` / `漫剧` 各 **0 命中**（守卫对新形态失明）；A5 的 `ad` **子串数千处 vs 词边界 0 处**；
A6 两支命令合计给出的路径集合**只有未跟踪的 `specs/021-form-plugin-validation/**` 设计件**
（这正是 C12 要求"必须含未跟踪新增文件"的原因——只跑 `git diff` 会把"新增"整类漏掉）。

---

## B. 本特性落地后可跑（B1~B5；实现已落地）

### B1 守卫与派生面（C5 / C6 / C7）

```bash
uv run pytest tests/unit/test_form_guard.py -q                        # 派生面 / 两层扫描 / 符号名锚点 / 例外三条 / 注入即红
uv run python ops/form_plugin.py guard                                # 退出码 0：零违规 + 逐条 (路径, 符号名, 行号, 命中内容)
uv run pytest tests/unit/test_billing_core_purity.py tests/unit/test_dev_core_degraded_purity.py -q   # 三副本委派后，断言体原位保留
```

**期望结果**：`guard` 退出码 **0**，输出零违规；派生面由 `configs/*.yaml` 的 `form:` 取值与 `form_aliases`
（别名键定名见 `contracts/zero-form-branch.md` C6）给出——**新增一份形态配置即自动进入禁令面**；
把**由派生值构造**的形态字面量注入 `agents/pilot/backends.py` ⇒ 该断言**变红**（注入即红率 100%）。

### B2 新形态的配置加载与装配（C1~C4 / C9~C11）

```bash
# 登记点完备性（五处逐一 + 登记完备三条 + 不新造第六处）
uv run python ops/form_plugin.py registration --config configs/<new-form>.yaml

# 020 口径完备性与 cadence 收口（缺项即拒绝启动、不取码内默认）
uv run pytest tests/unit/test_form_clause_completeness.py -q
uv run pytest tests/unit/test_form_registration.py -q
uv run pytest tests/unit/test_evaluator_plugin_assembly.py tests/contract/test_plugin_contracts.py -q

# 版本声明与实现的一致性（**只校验、不回写**；门禁只跑 --check）
uv run python ops/form_plugin.py sync-versions --check --config configs/<new-form>.yaml
```

**期望结果**：`registration` 退出码 **0**，五处逐一"已登记 + 已委派"，登记完备三条（`form:` 取值两两唯一 ∧
`declared_forms()` 与各登记点的形态集合**双向相等** ∧ 配置数 ≥ 2）成立，且**无第六处**；
`sync-versions --check` 退出码 **0**（无差集）——若声明的 `version` 与实现产出的 `spec.version` 不等 ⇒
退出码 **1** 并列出差集（**不得**用 `--write` 改配置来通过）。
缺任一 020 口径键 ⇒ `config_completeness` **拒绝启动**并点名段与键（退出码非 0）。

### B3 接入改动清单（C12 / C13）

```bash
# 基线 = 机制落地后、新形态接入前的提交（**必须显式给出**）
# `--out` **必须落临时目录**（I-07：`.gitignore` 无 `.specify` 规则 ⇒ 写进仓库内路径必然
# 让"仓库根零新增文件/未跟踪集合前后相等"这条断言失真）；下面用 `mktemp -d` 示意
OUT=$(mktemp -d)
uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> \
        --config configs/<new-form>.yaml --out "$OUT"
```

**期望结果**：退出码 **0**（`violations == []`），清单逐条给出路径 + `status` + **类别**（`category`
的**英文枚举**：`config` / `plugin` / `test_doc` / `out_of_scope`——字段与取值域以
`contracts/onboarding-ops.md` C12 的权威表为准），`counts["既有模块被修改"] == 0`；产物含 `baseline_ref` /
`mechanism_ledger_ref` / `config_fingerprint`（BLAKE3 前 12 位）；`index.jsonl` **追加**一行。
**越界取证**：人为在 `core/evaluators/composite.py` 里加一行注释 ⇒ 退出码 **1** 且**逐条点名**该路径。
**零残留判据**：跑完后 `git ls-files --others --exclude-standard` 的输出集合**前后逐条相等**
（`--out` 落仓库内路径 ⇒ 该判据必红）。

### B4 离线端到端演示（C14）

```bash
# 演示脚本是**机制侧资产**（A5 创建）且**形态无关**：遍历 declared_forms()，对新形态零改动即可演示
OUT=$(mktemp -d)
uv run python ops/demo_form_plugin.py --form ad --out "$OUT"   # 九步；退出码 0 = 全步 ok
```

**期望结果**：退出码 **0**，`steps` 九步逐条 `ok`；产物 `network: "none"`、
`credentials_required: false`、`uncalibrated: true` 且 `uncalibrated_reason` 非空；
**仓库根零新增文件**（产物落 `--out` 临时目录；演示的临时工作目录默认结束即清理）。
九步逐条对应见 `contracts/onboarding-ops.md` C14 的表（配置加载与预检 → 缺项即拒绝 → 插件装配 →
评估与合成分数 → 留痕 → 两形态共用同一份插件代码 → 静态守卫 → 登记点完备 → 诚实分层与零成本）。
**形态无关的判据**：`ops/demo_form_plugin.py` 内**零形态字面量、零形态分支**（C5 / C6 的扫描面口径）；
新增一份 `configs/<new-form>.yaml` 后，**对该形态零改动**即可演示（`--form` 只用于挑选演示对象）。

### B5 接入方流程（写配置 → 写插件 → 声明 → 校验 → 审计）

```bash
# 1) 新增 configs/<new-form>.yaml：段集合与既有两形态一致 + 020 口径逐项声明 + "未标定"标注 + evaluators 声明
# 2) 写业务无关的评估器插件：通用件落 core/evaluators/plugins/，Agent 绑定件落 agents/<agent>/evaluators/
#    （**两类都必须经 impl 声明才生效**——目录不决定可用性）
uv run python ops/form_plugin.py sync-versions --check --config configs/<new-form>.yaml   # 3) 版本一致性（只校验）
uv run python ops/form_plugin.py registration --config configs/<new-form>.yaml            # 4) 五处登记点逐一
OUT=$(mktemp -d)                                                                          # 5) 清单产物落临时目录
uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> \
        --config configs/<new-form>.yaml --out "$OUT"                                      # 接入改动清单（越界即非 0）
OUT2=$(mktemp -d)
uv run python ops/demo_form_plugin.py --form <形态 id> --out "$OUT2" --baseline <机制落地 ref>   # 6) 离线端到端
```

**形态名约定**：形态名**一律由 `configs/*.yaml` 的 `form:` 派生**；中文别名由 `form_aliases` 键声明
（**键名与派生口径以 `contracts/zero-form-branch.md` C6 为权威**，本文不另定一套写法）。
本文在示例命令里只使用形态 id **`ad`** 与 **`animated`**。

**命令行示例约定**：`<>` 包裹的取值（`<机制落地 ref>` / `<形态 id>` / `<new-form>`）一律是
**示例参数**，须替换为实际取值；它们**不是**配置取值，配置里（`configs/*.yaml`）**不得**出现 `<>` 占位
（配置写数值/字符串，缺项/非法即报错、不取码内默认）。

---

## 如何判定"新形态接入真的零代码改动"（核对步骤，逐条可执行）

1. **取对基线**（第一步，错了后面全部自相矛盾）：基线 = **机制落地后、新形态接入前**的提交，
   由 `--baseline <ref>` **显式**给出（**不得**默认取 `HEAD`、**不得**取 `main` 或仓库初始提交）；
   产物里 `baseline_ref` 与 `mechanism_ledger_ref` 两个字段**必须非空且不相等**（C13 机检 2）。
2. **由 git 派生而不是人写**：清单必须同时覆盖 `git diff --name-status <ref>` 与
   `git ls-files --others --exclude-standard`（未跟踪的新增文件）——只跑前者会把"新增"整类漏掉；
   在临时工作区造一个未跟踪新文件 ⇒ 清单**必须**含它（C12 机检 1）。
3. **逐条看类别，不看路径前缀**：对清单每一条同时看 `status` 与类别——
   新增 `configs/*.yaml` / 新增 `core/evaluators/plugins/**` 或 `agents/<agent>/evaluators/**` /
   新增或修改 `tests/**`、`docs/**`、`specs/**` ⇒ **放行**；
   **任何既有文件的修改或删除**落在 `core/` / `agents/` / `ops/` / `web/` / `dreaming/` / `policies/`
   ⇒ **越界**（退出码非 0 + 逐条点名）。注意同一前缀下的相反结论：
   新增 `agents/<agent>/evaluators/plugins.py` 放行、修改 `agents/<agent>/evaluators/__init__.py` 越界。
4. **核对"既有模块被修改"恒为 0**：`counts["既有模块被修改"] == 0`（SC-001①）；
   **不是**看"改动文件总数"——新增配置 + 插件 + 测试文档本来就会有若干条。
5. **核对越界判定的牙齿**：人为制造一份**故意越界**的改动（例如在 `core/evaluators/composite.py` 里加一行注释）
   ⇒ 退出码 **1**、清单**逐条点名**该路径与类别；"只报总数不报路径"的次数恒 0（SC-002）。
6. **核对五处登记点未被绕过**：`registration` 逐一判定五处"已登记 + **已委派**"——
   任一处的形态集合退回**字面量元组**（而非调用 `declared_forms()`）⇒ 该处对新形态**缺席** ⇒ 必红（C9 / C10）。
7. **核对插件确实"经声明生效"**：把插件文件放进目录但**不**在配置里声明 ⇒ 装配期**不可用**且报错；
   反过来，只改配置的 `impl` 指向另一个实现 ⇒ 装配结果随之改变 ⇒ **差异只在配置值**（C1 / C2）。
8. **核对两形态共用同一份插件代码**：至少一条 `impl` 与另一形态**逐字相同**，且两形态的得分/明细差异
   **全部来自配置值**（演示步 ⑥）。
9. **核对诚实边界**：结论只能是"**机制已就绪 / 业务定义未标定**"——**不得**写成"该形态已标定 / 已投产"
   （出现即红，C14）；"以模拟冒充标定"的次数恒 0。

---

## 机制侧总账（本特性的**代码改动主体**，逐条登记）

**口径（FR-013）**：本特性自身对 `core/` / `agents/` / `ops/` 与**既有配置**的机制改动**必须**与
"此后的新形态接入改动"**分开陈述**——**禁止**把机制改动本身写成"零代码改动"。下表与
`ops/form_onboarding.py` 的 `MECHANISM_LEDGER` **集合相等**（C13 机检 4：**路径集合**相等即通过，
**不写死条数**；当前实测 **69 条**，仅作对照）；`kind` 列里的 **new** = 新增文件、
**modified** = **既有模块被修改**（机制侧**必然**含 `modified` 项——这正是"机制侧不是零代码改动"的机器证明）。
**计数只按 FR-013 的六项归属**（①~⑥，见"服务哪一项总账"列），**不按实现阶段号 A1~A5**
（阶段号只表示实现顺序，不承载账目语义）。**按六项归属的当前实测分解（仅作对照，非判据；
重复路径只在其首次归属项计数）**：① = **35**（新增 20 / 修改 15；含测试侧夹具与存根、六份
`agents/*/config.py` 的声明面承载、A1 夹具同步面 5 条）／② = **5**（2 / 3）／③ = **18**（0 / 18；
含 T2146/T2196 普查后逐处委派的同族副本）／④ = **2**（1 / 1）／⑤ = **4**（1 / 3）／⑥ = **5**（5 / 0），
合计 **69**（新增 29 / 修改 40）。

**穷举口径（权威）= git 派生实测改动集**：`git diff --name-status 6579766 -- . ':(exclude)specs'`
**∪** `git ls-files --others --exclude-standard`（排除 `specs/**` 与 B 阶段接入侧件）——权威集合实测
**72** 条，其中 **69** 条入账、**3** 条按下列规则剔除（理由逐条写清，**不是**缺口）：
`agents/pilot/stages.py` 与 `agents/pilot/run_report.py`（**注释面**：019 构造点普查事实 13 → 14 的
更正，无语义变更、不服务 FR-013 任一项）、`docs/三期立项书.md`（**文档面**：C13 明文 `docs/**` 的
机制侧文档变更**不重复登记**，在清单里恒属 `test_doc` 放行类）。

| # | 路径 | kind | 服务哪一项总账（FR-013 的序号，①~⑥） |
| --- | --- | --- | --- |
| 1 | `agents/dev/config.py` | new | ① 声明面承载（`plugin_declarations` 逐字拷贝；缺段不补默认） |
| 2 | `agents/dev/evaluators/__init__.py` | **modified** | ① `build_dev_evaluators` 改委派 |
| 3 | `agents/dev/evaluators/plugins.py` | new | ① 同上 |
| 4 | `agents/editing/config.py` | new | ① 同上 |
| 5 | `agents/editing/evaluators/__init__.py` | **modified** | ① `build_editing_evaluators` 改委派 |
| 6 | `agents/editing/evaluators/plugins.py` | new | ① 同上 |
| 7 | `agents/screenplay/config.py` | new | ① 同上 |
| 8 | `agents/screenplay/evaluators/__init__.py` | **modified** | ① `build_screenplay_evaluators` 改委派 |
| 9 | `agents/screenplay/evaluators/plugins.py` | new | ① 同上 |
| 10 | `agents/sound/config.py` | new | ① 同上 |
| 11 | `agents/sound/evaluators/__init__.py` | **modified** | ① `build_sound_evaluators` 改委派（扁平列表、保序） |
| 12 | `agents/sound/evaluators/plugins.py` | new | ① 同上 |
| 13 | `agents/storyboard/config.py` | new | ① 同上 |
| 14 | `agents/storyboard/evaluators/__init__.py` | **modified** | ① `build_storyboard_evaluators` 改委派 |
| 15 | `agents/storyboard/evaluators/plugins.py` | new | ① 同上 |
| 16 | `agents/visual/config.py` | new | ① 同上 |
| 17 | `agents/visual/evaluators/plugins.py` | new | ① Agent 绑定薄工厂 |
| 18 | `agents/visual/loop.py` | **modified** | ① `build_evaluators` 改委派（签名与返回形状不变） |
| 19 | `configs/movie.yaml` | **modified** | ① 新增 `evaluators` 声明段（既有取值零改动） |
| 20 | `configs/shortdrama.yaml` | **modified** | ① 同上（与 movie 的该段逐字相同） |
| 21 | `core/calibration/config.py` | **modified** | ① cadence 取值域收口（取值域取自 `core/calibration/periods.py:30`，该文件一字不改） |
| 22 | `core/evaluators/errors.py` | **modified** | ① 错误类型（`EvaluatorError` 之下增声明/装配期错误） |
| 23 | `core/evaluators/plugin.py` | new | ① 唯一装配点（声明解析 + `importlib` + 参数注入 + 一致性校验） |
| 24 | `tests/contract/test_plugin_contracts.py` | new | ① 插件契约测试（C1~C4 的可执行面） |
| 25 | `tests/plugin_fixtures.py` | new | ① 测试侧声明面同步助手（内联配置字典夹具的 `evaluators` 段） |
| 26 | `tests/plugin_stubs.py` | new | ① 合成反例目标（装配期拒绝面的被测对象） |
| 27 | `tests/unit/fixtures/dependency_baseline.json` | new | ① 依赖基线快照（`pyproject.toml` 依赖集 + `uv.lock` 包名集，"机制落地前"导出） |
| 28 | `tests/unit/fixtures/evaluator_assembly_baseline.json` | new | ① 装配序列对照基线夹具（"改造前后逐字相同"的对照物） |
| 29 | `tests/unit/test_dev_composite.py` | **modified** | ① A1 夹具同步面（实测**确实改动**：内联字典补 `evaluators` 段） |
| 30 | `tests/unit/test_editing_composite.py` | **modified** | ① 同上 |
| 31 | `tests/unit/test_evaluator_plugin_assembly.py` | new | ① 装配面单测 |
| 32 | `tests/unit/test_form_no_new_dependency.py` | new | ① 零新增运行时依赖常驻用例（FR-012 的机检承载，A5/T2197） |
| 33 | `tests/unit/test_screenplay_cli.py` | **modified** | ① 同上 |
| 34 | `tests/unit/test_screenplay_composite.py` | **modified** | ① 同上 |
| 35 | `tests/unit/test_storyboard_composite.py` | **modified** | ① 同上 |
| 36 | `ops/form_guard.py` | new | ②③ 形态名派生 + 两层扫描（**单一实现**） |
| 37 | `tests/unit/test_billing_core_purity.py` | **modified** | ② 副本委派（断言体原位保留） |
| 38 | `tests/unit/test_dev_core_degraded_purity.py` | **modified** | ② 副本委派（同上） |
| 39 | `tests/unit/test_form_guard.py` | new | ② 守卫自检（注入即红 / 派生失败即报错） |
| 40 | `tests/unit/test_form_switch.py` | **modified** | ②⑤ 委派收敛 + 排除项删去 + "恰好两份"→登记完备 |
| 41 | `ops/demo_shortdrama_feedback.py` | **modified** | ③ 同族「两形态枚举」副本委派（断言体原位保留） |
| 42 | `ops/dev.py` | **modified** | ③ 同上 |
| 43 | `ops/screenplay.py` | **modified** | ③ 同上 |
| 44 | `tests/contract/test_billing_contracts.py` | **modified** | ③ 同上 |
| 45 | `tests/contract/test_llm_profile_contracts.py` | **modified** | ③ 同上（T2196 普查清单） |
| 46 | `tests/contract/test_pilot_contracts.py` | **modified** | ③ 禁用元组改派生（差异集断言一字不改） |
| 47 | `tests/contract/test_pilot_film_contracts.py` | **modified** | ③ 同上（T2196 普查清单；断言体与判据不削弱） |
| 48 | `tests/contract/test_transfer_contracts.py` | **modified** | ③ 同上（T2196 普查清单） |
| 49 | `tests/unit/test_billing_channels.py` | **modified** | ③ 同族枚举副本改派生 |
| 50 | `tests/unit/test_billing_config.py` | **modified** | ③ 同上 |
| 51 | `tests/unit/test_billing_gateway_cells.py` | **modified** | ③ 同上 |
| 52 | `tests/unit/test_billing_peak_windows.py` | **modified** | ③ 同上 |
| 53 | `tests/unit/test_calibration_transfer.py` | **modified** | ③ 同上 |
| 54 | `tests/unit/test_dev_policy_loader.py` | **modified** | ③ 同上 |
| 55 | `tests/unit/test_no_vendor_literals.py` | **modified** | ③ 同上（函数体内元组硬编码，G-02 纳入委派面） |
| 56 | `tests/unit/test_pilot_backend_selection.py` | **modified** | ③ 同上 |
| 57 | `tests/unit/test_pilot_rehearsal.py` | **modified** | ③ 同上（形态特定期望值按裁决改为"**期望值入配置 + 断言读配置**"） |
| 58 | `tests/unit/test_smoke_llm_profile.py` | **modified** | ③ 同上 |
| 59 | `agents/pilot/pilot.py` | **modified** | ④ `form_clause_completeness` 追加 + `:613` 裸形态词收敛 |
| 60 | `tests/unit/test_form_clause_completeness.py` | new | ④ 020 口径逐项机检与 cadence 越界取证 |
| 61 | `tests/conftest.py` | **modified** | ⑤ `PILOT_FORMS` 与 `pilot_form_config_path` 改派生 |
| 62 | `tests/unit/test_config_integrity.py` | **modified** | ⑤ 配置集合改派生 + 新增清单解析器与必需键条目 |
| 63 | `tests/unit/test_form_registration.py` | new | ⑤ 五处逐一 / 登记完备三条 / 不新造第六处 / 越界取证 |
| 64 | `tests/unit/test_pilot_chain_seven.py` | **modified** | ⑤ `config_completeness` 返回段清单变长的扩展 |
| 65 | `ops/demo_form_plugin.py` | new | ⑥ 离线端到端演示（九步；**A5 创建、形态无关**——遍历 `declared_forms()`，对新形态零改动即可演示） |
| 66 | `ops/form_onboarding.py` | new | ⑥ 清单 + 登记点完备性 + `MECHANISM_LEDGER` |
| 67 | `ops/form_plugin.py` | new | ⑥ CLI 门面（`guard` / `registration` / `onboarding` / `sync-versions`） |
| 68 | `tests/contract/test_form_onboarding_contracts.py` | new | ⑥ C12 / C13 的可执行面 |
| 69 | `tests/unit/test_form_onboarding.py` | new | ⑥ 清单派生一致率 / 类别判定 / 越界取证 / append-only |

**A1 夹具同步面的口径与实测偏差（如实登记）**：派生口径是"**凡在 `tests/**` 内调用六个
`build_*_evaluators` 的测试文件**"（由符号调用反查，不是人工维护）；但**符号命中面 ⊋ 改动面**——
实测命中 **16 个文件**，其中**只有 5 个**真的需要补 `evaluators` 声明段（上表 ① 组的
`tests/unit/test_{dev,editing,screenplay,storyboard}_composite.py`、`tests/unit/test_screenplay_cli.py`；
`tests/conftest.py` 归 ⑤），其余 **10 个**（`tests/contract/test_dev_contracts.py`、
`tests/unbiasedness/test_*_unbiased.py`、`tests/unit/test_{dev,screenplay}_compare_adopt.py`、
`tests/unit/test_sound_composite.py`、`tests/unit/test_visual_consistency.py`）从**真实形态配置**取
声明段 ⇒ **未改动、不入账**（逐条登记在 `NOT_LEDGER_ITEMS` 的剔除面）。**注意**：
`tests/unit/test_visual_composite.py` **不存在**（visual 的装配调用点在
`tests/unit/test_visual_consistency.py`）。

**本表与 `MECHANISM_LEDGER_PATHS` 的集合相等（判据，**不比较条数**）**：常量实测 **69** 条、
上表 69 行（编号 1~69 连续）⇒ **双向差集为空**。

**T2202 的差集收口登记（2026-09-26，按集合相等口径复核）**：本任务清单里的差集前提（"本表 **55** 行 vs
常量 **56** 条 ⇒ 1 条待收口"）**已在 `44edc1b` 的 ledger 穷举中收口**——那一次迭代**补齐 22 条**
（①组 8：六份 `agents/*/config.py` 声明面 + `tests/plugin_fixtures.py` + `tests/plugin_stubs.py`；③组 14：
`ops/{dev,screenplay,demo_shortdrama_feedback}.py` + 11 份同族副本），**剔除 13 条**（10 条"符号命中但
实测未改动"的夹具 + 3 条注释/文档面，逐条登记在 `NOT_LEDGER_ITEMS`）；口径也由"符号命中面"改为
**"实际改动面"**。**本次（T2202）复核的实测结论**：两侧**均不缺**——`表 \ 常量 == 常量 \ 表 == ∅`
（表 69 行 / 常量 69 条；`kind` 列逐行与常量一致，`ops/form_guard.py` 与 `tests/unit/test_form_switch.py`
各只出现一行）⇒ **本表无需再补条**，差集判定结论 = "谁都没缺，差额已被 ledger 穷举收口"。

**不属于机制侧 ledger 的两类（避免自相矛盾）**：① `core/evaluators/plugins/**`（含其 `__init__.py`）——
它是 **B 阶段接入侧**的新增面（清单里恒为 `category = "plugin"` ⇒ 放行）；② `specs/**` / `docs/**` 的
机制侧文档变更——它们在清单里恒属 `test_doc` 放行类，**不重复登记**。
（与 `contracts/onboarding-ops.md` C12 的 `plugin` 放行面、C13 的 `NOT_LEDGER_ITEMS` **同一口径**。）

**排除面 `NOT_LEDGER_ITEMS`（机检在做集合比较前必须逐条剔除，与 C13 同一口径）**：本表**真·条目
来源只有一处** = 上表的**路径列**；正文里另以反引号写出的下列路径**都不是条目**——
`tests/unit/test_visual_composite.py`（**不存在**；visual 的装配调用点在
`tests/unit/test_visual_consistency.py`，且**未改动**）、`tests/contract/test_screenplay_contracts.py`
及 storyboard / editing / sound 同族（**不存在**；`tests/contract/` 下**只有** `test_dev_contracts.py`
命中，且**未改动**）、`tests/contract/test_dev_contracts.py` / `tests/unbiasedness/test_*_unbiased.py` /
`tests/unit/test_{dev,screenplay}_compare_adopt.py` / `tests/unit/test_sound_composite.py` /
`tests/unit/test_visual_consistency.py`（**符号命中但实测未改动** ⇒ 不入账）、
`tests/unit/test_calibration_config.py`（存在但**明确不动**）、
`agents/pilot/stages.py` / `agents/pilot/run_report.py` / `docs/三期立项书.md`（**注释/文档面**：
019 构造点普查事实 13 → 14 的更正，无语义变更、不服务 FR-013 任一项）、
`plan.md` / `research.md` / `quickstart.md` / `data-model.md` / `spec.md`（**设计件的行文引用**）、
**以及一切"不含目录前缀的裸文件名"片段**（`__init__.py` / `test_dev_contracts.py` /
`test_screenplay_contracts.py` 等）——片段同样**不作为条目**。
⇒ **集合相等即通过，不比较条数、不比较顺序**；把正文引用与剔除面一并算进去的**朴素抽取**会得到
虚高计数——那是抽取口径问题，**不是**两侧表集合不一致，**条数从来不是判据**
（两处一致：C13 的"机检口径（唯一）"段与本段）。

**三步验收流程（不得颠倒）**：① 机制支线提交（打一个可引用的 ref = `mechanism_ledger_ref`）→
② 以 ① 为基线做新形态接入（**只新增配置 + 插件**，外加测试与文档）→ ③ 跑
`onboarding --baseline <①>` 必须 `violations == []`。
**反证（证明机制侧不是零代码改动）**：以 **① 之前的 ref** 为基线跑同一条命令 ⇒ `violations` **必然非空**，
且逐条命中上表的 **modified** 项（含六个装配函数、`agents/pilot/pilot.py`、`core/calibration/config.py`、
两份既有配置与 A1 夹具同步面）。

**`ops/demo_form_plugin.py` 的归属（I-09 裁决，写清以免 B1/B2 验收永远红）**：该脚本**由阶段 A5 创建**、
**形态无关**（遍历 `declared_forms()`），⇒ 属**机制侧资产**、位于 `baseline_ref` **之前**，
**不进**新形态接入的改动清单；**不得**把 `ops/demo_*.py` / `ops/form_*.py` 加进放行面
（那会拆掉"**新增 ops CLI 即越界**"的牙齿）。

---

## 如何看产物（离线跑完后的逐件核对）

**产物一律落 `--out` 临时目录**（I-07；`--out` **不接受**仓库内路径）——判据 = 跑完后
`git ls-files --others --exclude-standard` 的输出集合**前后逐条相等**。下表路径中的 `<out>` 即该临时目录。

| 产物 | 路径 | 看什么 |
| --- | --- | --- |
| 接入改动清单 | `<out>/onboarding-<form>-<seq:04d>.json` | `schema`（当前 `1`）/ `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]`（逐条 `path` + `status` + `category`（**英文枚举** `config`/`plugin`/`test_doc`/`out_of_scope`）+ `violation` + `reason`）/ `counts`（**中文键名**：`配置`/`插件`/`测试与文档`/`越界`/`既有模块被修改`）/ `violations[]` / `mechanism_changes_included` / `zero_code_onboarding` / `exit_code`——**字段与取值域以 `contracts/onboarding-ops.md` C12 的权威表为准** |
| 清单索引 | `<out>/index.jsonl` | 每行一次接入的固定字段——**append-only**，永不回改；同目录连跑两次 ⇒ 行数 +2、旧清单字节不变 |
| 守卫报告 | `<out>/guard-report.json` | 逐条 `(相对路径, 所属符号名, 行号, 命中内容)`（**符号名锚点**）；字面量层与判断分支层分别计数；例外三条的判定结果 |
| 登记报告 | `<out>/registration-report.json` | 五处逐一 `{site, symbol, kind, forms, delegated, missing[]}` + 登记完备三条结论 + 第六处反向扫描结论 |
| 演示报告 | `<out>/demo-report.json` | `steps`（九步逐条 `ok`）/ `ok` / `network: "none"` / `credentials_required: false` / `uncalibrated: true` / `uncalibrated_reason` / `elapsed_seconds` |
| 形态配置指纹 | 清单内 `config_fingerprint` | BLAKE3 十六进制**前 12 位**（口径沿用 `core/orchestration/models.py:36` 的 `fingerprint_of`）——"哪一次接入用的是哪一份配置"可指认 |
| 运行与节点留痕 | 演示的**临时**目录（默认已清理） | `eval_breakdown` 的键为 `evaluator_id@version`；**仓库根零残留** |

---

## 验收映射

| 契约 | 验证命令 | 成功标准 |
| --- | --- | --- |
| C1~C4 插件声明形状 / 唯一装配点 / 通用参数通道 / 版本冻结 | `pytest tests/unit/test_evaluator_plugin_assembly.py tests/contract/test_plugin_contracts.py`；B2 的 `sync-versions --check`；演示步 ③④⑥ | SC-004 / SC-005 |
| C5 / C6 / C7 / C8 字面量层与例外三条 / 分支层与形态名派生 / 三副本委派收敛 / 裸词收敛与 E1 例外登记 | `pytest tests/unit/test_form_guard.py`；`ops/form_plugin.py guard`；演示步 ⑦；A3 / A4 的 `grep` 取证 | SC-003 |
| C9~C11 五处登记点 / 登记完备 / 020 口径完备 | `pytest tests/unit/test_form_registration.py tests/unit/test_form_clause_completeness.py tests/unit/test_config_integrity.py tests/unit/test_form_switch.py`；`ops/form_plugin.py registration`；演示步 ①②⑧⑨ | SC-006 / SC-007 |
| C12~C13 接入改动清单 / 机制侧总账分账 | `pytest tests/unit/test_form_onboarding.py tests/contract/test_form_onboarding_contracts.py`；`ops/form_plugin.py onboarding --baseline <ref>`；本文"机制侧总账"的反证 | SC-001 / SC-002 |
| C14 CLI 与离线端到端演示 | `ops/demo_form_plugin.py --form ad`（退出码 0）；各入口 `--help` 0 / 缺 `--baseline` 2 / 有越界 1 | SC-008 |

**常驻门禁（本文不展开跑法，不放松）**：单元测试覆盖率 ≥ 85%（口径不降，含 `web`，
见 `.github/workflows/ci.yml`）；对抗测试（合并阻塞）、无偏性（发布阻塞）、Immutable 审计与
成本回归（每日）四道门禁常驻；`billing_alerts.yml` 的每日只读告警继续在位。
本特性**零真实渠道调用**——最小可行形态**不声明 judge** ⇒ 离线演示**不构造** `LLMGateway`
（若确需构造，则必须显式登记进 `OFFLINE_ASSEMBLIES` 并同步构造点计数，见 C14）。

---

## 验证记录

**只回填已实跑的结果；未跑的不下结论。**

- **已实跑（A 组既有面基线，2026-09-25，本仓）**：
  - `uv run ruff check .` → **All checks passed!**
  - `uv run ruff format --check .` → **585 files already formatted**
  - `uv run pytest tests/unit/test_form_switch.py -q` → **17 passed**（12.98s）
  - `uv run pytest tests/unit/test_config_integrity.py -q` → **125 passed**（33.00s）
  - `uv run pytest "tests/contract/test_pilot_contracts.py::TestC10到C13试水运行::test_c13_两套配置差异可归因且无形态分支" -q`
    → **1 passed**（0.81s）
  - **A3 机制缺口取证**：`grep -rnE "plugin|entry_point|import_module" core agents --include=*.py`
    → **0 命中**；`ls core/evaluators/` → **无 `plugins/` 目录**（声明面与插件落点均未建立）
  - **A4 守卫失明取证**：`grep -rnE "(^|[^0-9A-Za-z_])movie([^0-9A-Za-z_]|$)" core agents --include=*.py`
    → **恰好 2 处**（`core/deployment/evidence.py:97` 的配置路径字面量、`agents/pilot/pilot.py:613` 的裸形态词）；
    `grep -rn "animated" core agents --include=*.py` → **0 命中**；`grep -rn "漫剧" core agents --include=*.py`
    → **0 命中** ⇒ 新形态名**不会被今天的人工常量清单扫到**（守卫对新形态天然失明）
  - **A5 词边界取证**：`ad` 的**子串**出现 **1999 处**（`core/` + `agents/` 的 `.py`），
    **词边界**出现 **0 处** ⇒ 若按子串判定，`ad` 会命中 `read` / `load` / `head` 一类常见标识符，
    假阳性会逼出无意义的改名（`research.md` 决策 6 的量化依据）
  - **A6 基线派生取证**：`git diff --name-status HEAD` → **空**（无已跟踪文件改动）；
    `git ls-files --others --exclude-standard` → **全部未跟踪的 `specs/021-form-plugin-validation/**` 设计件**
    （本次实测为 **7 个**，因为该命令在 `quickstart.md` 落地之前执行）⇒ 只跑前者会把"新增"整类漏掉
    （C12 的"必须含未跟踪新增文件"由此得到现场举证）
  - **A7 差异集键数实测（I-03 的纠错依据）**：用 `yaml.safe_load` 逐键比较
    `configs/movie.yaml` 与 `configs/shortdrama.yaml` ⇒ 顶层差异集 **15 键**（`form` / `evaluator_weights` /
    `replay` / `promo` / `visual` / `sound` / `editing` / `storyboard` / `screenplay` / `dev` / `pilot` /
    `calibration` / `dreaming` / `deployment` / `budget`），与
    `tests/unit/test_form_switch.py:341-379`、`tests/contract/test_pilot_contracts.py:434-453` 的字面集合**逐字相同**
    ⇒ 三份契约中的键数表述**已按实测更正为 15 键**（旧表述的 16 是偏大值）
  - **A8 `.gitignore` 实测（I-07 的依据）**：`cat .gitignore` ⇒ **无** `.specify` 规则（只有 `.venv/` /
    `__pycache__/` / `.pytest_cache/` / `.coverage` / `.smoke-llm/` / `/billing/` 等）
    ⇒ 产物落 `.specify/onboarding` **必然**改变未跟踪集合 ⇒ `--out` 一律改走**临时目录**
  - **A9 A1 夹具同步面实测（I-04 的清单依据）**：`grep -rln -E "build_(dev|screenplay|storyboard|sound|editing)_evaluators|build_evaluators\(" tests/`
    ⇒ 命中 **16 个文件**（`tests/conftest.py` + 15 个）；但**符号命中面 ⊋ 改动面**——实测**只有 5 个**
    （`tests/unit/test_{dev,editing,screenplay,storyboard}_composite.py`、`tests/unit/test_screenplay_cli.py`）
    真的需要补 `evaluators` 声明段，其余 10 个从真实形态配置取声明段 ⇒ **未改动、不入账**
    （逐条登记在 `NOT_LEDGER_ITEMS` 的剔除面）；
    其中 **无** `tests/unit/test_visual_composite.py`、**无** `tests/contract/test_{screenplay,storyboard,editing,sound}_contracts.py`
    （实测偏差已如实登记在本文"机制侧总账"节与 `contracts/onboarding-ops.md` C13 的剔除面）
- **已实跑（B 组，2026-09-26，本仓；`--out` 一律 `mktemp -d` 临时目录）**：
  - `uv run python ops/form_plugin.py guard` → **退出码 0**（字面量层 `violations 0` / `candidates 1`，分支层
    `violations 0` / `candidates 0`；例外 E1 **恰好 1 处** = `core/deployment/evidence.py::_unbiasedness_result:97`）
  - `uv run python ops/form_plugin.py registration --config configs/movie.yaml` → **0**（五处逐一"已登记 + 已委派"、
    `forms = [ad, animated, movie, shortdrama]`、`sixth_sites []`、`completeness.violations []`）；
    对 `configs/ad.yaml` 与 `configs/animated.yaml` 各再跑一次 → 均 **0**
  - `uv run python ops/form_plugin.py sync-versions --check --config configs/shortdrama.yaml` → **0**（`diffs []`）
  - `uv run python ops/form_plugin.py onboarding --baseline 75181dc --mechanism-ref def1e14 --config configs/ad.yaml --out "$T"` → **0**
    （`violations []`、`counts = {配置 0, 插件 0, 测试与文档 2, 越界 0, 既有模块被修改 0}`、`config_fingerprint 97456ad7e897`）；
    `--config configs/animated.yaml` → **0**（同形 `counts`、`config_fingerprint 8505eaff8062` ⇒ 两份配置可指认）；
    缺 `--baseline` ⇒ **2**。**注**：以 `75181dc` 为基线时"配置/插件 = 0"是**该 ref 已含接入件**的后果
    （提交卫生，见 T2180）；权威接入取证 = 以 `44edc1b` 为基线的那两次运行（`counts = {配置 2, 插件 2, 测试与文档 6, 越界 0, 既有模块被修改 0}`）。
  - `uv run python ops/demo_form_plugin.py --form movie --out "$(mktemp -d)"` → **0**（`elapsed_seconds 60.063`）；
    `--form ad` → **0**（`52.245`）；`--form animated` → **0**（`56.378`）；`steps` **九步逐条 ok**、
    `network "none"` / `credentials_required false` / `uncalibrated true` / `uncalibrated_reason` 非空；
    `--form nope`（未声明形态）⇒ **2**；`--help` ⇒ **0**；跑完 `git status --porcelain` **无运行期产物**。
  - **产物齐备与 append-only**：同一 `--out` 依次跑 `guard` / `registration` / `onboarding`（对 ad 连跑两次）/ `demo`
    ⇒ **五类产物齐备**（六件：清单类 `onboarding-ad-0001.json` 与 `onboarding-ad-0002.json`、`index.jsonl`、
    `guard-report.json`、`registration-report.json`、`demo-report.json`）；`index.jsonl` 行数 **2**、
    `onboarding-ad-0001.json` 的 sha256 **前后不变** ⇒ append-only 成立。
  - **单文件测试子集（T2187 口径）**：`uv run pytest tests/unit/test_form_onboarding.py tests/contract/test_form_onboarding_contracts.py
    tests/unit/test_pilot_upgrade_path.py tests/unit/test_config_integrity.py tests/unit/test_form_switch.py
    tests/unit/test_form_no_new_dependency.py -q` → **428 passed**（exit 0），逐文件采集数
    **38 / 31 / 8 / 325 / 20 / 6**（`test_form_switch.py` 基线 **17**、`test_config_integrity.py` 基线 **125**
    ⇒ 用例数**只增不减**）；`uv run pytest tests/unit/test_form_guard.py tests/unit/test_form_registration.py -q` → **52 passed**。
  - **阶段 2~5 的其余单文件子集（本次实跑，2026-09-26）**：`tests/unit/test_form_clause_completeness.py`
    `tests/unit/test_evaluator_plugin_assembly.py` `tests/unit/test_core_plugin_artifact_metadata.py`
    `tests/contract/test_plugin_contracts.py` `tests/unit/test_pilot_chain_seven.py` `tests/unit/test_pilot_rehearsal.py`
    `tests/unit/test_billing_channels.py` `tests/unit/test_billing_core_purity.py` `tests/unit/test_no_vendor_literals.py`
    `tests/unit/test_dev_core_degraded_purity.py` `tests/unit/test_sound_composite.py` `tests/unit/test_dev_compare_adopt.py`
    `tests/unit/test_calibration_config.py` `tests/unit/test_visual_loop.py` `tests/unit/test_visual_consistency.py`
    `tests/unit/test_screenplay_composite.py` `tests/unit/test_storyboard_composite.py` `tests/unit/test_editing_composite.py`
    `tests/unit/test_dev_composite.py` `tests/unit/test_screenplay_cli.py` `-q` → **482 passed / 2 failed**。
    **两处失败全部落在 `tests/unit/test_billing_channels.py::TestC18诚实分层`**（`test_source_只能由装配面声明` /
    `test_拒绝即零调用零入账且来源记_refused`，报 `RunLogError: 运行记录不存在：…/runs/2026-09-25.json`），
    根因是 **020 遗留的日期依赖**（两处 `RecordingChannelCall` 未传 `clock` ⇒ 运行记录按**当日** `2026-09-26`
    落盘、断言却写死读 `2026-09-25`；021 对该文件的改动**只有** `FORMS = declared_forms(...)` 三行）
    ⇒ **与 021 判据无关**、**当日已修复**（两处 `RecordingChannelCall` 传 `clock=lambda: MOMENT`，同该文件既有口径）；
    修复后 `tests/unit/test_billing_channels.py` 复跑绿（并入下条 ① 的 **111 passed**）⇒ 该 20 文件子集**现已全绿**。
  - **清单工具两处缺陷的修复（2026-09-26，机制侧，`ops/form_onboarding.py`）**：① 给 git 调用加
    `-c core.quotePath=false` ⇒ **非 ASCII 路径逐字还原**（此前中文文档被 C 转义引号带偏、误判 `out_of_scope`）；
    ② `README.md`（仓库根）纳入 `test_doc` 放行面（裁决：交付文档不是模块逻辑改动；六目录既有文件修改与
    新增 `ops/**` **仍越界**，牙齿不变）。**实测**：以 `75181dc` 为基线的 `onboarding` 输出里
    `README.md` 与 `docs/三期立项书.md` **均为 `test_doc`**，**唯一**越界项是 `ops/form_onboarding.py`
    ——**本次修复本身**（`ops/**` 任何改动按判据越界；未提交 ⇒ 位于基线之后）⇒ 待该修复**提交**并把
    基线/`MECHANISM_LANDED_REF` 前移后即为 `violations []`（判据未改）。回归用例：
    `tests/unit/test_form_onboarding.py::Test非ASCII路径与文档放行面`。
  - **`onboarding` 的文档面口径（2026-09-26 修复后已可复跑）**：工作树**含 stage 10 文档收口**时，
    `README.md` 与 `docs/**`（含中文路径）均归 **`test_doc`**、`violations []`、退出码 0——修复与回归用例
    见 `docs/三期立项书.md` 的 G5 交付说明"如实登记"④（`README.md` 放行裁决 + `git -c core.quotePath=false`
    的路径解码；六目录既有文件修改与新增 `ops/**` **仍越界**，牙齿不变）。
- **未跑（覆盖率 / 契约两条腿 / 集成 / 对抗 / 无偏性 / 全量 unit）**：**由父代理在宿主机执行**，本次交付纪律
  不在本机跑；本节**如实留白**、不预填结论，属常驻 CI 与定时工作流面；口径**不放松**（覆盖率 ≥85% 含 `web`）。
- **待业务侧裁决（开放问题，不属本特性可交付面）**：① 广告 / 漫剧形态的**真实业务定义**
  （受众 / 指标口径 / 素材规格 / 评估器组合的业务正确性）与"最小可行形态"的签字形式；
  ② 广告 / 漫剧的**真实节律是否落在 `calibration.period_days ∈ {1,7}` 内**——本特性**不扩量纲**
  （`core/calibration/periods.py:30` 一字不改）；确需其他量纲 ⇒ **另立特性**，本特性**不代劳**、
  也**不得**用"按周近似 + 如实标注"含糊兜底。未裁决前按**最小可行形态**接入 + **三层"未标定"标注**。
