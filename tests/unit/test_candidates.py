"""候选生成器单测（US1 / T409）。

MutatorGenerator 确定性逐字节复现、候选互异且语法合法、静态检查必过；
LLMGenerator 网关计费、代码块切分、网关失败不重试（003 分工）。
"""

import pytest

from dreaming.candidates import LLMGenerator, MutatorGenerator
from policies.static_check import check_policy_source


class TestMutatorGenerator:
    def test_确定性逐字节复现(self, champion_source):
        champion = champion_source()
        a = MutatorGenerator("dream-x-1").generate(champion, {"rounds": []}, 8)
        b = MutatorGenerator("dream-x-1").generate(champion, {"rounds": []}, 8)
        assert a == b  # 同种子逐字节一致

    def test_候选互异且数量足(self, champion_source):
        candidates = MutatorGenerator("dream-x-1").generate(champion_source(), {}, 8)
        assert len(candidates) == 8
        assert len(set(candidates)) == 8  # 互异

    def test_候选语法合法且过静态检查(self, champion_source):
        for source in MutatorGenerator("dream-x-1").generate(champion_source(), {}, 4):
            compile(source, "<candidate>", "exec")  # 语法合法
            check_policy_source(source)  # 白名单静态检查必过

    def test_变异真实改变行为参数(self, champion_source):
        candidates = MutatorGenerator("dream-x-1").generate(champion_source(), {}, 4)
        assert any("0.3" not in c or "0.7" not in c for c in candidates)  # 参数档被扰动

    def test_轮次变则种子变(self, champion_source):
        a = MutatorGenerator("dream-x-1").generate(champion_source(), {}, 4)
        b = MutatorGenerator("dream-x-2").generate(champion_source(), {}, 4)
        assert a != b


class TestLLMGenerator:
    def _gateway_with_canned(self, text):
        from core.llm_gateway.gateway import LLMGateway

        class CannedBackend:
            def __init__(self):
                self.call_count = 0

            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                from core.llm_gateway.gateway import BackendResult

                return BackendResult(text=text, prompt_tokens=100, completion_tokens=50)

        return LLMGateway(
            CannedBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )

    def test_代码块切分与网关计费(self):
        blocks = [
            "class Policy:\n    def solve(self, env, budget):\n        return 'a'\n",
            "class Policy:\n    def solve(self, env, budget):\n        return 'b'\n",
        ]
        text = "候选如下：\n```python\n" + "```\n```python\n".join(blocks) + "```\n"
        gateway = self._gateway_with_canned(text)
        generator = LLMGenerator(gateway, model="mock-copy-v1")
        candidates = generator.generate("champion-src", {"rounds": []}, 8)
        assert candidates == blocks  # 代码块切分
        assert gateway.call_count == 1
        assert gateway.total_cost_usd > 0  # 计费入账

    def test_代码块不足M_如实返回(self):
        text = "```python\nclass Policy:\n    pass\n```\n"
        generator = LLMGenerator(self._gateway_with_canned(text), model="mock-copy-v1")
        assert len(generator.generate("src", {}, 8)) == 1

    def test_网关失败不重试(self):
        """003 分工：网关内部已退避 3 次，生成器不得再重试（防叠加放大）。"""
        from core.llm_gateway.gateway import (
            LLMGateway,
            TransientBackendError,
        )

        class DownBackend:
            def __init__(self):
                self.call_count = 0

            def complete(self, prompt, *, model, temperature, max_tokens):
                self.call_count += 1
                raise TransientBackendError("持续不可用")

        gateway = LLMGateway(
            DownBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        generator = LLMGenerator(gateway, model="mock-copy-v1")
        with pytest.raises(TransientBackendError):
            generator.generate("src", {}, 4)
        # 网关 1+3 次退避后抛出；生成器零额外重试
        assert gateway.backend.call_count == 4
