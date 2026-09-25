# Quickstart：形态插件扩展性验证（021-form-plugin-validation）

本文给出**可复制执行**的验证序列与期望结果。**全部命令离线、零真实花费、零外部网络、零凭证**；
命令与仓库既有工具链一致（`uv run …`，形态配置用 `configs/<form>.yaml`）。
契约落点：`contracts/plugin-config.md`（C1~C4，声明形状与唯一装配点 / 版本冻结）、
`contracts/zero-form-branch.md`（C5~C8，扫描面与形态名派生 / 副本收敛 / 裸词收敛）、
`contracts/form-registration.md`（C9~C11，五处登记点 / 登记完备 / 020 口径完备）、
`contracts/onboarding-ops.md`（C12~C14，接入改动清单 / 机制侧总账 / CLI 与离线演示）。

**当前状态如实标注**：本特性处于 **spec / research / plan / 契约阶段**——下文 **B 组命令所依赖的
实现**（`core/evaluators/plugin.py`、`core/evaluators/plugins/`、六个 `agents/<agent>/evaluators/plugins.py`、
`ops/form_guard.py`、`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py`
与相应的新增测试文件）**尚未落地** ⇒ B 组命令现在**必然找不到文件而失败**；A 组命令今天即可跑。
文末"验证记录"**只回填已实跑的结果**，不预填未跑的结论。

**今天"仅新增配置 + 插件"不成立（本特性必须先补机制）**：B 组不是"补测试"，而是"**机制不存在**"——
A 组里第 5~7 条命令即为该事实的三条取证。

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

## B. 本特性落地后可跑（B1~B5；实现尚未落地）

### B1 守卫与派生面（C5~C8）

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
uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> \
        --config configs/<new-form>.yaml --out .specify/onboarding
```

**期望结果**：退出码 **0**（`violations == []`），清单逐条给出路径 + `status` + **类别**
（配置 / 插件 / 测试与文档 / 越界），`counts["既有模块被修改"] == 0`；产物含 `baseline_ref` /
`mechanism_ledger_ref` / `config_fingerprint`（BLAKE3 前 12 位）；`index.jsonl` **追加**一行。
**越界取证**：人为在 `core/evaluators/composite.py` 里加一行注释 ⇒ 退出码 **1** 且**逐条点名**该路径。

### B4 离线端到端演示（C14）

```bash
uv run python ops/demo_form_plugin.py --form ad --out .specify/onboarding   # 九步；退出码 0 = 全步 ok
```

**期望结果**：退出码 **0**，`steps` 九步逐条 `ok`；产物 `network: "none"`、
`credentials_required: false`、`uncalibrated: true` 且 `uncalibrated_reason` 非空；
**仓库根零新增文件**（默认临时工作目录，演示结束即清理）。九步逐条对应见
`contracts/onboarding-ops.md` C14 的表（配置加载与预检 → 缺项即拒绝 → 插件装配 → 评估与合成分数 →
留痕 → 两形态共用同一份插件代码 → 静态守卫 → 登记点完备 → 诚实分层与零成本）。

### B5 接入方流程（写配置 → 写插件 → 声明 → 校验 → 审计）

```bash
# 1) 新增 configs/<new-form>.yaml：段集合与既有两形态一致 + 020 口径逐项声明 + "未标定"标注 + evaluators 声明
# 2) 写业务无关的评估器插件：通用件落 core/evaluators/plugins/，Agent 绑定件落 agents/<agent>/evaluators/
#    （**两类都必须经 impl 声明才生效**——目录不决定可用性）
uv run python ops/form_plugin.py sync-versions --check --config configs/<new-form>.yaml   # 3) 版本一致性（只校验）
uv run python ops/form_plugin.py registration --config configs/<new-form>.yaml            # 4) 五处登记点逐一
uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> \
        --config configs/<new-form>.yaml --out <目录>                                      # 5) 接入改动清单（越界即非 0）
uv run python ops/demo_form_plugin.py --form <形态 id> --baseline <机制落地 ref>           # 6) 离线端到端
```

**形态名约定**：形态名**一律由 `configs/*.yaml` 的 `form:` 派生**；中文别名由 `form_aliases` 键声明
（**键名与派生口径以 `contracts/zero-form-branch.md` C6 为权威**，本文不另定一套写法）。
本文在示例命令里只使用形态 id **`ad`** 与 **`animated`**。

**命令行示例约定**：`<>` 包裹的取值（`<机制落地 ref>` / `<形态 id>` / `<目录>` / `<new-form>`）一律是
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
`ops/form_onboarding.py` 的 `MECHANISM_LEDGER` **逐条一致**（C13 机检 4：路径列与
`MECHANISM_LEDGER_PATHS` 相等，**恰好 39 条**：新增 18 + 修改 21）；`kind` 列里的 **new** = 新增文件、
**modified** = **既有模块被修改**（机制侧**必然**含 `modified` 项——这正是"机制侧不是零代码改动"的机器证明）。

| # | 路径 | kind | 服务哪一项总账（FR-013 的序号） |
| --- | --- | --- | --- |
| 1 | `core/evaluators/plugin.py` | new | ① 唯一装配点（声明解析 + `importlib` + 参数注入 + 一致性校验） |
| 2 | `core/evaluators/errors.py` | **modified** | ① 错误类型（`EvaluatorError` 之下增声明/装配期错误） |
| 3 | `agents/visual/evaluators/plugins.py` | new | ① Agent 绑定薄工厂 |
| 4 | `agents/dev/evaluators/plugins.py` | new | ① 同上 |
| 5 | `agents/screenplay/evaluators/plugins.py` | new | ① 同上 |
| 6 | `agents/storyboard/evaluators/plugins.py` | new | ① 同上 |
| 7 | `agents/sound/evaluators/plugins.py` | new | ① 同上 |
| 8 | `agents/editing/evaluators/plugins.py` | new | ① 同上 |
| 9 | `agents/visual/loop.py` | **modified** | ① `build_evaluators` 改委派（签名与返回形状不变） |
| 10 | `agents/dev/evaluators/__init__.py` | **modified** | ① `build_dev_evaluators` 改委派 |
| 11 | `agents/screenplay/evaluators/__init__.py` | **modified** | ① `build_screenplay_evaluators` 改委派 |
| 12 | `agents/storyboard/evaluators/__init__.py` | **modified** | ① `build_storyboard_evaluators` 改委派 |
| 13 | `agents/sound/evaluators/__init__.py` | **modified** | ① `build_sound_evaluators` 改委派（扁平列表、保序） |
| 14 | `agents/editing/evaluators/__init__.py` | **modified** | ① `build_editing_evaluators` 改委派 |
| 15 | `configs/movie.yaml` | **modified** | ① 新增 `evaluators` 声明段（既有取值零改动） |
| 16 | `configs/shortdrama.yaml` | **modified** | ① 同上（与 movie 的该段逐字相同） |
| 17 | `core/calibration/config.py` | **modified** | ① A4：cadence 取值域收口（取值域取自 `core/calibration/periods.py:30`，该文件一字不改） |
| 18 | `tests/unit/test_evaluator_plugin_assembly.py` | new | ① 装配面单测 |
| 19 | `tests/contract/test_plugin_contracts.py` | new | ① 插件契约测试（C1~C4 的可执行面） |
| 20 | `ops/form_guard.py` | new | ②③ 形态名派生 + 两层扫描（**单一实现**） |
| 21 | `tests/unit/test_form_guard.py` | new | ② 守卫自检（注入即红 / 派生失败即报错） |
| 22 | `tests/unit/test_form_switch.py` | **modified** | ②⑤ 委派收敛 + 排除项删去 + "恰好两份"→登记完备 |
| 23 | `tests/unit/test_billing_core_purity.py` | **modified** | ② 副本委派（断言体原位保留） |
| 24 | `tests/unit/test_dev_core_degraded_purity.py` | **modified** | ② 副本委派（同上） |
| 25 | `tests/contract/test_pilot_contracts.py` | **modified** | ③ 禁用元组改派生（差异集断言一字不改） |
| 26 | `tests/unit/test_billing_channels.py` | **modified** | ③ 同族枚举副本改派生 |
| 27 | `tests/contract/test_billing_contracts.py` | **modified** | ③ 同上 |
| 28 | `tests/unit/test_pilot_rehearsal.py` | **modified** | ③ 同上（形态特定期望值改"逐形态声明期望值"） |
| 29 | `agents/pilot/pilot.py` | **modified** | ④ `form_clause_completeness` 追加 + `:613` 裸形态词收敛 |
| 30 | `tests/unit/test_form_clause_completeness.py` | new | ④ 020 口径逐项机检与 cadence 越界取证 |
| 31 | `tests/unit/test_form_registration.py` | new | ⑤ 五处逐一 / 登记完备三条 / 不新造第六处 / 越界取证 |
| 32 | `tests/unit/test_config_integrity.py` | **modified** | ⑤ 配置集合改派生 + 新增清单解析器与必需键条目 |
| 33 | `tests/conftest.py` | **modified** | ⑤ `PILOT_FORMS` 与 `pilot_form_config_path` 改派生 |
| 34 | `tests/unit/test_pilot_chain_seven.py` | **modified** | ⑤ `config_completeness` 返回段清单变长的扩展 |
| 35 | `ops/form_onboarding.py` | new | ⑥ 清单 + 登记点完备性 + `MECHANISM_LEDGER` |
| 36 | `ops/form_plugin.py` | new | ⑥ CLI 门面（`guard` / `registration` / `onboarding` / `sync-versions`） |
| 37 | `ops/demo_form_plugin.py` | new | ⑥ 离线端到端演示（九步） |
| 38 | `tests/unit/test_form_onboarding.py` | new | ⑥ 清单派生一致率 / 类别判定 / 越界取证 / append-only |
| 39 | `tests/contract/test_form_onboarding_contracts.py` | new | ⑥ C12 / C13 的可执行面 |

**三步验收流程（不得颠倒）**：① 机制支线提交（打一个可引用的 ref = `mechanism_ledger_ref`）→
② 以 ① 为基线做新形态接入（只新增配置 + 插件 + 测试文档）→ ③ 跑
`onboarding --baseline <①>` 必须 `violations == []`。
**反证（证明机制侧不是零代码改动）**：以 **① 之前的 ref** 为基线跑同一条命令 ⇒ `violations` **必然非空**，
且逐条命中上表的 **modified** 项（第 2 / 9~17 / 22~24 / 25~28 / 29 / 32~34 行）。

---

## 如何看产物（离线跑完后的逐件核对）

| 产物 | 路径 | 看什么 |
| --- | --- | --- |
| 接入改动清单 | `<out>/onboarding-<form>-<seq:04d>.json` | `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]`（逐条路径 + `status` + 类别 + 越界标记）/ `counts`（含 `既有模块被修改`）/ `violations[]` / `mechanism_changes_included` / `zero_code_onboarding` / `exit_code` |
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
| C5~C8 扫描面与形态名派生 / 副本收敛 / 裸词收敛 | `pytest tests/unit/test_form_guard.py`；`ops/form_plugin.py guard`；演示步 ⑦；A3 / A4 的 `grep` 取证 | SC-003 |
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
- **未跑（如实留白，B 组）**：`tests/unit/test_form_guard.py`、`tests/unit/test_form_registration.py`、
  `tests/unit/test_form_clause_completeness.py`、`tests/unit/test_form_onboarding.py`、
  `tests/unit/test_evaluator_plugin_assembly.py`、`tests/contract/test_plugin_contracts.py`、
  `tests/contract/test_form_onboarding_contracts.py` 与 `ops/form_guard.py` / `ops/form_onboarding.py` /
  `ops/form_plugin.py` / `ops/demo_form_plugin.py` **均尚未落地**（已核实：文件不存在）⇒
  B 组命令与"期望结果"**属待验证契约**，本文**不预填**其结论；实现落地后回填。
- **未跑（覆盖率 / 集成 / 对抗 / 无偏性 / 全量 unit）**：按本次交付纪律**不在本机跑**，
  属常驻 CI 与定时工作流面；口径**不放松**（覆盖率 ≥85% 含 `web`）。
- **待业务侧裁决（开放问题，不属本特性可交付面）**：① 广告 / 漫剧形态的**真实业务定义**
  （受众 / 指标口径 / 素材规格 / 评估器组合的业务正确性）与"最小可行形态"的签字形式；
  ② 广告 / 漫剧的**真实节律是否落在 `calibration.period_days ∈ {1,7}` 内**——本特性**不扩量纲**
  （`core/calibration/periods.py:30` 一字不改）；确需其他量纲 ⇒ **另立特性**，本特性**不代劳**、
  也**不得**用"按周近似 + 如实标注"含糊兜底。未裁决前按**最小可行形态**接入 + **三层"未标定"标注**。
