"""分镜 champion 策略（手工首版，008 US3）：剧本 + 规则约束 → ShotList 网格贪心探测。

代码即策略：版本 = 本文件内容 BLAKE3 前 12 位（文件名即版本号）。
每轮：observed() → 选已揭示中得分最高者（平分按 node_id 字典序，保证确定性）
→ 以 ShotList 网格顺序 probe（3 组，对齐 storyboard.boards_per_round 配比）；
预算耗尽或网格用尽即停止。

网格即镜头语言决策（手工首版工艺）：三场景各 3 镜、场景内同侧（轴线基准
scene-1/scene-2 = A、scene-3 = B）、景别相邻跳跃 ≤ max_size_jump=2 且无同景别连续
（rule.shot_grammar 门禁）、key 行（s1-l2 / s2-l1 / s3-l3）逐条承接（rule.coverage
必覆盖清单）、机位/运动档位在规则库枚举内、时长取 125ms 网格倍数（8fps 帧网格）。
三组网格给出三种景别节奏（由近及远 / 特写开场 / 全景开场），供进化在不同镜头语言
之间做选择；备选数为下游视觉线的并行方案参数。
"""

# 场景轴线基准（180° 线）：场景内同侧，场景切换重置
_AXIS_SIDE = {"scene-1": "A", "scene-2": "A", "scene-3": "B"}

# 每组网格：(scene_id, shot_id, covers, shot_size, camera, movement, est_duration_ms, alternatives)
_GRID_SPECS = [
    [
        # 组 1：由近及远再收束（特写开 → 全景展 → 中近收）
        ("scene-1", "shot-01", ["s1-l1"], "close_up", "eye_level", "static", 1000, 2),
        ("scene-1", "shot-02", ["s1-l2"], "medium", "over_shoulder", "dolly", 1250, 3),
        ("scene-1", "shot-03", ["s1-l3"], "full", "side", "pan", 1000, 1),
        ("scene-2", "shot-04", ["s2-l1"], "close_up", "low_angle", "static", 1000, 2),
        ("scene-2", "shot-05", ["s2-l2"], "medium", "high_angle", "tilt", 1250, 2),
        ("scene-2", "shot-06", ["s2-l3"], "wide", "eye_level", "handheld", 1500, 1),
        ("scene-3", "shot-07", ["s3-l1"], "medium", "high_angle", "static", 1000, 2),
        ("scene-3", "shot-08", ["s3-l2"], "close_up", "eye_level", "dolly", 1500, 3),
        ("scene-3", "shot-09", ["s3-l3"], "full", "low_angle", "static", 750, 1),
    ],
    [
        # 组 2：特写开场 → 全景收束（情绪压迫感优先）
        ("scene-1", "shot-01", ["s1-l1"], "extreme_close_up", "eye_level", "static", 1250, 1),
        ("scene-1", "shot-02", ["s1-l2"], "close_up", "over_shoulder", "dolly", 1000, 2),
        ("scene-1", "shot-03", ["s1-l3"], "medium", "side", "static", 1000, 2),
        ("scene-2", "shot-04", ["s2-l1"], "medium", "low_angle", "static", 1500, 3),
        ("scene-2", "shot-05", ["s2-l2"], "full", "high_angle", "tilt", 1000, 2),
        ("scene-2", "shot-06", ["s2-l3"], "medium", "eye_level", "handheld", 1250, 1),
        ("scene-3", "shot-07", ["s3-l1"], "wide", "high_angle", "static", 1250, 2),
        ("scene-3", "shot-08", ["s3-l2"], "medium", "eye_level", "dolly", 1000, 2),
        ("scene-3", "shot-09", ["s3-l3"], "close_up", "low_angle", "static", 1000, 1),
    ],
    [
        # 组 3：全景开场 → 特写收束（环境交代 → 情绪落点）
        ("scene-1", "shot-01", ["s1-l1"], "wide", "high_angle", "static", 1500, 2),
        ("scene-1", "shot-02", ["s1-l2"], "medium", "eye_level", "dolly", 1000, 2),
        ("scene-1", "shot-03", ["s1-l3"], "close_up", "low_angle", "static", 750, 1),
        ("scene-2", "shot-04", ["s2-l1"], "close_up", "eye_level", "static", 1000, 2),
        ("scene-2", "shot-05", ["s2-l2"], "medium", "high_angle", "tilt", 1250, 2),
        ("scene-2", "shot-06", ["s2-l3"], "full", "side", "handheld", 1000, 1),
        ("scene-3", "shot-07", ["s3-l1"], "full", "high_angle", "static", 1000, 2),
        ("scene-3", "shot-08", ["s3-l2"], "medium", "eye_level", "dolly", 1250, 2),
        ("scene-3", "shot-09", ["s3-l3"], "extreme_close_up", "low_angle", "static", 1500, 3),
    ],
]


def _build(spec):
    """网格规格 → ShotList dict（schema_version + 规范化字段，与执行器落盘形态一致）。"""
    return {
        "schema_version": "1.0.0",
        "shots": [
            {
                "shot_id": shot_id,
                "scene_id": scene_id,
                "covers": list(covers),
                "shot_size": shot_size,
                "camera": camera,
                "side": _AXIS_SIDE[scene_id],
                "movement": movement,
                "est_duration_ms": est_duration_ms,
                "alternatives": alternatives,
            }
            for (
                scene_id,
                shot_id,
                covers,
                shot_size,
                camera,
                movement,
                est_duration_ms,
                alternatives,
            ) in spec
        ],
    }


_GRID = [_build(spec) for spec in _GRID_SPECS]


class Policy:
    """手工首版：ShotList 网格顺序探测当前最优节点。"""

    def solve(self, env, budget):
        best_id = None
        probes = 0
        for round_no in range(len(_GRID)):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= budget.max_probes:
                break
            env.probe(best_id, _GRID[round_no % len(_GRID)])
            probes += 1
        return best_id or ""
