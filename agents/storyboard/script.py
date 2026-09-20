"""剧本段落模型 ScriptSegment（功能 008，research 决策 2）：分镜闭环的输入单元。

剧本段落 = 场景列表（有序）+ 每场景的台词/动作行 + 关键行标注（必覆盖清单，
澄清 Q1）+ 情绪基调标注 + 轴向基准（side A|B，场景切换重置 180° 线）。
构造即做形状校验：行 id 全局唯一、场景有序（scene_id 不重复）、关键行标注为
布尔、情绪为字符串或缺失、轴向基准 ∈ {A, B} 或缺失；配置相关口径（情绪取值
必须在情绪向量表内、剧本不足预检）走 validate_script——执行前拒绝注明"剧本不足"
（0 渲染 0 成本，C1/C2）。
"""

from dataclasses import dataclass, field

from core.tree.errors import ValidationError

# 行类型：台词/动作（剧本 Agent 接入时对齐本 schema，F4 降级模式）
LINE_KINDS = ("dialogue", "action")
# 轴向基准侧别（180° 线两侧；与 ShotEntry.side 同一取值域）
SIDES = ("A", "B")


def _require_nonempty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串，实际为 {value!r}")
    return value


@dataclass(frozen=True)
class ScriptLine:
    """剧本行：line_id 全局唯一；key=True 即必覆盖清单成员（须逐条被镜头承接）。"""

    line_id: str
    kind: str
    text: str
    key: bool = False
    emotion: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.line_id, "line_id")
        if self.kind not in LINE_KINDS:
            raise ValidationError(f"line.kind 必须 ∈ {list(LINE_KINDS)}，实际为 {self.kind!r}")
        _require_nonempty_str(self.text, "text")
        if not isinstance(self.key, bool):
            raise ValidationError(f"line.key 必须为布尔，实际为 {self.key!r}")
        if self.emotion is not None:
            _require_nonempty_str(self.emotion, "emotion")

    def to_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "kind": self.kind,
            "text": self.text,
            "key": self.key,
            "emotion": self.emotion,
        }


@dataclass(frozen=True)
class ScriptScene:
    """剧本场景：有序单元（顺序即剧本顺序，coverage 门禁逐场景对照）。"""

    scene_id: str
    lines: tuple[ScriptLine | dict, ...] | list[ScriptLine | dict]
    axis_base: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.scene_id, "scene_id")
        if self.axis_base is not None and self.axis_base not in SIDES:
            raise ValidationError(
                f"scene.axis_base 必须 ∈ {list(SIDES)} 或缺失，实际为 {self.axis_base!r}"
            )
        if self.lines is None:
            raise ValidationError(f"scene {self.scene_id!r} 缺少 lines 字段")
        lines = tuple(_normalize_line(line) for line in self.lines)
        object.__setattr__(self, "lines", lines)

    def line_ids(self) -> tuple[str, ...]:
        return tuple(line.line_id for line in self.lines)

    def key_line_ids(self) -> tuple[str, ...]:
        return tuple(line.line_id for line in self.lines if line.key)

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "axis_base": self.axis_base,
            "lines": [line.to_dict() for line in self.lines],
        }


def _normalize_line(item: ScriptLine | dict) -> ScriptLine:
    if isinstance(item, ScriptLine):
        return item
    if isinstance(item, dict):
        return ScriptLine(
            line_id=item.get("line_id"),
            kind=item.get("kind"),
            text=item.get("text"),
            key=item.get("key", False),
            emotion=item.get("emotion"),
        )
    raise ValidationError(f"line 必须为 dict 或 ScriptLine，实际为 {item!r}")


def _normalize_scene(item: ScriptScene | dict) -> ScriptScene:
    if isinstance(item, ScriptScene):
        return item
    if isinstance(item, dict):
        return ScriptScene(
            scene_id=item.get("scene_id"),
            lines=item.get("lines"),
            axis_base=item.get("axis_base"),
        )
    raise ValidationError(f"scene 必须为 dict 或 ScriptScene，实际为 {item!r}")


@dataclass(frozen=True)
class ScriptSegment:
    """剧本段落：场景列表（有序，scene_id 唯一）+ 全局唯一行 id。

    允许构造空剧本（scenes=() 或空场景）：这是"剧本不足"的合法中间态，
    由 validate_script 在执行前拒绝并注明（不静默产出空分镜）。
    """

    scenes: tuple[ScriptScene | dict, ...] | list[ScriptScene | dict] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        scenes = tuple(_normalize_scene(scene) for scene in self.scenes)
        scene_ids = [scene.scene_id for scene in scenes]
        duplicates = sorted({sid for sid in scene_ids if scene_ids.count(sid) > 1})
        if duplicates:
            raise ValidationError(f"scene_id 重复（场景须有序且唯一）：{duplicates}")
        line_ids = [line_id for scene in scenes for line_id in scene.line_ids()]
        repeated = sorted({lid for lid in line_ids if line_ids.count(lid) > 1})
        if repeated:
            raise ValidationError(f"line_id 必须全局唯一，重复：{repeated}")
        object.__setattr__(self, "scenes", scenes)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptSegment":
        if not isinstance(data, dict):
            raise ValidationError(f"剧本段落必须为 dict，实际为 {data!r}")
        return cls(scenes=data.get("scenes", ()))

    def to_dict(self) -> dict:
        return {"scenes": [scene.to_dict() for scene in self.scenes]}

    def scene_ids(self) -> tuple[str, ...]:
        return tuple(scene.scene_id for scene in self.scenes)

    def line_ids(self) -> tuple[str, ...]:
        return tuple(line_id for scene in self.scenes for line_id in scene.line_ids())

    def key_line_ids(self) -> tuple[str, ...]:
        """必覆盖清单（澄清 Q1）：key=True 行须逐条被镜头 covers 承接。"""
        return tuple(line_id for scene in self.scenes for line_id in scene.key_line_ids())

    def line(self, line_id: str) -> ScriptLine:
        for scene in self.scenes:
            for line in scene.lines:
                if line.line_id == line_id:
                    return line
        raise ValidationError(f"剧本中不存在行 {line_id!r}")

    def scene_of(self, line_id: str) -> str:
        for scene in self.scenes:
            if line_id in scene.line_ids():
                return scene.scene_id
        raise ValidationError(f"剧本中不存在行 {line_id!r}")

    def emotion_of(self, line_id: str) -> str | None:
        return self.line(line_id).emotion

    def is_empty(self) -> bool:
        return not self.line_ids()


def validate_script(script: ScriptSegment, *, emotion_vectors: dict | None = None) -> None:
    """执行前剧本预检（C2）：剧本不足拒绝（注明）；情绪取值须在向量表内。

    emotion_vectors 为 storyboard.emotion_vectors 配置段；不传则只做形状校验
    （形状校验已在构造完成，此处仅兜底非 ScriptSegment 输入）。
    """
    if not isinstance(script, ScriptSegment):
        raise ValidationError(f"script 必须为 ScriptSegment，实际为 {script!r}")
    if not script.scenes:
        raise ValidationError("剧本不足：场景列表为空（执行前拒绝，0 渲染 0 成本）")
    empty_scenes = [scene.scene_id for scene in script.scenes if not scene.lines]
    if empty_scenes:
        raise ValidationError(f"剧本不足：场景 {empty_scenes} 无任何行（执行前拒绝）")
    if emotion_vectors is None:
        return
    if not isinstance(emotion_vectors, dict) or not emotion_vectors:
        raise ValidationError("emotion_vectors 必须为非空字典（情绪向量表配置缺失）")
    for scene in script.scenes:
        for line in scene.lines:
            if line.emotion is not None and line.emotion not in emotion_vectors:
                raise ValidationError(
                    f"行 {line.line_id!r} 情绪基调 {line.emotion!r} 不在情绪向量表 "
                    f"{sorted(emotion_vectors)} 内"
                )
