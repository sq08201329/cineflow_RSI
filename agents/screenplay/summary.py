"""大纲结构化文本摘要（功能 009 / C10）：judge 输入的形成环节。

ScriptArtifact（**仅大纲阶段**）→ 确定性结构化文本：总览行 + 节拍清单（逐条含 act 与
必需/可选）+ 按场景分段（场景头/时序/出场角色/行数）+ 逐行（类型/归属角色/文本/情绪/
关键行标记）+ 角色表（含别名）。冻结锚点大纲集经**同一摘要函数**产出（judge 成对比较
的对照面口径一致）；scenes/script 阶段不接受摘要——judge 仅作用于大纲阶段，非大纲阶段
由合成层按"不适用"跳过（不伪造 0 分），故此处在入口即拒绝而非静默打分。

确定性纪律：文本仅由工件结构决定，无时间戳/随机流/字典序依赖——同输入重算逐字节一致
（回放与重算一致性的文本底座）。摘要函数变更与提示词变更同样改变打分行为，其实现文件
BLAKE3 前 8 位入 judge 版本号三段之一（原则一）。
"""

from pathlib import Path

import blake3

from agents.screenplay.artifact import ScriptArtifact
from core.tree.errors import ValidationError

# 行类型中文标签（摘要人可读口径）与缺失标注
_KIND_LABELS = {"dialogue": "对白", "action": "动作"}
_UNLABELED = "未标注"
_NO_ALIAS = "无别名"


def _overview(artifact: ScriptArtifact) -> str:
    return (
        f"大纲摘要：节拍 {len(artifact.beats)} 个（关键 {len(artifact.required_beat_ids())}）"
        f" | 场景 {len(artifact.scenes)} 个 | 行 {artifact.total_lines()} 条"
        f"（对白 {artifact.count_kind('dialogue')} / 动作 {artifact.count_kind('action')}）"
        f" | 角色 {len(artifact.characters)} 个"
    )


def _beat_lines(artifact: ScriptArtifact) -> list[str]:
    lines = []
    for beat in artifact.beats:
        flag = "必需" if beat.required else "可选"
        lines.append(f"[节拍] {beat.beat_id}（{beat.act}，{flag}）{beat.description}")
    return lines


def _scene_header(artifact: ScriptArtifact, scene_id: str) -> str:
    scene = artifact.scene(scene_id)
    cast = ",".join(scene.characters) if scene.characters else _UNLABELED
    return (
        f"[场景 {scene.scene_id}] {scene.heading} | 时序 {scene.time_marker} 分钟"
        f" | 出场 {cast} | 行 {len(artifact.lines_of_scene(scene.scene_id))} 条"
    )


def _line_text(line) -> str:
    speaker = line.character if line.character is not None else _UNLABELED
    emotion = line.emotion if line.emotion is not None else _UNLABELED
    segments = [
        f"[行 {line.line_id}] {_KIND_LABELS[line.kind]} {speaker}：{line.text}",
        f"情绪 {emotion}",
    ]
    if line.key:
        segments.append("关键")
    return " | ".join(segments)


def _character_line(artifact: ScriptArtifact) -> str:
    entries = []
    for character in artifact.characters:
        if character.aliases:
            entries.append(f"{character.name}（别名 {'/'.join(character.aliases)}）")
        else:
            entries.append(f"{character.name}（{_NO_ALIAS}）")
    return "[角色] " + " | ".join(entries)


def _text_lines(artifact: ScriptArtifact) -> list[str]:
    """大纲正文逐行前置标记（判"戏剧张力"需要正文，不只看结构标记）。"""
    return [f"[正文] {row}" for row in artifact.text.splitlines()]


def summarize_outline(artifact: ScriptArtifact) -> str:
    """大纲工件 → 结构化摘要文本（judge 输入，确定性；仅 outline 阶段可摘要）。"""
    if not isinstance(artifact, ScriptArtifact):
        raise ValidationError(f"摘要输入必须为 ScriptArtifact，实际为 {artifact!r}")
    if artifact.stage != "outline":
        raise ValidationError(
            f"大纲摘要仅接受 outline 阶段工件（judge 仅作用于大纲阶段），实际为 {artifact.stage!r}"
        )
    lines = [_overview(artifact), *_beat_lines(artifact)]
    for scene_id in artifact.scene_ids():
        lines.append(_scene_header(artifact, scene_id))
        lines.extend(_line_text(line) for line in artifact.lines_of_scene(scene_id))
    lines.append(_character_line(artifact))
    lines.extend(_text_lines(artifact))
    return "\n".join(lines)


def summary_function_hash() -> str:
    """摘要函数哈希 = 本实现文件 BLAKE3 前 8 位（judge 版本号三段之一，决策 6 / 原则一）。

    实现文件任一变更（含本函数文本）即哈希变更 → judge 版本变更。
    """
    return blake3.blake3(Path(__file__).read_bytes()).hexdigest()[:8]
