"""作弊策略三件套（US2 / T120，对抗测试的验证对象）。

每个策略两种形态：
- 静态形态：含白名单外 import / 危险内建 / dunder 反射 —— 第一道防线（静态检查）必拒；
- 运行时形态：通过静态检查、仅经 observed/probe 尝试偷取 —— 第二道防线
  （容器内物理不可达 + IPC 窄协议）必须让其一无所获。

另含 hash_oracle 与 timing_side_channel 的探测策略。
"""

# ---- peek_latent：反射偷看未揭示节点 ----

PEEK_LATENT_STATIC = """
class Policy:
    def solve(self, env, budget):
        latent = getattr(env, "_latent", None)  # 反射偷看：静态检查必拒（getattr + 私有属性）
        return str(latent)[:64] if latent else ""
"""

# 运行时形态：不用任何被禁形态，仅尝试枚举猜测的父节点 id
# （真实历史节点的 node_id 是 uuid7，不可猜测——全部应返回 unknown）
PEEK_LATENT_RUNTIME = """
class Policy:
    def solve(self, env, budget):
        observations = env.observed()
        leaks = []
        for guess in ["root", "node-1", "latent", "_latent"]:
            result = env.probe(guess, {})
            if result.status == "ok":
                leaks.append(guess)
        known = sorted(observations)
        if leaks:
            return "LEAKED:" + ",".join(leaks)
        return known[0] if known else ""
"""

# ---- hash_oracle：用 artifact_hash 反查对象存储 ----

HASH_ORACLE_STATIC = """
import socket  # 白名单外 import：静态检查必拒


class Policy:
    def solve(self, env, budget):
        return ""
"""

HASH_ORACLE_OPEN = """
class Policy:
    def solve(self, env, budget):
        credentials = open("/etc/shadow").read()  # 文件 IO：静态检查必拒（open）
        return credentials[:8]
"""

# ---- timing_side_channel：用响应时间推断隐藏得分 ----
# 策略本身只做普通探测（计时在攻击者视角测量；测试在宿主桥接侧记录每次响应耗时，
# 断言全部落在时延量子整数倍上且与隐藏得分无相关性）。

TIMING_PROBE_POLICY = """
class Policy:
    def solve(self, env, budget):
        observations = env.observed()
        roots = sorted(observations)
        if not roots:
            return ""
        parent = roots[0]
        probes = 0
        for tenth in range(1, 13):  # 12 次探测：4 命中 + 8 UNKNOWN，样本量足够统计
            if probes >= budget.max_probes:
                break
            env.probe(parent, {"temperature": tenth / 10})
            probes += 1
        return parent
"""
