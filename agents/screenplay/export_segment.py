"""剧本工件 → 008 分镜输入 schema 的导出适配（功能 009 / C17）。

`export_segment(artifact) -> ScriptSegment`：把剧本工件的场景头/行标记映射为 008 分镜
闭环的输入单元（场景有序 + 行 id 全局唯一 + 关键行标注 + 情绪 + 轴向基准），使分镜线可
由真实剧本产出驱动（008 侧夹具的替换点）。

本模块是 `agents/screenplay` **唯一的跨包依赖**，且只依赖 008 的 schema 契约模块
（`agents.storyboard.script`）——它是 009 与 008 之间"剧本表示"的单一对接面：
字段名与枚举值由 `tests/unit/test_screenplay_export.py` 的双向快照断言锁定，任一侧
漂移即测试失败（规格边界情况：schema 变更须同步更新对接文档与快照断言）。

导出纪律：只做 schema 映射，**不因缺陷丢数据**——节拍缺失/幽灵角色/比例失衡等是评估器
的判定对象，导出照常保留全部场景与行（行数、关键行清单、情绪逐条保真）；空场景如实
保留，由 008 侧 `validate_script` 以"剧本不足"在执行前拒绝（不静默补内容）。
"""

from agents.screenplay.artifact import ScriptArtifact
from agents.storyboard.script import ScriptLine, ScriptScene, ScriptSegment
from core.tree.errors import ValidationError


def export_segment(artifact: ScriptArtifact) -> ScriptSegment:
    """剧本工件 → 008 ScriptSegment（确定性；场景/行序与关键行/情绪逐条保真）。"""
    if not isinstance(artifact, ScriptArtifact):
        raise ValidationError(f"export_segment 输入必须为 ScriptArtifact，实际为 {artifact!r}")
    scenes = []
    exported_lines = 0
    for scene in artifact.scenes:
        scene_lines = artifact.lines_of_scene(scene.scene_id)
        exported_lines += len(scene_lines)
        scenes.append(
            ScriptScene(
                scene_id=scene.scene_id,
                axis_base=scene.axis_base,
                lines=[
                    ScriptLine(
                        line_id=line.line_id,
                        kind=line.kind,
                        text=line.text,
                        key=line.key,
                        emotion=line.emotion,
                    )
                    for line in scene_lines
                ],
            )
        )
    # 导出不丢行（缺陷工件同样保真；丢行会让分镜侧静默缺内容）
    if exported_lines != artifact.total_lines():
        raise ValidationError(
            f"导出丢行：工件 {artifact.total_lines()} 条 → 导出 {exported_lines} 条"
            "（行须全部归属既有场景）"
        )
    return ScriptSegment(scenes=scenes)
