"""候选生成器（T411，contracts/dreaming.md §3，research 决策 2）。

- CandidateGenerator 协议：generate(champion_source, digest, m) -> list[str]；
- MutatorGenerator：确定性模板变异（种子 = blake3(champion + round_id)），
  参数档扰动——明确标注为 LLM 占位（机制验证与 LLM 能力解耦）；
- LLMGenerator：digest → 提示词 → 网关 chat → 代码块切分；计费入账；
  网关失败不重试（网关内部已退避 3 次，双层重试禁止叠加放大）。
"""

import re
from typing import Protocol

import blake3

from core.llm_gateway.gateway import LLMGateway
from dreaming.digest import digest_hash

_TEMPERATURE_RE = re.compile(r'\{"temperature": [0-9.]+\}')


class CandidateGenerator(Protocol):
    """候选生成器协议：由冠军策略源码与输入摘要产出 M 套候选策略源码。"""

    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]: ...


class MutatorGenerator:
    """确定性模板变异器（开发/CI 默认，LLM 占位——明确标注）。

    对冠军源码的温度参数档做种子化扰动；同 (champion, round_id) 逐字节复现。
    """

    def __init__(self, round_id: str) -> None:
        self._round_id = round_id

    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]:
        import random

        seed = blake3.blake3((champion_source + self._round_id).encode()).hexdigest()
        rng = random.Random(int(seed[:16], 16))
        candidates = []
        for i in range(m):
            # 参数档扰动：每个网格项一档新温度（两位小数定点，确定性）
            temps = iter([round(rng.uniform(0.1, 0.9), 2)] * 16)

            def _next_temp(_match, temps=temps):
                return f'{{"temperature": {next(temps)}}}'

            mutated = _TEMPERATURE_RE.sub(_next_temp, champion_source)
            header = (
                f"# 候选 {i}（确定性变异占位——LLM 接入后由 LLMGenerator 承担）\n"
                f"# 种子: {seed[:12]} 轮次: {self._round_id}\n"
            )
            candidates.append(header + mutated)
        return candidates


class LLMGenerator:
    """LLM 候选生成器：digest → 提示词 → 网关 → 代码块切分（计费全过网关）。"""

    _BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)

    def __init__(self, gateway: LLMGateway, model: str) -> None:
        self._gateway = gateway
        self._model = model

    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]:
        prompt = (
            "你是探索策略工程师。阅读当前最优策略代码与最近回放摘要，"
            f"产出 {m} 套各有差异的改进策略代码（每套一个 ```python 代码块，"
            "类名 Policy，方法 solve(self, env, budget)）。\n\n"
            f"## 当前最优策略\n```python\n{champion_source}\n```\n\n"
            f"## 最近回放摘要（哈希 {digest_hash(digest)[:12]}）\n"
            f"{digest.get('rounds', [])}\n"
            f"{digest.get('note', '')}"
        )
        # 网关失败不重试：网关内部已指数退避 3 次（003 分工约定）
        result = self._gateway.chat(prompt, model=self._model, temperature=0.0)
        blocks = self._BLOCK_RE.findall(result.text)
        return [block.strip() + "\n" for block in blocks][:m]
