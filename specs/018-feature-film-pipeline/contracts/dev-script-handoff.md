# 契约：`dev` → 剧本 字段级交接（017 选题产出 → 剧本输入）

> 对应规格 FR-004~005、FR-016、SC-003、SC-011、US2（场景 1~7）、澄清第 3 条与第 8 条。
> 上游 = `agents/dev/export_slate.py:37 export_slate(slate)` 的导出面（017 已交付，单侧快照）；
> 下游 = 剧本阶段的输入视图（`agents/pilot/stages.py:398-441 _script_entry`）。
> 口径沿用既有 `FieldParity`（`agents/pilot/handoffs.py:35-75`）：
> `下游 == (上游 − 丢弃) ∪ 派生`，`丢弃 ⊆ 上游`、`派生 ∩ 上游 == ∅`——本特性按 FR-016 的
> **双来源**现实扩展两类：**运行级**与**改名承接**（`下游 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`，见 C5/C6）。

## C5 声明形状与"每个读取输入均已声明"（机检）

**已裁决（本契约的核心规则）**：剧本阶段实际读取的键有 4 个，逐个标类（承接 / 运行级 / 派生），
**未声明的直通禁止**；**承接不是丢弃，但同样不许静默**——改名必须登记在承接映射里。逐键定案：

| 下游键 | `dev_script_handoff`（链上有 `dev`） | `run_level_pilot_inputs`（链上无 `dev`，回落） | 备注 |
| --- | --- | --- | --- |
| `topic` | **承接** ← 被标记条目的 `genre`（**改名承接**，登记在 `renames`） | 运行级 ← `PilotInputs.topic` | 承接后 `topic` 非空由要点校验保证（`genre` 非空前置） |
| `constraints` | **承接** ← 同名要点 | 运行级 ← `PilotInputs.constraints` | 同名承接 = 恒等映射 |
| `characters` | **承接** ← 同名要点 | 运行级 ← `PilotInputs.characters` | 同上 |
| `target_duration_min` | 运行级 ← `PilotInputs.target_duration_min` | 运行级 ← 同名键 | **不在** 017 导出面上（`EXPORT_FIELDS`/`EXPORT_ENTRY_FIELDS` 共 11 个字段，`export_slate.py:25-34`）⇒ 运行级输入不退役 |

「题材（`genre`）逐字段可追溯」= 上表第 1 行；这也是 `genre` **不进丢弃集**的原因（C6 的承接 ↔ 丢弃互斥）。

```
FieldParity(label, upstream, downstream, dropped=frozenset(), derived=frozenset())   # 复用
DevScriptHandoff(                                            # 新增：双来源决策视图
    mode,               # "dev_script_handoff" | "run_level_pilot_inputs"（FR-016 二值，无第三值）
    reads,              # 读取集：键 → 类 ∈ {"承接","运行级","派生"}（合计且每键恰一类）
    renames,            # 承接映射：下游键 → 上游字段名（同名承接 = 恒等，改名承接 = 显式登记）
    parity,             # 上游 → 下游守恒声明（丢弃/派生显式；承接映射同上）
)
守恒等式（本交接）：下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生
```

- **读取集须机检锁定**，不得靠记忆：剧本阶段的读取路径 = `stages.py:904-905`（`inputs.get("topic")`/
  `inputs.get("characters")`）+ `agents/screenplay/loop.py:190-202`（`topic`/`target_duration_min`/
  `constraints`/`characters`）+ `:167`（匹配键取 `target_duration_min`）⇒
  `SCRIPT_INPUT_READS = {"topic", "target_duration_min", "constraints", "characters"}`。
  断言：**源码扫描式锁定**（两式 `inputs[` / `inputs.get(`），新增读取点而未登记即红。
- 断言（anti-recurrence）：`set(reads) == SCRIPT_INPUT_READS`——**合计**（每个读取键都有类）且
  **每键恰一类**；出现未声明读取键 ⇒ 交接拒绝，错误信息点名该键。
- **两条来源都要声明**（FR-016，禁止静默择一）：逐键取值见上表——
  - `mode = dev_script_handoff`（链上有 `dev`）：`topic`/`constraints`/`characters` 取**被标记条目的**
    可移交要点（承接类，`topic` 为 `genre` 的改名承接）；仅 `target_duration_min` 取运行级
    （`PilotInputs.target_duration_min`，`pilot.py:40-58`）。
  - `mode = run_level_pilot_inputs`（链上无 `dev`，如既有短剧试水链）：四键全为运行级
    （`stages.py:403` 的 `shared["pilot_inputs"]`）。
  - 生效 `mode` 必须**随机读可见**（`StageState.detail` + 预检报告），且同一环节**不得**一半取自
    上游、一半取自运行输入而不声明。
- 取数入口（FR-005）：`mode=dev_script_handoff` 时必须取组合内**"本轮进入生产"标记恰好一条**且
  指向组合内已存在条目的那一条（`TopicSlate.marked_entries`，`artifact.py:273-275`）；悬空/越界/
  多条/缺失 ⇒ **下游拒绝启动**并点名原因（不得静默取第一条兜底、不得伪装成"选题为空"）。
- 上游导出面**不因缺陷丢条目**（含悬空标记，`export_slate.py:43-53`）——故"拒绝"是交接侧的**显式
  拒绝**（`HandoffError` 语义），不是静默降级。

## C6 承接 / 丢弃 / 派生声明的义务

- **承接 ≠ 丢弃（本契约的绑定区分）**：上游字段进入下游，要么**承接**（可改名，登记在 `renames`）、
  要么**丢弃**（登记在 `dropped`），两者互斥且完备。**改名承接必须显式**：`genre → topic` 是**承接**
  （不是"丢 `genre` + 派生 `topic`"）——若只按 `FieldParity` 的 frozenset 记，这条改名会被错记成
  "丢一字段 + 派生一字段"（形式上合规、语义上把题材丢了），故本交接用承接映射（`renames`）承担改名：
  `genre → topic` 逐条登记，同名承接（`constraints`/`characters`）登记为恒等映射。
- `丢弃 ⊆ 上游字段集`，且上游每个既未承接又未进入下游的字段都必须在丢弃集里（**不许静默丢**，沿用
  `handoffs.py:8-11` 纪律）。本交接的丢弃项：`direction_id`/`rationale`/`eval_components`/`in_production`
  与顶层 `schema_version`/`signal_sources`；`production_marks` **不进下游读取集**，但它是**取数依据**
  （C5 的选条入口），须在声明里与"丢弃"区分登记，不得混为一谈。条目级 `genre`/`constraints`/`characters`
  三个可移交要点**全部承接**（`genre` 改名承接为 `topic`）——**丢弃集里不得出现 `genre`**。
- **判断项（承接 ≠ 免校验）**：三个可移交要点任一为空仍须**拒绝启动**（US2 场景 4），且该校验必须在
  **承接之前**判——`genre` 空 ⇒ `topic` 承接到空值 ⇒ 要到下游 `_validate_inputs`
  （`agents/screenplay/loop.py:190-202`）才炸，属"逃到下游才炸"。第二道防线与 017 的
  `rule.slate_structure` 判 0 口径同源（`artifact.py:191-193 handoff_complete`）。承接不解除要点校验。
- `派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`：派生键必须**声明来源**（形态配置或上游字段变换），
  不得占用上游、运行级或承接目标的键名；派生键的出现顺序与取值必须确定性（不得含墙钟/随机/进程内顺序）。
- `FieldParity.consistent()`（`handoffs.py:50-56`）的既有三式**原样保留**于"上游 → 下游"映射层
  （同名承接下逐式成立）；改名承接与运行级键不进该层，由 `renames` 与 `reads` 两处声明承担——两者不互相放宽。

## C7 上游 schema 变更的两侧同步

- 三处**缺一即红**：① `agents/dev/artifact.py:32 SCHEMA_VERSION` 升版；② 017 侧导出面快照断言
  （`tests/unit/test_dev_export.py`，字面量 + `dataclasses.fields` 双向锁定，见
  `specs/017-dev-agent-degraded/contracts/dev-artifact.md` C6）；③ **本侧交接声明**（C5/C6 的
  上游字段集与逐键类）。只改其一即导出面漂移报错（`export_slate._assert_export_face`，
  `export_slate.py:57-68`）或本侧交接断言红。
- 017 明确**不写 parity 函数、不改下游 schema**（`specs/017-dev-agent-degraded/contracts/dev-artifact.md`
  C6）——对侧断言由**本特性**补齐：上游字段集的唯一真相仍是 `EXPORT_FIELDS`/`EXPORT_ENTRY_FIELDS`，
  本侧只做**反解引用**（import 该常量），不得复制一份字面量。
- **改名承接不动导出面**：`genre` 仍在 017 导出面上（改名发生在**本侧**承接层，`genre → topic`），
  故本特性**不**要求任何导出面字段增删、也**不**触发 017 侧快照变更；三处同步义务只在导出面真的增删
  字段时生效。变更的只是本侧 `renames`/`reads`/`parity` 三处声明。
- 导出确定性不变：同 `TopicSlate` 两次 `export_slate` 逐字节一致；门禁违规组合仍导出全部条目与标记。

### 场景

1. 合规立项组合 + 标记恰好一条 → 契约成立，`reads` 四键各有类；`topic` 可回溯到上游 `genre`（改名承接，
   `renames` 里有条目），`constraints`/`characters` 同名承接，`target_duration_min` 标运行级
2. 上游导出面增删字段而未同步声明 → 断言红（两侧不同步；改名承接本身不触发此红，见 C7）
3. 标记悬空 / 越界 / 多条 / 缺失 → **下游拒绝启动**且错误信息点名（`dev` 失败时下游零调用、如实 `skipped`）
4. 被标记条目的 `genre`/`constraints`/`characters` 任一为空 → 拒绝并点名缺失字段（承接不解除此校验；
   `genre` 空即拒，不许等到下游拿空 `topic`）
5. 无 `dev` 阶段的链路 → `mode=run_level_pilot_inputs`，四键全运行级（含 `topic ← PilotInputs.topic`）；
   生效 mode 随机读可见
6. 下游读取集新增一个键而未登记类 → 红（不得静默直通）；把 `genre` 写进丢弃集 → 红（承接 ↔ 丢弃互斥）
