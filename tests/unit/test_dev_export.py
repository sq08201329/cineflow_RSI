"""立项组合 → G2 取数面导出单测（功能 017 / T1746，先于实现编写）。

契约 C6 落点：`export_slate(slate) -> dict`——导出确定性、**只做 schema 映射、不因缺陷丢
条目**（方向重复/要点缺失/条目数或标记越界是门禁的判定对象，导出照常保留全部条目与标记，
**含悬空标记**）、导出面含 `schema_version`、导出面字段集**单侧快照**锁定（下游字段名待
G2（018）立字段级 `FieldParity` 时反解补齐）。

**本侧不预写 `FieldParity`**：字段级交接声明属 G2，本特性只交付可反解、确定、不丢数据的
导出面——故本文件既断言导出面存在，也**显式断言 parity 声明缺席**（运行时属性 + AST 符号 +
导出面键三处），防后续"顺手补齐"。
"""

import ast
import pathlib
from dataclasses import fields

import pytest

import agents.dev.export_slate as export_slate_module
from agents.dev.artifact import SCHEMA_VERSION, SlateEntry, TopicSlate
from agents.dev.export_slate import EXPORT_ENTRY_FIELDS, EXPORT_FIELDS, export_slate
from core.tree.errors import ValidationError

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "agents" / "dev" / "export_slate.py"
DEV_PACKAGE = REPO_ROOT / "agents" / "dev"
# 缺陷变体：工件构造期全部合法（越界不由工件兜底），由两门禁判 0 并点名
DEFECT_VARIANTS = (
    "duplicate_direction",
    "missing_essentials",
    "count_out_of_range",
    "marks_out_of_range",
    "dangling_mark",
)


def _dev_sources() -> list[pathlib.Path]:
    files = sorted(path for path in DEV_PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)
    assert files, "未找到 agents/dev 源码（包落点变了？）"
    return files


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _symbol_names(tree: ast.Module) -> list[str]:
    """代码里的符号名（定义 / 赋值 / 导入别名）——不含文档字符串与字面量文本。

    模块文档字符串必须**说明**"字段级 parity 属 G2"这条边界，故裸文本扫描会把边界说明
    本身误判为预写声明；本检查只捉真正的声明面（符号名 + 导出面键 + 运行时属性）。
    """

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.extend(alias.asname or alias.name.split(".")[-1] for alias in node.names)
    return names


def _export_keys(value) -> list[str]:
    """导出面全部键（递归）：任何层级出现 parity 键都算预写对侧声明。"""
    if isinstance(value, dict):
        return [str(key) for key in value] + [
            key for item in value.values() for key in _export_keys(item)
        ]
    if isinstance(value, (list, tuple)):
        return [key for item in value for key in _export_keys(item)]
    return []


def _parity_names(names: list[str]) -> list[str]:
    return [name for name in names if "parity" in name.lower()]


class Test导出结构:
    def test_导出_规范结构(self, topic_slate):
        export = export_slate(topic_slate)
        assert isinstance(export, dict)
        assert export["schema_version"] == topic_slate.schema_version
        assert export["production_marks"] == list(topic_slate.production_marks)
        assert export["signal_sources"] == [dict(source) for source in topic_slate.signal_sources]

    def test_方向序与条目要点保真(self, topic_slate):
        export = export_slate(topic_slate)
        assert [entry["direction_id"] for entry in export["entries"]] == list(
            topic_slate.direction_ids()
        )
        first = export["entries"][0]
        assert first["genre"] == topic_slate.entries[0].genre
        assert first["constraints"] == list(topic_slate.entries[0].constraints)
        assert first["characters"] == list(topic_slate.entries[0].characters)
        assert first["rationale"] == topic_slate.entries[0].rationale
        assert first["eval_components"] == topic_slate.entries[0].eval_components
        assert first["in_production"] is topic_slate.entries[0].in_production

    def test_组合级标注随导出(self, topic_slate):
        """SC-009：模拟数据源标注随导出面进下游（不得让模拟信号看起来像真实商业数据）。"""
        sources = export_slate(topic_slate)["signal_sources"]
        assert {source["source"] for source in sources} == {
            "simulated.box_office_regression",
            "simulated.buzz_heat",
        }
        assert all(source["simulated"] is True for source in sources)
        assert all("非真实商业数据" in source["note"] for source in sources)

    def test_确定性(self, topic_slate):
        first, second = export_slate(topic_slate), export_slate(topic_slate)
        assert first == second
        assert (
            TopicSlate.from_dict(first).canonical_json()
            == TopicSlate.from_dict(second).canonical_json()
        )

    def test_导出可反解往返(self, topic_slate):
        """下游反解面：导出即规范结构（字段足以复原工件，无需本侧 parity 声明）。"""
        restored = TopicSlate.from_dict(export_slate(topic_slate))
        assert restored == topic_slate
        assert restored.slate_hash() == topic_slate.slate_hash()

    def test_非工件输入拒绝(self):
        with pytest.raises(ValidationError, match="TopicSlate"):
            export_slate({"entries": []})


class Test不因缺陷丢条目:
    @pytest.mark.parametrize("variant", DEFECT_VARIANTS)
    def test_门禁违规组合仍导出全部条目与标记(self, make_topic_slate, variant):
        """门禁违规由 `rule.*` 判 0 并点名；导出只做 schema 映射，不代判、不丢条目。"""
        slate = make_topic_slate(variant)
        export = export_slate(slate)
        assert len(export["entries"]) == len(slate.entries)
        assert [entry["direction_id"] for entry in export["entries"]] == list(slate.direction_ids())
        assert export["production_marks"] == list(slate.production_marks)
        assert [entry["in_production"] for entry in export["entries"]] == [
            entry.in_production for entry in slate.entries
        ]

    def test_要点缺失的条目仍导出空取值(self, make_topic_slate):
        """ "字段在但取值为空"与"字段缺失"不同：导出保留空取值，由门禁点名（不臆造要点）。"""
        entry = export_slate(make_topic_slate("missing_essentials"))["entries"][2]
        assert entry["direction_id"] == "dir-awakening"
        assert entry["genre"] == ""
        assert entry["constraints"] == []
        assert entry["characters"] == []

    def test_悬空标记原样导出(self, make_topic_slate):
        """悬空标记是组合门禁的判定对象，导出如实保留（不代删、不代改）。"""
        export = export_slate(make_topic_slate("dangling_mark"))
        assert export["production_marks"] == ["dir-not-in-slate"]
        assert all(entry["in_production"] is False for entry in export["entries"])

    def test_导出丢条目即报错(self, topic_slate, monkeypatch):
        """导出面退化即报错（不静默漏选题给下游）。"""
        original = TopicSlate.to_dict
        monkeypatch.setattr(
            TopicSlate,
            "to_dict",
            lambda self: {**original(self), "entries": original(self)["entries"][:-1]},
        )
        with pytest.raises(ValidationError, match="丢条目"):
            export_slate(topic_slate)

    def test_导出丢标记即报错(self, topic_slate, monkeypatch):
        original = TopicSlate.to_dict
        monkeypatch.setattr(
            TopicSlate,
            "to_dict",
            lambda self: {**original(self), "production_marks": []},
        )
        with pytest.raises(ValidationError, match="丢标记"):
            export_slate(topic_slate)


class Test单侧schema快照:
    """导出面字段集锁定（单侧：下游字段名由 G2 反解时补齐，本特性不预写对侧）。"""

    def test_顶层字段名锁定(self, topic_slate):
        assert tuple(export_slate(topic_slate)) == EXPORT_FIELDS
        assert EXPORT_FIELDS == ("schema_version", "entries", "production_marks", "signal_sources")

    def test_条目字段名锁定(self, topic_slate):
        assert tuple(export_slate(topic_slate)["entries"][0]) == EXPORT_ENTRY_FIELDS
        assert EXPORT_ENTRY_FIELDS == (
            "direction_id",
            "rationale",
            "eval_components",
            "genre",
            "constraints",
            "characters",
            "in_production",
        )
        assert {field.name for field in fields(SlateEntry)} == set(EXPORT_ENTRY_FIELDS)

    def test_导出含_schema_版本(self, topic_slate):
        assert export_slate(topic_slate)["schema_version"] == SCHEMA_VERSION == "1.0.0"

    def test_顶层字段漂移即报错(self, topic_slate, monkeypatch):
        """顶层导出面是快照的一部分：增删顶层字段同样立即红（两个面都锁）。"""
        original = TopicSlate.to_dict
        monkeypatch.setattr(
            TopicSlate,
            "to_dict",
            lambda self: {
                key: value for key, value in original(self).items() if key != "signal_sources"
            },
        )
        with pytest.raises(ValidationError, match="顶层字段漂移"):
            export_slate(topic_slate)

    def test_上游加字段而导出面未更新即报错(self, topic_slate, monkeypatch):
        """schema 变更须同时升版本 + 更新快照：只加字段不改导出面 → 立即红（不静默漏字段）。"""
        original = SlateEntry.to_dict
        monkeypatch.setattr(
            SlateEntry,
            "to_dict",
            lambda self: {**original(self), "in_production_note": "（新增字段示例）"},
        )
        with pytest.raises(ValidationError, match="漂移"):
            export_slate(topic_slate)


class Test不预写字段级parity声明:
    """C6：字段级交接声明（`FieldParity`）属 G2（018）——本侧显式断言其**缺席**。

    三处机检：①导出模块的符号面与运行时属性无 parity 声明（文档字符串里的边界**说明**
    不算声明）②整个 `agents/dev` 包无 parity 符号与 `FieldParity` 导入 ③导出面递归键集
    无 parity 键。
    """

    def test_导出模块无_parity_API(self):
        assert [name for name in dir(export_slate_module) if "parity" in name.lower()] == []
        assert not hasattr(export_slate_module, "FieldParity")
        names = _symbol_names(ast.parse(MODULE_PATH.read_text(encoding="utf-8")))
        assert _parity_names(names) == []

    def test_dev_包无_parity_声明(self):
        """整个 017 侧（agents/dev）不得预写下游 parity（防"顺手补齐"）。"""
        offenders = [
            f"{_rel(path)} 出现 parity 符号 {name}"
            for path in _dev_sources()
            for name in _parity_names(_symbol_names(ast.parse(path.read_text(encoding="utf-8"))))
        ]
        assert offenders == [], "017 侧不得预写字段级交接声明（属 G2）：\n" + "\n".join(offenders)

    def test_导出面无_parity_键(self, topic_slate):
        keys = _export_keys(export_slate(topic_slate))
        assert set(keys) >= {"schema_version", "entries", "direction_id"}  # 键面有效（导出非空）
        assert [key for key in keys if "parity" in key.lower()] == []

    def test_不依赖_018_侧交接类(self):
        """不得**导入**018 侧交接件（`FieldParity` 的落点）：本侧只保证导出可反解，对侧由 G2 补齐。

        文档字符串引用先例路径是允许的（说明边界），故此处只看 import 面。
        """
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        assert imported, "未解析到任何 import（解析面失效？）"
        assert [name for name in imported if "pilot" in name or "handoffs" in name] == []
