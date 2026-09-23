# 契约：立项组合工件 schema 与导出（`agents/dev/artifact.py`、`export_slate.py`）

> 对应规格 FR-001 / FR-011、US1 场景 5/6、US2 场景 1~3、SC-009/010。字段级约定建模自
> `agents/storyboard/shotlist.py`（纯结构化 JSON 工件）与 `agents/screenplay/artifact.py`。

## C4 立项组合工件 schema

```
SCHEMA_VERSION = "1.0.0"
SlateEntry(direction_id, rationale, eval_components, genre, constraints, characters, in_production=False)
TopicSlate(entries, signal_sources, schema_version=SCHEMA_VERSION)
TopicSlate.from_dict / to_dict / canonical_json / slate_hash / produce_ids   # produce_ids = 进入生产标记
```

- 建模对象：`shotlist.py:25`（`SCHEMA_VERSION`）、`:41/:111`（`ShotEntry`/`ShotList`）、`:131/:139`
  （`from_dict/to_dict`）、`:145`（`canonical_json`）、`:149`（`shotlist_hash`）、`:191`（`validate_*`）；
  `artifact.py:26/:278/:329/:342/:353/:357` 同款。
- 版本化：模块常量 + 实例字段 `schema_version`；字段名/枚举/必填性变更即升版（`shotlist.py:24` 口径）。
- 内容寻址：`canonical_json() = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)`
  （`shotlist.py:147`）→ `slate_hash() = blake3.blake3(canonical_json().encode()).hexdigest()`
  （`shotlist.py:151`，64 位 hex）；哈希 = 运营表幂等键分量 + 对象存储地址（plan.md 存储段）。
- 必填结构标记：条目列表非空、条目各字段非空（先例 `_require_nonempty_str`/`_require_marker_list`，
  `artifact.py:42-72`）；缺标记即拒绝，不静默降级。`eval_components` 为条目级分量呈现，实测分量仍落节点 `eval_breakdown`。
- SC-009 标注：`signal_sources` 逐条含来源标识 +"模拟非真实商业数据"，随 `to_dict()` 进产物本体
  （不得只落报告侧；标注口径同 `agents/pilot/package.py:33-36` 的 `SIMULATED_NOTE`）。

### 场景

1. 同工件两次 `canonical_json()` 逐字节一致、`slate_hash()` 逐位一致（确定性）
2. `to_dict()` → `from_dict()` 往返等价；缺必填标记即 `ValidationError`；字段变更不升版 → 快照断言红（C6）

## C5 组合条目与"本轮进入生产"标记

- `direction_id` 组合内唯一，由 `rule.slate_structure` 判定（注入重复即门禁判 0，US2 独立测试）；
  工件构造期**不代判**（规格边界情况"越界即门禁判 0，不由代码兜底"）。
- 每条必带题材/约束/角色设定要点（`genre`/`constraints`/`characters`，FR-011）：缺失 → `rule.slate_structure`
  判 0 且 `diagnostics.violations` 点名缺失字段（不留空待补）；被标记条目要点不全同此判 0。
- 标记数量须落在配置区间（默认区间 1）且指向组合内已存在条目；越界或指向不存在条目 →
  `rule.slate_combination` 判 0 并点名违规项（FR-003 / US2 场景 3）。
- 失败语义：门禁返回 `EvalResult(score=0.0, diagnostics={… "violations": [...]})`，**不抛异常**
  （先例 `evaluators/beat_structure.py:84-98`）；合成遇 `rule.*` 判 0 即短路总分 0（先例
  `evaluators/composite.py:40-42`）⇒ **gate 违规 = 总分 0 + 诊断点名违规项**。
- 配置缺项（条目数区间/标记区间/权重未声明）→ **装配期报错拒绝启动**（先例 `beat_structure.py:31-35`），
  不静默取码内默认（FR-012）。
- 无达标条目 → 如实落 0 标记 + 原因（门禁违规项或分数未达阈值），不得降格硬凑（规格边界情况）；
  若配置区间下界 > 0，0 标记即违区间 → 门禁判 0 并如实记录（判断项：规格两处口径以此统一）。

### 场景

1. 合规组合 → 两门禁满分、总分非 0；方向标识重复 / 缺要点 → `rule.slate_structure` 判 0 并点名
2. 标记数量越界 / 指向不存在条目 → `rule.slate_combination` 判 0 并点名；无达标条目 → 0 标记 + 原因
3. 缺条目数区间配置 → 装配即报错（拒绝启动，不取默认值）

## C6 导出与 schema 变更联动

```
export_slate(slate: TopicSlate) -> dict        # canonical 结构（G2 取数面）
```

- 导出确定性、只做 schema 映射，**不因缺陷丢条目**：条目与标记如实保留，缺陷由门禁判定
  （先例 `export_segment.py:22`、"缺陷工件导出不丢数据" `test_screenplay_export.py:94`）。
- 导出面含 `schema_version` + 每条目要点 + 组合级标注 → G2（018）据此立字段级 `FieldParity`
  （先例 `agents/pilot/handoffs.py:36`、`:109`）；**字段级交接声明属 G2，本特性不写 parity 函数、
  不改下游 schema**（规格假设与 FR-011）。
- 防漂移：任一 schema 变更必须**同时**①升 `SCHEMA_VERSION` ②更新快照断言（本侧 `to_dict()` 键集 +
  `dataclasses.fields` 名锁定，先例 `test_screenplay_export.py:111-146`）；只做其一即红。
- 快照为**单侧**：下游字段名由 G2 立 parity 时反解并补齐对侧断言，本特性不预写对侧。

### 场景

1. `export_slate` 两次调用逐字节一致；门禁违规组合仍导出全部条目与标记（不丢数据）
2. 改条目字段而不升版本 / 不更新快照 → 断言红；导出含 `schema_version` 且无 `FieldParity` 新增 API
