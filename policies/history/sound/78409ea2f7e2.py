"""声音 champion 策略（手工首版，006 US3）：三类型 SoundGenParams 网格贪心探测。

代码即策略：版本 = 本文件内容 BLAKE3 前 12 位（文件名即版本号）。
每轮：observed() → 选已揭示中得分最高者（平分按 node_id 字典序，保证确定性）
→ 以三类型参数网格顺序 probe（2 TTS + 1 SFX + 1 music，对齐单轮探索配比）；
预算耗尽或网格用尽即停止。
"""

_GRID = [
    {"gen_type": "tts", "seed": 1, "voice": "narrator", "speed_tier": 1, "text": "你终于来了。"},
    {"gen_type": "tts", "seed": 2, "voice": "narrator", "speed_tier": 2, "text": "我等了很久。"},
    {"gen_type": "sfx", "seed": 3, "kind": "door_slam", "density": 1},
    {"gen_type": "music", "seed": 4, "mood": "悬疑", "tempo": 90, "duration_s": 2.0},
]


class Policy:
    """手工首版：网格顺序探测当前最优节点。"""

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
