# 数据模型：电影长片全链路编排（018-feature-film-pipeline）

> 存储分层：**配置**（`configs/*.yaml` 的 `pilot` / `storyboard.render` 新增键）→ **运行记录**
> （`RunRecord`/`StageState`——`core/orchestration/models.py`；本特性**不改字段**，只把链从六环节扩到
> 七环节）→ **样片包**（`pilot/packages/{run_id}/` 五件套，`agents/pilot/package.py:31`；新增字段落
> `manifest.json` / `state.json` **既有件内**，不新增第六件）→ **报告侧**（`pilot/profiles/{run_id}.json`
> 性能画像：墙钟耗时**只**在此侧）。账目沿用 `core/orchestration/ledger.py`，树节点沿用各 Agent 既有落盘
> （`dev` 段用 `agents/dev/db.py:60 create_jobs_schema`，与 `stages.py:173-180` 建表段同列）。字段级约定见
> [contracts/pipeline-chain.md](contracts/pipeline-chain.md)、
> [dev-script-handoff.md](contracts/dev-script-handoff.md)、
> [render-index-scale.md](contracts/render-index-scale.md)、
> [package-evidence.md](contracts/package-evidence.md)。

## `pilot` 段配置 schema（新增键，两形态均须声明）

```yaml
pilot:
  scene_count: <int>             # 场景数（FR-014）：缺项即报错，不取码内默认
  lines_per_scene: <int>         # 每场景行数（FR-014）：同上（页数门禁的另一半输入）
  rehearsal:                     # 排练档（FR-013）：缩档只改这里，链路一行不动
    status: declared             # declared | unstandardized（未标定：只标注、不发明数字）
    work_kind: rehearsal         # rehearsal | real_work（真实作品用形态原值、不缩档）
    scale:                       # status=declared 时逐键齐备（缺一即报错）；取值属运营侧输入
      target_duration_s: <float>        # 秒级（可表达 30 秒演示档）→ 覆盖 editing.target_duration_s
      script_target_minutes: <float>    # 浮点分钟（0.5 合法）→ 覆盖 screenplay.target_duration_min
      script_tolerance_minutes: <float> # 浮点分钟 → 覆盖 screenplay.page_tolerance
      clip_duration_seconds: <float>    # → 覆盖 visual.clip_spec.duration_seconds
  performance:                   # 性能门禁阈值（FR-010）
    status: declared             # declared | unstandardized（未标定 → 不给达标结论）
    stage_seconds: {dev: <float>, script: <float>, storyboard: <float>, visual: <float>,
                    sound: <float>, editing: <float>, promo: <float>}
                                 # 键 = 七环节 id；status=declared 时逐键齐备
```

- 生效值解析**单点**（一处解析、全链消费）：`scale` 只覆盖体量键，链路拓扑/交接契约/门禁/评估器组合
  一行不动（FR-013）；`status=unstandardized` 时**不覆盖**（形态原值在 force）且如实登记"未标定"。
  不变量 `target_duration_s == script_target_minutes × 60`（容差 `1e-6`，只吸收浮点表示误差）；
  运行级 `PilotInputs.target_duration_min` 亦为**浮点分钟**，预检硬校验其 ×60 等于**生效**成片时长。
- 场景数与每场景行数的**唯一解析者 = `PilotConfig`**（`pilot` 段的加载器）：`build_shot_plan`/
  `build_screenplay_plan` **经参数注入**取值，`agents/*/config.py`（含 `agents/screenplay/config.py`）
  **不得**读 `pilot` 段（两处解析即两处漂移）。`agents/pilot/stages.py` 的三处码内体量常量**退役**：
  `_DEFAULT_SCENE_COUNT`（当前 `:97`）、`build_screenplay_plan`（当前 `:902-960`）内的 `range(4)`
  （当前 `:918`）与 `range(12)`（当前 `:930`）。
- **派生镜头数的单一无环持有者**：`derived_shot_count =
  max(scene_count, ceil(target_duration_s / clip_duration_seconds))` 落 `agents/pilot/scale.py`（新；
  叶子模块），`build_shot_plan`、`storyboard` 侧容量校验（见下）与排练档一致性机检**都只读该函数**
  ——**禁止**在两处各写一遍公式。
- 现状兼容：`pilot` 段现在只有 `backend`/`llm_backend`/`overrides`（`configs/movie.yaml` 当前
  `:591-594`、`configs/shortdrama.yaml` 当前 `:593-596`），而 `BackendSelection.from_yaml`
  （`agents/pilot/backends.py`，当前 `:91-130`）**不拒绝段内未知键** ⇒ 新增键不撞既有解析；
  但须新增 `pilot` 段加载器并登记（`agents/pilot/pilot.py` 的 `config_completeness` 加载器元组，
  当前 `:117-131`；`tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES`/`REQUIRED_PATHS`，
  当前 `:23-37`/`:43-67`）。
- **登记点条件项**：若两形态 `pilot` 段取值不同（排练档与性能阈值按形态声明，大概率如此），`pilot` 须并入
  `tests/unit/test_form_switch.py:260-281` 与 `tests/contract/test_pilot_contracts.py:423-440` 的
  **顶层差异集**（规格未列此点，本次勘查补登）。

## `storyboard.render.index_grid`（索引块网格与容量，FR-015）

```yaml
storyboard:
  render: {fps: 8, width: 320, height: 240, index_grid: {rows: 2, cols: 8}, …}   # 缺 index_grid 即装配期报错
```

- 索引条 = **帧顶 `R` 行 × `C` 列块网格**，**容量 = `2**(R·C)`**；现状 `INDEX_BITS = 4` 与
  `_INDEX_ROWS = 2`（`board_render.py` 当前 `:39-40`）是**当前设置而非口径**，两常量**退役**。
- 单一来源：编码/解码/绘制**同取**该键（三个函数网格参数化，`board_render.py` 当前
  `:137-141`/`:144-155`/`:203-208`），不再作取值来源、也不作静默回落值。
- 容量下界（防复发点）：`2**(R·C) >= 该形态派生镜头数`（持有者见上，**不新造第二个数字**）；
  量子上界（解码保真）：`2**C <= render.width`——块宽 `max(1, width // 2**C)`
  （`board_render.py` 当前 `:148`/`:205`）在块数超像素数时退化、位之间互相吞并；又 `1 <= R <= height`。
- 实测：movie **原值** `5400 s / 2.0 s = 2700 镜`（`editing.target_duration_s` 按长片语义修正为
  `90 × 60`；`clip_spec.duration_seconds: 2.0`，`configs/movie.yaml` 当前 `:113`，render width 320 在
  `:234`）⇒ 容量 ≥ **12 位**（如 `rows: 2, cols: 8` = 16 位，`2**8 = 256 ≤ 320`）；
  shortdrama `120 s / 7.5 s = 16 镜`（当前 `:117`，width 144 在 `:244`）⇒ 容量 ≥ **4 位**
  （如 `rows: 1, cols: 4`）。**镜头数取决于 `clip_spec.duration_seconds`**：要更少镜头就改单镜时长，
  **不是**把容量校验放宽。两形态取值不同，而 `storyboard` 段**已在**形态差异集内 ⇒ 无新增登记点。

## 领域模型

- **七环节运行记录（RunRecord，复用）**：`run_id` + `form` + `config_fingerprint`/`input_fingerprint`
  + 七 `StageState`（`status`/`started_at`/`finished_at`/`cost_usd`/`products`/`candidates`/`detail`）
  + `failure_stage`/`failure_reason`；链首多一环**不改字段**（`core/orchestration/models.py:397-467`）
- **阶段定义（StageSpec，复用）**：`stage_id`/`depends_on`/`entrypoint`/`handoff`/`output_kind`
  （`models.py:202-228`）；本特性新增链首一段（`output_kind="slate"`）
- **单一映射声明（新增）**：`STAGE_CONFIG_SECTION`（stage_id → 配置段名：`dev→dev`、`script→screenplay`…）
  与 `STAGE_TREE_PREFIX`（stage_id → 轮次树前缀：`script→screenplay`、`dev→dev`…）——一处声明、全链消费；
  **不得**用 `f"{stage_id}-round-"` 式推导（`script` 推出 `script-round-` 即错，实为 `screenplay-round-`，
  `agents/screenplay/loop.py:121-123`）
- **跨环节交接声明（FieldParity 复用 + DevScriptHandoff 扩展）**：上游**可移交要点集合** → 下游读取集，
  `reads` 为**三类**来源（承接含改名 / 运行级 / 派生），`dropped` 是与之**并列的独立集合**
  （**不是**同一分类的第四项），另有第三类登记项**取数依据**（`entries`/`production_marks`，选条入口）；
  声明形状与断言集见 [contracts/dev-script-handoff.md](contracts/dev-script-handoff.md) C5/C6
  （守恒等式 `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`）
- **选题产出导出面（SlateExport，017 交付）**：`EXPORT_FIELDS` / `EXPORT_ENTRY_FIELDS`
  （`agents/dev/export_slate.py:25-34`）；变更须两侧同步
- **环节来源标注（StageChannelMarking，新增）**：每环节 `source ∈ real|simulated`（019 保留值 `fallback`
  出现即原样标注，**不得折叠进 real**）+ 取值出处（装配面声明，`agents/pilot/backends.py:146-151`）
- **性能画像（PerformanceProfile，新增）**：各环节墙钟耗时（`StageState.started_at/finished_at`，
  `models.py:242-243`）+ 体量指标 + 阈值快照 + 结论（`meets|below|not_evaluable`）+ 时钟口径声明；
  固定时钟运行恒 `not_evaluable`
- **环节评估分量（StageEvalBreakdown，新增）**：每环节树节点 `eval_breakdown`（键 = `evaluator_id@version`，
  `core/evaluators/base.py:69-72`；落点 `core/tree/models.py:76`）外化入包
- **最小可行长片档（FeatureScaleProfile，新增）**：`pilot.rehearsal` 档位 + `work_kind` 标记 + 与形态
  原值对照；档位数字属运营侧输入，未标定即如实登记
- **剧本输入来源声明（ScriptInputSource）**：`dev_script_handoff` | `run_level_pilot_inputs`；生效来源
  随机读可见（`StageState.detail` + 预检报告），禁止静默择一
- **账目对账（CostLedger，复用）**：`by_stage`/`lines`/`reconciled`（`core/orchestration/ledger.py:64-139`），
  从六环节扩到七环节

## 身份与唯一性 / 生命周期

| 对象 | 身份 | 生命周期 |
| --- | --- | --- |
| RunRecord | `run_id`（= 配置+输入指纹派生，`agents/pilot/pilot.py:290-292`） | 落 `pilot/runs/{run_id}.json`；续跑同键幂等、指纹不一致即拒绝 |
| 各环节轮次树 | `round_tree_id(f"{run_id}-{stage_id}")` + Agent 前缀 | 节点一次性 INSERT（immutable）；`FAILED` 同样入账 |
| 样片包 | `pilot/packages/{run_id}/` | 七环节全 `done` 才装配；缺件/账目不符/分量缺项 ⇒ 拒绝装配（零半包） |
| 性能画像 | `(run_id, 时钟声明)` | 报告侧 append-only；**不**参与包内逐字节比对 |
| 交接声明 | 声明表本身（模块常量 + 快照断言） | 随源码冻结；上游 schema 变更须三处同步 |

## 样片包文件契约（五件套不变）

| 文件 | 新增内容 | 确定性 |
| --- | --- | --- |
| `manifest.json` | `stages[].source`（`real|simulated`）、`stages[].channel`、`channels` 汇总、`work_kind`（rehearsal/real_work） | 全取自配置声明；无墙钟/路径 ✓ |
| `products.json` | 七环节齐备（含 `dev` 的 `slate` kind 登记，`package.py:173-181`） | 内容寻址 ✓ |
| `cost.json` | 七环节 `by_stage`/`lines`（键集须覆盖 `PILOT_STAGE_IDS`） | 金额十进制常量 ✓ |
| `state.json` | `eval_breakdown`（逐环节，取自树节点）、`volume`（镜头数/片段数/成片时长/页数） | 树节点与配置派生 ✓ |
| `reel.mp4` | 不变（字节取自剪辑阶段产物，`package.py:324-332`） | 内容寻址 ✓ |
