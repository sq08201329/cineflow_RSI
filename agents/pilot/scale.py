"""体量档的纯计算叶子模块（功能 018 / 契约 C8/C10）。

`derived_shot_count = max(场景数, ceil(成片时长 / 单镜时长))` 的**唯一无环持有者**：
`agents/pilot/stages.py` 的镜头计划、`agents/storyboard/config.py` 的索引容量下界校验与档位
一致性机检**都只读本函数**——公式在两处各写一遍会让渲染器与门禁各按一份数字判定。

**叶子模块**（依赖方向单向，宪章原则五）：只做纯计算，不 import `agents/storyboard/*`、
不读形态配置、不触任何环境对象；取值由调用方（`PilotConfig` 的唯一解析结果）注入。
"""

import math

__all__ = ["derived_shot_count"]


def derived_shot_count(
    *, scene_count: int, target_duration_s: float, clip_duration_seconds: float
) -> int:
    """派生镜头数：场景数是下界（每场景至少一镜），否则按单镜时长切分成片目标时长。

    参数为**已解析的生效体量**（唯一解析者是 `agents/pilot/pilot.py` 的 `PilotConfig`）。
    缺项/取值非法即报错（不静默取默认：公式持有者不替调用方兜底）。
    """
    if isinstance(scene_count, bool) or not isinstance(scene_count, int) or scene_count < 1:
        raise ValueError(f"场景数必须为 ≥1 的整数，实际为 {scene_count!r}")
    for name, value in (
        ("成片时长", target_duration_s),
        ("单镜时长", clip_duration_seconds),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
            raise ValueError(f"{name}必须为正数，实际为 {value!r}")
    return max(scene_count, math.ceil(float(target_duration_s) / float(clip_duration_seconds)))
