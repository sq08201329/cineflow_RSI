# 契约：分镜索引块网格（容量）/ 版本规则 / 排练档与场景数

> 对应规格 FR-013~015、SC-010/011/012、US1 场景 1~3、澄清第 5/8/9/10 条与本轮"索引容量"裁决。
> 实现：`agents/storyboard/{board_render,config}.py`、`agents/storyboard/evaluators/alignment.py`、
> `agents/pilot/{scale,stages}.py`、`configs/*.yaml`。

## C8 索引块网格的配置键与容量校验（防复发点）

索引条 = **帧顶 `R` 行 × `C` 列块网格**：每块 1 位（亮块 = 1 / 暗块 = 0，按整卡均值阈值判定），
块宽 `block_width = max(1, width // 2**C)`（`block_width` 两处消费，`agents/storyboard/board_render.py`
的 `decode_index_code`/`_draw_index_code`，当前 `:148`/`:205`），**容量 = `2**(R·C)`**。
旧实现是**帧顶 2 行合成 1 位/列、共 4 列 ⇒ 4 位**（`INDEX_BITS = 4`、`_INDEX_ROWS = 2`，当前 `:39-40`）——即旧口径实为 `R=1`、`C=4`，而 `_INDEX_ROWS=2` 只是解码时的**行采样高度**、不是位网格的第二维；本契约按 **R×C 个独立块（行优先）** 定义容量 `2**(R·C)`，`R` 由此成为真正的第二个维度（`movie` 需 ≥12 位 ⇒ 如 `C=8, R=2`）——
两者是**当前设置，不是口径**。

```yaml
storyboard:
  render:
    fps: 8
    width: 320
    height: 240
    index_grid: {rows: <int>, cols: <int>}   # 缺项即装配期报错（编/解/绘三处同取同一取值）
```

- **单一来源**：编码 / 解码 / 绘制三处**同取** `render.index_grid`（三处**当前**函数名
  `encode_index_bits`/`decode_index_code`/`_draw_index_code`，当前 `:137-141`/`:144-155`/`:203-208`；
  实现时随网格参数化改名亦可，但**读序与取值只有一处**），
  码内常量 `INDEX_BITS`/`_INDEX_ROWS` **退役**、**不得**作静默回落值；`_require_render_cfg`
  （当前 `:71-77`）补网格缺项校验。渲染入口 `render_shot_card(..., index=…)`（当前 `:211-229`/`:259`）
  签名不变，网格经 `render_cfg` 传入；`render_cfg` 来源 = `StoryboardConfig.render`
  （`agents/storyboard/config.py` 的 `StoryboardConfig.render`，当前 `:247`；模拟渲染器读同一段），
  并随树快照冻结（`agents/storyboard/loop.py` 的 `config_snapshot` 冻结 `render` 段，当前 `:269`）。
- **容量下界（本契约的机检核心）**：`2**(R·C) >= 该形态派生镜头数`；镜头数的**单一无环持有者**
  见 C10 的"派生镜头数"条——**不新造第二个数字**（不引入 `max_shots` 之类冗余键）。
- **量子上界（解码保真）**：`2**C <= render.width`——块宽 `max(1, width // 2**C)` 在块数超像素数时
  退化为 1 并互相吞并、解码失真（当前 `:148`、`:205`）；又 `1 <= R <= render.height`
  （索引条占用帧顶 `R` 行，解码器读 `frame[0:R, …]`）。
- **实测与取值**：movie 形态**原值**派生 `ceil(5400 s / 2.0 s) = 2700 镜` ⇒ 容量 ≥ **12 位**
  （`2**11 = 2048 < 2700 ≤ 4096 = 2**12`），例如 `C=8, R=2`（16 位，`2**8 = 256 ≤ 320` ✓）；
  shortdrama `ceil(120 s / 7.5 s) = 16 镜` ⇒ 容量 ≥ **4 位**（例如 `C=4, R=1`，`2**4 = 16 ≤ 144` ✓）。
  **派生镜头数取决于 `visual.clip_spec.duration_seconds`（配置）**：形态要更少的镜头就改单镜时长，
  **不是**把容量校验放宽。
- 校验位置三点：装配期（`build_runtime`）、配置加载期（`_require_render`，`agents/storyboard/config.py`
  的 `_require_render`，当前 `:163-186`）、渲染器入口（`_require_render_cfg`）；缺失/越界 ⇒
  **拒绝启动**并给出实测数字（缺网格不得静默回落 `4×2`，沿用 `handoffs._clip_spec` 的缺项拒绝口径）。
- 该键落在 `storyboard` 段内 ⇒ **无新增登记点**（`storyboard` 已在形态差异集内，
  `tests/unit/test_form_switch.py:260-281`、`tests/contract/test_pilot_contracts.py:423-440`）；
  两形态均须声明（FR-014）。
- **会变红的既有断言**（改法是把"魔数 16"换成机制，**不是放宽**）：
  `tests/unit/test_pilot_stages.py` 的 `test_镜头计划在分镜渲染器上限内`（当前 `:58-73`）的
  `1 <= shots <= 16` / `shots == 16` → `2**(R·C) >= shots` 与 `2**C <= render.width`
  （对 movie 的 2700 镜而言**严于**旧式）；`tests/unit/test_storyboard_board_render.py` 的
  `render_cfg` 夹具（当前 `:45-56`）补 `index_grid`、`decode_index_code(frame) == index` 往返用例
  （当前 `:151`）补网格参数并新增 **2700 镜**逐序号往返（SC-010"100% 可编码"）。

### 场景

1. movie 配置（2700 镜）声明 `{rows: 2, cols: 8}`（16 位）→ 装配通过；声明 `{rows: 2, cols: 4}`
   （8 位）→ 拒绝启动并点名实测 `2**8 = 256 < 2700`
2. `{rows: 2, cols: 9}`（`2**9 = 512 > 320`）→ 拒绝启动（量子上界）；`render` 缺 `index_grid` → 拒绝启动
3. 逐镜往返：`decode_index_code(render_shot_card(index=i)) == i` 对 `i < 2700` 全成立；跨网格渲染的
   帧序列字节不同（不得用 4 列解码器解 8 列编码）

## C9 原则一版本规则（网格参数进实现哈希 ⇒ **即升版**，无"若"字）

- 现状链：`frame_function_hash()`（`agents/storyboard/board_render.py` 的 `frame_function_hash`，
  当前 `:233-239`，= 本实现文件 BLAKE3 前 8 位）进入 `proxy.emotion_alignment` 的版本号
  （`agents/storyboard/evaluators/alignment.py` 的 `EvaluatorSpec.version` 构造，当前 `:88-97`：
  `1.0.0` + `a<实现与口径哈希>` + `f<帧产出函数哈希>`）；版本构造器
  `implementation_version(*parts)`（`evaluators/_versioning.py:12-22`）哈希**调用方文件字节 + 附加部件**。
- **本特性的义务（两项，均为强定义务、无"若"字）**：
  ① 文件字节变更（编/解/绘三处网格参数化）⇒ `frame_function_hash()` 变更 ⇒ 对齐代理版本**即升**
     （既有断言 `tests/unit/test_storyboard_alignment.py:247` 的成员式校验仍成立，**不得**改为不校验）；
  ② **仅改配置的网格取值（`rows`/`cols` 变）也必须升版本**——否则同 `evaluator_id@version` 行为会随
     配置漂移，直接违反原则一。故**生效网格参数必须进入版本材料**（作为 `implementation_version` 的
     附加部件，或并入 `frame_function_hash` 的入参），机检：同一实现下两种网格取值 ⇒ 两个不同版本号。
- **受影响面（实测只有一处）**：读分镜卡帧像素的评估器只有 `proxy.emotion_alignment`
  （`storyboard_cards` 的唯一消费者）；`rule.*`（coverage/shot_grammar/axis_rule）与 `judge.script_fit`
  不读帧 ⇒ 版本不变，**如实登记**，不得顺手全量升版本。
- 既有工件的保全：树节点为一次性 INSERT 的 immutable 行、产物为**内容寻址**，故旧网格下已落盘的
  帧/预演 mp4/节点得分**逐字节不变**；`config_snapshot` 已冻结生效 `render` 值（`loop.py` 的
  `config_snapshot`，当前 `:269`），历史节点不受新网格影响。**禁止**静默改写历史节点或回填旧帧。
- 版本号升后：新节点带 `evaluator_id@新version`，旧节点保留 `@旧version`；版本正则形状不变
  （`tests/unit/test_storyboard_alignment.py` 的版本正则与成员式校验，当前 `:241`/`:247`；
  新增部件只改 `a<hash>` 段内容）。

### 场景

1. 网格 `{rows: 2, cols: 4}` → `{rows: 2, cols: 8}`：对齐代理版本号变化；同一批 ShotList 在新网格下的
   帧哈希与得分如实变化（旧节点不变）
2. 只改配置网格、不改文件 → 版本仍变（防"配置漂移为静默行为变更"）
3. 旧 `evaluator_id@version` 的既有节点得分与已落盘工件逐字节不变；无节点被改写

## C10 排练档表达、场景数与"派生镜头数"的单一持有者（缺项即报错）

- **场景数与每场景行数入配置、解析者唯一**：`pilot.scene_count` 与 `pilot.lines_per_scene` 是体量的
  两个唯一来源（键名与落点见 [../data-model.md](../data-model.md)）；**唯一解析者 = `PilotConfig`**
  （`pilot` 段的加载器）——`agents/pilot/stages.py` 的 `build_shot_plan` 与 `build_screenplay_plan`
  **都不得自行读这两个键**，一律**经参数注入**（两处解析即两处漂移）；任何 `agents/*/config.py`
  （含 `agents/screenplay/config.py`）**不得**读 `pilot` 段。缺项即报错（不取码内默认）。
  `agents/pilot/stages.py` 的三处码内体量常量**退役**：`_DEFAULT_SCENE_COUNT`（当前 `:97`）、
  `build_screenplay_plan`（当前 `:902-960`）内的 `range(4)`（当前 `:918`）与 `range(12)`（当前 `:930`）。
- **派生镜头数 = 单一无环持有者**：`derived_shot_count =
  max(scene_count, ceil(target_duration_s / clip_duration_seconds))` 落
  **`agents/pilot/scale.py`（新；叶子模块：只做纯计算，不 import `agents/storyboard/*`）**；
  `build_shot_plan`、`agents/storyboard/config.py` 的容量下界校验（C8）、排练档一致性机检**都只读该函数**
  ——**禁止**在两处各写一遍公式（否则渲染器与门禁会各按一份数字判定）。
- **排练档**：`pilot.rehearsal`（`status`/`work_kind`/`scale`）表达体量缩档——**链路与环节不变，
  仅时长/镜数/规模按档缩减**；生效值解析单点。`status: unstandardized`（运营未给定数字）⇒ 不覆盖
  （形态原值在 force）+ 如实标注"未标定"；**禁止**在配置或代码里发明数字。
- **时长粒度（C-01 口径）**：`scale.target_duration_s` 为**秒级**（浮点，**可表达 30 秒演示档**）；
  分钟键（`scale.script_target_minutes` 与运行级 `PilotInputs.target_duration_min`）为**浮点分钟**
  （`0.5` 合法，`ops/pilot.py --minutes` 相应接受浮点、指纹格式化须确定性）；不变量
  `target_duration_s == script_target_minutes × 60`，比较容差 `1e-6`（只吸收浮点表示误差）。
- **两处时长一致性机检（SC-012①）**：`screenplay.target_duration_min × 60 == editing.target_duration_s`
  （取**排练档覆盖后的生效值**，容差同上）——不一致 ⇒ **拒绝启动并点名两处实测值**（不静默择一、
  不按其一取值）；运行级 `PilotInputs.target_duration_min × 60 == 生效成片时长` 同口径（预检硬校验）。
  movie 的修复方向按长片语义：`editing.target_duration_s = 90 × 60 = 5400 s`（现值 120 s 是从短剧形态
  抄来的 bug；shortdrama 的 120 s ↔ 2 分钟本就自洽）。
- **真实作品用形态原值**：`work_kind: real_work` ⇒ 不缩档；包内 `work_kind` 标记可机检，
  排练产物**不得**被标为真实作品（FR-013）。
- **零形态分支**：`code` 侧不得出现"试水档/长片档/排练档"之类的分支判断（宪章原则五）；
  档位差异只以配置值表达；`tests/unit/test_pilot_stages.py` 的无形态字面量断言（当前 `:127-137`）与
  `tests/contract/test_pilot_contracts.py` 的 `core/`+`agents/` 全量扫描（当前 `:454-464`）常驻通过。
- **演示脚本的现实落差（发现的既有事实）**：`ops/demo_pilot.py` 的 `_derive_pilot_scale`（当前 `:51-61`）
  目前在**脚本里**改写临时配置副本（`editing.target_duration_s` / `screenplay.target_duration_min` /
  `page_tolerance`）来缩档——这既不是"配置声明"，也不覆盖场景数（场景数仍为码内 4）。本特性须把缩档
  改为**配置声明的排练档**（演示档取值同样以配置声明 + 如实标注落在配置副本里，而不是脚本改键）；
  该演示的配置加载器计数**断言必须是派生量**（写死的字面量恒假，属既有缺陷）。
- 断点续跑/指纹口径不变：缩档改变配置 ⇒ 配置指纹变 ⇒ 旧运行记录不可续（既有语义，链首多一环不改）。

### 场景

1. 缺 `pilot.scene_count` → 拒绝启动（不取 4）；`pilot.rehearsal` 缺 `status` → 拒绝启动
2. 声明排练档且 `status=declared` → 生效体量 = 档位值，链路七环节与交接契约一行不动
3. `status=unstandardized` → 形态原值在 force，包/画像标注"未标定"，无任何发明数字
4. `work_kind=real_work` → 不缩档；包内标记可机检地不为 rehearsal
5. **两处时长不一致**（`target_duration_min: 90` 配 `target_duration_s: 120`）→ 拒绝启动，错误信息含
   两处实测值（`5400 s` / `120 s`）与差额；改 `target_duration_s: 30`（排练档演示档）配
   `script_target_minutes: 0.5` → 折算一致、装配通过（SC-012①）
6. 派生镜头数**只由 `agents/pilot/scale.py` 一处给出**：把 `clip_spec.duration_seconds` 由 2.0 调到
   20.0 ⇒ movie 派生 270 镜、容量下界随之降到 9 位；**容量校验本身不因此放宽**（改的是单镜时长）
7. 场景数/每场景行数只由 `PilotConfig` 解析：把两个键分别从配置里删掉 → 各自拒绝启动；
   `build_screenplay_plan` 收到的仍是**注入值**（源码扫描：`agents/*/config.py` 无 `pilot` 段读取）
