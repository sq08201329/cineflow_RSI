"""确定性 Mock 后端（开发/CI 与全部自动化测试默认）。

以 blake3(model + prompt + 采样参数) 为种子生成结构化伪文本：
同输入逐字节可复现、零网络、零真实费用（成本仍由网关按价目表折算入账，
保证对账路径真实可走——"零成本"指无外部计费，非静默不计账）。
"""

import blake3

from core.llm_gateway.gateway import BackendResult

# 伪文案词表（确定性组句用，无业务含义）
_WORDS = [
    "光影",
    "故事",
    "镜头",
    "角色",
    "节奏",
    "画面",
    "情感",
    "悬念",
    "旅程",
    "瞬间",
    "想象",
    "共鸣",
    "叙事",
    "惊喜",
    "触动",
    "视界",
]


class MockBackend:
    """确定性伪后端：call_count 记录真实调用次数（缓存命中时不变）。"""

    def __init__(self) -> None:
        self.call_count = 0

    def complete(
        self, prompt: str, *, model: str, temperature: float, max_tokens: int
    ) -> BackendResult:
        self.call_count += 1
        seed = blake3.blake3(f"{model}|{prompt}|{temperature}|{max_tokens}".encode()).hexdigest()
        # 结构化伪文本：词表按种子取样组句
        words = [_WORDS[int(seed[i : i + 2], 16) % len(_WORDS)] for i in range(0, 16, 2)]
        text = f"[{model}] " + "，".join(words) + f"。（种子 {seed[:8]}）"
        return BackendResult(
            text=text,
            prompt_tokens=max(1, len(prompt) // 2),
            completion_tokens=max(1, min(max_tokens, len(text))),
        )
