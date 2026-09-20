"""剪辑 champion 策略（手工首版，007 US3）：镜头库 + 分区约束 → EDL 网格贪心探测。

代码即策略：版本 = 本文件内容 BLAKE3 前 12 位（文件名即版本号）。
每轮：observed() → 选已揭示中得分最高者（平分按 node_id 字典序，保证确定性）
→ 以 EDL 网格顺序 probe（3 组 EDL，对齐 editing.edits_per_round 配比）；
预算耗尽或网格用尽即停止。

网格即剪辑决策：分区顺序 a→b→c 不降（场景分区约束）、同区衔接用叠化
（forbid_jump_cut_within_scene 规则库约束）、跨区用 cut、出入点取镜头中段。
"""

_GRID = [
    {
        "clips": [
            {"shot_id": "shot-1", "in_ms": 500, "out_ms": 3500,
             "transition": {"type": "dissolve", "duration_ms": 750}},
            {"shot_id": "shot-2", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-3", "in_ms": 0, "out_ms": 4000,
             "transition": {"type": "dissolve", "duration_ms": 500}},
            {"shot_id": "shot-4", "in_ms": 200, "out_ms": 3200,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-5", "in_ms": 0, "out_ms": 5000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ],
        "audio": [{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}],
    },
    {
        "clips": [
            {"shot_id": "shot-1", "in_ms": 0, "out_ms": 2500,
             "transition": {"type": "dissolve", "duration_ms": 500}},
            {"shot_id": "shot-2", "in_ms": 500, "out_ms": 3500,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-4", "in_ms": 0, "out_ms": 4000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-6", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ],
        "audio": [{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.6}],
    },
    {
        "clips": [
            {"shot_id": "shot-3", "in_ms": 0, "out_ms": 4000,
             "transition": {"type": "dissolve", "duration_ms": 500}},
            {"shot_id": "shot-4", "in_ms": 0, "out_ms": 3000,
             "transition": {"type": "cut", "duration_ms": 0}},
            {"shot_id": "shot-5", "in_ms": 1000, "out_ms": 6000,
             "transition": {"type": "cut", "duration_ms": 0}},
        ],
        "audio": [],
    },
]


class Policy:
    """手工首版：EDL 网格顺序探测当前最优节点。"""

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
            env.probe(best_id, _GRID[round_no])
            probes += 1
        return best_id or ""
