# 调研：电影长片全链路编排（018-feature-film-pipeline）

> 阶段 0 产出。每条决策 = 结论 + 理由 + 被否决的替代方案。规格依据见 [spec.md](spec.md)，
> 架构落点见 [plan.md](plan.md)。本特性是**接线与体量**的特性：不新造 Agent、不重写适配器，
> 把 017 的选题产出接到生产主线、把长片体量的三个真实障碍按裁决落地、把证据面（分量 / 标注 /
> 性能 / 成本三方）补齐到可机检。每条决策的**事实**均在撰写时逐条核实（file:line 见 plan.md
> 的"现状勘查"节）。

## 决策 1：落点与包边界 = 链与证据面留在 `agents/pilot/`，报告件新增 `run_report.py`

**结论**：七环节链、交接声明、样片包与预检全部改造在 `agents/pilot/{stages,pilot,handoffs,package,backends}.py`；
新增**两个**模块——`agents/pilot/run_report.py`（性能画像 + 成本第三方腿对账，落报告侧
`pilot/profiles/{run_id}.json`）与 `agents/pilot/scale.py`（派生镜头数**单一无环持有者**，纯计算叶子）；
**不**下沉 `core/`、**不**放 `ops/`。索引块网格参数化落
`agents/storyboard/`（`config.py` + `board_render.py`；`config.py` 只**读** `agents/pilot/scale.py` 的函数），
体量键落两形态 `pilot` 段（`pilot.scene_count` /
`lines_per_scene` / `rehearsal`），策略装载落 `agents/dev/policy_loader.py`。

**理由**：`core/orchestration` 是**零业务概念**的通用编排层（`core/orchestration/models.py:14-16`
明确其"只认识 stage_id、依赖、执行入口引用与产物引用"），把"七环节/五件套/排练档"塞进去即违反原则五；
`agents/pilot/` 恰恰是"试水编排"这一业务件的所在地，且它的形态无关性已由常驻静态断言守住
（`tests/unit/test_pilot_stages.py:127-137` 不新增落树路径、`:134-137` 无形态分支）。报告件必须是
**新文件**而不是塞进样片包：五件套逐字节可比是 015 的标定口径（FR-012），而性能画像含**墙钟耗时**、
账本腿含**窗口累计**，二者天然非确定——混进去即把"可复现"与"性能/账本口径"两件事搅在一起。
`ops/` 只作薄封装（CLI 与演示），因为它不在覆盖率口径内、也不能被 `agents/` import。

**被否决**：
- *下沉 `core/orchestration`*：通用层一旦长出"七环节"与"排练档"，跨形态复用与"新增目录先归 core 或
  agents"的分类纪律同时被破坏（原则五）。
- *把标注/分量/画像全塞进 `manifest.json`*：破坏五件套逐字节可比（FR-012/SC-008 直接失守）。
- *新包 `agents/pilot/chain/`*：只为一处链装配引入新目录层次，与既有模块边界（`stages.py` 就是链）
  重复，YAGNI。
- *策略装载从 `ops/dev.py:81 _load_policy` 取用*：撞常驻断言"`agents/` 不得 import `ops/`"
  （`tests/unit/test_pilot_stages.py:140-162`），且两条装载路径会让静态检查在一侧静默失效——而
  静态检查正是 017 例外条款（宪章 v2.0.0 原则四末条）成立的前提。

## 决策 2：`dev` 作为链首的插入方式与"清单同步"风险的消解

**结论**：`build_stage_specs`（`agents/pilot/stages.py` 当前 `:1018-1069`）首项插入 `dev` 段
（`stage_id="dev"`、`depends_on=()`、`entrypoint=_dev_entry`、`handoff=_handoff_dev`、`output_kind="slate"`），
`script` 的 `depends_on` 由 `()` 改为 `("dev",)`；`PILOT_STAGE_IDS`（当前 `:94`）同步为首位。必须**同批**改动的
七处清单（逐处 file:line 见 plan.md"现状勘查"第 1 条）：阶段元组、阶段表、`AgentConfigs`、
运行时装配（含 `agents/dev/db.py:60 create_jobs_schema` 建表）、`agents/pilot/pilot.py` 的**四处**
清单（`config_completeness` 加载器元组当前 `:117-131` / 权重循环当前 `:135-140` /
`AgentConfigs` 构造当前 `:198-205` / 预算循环当前 `:208-224`）、
`agents/pilot/package.py:173-181` 的产物 kind 与内容类型、以及两形态配置声明。消解办法不是"记得改"，
而是**加不变量断言**：`PILOT_STAGE_IDS` == 阶段表顺序 == 权重循环名单 == 加载器名单 == 两形态的
`evaluator_weights` 键集；`PILOT_STAGE_IDS` 的每一项都必须有产物 kind 登记、后端槽位映射
（`STAGE_BACKEND_SLOT`）与轮次树前缀（`STAGE_TREE_PREFIX`）。任一处漏改即**多条断言同时红**。

**关键事实（决定了预算面不能"一行复制"）**：`dev` **没有** `exploration_per_round_usd`
（`agents/dev/config.py:150-164` 只有 `model`/`model_prices`），而预检的既有循环对"无单轮预算但有
价目表"的 Agent 走的是 **LLM 腿档位**分支（`agents/pilot/pilot.py:212-221`）——`dev` 恰好落在该分支上
且其环节档**两形态已声明**（`configs/movie.yaml:537-541`、`configs/shortdrama.yaml:539-543`）。
**不得**为让预检"看起来整齐"而在配置里发明一个单轮预算数字。

**被否决**：
- *`dev` 挂在 `script` 之后或并行*：规格澄清 Q2 已裁决为**链首**（选题决定下游一切），且并行会引入
  依赖语义变化（编排器支持并行，但本特性维持串行，`StageSpec.depends_on` 逐环前一环）。
- *把 `dev` 写成 `script` 的一个内部步骤*：等于新增落树路径与第二份 017 实现，直接违 FR-002。
- *用"两套链"（长片链 / 短剧链）承载*：规格明禁"为长片新造第二套链"，且形态集被
  `tests/unit/test_form_switch.py` 钉死为两套配置。
- *让预检跳过 `dev`（少一环也能跑）*：正是 FR-001 禁止的"静默降级"，且 `dev` 的档位缺失会在切真实后端时才炸
  （SC-006 失守）。

## 决策 3：交接声明的形状 = `reads` 三类（承接含改名 / 运行级 / 派生）+ `dropped` 独立集 + 取数依据

**结论**（契约 C5）：在 `agents/pilot/handoffs.py` 新增双来源决策视图（**同名字段子集**的守恒层继续用
`FieldParity`，新增部分承担"来源分类 + 取数依据"层）：

```
DevScriptHandoff(
  mode,                       # "dev_script_handoff" | "run_level_pilot_inputs"（二值，无第三值）
  reads: {下游键: 类 ∈ {承接, 运行级, 派生}},   # 合计且每键恰一类（逐字段来源）
  renames: {下游键: 上游字段名},  # 承接映射：同名承接 = 恒等，改名承接（genre → topic）= 显式登记
  dropped: frozenset,         # 独立集合（与 reads 并列，不是同一分类的第四项）；承接 ↔ 丢弃互斥
  derived: frozenset,         # 派生键集合（不得占用上游/运行级/承接目标键名）
  sources: {"entries", "production_marks"},   # 取数依据（选条入口）：不进 reads、也不进 dropped
)
"上游" = 可移交要点集合（genre/constraints/characters），**不是**整张导出面
SCRIPT_INPUT_READS = {"topic", "target_duration_min", "constraints", "characters"}
守恒等式（本交接，契约 C5）：下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生
```

断言（构造即校验）：① `set(reads) == SCRIPT_INPUT_READS`（**合计且每键恰一类**；出现未声明读取键 ⇒
拒绝并点名该键）；② 读取集**源码扫描式锁定**（两式 `inputs[` / `inputs.get(` 扫描 + 下游 loop 的输入
归一口径 `agents/screenplay/loop.py:186-204` **双向绑定**，新增读取点未登记即红）；③ **同名字段子集层**沿用
既有 `FieldParity.consistent()`（`agents/pilot/handoffs.py` 当前 `:50-56`）：同名字段子集成立
`下游同名键集 == (上游 − 丢弃) ∪ 派生`；**承接/运行级层不复用 `consistent()`，另立断言 ①~⑥**（见契约 C6：
守恒等式、`renames` 覆盖承接类键、承接与丢弃互斥、派生键约束、取数依据登记）——两层**不互相放宽**；
④ `mode` 两值都必须声明（无 `dev` 的链必落
`run_level_pilot_inputs`），生效 `mode` **随机读可见**（`StageState.detail` + 预检报告）。

**本特性已核实的映射**（`上游` = `agents/dev/export_slate.py:26-35` 的 `EXPORT_ENTRY_FIELDS`；
`下游` = `SCRIPT_INPUT_READS` 四键）：

| 下游键 | `dev_script_handoff` 模式 | `run_level_pilot_inputs` 模式 |
| --- | --- | --- |
| `topic` | **承接** ← 被标记条目的 `genre`（**改名承接**，登记在 `renames`） | 运行级 ← `PilotInputs.topic` |
| `constraints` | **承接** ← 被标记条目的 `constraints` | 运行级 ← `PilotInputs.constraints` |
| `characters` | **承接** ← 被标记条目的 `characters` | 运行级 ← `PilotInputs.characters` |
| `target_duration_min` | 运行级 ← `PilotInputs.target_duration_min` | 运行级 ← 同名键 |
| 承接映射 `renames` | `topic ← genre`（改名）+ `constraints`/`characters`（恒等） | 无交接上游 ⇒ 空 |
| 丢弃集 | `direction_id` / `rationale` / `eval_components` / `in_production` 与顶层 `schema_version` / `signal_sources`（逐条列全；**不含 `genre`**） | 无交接上游 ⇒ 空 |
| 派生集 | ∅（若日后出现必须显式声明来源） | ∅ |

**两层取数面与字段面的分离（易误读）**：`entries` / `production_marks` / `schema_version` 是**取数面**
（决定"哪一条进入生产"，见决策 4），`signal_sources` 是随包标注；字段级守恒只对**选定条目**的字段面成立。
`export_slate` 的顶层与条目字段集**两侧同步**义务落在 C7（导出面快照 + `SCHEMA_VERSION` + 本声明三处缺一即红）。

**已裁决（承接 `topic ← genre`，无 `dev` 回落运行级）**：`topic` 的来源 = **承接**——下游 `topic`
承接被标记条目的 `genre`（**改名承接**，登记在 `renames`），`constraints`/`characters` 同名承接；
`target_duration_min` 仍按运行级生效；**无 `dev` 阶段的链四键全回落运行级 `PilotInputs`**——
与规格澄清"有 `dev` 取交接、无 `dev` 回落 run 级"同口径，也是 US2 明写"编排把组合内**本轮进入生产**
标记指向的那条选题，按字段级交接声明交给剧本环节：**题材（`genre`）**/约束（`constraints`）/角色
（`characters`）三个可移交要点逐字段可追溯"能成立的唯一读法。**故 `genre` 不进丢弃集**：
它是改名承接（`genre → topic`），按 `FieldParity` 口径必须**显式声明**——承接不是丢弃，但同样不许静默；
把 `genre` 记进丢弃集（`topic` 取运行级）形式上合规、语义上把选题的题材丢掉了，**已否决**。
本轮对齐已同步到契约 C5/C6（逐键定案表 + 承接映射）与 [plan.md](plan.md) 缺口 13，**无待裁决项**。

**理由**：规格澄清 Q3 把契约钉在既有先例上（**同名字段子集**的"下游同名键集 == (上游 − 丢弃) ∪ 派生"，
`renames` 引入改名承接后该式**不可能**在新层成立），而 Q8 又要求运行级字段
"仍生效但必须显式声明、禁止静默择一"。现有 `FieldParity`（`agents/pilot/handoffs.py` 当前 `:35-75`）只有两类，
若把运行级字段塞进 `derived` 会让"派生"一词失真（它不是从上游派生的），若不加逐字段来源则无法机检
"未声明透传"；同理，改名承接若只按 frozenset 记（`genre` 进丢弃 + `topic` 进派生），会把"承接"错记成
"丢弃 + 派生"——故须 `renames` 承担改名。两处都会让 FR-016 的"禁止静默择一"退化为口号。逐字段的
`reads` 使**两种模式都**必须被声明：`dev_script_handoff`（链上有 `dev`）与
`run_level_pilot_inputs`（链上无 `dev` 的既有短剧链）——**没有第三种"没声明"的活法**。

**被否决**：
- *只用现有 `FieldParity`*：表达不了运行级来源（口径失真）或放任隐式透传（违 FR-016）。
- *`topic` 取运行级、`genre` 进丢弃集*：形式上合规（丢字段显式），但 US2 的"题材逐字段可追溯"不成立
  （见上"已裁决"）；本特性能自称"把选题交给剧本环节"的依据正是 `topic ← genre`。
- *在交接侧手写一份下游读取集*：与下游 loop 的输入视图无绑定，下游新增读取点即静默漏声明——正是要防的漂移。
- *把 `target_duration_min` 也算成交接产出*：那是发明需求（下游时长来自本次项目声明，不来自选题），
  且会让"形态档位"与"运行级输入"两处时长口径进一步纠缠（见决策 6/7）。
- *让交接直接改下游 schema*：017 已把导出面锁定为**单侧快照**（`agents/dev/export_slate.py:7-10`：
  "本模块**不写 parity 函数、不改下游 schema**"），下游字段名由本特性**反解**时补齐，不得顺手改 017 侧。

## 决策 4：取数入口的拒绝语义 = 下游**拒绝启动**，不静默降级

**结论**（契约 C5 的取数入口条 + C6 的要点校验条）：交接取数入口 = 组合级 `production_marks`
（`agents/dev/artifact.py:210-282`），
要求**恰好一条**且指向 `entries` 内已存在条目；以下四类情形一律**拒绝启动**并点名具体违规：
① 无标记；② 标记数量 > 1；③ 标记指向不存在条目（悬空）；④ 被标记条目的可移交要点任一为空
（`genre`/`constraints`/`characters`）。**不得**取第一条兜底、不得把违规轮伪装成"选题为空"。
017 的导出面**不因缺陷丢条目**（`agents/dev/export_slate.py:43-53`，含悬空标记原样保留），
故"拒绝"必须发生在**交接侧**而不是指望上游过滤。

**理由**：规格 US2 场景 3/4 与 FR-005 把"唯一 + 存在 + 要点齐备"定为**下游拒绝启动**的触发条件；
`TopicSlate` 构造期**刻意不代判**标记合法性（`agents/dev/artifact.py:16-21`："越界即门禁判 0，
不由代码兜底"），判定归 `rule.slate_combination`——但门禁判 0 只让该轮 tree 得 0 分，
**并不阻止**下游消费一个缺陷工件。这道防线必须由交接侧自己把住，且与 017 的
`rule.slate_structure`（要点缺失判 0）同源、互为第二道防线。

**被否决**：
- *悬空标记时回落到"未标记的唯一条目"*：即静默择一，FR-005 明禁。
- *让 `script` 阶段带着空 `genre` 往下跑*：会产出与选题无关的剧本，且下游门禁要到很后面才炸，
  故障定位代价远大于启动期拒绝。
- *由交接"修正"工件（补齐缺失要点）*：等于编造选题内容（原则六不编造）。

## 决策 5：索引块网格配置化、容量校验与升版义务（**不取**镜组切分、**不取**限镜数）

**结论**（契约 C8/C9）：索引码泛化为 **R 行 × C 列块网格**（容量 `2**(R·C)`），落
**`storyboard.render.index_grid.{rows, cols}`（必填）**；`agents/storyboard/board_render.py` 的模块常量
`INDEX_BITS = 4` 与 `_INDEX_ROWS = 2`（当前 `:39-40`）退役，编（当前 `:137-141`）、
解（当前 `:144-155`）、绘（当前 `:203-208`）三处**共用同一取值**（从 `render_cfg` 取，经 `_require_render_cfg`
（当前 `:71-77`）缺项即报错）。**容量下界**：`2**(R·C) ≥ 该形态派生镜头数`（派生镜头数是**单一无环持有者**
`agents/pilot/scale.py` 的 `max(场景数, ceil(成片时长 / 单镜时长))`，序号 `0..n-1` ⇒ `2**(R·C) ≥ n` 恰够；
`movie` 原值 **2700 镜**（`5400 s / 2.0 s`）⇒ 容量 ≥ **12 位**：`2**11 = 2048 < 2700 ≤ 4096 = 2**12`，
如 `rows: 2, cols: 8`（16 位））；**量子上界（解码保真）**：
`2**C ≤ render.width`（块宽 `max(1, width // 2**C)` 在块数超像素数时退化、位间互相吞并；又 `1 ≤ R ≤ height`）；
最终守卫是**全量编解码往返断言**（对 `0..派生镜头数-1` 逐序号 `decode_index_code(render_shot_card(i)) == i`）。
**派生镜头数取决于 `clip_spec.duration_seconds`（配置）**：形态要更少镜头就改单镜时长，**不是**放宽容量校验。
**升版义务（本案为真，不是条件句）**：`frame_function_hash()` = `board_render.py` 文件字节摘要
（当前 `:233-239`，其 docstring 已明写"含构图/色板/索引条口径"），被 `proxy.emotion_alignment` 版本号拼接
（`agents/storyboard/evaluators/alignment.py` 当前 `:88-97`）——**网格参数一改（含仅改配置取值），该评估器必然升版本**。
既有已落盘工件为内容寻址、**不受影响**；**禁止**任何改写历史节点/工件的路径（无迁移、无重算、无回填）。
证据三条：① 冻结**旧版本字面量**并断言新版本 ≠ 旧版本（先例 `tests/contract/test_dev_contracts.py:345`
的 `BOOTSTRAP_VERSION` 冻结法）；② 旧 blob 字节与旧节点得分无写路径（静态断言 + 无迁移脚本）；
③ 网格参数进 `config_snapshot`（`agents/storyboard/loop.py` 当前 `:269` 已冻结 `render` 段）⇒ 改网格即新快照指纹、
新树、新节点。

**理由**：网格参数是**渲染参数**，而 `render` 段已经天然流动到所有需要它的地方——`StoryboardConfig.render`
（`agents/storyboard/config.py` 当前 `:247`）→ 模拟渲染器（`platform/simulated.py`）→ 评估器 `render_cfg`
（`evaluators/__init__.py`，`alignment.py` 校验 fps/width/height）→ 树 `config_snapshot`
（`loop.py` 当前 `:269`）。放进 `render` 就**只有一处取值**，不存在"帧产出按 A 网格、评估器按 B 网格"的第二来源。
"每镜一码"（一镜一条码）的确定性口径被完整保留，且容量下界/量子上界校验把"容量够不够"
变成**可机检**（这正是"防同类 bug 再犯"的落点：今天 `shortdrama` 的 16 镜恰在旧 4 位单行网格上限，是**临界巧合**
而非余量；`movie` 的 2700 镜才是暴露面）。

**被否决**：
- *按镜组切分（每 16 镜一组、组内重新编号）*：引入**组间边界语义**（同一物理镜序号在不同组内编码不同），
  解码需知道组划分与组内偏移，帧身份不再自描述；且评估器/覆盖门禁/回放匹配键都要携带组信息——
  为一个编码问题上引入一层新语义，规格已明示不取。
- *限镜数（把最小可行长片压到 16 镜以内）*：让 G2 的"长片体量"名存实亡（用户故事 1 的第一句即"长片体量项目"）；
  且"上限"会变成一个**写死的业务数字**（而非配置声明）。要更少镜头应当**改 `clip_spec.duration_seconds`**。
- *把容量写死在代码里（只把 4 改成 6/12）*：下次形态体量再变即重演同一 bug（且 `movie` 与 `shortdrama` 的
  需求不同，写在代码里必然会退化成形态分支）。
- *把容量放 `storyboard` 段顶层而非 `render` 内*：会多出"帧产出与评估器两处取值"的接缝：本仓至少有三个
  调用点取 `render_cfg`，漏传一处即静默回落（比改名成本高得多）。
- *把网格参数并进评估器版本号之外的快照键（如 `alignment` 段）*：它是**帧产出**的口径，不是对齐口径；
  放错段会让"帧函数哈希已进版本号"的既有防线失效。

## 决策 6：场景数与每场景行数配置化（码内默认与码内常量一并退役）

**结论**（契约 C10）：新增必填键 `pilot.scene_count` 与 `pilot.lines_per_scene`（二者缺一即报错），
**唯一解析者 = `PilotConfig`**（`pilot` 段的加载器）；`agents/*/config.py`（含 `agents/screenplay/config.py`）
**不得**读 `pilot` 段，消费侧**经参数注入**取值（C-02）。
`agents/pilot/stages.py` 的 `_DEFAULT_SCENE_COUNT`（当前 `:97`）**删除**（不得再作默认值来源），
`build_runtime` 的 `shot_plan`（当前 `:195`）改取注入值；
`build_screenplay_plan`（当前 `:902-960`）的 `range(4)`（当前 `:918`）与 `range(12)`（当前 `:930`）改取同一对注入值。
页数一致性纳入档位机检：`场景数 × 每场景行数 ÷ lines_per_page` 必须落在
`[成片时长 − 页数容差, 成片时长 + 页数容差]`（`rule.page_minutes` 的口径，`page_minutes.py:55-66`）。
**派生镜头数的持有者**：`agents/pilot/scale.py`（新，叶子模块）给出
`max(场景数, ceil(成片时长 / 单镜时长))`，`build_shot_plan` 与 `agents/storyboard/config.py` 的容量校验
**都只读它**（F-04）。
**键名落点说明**：`scene_count` 落 `pilot` 段（链的装配面，与 `data-model.md` 同口径）；`lines_per_scene`
是**本计划补齐的键**——契约 C10 已登记该码内常量的存在（当前 `:930`）但未落键，而页数门禁同时依赖
"场景数 × 每场景行数"，只补场景数仍过不了门禁。

**理由**：规格澄清只点名了场景数（`_DEFAULT_SCENE_COUNT`），但**页数门禁的实际输入是"场景数 × 每场景行数"**
——只把场景数入配置，`movie` 形态仍然过不了门禁（页数 = 48 ÷ 45 ≈ 1.07 页 vs 目标 90 ± 5 页；
撰写时实跑核实）。这是"缩档只改配置"（FR-013/FR-014）能否成立的**必要条件**，也是本次勘查发现的
第一处真实缺口（plan.md 缺口 1）。把两个数都交给配置，还有一层收益：**缩档时链路与门禁一行不动**
——所有体量值都从配置派生，`shot_plan`/剧本计划/页数窗口自动一致（否则"缩场景数"会静默撞页数门禁）。

**被否决**：
- *只把 `_DEFAULT_SCENE_COUNT` 换成新的码内默认（如 8）*：仍是码内默认（FR-014 明禁"不取码内默认"），
  且新默认与两形态的页数窗口未必一致——等于把一个形态特化数字写进通用代码。
- *让剧本阶段按 `target_duration_min` 反算场景数*：把"生产方式"与"体量声明"混为一谈，且反算口径又是一处
  不可机检的隐含规则（写几个场景/每场几行不是时长能决定的）。
- *把每场景行数做成运行级输入*：它是**体量档位**的组成部分（缩档要缩的正是它），属形态声明而非运行输入。

## 决策 7：排练档（最小可行长片）的配置化与"未标定"登记

**结论**（契约 C10）：形态配置 `pilot` 段新增 `rehearsal` 子段，两形态均须声明：

```yaml
pilot:
  rehearsal:
    status: declared | unstandardized  # 档位数字是否已由运营给定（缺项即报错）
    work_kind: rehearsal | real_work   # 排练档 / 真实作品（真实作品用形态原值、不缩档）
    scale:                             # status=declared 时逐键齐备（缺一即报错）
      target_duration_s / script_target_minutes / script_tolerance_minutes / clip_duration_seconds
```

**时长粒度（C-01）**：`target_duration_s` 为**秒级浮点**（**可表达 30 秒演示档**），分钟键为**浮点分钟**
（`0.5` 合法，`ops/pilot.py --minutes` 与运行级 `PilotInputs.target_duration_min` 同步为浮点），
折算容差 `1e-6`（只吸收浮点表示误差）。

生效值**单点解析**（一处解析、全链消费）：`status=declared` 时 `scale` 覆盖对应体量键
（`editing.target_duration_s` / `screenplay.target_duration_min` / `screenplay.page_tolerance` /
`visual.clip_spec.duration_seconds`），链路拓扑、交接契约、门禁与评估器组合**一行不动**；
`status=unstandardized`（运营未给定数字）时**不覆盖**（形态原值在 force）并如实标注"未标定"。
**内部一致性机检**：`target_duration_s == script_target_minutes × 60`（容差 `1e-6`）；页数区间 == 分钟 ± 容差；
派生镜头数（`agents/pilot/scale.py` 唯一持有者；`build_shot_plan` 当前 `:230-248`、
`build_shotlist` 当前 `:503-533` 的实际产出与之一致）满足**容量下界** `2**(R·C) >= derived_shot_count`（决策 5）；
页数 == `pilot.scene_count` × `pilot.lines_per_scene` ÷ `lines_per_page` ∈ 页数区间。
**两处时长一致性机检（SC-012①）**：`screenplay.target_duration_min × 60 == editing.target_duration_s`
（取排练档覆盖后的生效值），以及运行级 `target_duration_min × 60 == 生效成片时长`——不一致 ⇒
**拒绝启动并点名两处实测值**。
`work_kind` 随包落盘（**排练产物不得被标为真实作品**）。档位数字属**运营侧输入**：本计划**不发明**；
`status`/`scale` 取值由运营给定后只改本段（零代码改动，见 plan.md 缺口 4）。

**理由**：今天"缩档"事实上由**演示脚本**承担（`ops/demo_pilot.py` 当前 `:51-61` 在临时副本里改写
`editing.target_duration_s` / `screenplay.target_duration_min` / `page_tolerance`），那是"改脚本即改行为"、
不可机检、也无处登记"未标定"，且它**不覆盖场景数**（场景数仍是码内 4）。而 `movie` 形态的**原值**
今天根本跑不通（剧本 90 分钟 vs 剧本计划 1.07 页；`editing.target_duration_s: 120` 与
`screenplay.target_duration_min: 90` 自相矛盾——**按长片语义应为 5400 秒**）——所以"形态配置声明的排练档"不是锦上添花，
它是**让长片体量可声明、可切换、可机检**的唯一载体。一致性校验的必要性也由此而来：
体量几项只要有一项与其余项脱节，链路就会在某个门禁处炸（页数、时长、索引容量三处都是）。

**被否决**：
- *在演示脚本里继续等值派生*：现状即如此，缺点是"改脚本改行为"、不可机检、"未标定"无登记处，
  且漏掉场景数（缩档不彻底）。
- *另建一张配置副本（如 `configs/movie-rehearsal.yaml`）*：形态集被 `tests/unit/test_form_switch.py`
  钉死为两套（`FORMS = ("movie", "shortdrama")`），且等于为长片新造第二套链（FR-014 明禁）。
- *把排练档做成 CLI 开关（`--tier`）*：档位是**体量声明**（配置事实），不是运行期开关；
  做成开关会让"同一份配置产出两种体量"——产物与配置的可追溯性退化（运行记录的配置指纹将不再决定体量）。
- *两档各声明一遍取值再逐项比对*（本计划初稿）：同一批数字出现两处，必然漂移——`scale` 的"覆盖"
  语义本身就是"与形态原值的对照"（不覆盖即原值），无需第二份声明。
- *只声明排练档、不做 `work_kind` 标记*：FR-013 要求"排练产物须可机检地不标为真实作品"，
  没有标记就无法机检，也无法给出"排练 vs 真实作品"的分离。
- *本计划直接选定数字以使两形态即刻可跑*：等于发明运营侧输入（规格开放问题 2 明禁）。

## 决策 8：样片包缺口① = 各环评估分量**从树节点取数**（不复制、不重算）

**结论**（契约 C11）：`state.json`（`agents/pilot/package.py:272-292`）新增逐环节分量段：
`{stage_id: [{node_id, artifact_hash, breakdown: {evaluator_id@version: score}, derived: bool}]}`，
分量**从冻结树节点取数**（`TreeNode.eval_breakdown`，`core/tree/models.py:76`；键形如
`evaluator_id@version`，由 `core/tree/store.py:130-135` 在落盘时强制）。逐环节至少**一条**分量记录，
否则**拒绝装配**（不得以空对象或"不适用"冒充分量齐备）；**派生产物**（如分镜阶段的 `shotlist` 清单：
它是另立的内容寻址工件，无对应节点）如实标 `derived: true` + 注明"派生产物、无节点分量"，
不得与"缺项"混同。为支持按环节取节点：各阶段 `detail` 增加 `tree_id`（**业务侧新增字段，零新增落树路径**），
装配期按 `tree_id` + 产物 `content_hash` 反查节点 `artifact_hash`。

**理由**：`eval_breakdown` 是**唯一权威**的分量面（原则一：每个节点的 `eval_breakdown` 必须含所用每个评估器的
`evaluator_id@version`），而今天样片包只带候选的 `candidate_id`/`score`/`reasons`
（`agents/pilot/package.py:272-292` 的 `build_state`），分量级理由只在判 0 时拼接
（`agents/pilot/stages.py:305-315` 的 `zero_components`）；015 规格
（`specs/015-pilot-shortdrama/spec.md:80`）曾把"各环评估分量"列为交付物而实际只带了候选分——这是**已存在的
交付缺口**，G2 行的验收列又明确要求它。**从节点取数**而非复制的好处：树节点已冻结（不可变、可复算），
复制会在包内造出第二份会漂移的分量；装配期取数还天然受"节点必须已被 INSERT"约束（不得对未落盘节点取数）。

**被否决**：
- *把 `eval_breakdown` 复制进 `CandidateOutcome`*：要改 `core/orchestration/models.py:127-157` 的通用模型
  （005/015/016 共用），且候选与节点的关系不是一一（续跑重建、多产物阶段）——通用层不该背这个语义。
- *在包内**重算**分量*：等于把评估器再跑一遍（真实 judge 会**真实计费**，违原则三），且重算结果与冻结节点
  可能不同（权重/漂移门禁状态可能已变）——"包内分量"必须与"树内分量"逐位一致。
- *缺项时留空对象并标注"不适用"*：FR-008 明禁；"不适用"只对**平台不适用**的分量成立（如无音轨时的 ASR），
  与"整环节无分量"是两回事。
- *给派生产物也造一个假节点*：伪造落树记录（原则二/六）。

## 决策 9：样片包缺口② = 每环真实/模拟标注的**机读面**（装配面声明，不推断）

**结论**（契约 C12）：新增 `STAGE_BACKEND_SLOT`（`agents/pilot/backends.py`）：`stage_id → 后端槽位`
（`dev`/`script` → `llm`；其余五环 → 同名平台槽位），**声明式**且带一致性断言（键集 == `PILOT_STAGE_IDS`）。
标注取值由**装配面**给出并规范化为 `real` / `simulated`：平台槽位取 `PilotBackends.resolved[slot]`
（`agents/pilot/backends.py:146-151`，取值 `simulated|http`），LLM 腿取 `resolved["llm"]`
（`mock|http`）；映射是**显式声明**（`http` ⇒ `real`，`simulated`/`mock` ⇒ `simulated`，
019 的保留值 `fallback` 出现即原样标注、**不得折叠进 `real`**），不推断、不默认。落**两处**：
样片包 `manifest.json` 的 `stages[].source`（+ `channel` 与 `llm.source` 汇总；`dev`/`script` 取
LLM 腿口径）与预检报告（`agents/pilot/pilot.py:240-252` 的 `pilot_backend` 段）。交叉核对：LLM 腿的
逐调用来源必须与 019 运行记录的 `source`（`core/billing/runlog.py:28` 的 `RUN_SOURCES`）一致——
二者同源于装配面声明（`backends.py:227`：`source = "real" if resolved[LLM_SLOT] == HTTP else "simulated"`）。
"真实渠道全链路已跑通"一类结论**只**在七环节 `source` 全为 `real` 时成立（机检）。

**理由**：今天平台侧的真实/模拟取值**只存在于装配面内存与预检报告**
（`agents/pilot/backends.py:146-151`、`agents/pilot/pilot.py:240-252`），**没有进样片包**——
而样片包是评审看的证据载体。`dev` 与剧本两个环节**没有平台槽位**（`PLATFORM_SLOTS` 不含它们，
`backends.py:60`），故其标注只能取 LLM 腿口径；`string` 式"按槽位名猜"会在这两个环节上出问题
（`resolved` 里根本没有 `script`/`dev` 键）——这正是需要**显式映射**而非推断的原因。
"模拟被标为真实"是本特性最不可接受的失败（SC-004 恒 0），故标注必须是**声明 + 交叉核对**的双保险，
而不是"运行时看着像就写上去"。

**被否决**：
- *按 `resolved` 键名与 stage_id 同名匹配*：`script`/`dev` 无同名键 ⇒ 要么 KeyError、要么静默缺标注
  （缺标注比标错更隐蔽，但同样让 SC-004 失去保护）。
- *只落预检报告不落包*：评审拿到的证据包里没有它（G2 行的"不可谎报"就落不了地）。
- *新增第六件（如 `channels.json`）*：规格假设明写"样片包仍是五件套"，且新增件要同时改
  `PACKAGE_FILES` 与 `verify_package`（对 015 口径的显式变更，本特性不预先决定）。
- *把平台腿也纳入 019 的账单/运行记录对账*：019 只实例化 LLM 渠道（平台腿无账单面），
  强行纳入等于发明凭据与账单格式（运营侧输入）。

## 决策 10：性能门禁为何**不能**用固定时钟跑出来的结果，以及阈值"未标定"的口径

**结论**（契约 C13）：性能画像（`agents/pilot/run_report.py`）取各环节 `started_at`/`finished_at`
（`core/orchestration/models.py:242-243`）+ 体量指标（镜头数/片段数/成片时长/页数）+ 形态配置
`pilot.performance` 的阈值快照（`status` + 逐环节 `stage_seconds`），输出**机读结论词**
`meets | below | not_evaluable`。
**两层判定**：① **声明层**：`run_pilot(clock=...)` 未注入时钟（= 墙钟）才具备证据资格；CLI 的
`--fixed-clock`（`ops/pilot.py:17` 的用法说明、`:36` 的 `FIXED_TIMESTAMP`）与测试注入的确定性时钟一律标 `clock_mode = fixed`；
② **退化检测层**：全环节耗时为 0 或彼此相同（含计数器式确定性时钟逐段 +1 秒的等距形态）⇒ 同样判
`not_evaluable`。判 `not_evaluable` 时**只**标注"耗时为确定性常量、不构成性能证据"，**不产出达标结论**；
阈值未标定（`pilot.performance.status: unstandardized`）⇒ 同样判 `not_evaluable`，**不发明数字**。
**确定性隔离**：阈值快照与结论词是确定性的（可机检、可逐字节比对），墙钟耗时**只**出现在报告侧
（`pilot/profiles/{run_id}.json`），**不得**进入五件套（FR-012）。

**理由**：015 的可复现口径建立在"注入确定性时钟 + 五件套逐字节一致"之上（`pilot/runs` 与样片包都带
注入时钟的时间戳）——那组时间戳是**常量**：它能证明"可复现"，**不能**证明"快慢"。两者混用会让性能
结论无法被证伪（原则六"指标口径必须可被证伪"）。因此"哪些运行算性能证据"必须由**声明**（是否注入时钟）
与**退化检测**共同判定，而不是看数字大小。阈值必须来自配置（FR-014）且允许"未标定"：这是纯粹的
运营侧输入（本机与 CI 预算、长片排练档体量不同），**码内零默认**。

**被否决**：
- *用固定时钟运行也出达标结论*：把常量当耗时，结论不可证伪（且会在 CI 里"永远达标"）。
- *按"耗时是否为正"判退化*：计数器式确定性时钟每段恰好 +1 秒，耗时为正且**等距**——检测不到。
- *阈值写在代码里（如"单环节 ≤ 300 秒"）*：违反"全部新增参数配置化、缺项即报错、不取码内默认"。
- *阈值未标定时给一个保守默认值*：即发明数字（spec 开放问题 2 与 FR-010 双禁）。
- *把画像塞进 `manifest.json`*：破坏五件套逐字节可比（见决策 1）。

## 决策 11：与 019 预算门禁的串联方式 ="调用点档位齐备"的预检 + 静态双检（**不新造映射声明**）

**结论**（契约 C3/C13）：不新增任何 stage → 环节档的映射表；改把两处既有声明**互相钉住**：
① **静态侧**（019 既有断言扩展）：全部 `.chat(` 调用点声明的 `stage=` 取值必须 ∈ **两形态**
`budget.tiers` 键集（计数仍 8，`dev` 的调用点 `agents/dev/loop.py:632` 声明 `stage="dev"`）；
② **预检侧**（本特性新增）：把同一判定前置到启动期——调用点档位清单与 `budget.tiers` 不一致即
**拒绝启动**（这是 SC-006"`dev` 环节档位缺失时启动拒绝率 100%"的落点）；③ `sound` 环节**无 LLM 调用**
如实声明（`agents/pilot/stages.py:594` 有明写），不因"没有它的档位"而误判缺档。
`dev` 的档位**两形态已声明**（`configs/movie.yaml:537-541`、`configs/shortdrama.yaml:539-543`），
本特性补的是**前置判定**，不重做计费机制、也不新造声明。

**理由**：FR-007 要求"链上每个环节的 LLM 调用都经按环节分档的前置门禁"，而**链阶段 id 与
`budget.tiers` 键不是一一对应**（一个环节可有多个档位，如 `screenplay` 与 `screenplay_judge`）。
019 明确**禁止在代码里发明 agent ↔ 环节 id 的映射**（`agents/pilot/pilot.py:144-150` 的注释即为此），
而调用点清单**已经是**那份"人写的归属"（8 处 `.chat(` 各自声明 `stage=`，由静态断言钉死）——
再叠一张 `pilot.stage_llm_tiers` 只是同一事实的第二次声明：它多一层会漂移的表，收益仅是"看起来更整齐"。
把判定前置到预检即可兑现 SC-006：漏档在**启动期**不可启动（而不是切真实后端时才炸、或在真实调用后
才被账本发现）——与 019 决策 6 的"前置判定"同一纪律。

**被否决**：
- *在代码里硬编码 `{"dev": "dev", "script": "screenplay", ...}`*：019 明禁；且它是一处**会漂移的
  Agent/环节映射**（真实调用点变、映射不跟）。
- *新增配置声明 `pilot.stage_llm_tiers`（stage → 环节档）*（本计划初稿）：**同一事实的第二份声明**
  ——调用点清单已经声明了归属，第二份表在真实调用点增删时会静默过期（正是本仓反复吃过的"两处清单漂移"）；
  且它的唯一额外能力（预检知道"某环节该有哪些档"）在"缺档即拒绝启动"这个目标下已由 ① ② 覆盖。
- *只检查 `dev` 一个环节的档位（按 FR-007 字面）*：把"按环节分档在全链路生效"退化成单点检查，
  且下一个环节漏档时同样只能事后发现。
- *新增 PG 表/注册表记录档位归属*：为一份静态可读的清单引入运行期存储（YAGNI，且违"静态可读"的审计偏好）。

## 决策 12：成本三方口径的第三腿（网关/账本记账）与窗口边界

**结论**（契约 C13，随包内 `cost.json` 的确定性段一路落盘）：三方口径 = ① 运行记录阶段成本
（`StageState.cost_usd`）⨯ ② 各 Agent 落盘账目
（`state.detail["spent_usd"]`，既有 `summarize_cost`，键集 + 1e-9 容差）⨯ ③ **网关/账本记账**。
第三腿取数方式：**环节边界只读采样**——在阶段执行前后采样网关累计记账
（`LLMGateway.total_cost_usd`，`core/llm_gateway/gateway.py` 当前 `:167` 自注"网关账本（对账三方之一）"），
差值即该环节的 LLM 记账增量；采样包装**只读、不改网关、不进树**，装饰在 `build_stage_specs` 一处；
**不构造 `LLMGateway`（构造点普查仍 13 处）、不改 `cost_breakdown`**（E-03）。
逐环节增量落 `cost.json`（确定性段：同输入同配置可复现）；**可比性分级**：
- **LLM 腿专属环节**（`PLATFORM_SLOTS` 不含者：`dev`/`script`，`backends.py:60`）：逐环节**必须相等**；
- **混合腿环节**（`promo` 等既走网关又有平台侧成本）：只要求"阶段成本 ≥ 该环节 LLM 记账增量"
  （平台腿无法从网关记账分离，**不得**断言相等，也不得静默跳过）；
- **账本腿**（`billing/{channel}/ledger.json` 的按档 `spent_usd`）：仅当档位 `window.kind == run`
  且窗口实例 == 本次 `run_id` 时逐项比对；`day`/`period` 窗口是**窗口累计**（含同窗口其它运行）⇒
  如实标注"窗口口径、不作逐项比对"（不静默比对、也不静默跳过）。

**理由**：FR-006 与 SC-001 要求"三方一致"，而 `cost_breakdown()` 只按 `(role, profile)` 分解
（`gateway.py:387-398`，`_breakdown` 的键是角色，不是环节）——**中间缺一个按环节取数的口径**。
按环节采样是唯一不动网关契约的取法：网关构造是**逐 run** 的（`agents/pilot/backends.py:186-229`
在 `build_runtime` 里装配），故进程内累计天然就是"本次运行"的口径，无需给网关加维度、也不破坏
"`core/llm_gateway` 对 `core/billing` 零 import"的既有边界。**不得给 019 的运行记录 entry 加字段**：
`RUN_ENTRY_FIELDS`（`core/billing/runlog.py`，当前 `:32-42`）是 `head_digest` 链式摘要的输入
（`:81-84`），增删字段会让**既有已封存记录的链校验失败**——那是改写历史证据（原则二）。

**被否决**：
- *只用网关总额做第三腿*：拿不到逐环节差额，"逐项差额在容差内"这条验收落空。
- *给运行记录 entry 加 `cost_usd`*：破坏既有记录的链式摘要校验（上文）；且运行记录是运营侧的按日证据，
  不该为编排层的对账需求改字段。
- *改 `LLMGateway` 增加"环节"维的 `_breakdown`*：动 016/019 的网关契约与快照口径（网关记账口径变化会
  影响历史复算），代价与收益不成比例。
- *账本窗口非 `run` 时也强行逐项比对*：`day` 窗口的 `spent_usd` 含同日其它运行，比出来的是假差异
  （会把账目可信度这件事弄反）。
- *把"被预算拒绝的那一笔"记成花费*：019 已就地实现零成本分支（`BudgetRefusedError` 先于通用失败分支
  捕获，`agents/dev/loop.py:646-670`），本特性不重复实现、也不放宽。

## 决策 13：**不做**清单（写进计划与规格防回潮）

- **真实生成/投放厂商对接**（立项书 G4 / 平台侧 B/C 路径）：`docs/pilot-upgrade-manifest.json` 的 B/C
  两条路径状态为 `not_delivered`，凭证、平台名与预算档属**运营侧前置输入**——本特性不发明、不假装具备，
  也不重写已在位的适配器协议（`core/platform_http.py`、`agents/*/platform/http_real.py` 原样复用）；
  未具备期间**不得**输出"真实渠道全链路已跑通"（FR-011/SC-004）。
- **多形态插件扩展**（立项书 G5）：形态仍只有两套配置（`FORMS = ("movie", "shortdrama")`），
  本特性不引入插件/注册表/新形态。
- **形态分支代码**：零 `form ==` 判断（常驻静态断言不放松），形态差异全部走配置项。
- **第二套链**：不得为长片新造链路；缩档只改配置值（决策 7）。
- **`CostRecord` 增列**：三方口径走报告层与 `cost.json` 的确定性段（决策 12），不动数据模型
  （019 决策 8 的同一取舍；完整收口留给后续特性）。
- **公网服务化与多租户**：本特性仍是内部 CLI + 报告（宪章技术栈约束；web 只读面不动）。
- **`dev` 环节的自动进化**：策略来源是人、必须过静态检查、必须人工采纳才更新部署指针
  （宪章原则六 + 立项书 §3.2；本特性只把既有轮次入口插进链，不新增任何自动生成/自动部署路径）。
- **改写历史节点或既有工件**：索引网格参数变更不回溯（决策 5）、无迁移、无回填；运行记录 entry 字段集不动
  （决策 12）。
