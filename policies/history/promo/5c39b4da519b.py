"""基线策略（手写，人工 approve 前的一期策略形态）：保守单档探测。

仅按 temperature=0.3 一档 probe 当前最优节点，最多 2 次。
代码即策略：版本 = 本文件内容 BLAKE3 前 12 位（文件名即版本号）。
"""


class Policy:
    def solve(self, env, budget):
        best_id = None
        probes = 0
        for _ in range(3):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= min(2, budget.max_probes):
                break
            env.probe(best_id, {"temperature": 0.3})
            probes += 1
        return best_id or ""
