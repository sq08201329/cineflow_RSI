"""立项组合工件 → G2 取数面的导出适配（功能 017 / C6）。

`export_slate(slate) -> dict`：把立项组合工件的**规范结构**交给下游（018 电影长片全链路：
选题产出 → 剧本输入），导出面 = schema 版本 + 逐条方向要点（方向标识/论证要点/可移交的
题材·约束·角色设定/条目级分量/进入生产标记位）+ 组合级"进入生产"指向 + 模拟数据源标注。

**字段级交接声明属 G2**：下游（018）据此立字段级 `FieldParity`（先例
`agents/pilot/handoffs.py`）；本模块**不写 parity 函数、不改下游 schema**——只保证导出面
确定、可反解、无对侧预写（快照为单侧：下游字段名由 G2 反解时补齐）。故对 008 先例
（`export_segment`）只取其"确定 + 不丢数据"的纪律，不取其双向锁定。

导出纪律：只做 schema 映射，**不因缺陷丢条目**——方向重复/要点缺失/条目数或标记越界是
评估器的判定对象（`rule.slate_structure` / `rule.slate_combination` 判 0 并点名），导出
照常保留全部条目与标记（**含悬空标记**），故门禁违规的工件仍可被下游反解与审计。

防漂移：导出面字段集是本侧快照的组成（`tests/unit/test_dev_export.py` 用字面量 +
`dataclasses.fields` 双向锁定）；任一 schema 变更须**同时**①升 `SCHEMA_VERSION`
②更新该快照断言——只做其一即红（此处对导出面漂移直接报错，不静默漏字段给下游）。
"""

from agents.dev.artifact import TopicSlate
from core.tree.errors import ValidationError

# G2 取数面字段集（单侧快照：本侧锁定，对侧由 G2 立 FieldParity 时反解）
EXPORT_FIELDS = ("schema_version", "entries", "production_marks", "signal_sources")
EXPORT_ENTRY_FIELDS = (
    "direction_id",
    "rationale",
    "eval_components",
    "genre",
    "constraints",
    "characters",
    "in_production",
)


def export_slate(slate: TopicSlate) -> dict:
    """立项组合工件 → G2 取数面（确定性；条目与标记逐条保真，不因门禁违规丢数据）。"""
    if not isinstance(slate, TopicSlate):
        raise ValidationError(f"export_slate 输入必须为 TopicSlate，实际为 {slate!r}")
    export = slate.to_dict()
    _assert_export_face(export)
    # 导出不丢条目与标记（缺陷工件同样保真；丢条目会让下游静默缺选题）
    exported = [entry["direction_id"] for entry in export["entries"]]
    if exported != list(slate.direction_ids()):
        raise ValidationError(
            f"导出丢条目：工件 {len(slate.entries)} 条方向 → 导出 {len(exported)} 条"
        )
    if list(export["production_marks"]) != list(slate.production_marks):
        raise ValidationError(
            "导出丢标记：组合级'进入生产'指向须逐条保真（悬空标记同样如实保留，"
            "合法性由 rule.slate_combination 判定）"
        )
    return export


def _assert_export_face(export: dict) -> None:
    """导出面漂移即报错（不静默改口径）：schema 变更须同时升版本 + 更新本侧快照断言。"""
    if tuple(export) != EXPORT_FIELDS:
        raise ValidationError(
            f"导出面顶层字段漂移：{tuple(export)} != {EXPORT_FIELDS}"
            "（schema 变更须同时升 SCHEMA_VERSION 并更新 tests/unit/test_dev_export.py 快照）"
        )
    for entry in export["entries"]:
        if tuple(entry) != EXPORT_ENTRY_FIELDS:
            raise ValidationError(
                f"导出面条目字段漂移：{tuple(entry)} != {EXPORT_ENTRY_FIELDS}"
                "（schema 变更须同时升 SCHEMA_VERSION 并更新快照断言）"
            )
