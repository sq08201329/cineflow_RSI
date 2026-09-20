"""EDL 结构化文本摘要（功能 007，澄清 Q1 / research 决策 5）：judge 输入的形成环节。

EDL → 确定性结构化文本（逐镜：序号/时长/转场/分区标注/音轨标记）；锚点 EDL 集
经同一摘要函数产出（judge 成对比较的对照面）。摘要函数的变更同提示词变更一样
改变打分行为，其实现文件 BLAKE3 前 8 位入 judge 版本号三段之一（原则一）。

确定性纪律：文本仅由 EDL/镜头库/分区结构决定，无时间戳/随机流/字典序依赖——
同 EDL 重算逐字节一致（回放与重算一致性的文本底座）。
"""

from pathlib import Path

import blake3

from agents.editing.edl import EditDecisionList
from agents.editing.shots import SceneStructure, ShotLibrary
from core.tree.errors import ValidationError


def summarize_edl(
    edl: EditDecisionList,
    shot_library: ShotLibrary | None = None,
    scenes: SceneStructure | None = None,
) -> str:
    """EDL → 结构化文本摘要（逐镜一行 + 音轨一行）。

    分区标注：有 SceneStructure 时标注分区名与序号（shot-orphan 等库外镜头
    标注"库外"——锚点 EDL 不经镜头库校验，如实标注）；无结构时不标注。
    """
    if not isinstance(edl, EditDecisionList):
        raise ValidationError(f"edl 必须为 EditDecisionList，实际为 {edl!r}")
    lines = [
        f"剪辑摘要：镜头数 {len(edl.clips)} | 总时长 {edl.total_duration_ms()}ms | "
        f"音轨 {len(edl.audio)} 条"
    ]
    for index, clip in enumerate(edl.clips, start=1):
        duration_ms = clip.out_ms - clip.in_ms
        transition = f"{clip.transition.type}({clip.transition.duration_ms}ms)"
        lines.append(
            f"[{index}] 镜头 {clip.shot_id} | 区间 [{clip.in_ms}, {clip.out_ms})ms | "
            f"时长 {duration_ms}ms | 分区 {_scene_label(clip.shot_id, shot_library, scenes)} | "
            f"转场 {transition}"
        )
    if edl.audio:
        for cue in edl.audio:
            lines.append(f"音轨: {cue.track_ref} @ {cue.at_ms}ms × 增益 {cue.gain}")
    else:
        lines.append("音轨: 无音轨")
    return "\n".join(lines)


def _scene_label(
    shot_id: str, shot_library: ShotLibrary | None, scenes: SceneStructure | None
) -> str:
    """分区标注：归属分区名（序号）；未分区/库外镜头如实标注，不臆造归属。"""
    if scenes is not None:
        try:
            index = scenes.scene_index_of(shot_id)
        except ValidationError:
            return "库外"
        return f"{scenes.scenes[index].scene_id}(#{index})"
    if shot_library is not None and shot_library.has_shot(shot_id):
        return shot_library.get(shot_id).scene_id
    return "库外"


def summary_function_hash() -> str:
    """摘要函数哈希 = 本实现文件 BLAKE3 前 8 位（judge 版本号三段之一，决策 5）。

    实现文件任一变更（含本函数文本）即哈希变更 → judge 版本变更（原则一）。
    """
    return blake3.blake3(Path(__file__).read_bytes()).hexdigest()[:8]
