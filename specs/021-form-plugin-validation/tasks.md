# 任务列表：形态插件扩展性验证（配置化评估器插件接入 + 零形态分支静态断言补面 + 接入改动清单机检）

**输入**: `specs/021-form-plugin-validation/` 的设计文档（spec.md 173 行：3 用户故事 / 14 FR / 9 SC / 14 条边界情况 / **13 项裁决** / 2 项开放问题；plan.md **617 行**：A 机制侧 A1~A5 与 B 接入侧 B1~B3、FR→契约对照表、复杂度跟踪、风险与回滚、兼容性段；research.md **742 行**：决策 1~12；data-model.md **462 行**：10 实体 + I-1~I-21 可机检不变量；contracts/ **C1~C14**——**已与本次 analyze 修订同步**：机制侧总账**不写死条数**、判据为"文档表与常量的**集合相等**"、夹具同步面**由符号调用反查**、演示脚本**属机制侧（A5 创建）且形态无关**、`--out` **必须落临时目录**；quickstart.md）。**注**：上述行数为本清单修订时的实测值，设计件在并行修订中可能继续变化 ⇒ **本清单对设计件的引用一律以符号名/键名为锚，行号只作人读辅助**。

**前置条件**: 项目宪章 **v2.0.0**（原则五：依赖单向 `agents → core`、形态差异**必须**经 `configs/*.yaml` 表达、切换形态**必须**零代码改动、形态配置例外**必须**是"新增配置项"而非"新增分支代码"；原则一：`evaluator_id@version` 全局唯一、同 id 同 version 重复注册**必须**被拒、行为变更**必须**升版本号；原则六：口径**必须**可被证伪、局限**必须**如实标注）。已交付可复用资产：001 的注册中心与 `EvaluatorSpec`（`core/evaluators/registry.py:18`/`:59`、`core/evaluators/base.py:42`）、010 的权重/合成读取面（`core/evaluators/weights.py:18`、`core/evaluators/composite.py:37`）、015 的零形态分支守卫（`tests/unit/test_form_switch.py` 整节）、017 的 AST import 扫描先例（`tests/unit/test_dev_core_degraded_purity.py:167-179`）、019 的 `budget.channels` 渠道命名空间、020 的**五处登记点**与 cadence 口径（`core/calibration/periods.py:30`）

**测试说明**: TDD——**测试任务排在对应实现任务之前**；全部用例离线（零真实花费、零外部网络、零凭证）；既有断言**按扩展更新、不削弱**（原则：**零删除、零放宽**，逐条见 `specs/021-form-plugin-validation/research.md:577` 起决策 12 的 **19 项**清单）。本特性的关键在于**两类改动分开记账**（A 机制侧 = 本特性的代码改动主体；B 接入侧才是"仅新增配置 + 插件"，其基线**必须**取"机制落地后、接入前"的提交，否则判据自相矛盾，`specs/021-form-plugin-validation/spec.md:87` 场景 5）、**既有评估器实现文件零改动**（版本号把实现文件**字节**并入哈希 ⇒ 改一字节即改 `eval_breakdown` ⇒ 违反 FR-013）、**扫描面补到 `core/` + `agents/` 全覆盖（含此前被排除的 `agents/pilot`）**、**形态名一律由 `configs/*.yaml` 的 `form:` 派生（零人工常量清单）**、**三副本委派收敛且断言语义原位保留**

**组织方式**: 阶段 0 前置与勘查核对 → **A 机制侧** A1~A5（= 阶段 2~6：A1 插件声明面与唯一装配点 / A2 扫描面补面与形态名派生 / A3 五处登记点派生与登记完备 / A4 020 口径完备与 cadence 收口 / A5 接入改动清单与 CLI）→ **B 接入侧** B1~B3（= 阶段 7~9：B1 广告形态接入 / B2 漫剧形态接入 / B3 离线端到端与门禁同步）→ 阶段 10 文档与门禁同步 → 验收与复核。**慢门禁单列在"验收与复核"段并由父代理在宿主机执行**（子代理不得跑：会超时）。

**⚠️ 执行者分工（硬约束）**: 子代理只跑**单文件快速子集**（阶段 0~10 各自的"快速核对"任务，以及新增测试文件的单文件跑法）。覆盖率（`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`）、契约两条腿（`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`）、集成（`uv run pytest tests/integration -m integration`）、对抗（`uv run pytest tests/adversarial -m adversarial`，**该套件不具备并行安全性、必须串行**）、无偏性（`uv run pytest tests/unbiasedness -m unbiasedness`）、静态检查双绿（`uv run ruff check . && uv run ruff format --check .`）**六类慢门禁全部由父代理在宿主机执行**（见文末 T2188~T2193）。

**⚠️ 阶段 1 说明**: 本特性的"契约与设计"阶段（对应 020 的"阶段 1 契约与 TDD 骨架"）已由 `specs/021-form-plugin-validation/{spec,plan,research,data-model}.md` 与 `contracts/*.md`（**C1~C14**）承载，**本清单不再设阶段 1**；TDD 骨架按 A/B 各阶段"先写测试"就地落地。

**⚠️ 本版修订登记（第二轮，依据 analyze 报告，共 17 项）**: ① **I-09**：`ops/demo_form_plugin.py` **前移进 A5（机制 ref 之前）且必须"形态无关"**（遍历 `declared_forms()`；B1/B2 只新增配置与插件 ⇒ 否则 C12 的"新增 ops CLI = 越界"会让 T2167①/T2173① 必红）；② **I-03**："差异集固定 **16 键**"→ **15 键**（实测 `tests/unit/test_form_switch.py:345-369` 与 `tests/contract/test_pilot_contracts.py:434-453` 各 15 键）；③ **I-04**：`MECHANISM_LEDGER_PATHS` 补 A1 的**夹具同步面**与新基线夹具，**不写死条数**、判据改"**文档表与常量的集合相等**"；④ **I-05**：T2186 的分解按 **FR-013 六项总账归属**计数；⑤ **I-06**：T2121 的范围限定为"**单个评估器实现模块**（`loudness.py`、`*_evaluator.py` 一类）"，**排除 `__init__.py` 与 `plugins.py`**；⑥ **I-07**：`--out` **必须指向 `tmp_path` 类临时目录**（T2166/T2175/T2194 统一）；⑦ **G-01**：新增"零新增运行时依赖"常驻用例（T2197）+ 复杂度论证核销（T2201）；⑧ **G-02**：`tests/unit/test_no_vendor_literals.py` **纳入委派面**（T2146）；⑨ **G-03**：反向扫描口径扩到"**任意 `ast.Tuple/List/Set/Dict`（含函数体内）与装饰器实参**"（T2198），并补"**命中数恒 0**"常驻断言；⑩ **G-04**：新增"宪章条款声明"（T2200）与"回放对比证据 / N/A 理由"（T2199）两条任务；⑪ **G-05**：派生名称面（含中文别名）在 `core/` + `agents/` 的既有命中数并入 T2104 取证；⑫ **U-04**：T2107 必须输出**穷举**夹具清单（含 `tests/adversarial/**` 结论）；⑬ **A-01**：T2178② 删去条件分支，**写死"不构造网关 ⇒ 计数不动（14）"**；⑭ **A-05**：T2194 的勘核占位改为**待填字段**并由 T2195 收口；⑮ **A-06**：T2146 的期望值处置**写死为"期望值入配置 + 断言读配置"**并登记被否决项；⑯ **I-16/I-17**：补同文件串行点（`configs/{movie,shortdrama}.yaml` 的 T2124/T2129、`agents/pilot/pilot.py` 的 T2133/T2142/T2152）、统一阶段 4 并列表、交汇点改为 T2172 与 T2173/T2174；⑰ **D-01**：T2146 的收敛清单并入**全仓形态枚举普查**结果（T2196，实测 ≥ 11 处）。

**⚠️ 号段安排（本版）**: 既有 **T2101~T2195 号保持不变**（仅 **T2166 的文档位置前移到 A5**，编号保留——镜像 020 的 T2086"跨阶段补号"先例，避免重编全部交叉引用）；**新增 7 条以 T2196~T2202 接续**，分别落：T2196（阶段 0）、T2197（阶段 6/A5）、T2198（阶段 4/A3）、T2199（阶段 9/B3）、T2200~T2202（阶段 10）。**合计 102 条，号段 T2101~T2202 连续无缺号**（各阶段条数见文末统计表）。

---

## 图一：阶段依赖图

```text
                     ┌────────────────────────────────────────────────────┐
                     │ 阶段 0 前置与勘查核对（T2101~T2108 + T2196，只读复核）│
                     └─────────────────┬──────────────────────────────────┘
                                       │ 事实基线：四条硬缺口 / 五处登记点的今天形状 /
                                       │ 三副本与扫描面盲区 / 两处既有字面量命中 + 派生名称面
                                       │ 命中数 / 版本哈希落点 / 零 DB 变更 / 19 项变红清单现状 /
                                       │ 内联配置字典夹具穷举清单 / 全仓形态枚举普查（≥11 处）/
                                       │ quickstart 机制侧总账（条数由常量给出、集合相等）
                                       ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │ A 机制侧（本特性的代码改动主体，FR-013 六项总账逐项可枚举）          │
        │                                                                      │
        │ 阶段 2 (A1) 插件声明面与唯一装配点（T2109~T2126）                    │
        │   ▲ **A 内部串行点**：T2111 的"改前装配序列快照"必须**先于**          │
        │     T2120 的装配函数委派落地（否则原则一/二失守）                    │
        │   ▲ 阶段 3 的 `ops/form_guard.py` 与 A1 文件**不重叠 ⇒ 可与 A1 并行**  │
        │                                                                      │
        │ 阶段 3 (A2) 扫描面补面 + 形态名派生 + 三副本收敛 + 裸词收敛（T2127~  │
        │   T2136）・阶段 4 (A3) 五处登记点派生 + 登记完备 + 枚举普查清零       │
        │   （T2137~T2147 + T2198）                                            │
        │   ▲ **串行点**：A2 的 `declared_forms()`/`form_literals()` 是 A3/A5   │
        │     的**唯一派生面**（A3 不能先于 A2 的 T2128、T2129）                │
        │                                                                      │
        │ 阶段 5 (A4) 020 口径完备与 cadence 收口（T2148~T2154）               │
        │   ▲ **串行点**：`agents/pilot/pilot.py` 被 A2 的 T2133、A3 的 T2142   │
        │     与 A4 的 T2152 **三次触碰 ⇒ 同文件必须串行**（T2133 → T2142 →     │
        │     T2152）                                                          │
        │                                                                      │
        │ 阶段 6 (A5) 接入改动清单 + CLI + **形态无关演示**（T2155~T2162 +      │
        │   T2166 + T2197）                                                    │
        │   ▲ 依赖 A1（装配集合↔权重键集）、A2（派生面）、A3（登记点白名单）、   │
        │     A4（`form_clause_completeness` 预检）⇒ **必须是 A 的最后一段**     │
        │   ▲ **I-09**：`ops/demo_form_plugin.py`（T2166）**在本段落地**，且     │
        │     演示**形态无关**（遍历 `declared_forms()`，对新形态零改动即可演示）│
        └───────────────────────────────┬──────────────────────────────────────┘
                                        │ **机制落地提交（打 ref = mechanism_ledger_ref）**
                                        │ ⚠️ 这一提交是 B 侧"零代码改动"判据的**基线**；
                                        │    基线与机制侧改动混同 ⇒ 判据自相矛盾（T2161）
                                        ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │ B 接入侧（机制建成后的举正面：**仅新增配置 + 插件 + 测试文档**）      │
        │                                                                      │
        │ 阶段 7 (B1) 广告形态接入（T2163~T2165、T2167~T2169）┐                 │
        │ 阶段 8 (B2) 漫剧形态接入（T2170~T2174）            ┘ **B1/B2 可并行**：│
        │   两份配置（`configs/ad.yaml` / `configs/animated.yaml`）彼此独立；    │
        │   **交汇点 = T2172（两形态并跑用例）与 T2173/T2174**（B2 的清单/登记/  │
        │   核对需 B1 与 B2 都就位 ⇒ 串行）；演示脚本已在 A5（T2166）落地 ⇒      │
        │   B1/B2 **零演示脚本改动**（形态无关性由 T2166 举证）                  │
        │ 阶段 9 (B3) 离线端到端 + 登记同步 + 留痕 + 回放对比证据（T2175~T2180   │
        │   + T2199，依赖 B1 与 B2）                                           │
        └───────────────────────────────┬──────────────────────────────────────┘
                                        ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │ 阶段 10 文档与门禁同步（T2181~T2187 + T2200~T2202：19 项变红清单核销 /│
        │   README / 三期立项书 G5 行 / pilot-upgrade-manifest / quickstart 回填│
        │   与总账表同步 / 总账一致性 / 宪章条款声明 / 复杂度论证核销 / 快速核对）│
        └───────────────────────────────┬──────────────────────────────────────┘
                                        ▼
        ┌──────────────────────────────────────────────────────────────────────┐
        │ 验收与复核 T2188~T2195（六类慢门禁**由父代理在宿主机执行**）           │
        └──────────────────────────────────────────────────────────────────────┘
```

**可并行关系（文件不重叠）**:

- **阶段 2（A1）**：T2109/T2110（两个新测试文件）可并行；T2114~T2119（六个 `agents/<agent>/evaluators/plugins.py`）**六份互不重叠、可并行**；T2121/T2122（两条独立机检）可并行；**A1 内部串行点 = T2111（对照快照）→ T2120（装配函数委派）**。
- **阶段 3（A2）**：T2128（`ops/form_guard.py`）与阶段 2 的 `core/evaluators/**`、`agents/*/evaluators/**` **不重叠 ⇒ A1 与 A2 可并行**；但 A2 内部 **T2128 → T2129 → T2130 → T2131 → T2132 → T2133/T2134**（同一派生面与同一批委派点）。**同文件串行（如实登记）**：T2124（A1）与 T2129（A2）都改 `configs/{movie,shortdrama}.yaml` ⇒ **必须先 T2124 后 T2129**；T2133（A2）与 T2142（A3）、T2152（A4）都改 `agents/pilot/pilot.py` ⇒ **T2133 → T2142 → T2152 串行**。
- **阶段 4（A3）**：**五处登记点改动**（T2138/①、T2140/②、T2141/③、T2142/④、T2144/⑤）**文件互不重叠、可并行**（都依赖 A2 的 T2128/T2129）；**连带面**（T2145 的 `REGISTRATION_SITES`、T2146 的枚举副本收敛、T2198 的反向扫描扩展与清零）**与五处文件不重叠、可并行**——但 **T2198 依赖 T2145 与 T2196**（白名单面与普查清单）。
- **阶段 5（A4）**：T2148/T2150/T2151/T2153 可并行；**T2149（`core/calibration/config.py`）与 T2152（`agents/pilot/pilot.py`，与 T2133/T2142 串行）各自单文件串行**。
- **阶段 6（A5）**：T2155/T2156（两个新测试文件）可并行；T2158（ledger 常量）与 T2159/T2160（CLI）可并行；T2197（依赖快照用例）与 T2166（演示）可并行（不同文件）。
- **阶段 7 / 8（B1/B2）**：两份形态配置与其插件声明**可并行**（T2164 / T2165 与 T2170 / T2171 不同文件）；**交汇点 = T2172（两形态并跑）与 T2173/T2174**（B2 侧的清单、登记与核对需 B1 与 B2 都就位）。
- **阶段 10**：T2182 / T2183 / T2184 / T2185 / T2200 / T2201 / T2202 **七份文档面互不重叠、可并行**；T2181（19 项核销）与 T2186（总账一致性）需最后做（以最终代码与最终常量为准）。

## 图二：跨阶段的关键依赖链

```text
链 1（插件装配链 —— "配置声明集 = 可用插件全集"）
  core/evaluators/plugin.py 的 parse_manifest / assemble / INJECTION_SLOTS（T2112）
    ├─ core/evaluators/errors.py 的 PluginDeclarationError / PluginAssemblyError（T2113）
    ├─ agents/<agent>/evaluators/plugins.py × 6 的 SLOT_LAYOUT 与薄工厂（T2114~T2119）
    │    └─ agents/visual/loop.py:103 的 _judge_anchor_hashes 整体迁入（T2114）
    ├─ 六个装配点改委派（T2120；**签名与返回形状不变**，`all` 由装配点派生）
    ├─ 各 *Config 的 plugin_declarations 承载属性（T2123；缺段 ⇒ None、不补默认）
    ├─ configs/{movie,shortdrama}.yaml 新增 evaluators 段（T2124；两形态**逐字相同**）
    └─ 内联配置字典夹具补段（T2125；**禁止**实现兜底 ⇒ 否则留下影子装配路径）
  ⇒ 对照机检：两形态装配序列 [(slot, id@version), ...]（含顺序）逐字相同（T2111）
  ⇒ 一一对应：装配集合 ↔ evaluator_weights.<agent> 键集（缺项/多项即拒绝装配）
  ⇒ **零新增运行时依赖**（FR-012）：pyproject/uv.lock 依赖集合 == 基线快照（T2197）

链 2（守卫链 —— 形态名派生面 = 扫描面唯一输入）
  configs/*.yaml 的 form: + form_aliases（T2129）
    → ops/form_guard.py 的 declared_forms（id 面）/ form_literals（名称面）（T2128）
    → iter_sources(("core","agents")) **含 agents/pilot**（T2128）
    → literal_violations / branch_violations（符号名锚点）/ classify_exception（E1/E2/E3）（T2128）
    → tests/unit/test_form_switch.py:423 的 pilot 排除**删去**（T2130，补面方向是变严）
    → 三副本**委派**收敛（T2131；断言体与循环体原位保留、副本数 ⇒ 1）
    → tests/contract/test_pilot_contracts.py:469-477 的写死元组改派生（T2132）
    → agents/pilot/pilot.py:613 的裸形态词**收敛**（T2133；**不得**加例外）
    → core/deployment/evidence.py:97 登记为**例外 E1**（T2134；**不得**改写该行）

链 3（登记链 —— 五处登记点从"两形态硬编码"改"配置/形态派生" + 全仓枚举清零）
  declared_forms()（T2128）
    ├─ ① tests/unit/test_form_switch.py 的 FORMS/_pair 改派生（T2138）
    │    └─ :438-441 的"恰好两份"升级为**登记完备**三条并列（T2139；**禁止删除**）
    ├─ ② tests/unit/test_config_integrity.py 的配置集合改派生 + 新增清单解析器与必需键条目（T2140）
    ├─ ③ tests/contract/test_pilot_contracts.py 的差异集（**15 键**）**原样保留** + 新增逐对断言（T2141）
    ├─ ④ agents/pilot/pilot.py 的 config_completeness 保通用性 + 追加 form_clause_completeness（T2142）
    │    └─ 返回段清单变长 ⇒ tests/unit/test_pilot_chain_seven.py:117-121 按扩展更新（T2143）
    └─ ⑤ tests/conftest.py 的 PILOT_FORMS 与 pilot_form_config_path 改派生（T2144）
  ⇒ 登记完备三条：form: 取值两两唯一 ∧ declared_forms() ⊆ registered_forms(site)（双向）∧ 配置数 ≥ 2
  ⇒ 不新造第六处：ops/form_onboarding.py 的 REGISTRATION_SITES（T2145）+ 反向扫描
  ⇒ **全仓形态枚举普查（T2196，实测 ≥ 11 处）→ 逐处改派生（T2146）→ 反向扫描面扩到
     任意容器字面量/装饰器实参（含函数体内）且命中数恒 0（T2198）**

链 4（020 口径链 —— 缺项即拒绝启动、不取码内默认）
  core/calibration/config.py 的 CalibrationConfig.from_dict 增 cadence 校验（T2149；取值域取自 `core/calibration/periods.py:30`）
    → agents/pilot/pilot.py 的 form_clause_completeness（T2142）逐项机检（含 cadence ∈ {1,7}）
    → "不适用"显式声明面（T2150；只允许 calibration.transfer / budget 两处）
    → 迁移口径联动（T2151；空声明 = 沉默失效 ⇒ 报错）
    → calibration.cadence_note（未标定形态必填、含「近似」与「未标定」、不得含「已标定/已达标/已投产」）
    → 三层"未标定"标注（形态层 pilot.rehearsal.status / 段层五段 note / 产物层 uncalibrated）（T2152）
    → 新形态配置逐项声明（T2164/T2170）→ 演示步①②⑨（T2166，A5 落地、形态无关）

链 5（分账链 —— 机制侧不是"零代码改动"的机器证明）
  MECHANISM_LEDGER（六项）+ MECHANISM_LEDGER_PATHS（**条数由常量给出**，含 A1 夹具同步面）（T2158）
    → ops/form_onboarding.py 的 changed_files / classify / build_manifest / write_manifest（T2157）
    → ops/form_plugin.py 的 onboarding --baseline（T2159）
    → 清单六产物键：baseline_ref / form / config_fingerprint / change_count / violations[] / exit_code
    → 越界 ⇒ 退出码 1 + 逐条点名（不得只报总数）
    → B1/B2 两次接入：counts["既有模块被修改"] == 0（T2167 / T2173）——**演示已前移 A5 ⇒ 不再越界**
    → 结构性反证：以 ledger **之前**的 ref 为基线 ⇒ violations 必然非空且命中 modified 项（T2161）
    → 门禁与文档收口（T2178 / T2181~T2186 / T2199~T2202）
```

## 格式：`[ID] [P] [Story] 描述`

- **[P]** = 可与同阶段其它任务并行（不同文件、无相互依赖）
- **[Story]** = 所属用户故事（**US1** 新形态以"配置 + 评估器插件"接入并离线跑通 / **US2** 零形态分支静态断言补面 / **US3** 接入改动清单与五处登记点完备性）
- 每条任务 = **一句话目标** + **精确文件路径**（新增文件路径一并写全）+ **完成判据**（可机检的断言或命令）

---

## 阶段 0：前置与勘查核对（只读，不改任何文件）

**目的**: 把 plan 的"四处真实缺口"与 research 的决策清单变成**带符号名与行号的事实基线**，使后续任务的失败断言有据可依。本阶段**零代码改动**，只产出核对结论（写入任务评论/交付说明，**不写仓库权威文件**）。

- [ ] T2101 [P] **复算"仅新增配置 + 插件今天不成立"的四条硬缺口** — 读 `core/evaluators/`（**无 `plugins/` 目录**）、六个装配函数 `agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、`agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、`agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27` — **完成判据**: `grep -rnE "plugin|entry_point|import_module" core agents --include=*.py | grep -v __pycache__` ⇒ **0 命中**；`ls core/evaluators/` ⇒ **无 `plugins/` 目录**；六个装配函数的**符号名 + 返回形状**（五个 dict / `build_sound_evaluators` 为扁平 list）逐条写"成立 / 已变（新行号）"。

- [ ] T2102 [P] **复算"恰好两份"与五处登记点的今天形状** — 读 `configs/movie.yaml:4` 与 `configs/shortdrama.yaml:6` 的 `form:`、`tests/unit/test_form_switch.py:30`（`FORMS`）与 `:438-441`（"恰好两份"）、`tests/unit/test_config_integrity.py:19-20`/`:23-40`/`:48-98`/`:153-154`、`tests/contract/test_pilot_contracts.py:434-453`（**15 键差异集**）/`:474`、`agents/pilot/pilot.py:377`（`config_completeness`，调用点 `:507`）、`tests/conftest.py:2854`（`PILOT_FORMS`）与 `:3054-3079`（`pilot_form_config_path`，未知形态 `:3072` 直接 `raise ValueError`） — **完成判据**: 五处逐条给"今天形状 → 新形态会静默逃逸 / 硬失败"结论；**差异集键数实测为 15**（逐键列出，与 T2138/T2141 的"15 键"口径一致）；实测基线 `uv run pytest tests/unit/test_form_switch.py -q` ⇒ **17 passed**（与 quickstart 的已跑结论一致）。

- [ ] T2103 [P] **复算扫描面盲区与形态名常量三副本** — 读 `tests/unit/test_form_switch.py:413`（`BANNED_LITERALS`）/`:414`（`BANNED_PATTERNS`）/`:423`（`if "pilot" not in path.parts`）、`tests/unit/test_billing_core_purity.py:34-35`、`tests/unit/test_dev_core_degraded_purity.py:28-29` — **完成判据**: 三副本逐处行号+内容核对；用**只读**脚本求出 `_sources("agents")` 的补集，结论必须为"`agents/pilot/**` 全部在补集内、`agents/pilot/backends.py` 在补集内"（装配点在盲区）；**不得**为此改动任何文件（实验后 `git diff` 为空）。

- [ ] T2104 [P] **复算 `core/` + `agents/` 内既有形态字面量的两处命中、派生名称面命中数与"不在扫描面"七处** — 读 `core/deployment/evidence.py:97`、`agents/pilot/pilot.py:613`（所属符号 `_require_duration_consistency`，`:604`）、`ops/billing.py:97`、`web/server.py:320`、`web/export.py:301`、`dreaming/deploy_hook.py:25`、`ops/ingest_metrics.py:121`、`ops/demo_merged_pool.py:55`、`ops/smoke_llm.py:358` — **完成判据**: ① `grep -rnE "(^|[^0-9A-Za-z_])movie([^0-9A-Za-z_]|$)" core agents --include=*.py | grep -v __pycache__` ⇒ **恰好 2 处**（`:97` 属例外 E1 / `:613` 属**违规**）；② **G-05：派生名称面（含中文别名）在 `core/` + `agents/` 的既有命中数**必须取证——对 `iter_sources(("core","agents"))` 的全集跑一次**只读**的名称面扫描（今天名称面 = `movie` / `shortdrama`，中文别名面为空），实测 `grep -rn "广告\|漫剧" core agents --include=*.py | grep -v __pycache__` ⇒ **0 命中**、`animated` ⇒ **0 命中**；结论写清"新形态名今天不被任何守卫覆盖 ⇒ 派生面是唯一出路"；③ 七处（`ops/`/`web/`/`dreaming/`）"不在扫描面且不得为过断言改写"逐条登记。

- [ ] T2105 [P] **复算版本哈希含实现文件字节这条落点约束** — 读 `agents/sound/evaluators/_versioning.py:12`（`implementation_version`）与 `:22-23`（`hasher.update(Path(caller_file).read_bytes())`）、`agents/dev/evaluators/_versioning.py:13` — **完成判据**: 结论写清"改一字节既有评估器**实现文件** ⇒ 改 `spec.version` ⇒ 改 `eval_breakdown` ⇒ 违反 FR-013"；并列出机制改动**允许**的落点表（新增文件 / 装配函数**函数体** / 配置段；包名哈希只取 `Path(__file__).parent.parent.name`，与 `__init__.py` 内容无关）——**该表必须区分三类别**：① `agents/*/evaluators/` 下的**单个评估器实现模块**（`loudness.py` / `*_evaluator.py` 一类：**零改动**）；② `agents/*/evaluators/__init__.py`（五个：**允许函数体替换**，T2120 的机制改动面）；③ `agents/*/evaluators/plugins.py`（六个**新增**文件：不参与任何既有版本哈希）。

- [ ] T2106 [P] **复算零 DB 变更与 cadence 现状** — 读 `ops/migrations/versions/`（当前迁移头）、`core/calibration/periods.py:30-31`、`core/calibration/config.py:149`（`period_days` 字段）与 `:161`（`from_dict`）、`core/calibration/drift_config.py:130-143`、`agents/promo/config.py:43` — **完成判据**: 两条结论成立——① 本特性**零 DB 变更、零迁移**（发现树 / `CostRecord` / `eval_breakdown` / 得分一律不动）；② cadence 取值域今天只有 `{1,7}`，既有夹具 `period_days` 取值只有 `1`/`7`（`tests/unit/test_calibration_config.py:51`）。

- [ ] T2107 [P] **复算 19 项"会变红的既有测试与夹具"在本仓的现状，并输出内联配置字典夹具的穷举清单** — 读 `specs/021-form-plugin-validation/research.md:577` 起决策 12 的 19 项表，逐处打开其点名的文件（含 `tests/unit/test_form_switch.py:421-429`/`:431-436`/`:438-441`/`:443-461`、`tests/unit/test_billing_core_purity.py:31`/`:34-35`/`:145`/`:210-224`/`:268-285`/`:355-362`、`tests/unit/test_dev_core_degraded_purity.py:28-29`/`:101`/`:163-179`、`tests/unit/test_sound_composite.py:98`/`:129`/`:244`、`tests/unit/test_dev_compare_adopt.py:758`、`tests/unit/test_calibration_config.py:124`、`tests/unit/test_billing_channels.py:52`、`tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`、`tests/unit/test_pilot_chain_seven.py:117-121`、`tests/conftest.py:2854`/`:3054-3079`/`:3196`） — **完成判据**: ① 19 项**逐项**给出"保留 / 委派 / 扩展 / 改口径"结论（对齐 T2181 的核销口径）；② **U-04：内联配置字典夹具清单必须是穷举的**——**穷举口径以契约为准**：**"凡在 `tests/**` 内调用六个 `build_*_evaluators` 的测试文件"**（**符号调用反查**，不是人工维护；契约的机检 6 用同一反查面）；再用逐份 `configs/*.yaml` 与调用面交叉核对（`grep -rln "evaluator_weights\|build_.*_evaluators" tests/ --include=*.py` 的实测输出 + 逐文件打开确认是否为"内联配置字典/精简配置文本"）——**实测该反查命中 16 个文件**（其中 `tests/conftest.py` 属登记点 ⑤）⇒ 产出一份**逐路径**清单（含 `tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、`tests/unbiasedness/**` 的每一份命中文件），**不得**以"等"字收尾；③ **`tests/adversarial/**` 是否受影响**必须给出明确结论（逐文件核对该目录是否构造内联配置字典；结论写成"受影响 ⇒ 逐处列出"或"不受影响 ⇒ 给出核对命令与实测输出"）；④ 该清单是 T2125 的**唯一输入**（T2125 逐处点名，不得新增清单外条目）。

- [ ] T2108 [P] **复算 quickstart 机制侧总账表的当前条数与去重纪律（**不写死条数**）** — 读 `specs/021-form-plugin-validation/quickstart.md` 的"机制侧总账"表（`specs/021-form-plugin-validation/quickstart.md` 的"机制侧总账"表（**按表头 `#/路径/kind/服务哪一项总账` 定位，不引行号**——该文件在并行修订中已重排），按表头"#/路径/kind/服务哪一项总账"定位）与 `specs/021-form-plugin-validation/contracts/onboarding-ops.md` 的 `MECHANISM_LEDGER_PATHS` 段 + C13 机检 4~6（**按符号/键名定位，不引行号**——该文件已在并行修订中重排） — **完成判据**: ① 数出该表**当前**的路径行数（写入结论，作为"修订前基线"）并核对去重纪律：`ops/form_guard.py`（②③）与 `tests/unit/test_form_switch.py`（②⑤）各**只算一条**；② 判定口径 = 契约已定的"**文档表与常量的集合相等**"——**条数由 `MECHANISM_LEDGER_PATHS` 给出**（**不写死数字**；契约明确"当前实测 **56 条**仅作对照、**不是判据**"）；③ **A1 的夹具同步面已并入总账 ①**，其**穷举口径 = "凡在 `tests/**` 内调用六个 `build_*_evaluators` 的测试文件"（由符号调用反查，不是人工维护）**——实测命中 **16 个文件**，其中 `tests/conftest.py` 属 ⑤ ⇒ 夹具同步面子表 **15 条**；**新增基线夹具** `tests/unit/fixtures/evaluator_assembly_baseline.json` 与 T2197 的两个新文件归属 ①；⇒ T2158/T2202 一律以**集合相等**为准，quickstart 表按 T2202 同步（**不再需要"上缴口径修订"**——契约口径已修订）；④ A4 **不单列第 7 项**（`core/calibration/config.py` → ①；`agents/pilot/pilot.py` 与 `tests/unit/test_form_clause_completeness.py` → ④）；`tests/unit/test_calibration_config.py` **不在**该表内。

- [ ] T2196 [P] [US2] **全仓形态枚举普查（只读；AST 任意容器字面量与装饰器实参）** — 落点：只读脚本 + 普查清单结论（**不写仓库权威文件**）；清单是 T2146 / T2198 / T2125 的输入 — **完成判据**: ① 用 AST 扫 `tests/**`、`ops/**`、`core/**`、`agents/**`（**含 `tests/adversarial/**`**），列出**任意** `ast.Tuple`/`List`/`Set`/`Dict`（**含函数体内**）与**装饰器实参**（如 `@pytest.mark.parametrize("form", ("movie","shortdrama"))`、`@pytest.mark.parametrize("config_path", (SHORTDRAMA, MOVIE))`）中含形态名集合字面量的代码点；② 每处给 `{path, line, 所属符号名, 容器种类（元组/列表/集合/字典/装饰器实参）, 处置（委派 / 逐形态声明期望值）}`；③ **命中面实测 ≥ 11 处**（本次只读普查定位到 **≥ 24 处 / 20 个文件**，含 `tests/unit/test_no_vendor_literals.py:43`/`:159`、`tests/unit/test_billing_gateway_cells.py:42`/`:305`、`tests/contract/test_transfer_contracts.py:328`、`tests/unit/test_calibration_transfer.py:534`/`:739`、`tests/contract/test_llm_profile_contracts.py:120`、`tests/contract/test_pilot_film_contracts.py:175`/`:278`、`tests/unit/test_dev_policy_loader.py:214`、`tests/unit/test_pilot_chain_seven.py:80`、`tests/unit/test_dev_core_degraded_purity.py:78`、`tests/unit/test_pilot_backend_selection.py:552`、`tests/unit/test_billing_peak_windows.py:264`、`tests/unit/test_billing_config.py:162`/`:196`、`tests/unit/test_smoke_llm_profile.py:176`，以及**已登记的 6 处** `tests/unit/test_form_switch.py:30`、`tests/unit/test_billing_core_purity.py:31`、`tests/unit/test_billing_channels.py:52`、`tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`、`tests/conftest.py:2854`）——**逐条以普查脚本的实际输出为准**（本清单的枚举是下限，不得据它裁剪）；④ **`tests/adversarial/**` 的结论**明确写出（原文/夹具是否有形态集合字面量；有 ⇒ 并入清单，无 ⇒ 给核对命令与输出）；⑤ **不得**改动任何文件（实验后 `git diff` 为空）；⑥ 清单交给 T2146（逐处改派生）与 T2198（反向扫描清零）。

**检查点**: ✅ 四条硬缺口、五处登记点、三副本与盲区、两处既有字面量 + 派生名称面命中数、版本哈希落点（三类别）、零 DB 变更、19 项现状 + **穷举**夹具清单、总账表当前条数与集合相等口径、**全仓形态枚举普查（≥ 11 处）** —— **九项事实基线**全部带符号名/行号复核；`tests/unit/test_form_switch.py` 基线 17 passed 与 quickstart 的已跑结论一致。**跨阶段前置（如实登记）**: T2105 的落点表（三类别）是 T2114~T2121 的施工边界；T2107 的**穷举**夹具清单是 T2125 的唯一输入；T2108 的集合相等口径是 T2158/T2200 的判据；T2196 的普查清单是 T2146/T2198 的输入。

---

## 阶段 2（A1）：插件声明面与唯一装配点（T2109~T2126）

**目标**: 让"新形态接入 = 仅新增配置 + 插件"在**机制上**成立——新增顶层段 `evaluators.plugins.<agent>.<slot>.<evaluator_id> = {impl, version, params}`，由**唯一装配点**用 `importlib` 解析并关键字注入；六个既有装配函数**函数体替换、签名与返回形状逐字不变**；**既有评估器实现文件零改动**、**既有参数不搬迁**（单一事实源）。

**独立测试**: `uv run pytest tests/unit/test_evaluator_plugin_assembly.py tests/contract/test_plugin_contracts.py -q`（本阶段完成时 T2109/T2110 转绿）；`uv run pytest tests/unit/test_sound_composite.py tests/unit/test_dev_compare_adopt.py -q` **保持绿**。

**⚠️ 关键（本阶段风险最高的两条）**: ① **T2111 的"改前装配序列快照"必须先于 T2120 落地**（顺序颠倒即无法证明"逐字不变"，原则一/二失守）；② **T2125 的夹具同步必须"扩展夹具"而不是"实现兜底"**——在实现里加"缺 `evaluators` 段即回落到硬编码装配"会留下**影子装配路径**（违反 FR-012）。

### 阶段 2 的测试任务（先写，确认失败）

- [ ] T2109 [P] [US1] **新增装配面单测（先写、预期红）** — 新增 `tests/unit/test_evaluator_plugin_assembly.py` — **完成判据**: 覆盖 `specs/021-form-plugin-validation/contracts/plugin-config.md` 的 C1/C2/C3 机检断言——① 声明形状与**叶子键恰好三键**（缺任一 / 出现第四键 ⇒ `PluginDeclarationError`）；② `impl` 的 `module:attr` 解析（`:` 数为 0 或 2、右侧属性不存在、非 callable ⇒ `PluginAssemblyError`）；③ 参数严格性 `required(impl) == set(params) | injected` 的**双向反例**（少一个 / 多一个各一条）与 `*args`/`**kwargs` ⇒ 报错；④ 三项一致性（`spec.evaluator_id` == 声明键、前缀↔kind、`spec.version` == 声明值）各一条反例；⑤ 集合 ↔ `evaluator_weights.<agent>` 键集**缺项/多项两个方向**均拒绝装配；⑥ **保序**（每槽位装配序列 == 声明序列）；⑦ 注入槽位越界（∉ `INJECTION_SLOTS`）⇒ 报错；⑧ **缺 `evaluators` 段即装配期报错**（常驻）；⑨ **目录里存在但未声明 ⇒ 装配期不可用**；⑩ `parse_manifest`/`assemble` 的签名逐字（`document, agent, *, slots` / `manifest, *, agent_config, gateway=None, artifacts=None, registry=None`）。跑 `uv run pytest tests/unit/test_evaluator_plugin_assembly.py -q` ⇒ **收集/运行期红**（`core/evaluators/plugin.py` 不存在 ⇒ `ImportError`），**不得**写成"跳过即绿"。

- [ ] T2110 [P] [US1] **新增插件契约测试（先写、预期红）** — 新增 `tests/contract/test_plugin_contracts.py` — **完成判据**: C1~C4 的可执行面——① "配置声明集 = 可用插件全集"（六 Agent 的声明并集逐字等于 `evaluator_weights.<agent>` 键集）；② **`assemble` 是 `impl` 的唯一解析点**（全仓 `importlib.import_module` 的**插件解析**命中点集合 == {`core/evaluators/plugin.py`}）；③ 纯关键字调用（AST：装配点的 `impl(...)` 调用**零位置实参**）；④ 同 id 同 version 在**同一注册中心**重复注册被拒（`core/evaluators/registry.py:35-38`）、非确定性插件注册被拒（`:29-33`，`human` 例外）；⑤ 必需元数据缺失（`evaluator_id`/`version`/`kind`/`deterministic`/`cost_per_call`）即报错（`core/evaluators/base.py:42`）；⑥ `version` 不允许覆盖实现（装配后 `spec.version` 三方相等，`core/evaluators/base.py:41` 的 `frozen=True` 未被反射改写）；⑦ 两形态 `evaluators` 段**逐字相同**（`movie["evaluators"] == shortdrama["evaluators"]`）；⑧ 插件业务无关的**文本 + AST 双层**机检（AST 层复用 `tests/unit/test_dev_core_degraded_purity.py:167-179` 的 import 扫描法）。跑该文件 ⇒ 红。

- [ ] T2111 [US1] **两形态装配序列对照机检（前置，必须先于 T2120 落地）** — 改 `tests/unit/test_evaluator_plugin_assembly.py`（同文件追加对照用例）+ 新增 `tests/unit/fixtures/evaluator_assembly_baseline.json` — **完成判据**: ① 快照含**六个 Agent × 两形态**的 `[(slot, evaluator_id@version), ...]`（**含顺序**），由**改造前**的硬编码装配导出（本用例在 T2120 之前即应跑绿 ⇒ 它就是"改前"的取证）；② 断言每 Agent 的 `all` == 按 `SLOT_LAYOUT` 顺序拼接各槽位；③ `sound` 的返回是**扁平列表**且下标顺序 == 声明顺序（先例 `tests/unit/test_sound_composite.py:129`）；④ T2120 落地后**再跑必须仍绿**（逐字相同 ⇒ 既有 `eval_breakdown` 与得分不受影响）；⑤ 该夹具文件（`tests/unit/fixtures/evaluator_assembly_baseline.json`）**并入机制侧总账**（T2158 的 ① 项，`kind == "new"`）。

### 阶段 2 的实现

- [ ] T2112 [US1] **新增唯一装配点 `core/evaluators/plugin.py`（业务无关）** — 新增 `core/evaluators/plugin.py` — **完成判据**: 三处定名逐字落地——`parse_manifest(document, agent, *, slots) -> PluginManifest`、`assemble(manifest, *, agent_config, gateway=None, artifacts=None, registry=None) -> dict[str, list[Evaluator]]`、`INJECTION_SLOTS = ("agent_config", "gateway", "artifacts", "registry")`；`importlib.import_module(<module>)` + 右侧**逐段 `getattr`**；**按签名 opt-in** 只传目标签名声明了的槽位；仅在提供 `registry` 时逐实例 `registry.register`（`core/evaluators/registry.py:18`）；**该模块文件内容零 Agent 名、零形态名、零形态分支**（`grep -nE "movie|shortdrama|animated|form ==" core/evaluators/plugin.py` ⇒ 0 命中；并断言模块不 import 任何 `agents.*`）；**零新增运行时依赖**（只许 stdlib 的 `importlib`/`inspect` + 既有依赖，T2197 常驻守住）；`uv run pytest tests/unit/test_evaluator_plugin_assembly.py -q` 由红转绿（夹具同步前的剩余失败必须在 T2125 收口）。

- [ ] T2113 [US1] **在 `core/evaluators/errors.py` 增两个"声明/装配期"错误类型** — 改 `core/evaluators/errors.py`（在 `:8` 的 `EvaluatorError` 之下） — **完成判据**: `PluginDeclarationError`（声明面：缺 `evaluators`/`plugins`/本 Agent 子键、叶子键缺/多、`impl` 非字符串或 `:` 数不为 1、`version` 非字符串、`params` 非映射）与 `PluginAssemblyError`（装配期：`impl` 不可解析/非 callable、参数严格性不符、三项一致性不符、槽位越界、集合与权重键集不匹配、`EvaluatorSpec`/注册校验失败）两类**均继承 `EvaluatorError`**；文案**必须点名声明路径 + 两侧实测值**（**禁止**只报"插件装配失败"这类无定位信息的文案）；`RegistrationError` 与 `ValidationError` 的既有语义与文案**不改写**。

- [ ] T2114 [P] [US1] **新增 `agents/visual/evaluators/plugins.py`（Agent 绑定薄工厂 + 槽位布局）** — 新增 `agents/visual/evaluators/plugins.py`；同步改 `agents/visual/loop.py` 的 `:138` 与 `:222` 调用点 — **完成判据**: `SLOT_LAYOUT = ("compliance", "proxies", "judge")`；一评估器一函数、**纯关键字签名**（既有参数从 `agent_config` 槽位读，**零拷贝**）；judge 类工厂声明 `gateway` 与 `artifacts` 两个槽位；`_judge_anchor_hashes` 从 `agents/visual/loop.py:103` **整体迁入**本文件（语义逐字、`_` 前缀 ⇒ 不计入"孤立插件"扫描面），`agents/visual/loop.py` 的两个调用点同步且语义不变；**既有评估器实现文件（`agents/visual/evaluators/` 下的单评估器模块）一律不碰**。

- [ ] T2115 [P] [US1] **新增 `agents/dev/evaluators/plugins.py`** — 新增 `agents/dev/evaluators/plugins.py` — **完成判据**: `SLOT_LAYOUT = ("gates", "proxies")`；薄工厂覆盖 dev 的 4 个评估器；对象型参数（`SimulatedSignalSource`，先例 `agents/dev/evaluators/__init__.py:47`）在工厂内经 `agent_config` 构造；`registry` 槽位**不在**该 Agent 的声明槽位面（由 `build_dev_evaluators` 的关键字符参承担，见 T2120）。

- [ ] T2116 [P] [US1] **新增 `agents/screenplay/evaluators/plugins.py`** — 新增 `agents/screenplay/evaluators/plugins.py` — **完成判据**: `SLOT_LAYOUT = ("gates", "proxies", "judge")`；覆盖 screenplay 的 7 个评估器；judge 类工厂声明 `gateway` 槽位；签名纯关键字。

- [ ] T2117 [P] [US1] **新增 `agents/storyboard/evaluators/plugins.py`** — 新增 `agents/storyboard/evaluators/plugins.py` — **完成判据**: `SLOT_LAYOUT = ("gates", "alignment", "judge")`（**注意槽位名 `alignment` 而非 `proxies`**）；覆盖 storyboard 的 5 个评估器；judge 类工厂声明 `gateway` 槽位。

- [ ] T2118 [P] [US1] **新增 `agents/sound/evaluators/plugins.py`（单槽 `all`）** — 新增 `agents/sound/evaluators/plugins.py` — **完成判据**: `SLOT_LAYOUT = ("all",)`（`sound` 返回**扁平列表**，其唯一可声明槽位名为 `all` —— 与五个 dict Agent 的派生汇总键 `all` **同名不同物**，判定由传入的 `slots` 入参区分、**无人工特例分支**）；覆盖 sound 的 4 个评估器（含 `config.loudness` / `config.av_sync_threshold_ms` / `config.asr["cer_cap"]` 三处既有参数经 `agent_config` 读取，先例 `agents/sound/evaluators/__init__.py:26-30`）。

- [ ] T2119 [P] [US1] **新增 `agents/editing/evaluators/plugins.py`** — 新增 `agents/editing/evaluators/plugins.py` — **完成判据**: `SLOT_LAYOUT = ("gates", "pacing", "judge")`；覆盖 editing 的 5 个评估器；judge 类工厂声明 `gateway` 槽位。

- [ ] T2120 [US1] **六个装配函数改委派（函数体替换，签名与返回形状不变）** — 改 `agents/visual/loop.py:207`、`agents/dev/evaluators/__init__.py:31`、`agents/screenplay/evaluators/__init__.py:40`、`agents/storyboard/evaluators/__init__.py:26`、`agents/sound/evaluators/__init__.py:24`、`agents/editing/evaluators/__init__.py:27` — **完成判据**: 六个返回形状**逐字不变**——`visual` ⇒ `{"compliance","proxies","judge","all"}`、`dev` ⇒ `{"gates","proxies","all"}`（**保留 `registry` 关键字符参**）、`screenplay` ⇒ `{"gates","proxies","judge","all"}`、`storyboard` ⇒ `{"gates","alignment","judge","all"}`、`sound` ⇒ **扁平列表**（保序）、`editing` ⇒ `{"gates","pacing","judge","all"}`；`all` 由装配点按 `SLOT_LAYOUT` 顺序**派生**（不可声明）；一一对应校验沿用既有中文文案（`agents/dev/evaluators/__init__.py:52-58`、`agents/screenplay/evaluators/__init__.py:64-71`）；**T2111 的对照用例仍绿**；`uv run pytest tests/unit/test_sound_composite.py tests/unit/test_dev_compare_adopt.py -q` 绿（`:758` 的"实现源码不含 `build_dev_evaluators`"断言原样成立）；**改动只落函数体**——五个 `agents/*/evaluators/__init__.py` 的 `kind` 在 ledger 中记为 **`modified`**（机制改动，**不是**实现文件）。

- [ ] T2121 [US1] **【独立任务】"既有评估器实现文件零改动"机检（范围 = 单个评估器实现模块）** — 落点：`tests/unit/test_evaluator_plugin_assembly.py`（常驻用例）+ `ops/form_onboarding.py` 的 `MECHANISM_LEDGER_PATHS`（T2158） — **完成判据**: ① 范围限定为 **`agents/*/evaluators/` 下的单个评估器实现模块**（`loudness.py`、`*_evaluator.py` 一类）：`MECHANISM_LEDGER_PATHS` 中此类条目数 **== 0**（**既无 `new` 也无 `modified`**，常驻断言）；② **明确排除** `agents/*/evaluators/__init__.py`（**五个，`kind == "modified"`**，属 T2120 的机制改动：装配函数体替换）与 `agents/*/evaluators/plugins.py`（**六个，`kind == "new"`**，允许项）——两类都**不**计入"实现文件零改动"的判定面（否则本断言必红）；③ 人工复核命令：以"机制落地前 ref"为基线跑 `git diff --name-status <ref> -- 'agents/*/evaluators/*.py'` ⇒ 输出**只**含 6 个 `plugins.py`（`A`）与 5 个 `__init__.py`（`M`），**零**单评估器实现模块（既无 `A`、`M` 也无 `D`）；④ 版本未变的举证由 T2111 的快照承担（`evaluator_id@version` 逐字相同 ⇒ 实现文件字节未变）；⑤ 反向机检：`agents/*/evaluators/plugins.py` **不得**出现在接入清单的 `M`（修改）行（T2167/T2173 的 `counts["既有模块被修改"] == 0` 覆盖）。

- [ ] T2122 [US1] **【独立任务】"既有参数不搬迁"机检（单一事实源）** — 落点：`tests/unit/test_evaluator_plugin_assembly.py`（常驻用例） — **完成判据**: ① 两形态配置 `evaluators` 段的 `params` **全为 `{}`**（遍历声明面断言 `params == {}`，零例外）；② `agents/<agent>/evaluators/plugins.py` 的薄工厂从 `agent_config` 槽位读既有参数（一评估器一函数），**零把既有取值拷进配置**；③ 单一事实源断言：`tests/unit/test_form_switch.py:166-169` 读的 dataclass 路径（`SoundConfig.from_yaml(...).av_sync_threshold_ms`）仍是唯一来源，该用例**一字不改且绿**；④ **反例**：若某 `params` 非空且键名与既有 `agents/<agent>/config.py` 的字段同义（同一阈值两处）⇒ 红（双事实源 ⇒ 漂移即假绿）。

- [ ] T2123 [US1] **各 `*Config` 新增 `plugin_declarations` 承载属性（缺段 ⇒ `None`、不补默认）** — 改六个 Agent 的配置类所在文件（`agents/visual/config.py`、`agents/dev/config.py`、`agents/screenplay/config.py`、`agents/storyboard/config.py`、`agents/sound/config.py`、`agents/editing/config.py`） — **完成判据**: 每个 `*Config` 新增 `plugin_declarations` = 文档中 `evaluators.plugins.<本 Agent>` 子树的**逐字拷贝**；缺 `evaluators` 段 / 缺 `plugins` / 缺本 Agent 子键 ⇒ 该属性为 `None`（**原样保留"缺失"事实、不补默认**）⇒ 由 `parse_manifest` 见到 `None` 即抛 `PluginDeclarationError`；该属性**不含段级 `note`**（C11 的 `evaluators` 段级标注键位于 `plugins` 子树**之上**，层级不同、不碰撞）。

- [ ] T2124 [US1] **两份既有配置新增 `evaluators` 段（两形态逐字相同，既有取值零改动）** — 改 `configs/movie.yaml`（`evaluator_weights` 段之侧，`:6`）与 `configs/shortdrama.yaml`（`:8`） — **完成判据**: 30 条 `version` 声明齐备（visual 5 / dev 4 / screenplay 7 / storyboard 5 / sound 4 / editing 5），`impl` 指向 T2114~T2119 的薄工厂、`params: {}`；`movie["evaluators"] == shortdrama["evaluators"]` **逐字相等**（常驻断言，保护既有差异集断言不被动）；**既有取值零改动**——`configs/movie.yaml:620` 的 `7`、`configs/shortdrama.yaml:658` 的 `14`、`configs/movie.yaml:621` 与 `configs/shortdrama.yaml:659` 的 `0` **逐字节不变**；`uv run pytest tests/unit/test_form_switch.py -q` ⇒ **17 passed 不减**且 `:341-379` 的差异集用例（**15 键**）一字不改；`uv run python ops/form_plugin.py sync-versions --check --config configs/movie.yaml`（T2159 落地后）⇒ 0（无差集）。**同文件串行**：本任务必须**先于** T2129（同改两份 `configs/*.yaml`）。

  **A1 批次登记（父代理裁决，2026-09-25）**: **"两形态 `evaluators` 段逐字相同"实测不成立**——30 条 `version` 有 17 条因**形态口径参数进了 `implementation_version` 哈希**而不同（`configs/movie.yaml:620` 的 7 与 `configs/shortdrama.yaml:658` 的 14 等）。**裁决**：① 接受 `version` 逐形态不同，`evaluators` **登记进两处顶层差异集**（**只增不减**，属扩展而非放宽）；② 同时加一条**更强的**断言——两形态的 `evaluators` **键集合（`(agent, slot, evaluator_id)` 三元组）必须逐字相同**（实测 15/15 相等），**只有 `version` 可因形态参数而异**，差异原因必须可指认；③ **被否决**：把形态参数从版本哈希里剔除以求"两形态字节相同"——参数变而版本不变会让版本不可证伪，违反原则一。④ 本阶段先完成，`MECHANISM_LEDGER_PATHS` 的条目数断言归 A5/T2158。

- [ ] T2125 [US1] **夹具同步：内联配置字典补 `evaluators` 段（本阶段最大风险）** — 改 `tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、`tests/unbiasedness/**`（以及 T2107 穷举清单里的每一处；`tests/adversarial/**` 按 T2107③ 的结论处置） — **完成判据**: ① **逐处点名**、无遗漏地补齐 `evaluators` 段（以 T2107② 的**穷举**清单为唯一输入；可从真实 `configs/movie.yaml` 抄或复用公共夹具构造）；② **实现里零"缺 `evaluators` 段即回落到硬编码装配"的兜底**（常驻断言：用一份删掉 `evaluators` 段的派生配置装配 ⇒ **报错**、退出码非 0；`grep -rnE "回落到硬编码|缺段.*兜底" core/evaluators agents` ⇒ 0 命中）；③ `uv run pytest tests/unit/test_sound_composite.py tests/unit/test_screenplay_composite.py tests/unit/test_storyboard_composite.py tests/unit/test_editing_composite.py tests/unit/test_dev_composite.py tests/unit/test_visual_composite.py -q` 绿。

- [ ] T2126 [US1] **阶段 2 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_evaluator_plugin_assembly.py tests/contract/test_plugin_contracts.py -q` **转绿**；② `uv run pytest tests/unit/test_sound_composite.py tests/unit/test_dev_compare_adopt.py tests/unit/test_form_switch.py -q` 绿且用例数不减（`test_form_switch.py` 基线 **17 passed**）；③ **T2111 的对照用例仍绿**（装配序列逐字相同）；④ 只读核对：`grep -nE "movie|shortdrama|animated|form ==" core/evaluators/plugin.py` ⇒ 0 命中；`uv run pytest tests/unit/test_evaluator_plugin_assembly.py -q -k "缺"` 全绿。

**检查点**: ✅ 声明面（`evaluators.plugins`）落地且**配置声明集 = 可用插件全集**；唯一装配点 `core/evaluators/plugin.py` 业务无关（零 Agent 名/零形态名/零分支）；六个装配函数**签名与返回形状不变**、装配序列**逐字相同**；**既有评估器实现文件零改动**（范围 = 单个评估器实现模块）与**既有参数不搬迁**两条独立机检就位；两形态 `evaluators` 段逐字相同 ⇒ 015 的差异集断言（**15 键**）**一字不改**。

---

## 阶段 3（A2）：扫描面补面、形态名派生、三副本收敛与裸词收敛（T2127~T2136）

**目标**: 把"零形态分支"从"两形态硬编码、按行号引用、清单写死、显式排除 `agents/pilot`"升级为"**由配置派生形态名、全覆盖 `core/` + `agents/`（含 `agents/pilot`）、按符号名锚定**"；三份副本**委派**收敛为单一实现（副本数 ⇒ 1）且**断言语义只增不减**；`agents/pilot/pilot.py:613` 的裸形态词**收敛、不开例外**。

**独立测试**: `uv run pytest tests/unit/test_form_guard.py -q`（本阶段完成时 T2127 转绿）；`uv run pytest tests/unit/test_form_switch.py tests/unit/test_billing_core_purity.py tests/unit/test_dev_core_degraded_purity.py -q` **全绿且用例数不减**。

### 阶段 3 的测试任务（先写，确认失败）

- [ ] T2127 [P] [US2] **新增守卫单测（先写、预期红）** — 新增 `tests/unit/test_form_guard.py` — **完成判据**: 覆盖 `specs/021-form-plugin-validation/contracts/zero-form-branch.md` 的 C5/C6/C7 机检断言——① 派生面（`declared_forms` 的 id 面 / `form_literals` 的名称面，**分两个函数、不得混用**）；② **派生失败四条各自报错**（缺 `form` 键 / `form` 非字符串 / 缺 `form_aliases` 键 / `form` 取值重复或别名与他形态 `form` 冲突）；③ 两层扫描（`literal_violations` / `branch_violations`）；④ **符号名锚点**（`FormHit` 五字段 `{path, symbol, line, hit, layer}`、`symbol` 非空、模块级为 `<module>`、**机检定位不依赖行号**——同一违规上下移动若干行仍由同一 `symbol` 定位）；⑤ **例外三条**（E1 谓词 / E2 = 扫描面定义 / E3 谓词硬条件"该行不含 `=`/`⇒`/`→`/分支模式"）与"**词边界是判定、例外是放行——两者不得互相顶替**"；⑥ **词边界判定**（ASCII 按 `(?<![0-9A-Za-z_])<name>(?![0-9A-Za-z_])`、中文按子串）与短 id 假阳性反例（`ad` 不得命中 `read`/`load`/`head`）；⑦ **有牙齿三类合成反例**（裸字面量写在 `core/` / `if form == "<forms[0]>"` 写在 `agents/pilot/backends.py` / docstring 里 `<form> ⇒ 30 s` 的取值绑定）；⑧ `agents/pilot/backends.py` ∈ `iter_sources("agents")`。跑该文件 ⇒ **红**（`ops/form_guard.py` 不存在 ⇒ `ImportError`），**不得**写成 skip。

### 阶段 3 的实现

- [ ] T2128 [US2] **新增单一实现 `ops/form_guard.py`（形态名派生 + 两层扫描；业务无关）** — 新增 `ops/form_guard.py` — **完成判据**: 七个对外定名逐字——`declared_forms(configs_dir)`（**id 面**：全部 `configs/*.yaml` 的 `form:` 取值，两两唯一、非空字符串）、`form_literals(configs_dir)`（**名称面** = id 面 ∪ 全部 `form_aliases` 项）、`form_branch_patterns()`（形态无关的语法模式，六条字面不变）、`iter_sources(roots=("core","agents"))`（**含 `agents/pilot`**、排除 `__pycache__`）、`literal_violations(...)`、`branch_violations(...)`、`classify_exception(hit)`（返回 `"E1"|"E2"|"E3"|None`）；符号名由 AST 求所属 `FunctionDef`/`ClassDef`；**覆盖面完整性机检**：`iter_sources("core") + iter_sources("agents")` 的路径集合 == 两根下 `.py` 全集（集合差为空，"零排除"本身可机检）；**该模块自身零人工形态常量**（合成反例由派生值构造）；**零新增运行时依赖**（T2197 常驻守住）；`uv run pytest tests/unit/test_form_guard.py -q` 由红转绿。

- [ ] T2129 [US2] **两份既有配置新增 `form_aliases` 键 + "文件名 stem == `form:` 取值"常驻机检** — 改 `configs/movie.yaml`（顶层，与 `:4` 的 `form:` 同级）与 `configs/shortdrama.yaml`（与 `:6` 同级） — **完成判据**: 两形态各新增顶层 `form_aliases`（**允许显式空列表 `[]`，缺键即报错**）；逐份断言 `configs/<id>.yaml` 的 **stem == 该配置的 `form:` 取值**（`movie` / `shortdrama` 实测成立，不一致即报错 ⇒ 登记点 ⑤ 的零人工常量反查前提）；跨文件的**全部名称**（id 面 ∪ 别名面）两两唯一；`form_aliases` 是**两形态一致的新增键** ⇒ 段集合仍相等（`tests/unit/test_config_integrity.py:122-126` 保持绿）、差异集**不含它**（`tests/unit/test_form_switch.py:341-379`（**15 键**）与 `tests/contract/test_pilot_contracts.py:434-453`（**15 键**）一字不改）。**同文件串行**：本任务必须在 T2124 之后（同改两份 `configs/*.yaml`）。

- [ ] T2130 [US2] **扫描面补面：删去 `tests/unit/test_form_switch.py:423` 的 `agents/pilot` 排除** — 改 `tests/unit/test_form_switch.py`（`:423`） — **完成判据**: 该排除行**删去**（补面方向是**变严**，不是"为过断言改守卫"）；`:421-429` 的循环体与断言语句**原位保留**、失败信息形态（`f"{path} 不得出现形态字面量：{banned}"`）不变；**注入即红**：把**由派生值构造**的形态字面量写进 `agents/pilot/backends.py` ⇒ `Test零形态分支静态断言.test_core_与_agents_无形态字面量` **必须变红**（SC-003 的举证）；**禁止**以文件白名单、`if path.name == "backends.py": continue`、`# noqa` 等变相恢复排除（新增断言的红同样适用）。

- [ ] T2131 [US2] **三副本委派收敛（单一实现、断言语义保留原位）** — 改 `tests/unit/test_form_switch.py:413`/`:414`、`tests/unit/test_billing_core_purity.py:34-35`、`tests/unit/test_dev_core_degraded_purity.py:28-29` — **完成判据**: 三处的形态名常量改为**从 `ops/form_guard.py` 取**（`form_literals` / `form_branch_patterns`）；**循环体与断言体原位保留**——`tests/unit/test_form_switch.py:429`/`:436` 的失败信息、`tests/unit/test_billing_core_purity.py:145` 与 `tests/unit/test_dev_core_degraded_purity.py:101` 的用例体、两处的"有牙齿"自检（`tests/unit/test_billing_core_purity.py:210-224`）**逐字不动**；**副本数（形态名清单的定义点数）恒为 1**，判定口径 = T2198 的**扩展反向扫描**（**任意 `ast.Tuple`/`List`/`Set`/`Dict`（含函数体内）与装饰器实参**含派生名称面任一字符串 ⇒ 命中数 **0**；**只看模块级常量会空跑假绿**，故必须以 T2198 的口径为准；三处是**委派点**不计入）；派生面**严格覆盖**原常量表（`shortdrama` / `"movie"` / `'movie'` 三者仍全部判违规）；三处**各自**注入 ⇒ 各自变红。

- [ ] T2132 [US2] **`tests/contract/test_pilot_contracts.py:469-477` 的写死禁用元组改派生** — 改 `tests/contract/test_pilot_contracts.py`（`:474` 的元组 / `:468-477` 的循环） — **完成判据**: 禁用清单改由 `form_literals()` / `form_branch_patterns()` 派生；该处的禁用面与 `tests/unit/test_form_switch.py:421`/`:431` 的禁用面**逐字相等**（消除"两处扫描面口径分叉"这一既有隐患）；`:434-453` 的固定 **15 键**差异集断言**一字不改**；`:469` 的 `for root in ("core", "agents")` 全覆盖循环**仍在**（该处**本就覆盖 `agents/pilot`**，与补面后口径必然一致）。

- [ ] T2133 [US2] **`agents/pilot/pilot.py:613` 的裸形态词收敛（不得加例外）** — 改 `agents/pilot/pilot.py`（`_require_duration_consistency` 的 docstring，`:604` 起，命中行 `:613`） — **完成判据**: 改为中性措辞——形如"形态原值：`screenplay.target_duration_min × 60 == editing.target_duration_s`（形态值只作参数透传，不绑定具体形态的取值）"；**保留**其后"任一不一致即拒绝启动并**点名两处实测值**（不静默择一、不按其一取值）"的既有语义；`_require_duration_consistency` 的**函数体逻辑零改动**（`:620-625` 与 `:629` 起的两处比较、`DURATION_TOLERANCE_S`（`:57`）逐字不变）；**不得**为它开 E3 例外（**禁止**把 E3 放宽到"该行含 `⇒` 也算中性描述"）；收敛后 `agents/pilot/pilot.py` 在派生名称面下**违规数 == 0**（含 docstring 与注释）。**同文件串行**：本任务必须先于 T2142 与 T2152（三者同改 `agents/pilot/pilot.py`）。

- [ ] T2134 [US2] **例外三条的机检边界落地与既有命中处置（E1 恰好一处、E3 零处）** — 落点：`ops/form_guard.py` 的 `classify_exception`（T2128）+ 常驻用例落在 `tests/unit/test_form_guard.py` — **完成判据**: ① `classify_exception(hit) == "E1"` 的命中点集合**恰好一处** == {(`core/deployment/evidence.py`, `_unbiasedness_result`（`:90`）, `:97`, `source="configs/movie.yaml deployment.gate.unbiasedness=false"` 一族)}，**该行不得改写**（改写即改变既有证据的来源标注语义）；② `classify_exception(hit) == "E3"` 的命中点数 == **0**（T2133 收敛后）；③ E1 判定不被滥用（把形态名写进普通字符串如 `FORM_LABEL = "movie"` **不得**判为 E1）；④ `ops/`/`web/`/`dreaming/` 的既有配置路径默认值与演示形态值（`ops/billing.py:97`、`web/server.py:320`、`web/export.py:301`、`dreaming/deploy_hook.py:25`、`ops/ingest_metrics.py:121`、`ops/demo_merged_pool.py:55`、`ops/smoke_llm.py:358`）**不在扫描面内、相关行逐字不变**；⑤ 新增任一 E1 命中点 ⇒ 红（例外面冻结，须走契约修订）。

- [ ] T2135 [US2] **"有牙齿"自检常驻（三类合成反例 + 三处委派点各自变红）** — 落点：`tests/unit/test_form_guard.py` — **完成判据**: ① 三类反例**逐条断言被判违规**——(a) 裸字面量写在 `core/` 某文件、(b) `if form == "<forms[0]>"` 写在 `agents/pilot/backends.py`、(c) docstring 里 `<form> ⇒ 30 s` 的取值绑定（违反 E3）；② **合成字面量全部由派生值构造** ⇒ 守卫模块与测试文件自身**零人工形态常量**（按 T2198 的**扩展反向扫描**口径判定，命中数 0）；③ 三处委派点（`tests/unit/test_form_switch.py` 的字面量与分支两个用例、`tests/unit/test_billing_core_purity.py` 与 `tests/unit/test_dev_core_degraded_purity.py` 的同名断言）**各自**注入 ⇒ **各自变红**；④ 派生面失效取证：临时移走某配置的 `form` 键 / `form_aliases` 键 ⇒ 派生**报错**（不静默跳过、不静默全绿）。

- [ ] T2136 [US2] **阶段 3 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_guard.py -q` **转绿**；② `uv run pytest tests/unit/test_form_switch.py tests/unit/test_billing_core_purity.py tests/unit/test_dev_core_degraded_purity.py -q` 绿且**用例数不减**（`test_form_switch.py` 基线 17 passed）；③ 只读核对：`agents/pilot/pilot.py` 在派生名称面下违规数 0、`core/deployment/evidence.py:97` 为唯一 E1 命中；④ 只读核对覆盖面：`iter_sources` 的路径集合 == 两根 `.py` 全集（差集为空）；⑤ `uv run pytest "tests/contract/test_pilot_contracts.py::TestC10到C13试水运行::test_c13_两套配置差异可归因且无形态分支" -q` 绿。

**检查点**: ✅ 扫描面**含 `agents/pilot`**（装配点在扫描集合内且注入即红）；形态名**由 `configs/*.yaml` 派生**（零人工常量清单、派生失败即报错）；**三副本委派收敛，副本数 == 1**（判定口径 = T2198 的扩展反向扫描）且断言语义**只增不减**；锚点按**符号名**（抗行号漂移）；例外**只有三条**（E1 恰好一处、E3 零处）；`agents/pilot/pilot.py:613` 裸形态词**收敛率 100% 且零例外**。

---

## 阶段 4（A3）：五处登记点改配置/形态派生 + 登记完备口径 + 枚举普查清零（T2137~T2147 + T2198）

**目标**: 把"新形态必须走既有全部五处登记点"从**叙述**变成**可机检的逐一判定**：①②③⑤ 改由 `declared_forms()` 派生（④ 保住按配置路径通用的性质并追加 020 口径机检）；`tests/unit/test_form_switch.py:438-441` 的"恰好两份"升级为**登记完备**三条并列（**禁止删除**）；**不新造第六处**；并把**全仓形态枚举**（T2196 普查，实测 ≥ 11 处）逐处改派生、由**扩展反向扫描**清零。

**独立测试**: `uv run pytest tests/unit/test_form_registration.py -q`（本阶段完成时 T2137 转绿）；`uv run pytest tests/unit/test_form_switch.py tests/unit/test_config_integrity.py -q` 绿且**用例数不减**（`test_config_integrity.py` 基线 125 passed）。

**⚠️ 关键**: 五处改动**每一处的断言体与循环体都保留原位**，只换常量来源（**委派**）并**新增**更严格断言；**不得**以删除/放宽换取通过；**委派证明**（AST：形态集合表达式必须是**调用**而非字面量元组）是"新形态静默逃逸"的根因守卫。

### 阶段 4 的测试任务（先写，确认失败）

- [ ] T2137 [P] [US3] **新增登记点完备性测试（先写、预期红）** — 新增 `tests/unit/test_form_registration.py` — **完成判据**: 覆盖 `specs/021-form-plugin-validation/contracts/form-registration.md` 的 C9/C10——① 对 `declared_forms()` 的**每一个**取值，五处**逐一**判定"已登记 + 已委派"，缺项**点名是哪一处、缺哪个形态**（不得只报总数）；② 登记完备三条（两两唯一 ∧ `declared_forms() ⊆ registered_forms(site)` **双向** ∧ 配置数 **≥2**）；③ **不新造第六处**（`REGISTRATION_SITES` 恰好五处 + 反向扫描集合 == `form_set` 面）；④ **故意越界取证**："一份临时第三形态配置 + 一处人工枚举"⇒ **② 必红**；"两份临时配置 `form:` 取值相同"⇒ **① 必红**；"`configs/` 指向空目录"⇒ **③ 必红**；⑤ **委派证明**：每处形态集合表达式**不得**是 `ast.Tuple`/`ast.List` 且元素全为 `ast.Constant`（AST 判定）；⑥ **id 面/名称面不混用**（C10 的用例与实现面引用 `form_literals` 的次数 == 0）；⑦ **反向扫描的有牙齿自检**（与 T2198 同口径）：注入一处**函数体内**元组（如 `for form in ("movie", "shortdrama"):`）与一处**装饰器实参**（如 `@pytest.mark.parametrize("form", ("movie", "shortdrama"))`）⇒ 各自**必红**。跑 ⇒ 红。

### 阶段 4 的实现

- [ ] T2138 [US2] **① `tests/unit/test_form_switch.py` 的 `FORMS`/`_pair` 改派生 + 新增"逐形态对"断言** — 改 `tests/unit/test_form_switch.py`（`FORMS` 在 `:30`、`_pair` 在 `:144`） — **完成判据**: `FORMS` 与 `_pair` 由 `declared_forms()` 驱动，且**赋值表达式是调用而非字面量元组**（AST 判定）；**保留** `:341-379` 的固定差异集断言（`Test差异逐项可归因.test_全量差异都被配置文件承载`，**15 键**）**原位一字不改**（口径澄清 B：两形态 `evaluators` 段逐字相同 ⇒ 差异集不变）；**新增**"逐形态对"常驻断言——每一对形态的顶层差异集**非空**、必含 `form`、必**不含** `web` / `cost_regression`；`:161` 的 `set(movie_w) == set(short_w)` 保持绿。

- [ ] T2139 [US2] **①′ `tests/unit/test_form_switch.py:438-441` 升级为"登记完备"三条并列（禁止删除）** — 改 `tests/unit/test_form_switch.py`（`Test零形态分支静态断言.test_形态切换只经配置文件`，`:438`，断言在 `:441`） — **完成判据**: 三条并列——① 每份 `configs/*.yaml` 的 `form:` 取值**两两唯一**（唯一数 == 配置文件数）；② `declared_forms() ⊆ registered_forms(site)` **双向相等**；③ 配置数 **≥ 2**（**下界保留**，**不得**提到 3——那会把"机制可用"与"本次接入了几个形态"耦合，违反 FR-013 的机制/接入分离）；**该用例不得删除**（AST 断言该 `FunctionDef` **仍存在**且函数体内含三条并列断言，"删除次数恒 0"）；原断言的**原意**（形态以配置文件为唯一载体、代码侧无形态枚举/映射表）由 ①+②+反向扫描（T2145 / T2198）**共同**承载：**强度只增不减**；本用例引用 `form_literals` 的次数 == 0（id 面/名称面不混用）。

- [ ] T2140 [US2] **② `tests/unit/test_config_integrity.py` 配置集合改派生 + 新增清单解析器与必需键条目** — 改 `tests/unit/test_config_integrity.py`（`SHORTDRAMA`/`MOVIE` 在 `:19-20`、`CONFIG_CLASSES` 在 `:23-40`、`REQUIRED_PATHS` 在 `:48-98`、参数化面在 `:153-154`） — **完成判据**: `SHORTDRAMA`/`MOVIE` 两常量**保留**（既有符号不删，作为对照面）；参数化面（`:153-154`）改由 `declared_forms()` 驱动 ⇒ **每份 `configs/*.yaml` 都跑全部加载器与全部"缺项即红"条目**；`CONFIG_CLASSES` **新增** `evaluators` 段的清单解析器条目（`core/evaluators/plugin.py` 的 `parse_manifest`）；`REQUIRED_PATHS` **新增** `evaluators.plugins.*.*.*.impl` / `.version` / `.params` 与"`<evaluator_id>` 键集 == `evaluator_weights.<agent>` 键集"条目；`:118`、`:122-126`、`:144-150`、`:174-178` 的原位断言**仍存在**；`uv run pytest tests/unit/test_config_integrity.py -q` 绿且**用例数 ≥ 125**。

- [ ] T2141 [US2] **③ `tests/contract/test_pilot_contracts.py` 差异集原样保留 + 新增逐对断言** — 改 `tests/contract/test_pilot_contracts.py`（`test_c13_两套配置差异可归因且无形态分支`，`:418`） — **完成判据**: `:434-453` 的固定 **15 键**集合**逐字未改**（015 的核心证据不得弱化）；**新增**逐对形态断言（口径与 T2138 的逐对断言**逐字一致**：非空 ∧ 必含 `form` ∧ 必不含 `web`/`cost_regression`）；`:468-477` 的禁用面与 T2131/T2132 的派生面**同源**；该文件用例数**不减**。

- [ ] T2142 [US3] **④ `agents/pilot/pilot.py` 新增 `form_clause_completeness` 并由 `config_completeness` 收口** — 改 `agents/pilot/pilot.py`（`config_completeness` 在 `:377`，调用点 `:507`；新增同模块函数） — **完成判据**: **保住通用性**（按**配置路径**通用，对新形态无需改代码即生效）；新增 `form_clause_completeness(config_path)` 读**原始文档**（不经模型），逐项机检——`calibration.period_days ∈ {1, 7}`、`calibration.window_semantics` 取值域单元素、`calibration.window_semantics_change_date` 非空、`budget.channels.<id>.tiers` 非空且**档位不跨渠道串用**、`promo.attribution_date_required_since` 存在、`calibration.transfer` **六键**齐备、**"不适用"必须显式声明**（留空/省略即报错）；**必须把 `evaluators` 段清单解析器加进预检清单**（删掉该段 ⇒ **拒绝启动**，"漏声明插件清单"不得静默逃逸）；逐项删掉一个 020 口径键 ⇒ `PrecheckError` 且**点名段与键**；**禁止**给该链路加"缺段即回落到码内默认"的兜底、**禁止**按形态名分支决定要不要检查。**同文件串行**：T2133 → T2142 → T2152。

- [ ] T2143 [US3] **④′ `config_completeness` 返回段清单变长的既有用例按扩展更新** — 改 `tests/unit/test_pilot_chain_seven.py`（`Test清单同步不变量` 在 `:88`，用例 `:117-121`） — **完成判据**: 返回项**新增** `form_clauses` 与 `evaluators` 两项，既有项（`dev`/`pilot`/`screenplay`/`storyboard`/`visual`/`sound`/`editing`/`promo`/`pooling`/`dreaming`/`calibration`/`transfer`/`drift`/`deployment`/`budget`/`web` 与 `weights:<段>`）**一个不少**；断言按**扩展**更新（不删既有项、不放宽）；**注意**：该文件的函数体内形态枚举（`:80` 的 `for form in ("movie", "shortdrama"):`）须按 T2146/T2198 一并委派（普查清单条目之一）。

- [ ] T2144 [US2] **⑤ `tests/conftest.py` 的 `PILOT_FORMS` 与 `pilot_form_config_path` 改派生** — 改 `tests/conftest.py`（`PILOT_FORMS` 在 `:2854`、`pilot_form_config_path` 在 `:3054-3079`、`_MINIMAL_MOVIE_CONFIG` 在 `:3196`） — **完成判据**: `PILOT_FORMS` 改由 `declared_forms()` 驱动（AST：赋值表达式是**调用**）；`pilot_form_config_path(form)` 对**任意已声明形态**返回**该形态真实配置的派生副本**（只改 `budget.ledger.root` 一行；`:3073` 的派生点断言 `assert "root: billing" in source` **保留**）；`:3072` 对**未声明**形态仍 `raise ValueError`（派生面之外的形态就是未知形态）；`movie` 分支**继续**用 `_MINIMAL_MOVIE_CONFIG`（其"精简副本"职责保留——它是形态**无关性**的举证面，不属形态枚举）；**反例**：让新形态返回 movie 的精简副本 ⇒ 红（用例在**错误前提**下通过 = 假绿，比变红危险得多）。

- [ ] T2145 [US3] **⑥ `ops/form_onboarding.py` 落地 `REGISTRATION_SITES` + 反向扫描（不新造第六处）** — 新增 `ops/form_onboarding.py`（本任务只落登记点面；清单机制在 T2157 追加） — **完成判据**: `REGISTRATION_SITES` 是**常驻白名单、恰好五处**，每项含 `{path, symbol, kind}`（`kind ∈ {"form_set", "clause_list"}`，①③⑤ 与 ② 的配置参数化面为 `form_set`、④ 为 `clause_list`）；`site_registration_status(site)` 逐处判定"已登记 + **已委派**"（含 T2137⑤ 的 AST 委派证明）；`sixth_site_scan()` **反向扫描**"形态清单被枚举/写死"的代码点（字面量元组/列表形态的形态集合常量、"形态 → 配置路径"映射、`configs/{form}.yaml` 硬编码拼装的模块级常量），断言其集合 == 白名单的 `form_set` 面（**新增一处即红，须显式登记**）；**扫描口径必须是 T2198 的扩展口径**（任意容器字面量 + 装饰器实参，**含函数体内**）；纪律与 `tests/unit/test_billing_core_purity.py:268-285` 的 `OFFLINE_ASSEMBLIES` 常驻清单同款。

- [ ] T2146 [US3] **同族"两形态枚举"副本逐处改派生（连带面，不是第六处登记点）** — 改 `tests/unit/test_billing_core_purity.py:31`（`FORMS`）、`tests/unit/test_billing_channels.py:52`、`tests/contract/test_billing_contracts.py:97`、`tests/unit/test_pilot_rehearsal.py:34`、**`tests/unit/test_no_vendor_literals.py:43`/`:159`（G-02：函数体内元组硬编码 `("movie","shortdrama")`，**不属**规格点名的任何同族副本 ⇒ **纳入委派面**）**，以及 **T2196 普查清单中的每一处**（**D-01：不得只覆盖已登记的 6 处**；普查实测 ≥ 11 处，含 `tests/unit/test_billing_gateway_cells.py:42`/`:305`、`tests/contract/test_transfer_contracts.py:328`、`tests/unit/test_calibration_transfer.py:534`/`:739`、`tests/contract/test_llm_profile_contracts.py:120`、`tests/contract/test_pilot_film_contracts.py:175`/`:278`、`tests/unit/test_dev_policy_loader.py:214`、`tests/unit/test_pilot_chain_seven.py:80`、`tests/unit/test_dev_core_degraded_purity.py:78`、`tests/unit/test_pilot_backend_selection.py:552`、`tests/unit/test_billing_peak_windows.py:264`、`tests/unit/test_billing_config.py:162`/`:196`、`tests/unit/test_smoke_llm_profile.py:176`） — **完成判据**: ① 普查清单中每一处逐处改由 `declared_forms()` 派生（AST：赋值/遍历表达式是**调用**）；② **断言体与循环体不删、不放宽**；③ **形态特定取值假设的处置（裁决已定，**A-06**）**：`tests/unit/test_pilot_rehearsal.py` 的"排练档与形态原值对照"等期望值**一律写入形态配置**（新增键或既有段内键，**逐形态声明**），断言**从配置读取**；**被否决方案（登记在案，不得采用）**："建'形态 → 期望值'的映射登记表"——它与 C7 的"形态名常量表副本数恒 1"与 C9.7 的"禁止'形态 → 配置路径'映射常量"直接冲突（等于把枚举换个形状复活）；④ 每处改后新形态**自动**进入其遍历面（静默逃逸消除）；⑤ 若某处**确实不能委派**（语义上必须锁定形态集合）⇒ 必须**上缴裁决**，**不得**自行留下豁免（T2198 的白名单为空）。

- [ ] T2147 [US3] **阶段 4 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_registration.py tests/unit/test_form_switch.py tests/unit/test_config_integrity.py -q` **转绿**且用例数不减（基线 17 / 125）；② `uv run pytest tests/unit/test_pilot_chain_seven.py tests/unit/test_pilot_rehearsal.py tests/unit/test_billing_channels.py tests/unit/test_billing_core_purity.py tests/unit/test_no_vendor_literals.py -q` 绿；③ `uv run pytest "tests/contract/test_pilot_contracts.py::TestC10到C13试水运行::test_c13_两套配置差异可归因且无形态分支" -q` 绿；④ 只读核对：`len(declared_forms()) == 2`（配置仍两份）且三处登记点的形态集合与 `declared_forms()` **双向相等**；⑤ 只读核对：`:438` 的用例**仍在**且含三条并列断言；⑥ 只读核对：T2196 普查清单中**已处置**的条目数 == 清单总条目数（逐条对号，未处置项必须为零）。

- [ ] T2198 [US3] **反向扫描面扩展（AST 任意容器字面量/装饰器实参）+ 全仓形态枚举命中数清零** — 改 `ops/form_onboarding.py`（`sixth_site_scan` 扩口径 + 新增 `form_enum_sites()`）+ 改 T2146 点名的各处 + 常驻用例在 `tests/unit/test_form_registration.py` — **完成判据**: ① **扫描口径**：`form_enum_sites()` 扫 `tests/**`、`ops/**`、`core/**`、`agents/**` 的**任意** `ast.Tuple`/`List`/`Set`/`Dict`（**含函数体内**）与**装饰器实参**（如 `@pytest.mark.parametrize("form", ("movie","shortdrama"))`），命中条件 = 容器内出现 `form_literals()` 的任一条目（字符串常量）；**只看模块级常量 ⇒ 空跑假绿**，本口径必须覆盖函数体内与装饰器实参；② **命中数恒 0** 是常驻断言（`form_enum_sites() == ()`）；③ 已知 ≥5 处**必须先清零**（`tests/unit/test_billing_gateway_cells.py:305`、`tests/contract/test_transfer_contracts.py:328`、`tests/unit/test_calibration_transfer.py:739`、`tests/unit/test_no_vendor_literals.py:43`/`:159`；其余按 T2196 普查清单）；④ **白名单/豁免为空**——确需豁免必须**上缴裁决并显式登记**（本清单**不预设豁免**）；⑤ **有牙齿**：注入一处函数体内元组与一处装饰器实参 ⇒ **必红**（常驻自检）；⑥ 该口径与 T2131 的"副本数恒 1"判定**同一实现**（不各写一份）。

**检查点**: ✅ 五处登记点**逐一可机检**（①②③⑤ 派生、④ 保通用性并加 020 口径机检）；**委派证明**（AST 判定"调用而非字面量"）常驻；"恰好两份"升级为**登记完备**（三条并列 + 下界 ≥2 + **未删除**）；**不新造第六处**（白名单恰好五处 + 反向扫描）；**全仓形态枚举**（普查 ≥ 11 处）逐处改派生、由**扩展口径**的反向扫描清零（命中数恒 0）；新形态在五处与全部副本面**不再静默逃逸、不再硬失败**。

---

## 阶段 5（A4）：020 口径声明完备与 cadence 显式报错（T2148~T2154）

**目标**: 把"新形态必须逐项声明 020 的全部新增口径"落成可机检的**声明完备性**，并把 cadence 取值域收口到唯一加载入口；**"不适用"必须显式声明而非留空**；**不扩量纲**（`core/calibration/periods.py:30` 一字不改）。

**独立测试**: `uv run pytest tests/unit/test_form_clause_completeness.py -q`（本阶段完成时 T2148 转绿）；`uv run pytest tests/unit/test_calibration_config.py -q` 保持绿。

### 阶段 5 的测试任务（先写，确认失败）

- [ ] T2148 [P] [US3] **新增 020 口径完备性测试（先写、预期红）** — 新增 `tests/unit/test_form_clause_completeness.py` — **完成判据**: 覆盖 `specs/021-form-plugin-validation/contracts/form-registration.md` 的 C11——① 七项口径逐项在形态配置中声明（cadence / 窗口口径与生效日 / 渠道命名空间 / 归属日生效日 / 迁移六键 / 运行窗口下限与断档容差），缺任一项 ⇒ `config_completeness` **拒绝启动**并**逐条点名**（段名 + 键路径）；② **cadence 越界即显式报错**：注入 `calibration.period_days: 14`（或 `0` / `2` / `"7"`）⇒ `CalibrationConfig.from_dict` 报错、文案**点名取值域 `(1, 7)`**、**不回落**到日级/周级；③ **"不适用"显式声明**：留空/省略次数恒 0；判为适用却在 `not_applicable` 里声明不适用 ⇒ 报错；在 `budget` / `calibration.transfer` 之外出现 `not_applicable` ⇒ 报错；④ **`calibration.cadence_note`**：未标定形态（`pilot.rehearsal.status == "unstandardized"`）**必填**且**必须同时含**「近似」与「未标定」两处字样；非未标定形态给出时**不得**含「近似」；该键**不得**出现「已标定」「已达标」「已投产」（出现即红）；⑤ **既有两形态取值零改动**（`configs/movie.yaml:620` 的 `7` / `configs/shortdrama.yaml:658` 的 `14` / `:621`、`:659` 的 `0` 逐字节不变）。跑 ⇒ 红。

### 阶段 5 的实现

- [ ] T2149 [US3] **cadence 收口：`CalibrationConfig.from_dict` 增一道同源校验** — 改 `core/calibration/config.py`（`CalibrationConfig.from_dict` 在 `:161`；字段区 `:149`） — **完成判据**: 取值域取自 `core/calibration/periods.py:30` 的 `SUPPORTED_CADENCES`（**`periods.py` 一字不改**，常驻断言：读原文件比对 `SUPPORTED_CADENCES == (1, 7)`）；错误文案**点名取值域 `(1, 7)`**；既有 `core/calibration/drift_config.py:130-143` 与 `agents/promo/config.py:43` 两处同取值域校验**原样保留**（同一口径的多个检查点，不是三套口径）；`uv run pytest tests/unit/test_calibration_config.py -q` 保持绿（`:124` 的"缺项即红"参数化**不动**）。

- [ ] T2150 [US3] **"不适用"的显式声明面（只允许两处、闭合取值域）** — 落点：`agents/pilot/pilot.py` 的 `form_clause_completeness`（T2142）+ 常驻用例在 `tests/unit/test_form_clause_completeness.py` — **完成判据**: `not_applicable` 是**段内键（映射）**，键 = 该段内的**相对键路径**、值 = **非空字符串理由且必须含「不适用」二字**（纯空白 / `null` / 空列表 ⇒ 报错；留空/省略 ⇒ 报错）；**只允许两处**——`calibration.transfer.not_applicable`（相对键取值域 `{source_forms, target_forms}`）与 `budget.not_applicable`（相对键取值域 `{channels}`），其它段出现即报错（防用"不适用"逃避填值）；**双向无歧义**：判为适用的键**不得**出现在 `not_applicable`，判为不适用的键**不得**再给出取值（防双事实源）；与 C1 的层级区分（`evaluators` 段级标注键位于 `plugins` 子树**之上**，不参与插件清单解析）。

- [ ] T2151 [US3] **迁移口径联动机检（空声明 = 沉默失效 ⇒ 报错）** — 落点：`agents/pilot/pilot.py` 的 `form_clause_completeness` + 常驻用例 — **完成判据**: 读**原始文档**（不经模型）断言——`calibration.transfer` 六键（`basis` / `source_forms` / `target_forms` / `conditions` / `storage` / `adoption`）齐备；若某形态 `source_forms ∪ target_forms ⊆ {该形态自身}`（"既不作为来源、也不作为目标互通"）则**必须**在 `calibration.transfer.not_applicable` 给出 `source_forms` / `target_forms` 的**非空理由**，否则报错；依据 `core/calibration/transfer.py:456` 的 `_registered_ids`、`:497-505` 的未声明形态显式拒绝、`:289-298` 的 `_c_evaluator_registered`（要求目标形态登记同 id 同 version）——**空声明 = 沉默失效**（结论既不能迁出、也接不进任何目标形态）。

- [ ] T2152 [US3] **三层"未标定"标注的机检落点（复用既有词汇，不发明）** — 落点：`agents/pilot/pilot.py` 的 `form_clause_completeness` + 常驻用例 + 产物字段（键名权威在 C14） — **完成判据**: ① **形态层**：`pilot.rehearsal.status` 取值 ∈ `("declared", "unstandardized")`（`agents/pilot/pilot.py:53`），新形态 == `unstandardized`；② **段层**：承载业务数字的五段（`promo` / `budget` / `calibration` / `pilot` / `evaluators`）**必须**带**非空** `note` 且含「未标定」字样（先例 `configs/movie.yaml:603`）——缺任一段 ⇒ 报错并**点名段名**；③ **产物层**：清单与演示产物必须带 `uncalibrated: true` + `uncalibrated_reason`（非空，点名"受众 / 指标口径 / 素材规格 / 预算档属业务侧输入，未给定"）；④ 结论文案**不得**出现「已标定」/「已达标」/「已投产」（出现即红）；⑤ **"以模拟冒充标定"次数恒 0**。**同文件串行**：T2133 → T2142 → T2152。

- [ ] T2153 [US3] **既有两形态取值零改动常驻断言 + 既有夹具取值复核** — 落点：`tests/unit/test_form_clause_completeness.py` — **完成判据**: ① 读**原文件**比对：`configs/movie.yaml:620` 的 `7`、`configs/shortdrama.yaml:658` 的 `14`、`configs/movie.yaml:621` 与 `configs/shortdrama.yaml:659` 的 `gap_tolerance_days: 0` **逐字节不变**（改动即红）；② 既有夹具的 `period_days` 取值只有 `1` / `7`（`tests/unit/test_calibration_config.py:51`）⇒ cadence 收口后**既有夹具不变红**；③ 新增用例若需越界取值 ⇒ **一律用临时文件注入**（**不回退校验**、不改既有文件）。

- [ ] T2154 [US3] **阶段 5 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_clause_completeness.py tests/unit/test_calibration_config.py -q` 绿；② 只读核对 `grep -n "SUPPORTED_CADENCES" core/calibration/periods.py` ⇒ 仍为 `SUPPORTED_CADENCES = (1, 7)`（取值域未被放宽）；③ 只读核对两形态配置的四处既有取值（`configs/movie.yaml:620-621`、`configs/shortdrama.yaml:658-659`）逐字未变。

**检查点**: ✅ 020 全部新增口径**逐项声明**（七项）；cadence **`∈ {1,7}` 越界即显式报错**、取值域**未被放宽**、**不发明第三档量纲**；"不适用"**显式声明率 100%**（留空/省略恒 0）；`calibration.cadence_note` 规则常驻（未标定必填 ∧ 含「近似」与「未标定」∧ 不得宣称已达标）；三层"未标定"标注齐备；既有两形态取值**逐字节不变**。

---

## 阶段 6（A5）：接入改动清单 + CLI + 形态无关演示（T2155~T2162 + T2166 + T2197）

**目标**: 把 G5 验收原文里那个"**仅**"字变成**可审计的清单**——改动集合**由 git 派生**（含未跟踪新增文件）、逐条**按类别**判定（配置 / 插件 / 测试与文档 / 越界）、越界即**退出码 1 + 逐条点名**、产物 **append-only** 且可回溯到基线 ref 与配置指纹；同时把 **机制侧总账**（六项、路径集合由常量给出）落成常驻登记并给出"**机制侧不是零代码改动**"的机器证明；**并把离线九步演示（T2166）前移进本段**——它必须是**形态无关**的机制件，且**必须先于机制落地 ref** 存在（否则"新增 ops 文件 = 越界"会让 B 侧的清单判据必红）。

**独立测试**: `uv run pytest tests/unit/test_form_onboarding.py tests/contract/test_form_onboarding_contracts.py -q`（本阶段完成时 T2155/T2156 转绿）；`uv run python ops/demo_form_plugin.py --form movie --out <tmp_path 类临时目录>` ⇒ 0。

### 阶段 6 的测试任务（先写，确认失败）

- [ ] T2155 [P] [US3] **新增接入改动清单单测（先写、预期红）** — 新增 `tests/unit/test_form_onboarding.py` — **完成判据**: 覆盖 C12 的机检断言——① **一致率 100%**（`changed_files(ref)` 的路径集合与同一 git 派生面解析出的集合逐条相等）；② **未跟踪新增文件不遗漏**（在临时工作区造一个未跟踪新文件后清单必须含它，**这是最常见的漏项模式**）；③ **类别判定**（含"同前缀相反结论"一对：新增 `agents/<agent>/evaluators/plugins.py` **放行** vs 修改 `agents/<agent>/evaluators/__init__.py` **越界**）；④ **故意越界 100% 报出**（例如在 `core/evaluators/composite.py` 里加一行注释 ⇒ 判越界、退出码 **1**、**点名该路径与类别**）；⑤ **append-only**（同目录连跑两次 ⇒ `index.jsonl` 行数 +2、已有清单文件**字节不变**）；⑥ **配置指纹**（BLAKE3 十六进制**前 12 位**）；⑦ 基线取错（`baseline_ref == mechanism_ledger_ref` ⇒ 报错）；⑧ **只读纪律**（`ops/form_onboarding.py` 与 `ops/form_plugin.py` 内零 `git add`/`commit`/`reset`/`checkout` 子命令字符串，文本 + AST 双层；跑完后仓库根未跟踪文件集合前后相等）。跑 ⇒ 红。

- [ ] T2156 [P] [US3] **新增接入清单契约测试（先写、预期红）** — 新增 `tests/contract/test_form_onboarding_contracts.py` — **完成判据**: C12/C13 的可执行面——① 清单形状逐字段（`schema` / `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]` / `counts` / `violations[]` / `mechanism_changes_included` / `zero_code_onboarding` / `exit_code`）；② **六产物键**（`index.jsonl` 每行：`baseline_ref` / `form` / `config_fingerprint` / `change_count` / `violations[]` / `exit_code`）；③ `counts["既有模块被修改"]` == `changes` 中 `violation is True` 且 `status ∈ {M, D}` 的条数（SC-001① 的落点）；④ **I-04**：`len(MECHANISM_LEDGER) == 6` 且 **`MECHANISM_LEDGER_PATHS` 与 `specs/021-form-plugin-validation/quickstart.md` 的"机制侧总账"表（**按表头 `#/路径/kind/服务哪一项总账` 定位，不引行号**——该文件在并行修订中已重排） 的"机制侧总账"表**路径列**集合相等**（**不写死条数**——条数由常量给出；"恰好 39 条"是当次登记面的派生值，条数变化**不**使本用例红，集合不等才红）；⑤ **结构性反证**：`MECHANISM_LEDGER` 中**至少一项** `kind == "modified"` 且路径前缀 ∈ {`core/`, `agents/`, `ops/`}（机制改动**必然**修改既有模块逻辑）；⑥ 退出码语义（0 通过 / 1 有越界或判定失败且逐条点名 / 2 用法或配置错误）。

  **④ 的落空风险（如实登记）**: 本用例读 quickstart 的表 ⇒ 在 T2202 同步该表**之前**它会红（这正是 TDD 序要求看到的失败：文档面尚未补齐）。跑 ⇒ 红。

### 阶段 6 的实现

- [ ] T2157 [US3] **`ops/form_onboarding.py` 增清单机制（git 派生 / 类别判定 / append-only / 指纹）** — 改 `ops/form_onboarding.py`（T2145 已建，本任务追加） — **完成判据**: `changed_files(baseline_ref)` = `git diff --name-status <ref>` **∪** `git ls-files --others --exclude-standard`（**必须含未跟踪新增文件**）；`classify(path, status)` 按**类别**（取值域 `{"config", "plugin", "test_doc", "out_of_scope"}`）而非按路径前缀——配置 = **新增** `configs/*.yaml`；插件 = **新增** `core/evaluators/plugins/**` 或 `agents/<agent>/evaluators/**`；测试与文档 = **新增或修改** `tests/**`、`docs/**`、`specs/**`；越界 = **任何既有文件的修改或删除**位于 `core/`/`agents/`/`ops/`/`web/`/`dreaming/`/`policies/`，或三类之外的**新增**文件；`config_fingerprint(config_path)` 用 `core/orchestration/models.py:36` 的 `fingerprint_of` 取前 12 位（**不改该函数本身**）；`build_manifest(config_path, baseline_ref, *, mechanism_ledger_ref=None)`；`write_manifest(manifest, out_dir)` **append-only**（`onboarding-<form>-<seq:04d>.json` 写后不回改 + `index.jsonl` **追加**一行）；越界 ⇒ 返回码 **1** 并**逐条点名**路径与类别（不得只报总数、不得静默放行）。

- [ ] T2158 [US3] **`MECHANISM_LEDGER`（六项）与 `MECHANISM_LEDGER_PATHS`（路径集合，**条数由常量给出**）常驻登记** — 改 `ops/form_onboarding.py` — **完成判据**: 六项与 FR-013 的六项总账**一一对应**（① 配置驱动的插件声明与唯一装配点 ② 扫描面补面 ③ 形态名派生 + 三副本收敛 ④ `agents/pilot/pilot.py:613` 裸词收敛 + 020 口径逐项机检 ⑤ "恰好两份"升级为登记完备 ⑥ 接入改动清单机检），每项形如 `{"step": "<A1~A5>", "items": [{"path": "...", "kind": "new|modified"}]}`；**I-04：路径并集必须补齐下列两面**——(a) **A1 的夹具同步面**（**穷举口径 = "凡在 `tests/**` 内调用六个 `build_*_evaluators` 的测试文件"；该面由符号调用反查、不是人工维护**：`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、`tests/unbiasedness/**` 的实际命中文件；实测命中 **16 个文件**，其中 `tests/conftest.py` 属 ⑤ ⇒ 子表 **15 条**；**逐条以反查结果与 T2107② 的穷举清单为准**）、(b) **新增基线夹具** `tests/unit/fixtures/evaluator_assembly_baseline.json`（① 项，`kind == "new"`）与 **T2197 的两个新文件**（`tests/unit/test_form_no_new_dependency.py`、`tests/unit/fixtures/dependency_baseline.json`，① 项）；并**同步登记** T2146 普查后须委派的同族枚举副本（③ 项，逐条以 T2196 清单为准）；判据 = 与 `specs/021-form-plugin-validation/quickstart.md` 的"机制侧总账"表（**按表头 `#/路径/kind/服务哪一项总账` 定位，不引行号**——该文件在并行修订中已重排） 的表**集合相等**（常驻机检读该表格；**不写死条数**）；去重纪律：`ops/form_guard.py`（②③）与 `tests/unit/test_form_switch.py`（②⑤）各**只算一条**；**A4 不单列第 7 项**（`core/calibration/config.py` → ①；`agents/pilot/pilot.py` 与 `tests/unit/test_form_clause_completeness.py` → ④）；`tests/unit/test_calibration_config.py` **不在**该表内（它是"明确不动的既有文件"）；**`ops/demo_form_plugin.py` 归 ⑥**（`kind == "new"`，其创建在 T2166/A5）。

- [ ] T2159 [US3] **新增 CLI 门面 `ops/form_plugin.py`（薄转发 + 退出码语义与既有工具一致）** — 新增 `ops/form_plugin.py` — **完成判据**: 四子命令逐字——`guard [--configs-dir configs] [--roots core agents]`、`registration --config <路径>`、`onboarding --baseline <ref> --config <路径> --out <目录>`、`sync-versions --check|--write --config <路径>`；**退出码**：`0` 通过 / `1` 判定失败或越界（**逐条点名**）/ `2` 用法或配置错误（缺 `--baseline`、基线 ref 不可解析、配置不可读、`declared_forms()` 派生失败），常量符号沿用 `EXIT_OK` / `EXIT_FAILED` / `EXIT_USAGE`（先例 `ops/transfer.py:51-53`）；各入口 `--help` ⇒ **0**；**薄转发**（判定全在 `ops/form_guard.py` / `ops/form_onboarding.py`，CLI 只解析参数与打印 JSON）；**只读 git 与文件、只写 `--out`**（**不** `git add`/`commit`/`reset`、不触碰工作区）；`uv run python ops/form_plugin.py guard` ⇒ 0（两层零违规、逐条打印 `(相对路径, 所属符号名, 行号, 命中内容)`）；**零新增运行时依赖**（T2197）。

- [ ] T2160 [US3] **`sync-versions` 的校验口径与"不允许覆盖实现"机检** — 落点：`ops/form_plugin.py`（`sync-versions`）+ 常驻用例在 `tests/contract/test_plugin_contracts.py` — **完成判据**: `--check`（**默认**）只报差集、**退出码非 0**（有差集时 1）；`--write` 才回写且**只改** `evaluators.plugins.*.*.*.version` 一个叶子键（走既有 `core/yaml_edit.py` 定点改写，其余字节逐字不变、含注释与行序）；**判据永远是装配期一致性校验**（声明 `version` == 实现产出 `spec.version`），**不是** sync 的产物；**门禁只跑 `--check`**（**禁止**以改写权威配置换取绿灯）；**三条禁止**机检常驻：不得把声明值写回 `spec`（`core/evaluators/base.py:41` 的 `frozen=True`）、不得用 `dataclasses.replace`/反射在注册前改写 `spec.version` 或 `spec.key`、不得在注册前"归一化"版本字符串；装配后三方相等（声明值 == 实现产出 == `registry` 内实例值）。

- [ ] T2161 [US3] **机制侧总账的分离举证（不得冒充零代码改动）** — 落点：`ops/form_onboarding.py` + `tests/contract/test_form_onboarding_contracts.py` + 交付说明 — **完成判据**: ① 清单头两字段齐备且**不相等**（`baseline_ref` != `mechanism_ledger_ref`；相等 ⇒ 报错，属"基线取错、判据自相矛盾"）；② `mechanism_changes_included: false`（为 `true` ⇒ 报错：机制改动被混进接入账）与 `zero_code_onboarding: true`（**只对基线之后的接入侧**成立）；③ **结构性反证常驻**：以 `MECHANISM_LEDGER` **之前**的 ref 为基线跑 `onboarding` ⇒ `violations` **必然非空**且**逐条命中** ledger 的 `modified` 项（这就是"机制侧不是零代码改动"的机器证明）；④ 变更说明与 `specs/021-form-plugin-validation/quickstart.md` 逐项列出六项总账，并写明"**此后**新形态接入才真的仅新增配置 + 插件"——**禁止**把 A1~A5 写成"零代码改动"。

- [ ] T2166 [US1] **新增离线九步端到端演示 `ops/demo_form_plugin.py`（唯一入口、**形态无关**、退出码 0 = 全步 ok）** — 新增 `ops/demo_form_plugin.py`（**I-09：本任务由 B1 前移进 A5——它必须在机制落地 ref 之前落地**；否则它作为"新增 ops CLI"落进接入账单，C12 判"新增 ops 文件 = 越界" ⇒ T2167①/T2173① 的 `violations == []` 与退出码 0 **必红**） — **完成判据**: ① **形态无关（硬要求）**：演示的形态集合**一律遍历 `declared_forms()`**，**零形态字面量、零形态判断分支**（同时受 T2128/T2130 的两层扫描守卫），对新形态**零代码改动**即可演示（B1/B2 的接入改动集里**不含**本文件）；`--form` 只接受 `declared_forms()` 中已声明的取值（未声明 ⇒ 用法错误、退出码 **2**）；② 九步逐条对应 C14 的表——配置加载与预检（临时目录派生副本，先例 `tests/conftest.py:3054-3079`；`config_completeness` 经 020 口径七项 + `evaluators` 段清单解析器全部通过）；缺项即拒绝（逐项删一个 020 口径键、再删 `evaluators` 段 ⇒ 预检**拒绝启动**并点名段与键）；插件装配（声明解析 / `evaluator_id`/`version` 一致性 / 一一对应与保序 / 同键重复注册被拒 / 非确定性被拒 / 缺 `cost_per_call` 被拒 / 目录里存在但未声明 ⇒ 不可用）；评估与合成分数（`eval_breakdown` 键为 `evaluator_id@version` ⇒ `composite_score_versioned`）；留痕（落一个带 `eval_breakdown` 的节点与一份运行记录到**临时目录**）；**两形态共用同一份插件代码**（至少一条 `impl` 与另一形态**逐字相同**——对 A5 的两形态即 `movie` / `shortdrama`）；静态守卫（两层扫描零违规 + 由派生值构造的合成反例举证"注入即红"）；登记点完备（五处逐一 + 登记完备三条 + 无第六处）；诚实分层与零成本（三层"未标定"机检 + 真实花费 0 / 外部网络 0 / 凭证读取 0）；③ **I-07：`--out` 与工作目录一律落 `tmp_path` 类临时目录**（`tempfile.mkdtemp()`；`--keep-work-dir` 仅调试保留），**禁止**默认落仓库根、`.specify/` 或任何仓库内路径——"仓库根零新增文件"由此成为可断言事实；④ **结构性零成本**：最小形态不声明 judge ⇒ **不构造 `LLMGateway`**；本文件**不含** `os.environ`/`os.getenv`/`environ`（文本 + AST 双层）、**不 import** 任何 HTTP 客户端（`http.client`/`urllib.request`/`requests`/`httpx`）；⑤ 产物固定字段 `network: "none"` / `credentials_required: false` / `uncalibrated: true` / `uncalibrated_reason` 非空；⑥ 跑完**仓库根零新增文件**（前后未跟踪文件集合相等）；⑦ `--help` ⇒ **0**；⑧ **不新造第二个端到端演示入口**；⑨ **零新增运行时依赖**（T2197）。

- [ ] T2197 [US3] **零新增运行时依赖常驻用例（FR-012 的机检承载）** — 新增 `tests/unit/test_form_no_new_dependency.py` + 新增基线快照 `tests/unit/fixtures/dependency_baseline.json` — **完成判据**: ① 快照由**机制落地前**导出，内容 = `pyproject.toml` 的 `dependencies` / `optional-dependencies`（规范化后的名字集合）与 `uv.lock` 的包名集合；② 常驻断言：**当前依赖集合 == 基线快照**（新增、删除、重命名任一依赖 ⇒ **红**），并给出集合差集（逐条点名，不得只报总数）；③ 断言本特性新增模块的 import 面 ⊆ **stdlib ∪ 既有依赖**——`core/evaluators/plugin.py`、`core/evaluators/plugins/**`、六个 `agents/<agent>/evaluators/plugins.py`、`ops/form_guard.py`、`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py` 逐个（AST 求 import 名，容器内**不得**出现第三方插件框架）；④ `grep -n "entry_points" pyproject.toml` ⇒ **0 命中**（不引入第三方插件框架/安装元数据驱动的可用性）；⑤ 该用例与 `tests/unit/test_form_onboarding.py` 同属机制侧（ledger ① 项，`kind == "new"`）。

- [ ] T2162 [US3] **阶段 6 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_onboarding.py tests/unit/test_form_no_new_dependency.py tests/contract/test_form_onboarding_contracts.py -q` **转绿**（④ 的文档面一致率用例需在 T2202 同步 quickstart 表后转绿，**如实登记该依赖**）；② `uv run python ops/form_plugin.py guard` ⇒ **0**；`uv run python ops/form_plugin.py registration --config configs/movie.yaml` ⇒ **0**；`uv run python ops/form_plugin.py sync-versions --check --config configs/shortdrama.yaml` ⇒ **0**；③ 各子命令 `--help` ⇒ **0**；缺 `--baseline` ⇒ **2**；④ `uv run python ops/demo_form_plugin.py --form movie --out "$(mktemp -d)"` ⇒ **0**（九步全 ok）；`--form nope`（未声明形态）⇒ **2**；⑤ 只读核对 `len(MECHANISM_LEDGER) == 6` 且 `MECHANISM_LEDGER_PATHS` 的路径集合与 quickstart 表**集合相等**（条数由常量给出，不写死）；⑥ 只读核对 `git status --porcelain` 中除本特性新增件外**无运行期产物残留**。

**检查点**: ✅ 清单**由 git 派生**（含未跟踪新增文件 ⇒ 一致率 100% 由构造保证）；判定**按类别**（同前缀相反结论可复现）；越界 ⇒ 退出码 1 + 逐条点名；产物 **append-only** 且含基线 ref 与配置指纹；六产物键齐备；**机制侧总账常驻**（含 A1 夹具同步面与新基线夹具）且与 quickstart 表**集合相等**；**"机制侧不是零代码改动"有结构性反证**；**演示脚本已前移 A5 且形态无关**（B 侧接入改动集不含它）；**零新增运行时依赖有机检**。

---

## 阶段 7（B1）：广告形态接入（T2163~T2165、T2167~T2169）

**目标**: 机制建成后的**第一个举正面**——`ad` 形态**仅新增配置 + 插件**即可从配置跑到跑通，并以"接入改动清单"证明本次接入**未触碰**任何既有模块逻辑。

**独立测试**: `uv run python ops/demo_form_plugin.py --form ad --out <tmp_path 类临时目录>` ⇒ 退出码 0；`uv run python ops/form_plugin.py registration --config configs/ad.yaml` ⇒ 0；`uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> --config configs/ad.yaml --out <临时目录>` ⇒ 0（`violations == []`）。

**⚠️ 关键**: ① **演示脚本已在 A5（T2166）落地且形态无关** ⇒ 本阶段**零演示脚本改动**（`ops/demo_form_plugin.py` **不得**出现在 B1 的接入改动集里——若出现，说明机制 ref 打早了，须先补打机制 ref 再重跑，**不得**手工排除）；② **不发明业务定义**——广告形态的受众、指标口径、素材规格、预算档**属业务侧输入**（开放问题 1）；未给定期间按**最小可行形态**接入并**如实标注"未标定"**（复用既有标记词汇 `unstandardized` + 段级 `note` + 产物 `uncalibrated_reason`，**不新造同义词**）。

### 阶段 7 的测试任务（先写，确认失败）

- [ ] T2163 [P] [US1] **新增离线端到端集成测试（先写、预期红）** — 新增 `tests/integration/test_new_form_onboarding_offline.py` — **完成判据**: 覆盖 US1 场景 1~5 与 FR-010——① 装配按**配置声明**实例化并注册（`impl` 由唯一装配点解析、`spec.evaluator_id` == 声明键、`spec.version` == 声明值、集合 ↔ `evaluator_weights.<agent>` 键集逐字相等且保序）；② 评估 → `eval_breakdown` 键为 `evaluator_id@version` → `composite_score_versioned`（`core/evaluators/composite.py:37`）出分 → 留痕；③ **退出码 0**、**零真实花费、零外部网络调用、零凭证读取**；④ 接入改动清单 `violations == []`；⑤ **该用例必须离线**（不依赖 Docker / 真实 DB ⇒ 单文件子集可跑）；打 `@pytest.mark.integration`；⑥ 全部落盘走 `tmp_path`（不改仓库）；跑 `uv run pytest tests/integration/test_new_form_onboarding_offline.py -q` ⇒ **红**（`configs/ad.yaml` 与插件尚未落地），**不得**写成 skip。

### 阶段 7 的实现

- [ ] T2164 [US1] **新增 `configs/ad.yaml`（形态配置，本形态唯一的新增配置面）** — 新增 `configs/ad.yaml` — **完成判据**: ① `form: ad` 且**文件名 stem == `form:` 取值**（T2129 的纪律）；`form_aliases` 显式声明（含中文别名，如「广告」；**不得**把中文别名写进代码）；② **段集合与既有两形态一致**（含新增 `evaluators` 段与 `form_aliases`；"形态差异靠值不靠删段"，`tests/unit/test_config_integrity.py:122-126` 的口径）；③ **020 全部口径逐项声明**——`calibration.period_days ∈ {1,7}`（业务侧未确认前取最接近一档）+ `calibration.cadence_note`（**必填**且含「近似」与「未标定」）；`calibration.window_semantics` 与 `window_semantics_change_date`；`budget.channels.<id>.tiers` 渠道命名空间（或 `budget.not_applicable.channels` 的**非空理由且含「不适用」**）；`promo.attribution_date_required_since`；`calibration.transfer` 六键 + `not_applicable` 显式理由（若仅声明自身）；`budget.runs.min_window_days` 与 `gap_tolerance_days`；④ `pilot.rehearsal.status: unstandardized`；五段（`promo`/`budget`/`calibration`/`pilot`/`evaluators`）**非空 `note` 且含「未标定」**；⑤ **不声明 judge**（结构性零 LLM 花费 ⇒ 演示不构造 `LLMGateway`）；⑥ 每个 Agent **至少一条 `rule.` 门禁 + 至少一条 `proxy.` 分量**（先例 `tests/unit/test_config_integrity.py:144-150`）；⑦ **零 `<>` 占位**、零 `TODO`/`FIXME`/`PLACEHOLDER`/`xxx`（`:174-178` 的禁列）；⑧ **零新造业务数字**（能复用既有形态取值的复用并在 `note` 标明"未标定（沿用 <形态> 现值，待业务侧给定）"）。

- [ ] T2165 [US1] **`configs/ad.yaml` 的插件声明与通用插件（两类都必须经 `impl` 声明才生效）** — 新增 `core/evaluators/plugins/__init__.py` 与 `core/evaluators/plugins/<plugin>.py`（业务无关通用件，**新目录**）；改 `configs/ad.yaml` 的 `evaluators.plugins` 段 — **完成判据**: ① 评估器组合**尽量复用**既有 Agent 绑定工厂的 `impl`（这就是"两形态共用同一份插件代码"的举正面）；② 本形态特有的通用件落 `core/evaluators/plugins/` 并**必须**在该配置里经 `impl` 引用（"**插件目录决定不了可用性、配置声明才决定**"；未声明的模块级公开函数 ⇒ 机检报错）；③ 插件**业务无关**四条的**文本 + AST 双层**机检：零 `import agents.*` / `dreaming.*`、零形态字面量与形态判断分支、零 `os.environ`/`os.getenv`/配置文件路径读取/网络调用、零 LLM 或厂商 SDK 直连；④ 参数**全部**来自 `params` 注入（缺项即报错、不取码内默认；签名含 `*args`/`**kwargs` ⇒ 报错）；⑤ 插件携带必需元数据（`evaluator_id`/`version`/`kind`/`deterministic`/`cost_per_call`，`core/evaluators/base.py:42`）且 `deterministic=True`（**不声明 judge ⇒ 无 LLM 面**）；⑥ 新增插件的单元测试、注册元数据与"与既有评估器的对比样本"随件提交（宪章工作流门禁）；**回放对比证据**（"涉及评估器的变更必须附带回放对比证据"）由 **T2199** 承载（本任务只交付插件与其对比样本 ⇒ **不得**以"对比样本"顶替回放证据）；⑦ `uv run python ops/form_plugin.py sync-versions --check --config configs/ad.yaml` ⇒ **0**（无差集）；⑧ **零新增运行时依赖**（T2197）。

- [ ] T2167 [US1] **`configs/ad.yaml` 的接入改动清单（越界为 0）** — 落点：`uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> --config configs/ad.yaml --out <临时目录>` — **完成判据**: ① **退出码 0**、`violations == []`、`counts["既有模块被修改"] == 0`（**既有模块被修改的文件数恒为 0**）；② 清单逐条给出路径 + `status` + **类别**（配置 / 插件 / 测试与文档 / 越界）；③ 产物含 `baseline_ref` / `mechanism_ledger_ref`（**两者非空且不相等**）/ `form` / `config_path` / `config_fingerprint`（BLAKE3 前 12 位）；④ `index.jsonl` **追加**一行（六产物键齐备）、已有清单文件**字节不变**；⑤ **I-09 的收口断言**：清单**不得**出现 `ops/demo_form_plugin.py`（演示已前移 A5 ⇒ 不在本基线之后的改动集内）；若出现 ⇒ 判"机制 ref 打早了"，**先补打机制 ref 再重跑**，**不得**手工排除该条（手工排除即"以清单换取绿灯"）；⑥ 清单**只含** B1 的新增件（`configs/ad.yaml` + 插件 + 测试与文档）。

- [ ] T2168 [US1] **`configs/ad.yaml` 的登记点完备性（五处逐一 + 登记完备三条 + 无第六处）** — 落点：`uv run python ops/form_plugin.py registration --config configs/ad.yaml` + `tests/unit/test_form_registration.py` — **完成判据**: ① 退出码 **0**，五处逐一"已登记 + **已委派**"；② 登记完备三条成立（`form:` 取值两两唯一 ∧ `declared_forms()` 与各登记点形态集合**双向相等** ∧ 配置数 ≥ 2）；③ `sixth_site_scan()` 结论为**无第六处**（含 T2198 的扩展口径：全仓形态枚举命中数恒 0）；④ `uv run pytest tests/unit/test_form_registration.py -q` 绿——该用例遍历 `declared_forms()` 的**全部**取值 ⇒ `ad` **自动**纳入（无需为该形态改测试）；⑤ 新形态的**取值差异**已进入 T2138/T2141 新增的逐对断言面。

- [ ] T2169 [US1] **阶段 7 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_registration.py tests/unit/test_form_switch.py tests/unit/test_config_integrity.py tests/unit/test_form_guard.py -q` 绿（`ad` 与中文别名自动进派生面与禁令面 ⇒ 用例数增加、**零用例减少**）；② `uv run python ops/demo_form_plugin.py --form ad --out "$(mktemp -d)"` ⇒ **0**（九步全 ok，**脚本零改动**）；③ `uv run python ops/form_plugin.py guard` ⇒ **0**；④ 只读核对 `grep -rn "广告" core agents --include=*.py` ⇒ **0 命中**（中文别名只出现在配置里，不进代码）；⑤ 只读核对 `git status --porcelain` 无运行期产物残留。

**检查点**: ✅ `ad` 形态**仅新增配置 + 插件**即可装配通过并离线跑通（退出码 0）；**零真实花费/零网络/零凭证**为结构性事实（不声明 judge ⇒ 不构造网关）；接入改动清单**越界为 0** 且**不含演示脚本**（I-09 的收口）；五处登记点**自动覆盖**该形态；三层"未标定"标注齐备、**零发明业务数字**。

---

## 阶段 8（B2）：漫剧形态接入（T2170~T2174）

**目标**: **第二个举正面**——证明机制**可复用**：第二个形态同样**仅新增配置 + 插件**接入，且与 `ad`（或既有两形态）**共用同一份插件代码**（形态差异**只在配置值**）。

**独立测试**: `uv run python ops/demo_form_plugin.py --form animated --out <tmp_path 类临时目录>` ⇒ 0；`uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> --config configs/animated.yaml --out <临时目录>` ⇒ 0。

- [ ] T2170 [US1] **新增 `configs/animated.yaml`（形态配置）** — 新增 `configs/animated.yaml` — **完成判据**: 口径与标注要求**同 T2164 逐条**（`form: animated` + 中文别名（如「漫剧」）+ stem 一致 + 段集合一致 + 020 口径逐项声明 + `pilot.rehearsal.status: unstandardized` + 五段 `note` 含「未标定」+ 不声明 judge + 零 `<>` 占位 + 零 `TODO` 系列）；**不得**把 `configs/ad.yaml` 复制后只改名——业务侧未标定的部分**逐项如实标注**、**零发明数字**；若某取值沿用 `ad` 或既有形态，在 `note` 里标明"未标定（沿用 <形态> 现值，待业务侧给定）"。

- [ ] T2171 [US1] **`configs/animated.yaml` 的插件声明：至少一条 `impl` 与 `ad`（或既有两形态之一）逐字相同** — 改 `configs/animated.yaml` 的 `evaluators.plugins` 段 — **完成判据**: ① 与另一形态**逐字相同**的 `impl` 条数 **≥ 1**（直接举证 US1 场景 3："同一插件被两个形态配置声明、两形态**共用同一份插件代码**、形态差异**只在配置值**"）；② 同 id 同 version 在**同一注册中心**内重复注册**被拒**（`core/evaluators/registry.py:35-38`，由 T2110 的契约测试常驻守住）；③ 若该形态需要新参数 ⇒ **只经 `params` 注入**，**不得**为此改动任何既有 Agent 的配置类或装配函数；④ `uv run python ops/form_plugin.py sync-versions --check --config configs/animated.yaml` ⇒ **0**；⑤ **零新增运行时依赖**（T2197）。

- [ ] T2172 [US1] **两形态并跑用例（共用插件实例类型 + 差异全部来自配置值）** — 落点：`tests/integration/test_new_form_onboarding_offline.py`（T2163 的同文件追加用例） — **完成判据**: 一条用例跑完 `ad` + `animated`（各自装配 → 评估 → 合成分数）：① 断言两形态**共用插件实例类型**（`impl` 相同的条目产出同一类/工厂，逐条比对类型）；② 断言两形态的**得分/明细差异全部来自配置值**——对同一工件、同一评估器，逐项把差异归因到配置差异键（差异项集合 == 配置差异键集合，**无未归因差异**）；③ 两形态各自的 `eval_breakdown` 键集 == 各自的 `evaluator_weights.<agent>` 键集；④ 全部落盘走 `tmp_path`。

- [ ] T2173 [US1] **`configs/animated.yaml` 的接入改动清单 + 登记点完备（含"对 ad 与 animated 各跑一次"的机检）** — 落点：`onboarding` / `registration` 两条 CLI — **完成判据**: ① `uv run python ops/form_plugin.py onboarding --baseline <机制落地 ref> --config configs/animated.yaml --out <临时目录>` ⇒ **0** 且 `violations == []`；② `uv run python ops/form_plugin.py registration --config configs/animated.yaml` ⇒ **0**；③ **对 `ad` 与 `animated` 各跑一次"接入未触碰既有模块逻辑"的机检**——两次清单的 `counts["既有模块被修改"]` **均为 0**，且两次的 `baseline_ref` 一致（同为机制落地 ref）、`config_fingerprint` **不同**（两份配置可指认）；④ 两份清单互不覆盖（append-only、文件名含形态 id 与序号）；⑤ **两份清单都不得出现 `ops/demo_form_plugin.py`**（I-09 的收口，同 T2167⑤）。

- [ ] T2174 [US1] **阶段 8 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_registration.py tests/unit/test_config_integrity.py tests/unit/test_form_switch.py -q` 绿（两份新配置**自动**纳入派生面 ⇒ 用例数增加、零减少）；② 两支 `registration`（`configs/ad.yaml` / `configs/animated.yaml`）⇒ **0**；③ `uv run python ops/demo_form_plugin.py --form animated --out "$(mktemp -d)"` ⇒ **0**（**脚本仍零改动**）；④ 只读核对 `len(declared_forms()) == 4`（movie / shortdrama / ad / animated）且四个 `form:` 取值**两两唯一**。

**检查点**: ✅ 两形态**共用同一份插件代码**（至少一条 `impl` 逐字相同）；两份接入清单**越界均为 0** 且不含演示脚本；`animated` 在五处登记点**自动**登记；**零发明业务数字**；机制可复用性由"第二个形态零代码接入（含演示脚本零改动）"直接举证。

---

## 阶段 9（B3）：离线端到端、登记与门禁同步、交付留痕（T2175~T2180 + T2199）

**目标**: 把机制闭合——对 `ad` 与 `animated` **各跑一次**离线端到端演示，产出接入改动清单 / 守卫报告 / 登记报告 / 演示报告（append-only、可回溯），交付**回放对比证据**（或"零行为变更"的 N/A 理由），并核实常驻门禁口径**零放松**。

- [ ] T2175 [US1] **离线端到端证据（对 `ad` 与 `animated` 各跑一次）** — 落点：`uv run python ops/demo_form_plugin.py --form ad --out "$(mktemp -d)"` 与 `--form animated --out "$(mktemp -d)"` — **完成判据**: ① 两支命令**退出码 0**、`steps` 九步逐条 `ok`；② **真实花费 = 0 / 外部网络调用 = 0 / 凭证读取 = 0**（演示内计数断言：网关调用 0 次 / 账本总额 0 / `CostRecord` 零新增 / 不构造网关）；③ **新形态触发的真实投放与真实素材生成次数恒 0**（不装配投放适配器、不调用任何生成厂商、不做多租户/公网服务化——B/C 路径不变）；④ 产物 `network == "none"`、`credentials_required is False`；⑤ **I-07：`--out` 落 `tmp_path` 类临时目录**（`mktemp -d` / `tempfile.mkdtemp()`），**跑完仓库根零新增文件**（前后未跟踪文件集合相等）；⑥ **演示脚本零改动**（两形态共用同一入口，形态集合来自 `declared_forms()`）。

- [ ] T2176 [US3] **登记点同步验证（逐处，缺一即逃逸）** — 落点：`uv run python ops/form_plugin.py registration --config configs/<id>.yaml`（对两新形态各一次） — **完成判据**: ① 五处**逐一**判定"已登记 + 已委派"，缺项 ⇒ 报错并**点名是哪一处、缺哪个形态**；② 登记完备三条成立（两两唯一 ∧ 双向集合相等 ∧ 配置数 ≥ 2）；③ `sixth_site_scan()` / `form_enum_sites()` 结论为**无第六处、枚举命中数 0**（新形态未新造第六处、未留下枚举副本）；④ 新形态的**取值差异**已登记进逐对断言面（每对差异集非空 ∧ 必含 `form` ∧ 必不含 `web`/`cost_regression`）。

- [ ] T2177 [US1] **产物与留痕（append-only、可回溯、标注复现）** — 落点：`--out`（**tmp_path 类临时目录**） — **完成判据**: ① 五件产物齐备——`onboarding-<form>-<seq:04d>.json`、`index.jsonl`、`guard-report.json`、`registration-report.json`、`demo-report.json`；② **append-only**：同目录连跑两次 ⇒ `index.jsonl` 行数 +2、已有清单文件**字节不变**；③ 守卫报告逐条 `(相对路径, 所属符号名, 行号, 命中内容)`（**符号名锚点**）+ 两层分别计数 + 例外三条判定结果；④ 登记报告逐处 `{site, symbol, kind, forms, delegated, missing[]}`；⑤ 演示报告含 `steps` / `ok` / `network` / `credentials_required` / `uncalibrated` / `uncalibrated_reason` / `elapsed_seconds`；⑥ 结论文案固定为"**机制已就绪 / 业务定义未标定**"，**不得**出现「已标定」「已达标」「已投产」。

- [ ] T2178 [US3] **门禁同步（覆盖率口径与四道常驻门禁零放松）** — 落点：`tests/unit/test_billing_core_purity.py` 的 `OFFLINE_ASSEMBLIES`（`:268-285`）与构造点计数（`:359`） — **完成判据**: ① 覆盖率口径**不降**（≥85%，含 `web`）；对抗（合并阻塞）、无偏性（发布阻塞）、Immutable 审计与成本回归（每日）四道常驻门禁**零放松**（本特性对 `tests/adversarial/**` 的用例体零改动；对 `tests/unbiasedness/**` 的改动**仅限** T2125 的夹具同步面，断言体零改动——如实登记）；② **A-01（删去条件分支，写死结论）**：演示**不构造 `LLMGateway`** ⇒ `OFFLINE_ASSEMBLIES` 与构造点计数 `:359` 的 `len(sites) == 14` **一律不动**（**本项无"若…则…"分支**：若实现期确需构造网关，属对 C14 的重大偏离，**必须先上缴裁决**再改，**不得**自行登记"按扩展更新"换取绿灯）；③ `uv run pytest tests/unit/test_billing_core_purity.py -q` 绿且用例数不减。

- [ ] T2179 [US3] **机制/接入分账的最终复核（防回潮）** — 落点：两份接入清单 + `MECHANISM_LEDGER_PATHS` + 变更说明 — **完成判据**: ① 两次接入的 `baseline_ref` 均为**机制落地 ref** 且 `!= mechanism_ledger_ref`；② `mechanism_changes_included: false`、`zero_code_onboarding: true`；③ 以 ledger **之前**的 ref 为基线的反证用例**仍红**（`violations` 非空且逐条命中 `modified` 项）；④ `MECHANISM_LEDGER_PATHS` 的路径集合**不被新形态污染**（两份新形态配置、其插件与**演示脚本**都不属"接入改动"新增项——演示脚本已是机制件：`ops/demo_form_plugin.py` 在 ⑥ 项、`configs/ad.yaml` / `configs/animated.yaml` 与 `core/evaluators/plugins/**` **不在** ledger 内）；⑤ 变更说明里 A 与 B **分开陈述**（"机制侧 = 本特性的代码改动主体；**此后**新形态接入才真的仅新增配置 + 插件"）。

- [ ] T2180 [US1] **阶段 9 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_onboarding.py tests/unit/test_form_registration.py tests/unit/test_form_guard.py tests/unit/test_form_no_new_dependency.py -q` 绿；② 两条 `onboarding`（ad / animated）⇒ **0**；两条 `registration` ⇒ **0**；③ `uv run python ops/form_plugin.py guard` ⇒ **0**；④ `uv run python ops/demo_form_plugin.py --form ad --out "$(mktemp -d)"` 与 `--form animated --out "$(mktemp -d)"` ⇒ 均 **0**；⑤ 只读核对 `git status --porcelain` **无任何运行期产物**（`--out` 一律落临时目录）。

- [ ] T2199 [US1] **回放对比证据（或"零行为变更"的 N/A 理由）** — 落点：`tests/integration/test_new_form_onboarding_offline.py`（追加证据用例）+ 交付说明 — **完成判据**: ① 对**新增插件**（`core/evaluators/plugins/**` 与 `agents/<agent>/evaluators/plugins.py` 的新条目）附**回放对比证据**：同一工件、同一配置下用**既有插件**与**新增插件**各跑一次评估，产出可机检的对照件（各自 `eval_breakdown` 的键集与逐条得分/明细、`evaluator_id@version`、**确定性复跑一致**）；② 对**既有评估器**：本特性**零行为变更** ⇒ **必须显式写 N/A 理由**（"既有评估器实现文件（单评估器实现模块）零改动 + 装配序列改造前后逐字相同（T2111 / T2121）⇒ 无行为变更、回放对比不适用；证据面 = `tests/unit/fixtures/evaluator_assembly_baseline.json` 与 T2121 的实现文件零改动机检"）——**不得**省略该条（宪章"涉及评估器/策略/模拟器的变更必须附带回放对比证据"是 **MUST**，"不适用"也要写明理由）；③ 证据件与理由一并登记进交付说明（与 T2200 的宪章条款声明同一处）。

**检查点**: ✅ 两形态离线端到端**各一条证据**、退出码 0、零花费/零网络/零凭证；五件产物 append-only 且可回溯（`--out` 一律临时目录）；登记点逐处已登记、无第六处、枚举命中数 0；**回放对比证据或 N/A 理由**齐备；常驻门禁口径**零放松**（`len(sites) == 14` 写死不动）；机制侧与接入侧**分账清晰**（两件事不混同）。

---

## 阶段 10：文档与门禁同步（T2181~T2187 + T2200~T2202）

**目标**: 把"会变红的既有测试与夹具"逐项按**扩展**更新并核销；把本特性的交付面、**触及的宪章条款**与**复杂度论证**写进交付说明；把 quickstart 的机制侧总账表与 `MECHANISM_LEDGER_PATHS` 同步为**集合相等**。

- [ ] T2181 [US2] **"会变红的既有测试与夹具"19 项逐项核销（按扩展更新、不削弱）** — 落点：交付说明（核销结论）+ 逐处文件按结论落地 — **完成判据**: 逐项核销 `specs/021-form-plugin-validation/research.md:577` 起决策 12 的 **19 项**，每项给出"保留 / 委派 / 扩展 / 改口径"结论与**具体改动点**，且**逐条点名**：① `tests/unit/test_form_switch.py:421-429`（**扩展 + 委派**：`:423` 删去属**补面**、`agents/pilot/pilot.py:613` 收敛、`core/deployment/evidence.py:97` 走 E1、循环体与断言体保留）；② `:431-436`（**委派**，模式集不变）；③ `:438-441`（**改口径**为登记完备，**禁止删除**）；④ `:443-461`（**委派**，与字面量清单同源）；⑤ `:341-379`（**保留**，**15 键**差异集不变 + 逐对断言另立）；⑥ `tests/unit/test_billing_core_purity.py:34-35`（**委派** + `:145` 与 `:210-224` 保留）；⑦ `tests/unit/test_dev_core_degraded_purity.py:28-29`（**委派** + `:101`/`:163-179` 保留）；⑧ `tests/unit/test_config_integrity.py:133-141`（**扩展**）；⑨ `tests/unit/test_config_integrity.py:153-171`（**扩展**：`REQUIRED_PATHS` 增条目）；⑩ `tests/unit/test_config_integrity.py:122-126`（**保留** + 新增"全部形态段集合一致"另立）；⑪ `tests/contract/test_pilot_contracts.py:418-477`（**保留** + **委派**禁用元组；差异集 **15 键**）；⑫ `agents/pilot/pilot.py:377` 的返回段清单（**扩展**，见 T2143）；⑬ `tests/conftest.py:2854`/`:3054-3079`（**扩展**：`movie` 精简副本职责保留）；⑭ 六个装配函数的调用面与**内联配置字典夹具**（**必须扩展夹具**，见 T2125；**不得**实现兜底）；⑮ `tests/unit/test_billing_core_purity.py:355-362`（**不动**：演示不构造网关 ⇒ `len(sites) == 14` 写死，见 T2178②）；⑯ `tests/unit/test_sound_composite.py:98`/`:129`/`:244`（**保留**：装配点保序是硬要求）；⑰ `tests/unit/test_dev_compare_adopt.py:758`（**保留**，本特性不改该断言）；⑱ `tests/unit/test_billing_channels.py:52` / `tests/contract/test_billing_contracts.py:97` / `tests/unit/test_pilot_rehearsal.py:34` / `tests/unit/test_billing_core_purity.py:31` / `tests/unit/test_form_switch.py:30`（**扩展/委派**，见 T2146）；⑲ `tests/unit/test_calibration_config.py:124`（**保留**；cadence 越界报错**另立**新用例，见 T2148/T2153）。**零删断言、零放宽**；**"明确不改"清单（如实登记，逐条给理由）**：`tests/adversarial/**`（用例体零改动）、`tests/unit/test_no_vendor_literals.py` 的**非形态面**断言（形态枚举面**已纳入** T2146 的委派面 ⇒ 不算"不改"）、`tests/contract/test_calibration_contracts.py`（010 的逐字节与"昂贵动作计数为 0"机检 ⇒ 必须继续通过）、`tests/contract/test_pilot_film_contracts.py`（**若** T2196 普查判定其函数体内枚举须委派，则按"委派 + 逐形态声明期望值"处置，**断言体与判据不削弱**，并把该文件从"明确不改"移入"已按扩展更新"——**如实登记，不掩盖**）。

- [ ] T2182 [P] **文档收口：`README.md` 新增本特性章节** — 改 `README.md`（新增章节 + 交叉引用 `:514` 的 020 章节与 `:992` 的 015 短剧章节） — **完成判据**: 新增"形态插件扩展性验证（功能 021）"章节，内容含——声明面 `evaluators.plugins.<agent>.<slot>.<evaluator_id>.{impl, version, params}`、"**配置声明集 = 可用插件全集**、目录不决定可用性"、唯一装配点与通用参数通道、扫描面补面（含 `agents/pilot`）与**形态名由 `configs/*.yaml` 派生**、三副本委派收敛与**全仓形态枚举普查清零**、五处登记点与登记完备口径、接入改动清单与**三步验收流程**、CLI 四子命令、**形态无关的**离线九步演示、**诚实分层**（"机制已就绪 / 广告与漫剧的业务定义未标定"）；并**明文写明**"机制侧六项总账**不得**表述为零代码改动"（FR-013 末句）；措辞与 `specs/021-form-plugin-validation/quickstart.md` 的对应段**逐字一致**。

- [ ] T2183 [P] **文档收口：`docs/三期立项书.md:167` 的 G5 行标注交付状态** — 改 `docs/三期立项书.md`（G5 行 `:167`；同批复核 `:212` 的周 9~12 里程碑行） — **完成判据**: G5 行按 G4 行（`:166`）的同一体裁标注交付状态；验收列**逐条如实登记**——① "**新形态接入 = 仅新增配置 + 评估器插件**"标注**机制已就绪**（两次接入改动清单 `counts["既有模块被修改"] == 0`，基线 = 机制落地 ref；机制件含**形态无关**的演示脚本，见 I-09）；② "**静态断言无形态分支**"标注**两层常驻通过 + 扫描面含 `agents/pilot` + 形态名由配置派生（人工常量清单数 0、枚举命中数 0）**；并登记**未交付面**（广告/漫剧的真实业务定义与真实节律量纲，属业务侧输入，见本清单"未覆盖项"）；`:212` 的里程碑行把 G5 由"未启动"改为交付态。

- [ ] T2184 [P] **文档收口：`docs/pilot-upgrade-manifest.json` 的相关项复核** — 读/必要时改 `docs/pilot-upgrade-manifest.json`（`paths` 在 `:27` 起） — **完成判据**: 逐项核对 `paths` 下是否有与 G5 / 形态插件相关的条目；**如有** ⇒ 按本特性实际交付面更新其 `notes`（**不改 `credential_envs`、不新增变量、`status` 不假称 `delivered`**）并在 `notes` 写清"机制的哪一部分已交付、哪一部分未到位"；**如无相关项** ⇒ 在交付说明里**如实登记"无相关项"**并给出核对命令（`grep -nE "form|plugin|形态" docs/pilot-upgrade-manifest.json` 的实测输出）；两种情形**都必须**保持 `uv run pytest tests/unit/test_pilot_upgrade_path.py -q` 绿。

- [ ] T2185 [P] **文档收口：`specs/021-form-plugin-validation/quickstart.md` 的"验证记录"回填** — 改 `specs/021-form-plugin-validation/quickstart.md`（`## 验证记录` 段，**按标题定位**——该文件在并行修订中已重排，不引行号） — **完成判据**: ① 只回填**已实跑**的结果——B1 的 `guard` 退出码、B2 的 `registration` 与 `sync-versions --check` 退出码、B3 的 `onboarding` 退出码与 `counts`、B4 的九步演示退出码与用时（并注明 `--out` 用临时目录），逐条给命令原文 + 实测值；② **未跑**的（覆盖率 / 契约两条腿 / 集成 / 对抗 / 无偏性 / 全量 unit）**如实留白**并按执行者分工标注"由父代理在宿主机执行"；③ **不得**改动该文件的 A/B 组命令与"如何判定零代码改动"九步；**"机制侧总账"表**由 **T2202** 同步（**本任务不再禁止改该表**，但**不得**在本任务里顺手改它——两件事分开做、分开复核）；④ 页首"当前状态如实标注"段按实际从"尚未落地"更新为落地态（**只改状态描述，不改命令与结论口径**）。

- [ ] T2186 [P] **机制侧总账与本清单的逐条一致性核对（交付前）** — 落点：`ops/form_onboarding.py` 的 `MECHANISM_LEDGER_PATHS` + `specs/021-form-plugin-validation/quickstart.md` 的"机制侧总账"表（**按表头 `#/路径/kind/服务哪一项总账` 定位，不引行号**——该文件在并行修订中已重排） — **完成判据**: ① 路径列**集合相等**（C13 机检 4 的常驻用例；**不写死条数**，条数由常量给出）；② **I-05：分解口径改为按 FR-013 的六项总账归属（不是实现阶段号）**，逐项给出路径集合（**集合**，不给条数）——① 唯一装配点与声明面与 A1 的**夹具同步面**：`core/evaluators/plugin.py`、`core/evaluators/errors.py`、六个 `agents/<agent>/evaluators/plugins.py`、六个装配函数所在文件、两份既有配置、`core/calibration/config.py`、`tests/unit/test_evaluator_plugin_assembly.py`、`tests/contract/test_plugin_contracts.py`、`tests/unit/fixtures/evaluator_assembly_baseline.json`、`tests/unit/test_form_no_new_dependency.py`、`tests/unit/fixtures/dependency_baseline.json`、**夹具同步面**（`tests/unit/test_{sound,screenplay,storyboard,editing,dev,visual}_composite.py`、`tests/contract/test_{dev,screenplay,storyboard,editing,sound}_contracts.py`、`tests/unbiasedness/**` 的实际命中文件）；② 扫描面补面：`ops/form_guard.py`、`tests/unit/test_form_guard.py`、`tests/unit/test_form_switch.py`、`tests/unit/test_billing_core_purity.py`、`tests/unit/test_dev_core_degraded_purity.py`；③ 形态名派生 + 三副本收敛 + **枚举普查清零**：`ops/form_guard.py`（与②并集去重）、`tests/contract/test_pilot_contracts.py`、`tests/unit/test_billing_channels.py`、`tests/contract/test_billing_contracts.py`、`tests/unit/test_pilot_rehearsal.py`、`tests/unit/test_no_vendor_literals.py`，以及 **T2196 普查后须委派的同族副本**（逐条以普查清单为准）；④ 裸词收敛 + 020 口径逐项机检：`agents/pilot/pilot.py`、`tests/unit/test_form_clause_completeness.py`；⑤ 登记完备口径：`tests/unit/test_form_registration.py`、`tests/unit/test_form_switch.py`（与②并集去重）、`tests/unit/test_config_integrity.py`、`tests/conftest.py`、`tests/unit/test_pilot_chain_seven.py`、`ops/form_onboarding.py`（登记点白名单面）；⑥ 接入改动清单机检：`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py`、`tests/unit/test_form_onboarding.py`、`tests/contract/test_form_onboarding_contracts.py`；③ `ops/form_guard.py`（②③）与 `tests/unit/test_form_switch.py`（②⑤）各**只算一条**；④ `tests/unit/test_calibration_config.py` **不在**总账内；⑤ 两条新形态配置（`configs/ad.yaml` / `configs/animated.yaml`）与其新增插件**不在**总账内（它们属**接入改动**）。

- [ ] T2187 **阶段 10 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_form_onboarding.py tests/contract/test_form_onboarding_contracts.py -q` 绿（含"quickstart 表 vs `MECHANISM_LEDGER_PATHS` 集合相等"的用例）；② `uv run pytest tests/unit/test_pilot_upgrade_path.py tests/unit/test_config_integrity.py tests/unit/test_form_switch.py tests/unit/test_form_no_new_dependency.py -q` 绿且用例数不减；③ 只读核对：quickstart 总账表的路径行集合与 `MECHANISM_LEDGER_PATHS` **相等**（打印两侧集合差集，必须为空；**不比对条数**）；④ 只读核对文档面：`README.md` 新章节存在、`docs/三期立项书.md:167` 的 G5 行已标注交付状态、交付说明含**宪章条款声明**与**复杂度论证核销**两段（**内容人工复核、措辞与 quickstart 一致**）。

- [ ] T2200 [P] [US3] **交付说明声明触及的宪章条款与合规方式（宪章评审 MUST）** — 落点：交付说明（与 T2199 的证据登记同一处）+ `README.md` 的本特性章节（T2182） — **完成判据**: ① **逐条**声明**触及的宪章条款 + 本特性的合规方式**，至少覆盖：**原则一**（`evaluator_id@version` 全局唯一、同 id 同 version 重复注册被拒、行为变更必升版本号 ⇒ 由 T2109/T2110/T2111/T2121/T2160 承载）、**原则二**（节点不可变、零迁移、既有 `eval_breakdown`/得分/成本入账零回改 ⇒ 由 T2106/T2111 承载）、**原则三**（一切 LLM 调用经网关、昂贵动作仅限线上探索 ⇒ 由 T2164/T2170「不声明 judge」+ T2166「不构造网关」+ T2175 的零花费断言承载）、**原则四**（沙箱隔离与前缀不泄露 ⇒ **不触及**，**必须写明理由**："本特性无新增策略执行面、不触模拟器、不引入新的执行路径"）、**原则五**（单向依赖 + 形态差异经配置表达 + 例外只能是"新增配置项" ⇒ 由 T2112/T2124/T2128/T2159/T2164/T2170 承载）、**原则六**（口径必须可被证伪 + 局限如实标注 + 工作流门禁"新增评估器必须同时提交单元测试、注册元数据含 `cost_per_call`、与既有评估器的对比样本" ⇒ 由 T2155~T2161/T2127/T2135/T2137/T2145/T2142/T2148/T2152/T2165/T2199 承载）；② **"不触及"的条款也必须显式声明并给理由**（**不得**省略整条）；③ 该段与 `specs/021-form-plugin-validation/plan.md` 的"宪章检查"表**逐条对应**（条款编号与结论一致）。

- [ ] T2201 [P] [US3] **复杂度论证核销（对照 `plan.md` 的复杂度跟踪表逐项闭合）** — 落点：交付说明（与 T2200 同一段） — **完成判据**: 对照 `specs/021-form-plugin-validation/plan.md` 的"复杂度跟踪（新增抽象论证）"表**逐项闭合**——`core/evaluators/plugin.py`（唯一装配点）、`core/evaluators/plugins/`（新目录）、6 个 `agents/<agent>/evaluators/plugins.py`（薄工厂）、新顶层配置段 `evaluators`、`ops/form_guard.py`（单一实现）、`ops/form_onboarding.py` + `ops/form_plugin.py`、`ops/demo_form_plugin.py`（**形态无关**）——每项给出三栏：**落地路径**（实际文件，与 T2158 的 ledger 一致）/ **为什么既有能力不足** / **被否决的更简方案**；**零新增第三方依赖**由 T2197 的常驻用例举证（不是口头承诺）；若实现期新增了 plan 未列出的抽象 ⇒ **必须补登**并说明理由（新增即须论证）。

- [ ] T2202 [P] [US3] **quickstart 的"机制侧总账"表核对与补齐（集合相等口径）** — 改 `specs/021-form-plugin-validation/quickstart.md`（"机制侧总账"表，**按表头 `#/路径/kind/服务哪一项总账` 定位**） — **完成判据**: ① **先核差集、再补齐**：该表**已由并行修订补入 A1 夹具同步面**——当前实测 **55** 行（编号 1~55 连续，其中 **41~55 = 夹具同步面**），而契约 `specs/021-form-plugin-validation/contracts/onboarding-ops.md` 的 `MECHANISM_LEDGER_PATHS` 段实测 **56 条** ⇒ **两侧存在 1 条的差集待收口**——**必须打印集合差集**（`表 \ 常量` 与 `常量 \ 表` 两个方向）并**逐条判定哪一侧缺**，再补齐；**不得**只比条数、**不得**以"改数字对齐"的方式糊过去（判据 = **集合相等**，C13 机检 4；**条数由常量给出、不写死**）；② 差集若落在 **I-04** 的两面则按实测补齐——**A1 的夹具同步面**（**穷举口径 = "凡在 `tests/**` 内调用六个 `build_*_evaluators` 的测试文件"，由符号调用反查**；实测 **16 个文件**、其中 `tests/conftest.py` 归 ⑤ ⇒ 子表 **15 条**；**注意：`tests/unit/test_visual_composite.py` 不存在**，visual 的装配调用点在 `tests/unit/test_visual_consistency.py`——该偏差已由并行修订如实登记）与**新增基线夹具**（`tests/unit/fixtures/evaluator_assembly_baseline.json`），以及 T2197 的两个新文件（`tests/unit/test_form_no_new_dependency.py`、`tests/unit/fixtures/dependency_baseline.json`）；③ 每行的 `kind` 列（**new** / **modified**）逐条正确，且 `ops/form_guard.py` 与 `tests/unit/test_form_switch.py` 各**只出现一行**（去重纪律）；④ 该表"服务哪一项总账"列与本清单 **T2186②** 的六项归属**逐条一致**（I-05：按 FR-013 归属，不按实现阶段号）；⑤ **只改该表及其紧邻的说明文字**（不改 A/B 组命令、不改"如何判定零代码改动"九步）；⑥ **契约侧已同步，无需再上缴**：`MECHANISM_LEDGER_PATHS` 段与 C13 机检 4~6 已写明"**不写死条数**（当前实测 56 条仅作对照）、判据 = 文档表与常量的**集合相等**、夹具同步面**由符号调用反查**"；本任务按该口径收口并**回填差集的判定结论**（谁缺、补了什么）。

**检查点**: ✅ 19 项变红清单**逐项核销**（零删除、零放宽）；文档收口（README / 三期立项书 G5 行 / pilot-upgrade-manifest / quickstart 验证记录）；**quickstart 总账表与 `MECHANISM_LEDGER_PATHS` 集合相等**（含 A1 夹具同步面与新增基线夹具，条数不写死）；**宪章条款声明与复杂度论证核销**两段齐备（"不触及"的条款也给理由）；机制侧与接入侧的分账在文档面**不得混同**。

---

## 验收与复核

**⚠️ 执行者硬约束**: 本节 T2188~T2193 **六类慢门禁全部由父代理在宿主机执行**——**子代理不得跑**（会超时）。T2194/T2195 为可本地执行的结果核对与口径复核。

- [ ] T2188 **【由父代理在宿主机执行】** 覆盖率门禁（口径不降，含 `web`） — 命令原文：`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85` — **完成判据**: **0 失败**且 `TOTAL` 覆盖率 **≥ 85%**（含 `web`）；覆盖率口径**不因本特性降低**；新增模块（`core/evaluators/plugin.py`、`core/evaluators/plugins/**`、六个 `agents/<agent>/evaluators/plugins.py`、`ops/form_guard.py`、`ops/form_onboarding.py`、`ops/form_plugin.py`、`ops/demo_form_plugin.py`）均有单元用例覆盖；跑完仓库根**无运行期产物残留**。

- [ ] T2189 **【由父代理在宿主机执行】** 契约两条腿（含 stub 口径） — 命令原文：`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q` — **完成判据**: 两条腿**均全绿**（skip 仅允许"真实实现无凭证按用例跳过"的既有口径）；`tests/contract/test_pilot_contracts.py` 的用例数**不减少**且 `:434-453` 的固定 **15 键**差异集断言**逐字在位**；`tests/contract/test_billing_contracts.py` 的 `FORMS`（`:97`）改派生后用例数不减；`tests/contract/test_plugin_contracts.py` 与 `tests/contract/test_form_onboarding_contracts.py` 两个新文件全绿。

- [ ] T2190 **【由父代理在宿主机执行】** 集成门禁 — 命令原文：`uv run pytest tests/integration -m integration` — **完成判据**: 全绿；**本特性新增的离线用例**（`tests/integration/test_new_form_onboarding_offline.py`：B1 的装配/评估/合成分数/留痕 + B2 的两形态并跑共用插件代码 + T2199 的回放对比证据）**全绿且不依赖真实凭证**；既有集成用例**零改动**（本特性**零 DB 变更、零迁移**）。

- [ ] T2191 **【由父代理在宿主机执行】** 对抗测试门禁（**合并阻塞，不放松**） — 命令原文：`uv run pytest tests/adversarial -m adversarial` — **完成判据**: 全绿；**本特性对 `tests/adversarial/**` 的用例体零改动**（按 T2196 的普查结论：若该目录确有形态集合字面量 ⇒ 该结论**改为"已按扩展更新"**并如实登记）；**执行方式必须串行**——该套件**不具备并行安全性**：与全量 `tests/unit`（含起容器的用例）或 `tests/integration` 并跑时，其 autouse"无孤儿容器"teardown 断言会因并发的 `cineflow-sandbox-*` 容器**假红**（020 已实测复现，**后续门禁一律串行执行**）。

- [ ] T2192 **【由父代理在宿主机执行】** 无偏性门禁（发布阻塞，**不放松**） — 命令原文：`uv run pytest tests/unbiasedness -m unbiasedness` — **完成判据**: 全绿；本特性对 `tests/unbiasedness/**` 的**断言体零改动**（仅 T2125 的夹具同步面可改，如实登记）；新增形态**不得**触碰无偏性判据（`core/deployment/evidence.py:97` 的 E1 行**原样保留**、`deployment.gate.require_unbiasedness` 口径不变）。

- [ ] T2193 **【由父代理在宿主机执行】** 静态检查双绿（**本仓 CI 逐字一致**） — 命令原文：`uv run ruff check . && uv run ruff format --check .` — **完成判据**: 两条命令均退出 0（`ruff check` 零告警、`ruff format --check` 零待格式化文件）；**注意扫描面真实性**：本特性新增件必须落在 ruff 的树扫描面内（不得被 `.gitignore` 规则吞掉——020 曾因裸 `billing/` 规则使 `core/billing/` 整包逃逸，已改根锚定；本特性新增的 `core/evaluators/plugins/` 与 `tests/unit/fixtures/**` 必须实测在扫描面内）。

- [ ] T2194 **离线端到端演示与产物核对（可本地执行）** — 命令：`uv run python ops/demo_form_plugin.py --form ad --out "$(mktemp -d)"` 与 `--form animated --out "$(mktemp -d)"`（**I-07：`--out` 必须是 `tmp_path` 类临时目录**，与 T2166③ / T2175⑤ 统一） — **完成判据**: **退出码 0**（九步全 ok）；仓库根**零新增文件**（前后未跟踪文件集合相等）、**零真实花费产物**、**零外部网络**（无凭证亦可跑）；逐件核对产物——`onboarding-<form>-<seq:04d>.json`（含 `baseline_ref` / `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]` / `counts`（含 `既有模块被修改`）/ `violations[]` / `mechanism_changes_included` / `zero_code_onboarding` / `exit_code`）、`index.jsonl`（每行**六产物键**、append-only）、`guard-report.json`（两层分别计数 + 例外三条判定）、`registration-report.json`（五处逐一 + 登记完备三条 + 无第六处）、`demo-report.json`（`network: "none"` / `credentials_required: false` / `uncalibrated: true` / `uncalibrated_reason` 非空）；对照 `specs/021-form-plugin-validation/quickstart.md` 的"如何看产物"表逐件核对。

  **勘核（待填字段；由 T2195 收口，未跑即留空并注明"未跑"）**: 对每个形态填 `{form, exit_code, steps_ok（九步逐条布尔）, counts_既有模块被修改, violations_len, network, credentials_required, uncalibrated, uncalibrated_reason, elapsed_seconds}`；本字段**不接受**"（留给实现批次）"一类占位——**未跑就写"未跑"**，并由 T2195 核对本字段已填或已明确留白。

- [ ] T2195 **SC 映射、统计与一致性复核（本清单的自检出口）** — 无新文件 — **完成判据**: ① 逐条核对 `specs/021-form-plugin-validation/spec.md:142` 起的 SC-001~SC-009 **均有承载任务**（见下表"SC 映射"）；② 核对**全部 14 条 FR**（`specs/021-form-plugin-validation/spec.md:112`~`:125`）在任务里有落点；③ 统计"任务总数 / 各阶段条数"与实际条目一致（含 T2166 的**位置前移**与 T2196~T2202 的**补号**）；④ 核对每条任务的**文件路径真实存在**（新增件路径写全且可被创建）且**无 `path:line` 引用越界**；⑤ 核对 **T2194 的勘核字段**已填或已明确留白（**A-05 的收口**）；⑥ 登记"未覆盖项"（见文末）。

### SC 映射

| 成功标准 | 承载任务 |
| --- | --- |
| SC-001 里程碑验收（① 接入改动清单"既有模块被修改 == 0" ② 零形态分支两层常驻 + 扫描面含 `agents/pilot`） | T2130 / T2131 / T2134 / T2167 / T2173 / T2183 / T2188~T2195 |
| SC-002 判据可机检率 100%（清单与 git 一致率 100% / 越界逐条 + 退出码非 0 / 只报总数次数恒 0） | T2155 / T2156 / T2157 / T2167 / T2173 |
| SC-003 静态断言盲区为 0（`agents/pilot` 在扫描面内且注入即红 / 形态名由配置派生 / 人工常量清单数 0 / 副本数 1 / **枚举命中数 0** / 符号名锚点 / 既有断言语义零削弱 / `:613` 收敛率 100%） | T2127 / T2128 / T2130 / T2131 / T2132 / T2133 / T2134 / T2135 / T2136 / T2196 / T2198 |
| SC-004 插件业务无关率 100%（AST 零 import / 零形态字面量与分支 / 零环境变量与网络 / 参数来自 `params` 100%） | T2110 / T2165 / T2171 |
| SC-005 插件契约强制率 100%（一一对应 / 缺声明即报错 / 同键重复注册被拒 / 非确定性被拒 / 元数据与 `version` 缺失即报错） | T2109 / T2110 / T2112 / T2113 / T2120 / T2124 / T2160 / T2165 |
| SC-006 五处登记点完备率 100% + 不新造第六处 + 预检缺项拒绝启动率 100% + 登记完备口径（两两唯一 / ⊆ / ≥2 / "恰好两份"未删） | T2137 / T2138 / T2139 / T2140 / T2141 / T2142 / T2143 / T2144 / T2145 / T2146 / T2168 / T2176 |
| SC-007 020 新增口径声明完备率 100%（cadence ∈ {1,7} / 窗口口径与生效日 / 渠道命名空间 / 归属日生效日 / 迁移六键 / 运行窗口下限与断档容差 / "不适用"显式声明 / 既有两形态取值零改动） | T2148 / T2149 / T2150 / T2151 / T2152 / T2153 / T2154 / T2164 / T2170 |
| SC-008 离线端到端证据 1 条（退出码 0 / 零花费 / 零网络 / 零凭证 / 未标定标注缺失恒 0） | T2163 / T2166 / T2175 / T2177 / T2194 / T2199 |
| SC-009 常驻门禁不放松（覆盖率 ≥85% 含 web / 四道门禁 / 既有两形态 `eval_breakdown` 与得分逐字节不变 / 019·020 断言只增不减 / **零新增运行时依赖**） | T2111 / T2121 / T2126 / T2178 / T2181 / T2197 / T2200 / T2201 / T2188~T2193 |

### 任务总数 / 各阶段条数

| 阶段 | 任务号区间（含补号） | 条数 |
| --- | --- | --- |
| 阶段 0：前置与勘查核对（只读） | T2101~T2108 + **T2196** | 9 |
| 阶段 2（A1）：插件声明面与唯一装配点 | T2109~T2126 | 18 |
| 阶段 3（A2）：扫描面补面 / 形态名派生 / 三副本收敛 / 裸词收敛 | T2127~T2136 | 10 |
| 阶段 4（A3）：五处登记点派生 + 登记完备 + 枚举普查清零 | T2137~T2147 + **T2198** | 12 |
| 阶段 5（A4）：020 口径完备与 cadence 收口 | T2148~T2154 | 7 |
| 阶段 6（A5）：接入改动清单 + CLI + 形态无关演示 | T2155~T2162 + **T2166** + **T2197** | 10 |
| 阶段 7（B1）：广告形态接入 | T2163~T2165 + T2167~T2169 | 6 |
| 阶段 8（B2）：漫剧形态接入 | T2170~T2174 | 5 |
| 阶段 9（B3）：离线端到端 / 登记与门禁同步 / 交付留痕 | T2175~T2180 + **T2199** | 7 |
| 阶段 10：文档与门禁同步 | T2181~T2187 + **T2200/T2201/T2202** | 10 |
| 验收与复核（含 6 条**由父代理执行**的慢门禁） | T2188~T2195 | 8 |
| **合计** | **T2101~T2202**（连续无缺号；含 7 条跨阶段补号与 1 处位置前移） | **102** |

**号段与位置的登记（如实说明）**: ① **T2166（形态无关演示）由 B1 前移到 A5**（I-09 裁决）——**编号保留**，故它在本表中计入阶段 6 的条数、在文档里位于阶段 6 之末；② **T2196 / T2197 / T2198 / T2199 / T2200 / T2201 / T2202 为跨阶段补号**（镜像 020 的 T2086 先例），文档位置分别在阶段 0 / 6 / 4 / 9 / 10（三条）——**编号与文档位置的这两类不一致都是刻意的**，避免重编 95 条既有任务号与全部交叉引用；③ 统计口径 = **按文档位置归位计数**，合计 102 条。

### 未覆盖项（如实说明）

1. **广告 / 漫剧的真实业务定义不可在本特性内达成**（业务侧输入，开放问题 1）：受众、指标口径（用什么信号衡量该形态质量）、素材规格（时长/尺寸/格式）、评估器组合的**业务正确性**、平台名与预算数字——本清单只交付"**机制可跑通 + 如实标注未标定**"（T2164 / T2170 / T2166 / T2152），**不发明任何业务数字**；"最小可行形态"的签字形式需**人工裁决**。
2. **广告 / 漫剧的真实节律是否落在 `calibration.period_days ∈ {1,7}` 内**（业务侧输入，开放问题 2）：本特性**不扩量纲**（`core/calibration/periods.py:30` 一字不改，T2149 / T2154 有常驻断言）；若业务侧确认确需其他量纲（如双周/月）⇒ **必须另立特性**，本特性**不代劳**、**不得**用"按周近似 + 如实标注"含糊兜底（`calibration.cadence_note` 只允许登记**近似关系**，T2148 / T2150 有机检）。
3. **真实投放与真实素材生成不在本特性范围**：B/C 路径不变（`docs/三期立项书.md:212` 的 `not_delivered` 口径**不放宽**）；不做多租户、不做公网服务化；新形态**不声明 judge** ⇒ 离线演示**不构造 `LLMGateway`** ⇒ `OFFLINE_ASSEMBLIES` 与构造点计数 `len(sites) == 14` **一律不动**（T2178② **写死，无条件分支**；若实现期确需构造网关，须先上缴裁决）。
4. **`ops/` / `web/` / `dreaming/` 的既有形态字面量与配置路径默认值不改写**：它们**不在**扫描面内（扫描面只有 `core/` + `agents/`），且**不得**为过断言而改写（T2134 有常驻断言）——这是**刻意的范围边界**，不是遗漏。
5. **`promo` 构造点的插件化不在本特性改动面内**：规格只点名**六个**装配点；`agents/promo/` 的构造点不在改动面，但**零形态字面量/分支守卫覆盖 `agents/promo/`**（扫描面 = `core/` + `agents/` 全覆盖）。
6. **`tests/adversarial/**` / `tests/contract/test_calibration_contracts.py` 零改动**（如实登记"明确不改"清单，T2178 / T2181 / T2191 / T2192）；**`tests/unit/test_no_vendor_literals.py` 与 `tests/contract/test_pilot_film_contracts.py` 是否"已按扩展更新"**由 T2196 的普查结论决定（T2181 的清单随之更新，**不预填结论**）。
7. **实际门禁执行结果不预填**：覆盖率 / 契约两条腿 / 集成 / 对抗 / 无偏性 / ruff 双绿的**实测数值**由父代理在宿主机执行后回填（T2188~T2194 / T2185）；quickstart 的验证记录区同理**只回填已实跑结果**；T2194 的勘核字段**未跑即写"未跑"**（A-05）。
8. **`docs/pilot-upgrade-manifest.json` 是否有 G5 相关项待核**（T2184）：核对命令已给；**如无相关项则如实登记"无相关项"**，**不**为凑数新增条目、**不**改 `credential_envs`。
9. **机制侧总账：条数是派生值、两侧差集待收口**（I-04）：契约 `specs/021-form-plugin-validation/contracts/onboarding-ops.md` 已明确"**不写死条数**（当前实测 **56 条**仅作对照）、判据 = 文档表与常量的**集合相等**、**夹具同步面由符号调用反查**"；**A1 的夹具同步面（实测 16 个命中文件，其中 `tests/conftest.py` 归 ⑤ ⇒ 子表 15 条）与新增基线夹具（`tests/unit/fixtures/evaluator_assembly_baseline.json`）已并入总账 ①**；**quickstart 表也已由并行修订补入夹具同步面（当前实测 55 行、编号 1~55 连续）** ⇒ **两侧现存 1 条的差集**（表 55 行 vs 常量实测 56 条），**由 T2202 先打印双向差集、逐条判定谁缺再补齐**（T2156④ / T2162① / T2187③ 的集合相等用例在该差集收口前**预期红**——如实登记，不预填结论、不预判谁对）。
10. **`tests/adversarial/**` 是否受 T2146/T2198 影响**：**未预判**（T2196③/T2196④ 要求给出结论与核对命令）；若受影响 ⇒ 该目录的相关文件进入委派面，并把 T2178①/T2191 的"零改动"结论**如实更改为"已按扩展更新"**。

---

## 依赖关系与执行顺序

### 阶段依赖（含跨阶段前置，如实登记）

- **阶段 0**：无依赖、可立即开始；**只读**，产出事实基线（**九项**，含 T2196 的全仓形态枚举普查）。
- **阶段 2（A1）**：依赖阶段 0 的事实基线（T2101 / T2105 / T2107）。**跨阶段前置（硬）**: T2111 的"改前装配序列快照"**必须先用**，否则 T2120 的函数体替换无法证明"逐字不变"（原则一/二失守）。**同文件串行**: T2124（两份 `configs/*.yaml`）→ T2129（A2，同文件）。
- **阶段 3（A2）**：依赖阶段 2 的**不重叠面**可并行；**但** T2131/T2132 触碰 `tests/unit/test_*` 与 `tests/contract/*`，与阶段 4 的 T2138~T2141 **同文件 ⇒ 必须先于阶段 4 的对应条目**（A2 → A3 的串行点在 `tests/unit/test_form_switch.py` 与 `tests/contract/test_pilot_contracts.py`）。**跨阶段前置（硬）**: `declared_forms()` / `form_literals()`（T2128）与 `form_aliases`（T2129）是 A3/A5 的**唯一派生面**。**同文件串行**: T2133（`agents/pilot/pilot.py`）→ T2142（A3）→ T2152（A4）。
- **阶段 4（A3）**：依赖阶段 3 的 T2128 / T2129（派生面就位）；**T2198 依赖 T2145（白名单/反向扫描实现）与 T2196（普查清单）**；T2146 依赖 T2196。
- **阶段 5（A4）**：T2149（`core/calibration/config.py`）与 T2148（新测试文件）可并行；T2150~T2152 依赖 T2142 的 `form_clause_completeness` 落地（**同文件 ⇒ 与 T2133/T2142 串行**）。
- **阶段 6（A5）**：依赖 A1（装配集合↔权重键集）、A2（派生面）、A3（登记点白名单）、A4（预检）**全部完成** ⇒ 是 A 的最后一段；T2158 的总账路径集合依赖 A1~A5 的最终文件清单（含 T2107 的夹具穷举清单）；**T2166 必须在机制落地 ref 之前落地**（I-09）；T2156④/T2162① 的"文档面集合相等"用例依赖 **T2202**（阶段 10）⇒ 该两处在阶段 6 结束时**预期仍红**（TDD 序，如实登记）。
- **B 侧（阶段 7~9）**：**必须**在 A 全部落地并**打一个可引用的 ref**（= `mechanism_ledger_ref`）之后开始——否则"接入改动清单"会把机制侧总账算成越界（判据自相矛盾）；且 **B 侧的接入改动集必须不含 `ops/demo_form_plugin.py`**（它已是机制件）。
- **阶段 10**：依赖 B 侧完成（19 项核销与文档收口以最终代码为准）；**T2202 必须在 T2186/T2187 之前完成**（先同步表、再核集合相等）。
- **验收与复核**：依赖阶段 10；T2188~T2193 **由父代理在宿主机执行**。

### 并行机会

- **阶段 2**：T2109/T2110（两个新测试文件）可并行；**T2114~T2119（六个 `agents/<agent>/evaluators/plugins.py`）六份互不重叠、可并行**；T2121/T2122 可并行。**串行点**：T2111 → T2120。
- **阶段 3 与阶段 2 部分并行**：`ops/form_guard.py`（T2128）与 `core/evaluators/**`（T2112）**文件不重叠 ⇒ A1 与 A2 可并行推进**；A2 内部 T2128 → T2129 → T2130 → T2131 → T2132 → T2133/T2134 串行（T2133 受"同文件串行"约束，须先于 T2142）。
- **阶段 4**：**五处登记点**（T2138 / T2140 / T2141 / T2142 / T2144）+ **连带面**（T2145 / T2146 / T2198）＝ **八处文件互不重叠、可并行**（五处依赖 T2128/T2129；T2146 依赖 T2196；T2198 依赖 T2145 与 T2196）。
- **阶段 5**：T2148 / T2150 / T2151 / T2153 可并行；T2149（`core/calibration/config.py`）单文件；T2152（`agents/pilot/pilot.py`）与 T2133/T2142 串行。
- **阶段 6**：T2155 / T2156（两个新测试文件）可并行；T2158 与 T2159 / T2160 可并行；T2166（演示）与 T2197（依赖快照用例）文件不重叠、可并行。
- **阶段 7 / 8**：两份形态配置与其插件**可并行**（T2164 / T2165 与 T2170 / T2171 不同文件）；**交汇点 = T2172（两形态并跑）与 T2173/T2174**（B2 侧的清单、登记与核对需 B1 与 B2 都就位）；演示脚本已在 A5 落地 ⇒ **B1/B2 零脚本改动**。
- **阶段 10**：T2182 / T2183 / T2184 / T2185 / T2200 / T2201 / T2202 **七处文档面互不重叠、可并行**（T2202 须先于 T2186/T2187）；T2181（19 项核销）与 T2186（总账一致性）需最后做（以最终代码与最终常量为准）。

### MVP 优先（US1）

1. 阶段 0 → 阶段 2（A1）→ 阶段 3（A2）→ 阶段 6（A5：清单 + CLI + **形态无关演示** T2166）**不可跳过**——A1 打开路径，A2 补齐守卫，A5 给出可审计的判据与**唯一演示入口**。
2. 独立验证：`uv run pytest tests/unit/test_evaluator_plugin_assembly.py tests/contract/test_plugin_contracts.py tests/unit/test_form_guard.py -q` + `uv run python ops/demo_form_plugin.py --form movie --out "$(mktemp -d)"`。
3. 此时交付"**新形态仅新增配置 + 插件即可跑通（演示零改动），且接入改动可逐条审计**"的价值（SC-001 / SC-002 / SC-005）。

### 增量交付

1. A1（阶段 2）→ 配置驱动的插件声明与唯一装配点（FR-001/FR-002/FR-012；SC-004/SC-005）
2. A2（阶段 3）→ 零形态分支守卫补面与形态名派生（FR-005/FR-006；SC-003）
3. A3+A4（阶段 4/5）→ 五处登记点派生、枚举普查清零与 020 口径完备（FR-007/FR-008；SC-003/SC-006/SC-007）
4. A5（阶段 6）→ 接入改动清单、CLI 与形态无关演示（FR-003/FR-004/FR-010/FR-014；SC-002）
5. B1+B2+B3（阶段 7~9）→ 两个形态零代码接入 + 离线端到端证据 + 回放对比证据（FR-010/FR-011；SC-001/SC-008）
6. 阶段 10 + 验收 → 文档收口、宪章条款声明、复杂度核销与常驻门禁（SC-009）

## 备注

- **原则一落点**（评估器确定性与版本冻结）: T2109（三项一致性）/ T2110（同键重复注册被拒、非确定性被拒）/ T2111（装配序列逐字相同 ⇒ 既有 `eval_breakdown` 不变）/ T2121（既有实现文件零改动，范围 = 单评估器实现模块）/ T2160（`version` 显式校验、**不允许覆盖实现**）。
- **原则二落点**（节点不可变与零回改）: 本特性**零 DB 变更、零迁移**（T2106 的事实基线）；发现树 / `CostRecord` / `eval_breakdown` / 得分**一律不动**；产物一律落 `--out`（**tmp_path 类临时目录**，T2157 / T2166③ / T2177）。
- **原则三落点**（昂贵动作与网关）: T2164 / T2170（最小形态**不声明 judge** ⇒ 结构性零花费）/ T2166④（演示**不构造 `LLMGateway`**、零凭证面）/ T2178②（`len(sites) == 14` **写死不动**）/ T2175（零花费、零网络、零凭证的计数量断言）。
- **原则五落点**（形态差异经配置表达、例外只能是"新增配置项"）: T2112（唯一装配点**业务无关**、零形态名/零 Agent 名/零分支）/ T2124（声明段是**新增配置项**）/ T2128（`ops/form_guard.py` 的形态名**是数据、不是分支**）/ T2159（CLI 只读 git 与文件）/ T2164 / T2170（新形态**只新增配置 + 插件**）；宪章合规方式的**显式声明**见 T2200。
- **原则六落点**（口径可证伪、局限如实标注）: T2155~T2161（清单逐条路径 + 类别 + 越界判定、越界退出码非 0）/ T2127 / T2135（注入即红的有牙齿自检）/ T2137 / T2145 / T2198（登记完备、不新造第六处、枚举命中数 0）/ T2142 / T2148（缺项即拒绝启动）/ T2152（三层"未标定"）/ T2166 / T2194（离线端到端证据与产物逐件核对）/ T2199（回放对比证据或 N/A 理由）。
- **本清单的"判断调用"（如实登记，不掩盖）**:
  1. **`ops/form_onboarding.py` 分两次落地**（T2145 落登记点面、T2157 落清单机制），以遵守 TDD 序与"先写测试"的纪律；`REGISTRATION_SITES` 的反向扫描（不新造第六处）属 A3 的验收面，故提前到阶段 4；**扩展反向扫描口径**（T2198）同在阶段 4。
  2. **A4 不单列第 7 项机制侧总账**（C13 的明文纪律）：`core/calibration/config.py` 归 ①、`agents/pilot/pilot.py` 与 `tests/unit/test_form_clause_completeness.py` 归 ④，使"六项"与 FR-013 保持一一对应（T2158 / T2186 有常驻机检）。
  3. **I-09 的落法 = "前移 + 形态无关"，不是"给清单开豁免"**：`ops/demo_form_plugin.py` 按 C13 表本就归 ⑥ 的 ledger 项，但**把它的创建留在 B1 会让 C12 判它越界**（新增 ops 文件）⇒ 本版把它的创建前移到 A5（T2166），并要求它遍历 `declared_forms()`（对新形态零改动）——**B1/B2 的接入改动集因此不含任何 ops 文件**，判据自洽；**被否决方案**：把 `ops/demo_form_plugin.py` 加进 `classify` 的放行白名单（那等于"为过判据改判据"，且会让"新增 ops CLI = 越界"这条纪律失效）。
  4. **`sync-versions --write` 的实现保留在 T2160，但门禁只跑 `--check`**：`--write` 仅服务人工回填，**禁止**以改写权威配置换取绿灯（C4 明文）。
  5. **两处待核事实（不预判）**：`docs/pilot-upgrade-manifest.json` 是否有 G5 相关条目（T2184）；`tests/unit/test_no_vendor_literals.py` / `tests/contract/test_pilot_film_contracts.py` / `tests/adversarial/**` 的形态枚举面是否需委派（T2196 普查给出结论后，T2181 的"明确不改"清单随之更新）。
  6. **A-06 的裁决与**被否决方案**（登记在案）**：形态特定期望值**入配置 + 断言读配置**（T2146③）；**否决**"建'形态 → 期望值'的映射登记表"——它与 C7 的"副本数恒 1"与 C9.7 的"禁止'形态 → 配置路径'映射常量"直接冲突。
  7. **I-04 的条数口径与两侧差集（与并行修订后的设计件一致）**：`MECHANISM_LEDGER_PATHS` 的**条数是派生值**（当前实测 **56 条**，仅作对照），判据是"**文档表与常量的集合相等**"；夹具同步面**由符号调用反查**（`tests/**` 内调用六个 `build_*_evaluators` 的文件集合，实测 **16 个**、其中 `tests/conftest.py` 归 ⑤ ⇒ 子表 **15 条**；`tests/unit/test_visual_composite.py` **不存在**，visual 的调用点在 `tests/unit/test_visual_consistency.py`）；**quickstart 表已由并行修订补入夹具同步面（实测 55 行）** ⇒ **现存 1 条差集**由 T2202 打印双向差集后收口（契约侧已同步，**无需上缴**）。
  8. **T2166 的编号保留 + T2196~T2202 的跨阶段补号**（编号与文档位置的两类不一致）是**刻意**的（镜像 020 的 T2086 先例），以避免重编 95 条既有任务号与全部交叉引用；统计按**文档位置**归位（合计 102 条）。
- **慢门禁纪律**: T2188~T2193 的命令**逐字**取自本仓既有 CI 口径与 `specs/020-shortdrama-real-feedback/tasks.md` 的 T2078~T2083 同源写法；**由父代理在宿主机执行**，子代理只跑各阶段的"快速核对"单文件子集。**对抗套件必须串行**（不具备并行安全性）。
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过。
