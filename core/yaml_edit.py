"""YAML 段级定点改写（行替换保注释，零解析往返）。

功能 010 决策 6 与 WS3 遗留（dreaming approve 的 yaml.safe_dump 全量重写丢注释）
共用的可复用实现：只替换指定嵌套段内的指定标量键值行，注释、空行与其他段
逐字节保留。仅支持嵌套映射段内的标量值替换（校准/部署指针场景的充分口径）。
"""

import re

from core.calibration.errors import CalibrationError


class YamlEditError(CalibrationError):
    """定点改写失败：段路径不存在、键缺失或行形态不支持。"""


def _format_scalar(value: float) -> str:
    """标量格式化：12 位有效数字（0.30000000000000004 → 0.3，浮点尾数不外泄）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise YamlEditError(f"定点改写仅支持数值标量，实际为 {value!r}")
    return f"{value:.12g}"


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


def _find_section(lines: list[str], path: tuple[str, ...], start: int, end: int) -> tuple[int, int]:
    """沿段路径逐级定位；返回末级段头行号与段头缩进。缺失 → YamlEditError。"""
    pos, parent_indent = start, -1
    for depth, segment in enumerate(path):
        found = None
        for i in range(pos, end):
            match = re.match(rf"^(\s*){re.escape(segment)}\s*:\s*(#.*)?$", lines[i].rstrip("\n"))
            if not match:
                continue
            indent = len(match.group(1))
            # 顶层段必须零缩进；嵌套段缩进必须大于父级
            if depth == 0 and indent != 0:
                continue
            if depth > 0 and indent <= parent_indent:
                continue
            found = (i, indent)
            break
        if found is None:
            raise YamlEditError(f"配置段不存在：{'/'.join(path[: depth + 1])}")
        pos, parent_indent = found[0] + 1, found[1]
    return found


def replace_section_entries(
    text: str, section_path: tuple[str, ...], updates: dict[str, float]
) -> str:
    """定点替换 section_path 段内 updates 各键的标量值行，返回改写后全文。

    行形态 `indent key: old_value [# 注释]`：缩进与行尾注释保留，仅替换值。
    """
    lines = text.splitlines(keepends=True)
    header_idx, header_indent = _find_section(lines, section_path, 0, len(lines))
    block_end = _block_end(lines, header_idx, header_indent)

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
    if remaining:
        raise YamlEditError(
            f"配置段 {'/'.join(section_path)} 中缺少键：{sorted(remaining)}"
        )
    return "".join(lines)
