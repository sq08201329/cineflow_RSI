# 实现计划：电影长片全链路编排（七 Agent 串链 + 选题产出 → 剧本输入的跨环节交接 + 真实渠道分层）

**分支**: `018-feature-film-pipeline` | **日期**: 2026-09-24 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/018-feature-film-pipeline/spec.md` 的功能规格说明（含 2026-09-24 澄清会话十一条决议）

## 概要

把三期立项书 G2 的三件事接到一条链上：

1. **链首插入 `dev`**（015 的六环境链 → 七环节链 `dev → script → storyboard → visual → sound → editing → promo`）：
   `dev` 阶段入口**只调 017 既有轮次入口**（`agents/dev/loop.py:490 run_dev_round`），落树/幂等/成本对账
   全部沿用；编排层**零新增落树路径**（常驻静态断言）。真正的工程量在**那条必须同步的集中声明清单**——
   阶段元组、阶段表、`AgentConfigs`、运行时装配（含建表）、预检四处清单、产物 kind 登记、形态配置与
   测试字面量，**漏一处即红**（本计划把清单逐处钉到 file:line，并让"元组/表/清单"之间建立一致性断言，
   而不是靠人记得）。
2. **`dev` → 剧本阶段的字段级交接**（017 唯一被显式挂到 G2 的跨特性债务）：**同名字段子集层**沿用
   `FieldParity` 的"下游同名键集 == (上游 − 丢弃) ∪ 派生"，并**在该层之外扩出两类**——**运行级**来源与
   **改名承接**（登记在 `renames`），守恒等式因此成为
   `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`；`dropped` 是与 `reads` **并列的独立集**，
   另有第三类登记项**取数依据**（`entries`/`production_marks`）。
   运行级字段（`target_duration_min` 等）仍按运行级生效（它们是"本次生产项目"的属性，不是选题产出的
   属性），但**必须被交接声明显式覆盖**：声明逐字段给出**承接 / 运行级 / 派生**三类来源，且
   `下游读取集 == 剧本阶段实际读取的输入键集`（双向机检）。**禁止未声明透传**——下游 loop 的输入视图是
   权威键集，新增输入字段即断言红。
3. **长片体量的三个真实障碍**（本次勘查逐条核实，全部有裁决）：① 分镜索引条是**帧顶 2 行 × 4 列
   块网格（容量 16）**而 `movie` 原值派生 **2700 镜**；② 场景数与每场景行数是**码内常量**（`range(4)`/`range(12)`），
   而页数门禁 = 场景数 × 每场景行数 ÷ 每页行数；③ **`movie` 形态原值今天根本跑不通**（剧本目标 90 分钟
   ⇒ 页数门禁 [85,95] 页，而计划的剧本只有 48 行 ≈ 1.07 页；编辑目标 120 秒与剧本 90 分钟亦自相矛盾，
   按长片语义应为 **5400 秒**）。
   处理：索引码**泛化为 R×C 块网格**（容量 `2**(R·C)`，`rows`/`cols` **入形态配置** + 容量下界/量子上界校验，
   保留"每镜一码"），体量档（**排练档**：成片时长/页数/单镜时长/镜头数/场景数/每场景行数）**全配置化 +
   档位一致性机检**（含"两处时长一致"不变量与派生镜头数的**单一持有者**），链路与环节一行不动。

技术主线不是"再写一个 Agent"，而是**把链路与证据面做诚实**：样片包补两处既有缺口（各环评估分量、
每环真实/模拟标注，都要**可机检**）、性能与预算门禁复用运行记录与配置阈值（**固定时钟运行不得产出
达标结论**）、成本三方口径补上"网关/账本记账"那一条腿（019 的运行记录 entry 字段集是链式摘要的输入，
**不得**增删，故第三腿落报告侧 + `cost.json` 的确定性段）。

**诚实边界（逐条登记，不掩盖）**：平台侧真实生成/投放（`docs/pilot-upgrade-manifest.json` 的 B/C 路径
`not_delivered`）**不在本特性**——本特性"真实渠道" = **LLM 腿真实 + 平台侧按配置（默认模拟）**，每环
标注必须机读可见，未具备时**不得**输出"真实渠道全链路已跑通"；排练档的具体数字、真实渠道凭证与平台名
属**运营侧输入**，本计划**不发明**（未给定期间按"未标定"如实登记）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: **零新增运行时依赖**——stdlib + 既有 `core/{orchestration,billing,llm_gateway,evaluators,tree}`
与 `agents/{pilot,dev,screenplay,storyboard,...}`；策略装载复用 `policies/static_check` + `core/degraded/policy`

**存储**: **无数据库迁移**（0010 之后不动）——`build_runtime` 的建表清单加一行
`agents/dev/db.py:60 create_jobs_schema`；发现树/工件仍一次性 INSERT + 内容寻址；样片包仍五件套；
新增证据件落**报告侧** `pilot/profiles/{run_id}.json`（性能画像 + 成本第三方腿对账），运行记录仍
`pilot/runs/{run_id}.json`

**测试**: pytest（TDD）；新增 `tests/unit/test_pilot_chain_seven.py`（链拓扑与清单不变量）、
`test_pilot_dev_script_handoff.py`（交接守恒与拒绝语义）、`test_storyboard_index_scale.py`（网格容量下界/上界 +
全量编解码往返 + 版本升版）、`test_pilot_evidence_pack.py`（分量/标注入包与缺项拒绝）、
`test_pilot_performance.py`（固定时钟恒 `not_evaluable`、未标定不发明数字）、
`test_pilot_rehearsal.py`（档位单点解析 + 两处时长一致）、`test_pilot_cost_three_way.py`（三方口径与
可比性分级）、`test_dev_policy_loader.py`（装载下沉 + **例外三项替代约束**）；同步更新既有字面量与夹具清单
（**逐处见"规格缺口与处理"第 6 条**）；四条常驻静态断言（不新增落树路径、零形态分支、
`agents/` 不 import `ops/`、`core/` 零形态字面量）**不放松**

**目标平台**: Linux（WSL2 + CI）；`fcntl` / ffmpeg / numpy 边界与 015/019 同款，本特性不新增

**项目类型**: `agents/pilot/`（链与证据面，业务侧）+ `agents/storyboard|screenplay|dev` 三处薄改
（索引网格参数化、体量键配置化、策略装载下沉）+ 两形态 `pilot` 段新键 + CLI/演示 + 测试同步

**性能目标**: 本特性**首次引入性能门禁**（019/017 均"不设性能门禁"）：口径 = 各环节墙钟耗时（复用
`core/orchestration/models.py` 的 `started_at`/`finished_at`，当前 `:242-243`）+ 体量指标（镜头数/片段数/成片时长/
页数），对照**形态配置声明的阈值**（`pilot.performance.stage_seconds`）判定；阈值未标定即如实标注"未标定"
（`status: unstandardized`），**码内零默认**；**固定时钟（确定性档）运行恒 `not_evaluable`**（那组时间戳
是常量，只能证明可复现）

**约束**: 不新增 `LLMGateway.chat` 调用点（019 钉死 8 处；`dev` 阶段复用既有轮次入口即不新增）；
**不新增 `LLMGateway(` 构造点**（019 普查仍 13 处；`run_report` 只读 `total_cost_usd`，E-03）；
`CostRecord` 与发现树字段不动；`core/` 保持业务无关、依赖单向；全部新增参数形态配置化、**零形态分支**；
样片包五件套口径不变（新增字段落既有件内）；`PACKAGE_FILES`/`verify_package` **不改**

**规模/范围**: `agents/pilot/{stages,pilot,handoffs,package,backends}.py` 五处改造 + 新模块
`agents/pilot/{run_report,scale}.py` 与 `agents/dev/policy_loader.py` + `agents/storyboard/{board_render,config}.py`
与 `agents/screenplay/loop.py` 薄改（`agents/screenplay/config.py` **不**读 `pilot` 段）+ `configs/{movie,shortdrama}.yaml`
的 `pilot` 段新键（`scene_count`/`lines_per_scene`/`rehearsal`/`performance`）+ `storyboard.render.index_grid` +
`ops/{pilot,demo_pilot,dev}.py` + 测试同步；**不含**：真实生成/投放厂商对接（运营侧输入 / G4）、
多形态插件（G5）、形态分支代码、`CostRecord` 增列、公网服务化、多租户

## 现状勘查（先核实，后设计）

**事实清单（本特性据以设计的硬事实，逐条已核实）**

| # | 事实 | 本特性处理 |
| --- | --- | --- |
| 1 | 链的集中声明点共**七处**：`agents/pilot/stages.py` 的 `PILOT_STAGE_IDS`（当前 `:94`）、阶段表（当前 `:1018-1069`）、`AgentConfigs`（当前 `:102-111`）、运行时装配与建表（当前 `:163-170`+`:173-180`）、`agents/pilot/pilot.py` 的预检四处清单（`config_completeness` 的加载器元组当前 `:117-131` / 权重循环当前 `:135-140` / `AgentConfigs` 构造当前 `:198-205` / 预算循环当前 `:208-224`）、`agents/pilot/package.py:173-181`（产物 kind 与内容类型）、两形态配置 | 逐处改造 + **一致性断言**（元组 == 表 == 权重 == 加载器 == 档位声明），漏一处即红（C1） |
| 2 | `dev` **没有** `exploration_per_round_usd`（`agents/dev/config.py:150-164` 只有 `model`/`model_prices`） | 预检的预算循环必须把它当 **LLM 腿档位**处理（`pilot.py:212-221` 的既有分支），**不为过预检而发明单轮预算**（C1/C13） |
| 3 | `run_dev_round` 的拒绝路径返回 `job["artifact_hash"] = None` 且节点 `eval_breakdown={}`（`agents/dev/loop.py:553-566`） | `dev` 阶段入口必须把"无产物哈希"判为**阶段失败**（不得带着空工件往下走）；空分量由 C11 的装配门禁兜底 |
| 4 | 剧本阶段今天读整份运行级输入（`agents/pilot/stages.py:403`），而剧本 loop 的输入归一口径**只认四个键**（`agents/screenplay/loop.py:186-204`） | 交接声明的下游读取集 = 该四键（权威口径，并与调用侧读取点 `stages.py:904-905` 双向绑定）；运行级字段显式声明（C5） |
| 5 | 分镜索引条 = 模块常量 `INDEX_BITS = 4` + `_INDEX_ROWS = 2`（`agents/storyboard/board_render.py` 当前 `:39-40`），编/解/绘三处共用（当前 `:137-141`/`:144-155`/`:203-208`）；`movie` 原值派生 **2700 镜**、`shortdrama` **16 镜**（按 `clip_spec.duration_seconds` 复算） | 索引码入 `storyboard.render.index_grid`（`rows`/`cols`；随快照与评估器 `render_cfg` 天然流动）+ 容量下界/量子上界校验 + 全量往返断言（C8/C9） |
| 6 | 网格参数**确实**进入评估器实现哈希：`frame_function_hash()` = `board_render.py` 文件字节摘要（当前 `:233-239`），被 `proxy.emotion_alignment` 版本号拼接（`agents/storyboard/evaluators/alignment.py` 当前 `:88-97`） | **升版是必定义务而非条件句**：网格参数变更（含仅改配置取值）即新版本；旧版本节点/工件不动（C9） |
| 7 | 场景数与每场景行数是码内常量（`stages.py:97` 的 `_DEFAULT_SCENE_COUNT`、`:918` 的 `range(4)`、`:930` 的 `range(12)`），而页数门禁 = 场景数 × 每场景行数 ÷ `lines_per_page` | 三者全部配置化（`pilot.scene_count` + `pilot.lines_per_scene`，后者由本计划补齐）且**唯一解析者 = `PilotConfig`**（`build_screenplay_plan` 经参数注入）；页数一致性纳入档位机检（C10） |
| 8 | `movie` 形态**原值跑不通**：剧本目标 90 分钟 ⇒ 页数门禁 [85,95]，而计划的剧本 48 行 ÷ 45 行/页 ≈ 1.07 页（`rule.page_minutes` 判 0）；且 `editing.target_duration_s: 120`（秒）与 `screenplay.target_duration_min: 90`（分钟）自相矛盾（**按长片语义应为 5400 秒**）；演示脚本靠"等值派生"（`ops/demo_pilot.py` 当前 `:51-61`）把它压成短剧档才跑通 | 排练档把体量变成**单点声明 + 内部一致性机检**（含两处时长一致不变量，C10/SC-012①）；未标定期不覆盖（形态原值在 force）并如实标注（缺口 4） |
| 9 | 019 的运行记录 entry 字段集是**链式摘要的输入**（`core/billing/runlog.py` 的 `RUN_ENTRY_FIELDS`，当前 `:32-42`，`_entry_digest` 只摘要该字段集） | **不得**给 entry 增删字段（否则既有记录链校验失败）⇒ 第三方账目腿落报告侧（`pilot/profiles/`）与 `cost.json` 的确定性段（C13） |
| 10 | 运行级 `target_duration_min`（`agents/pilot/pilot.py` 当前 `:41-58`）与形态配置的成片时长是**两个来源**；页数门禁取配置、剧本提示词取运行级 | 预检一致性校验：运行级 ×60 必须等于**生效**成片时长，不一致即**拒绝启动并点名**（不静默择一，缺口 5；容差口径见 C10） |
| 11 | `dev` 轮次入口要求 `inputs.genre_bounds`/`inputs.audience`（`agents/dev/loop.py:176-191`），而运行级输入 `PilotInputs` 只有 topic/时长/角色/约束 | 运行级输入扩展两字段（缺项即预检拒绝），并留痕输入指纹口径变更（缺口 2） |
| 12 | 两形态的 `budget.tiers` **已含 `dev` 档**（`configs/movie.yaml:537-541`、`configs/shortdrama.yaml:539-543`），`agents/dev/loop.py:632` 的调用点已声明 `stage="dev"` | FR-007 的"dev 档两形态均声明"现状已满足；本特性把该口径**前置到预检**（调用点档位齐备双检，缺档即拒绝启动）且**不发明映射**（C3） |
| 13 | `ops/demo_pilot.py` 当前 `:83-85` 断言配置加载器计数为**写死的字面量**，与 `config_completeness` 的实测返回不符——演示第 1 步 `ok` 恒假（既有缺陷，实跑核实；**计数随实现派生，计划与规格都不写数字**） | 顺手修正并改为派生量断言（缺口 6） |
| 14 | 平台侧真实渲染请求**全量透传** `storyboard.render`（`agents/storyboard/platform/http_real.py:105-122`） | `index_grid` 入 `render` 会改变该载荷；B 路径 `not_delivered` ⇒ 无在线影响，但本地 stub 契约须同步（缺口 8） |

**契约编号口径（F-06）**：本计划引用的 **C1~C13 是 `specs/018-feature-film-pipeline/contracts/` 的 018
编号**，与 `tests/contract/test_pilot_contracts.py` **文件内部**的 015 编号（该文件内亦称 C13）**同名不同物**；
引用时一律冠以"018 C<n>"或写明文件名与段名。

**口径澄清 A（最易误读）：运行级字段"仍生效"与"必须显式声明"是两件事。** 剧本阶段的
`target_duration_min` 取自运行级（本次生产项目的目标时长），它**不是**选题产出的属性，故**不**从
`dev` 交接里来；但"它生效"不等于"它可以不被声明"——交接声明必须逐字段给出**三类来源**（承接 /
运行级 / 派生），且 `下游读取集 == 剧本阶段实际读取的输入键集`（`agents/screenplay/loop.py:186-204` 的归一口径）。
**未声明即断言红**：这是"禁止未声明透传"的机检落点，也是 FR-016"禁止静默择一"的同一纪律。

**口径澄清 B：`shortdrama` 的 16 镜**恰在旧容量上限（`2**(2·4) = 256 ≥ 16`，而旧 4 位单行网格为 `2**4 = 16`，
序号 `0..15` 恰可编码），故今天的短剧链路"看起来没问题"是**临界巧合**，不是余量；`movie` 原值的
**2700 镜**才是暴露面。容量下界按**该形态派生镜头数**校验（单一无环持有者
`agents/pilot/scale.py`：`max(场景数, ceil(成片时长 / 单镜时长))`；当前 `movie` ⇒ 容量 ≥ **12 位**：
`2**11 = 2048 < 2700 ≤ 4096 = 2**12`），量子上界按 `2**C ≤ render.width`
（块宽 `max(1, width // 2**C)` 在块数超像素数时退化、位间互相吞并；又 `1 ≤ R ≤ height`）。

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器确定性与版本冻结 | 网格参数经 `frame_function_hash`（`board_render.py` 当前 `:233-239`）进入 `proxy.emotion_alignment` 版本（`alignment.py` 当前 `:88-97`）⇒ 网格参数变更（**含仅改配置取值**）**即**新版本，同 `evaluator_id@version` 行为不变；既有已落盘工件为内容寻址，**不重写、无迁移**；渲染/档位随 `config_snapshot` 冻结（`agents/storyboard/loop.py` 当前 `:269` 已冻结 `render` 段） | ✅ 满足（C8/C9） |
| 原则二：节点不可变与全量谱系 | 链首插入不改落树路径（`dev` 走 017 既有入口）；样片包新字段全在既有件内；`CostRecord` 与树模型**零改动**；失败照常入账（`dev` 的拒绝路径**零成本**、`BudgetRefusedError` 分支已在 017/019 就地实现）；本特性不产出任何改写历史节点/工件的代码路径；**不新增 `LLMGateway(` 构造点**（019 普查仍 13 处，E-03） | ✅ 满足（C1/C2/C11/C13） |
| 原则三：昂贵动作仅限线上探索 | 真实渠道仍只走 LLM 腿（网关 + 019 前置门禁 + 账单对账）；平台侧保持配置默认**模拟**并**逐环机读标注**；不新增真实调用点；预算按环节分档在全链路生效（`dev` 档两形态已声明，缺档即拒绝启动） | ✅ 满足（C3/C12） |
| 原则四：沙箱隔离与前缀不可泄露 | **不新增策略执行路径**：`dev` 阶段只在链首**装载人工策略并调既有轮次入口**，例外条款的替代约束**三项**必须在**新造的下沉路径**上落机检（C2 逐项登记）——① 静态检查在装载时生效（`policies/static_check.check_policy_source` 前置）；② **策略执行带超时上限**（静态检查不禁循环，注入死循环策略 ⇒ 超时判失败而非挂死）；③ **策略零环境对象 / 不触网关**（只喂 `plan(inputs, config)`，不交付 `observed()`/`probe()` 或存储/账本/网关句柄；策略本体不持凭证、不经网关、不触生成，网关调用由宿主 `run_dev_round` 发出并受 019 门禁约束）。落点 = `agents/dev/policy_loader.py` + `tests/unit/test_dev_policy_loader.py`（T1807/T1808 先写后实现）。本特性**不**放宽例外条件，也**不**借此把开发环节升级为自动进化 | ✅ 满足（**附义务**：三项替代约束与装载静态检查，C2/T1807/T1808） |
| 原则五：单向依赖与形态配置化 | 全部新增参数（`pilot.scene_count`/`lines_per_scene`、`pilot.rehearsal`、`pilot.performance`、`storyboard.render.index_grid`、来源标注口径、交接声明）**全落配置、两形态均须声明、缺项即报错**；零形态分支（既有静态断言常驻）；`agents/` 侧策略装载（不得 import `ops/`）、派生镜头数叶子模块（不得反向 import `agents/storyboard/*`）；新模块落 `agents/pilot/`（业务侧） | ✅ 满足（C2/C4/C8/C10/C13） |
| 原则六：诚实边界与人类锚点 | 真实/模拟**逐环机读标注**且"模拟标为真实"恒 0；未具备真实渠道时"全链路已跑通"结论恒 0；阈值/档位未标定即如实登记（**不发明数字**）；固定时钟运行恒 `not_evaluable`；成本三方口径的不可比情形（平台腿、窗口累计）逐条登记 | ✅ 满足（C12/C13） |
| 治理：复杂度必须被论证 | 十一项新增抽象（含 dev 阶段入口、交接声明、下游读取集常量、映射声明、逐环节记账采样、排练档、每场景行数键、报告件、装配面槽位映射、策略装载下沉、**派生镜头数持有者 `agents/pilot/scale.py`**）逐条进入复杂度跟踪表，对照被否决的更简方案 | ✅ 满足 |
| 测试纪律 | TDD（测试任务与实现任务分列）；**不依赖真实昂贵调用**（全模拟后端 + 夹具 + 确定性时钟）；缺项即报错类断言（新增输入/档位/网格/阈值）；四条常驻静态断言不放松；覆盖率 ≥85%（口径不降，含 web）；对抗/无偏性门禁不放松 | ✅ 满足 |

**门禁通过。** 原则一的满足以 C9 的三条证据为**前提**（升版可机检、旧节点与旧工件逐字节不变、
无改写历史路径）；原则四的满足以 C2 登记的**三项替代约束**全部落机检为**前提**（静态检查在装载时生效、
策略执行超时、策略零环境对象/不触网关，落点 T1807/T1808）——任一项未落实即视为未通过。
新增抽象按治理规则在"复杂度跟踪"表中逐条论证。

## 项目结构

### 文档（此功能）

```text
specs/018-feature-film-pipeline/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── pipeline-chain.md         # 链首插入与清单同步不变量 / dev 阶段入口与产出 / dev 预算与预检 / dev 产物 kind（C1~C4）
│   ├── dev-script-handoff.md     # 声明形状与"每个读取输入均已声明" / 丢弃与派生义务 / 上游 schema 两侧同步（C5~C7）
│   ├── render-index-scale.md     # 索引块网格容量与校验 / 原则一版本规则 / 排练档·场景数·派生镜头数持有者（C8~C10）
│   └── package-evidence.md       # 逐环节评估分量入包 / 逐环节真实·模拟标 / 性能与预算门禁证据面与可复现性（C11~C13）
└── tasks.md                      # 阶段 2 输出（/skill:speckit-tasks）
```

> **编号口径（F-06）**：以上 C1~C13 是 **018 自己的契约编号**，与 `tests/contract/test_pilot_contracts.py`
> 文件内部的 015 编号（该文件内亦称 C13）**同名不同物**——引用时冠以"018 C<n>"或写明文件名。

### 源代码（仓库根目录，在既有结构上增量）

```text
agents/pilot/stages.py            # 既有：七环节链（PILOT_STAGE_IDS / 阶段表 / AgentConfigs.dev / 建表）
│                                 #   + _dev_entry（调 run_dev_round）+ _handoff_dev/_handoff_script（交接声明接线）
│                                 #   + 逐环节网关记账采样包装（只读测量，装饰在 build_stage_specs 一处）
│                                 #   + build_shot_plan/build_screenplay_plan 的体量键改经参数注入
│                                 #     （唯一解析者是 PilotConfig；三处码内体量常量退役）
│                                 #   + STAGE_CONFIG_SECTION/STAGE_TREE_PREFIX（stage→配置段/轮次树前缀，单一映射声明）
agents/pilot/scale.py             # 新：派生镜头数单一无环持有者（纯计算叶子模块）
│                                 #   derived_shot_count = max(场景数, ceil(成片时长 / 单镜时长))
│                                 #   （build_shot_plan、storyboard 容量校验、档位一致性机检都只读它）
agents/pilot/pilot.py             # 既有：PilotInputs 扩展（genre_bounds/audience；target_duration_min 改浮点分钟）
│                                 #   + 预检四处清单加 dev + 新预检（档位齐备 / 两处时长一致 / 档位—体量一致性）
│                                 #   + _backend_report 增 stages:{stage_id: source}（E-01）
agents/pilot/handoffs.py          # 既有：交接声明 = reads 三类（承接含改名/运行级/派生）+ dropped 独立集
│                                 #   + 取数依据（entries/production_marks）+ dev_to_script_inputs
│                                 #   + SCRIPT_INPUT_READS 绑定断言 + C6 ①~⑥（承接层不复用 consistent()）
agents/pilot/package.py           # 既有：_KIND_CONTENT_TYPE 登记 dev 产物 kind（slate→json）
│                                 #   + manifest.stages[].source/channel/llm.source / state.eval_breakdown
│                                 #   + cost 的网关腿 / 文案去"六阶段"（含 agents/pilot/__init__.py 的包注释）
agents/pilot/backends.py          # 既有：STAGE_BACKEND_SLOT（stage→后端槽位）声明 + 标注视图
agents/pilot/run_report.py        # 新：报告侧证据（性能画像 + 成本第三方腿对账）→ pilot/profiles/{run_id}.json
│                                 #   （只读 total_cost_usd；不构造 LLMGateway，构造点普查仍 13 处）
agents/dev/policy_loader.py       # 新：链首策略装载（静态检查前置 + 版本核验 + 实例化 + 超时 + 零环境对象守护），
│                                 #   ops/dev.py 收敛到它（原则四例外三项替代约束的落点）
agents/storyboard/board_render.py # 既有：index_grid 参数化（编/解/绘三处同取 render_cfg）+ 编解码往返工具
agents/storyboard/config.py       # 既有：storyboard.render.index_grid 必填 + 容量下界（读 agents/pilot/scale.py）
│                                 #   + 量子上界（2**C ≤ width；1 ≤ R ≤ height）；不自行读 pilot 段
agents/screenplay/config.py       # 既有：**不读** pilot 段（体量键的唯一解析者是 PilotConfig）
agents/screenplay/loop.py         # 既有：导出 SCRIPT_INPUT_READS（与 _validate_inputs 归一口径绑定）
ops/pilot.py / ops/demo_pilot.py / ops/dev.py
                                  # 既有：--genre-bounds/--audience、--minutes 接受浮点分钟、七环节演示
                                  #   （缩档走配置的排练档、配置加载器计数断言改派生量）、
                                  #   _load_policy 收敛到 agents/dev/policy_loader.py
configs/movie.yaml / shortdrama.yaml  # 新增：pilot.scene_count / pilot.lines_per_scene / pilot.rehearsal
                                      #   （status/work_kind/scale；时长秒级+浮点分钟）/ pilot.performance
                                      #   （status/stage_seconds）/ storyboard.render.index_grid——两形态取值不同
tests/unit/test_pilot_stages.py / test_pilot_run.py / test_pilot_package.py / test_pilot_product_labels.py
tests/unit/test_pilot_backend_selection.py / test_pilot_drift_wiring.py / test_form_switch.py
tests/unit/test_config_integrity.py / test_storyboard_config.py / test_storyboard_board_render.py
tests/unit/test_pilot_upgrade_path.py / tests/contract/test_pilot_contracts.py
tests/conftest.py                 # 既有：_MINIMAL_MOVIE_CONFIG 与派生夹具注释（"16 镜上限"）
tests/unit/test_pilot_chain_seven.py / test_pilot_dev_script_handoff.py / test_storyboard_index_scale.py
tests/unit/test_pilot_evidence_pack.py / test_pilot_performance.py   # 新
tests/contract/test_pilot_film_contracts.py                          # 新：C1~C13 聚合
```

**结构决策**: 链与证据面留在 `agents/pilot/`（它是"试水编排"这一**业务件**的所在地，且形态无关性由既有的
静态断言守住）；**不**下沉 `core/`——`core/orchestration` 是零业务概念的通用编排层，把"七环节/五件套/
排练档"塞进去即违反原则五。索引网格参数化落 `agents/storyboard/`（帧产出与评估器输入的形成环节同源，
**一处取值**），而**派生镜头数抽到 `agents/pilot/scale.py`（纯计算叶子模块）**——`agents/storyboard/config.py`
只**读**该函数，模块本身**不** import `agents/storyboard/*`，故依赖仍单向无环，且"公式只有一份"
（两处各写一遍会让渲染器与门禁各按一份数字判定）。策略装载下沉到 `agents/dev/policy_loader.py` 而不是从
`ops/dev.py` 取用——既有常驻断言明确禁止 `agents/` import `ops/`
（`tests/unit/test_pilot_stages.py` 当前 `:140-162`），且"两条装载路径"会让静态检查在一侧静默失效
（017 的例外条款第①项即以此为前提；三项替代约束的落机检见 C2）。
**跨工件对齐**：本计划的契约编号与分文件归属与 `data-model.md` / 四份 `contracts/*.md` 逐条一致（同名段、
同名键：`pilot.{scene_count,lines_per_scene,rehearsal,performance}`、`storyboard.render.index_grid`、
`manifest.stages[].source`、`pilot/profiles/{run_id}.json`）、交接声明同名
（`SCRIPT_INPUT_READS` / `reads` / `renames` / `dropped` / `sources`）。四处差异已登记：
① 本计划**补齐**并行产出未落键的 `pilot.lines_per_scene`（契约 C10 已登记该码内常量，页数一致性所必需）；
② 本计划**补齐**"成本第三方腿"的取数口径（契约 C13 的同一证据面，逐环节网关记账采样）；
③ 本计划**补齐**派生镜头数的持有者模块 `agents/pilot/scale.py`（契约 C8/C10 已要求"单一派生源"）；
④ **`topic` 的来源：已裁决**——**承接 `topic ← genre`（改名承接，登记在 `renames`），无 `dev` 阶段的链
回落运行级 `PilotInputs.topic`**；`genre` **不进丢弃集**（承接 ≠ 丢弃，但同样不许静默）。契约 C5 已按此
定案（逐键定案表 + 守恒等式 `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`），与本计划原写法一致，
见 [research.md](research.md) 决策 3。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要（13 条，逐条 = 结论 + 理由 + 被否决）：

1. 落点与包边界：链与证据面在 `agents/pilot/`，新模块 `run_report.py`（性能画像 + 成本第三方腿，落
   `pilot/profiles/{run_id}.json`）与 `scale.py`（派生镜头数叶子）；不落 `core/`（通用编排层须零业务概念）、
   不落 `ops/`
2. `dev` 作为链首的插入方式与清单同步风险：`build_stage_specs` 首项 + 依赖 `()`，`script` 依赖改
   `("dev",)`；风险用**不变量断言**消解（元组 == 表 == 权重 == 加载器 == 档位 == 两形态声明）
3. 交接声明的形状：在手写声明层扩出**运行级**类（`reads` 三类：承接含改名 / 运行级 / 派生 + 逐字段来源 +
   两模式化，另有 `dropped` 独立集与取数依据登记），与"上游 → 下游"**同名字段子集**映射层的既有
   `FieldParity` 三式并存（承接层不复用 `consistent()`，另立 C6 ①~⑥）；
   `下游读取集 == SCRIPT_INPUT_READS` 双向机检；**运行级字段显式覆盖**，禁止未声明透传
4. 取数入口的拒绝语义：标记恰好一条且指向组合内条目；悬空/越界/要点不全 ⇒ **下游拒绝启动**并点名，
   不静默降级（017 的导出面不因缺陷丢条目，故拒绝必须发生在交接侧）
5. 索引网格配置化、容量校验与升版义务：`storyboard.render.index_grid`（`rows`/`cols`）+ `2**(R·C) ≥ 该形态
   派生镜头数`（movie **原值 ⇒ ≥12**）+ `2**C ≤ width`（+ `1 ≤ R ≤ height`）+ 全量往返断言；
   派生镜头数**单一无环持有者** `agents/pilot/scale.py`；升版**必定义务**（`frame_function_hash` 已入版本号，
   **仅改配置网格取值也必须升版**），冻结旧版本字面量并断言新版本 ≠ 旧版本；既有工件内容寻址、
   无迁移、无改写；**不取**镜组切分、**不取**限镜数
6. 场景数与每场景行数配置化：三处码内常量退役（`_DEFAULT_SCENE_COUNT`/`range(4)`/`range(12)` 不得再作
   取值来源），**唯一解析者 = `PilotConfig`**、消费侧经参数注入；页数一致性纳入档位机检
7. 排练档（最小可行长片）配置化：`pilot.rehearsal`（`status`/`work_kind`/`scale`，**单点解析覆盖**体量键）；
   时长粒度 = 秒级（浮点）+ 浮点分钟 + 容差 `1e-6`（可表达 30 秒演示档）；两处时长一致不变量
   （movie ⇒ 5400 s）与"运行级 ×60 == 生效成片时长"同口径；
   `status=unstandardized` 不覆盖（形态原值在 force）+ 如实标注；数字属运营侧输入（本特性不发明）；
   排练/真实作品由包内 `work_kind` 标记机检分离
8. 样片包缺口①：各环评估分量**从树节点取数**（`eval_breakdown`，不复制、不重算），缺项即拒绝装配，
   派生产物显式标注
9. 样片包缺口②：每环真实/模拟标注入包（装配面 `STAGE_BACKEND_SLOT` → `manifest.stages[].source`/`channel`，
   来源 = 装配面声明、与 019 运行记录逐调用 `source` 交叉核对、包与预检报告两处）
10. 性能门禁为何不能用固定时钟：确定性档时间戳是常量，"快慢"不可证；两层判定（声明层 `clock_mode`
    + 退化检测）产出 `meets|below|not_evaluable`；墙钟只落报告侧
11. 与 019 预算门禁的串联方式：**不新造 stage↔环节档映射表**，改用"调用点档位齐备"的预检 + 静态双检
    （全部调用点 `stage=` 取值 ∈ 两形态 `budget.tiers` 键集；`dev` 缺档即拒绝启动；不发明 agent↔环节映射）
12. 成本三方口径的第三腿：网关记账按**环节边界采样**取逐环节增量；账本窗口为 `run` 时逐项、
    为 `day`/`period` 时如实备注（不静默比对、也不静默跳过）；019 运行记录 entry 字段集不动
13. **不做**清单（真实生成/投放对接、多形态插件、形态分支、`CostRecord` 增列、公网服务化、多租户）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：`pilot` 段新增键（`scene_count` / `lines_per_scene` / `rehearsal`
  （`status`/`work_kind`/`scale`，时长秒级+浮点分钟）/ `performance`（`status`/`stage_seconds`））、
  `storyboard.render.index_grid`、新模块 `agents/pilot/scale.py`（派生镜头数持有者）、`DevConfig` 接入
  `AgentConfigs`、交接声明（`reads` **三类**：承接含改名/运行级/派生 + `dropped` 独立集 + 取数依据
  `entries`/`production_marks`——**不是**"四类互斥"）、`StageChannelMarking`、
  `PerformanceProfile`（结论词 `meets|below|not_evaluable`）、`StageEvalBreakdown`、`FeatureScaleProfile`
  （`work_kind` 标记 + 与形态原值对照）、`ScriptInputSource`、`STAGE_CONFIG_SECTION`/`STAGE_TREE_PREFIX`
  单一映射声明、样片包五件套新增字段表（**由并行任务产出，与本文档逐条对齐**）
- [contracts/pipeline-chain.md](contracts/pipeline-chain.md)（**C1~C4**）：C1 链首插入与清单同步不变量
  （逐处 file:line + 不变量断言、**漏一处即红**；**不新增 `LLMGateway(` 构造点**——019 普查仍 13 处，
  E-03）；C2 `dev` 阶段入口与产出契约（只用
  `agents/dev/loop.py:490 run_dev_round`、**零新增落树路径**、策略装载下沉 `agents/dev/` 且**装载即静态检查**、
  **例外三项替代约束（静态检查 / 执行超时 / 策略零环境对象·不触网关）全部落机检**、
  版本取 `deployment.dev.current_policy_version`（缺指针即拒绝）、产物 `kind="slate"`、
  `rejected`/`failed` 均判阶段失败且下游零调用、**`dev` 输入由显式声明的运行级映射产生**——键名与映射
  函数归本计划：`PilotInputs` 扩展 `genre_bounds`/`audience`，`target_duration_min` 改浮点分钟）；
  C3 `dev` 的预算与预检（无平台槽位、
  无 `exploration_per_round_usd`⇒走 LLM 腿档位、`budget.tiers.dev` 两形态声明缺失即拒绝启动、
  **不新增 `.chat(` 调用点**、不发明 agent↔环节映射）；C4 `dev` 产物 kind 登记（`"slate": "json"`
  与测试侧对照表双向一致）
- [contracts/dev-script-handoff.md](contracts/dev-script-handoff.md)（**C5~C7**）：C5 声明形状与
  "**每个读取输入均已声明**"（读取集机检锁定 `SCRIPT_INPUT_READS`——本计划要求它**同时**绑定下游 loop 的
  输入归一口径（`agents/screenplay/loop.py:186-204`）与调用侧读取点（`agents/pilot/stages.py:904-905`）；
  `reads` 三类（承接含改名 / 运行级 / 派生）+ 逐字段来源 + 两种模式化 + **"上游"界定为可移交要点集合** +
  **取数依据（`entries`/`production_marks`）第三类登记项**；取数入口取**恰好一条**"进入生产"标记，
  悬空/越界/要点不全 ⇒ **下游拒绝启动**并点名；导出面不因缺陷丢条目 ⇒ 拒绝是交接侧的显式行为）；
  C6 承接 / 丢弃 / 派生声明的义务（**承接 ≠ 丢弃**：`genre → topic` 是改名承接、登记在 `renames`，
  丢弃集里不得出现 `genre`；丢字段一律列全、派生不得占用上游 / 运行级 / 承接目标的键名；
  **`consistent()` 三式只在同名字段子集上成立，承接/运行级层另立断言 ①~⑥**——两条要求同时可满足）；
  C7 上游 schema 变更两侧同步（`SCHEMA_VERSION` + 导出面快照断言 + 交接声明三处缺一即红；改名承接
  **不动导出面**，只在导出面真的增删字段时触发）
- [contracts/render-index-scale.md](contracts/render-index-scale.md)（**C8~C10**）：C8 网格配置键与容量校验
  （`storyboard.render.index_grid.{rows,cols}` 必填、编/解/绘三处同取、码内 `INDEX_BITS`/`_INDEX_ROWS` 退役；
  容量下界 `2**(R·C) ≥ 该形态派生镜头数`（派生镜头数由 `agents/pilot/scale.py` **单一无环持有**，
  不新造第二个数字）、量子上界 `2**C ≤ render.width`（+ `1 ≤ R ≤ height`）；
  本计划补**全量编解码往返断言**作为最终守卫）；C9 原则一版本规则
  （网格参数经 `frame_function_hash` 进 `proxy.emotion_alignment` 版本 ⇒ **升版为必定义务、无"若"字**；
  旧 `evaluator_id@version` 节点得分与已落盘工件逐字节不变；无迁移、无改写路径）；C10 排练档表达与场景数
  配置（`pilot.scene_count`/`lines_per_scene` 唯一解析者 = `PilotConfig`、`_DEFAULT_SCENE_COUNT` 退役、
  `pilot.rehearsal` 单点解析覆盖体量键、**两处时长一致性机检（SC-012①）**、时长粒度（秒级+浮点分钟+容差）、
  `status=unstandardized` 不覆盖且如实标注、`work_kind=real_work` 不缩档且包内标记可机检、零形态分支；
  **本计划补齐**：派生镜头数持有者 `agents/pilot/scale.py`，否则渲染器与门禁会各按一份公式判定）
- [contracts/package-evidence.md](contracts/package-evidence.md)（**C11~C13**）：C11 逐环节评估分量入包
  （`state.json.eval_breakdown` **取自树节点**、键 = `evaluator_id@version`、逐环节覆盖全部候选节点、
  缺项即拒绝装配、派生产物显式标注）；C12 逐环节真实/模拟标（`manifest.stages[].source` + `channel`、
  `dev`/`script` 取 LLM 腿口径、取值只来自装配面声明、`llm.source` 与 019 运行记录逐调用 `source` 交叉
  核对、"真实渠道全链路已跑通"只在七环节全 `real` 时成立；**预检报告侧落点 = `agents/pilot/pilot.py`
  的 `_backend_report` 增 `stages:{stage_id: source}`**，E-01）；C13 性能与预算门禁的证据面 + 包可复现性
  （`pilot/profiles/{run_id}.json` 画像、阈值声明与"未标定"、**固定时钟运行恒 `not_evaluable`**、
  墙钟只落报告侧、包内逐字节可比；**本计划补**：成本三方口径的第三腿取数（环节边界采样）与不可比情形
  登记，随该证据面落盘；**且不新增 `LLMGateway(` 构造点（普查仍 13 处）、`run_report` 只读
  `total_cost_usd`**，E-03）
- [quickstart.md](quickstart.md)：验证命令 + 七步流程（预检 → 七环节跑通 → 可复现对照 → 交接审计 →
  证据面核对 → 性能画像 → 拒绝语义）+ 验收口径 + 验证记录回填区；**`--minutes` 取生效档值（浮点分钟，
  与预检"运行级 ×60 == 生效成片时长"同批定稿）**；**契约编号口径同本计划（018 C 编号 ≠ 015 编号）**

**FR → 契约落点对照**（不新增需求，只做覆盖核对）：

| FR | 承载契约 | 说明 |
| --- | --- | --- |
| FR-001 七环节串链、落在集中声明清单、漏同步即红 | **C1**（+C4） | 逐处 file:line；不变量断言替代"靠人记得" |
| FR-002 `dev` 调 017 既有轮次入口、零新增落树路径、策略走 `agents/` | **C2** | 常驻静态断言不放松；装载即静态检查**+ 执行超时 + 零环境对象**（例外三项） |
| FR-003 七环全产出方可装配、失败不产半包、五件套口径不变 | **C1/C11** | `PACKAGE_FILES` 与 `verify_package` 不改 |
| FR-004 字段级交接声明、守恒等式、可追溯 100% | **C5/C6** | `reads` 三类 + 逐字段来源 + 丢字段列全 + 取数依据登记 |
| FR-005 取数入口唯一且存在、拒绝启动、两侧同步 | **C5**（+C7） | 导出面不丢条目 ⇒ 显式拒绝而非静默降级 |
| FR-006 逐环节成本入账、含 `FAILED`、三方一致、被拒零入账 | **C13**（+C11） | 第三腿取数与不可比情形登记 |
| FR-007 LLM 腿按环节分档、`dev` 档两形态声明、缺档拒绝启动 | **C3**（+C1） | 调用点档位齐备的预检/静态双检；不发明映射 |
| FR-008 各环评估分量入包、缺项即拒绝装配 | **C11** | 从节点取数，键 = `evaluator_id@version` |
| FR-009 每环真实/模拟标注机读、不推断、不谎报 | **C12** | 装配面声明 + 019 运行记录交叉核对；预检报告 `stages`（E-01） |
| FR-010 性能画像（耗时 + 体量 + 阈值对照）、固定时钟不达标、未标定不发明 | **C13** | 报告侧 `pilot/profiles/`；结论词机读 |
| FR-011 真实渠道诚实分层、凭证属运营侧输入、禁静默回落 | **C12** | B/C 路径 `not_delivered` 期间结论恒 0 |
| FR-012 可复现口径不变（逐字节）、新增字段确定性 | **C11/C12/C13** | 无墙钟/无路径/无进程内顺序 |
| FR-013 排练档：链路不变、只改配置、真实作品用原值、两处时长一致、排练/真实分离 | **C10** | `work_kind` 标记 + 单点解析覆盖 + SC-012①（时长一致机检） |
| FR-014 全部新增参数配置化、两形态声明、缺项即报错、零形态分支 | **C10**（+C8/C13） | 登记点清单 + 静态断言 + 唯一解析者 `PilotConfig`（C-02） |
| FR-015 索引容量按派生镜头数（R×C 网格）、保留每镜一码、升版、禁改写历史 | **C8/C9** | 升版为本仓必定义务（无"若"字） |
| FR-016 剧本输入来源显式声明、禁止静默择一 | **C5** | 两种模式都要声明 + 逐字段来源齐备 |

## 规格缺口与处理（如实登记）

| # | 缺口 / 张力 | 处理 |
| --- | --- | --- |
| 1 | **规格只点名场景数**，但剧本体量还有**一处码内常量**：每场景行数（`agents/pilot/stages.py:930` 的 `range(12)`）；而页数门禁 = 场景数 × 每场景行数 ÷ `lines_per_page`（`agents/screenplay/evaluators/page_minutes.py:55-66`） | 两处一并配置化（`pilot.scene_count` + `pilot.lines_per_scene`，后者由本计划补齐并回填 data-model）；页数一致性纳入档位机检；**"缩档只改配置"在只改场景数时不成立**——该缺口由本特性闭合（C10） |
| 2 | **`dev` 轮次入口要求的输入不在运行级输入里**（`genre_bounds`/`audience`，`agents/dev/loop.py:176-191`），而交接契约把键名与映射函数留给 plan（C2 的"输入来源（判断项）"） | 运行级输入扩展两字段（缺项即预检拒绝启动）；`PilotInputs.to_dict()/fingerprint()` 同步 ⇒ **输入指纹口径变更**，如实留痕（既有 run_id 由指纹派生，测试多用显式 run_id） |
| 3 | **`dev` 无 `exploration_per_round_usd`**（`agents/dev/config.py:150-164`），与"预算面按 Agent 逐段校验"的既有形状不同 | 预检按 **LLM 腿档位**处理（既有 `pilot.py:212-221` 分支）：`model_prices` 非空 + `budget.tiers` 声明额度；**不得**为过预检在配置里发明单轮预算（该口径写入 C3） |
| 4 | **排练档数字未定**（spec 开放问题 2：运营侧输入，**不得发明数字**），而"两形态跑通"需要一组**互相一致**的取值 | 机制全部落地（`pilot.rehearsal` 单点解析 + `status`/`work_kind` + 内部一致性机检）；`status: unstandardized` 期间**不覆盖**（形态原值在 force）且如实标注；运营给定后只改本段取值（零代码改动）。**留痕**：spec 开放问题 2 仍如实登记"数字待运营给定" |
| 5 | **两处时长来源**（运行级 `target_duration_min` 与形态配置成片时长）规格未定义不一致时的处置 | 预检**一致性校验**：运行级目标时长 ×60 必须等于**生效**体量的成片时长，否则拒绝启动并点名（不静默择一；容差 `1e-6`，粒度口径见 C10）。**已否决**"删掉运行级时长只留配置"：`PilotInputs` 是既有运行输入面（CLI/测试/指纹），且 FR-016 明写运行级字段仍生效 |
| 6 | **既有测试/夹具/演示的陈旧字面量**（本特性必sync，逐处已核实）：`tests/unit/test_pilot_stages.py` 的 `Test阶段定义`（六阶段顺序与装配，当前 `:37-56`）、同文件的 `test_镜头计划在分镜渲染器上限内`（当前 `:58-73`）、`tests/contract/test_pilot_contracts.py:368-375`（六元组）、`tests/unit/test_pilot_package.py:59-66`/`:72-79`、`tests/unit/test_pilot_run.py:90-97`、`tests/unit/test_pilot_product_labels.py:36-44`/`:127`、`tests/unit/test_form_switch.py:151-159`（权重差异循环，**017 起已含 `dev`**，本次只复核）/`:260-281`、`tests/unit/test_config_integrity.py:23-37`/`:43-67`、`tests/conftest.py:3159-3163`（"不声明 `dev` 段"的书面备忘）与 `:3065-3077`（演示档注释的"16 镜上限"）、`ops/demo_pilot.py`（配置加载器计数的**写死字面量**，当前 `:83-85`；**计数随实现派生，不写数字**）、`tests/unit/test_storyboard_board_render.py` 的 `render_cfg` 夹具（当前 `:45-56`，补 `index_grid`）与 `tests/unit/test_storyboard_config.py:181`（缺项样例补键） | 全部同步更新（**不得为过测试而放宽断言**）；演示的配置加载器计数断言改为**派生量**；另：写死字面量是**既有缺陷**（今日恒假），顺手修正 |
| 7 | **019 运行记录 entry 字段集不得增删**（链式摘要输入，`core/billing/runlog.py` 的 `RUN_ENTRY_FIELDS`，当前 `:32-42`） | 第三方账目腿**不落 entry**：逐环节网关增量落 `cost.json`（确定性段），账本窗口口径落报告侧 `pilot/profiles/`；`day`/`period` 窗口如实标注"窗口累计、不作逐项比对"（不静默比对、也不静默跳过） |
| 8 | **`index_grid` 入 `storyboard.render` 会改变真实平台渲染载荷**（`agents/storyboard/platform/http_real.py:105-122` 全量透传 `cfg.render`） | 如实登记：B 路径 `not_delivered` ⇒ 无在线影响；本地 stub 契约与 `render_params` 的载荷口径须随之同步（不新增档案配置键、不重写适配器协议） |
| 9 | **"链上每个环节的 LLM 调用都经按环节分档的门禁"没有直接机检面**（FR-007 只点名 `dev` 档；链阶段 id 与 `budget.tiers` 键不一一对应，019 又禁止在代码里发明 agent↔环节映射） | 复用 019 的**调用点清单**：预检 + 静态断言"全部 `.chat(` 调用点声明的 `stage=` 取值 ∈ 两形态 `budget.tiers` 键集"（计数仍 8，`dev` 恰同名属巧合而非规则）；**不新造** stage↔环节档映射声明（多一层会漂移的声明，收益仅是"更整齐"）。`sound` 环节无 LLM 调用如实声明（`agents/pilot/stages.py` 当前 `:594` 一线附近，读源码时按结构名定位） |
| 10 | **性能阈值无既定落点**（FR-010/FR-014 要求配置化且未标定不得发明数字） | `pilot.performance`（`status` + `stage_seconds` 逐环节键；缺项即报错）；结论词取值域固定（`meets|below|not_evaluable`），`unstandardized` 与固定时钟都落 `not_evaluable`——**不产出数字结论** |
| 11 | **样片包仍是五件套**（spec 假设：不新增第六件） | 分量/标注落既有件（`state.json`/`manifest.json`/`cost.json`）；性能画像与第三方腿对账落**报告侧**——它们含墙钟耗时与窗口累计，混进五件套会破坏逐字节可比（FR-012） |
| 12 | 平台腿**无** 019 账单/账本/运行记录面（019 只实例化 LLM 渠道） | 平台腿标注**只**来自装配面声明（如实登记"无交叉核对面"）；平台腿花费不进厂商账单对账（该腿的账单证据随 B/C 路径接入） |
| 13 | **`topic` 的来源**（规格 US2 说"被标记选题的题材（`genre`）交给剧本环节"，而下游键名是 `topic`、017 导出面上没有同名键） | **已裁决（2026-09-24）：承接 `topic ← genre`（改名承接，登记在 `renames`），无 `dev` 阶段的链回落运行级 `PilotInputs.topic`**——与澄清"有 `dev` 取交接、无 `dev` 回落 run 级"同口径，也是 US2"题材逐字段可追溯"能成立的唯一读法；**`genre` 不进丢弃集**（承接 ≠ 丢弃，但改名同样必须显式声明）。契约 C5/C6 已按此定案（逐键定案表 + `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`）；**已否决**"`topic` 取运行级 + `genre` 进丢弃集"（形式上合规、语义上把题材丢了）。无待裁决项 |

## 复杂度跟踪（新增抽象论证）

| 新增抽象 | 为什么需要 | 为什么不选更简方案 |
| --- | --- | --- |
| `dev` 阶段入口 + 交接接线（`_dev_entry`/`_handoff_dev`） | FR-001 要求链首插入且复用既有轮次入口 | *在编排层直接调网关生成立项组合*：等于第二份 017 实现 + 新增落树路径（违 FR-002 与常驻断言）；*不插入*：017 已把插入动作明确归 018（`tests/conftest.py:3159-3163`） |
| 交接声明扩出"运行级"类（`reads` 三类 + 逐字段来源 + 两模式 + `dropped` 独立集 + 取数依据） | FR-004/FR-016 要求 `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生` 且运行级字段**显式声明**、禁止静默择一 | *只用手写层之外的现有 `FieldParity`*：只有两类（上游/派生），无法表达"运行级来源"，只能把运行级字段塞进 `derived`（口径失真）或放任隐式透传（违 FR-016） |
| `SCRIPT_INPUT_READS`（下游读取集常量 + 双向绑定断言） | 交接声明的下游侧必须**可双向机检**（新增输入字段即红） | *在交接侧手写一份下游键集*：无绑定的重复声明，下游新增字段即静默漏声明（正是要防的漂移） |
| `STAGE_CONFIG_SECTION` / `STAGE_TREE_PREFIX`（stage → 配置段 / 轮次树前缀的两处映射声明） | 取数（各阶段轮次树）与配置读取都需要 stage_id → 别的名字的映射，而 `script` 与 `screenplay-round-` 无法从 stage_id 推导（`agents/screenplay/loop.py:121-123`） | *用 `f"{stage_id}-round-"` 推导*：`script` 会推出 `script-round-`（错），静默取不到树/分量；*散落在各阶段入口里写死*：同一映射多份、漏改无断言 |
| 逐环节网关记账采样（阶段边界只读采样） | FR-006 三方口径缺"网关/账本记账"那条腿，而 `cost_breakdown()` 只按 (role, profile) 分解（`core/llm_gateway/gateway.py:387-398`），**不按环节** | *只用网关总额*：无法逐环节核验（"逐项差额"落空）；*改 `cost_breakdown` 加环节维*：动 016/019 的网关契约与快照口径，且网关对 billing 零 import 的边界被搅动；*新增 `LLMGateway(` 构造点去"顺便取数"*：019 的构造点普查（13 处）会失效（E-03） |
| `pilot.rehearsal`（`status`/`work_kind`/`scale` + 单点解析覆盖） | FR-013/FR-014 要求体量档配置化、真实作品用原值、排练与真实作品可机检分离 | *演示脚本里等值派生*（`ops/demo_pilot.py` 当前 `:51-61`）：改脚本即改行为、不可机检、"未标定"无处登记；*另建第二套配置副本*：形态集被 `test_form_switch` 钉死为两套，且等于为长片新造第二套链（规格明禁）；*两档各宣告一遍取值再逐项比对*：同一批数字出现两处，必然漂移 |
| `pilot.scene_count` / `pilot.lines_per_scene` 两个配置键（唯一解析者 `PilotConfig`） | 体量档要能"只改配置"缩档，而三处都是码内常量（且页数门禁 = 场景数 × 每场景行数 ÷ 每页行数） | *只把 `_DEFAULT_SCENE_COUNT` 换个新默认值*：仍是码内默认（违 FR-014），且页数门禁随场景数联动——movie 形态照样过不去；*让 `agents/screenplay/config.py` 各自读 `pilot` 段*：两处解析即两处漂移（C-02） |
| `agents/pilot/scale.py`（派生镜头数单一无环持有者） | FR-015 的容量下界与 C10 的档位/页数一致性机检都依赖同一个"派生镜头数"，而它由三个配置值算出 | *在两处各写一遍公式*（`build_shot_plan` 一份、`agents/storyboard/config.py` 一份）：渲染器与门禁各按一份数字判定、漂移无断言；*放 `agents/storyboard/` 内*：`agents/pilot/` 侧的档位机检要反向 import storyboard，依赖方向多一条反向边 |
| 报告件 `run_report.py`（性能画像 + 成本第三方腿） | 性能/账本证据含墙钟与窗口累计，**不得**进逐字节比对面，又必须**可机检** | *塞进 `manifest.json`*：破坏五件套逐字节可比（FR-012 失守）；*只打印不落盘*：不可机检、不可审计 |
| `STAGE_BACKEND_SLOT`（stage → 后端槽位显式映射） | FR-009 要求标注**只来自装配面声明**、不得推断；而 `PLATFORM_SLOTS` 不含 `dev`/`script` | *按 `resolved` 键名猜*：`script`/`dev` 无对应键 ⇒ 运行期 KeyError 或静默缺标注（谎报风险）；*逐环节写 if-else*：形态/环节分支散落（违原则五） |
| `agents/dev/policy_loader.py`（链首策略装载下沉） | 链首必须装载人工策略，且原则四例外的**三项替代约束**（装载即静态检查 / 执行超时 / 零环境对象·不触网关）必须落机检，而 `agents/` 不得 import `ops/` | *从 `ops/dev.py` 的 `_load_policy`（当前 `:81-123`）import*：撞常驻静态断言；*复制一份*：两条装载路径会漂移，静态检查（以及超时与零环境对象守护）可能在一侧静默失效 |

## 宪章复核（阶段 1 后）

原则一由 **C8/C9** 承载：`index_grid` 入 `storyboard.render` 随树 `config_snapshot` 冻结
（`agents/storyboard/loop.py` 当前 `:269`），经 `frame_function_hash` 入 `proxy.emotion_alignment` 版本
（`alignment.py` 当前 `:88-97`）⇒ 网格参数变更（含仅改配置取值）即新版本、同 `id@version` 行为不变；
旧工件内容寻址、无迁移、无改写。
原则二不受影响：链首插入走 017 既有入口（零新增落树路径，常驻断言不放松），`CostRecord`/树模型零改动，
失败照常入账、被拒零入账两条分支就地复用；**不新增 `LLMGateway(` 构造点**（构造点普查仍 13 处，E-03）。
原则三由 **C3/C12** 承载：真实渠道仍只走 LLM 腿（019 门禁 +
账单对账），平台侧保持配置默认模拟并**逐环机读标注**；LLM 腿按环节分档（`dev` 档两形态已声明、缺档即
拒绝启动，C3）。原则四**不新增执行路径**：装载下沉 `agents/dev/policy_loader.py`，例外条款的**三项替代约束**
（① 静态检查在装载时生效；② **策略执行超时**；③ **策略零环境对象 / 不触网关**——只喂 `plan(inputs, config)`，
网关调用由宿主发出并受 019 门禁约束）逐项在 T1807（测试，先写）与 T1808（实现）落机检，不放宽例外条件。
原则五由
**C2/C4/C8/C10/C13** 承载（全部新增参数两形态声明、缺项即报错、零形态分支、`agents/` 不 import `ops/`、
派生镜头数叶子模块不反向 import `agents/storyboard/*`）。
原则六由 **C12/C13** 承载（标注取值域与"模拟标为真实恒 0"、未标定不发明数字、固定时钟恒 `not_evaluable`、
不可比情形逐条登记）。治理规则：**十一项**新增抽象已在复杂度跟踪表逐条对照更简方案。**门禁通过**；两项前提——
(a) C9 的三条证据（升版可机检、旧节点与旧工件逐字节不变、无改写路径）随实现落地；
(b) C2 的**三项**替代约束（静态检查在装载时生效、执行超时、零环境对象/不触网关）全部随实现落地
——任一项未落实即视为未通过。
