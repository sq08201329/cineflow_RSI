# 契约：分镜索引位宽 / 版本规则 / 排练档与场景数

> 对应规格 FR-013~015、SC-010/011、US1 场景 1~3、澄清第 5/8/9/10 条。
> 实现：`agents/storyboard/{board_render,config}.py`、`agents/storyboard/evaluators/alignment.py`、
> `agents/pilot/stages.py`、`configs/*.yaml`。

## C8 位宽配置键与下界校验（防复发点）

```yaml
storyboard:
  render: {fps: 8, width: …, height: …, index_bits: <int>, …}   # 缺 index_bits 即装配期报错
```

- 现状：位宽是**码内常量** `INDEX_BITS = 4`（`agents/storyboard/board_render.py:39`，
  注释"最多 16 镜/组"在 `:38`），编码/解码/绘制三处消费（`encode_index_bits` `:137-141`、
  `decode_index_code` `:144-155`、`_draw_index_code` `:203-208`）；超域即 `ValidationError`（`:139-140`）。
- **单一来源**：编码与解码**同取** `render.index_bits`（三个函数位宽参数化，`:206`/`:148`/`:205` 同源），
  码内常量退役、**不得**作静默回落值。渲染入口 `render_shot_card(..., index=…)`（`:211-229`/`:259`）
  不改签名，位宽经 `render_cfg` 传入；`render_cfg` 来源 = `StoryboardConfig.render`
  （`agents/storyboard/config.py:247`，`agents/storyboard/platform/simulated.py:53`），
  并已随树快照冻结（`agents/storyboard/loop.py:269` 的 `"render": config.render`）。
- **下界（本契约的机检核心）**：`2**index_bits >= 该形态声明的镜头数`；镜头数 = **单一派生源**
  `max(场景数, ceil(成片目标时长 / 单镜时长))`（`agents/pilot/stages.py:230-247 build_shot_plan`）——
  **不新造第二个数字**（"该形态声明的最大镜头数"即此派生值；不引入 `max_shots` 之类冗余键）。
  实测：movie 60 镜 ⇒ 位宽 ≥ 6；shortdrama 16 镜 ⇒ 位宽 ≥ 4。
- **上界（解码保真）**：`2**index_bits <= storyboard.render.width`——位块宽 `max(1, width // 2**bits)`
  在块数超像素数时互相吞并、解码失真（`:148`、`:205`）。实测 movie width 320 ⇒ ≤ 8；
  shortdrama width 144 ⇒ ≤ 7。
- 校验位置三点：装配期（`build_runtime`）、配置加载期（`_require_render`，`config.py:163-186`）、
  渲染器入口（`_require_render_cfg`，`board_render.py:71-77`，现只要求 fps/width/height，须补位宽）；
  缺失/越界 ⇒ **拒绝启动**并给出实测数字（缺位宽不得静默用 4，沿用 `handoffs._clip_spec` 的缺项拒绝口径）。
- 该键落在 `storyboard` 段内 ⇒ **无新增登记点**（`storyboard` 已在形态差异集内，
  `tests/unit/test_form_switch.py:260-281`、`tests/contract/test_pilot_contracts.py:423-440`）；
  两形态均须声明（FR-014）。
- **会变红的既有断言**（改法是把"魔数 16"换成机制，**不是放宽**）：
  `tests/unit/test_pilot_stages.py:58-73` 的 `1 <= shots <= 16` / `shots == 16` —— 改为
  `2**index_bits >= shots` 与 `2**index_bits <= render.width`（对 movie 的 60 镜而言**严于**旧式）；
  `tests/unit/test_storyboard_board_render.py:151` 的 `decode_index_code(frame) == index` 往返用例须
  补位宽参数并新增 60 镜往返（SC-010"100% 可编码"）。

### 场景

1. movie 配置（60 镜）声明位宽 6 → 装配通过；声明 4 → 拒绝启动并点名实测 60 > 16
2. 位宽 9（`2**9 = 512 > 320`）→ 拒绝启动（上界）；`render` 缺 `index_bits` → 拒绝启动
3. 逐镜往返：`decode_index_code(encode_index_bits(i)) == i` 对 `i < 60` 全成立；跨位宽渲染的帧
   序列字节不同（不得用 4 位解码器解 6 位编码）

## C9 原则一版本规则（位宽进实现哈希即升版本）

- 现状链：`frame_function_hash()`（`board_render.py:233-239`，= 本实现文件 BLAKE3 前 8 位）
  进入 `proxy.emotion_alignment` 的版本号（`agents/storyboard/evaluators/alignment.py:93-97`：
  `1.0.0` + `a<实现与口径哈希>` + `f<帧产出函数哈希>`）；版本构造器
  `implementation_version(*parts)`（`agents/storyboard/evaluators/_versioning.py:12-22`）哈希**调用方文件字节
  + 附加部件**。
- **本特性的义务（位宽参数化的两个后果）**：
  ① 文件字节变更 ⇒ `frame_function_hash()` 变更 ⇒ 对齐代理版本自动升（既有断言
     `tests/unit/test_storyboard_alignment.py:247` 的成员式校验仍成立，**不得**改为不校验）；
  ② **仅改配置值（位宽 4 → 6）也必须升版本**——否则同 `evaluator_id@version` 行为会随配置漂移，
     直接违反原则一。故**生效位宽必须进入版本材料**（作为 `implementation_version` 的附加部件，
     或并入 `frame_function_hash` 的入参），机检：同一实现下两种位宽取值 ⇒ 两个不同版本号。
- **受影响面（实测只有一处）**：读分镜卡帧像素的评估器只有 `proxy.emotion_alignment`
  （`storyboard_cards` 的唯一消费者，`alignment.py:122`）；`rule.*`（coverage/shot_grammar/axis_rule）
  与 `judge.script_fit` 不读帧 ⇒ 版本不变，**如实登记**，不得顺手全量升版本。
- 既有工件的保全：树节点为一次性 INSERT 的 immutable 行、产物为**内容寻址**，故旧位宽下已落盘的
  帧/预演 mp4/节点得分**逐字节不变**；`config_snapshot` 已冻结生效 `render` 值（`loop.py:269`），
  历史节点不受新位宽影响。**禁止**静默改写历史节点或回填旧帧。
- 版本号升后：新节点带 `evaluator_id@新version`，旧节点保留 `@旧version`；版本正则
  （`tests/unit/test_storyboard_alignment.py:241`）形状不变（新增部件只改 `a<hash>` 段内容）。

### 场景

1. 位宽 4 → 6：对齐代理版本号变化；同一批 ShotList 在新位宽下的帧哈希与得分如实变化（旧节点不变）
2. 只改配置位宽、不改文件 → 版本仍变（防"配置漂移为静默行为变更"）
3. 旧 `evaluator_id@version` 的既有节点得分与已落盘工件逐字节不变；无节点被改写

## C10 排练档表达与场景数配置（缺项即报错）

- **场景数入配置**：`pilot.scene_count` 是唯一来源（键名与落点见 [../data-model.md](../data-model.md)）；
  缺项即报错（不取码内默认）；`agents/pilot/stages.py:97 _DEFAULT_SCENE_COUNT` **退役**。
  **判断项（发现的规格缺口）**：另有一处码内硬编码 `stages.py:918`（`for scene_index in
  range(4)`，剧本计划逐场生成）规格未点名，必须一并入配置，否则"缩档只改配置"不成立；
  同处 `:930`（`range(12)`，每场景行数）为同类体量常量，一并登记。
- **排练档**：`pilot.rehearsal`（`status`/`work_kind`/`scale`）表达体量缩档——**链路与环节不变，
  仅时长/镜数/规模按档缩减**；生效值解析单点。`status: unstandardized`（运营未给定数字）⇒ 不覆盖
  （形态原值在 force）+ 如实标注"未标定"；**禁止**在配置或代码里发明数字。
- **真实作品用形态原值**：`work_kind: real_work` ⇒ 不缩档；包内 `work_kind` 标记可机检，
  排练产物**不得**被标为真实作品（FR-013）。
- **零形态分支**：`code` 侧不得出现"试水档/长片档/排练档"之类的分支判断（宪章原则五）；
  档位差异只以配置值表达；`tests/unit/test_pilot_stages.py:134-137`（无形态字面量）与
  `tests/contract/test_pilot_contracts.py:454-464`（`core/`+`agents/` 全量扫描）常驻通过。
- **演示脚本的现实落差（发现的既有事实）**：`ops/demo_pilot.py:51-61 _derive_pilot_scale` 目前在
  **脚本里**改写临时配置副本（`editing.target_duration_s` / `screenplay.target_duration_min` /
  `page_tolerance`）来缩档——这既不是"配置声明"，也不覆盖场景数（场景数仍为码内 4）。本特性须把缩档
  改为**配置声明的排练档**（演示档取值同样以配置声明 + 如实标注落在配置副本里，而不是脚本改键）。
- 断点续跑/指纹口径不变：缩档改变配置 ⇒ 配置指纹变 ⇒ 旧运行记录不可续（既有语义，链首多一环不改）。

### 场景

1. 缺 `pilot.scene_count` → 拒绝启动（不取 4）；`pilot.rehearsal` 缺 `status` → 拒绝启动
2. 声明排练档且 `status=declared` → 生效体量 = 档位值，链路七环节与交接契约一行不动
3. `status=unstandardized` → 形态原值在 force，包/画像标注"未标定"，无任何发明数字
4. `work_kind=real_work` → 不缩档；包内标记可机检地不为 rehearsal
