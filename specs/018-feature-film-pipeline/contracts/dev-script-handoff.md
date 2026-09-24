# 契约：`dev` → 剧本 字段级交接（017 选题产出 → 剧本输入）

> 对应规格 FR-004~005、FR-016、SC-003、SC-011、US2（场景 1~7）、澄清第 3 条与第 8 条。
> 上游 = `agents/dev/export_slate.py:37 export_slate(slate)` 的导出面（017 已交付，单侧快照）；
> 下游 = 剧本阶段的输入视图（`agents/pilot/stages.py` 的 `_script_entry`，当前 `:398-441`）。
> **本特性的守恒口径**：`下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`，
> `丢弃 ⊆ 上游`、`丢弃 ∩ 承接映射值集 == ∅`、`派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`
> ——来源分类是 `reads` 的**三类**（承接 / 运行级 / 派生），`dropped` 是与之**并列的独立集合**
> （**不是**同一分类的第四项），此外还有一类**取数依据**（`entries`/`production_marks`，见 C5）。
> 既有 `FieldParity` 的三式**只在同名字段子集上**复用，口径边界见 C6；本特性按 FR-016 的
> **双来源**现实扩展：**运行级**与**改名承接**（见 C5/C6）。

## C5 声明形状与"每个读取输入均已声明"（机检）

**已裁决（本契约的核心规则）**：剧本阶段实际读取的键有 4 个，逐个标类（承接 / 运行级 / 派生），
**未声明的直通禁止**；**承接不是丢弃，但同样不许静默**——改名必须登记在承接映射里。

**两个概念先界定（B-01）**：

- **"上游"在本契约中等同于"可移交要点集合"**（条目级 `genre`/`constraints`/`characters`），
  而不是整张 017 导出面——导出面还带方向标识、论证要点、分量、条目标记与顶层 `schema_version`/
  `signal_sources`，那些**不进**本交接的守恒域（它们或进 `dropped`，或属取数依据）。
- **取数依据（第三类登记项）**：`entries` 与 `production_marks` **不是**读取键、**不进** `reads`、
  也**不进** `dropped`——它们是"哪一条进入生产"的**选条入口**依据，必须与"丢弃"分开登记
  （混为一谈会把"用来选条"错记成"丢给下游"）。逐键定案：

| 下游键 | `dev_script_handoff`（链上有 `dev`） | `run_level_pilot_inputs`（链上无 `dev`，回落） | 备注 |
| --- | --- | --- | --- |
| `topic` | **承接** ← 被标记条目的 `genre`（**改名承接**，登记在 `renames`） | 运行级 ← `PilotInputs.topic` | 承接后 `topic` 非空由要点校验保证（`genre` 非空前置） |
| `constraints` | **承接** ← 同名要点 | 运行级 ← `PilotInputs.constraints` | 同名承接 = 恒等映射 |
| `characters` | **承接** ← 同名要点 | 运行级 ← `PilotInputs.characters` | 同上 |
| `target_duration_min` | 运行级 ← `PilotInputs.target_duration_min` | 运行级 ← 同名键 | **不在** 017 导出面上（`EXPORT_FIELDS`/`EXPORT_ENTRY_FIELDS` 共 11 个字段，`export_slate.py:25-34`）⇒ 运行级输入不退役 |

| 登记项 | 内容 | 与 `reads`/`dropped` 的关系 |
| --- | --- | --- |
| `reads` | 4 个读取键 → 类（承接 / 运行级 / 派生），**合计且每键恰一类** | 三类来源，见下表守恒等式 |
| `dropped` | 上游**可移交要点集合**之外、又不进下游的上游字段（`direction_id`/`rationale`/`eval_components`/`in_production` 与顶层 `schema_version`/`signal_sources`） | 与 `reads` **并列的独立集合**（承接 ↔ 丢弃互斥：承接过的字段不得出现在 `dropped`） |
| `取数依据` | `entries` / `production_marks`（选"本轮进入生产"那一条） | **既不**是读取键、**也不**是丢弃项——只登记为选条入口依据 |

「题材（`genre`）逐字段可追溯」= 上表第 1 行；这也是 `genre` **不进丢弃集**的原因（C6 的承接 ↔ 丢弃互斥）。

```
FieldParity(label, upstream, downstream, dropped=frozenset(), derived=frozenset())   # 复用（同名字段子集）
DevScriptHandoff(                                            # 新增：双来源决策视图
    mode,               # "dev_script_handoff" | "run_level_pilot_inputs"（FR-016 二值，无第三值）
    reads,              # 读取集：键 → 类 ∈ {"承接","运行级","派生"}（合计且每键恰一类）
    renames,            # 承接映射：下游键 → 上游字段名（同名承接 = 恒等，改名承接 = 显式登记）
    dropped,            # 独立集合（上游 − 承接目标）；承接 ↔ 丢弃互斥
    derived,            # 派生键集合（须声明来源；不得占用上游/运行级/承接目标键名）
    sources,            # 取数依据登记：{"entries", "production_marks"}（选条入口，不属读取键/丢弃项）
)
守恒等式（本交接）：下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生
```

- **读取集须机检锁定**，不得靠记忆：剧本阶段的读取路径 = `stages.py:904-905`（`inputs.get("topic")`/
  `inputs.get("characters")`）+ `agents/screenplay/loop.py:190-202`（`topic`/`target_duration_min`/
  `constraints`/`characters`）+ `:167`（匹配键取 `target_duration_min`）⇒
  `SCRIPT_INPUT_READS = {"topic", "target_duration_min", "constraints", "characters"}`。
  断言：**源码扫描式锁定**（两式 `inputs[` / `inputs.get(`），新增读取点而未登记即红。
- 断言（anti-recurrence）：`set(reads) == SCRIPT_INPUT_READS`——**合计**（每个读取键都有类）且
  **每键恰一类**；出现未声明读取键 ⇒ 交接拒绝，错误信息点名该键。**完整断言集见 C6 的 ①~⑥**
  （含 `renames` 覆盖完备、承接 ↔ 丢弃互斥、取数依据登记完备）。
- **两条来源都要声明**（FR-016，禁止静默择一）：逐键取值见上表——
  - `mode = dev_script_handoff`（链上有 `dev`）：`topic`/`constraints`/`characters` 取**被标记条目的**
    可移交要点（承接类，`topic` 为 `genre` 的改名承接）；仅 `target_duration_min` 取运行级
    （`agents/pilot/pilot.py` 的 `PilotInputs`，当前 `:40-58`）。
  - `mode = run_level_pilot_inputs`（链上无 `dev`，如既有短剧试水链）：四键全为运行级
    （`agents/pilot/stages.py` 的 `_script_entry` 读 `shared["pilot_inputs"]`，当前 `:403`）。
  - 生效 `mode` 必须**随机读可见**（`StageState.detail` + 预检报告），且同一环节**不得**一半取自
    上游、一半取自运行输入而不声明。
- 取数入口（FR-005）：`mode=dev_script_handoff` 时必须取组合内**"本轮进入生产"标记恰好一条**且
  指向组合内已存在条目的那一条（`TopicSlate.marked_entries`，`agents/dev/artifact.py` 当前 `:273-275`）；
  悬空/越界/多条/缺失 ⇒ **下游拒绝启动**并点名原因（不得静默取第一条兜底、不得伪装成"选题为空"）。
- 上游导出面**不因缺陷丢条目**（含悬空标记，`agents/dev/export_slate.py` 的导出实现，当前 `:43-53`）
  ——故"拒绝"是交接侧的**显式拒绝**（`HandoffError` 语义），不是静默降级。

## C6 承接 / 丢弃 / 派生声明的义务

- **承接 ≠ 丢弃（本契约的绑定区分）**：上游**可移交要点**进入下游，要么**承接**（可改名，登记在
  `renames`）、要么**丢弃**（登记在 `dropped`），两者**互斥**（同一上游字段不得既承接又丢弃）
  且对**可移交要点集合**完备（要点之外的上游字段一律进 `dropped`，不许静默丢）——
  `reads` 的三类来源与 `dropped` 是**并列的两个集合**，不是同一分类的四个互斥项（C5）。
  **改名承接必须显式**：`genre → topic` 是**承接**
  （不是"丢 `genre` + 派生 `topic`"）——若只按 `FieldParity` 的 frozenset 记，这条改名会被错记成
  "丢一字段 + 派生一字段"（形式上合规、语义上把题材丢了），故本交接用承接映射（`renames`）承担改名：
  `genre → topic` 逐条登记，同名承接（`constraints`/`characters`）登记为恒等映射。
- `丢弃 ⊆ 上游可移交要点集合`，且上游每个既未承接又未进入下游的字段都必须在丢弃集里（**不许静默丢**，
  沿用 `agents/pilot/handoffs.py` 模块头的字段集纪律，当前 `:8-11`）。本交接的丢弃项：
  `direction_id`/`rationale`/`eval_components`/`in_production` 与顶层 `schema_version`/`signal_sources`；
  `entries`/`production_marks` **不进下游读取集、也不进丢弃集**，它们登记为**取数依据**
  （C5 的选条入口，第三类登记项），须与"丢弃"区分登记、不得混为一谈。条目级
  `genre`/`constraints`/`characters` 三个可移交要点**全部承接**（`genre` 改名承接为 `topic`）
  ——**丢弃集里不得出现 `genre`**。
- **判断项（承接 ≠ 免校验）**：三个可移交要点任一为空仍须**拒绝启动**（US2 场景 4），且该校验必须在
  **承接之前**判——`genre` 空 ⇒ `topic` 承接到空值 ⇒ 要到下游 `_validate_inputs`
  （`agents/screenplay/loop.py:190-202`）才炸，属"逃到下游才炸"。第二道防线与 017 的
  `rule.slate_structure` 判 0 口径同源（`artifact.py:191-193 handoff_complete`）。承接不解除要点校验。
- `派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`：派生键必须**声明来源**（形态配置或上游字段变换），
  不得占用上游、运行级或承接目标的键名；派生键的出现顺序与取值必须确定性（不得含墙钟/随机/进程内顺序）。
- `FieldParity.consistent()`（`agents/pilot/handoffs.py` 的 `FieldParity.consistent`，当前 `:50-56`）
  的三式**只适用于同名字段子集**，且**原样保留**于"上游 → 下游"的同名映射层：
  同名字段子集成立 `下游同名键集 == (上游同名键集 − 丢弃) ∪ 派生`。**改名承接与运行级键不进该层**——
  在 `renames` 引入改名承接后，下游键名与上游字段名不再逐一对应，`consistent()` 的逐式**不可能**
  也不应在新层成立（否则 `genre → topic` 只能被错记成"丢 `genre` + 派生 `topic`"）。
  **改名承接层因此不复用 `consistent()`，另立断言（机检等价表述）**：
  ① `set(reads) == SCRIPT_INPUT_READS`（合计且每键恰一类）；
  ② `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`；
  ③ `丢弃 ⊆ 上游 ∧ 丢弃 ∩ (承接映射值集 ∪ 运行级键集 ∪ 派生键集) == ∅`（承接 ↔ 丢弃互斥）；
  ④ `派生 ∩ (上游 ∪ 运行级 ∪ 承接映射值集) == ∅`；
  ⑤ `set(renames) == {k for k, cls in reads.items() if cls == "承接"}`（承接类键**逐一**有映射，
  同名承接登记为恒等映射，不得漏登）；
  ⑥ `sources == {"entries", "production_marks"}`（取数依据登记完备，且与 `reads`/`dropped` 无交集）。
  **两层断言不互相放宽**：同名层保留 `consistent()`，承接/运行级层用上式①~⑥，二者都必须绿。

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
6. 下游读取集新增一个键而未登记类 → 红（不得静默直通）；把 `genre` 写进丢弃集 → 红（承接 ↔ 丢弃互斥）；
   承接类键漏登 `renames`（或写成"丢 `genre` + 派生 `topic`"）→ 红（C6 断言⑤）
7. **两层断言各自成立**：同名映射层 `consistent()` 绿 ∧ 承接/运行级层 C6 ①~⑥ 绿；把 `entries`/
   `production_marks` 挪进 `reads` 或 `dropped` → 红（取数依据是与二者并列的第三类登记项，C6 断言⑥）
