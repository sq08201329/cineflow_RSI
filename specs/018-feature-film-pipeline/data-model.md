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
  rehearsal:                     # 排练档（FR-013）：缩档只改这里，链路一行不动
    status: declared             # declared | unstandardized（未标定：只标注、不发明数字）
    work_kind: rehearsal         # rehearsal | real_work（真实作品用形态原值、不缩档）
    scale:                       # status=declared 时逐键齐备（缺一即报错）；取值属运营侧输入
      target_duration_s: <int>          # → 覆盖 editing.target_duration_s
      script_target_minutes: <int>      # → 覆盖 screenplay.target_duration_min
      script_tolerance_minutes: <int>   # → 覆盖 screenplay.page_tolerance
      clip_duration_seconds: <float>    # → 覆盖 visual.clip_spec.duration_seconds
  performance:                   # 性能门禁阈值（FR-010）
    status: declared             # declared | unstandardized（未标定 → 不给达标结论）
    stage_seconds: {dev: <float>, script: <float>, storyboard: <float>, visual: <float>,
                    sound: <float>, editing: <float>, promo: <float>}
                                 # 键 = 七环节 id；status=declared 时逐键齐备
```

- 生效值解析**单点**（一处解析、全链消费）：`scale` 只覆盖体量键，链路拓扑/交接契约/门禁/评估器组合
  一行不动（FR-013）；`status=unstandardized` 时**不覆盖**（形态原值在 force）且如实登记"未标定"。
- 场景数**唯一来源** = `pilot.scene_count`；`agents/pilot/stages.py:97` 的 `_DEFAULT_SCENE_COUNT` **退役**。
  **判断项（发现的规格缺口）**：场景数还有第二处码内硬编码 `stages.py:918`（`for scene_index in range(4)`），
  规格只点名 `:97`——两处必须一并入配置，否则"缩档只改配置"不成立；同处 `:930` 的 `range(12)`（每场景行数）
  属同类体量常量，一并登记。
- 现状兼容：`pilot` 段现在只有 `backend`/`llm_backend`/`overrides`（`configs/movie.yaml:591-594`、
  `configs/shortdrama.yaml:593-596`），而 `BackendSelection.from_yaml`（`agents/pilot/backends.py:91-130`）
  **不拒绝段内未知键** ⇒ 新增键不撞既有解析；但须新增 `pilot` 段加载器并登记（`agents/pilot/pilot.py:114-129`、
  `tests/unit/test_config_integrity.py:29-43` `CONFIG_CLASSES` / `:59-81` `REQUIRED_PATHS`）。
- **登记点条件项**：若两形态 `pilot` 段取值不同（排练档与性能阈值按形态声明，大概率如此），`pilot` 须并入
  `tests/unit/test_form_switch.py:260-281` 与 `tests/contract/test_pilot_contracts.py:423-440` 的
  **顶层差异集**（规格未列此点，本次勘查补登）。

## `storyboard.render.index_bits`（索引编码位宽，FR-015）

```yaml
storyboard:
  render: {fps: 8, width: 320, height: 240, index_bits: 6, …}   # 缺 index_bits 即装配期报错
```

- 单一来源：编码/解码**同取**该键（`board_render.py:137-155` 两位宽函数参数化），码内 `INDEX_BITS = 4`
  （`board_render.py:39`）不再作取值来源、也不作静默回落值。
- 下界（防复发点）：`2**index_bits >= 该形态声明的镜头数` = `max(场景数, ceil(成片目标时长 / 单镜时长))`
  （单一派生源 `stages.py:230-247`，**不新造第二个数字**）；上界（解码保真）：
  `2**index_bits <= storyboard.render.width`——位块宽 `max(1, width // 2**bits)`（`board_render.py:148`/`:205`）
  在块数超像素数时退化、位之间互相吞并。
- 实测：movie `120s / 2.0s = 60 镜`（`configs/movie.yaml:166`/`:113`，render width 320 在 `:234`）
  ⇒ 位宽 ∈ [6, 8]；shortdrama `16 镜`（`configs/shortdrama.yaml:175`/`:117`，width 144 在 `:244`）
  ⇒ 位宽 ∈ [4, 7]。两形态取值不同，而 `storyboard` 段**已在**形态差异集内 ⇒ 无新增登记点。

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
- **跨环节交接声明（FieldParity，复用+扩展）**：上游导出字段集 / 下游输入字段集 / 丢弃集 / 派生集
  （`agents/pilot/handoffs.py:35-75`）+ 新增**运行级集**；等式扩展为"四类**互斥且完备**"
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
