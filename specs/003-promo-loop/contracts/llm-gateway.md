# 契约：LLM 网关（最小版）

**模块**: `core/llm_gateway/` | **消费方**: agents/*（本期为 promo 物料生成）

## 1. 接口

```python
class LLMGateway:
    def chat(self, prompt: str, *, model: str, temperature: float = 0.0,
             max_tokens: int = 1024) -> LLMResult: ...
```

`LLMResult`：`text`、`usage`（prompt/completion tokens）、`cost_usd`、`cached: bool`。

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 统一入口 | 业务代码**禁止**绕过网关直连模型 API（宪章原则三）；网关是唯一能产生 LLM 成本的地方 |
| 计费 | 每次真实后端调用按模型价目表折算成本，返回并供调用方入账 CostRecord |
| 缓存 | key = blake3(model + prompt + 采样参数)；命中缓存 `cached=True`、成本为 0、不调用后端 |
| 重试 | 后端 5xx/超时按指数退避重试（上限 3 次）；4xx 不重试直接失败 |
| 确定性 | `temperature=0` 的调用在 Mock 后端逐字节可复现 |

## 3. 后端

- `MockBackend`（默认）：`blake3(prompt+params)` 为种子生成结构化伪文本；确定性、零成本、
  零网络——开发/CI 与全部自动化测试使用；
- `HttpBackend`：OpenAI 兼容端点（`OPENAI_BASE_URL`/`OPENAI_API_KEY` 环境变量注入）；
  本期无凭证环境，不由自动化测试覆盖真实调用。

## 4. 边界

- 价目表为 configs 一部分（模型 → 单价），缺价目即报错（不允许静默零成本）；
- 网关不做提示词业务逻辑——物料模板与组装属 `agents/promo/material.py`。
