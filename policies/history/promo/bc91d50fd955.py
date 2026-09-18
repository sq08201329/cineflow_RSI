"""变体策略（手写，探索参数人工调整）：双档探测。

按 temperature=0.3 / 0.7 两档交替 probe 当前最优节点，最多 4 次——
比基线多覆盖一档走法。
代码即策略：版本 = 本文件内容 BLAKE3 前 12 位（文件名即版本号）。
"""


class Policy:
    def solve(self, env, budget):
        grid = [{"temperature": 0.3}, {"temperature": 0.7}]
        best_id = None
        probes = 0
        for round_no in range(6):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= min(4, budget.max_probes):
                break
            env.probe(best_id, grid[round_no % len(grid)])
            probes += 1
        return best_id or ""
