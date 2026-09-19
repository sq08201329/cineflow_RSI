"""YAML 段级定点改写（行替换保注释，零解析往返）。

功能 010 决策 6 与 WS3 遗留（dreaming approve 的 yaml.safe_dump 全量重写丢注释）
共用的可复用实现：只定点改写指定嵌套段内的指定标量键值行，注释、空行与其他段
逐字节保留。仅支持嵌套映射段内的标量值替换/追加（校准/部署指针场景的充分口径）。

- replace_section_entries：严格替换，段或键缺失即报错（校准权重生效口径）；
- upsert_section_entries：存在则替换、缺失则按缩进约定追加（部署指针口径，
  deployment 段/agent 子段/指针键可能尚未写入，需幂等补全）。
"""

import json
import re

import yaml

from core.calibration.errors import CalibrationError


class YamlEditError(CalibrationError):
    """定点改写失败：段路径不存在、键缺失或行形态不支持。"""


_PLAIN_STRING = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+/-]*$")


def _format_scalar(value: float | str) -> str:
    """标量格式化：数值 12 位有效数字（0.30000000000000004 → 0.3，浮点尾数不外泄）；
    字符串优先原样输出，可能被 YAML 解析成非字符串时退化为双引号（round-trip 保类型）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise YamlEditError(f"定点改写仅支持数值/字符串标量，实际为 {value!r}")
    if isinstance(value, (int, float)):
        return f"{value:.12g}"
    if _PLAIN_STRING.match(value) and yaml.safe_load(value) == value:
        return value
    return json.dumps(value, ensure_ascii=False)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """段块结束行：首条缩进 ≤ 段头的非空非注释行（注释行不终结段块）。"""
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped and not stripped.startswith("#") and _indent_of(lines[end]) <= indent:
            break
        end += 1
    return end


def _find_section_prefix(
    lines: list[str], path: tuple[str, ...], start: int, end: int
) -> tuple[list[tuple[int, int]], int | None]:
    """沿段路径逐级定位（容忍缺失）。

    返回 (已定位前缀 [(段头行号, 段头缩进)], 首个缺失深度或 None)。
    顶层段必须零缩进；嵌套段缩进必须大于父级。
    """
    pos, parent_indent = start, -1
    prefix: list[tuple[int, int]] = []
    for depth, segment in enumerate(path):
        found = None
        for i in range(pos, end):
            match = re.match(rf"^(\s*){re.escape(segment)}\s*:\s*(#.*)?$", lines[i].rstrip("\n"))
            if not match:
                continue
            indent = len(match.group(1))
            if depth == 0 and indent != 0:
                continue
            if depth > 0 and indent <= parent_indent:
                continue
            found = (i, indent)
            break
        if found is None:
            return prefix, depth
        prefix.append(found)
        pos, parent_indent = found[0] + 1, found[1]
    return prefix, None


def _find_section(lines: list[str], path: tuple[str, ...], start: int, end: int) -> tuple[int, int]:
    """沿段路径逐级定位；返回末级段头行号与段头缩进。缺失 → YamlEditError。"""
    prefix, missing_at = _find_section_prefix(lines, path, start, end)
    if missing_at is not None:
        raise YamlEditError(f"配置段不存在：{'/'.join(path[: missing_at + 1])}")
    return prefix[-1]


def _replace_in_block(
    lines: list[str], header_idx: int, header_indent: int, block_end: int, updates: dict
) -> dict:
    """段块内替换已存在键的标量值行（缩进与行尾注释保留）；返回未匹配的 updates。"""
    remaining = dict(updates)
    for i in range(header_idx + 1, block_end):
        body = lines[i].rstrip("\n")
        newline = "\n" if lines[i].endswith("\n") else ""
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        if _indent_of(body) <= header_indent:
            continue
        for key in list(remaining):
            match = re.match(rf"^(\s*{re.escape(key)}\s*:\s*)([^#\n]*?)(\s*#.*)?$", body)
            if match:
                formatted = _format_scalar(remaining.pop(key))
                trailing = match.group(3) or ""
                lines[i] = f"{match.group(1)}{formatted}{trailing}{newline}"
                break
    return remaining


def _insertion_point(lines: list[str], header_idx: int, header_indent: int) -> tuple[int, int]:
    """段内追加位点与条目缩进：末个同级条目（含子树）之后、尾随空行/注释之前。"""
    entries = [
        i
        for i in range(header_idx + 1, _block_end(lines, header_idx, header_indent))
        if lines[i].strip()
        and not lines[i].lstrip().startswith("#")
        and _indent_of(lines[i]) > header_indent
    ]
    if entries:
        entry_indent = min(_indent_of(lines[i]) for i in entries)
        last = max(i for i in entries if _indent_of(lines[i]) == entry_indent)
        insert_idx = _block_end(lines, last, entry_indent)
    else:
        entry_indent = header_indent + 2
        insert_idx = header_idx + 1
    while insert_idx > header_idx + 1 and (
        not lines[insert_idx - 1].strip() or lines[insert_idx - 1].lstrip().startswith("#")
    ):
        insert_idx -= 1
    return insert_idx, entry_indent


def replace_section_entries(
    text: str, section_path: tuple[str, ...], updates: dict[str, float | str]
) -> str:
    """定点替换 section_path 段内 updates 各键的标量值行，返回改写后全文。

    行形态 `indent key: old_value [# 注释]`：缩进与行尾注释保留，仅替换值。
    段或键缺失 → YamlEditError（需幂等补全的场景用 upsert_section_entries）。
    """
    lines = text.splitlines(keepends=True)
    header_idx, header_indent = _find_section(lines, section_path, 0, len(lines))
    block_end = _block_end(lines, header_idx, header_indent)
    remaining = _replace_in_block(lines, header_idx, header_indent, block_end, updates)
    if remaining:
        raise YamlEditError(
            f"配置段 {'/'.join(section_path)} 中缺少键：{sorted(remaining)}"
        )
    return "".join(lines)


def upsert_section_entries(
    text: str, section_path: tuple[str, ...], updates: dict[str, float | str]
) -> str:
    """定点 upsert：段与键存在则替换值行；缺失则按缩进约定追加，返回改写后全文。

    部署指针场景：deployment 段/agent 子段/指针键可能尚未写入，需幂等补全；
    缺键追加在段尾（末个同级条目之后），缺段在父段块尾（顶层缺失在文件尾）
    补全嵌套结构，注释、空行与其他段逐字节保留。
    """
    lines = text.splitlines(keepends=True)
    prefix, missing_at = _find_section_prefix(lines, section_path, 0, len(lines))
    if missing_at is None:
        header_idx, header_indent = prefix[-1]
        block_end = _block_end(lines, header_idx, header_indent)
        remaining = _replace_in_block(lines, header_idx, header_indent, block_end, updates)
        if remaining:
            insert_idx, entry_indent = _insertion_point(lines, header_idx, header_indent)
            lines[insert_idx:insert_idx] = [
                f"{' ' * entry_indent}{key}: {_format_scalar(value)}\n"
                for key, value in remaining.items()
            ]
        return "".join(lines)

    # 段缺失：补全缺失层级与全部键（父段块尾 / 文件尾）
    base_indent = prefix[-1][1] + 2 if prefix else 0
    new_lines = [
        f"{' ' * (base_indent + 2 * (depth - missing_at))}{section_path[depth]}:\n"
        for depth in range(missing_at, len(section_path))
    ]
    key_indent = base_indent + 2 * (len(section_path) - missing_at)
    new_lines.extend(
        f"{' ' * key_indent}{key}: {_format_scalar(value)}\n"
        for key, value in updates.items()
    )
    if prefix:
        insert_idx, _ = _insertion_point(lines, prefix[-1][0], prefix[-1][1])
    else:
        insert_idx = len(lines)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            new_lines.insert(0, "\n")  # 新顶层段前空行分隔
    lines[insert_idx:insert_idx] = new_lines
    return "".join(lines)
