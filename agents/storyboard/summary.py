"""ShotList 结构化文本摘要（功能 008 / C8）：judge 输入的形成环节。

ShotList × 剧本段落 → 确定性结构化文本（总览行 + 按场景分段 + 逐镜：序号/景别/
机位含侧别/运动/时长/备选数/承接行/情绪 + 关键行承接 + 剧本情绪）；锚点 ShotList
（无剧本）经同一摘要函数产出（judge 成对比较的对照面）。摘要函数的变更同提示词
变更一样改变打分行为，其实现文件 BLAKE3 前 8 位入 judge 版本号三段之一（原则一）。

确定性纪律：文本仅由 ShotList/剧本结构决定，无时间戳/随机流/字典序依赖——
同输入重算逐字节一致（回放与重算一致性的文本底座）。
"""

from pathlib import Path

import blake3

from agents.storyboard.board_render import shot_emotion
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotEntry, ShotList
from core.tree.errors import ValidationError

_NO_KEY_LINES = "关键行承接：无（必覆盖清单为空）"
_UNLABELED = "未标注"


def _overview(shotlist: ShotList, script: ScriptSegment | None) -> str:
    key_count = len(script.key_line_ids()) if script is not None else 0
    return (
        f"分镜摘要：镜头数 {len(shotlist.shots)} | 场景数 {len(shotlist.scene_ids())} | "
        f"预估时长 {shotlist.total_est_duration_ms()}ms | 关键行 {key_count} 条"
    )


def _scene_header(scene_id: str, script: ScriptSegment | None, shotlist: ShotList) -> str:
    """场景段头：有剧本时标注轴线基准与剧本行数（coverage 门禁对照面）。"""
    shot_count = len(shotlist.shots_of_scene(scene_id))
    if script is None:
        return f"[场景 {scene_id}] 镜头 {shot_count} 个"
    scene = next(scene for scene in script.scenes if scene.scene_id == scene_id)
    axis = scene.axis_base if scene.axis_base is not None else _UNLABELED
    return f"[场景 {scene_id}] 轴线 {axis} | 剧本行 {len(scene.lines)} 条 | 镜头 {shot_count} 个"


def _shot_line(index: int, shot: ShotEntry, script: ScriptSegment | None) -> str:
    segments = [
        f"[{index}] 镜头 {shot.shot_id}",
        f"场景 {shot.scene_id}",
        f"景别 {shot.shot_size}",
        f"机位 {shot.camera}(侧 {shot.side})",
        f"运动 {shot.movement}",
        f"时长 {shot.est_duration_ms}ms",
        f"备选 {shot.alternatives}",
        f"承接 {','.join(shot.covers)}",
    ]
    if script is not None:
        emotion = shot_emotion(shot, script)
        segments.append(f"情绪 {emotion if emotion is not None else _UNLABELED}")
    return " | ".join(segments)


def _key_line_lines(shotlist: ShotList, script: ScriptSegment) -> str:
    """关键行承接清单：逐条给出承接镜头（必覆盖清单为空时如实降级注明）。"""
    key_lines = script.key_line_ids()
    if not key_lines:
        return _NO_KEY_LINES
    pairs = []
    for line_id in key_lines:
        covering = [shot.shot_id for shot in shotlist.shots if line_id in shot.covers]
        pairs.append(f"{line_id}←{','.join(covering) if covering else '未承接'}")
    return "关键行承接：" + ", ".join(pairs)


def _script_emotion_line(script: ScriptSegment) -> str:
    pairs = []
    for scene in script.scenes:
        for line in scene.lines:
            emotion = line.emotion if line.emotion is not None else _UNLABELED
            pairs.append(f"{line.line_id}={emotion}")
    return "剧本情绪：" + ", ".join(pairs)


def summarize_shotlist(shotlist: ShotList, script: ScriptSegment | None = None) -> str:
    """ShotList（× 可选剧本段落）→ 结构化文本摘要（judge 输入，确定性）。

    剧本给定时附带情绪/关键行承接/剧本情绪三部分（judge 判断贴合度的对照面）；
    不给付（锚点 ShotList）只输出镜头结构，如实不标注来源。
    """
    if not isinstance(shotlist, ShotList):
        raise ValidationError(f"shotlist 必须为 ShotList，实际为 {shotlist!r}")
    if script is not None and not isinstance(script, ScriptSegment):
        raise ValidationError(f"script 必须为 ScriptSegment 或 None，实际为 {script!r}")
    lines = [_overview(shotlist, script)]
    current_scene: str | None = None
    for index, shot in enumerate(shotlist.shots, start=1):
        if shot.scene_id != current_scene:
            current_scene = shot.scene_id
            lines.append(_scene_header(current_scene, script, shotlist))
        lines.append(_shot_line(index, shot, script))
    if script is not None:
        lines.append(_key_line_lines(shotlist, script))
        lines.append(_script_emotion_line(script))
    return "\n".join(lines)


def summary_function_hash() -> str:
    """摘要函数哈希 = 本实现文件 BLAKE3 前 8 位（judge 版本号三段之一，决策 6）。

    实现文件任一变更（含本函数文本）即哈希变更 → judge 版本变更（原则一）。
    """
    return blake3.blake3(Path(__file__).read_bytes()).hexdigest()[:8]
