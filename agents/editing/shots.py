"""镜头库与场景分区模型（功能 007）：EDL 执行前四层校验第①③层的结构底座。

- ShotEntry：镜头条目（shot_id/artifact_hash/duration_ms/scene_id/metadata），
  构造即校验；
- ShotLibrary：镜头库（shot_id 唯一检索 + 音轨引用集合）；
- SceneStructure（场景分区，澄清 Q2 决议）：scenes 有序列表，
  校验 shot_ids 无重复、引用存在、归属与 ShotEntry.scene_id 一致；
  分区序号 = scenes 列表序，供 EDL 场景顺序不降校验使用。

ValidationError 风格对齐 core（core.tree.errors，同 006 timing.py 用法）。
"""

from dataclasses import dataclass, field

from core.tree.errors import ValidationError


def _require_nonempty_str(value, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} 必须为非空字符串，实际为 {value!r}")
    return value


@dataclass(frozen=True)
class ShotEntry:
    """镜头条目：scene_id 为分区归属声明（与 SceneStructure 分配必须一致）。"""

    shot_id: str
    artifact_hash: str  # 64 位小写 hex（004 视觉工件内容寻址）
    duration_ms: int
    scene_id: str
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty_str(self.shot_id, "shot_id")
        if (
            not isinstance(self.artifact_hash, str)
            or len(self.artifact_hash) != 64
            or self.artifact_hash != self.artifact_hash.lower()
            or any(c not in "0123456789abcdef" for c in self.artifact_hash)
        ):
            raise ValidationError(
                f"artifact_hash 必须为 64 位小写 hex，实际为 {self.artifact_hash!r}"
            )
        if (
            not isinstance(self.duration_ms, int)
            or isinstance(self.duration_ms, bool)
            or self.duration_ms <= 0
        ):
            raise ValidationError(f"duration_ms 必须为正整数毫秒，实际为 {self.duration_ms!r}")
        _require_nonempty_str(self.scene_id, "scene_id")
        if not isinstance(self.metadata, dict):
            raise ValidationError(f"metadata 必须为 dict，实际为 {self.metadata!r}")


@dataclass(frozen=True)
class ShotLibrary:
    """镜头库：shot_id 唯一 + 音轨引用集合（EDL 第①层引用存在校验的查询面）。"""

    shots: tuple[ShotEntry, ...] | list[ShotEntry]
    audio_tracks: tuple[str, ...] | list[str] = ()

    def __post_init__(self) -> None:
        shots = tuple(self.shots)
        if not shots:
            raise ValidationError("镜头库不能为空（素材不足在执行前拒绝，不允许空库通过构造）")
        ids = [s.shot_id for s in shots]
        if len(set(ids)) != len(ids):
            raise ValidationError("镜头库 shot_id 重复")
        tracks = tuple(self.audio_tracks)
        for track in tracks:
            _require_nonempty_str(track, "audio_tracks 元素")
        if len(set(tracks)) != len(tracks):
            raise ValidationError("音轨引用重复")
        object.__setattr__(self, "shots", shots)
        object.__setattr__(self, "audio_tracks", tracks)
        object.__setattr__(self, "_by_id", {s.shot_id: s for s in shots})

    def has_shot(self, shot_id: str) -> bool:
        return shot_id in self._by_id

    def get(self, shot_id: str) -> ShotEntry:
        try:
            return self._by_id[shot_id]
        except KeyError:
            raise ValidationError(f"镜头库不存在镜头 {shot_id!r}") from None

    def has_audio(self, track_ref: str) -> bool:
        return track_ref in self.audio_tracks


@dataclass(frozen=True)
class Scene:
    """场景分区：有序 shot_ids 归属清单。"""

    scene_id: str
    shot_ids: tuple[str, ...] | list[str]

    def __post_init__(self) -> None:
        _require_nonempty_str(self.scene_id, "scene.scene_id")
        shot_ids = tuple(self.shot_ids)
        for shot_id in shot_ids:
            _require_nonempty_str(shot_id, "scene.shot_ids 元素")
        object.__setattr__(self, "shot_ids", shot_ids)


def _normalize_scene(item: Scene | dict) -> Scene:
    if isinstance(item, Scene):
        return item
    if isinstance(item, dict):
        return Scene(scene_id=item.get("scene_id"), shot_ids=item.get("shot_ids", ()))
    raise ValidationError(f"scene 必须为 dict 或 Scene，实际为 {item!r}")


@dataclass(frozen=True)
class SceneStructure:
    """场景分区结构：scenes 有序列表（分区顺序 = 列表顺序）。

    校验：scene_id 唯一；shot_ids 跨分区无重复；引用存在（在镜头库）；
    归属一致（镜头 ShotEntry.scene_id == 所属分区 scene_id）。
    """

    scenes: tuple[Scene | dict, ...] | list[Scene | dict]
    shot_library: ShotLibrary

    def __post_init__(self) -> None:
        if not isinstance(self.shot_library, ShotLibrary):
            raise ValidationError(f"shot_library 必须为 ShotLibrary，实际为 {self.shot_library!r}")
        scenes = tuple(_normalize_scene(s) for s in self.scenes)
        scene_ids = [s.scene_id for s in scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValidationError("SceneStructure scene_id 重复")
        seen: dict[str, str] = {}  # shot_id → scene_id（跨分区重复检测）
        for scene in scenes:
            for shot_id in scene.shot_ids:
                if shot_id in seen:
                    raise ValidationError(
                        f"镜头 {shot_id!r} 在分区 {seen[shot_id]!r} 与 {scene.scene_id!r} 重复出现"
                    )
                shot = self.shot_library.get(shot_id)  # 引用存在
                if shot.scene_id != scene.scene_id:
                    raise ValidationError(
                        f"镜头 {shot_id!r} 归属不一致：ShotEntry.scene_id={shot.scene_id!r} "
                        f"但被分进分区 {scene.scene_id!r}"
                    )
                seen[shot_id] = scene.scene_id
        object.__setattr__(self, "scenes", scenes)

    def scene_index_of(self, shot_id: str) -> int:
        """镜头所属分区序号（EDL 场景顺序不降校验用）；未分区镜头拒绝。"""
        for index, scene in enumerate(self.scenes):
            if shot_id in scene.shot_ids:
                return index
        raise ValidationError(f"镜头 {shot_id!r} 不归属 SceneStructure 任何分区（跨分区违规）")
