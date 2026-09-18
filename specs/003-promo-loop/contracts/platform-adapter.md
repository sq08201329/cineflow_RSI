# 契约：平台适配器（PlatformAdapter）

**模块**: `agents/promo/platform/` | **实现**: `SimulatedPlatform`（确定性，开发/CI 默认）、
`HttpRealPlatform`（真实渠道，凭证经环境变量注入）

## 1. 接口

```python
class PlatformAdapter(Protocol):
    def create_campaign(self, material: PromoMaterial, budget_usd: float) -> Campaign: ...
    def get_status(self, external_id: str) -> CampaignStatus: ...
    def fetch_metrics(self, external_id: str) -> MetricSnapshot: ...
    def pause(self, external_id: str) -> None: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 花费上限 | `create_campaign` 的实际扣费**不得超过** `budget_usd`；超出即平台侧错误 |
| 幂等 | `create_campaign` 携带客户端幂等键（round_id+material_id）；平台重复提交返回同一活动 |
| 指标就绪 | `delivered` 前 `fetch_metrics` 抛 `MetricsNotReadyError`；就绪后返回完整快照 |
| 指标校验 | 快照越界（比率 ∉ [0,1]、负计数）由调用方校验拒绝——适配器原样透传不篡改 |
| 失败 | 平台错误统一映射 `PlatformError`（含 `rate_limited`/`unavailable`/`invalid_request` 子类），不泄漏 SDK 异常类型 |
| 暂停 | `pause` 幂等；对已结束活动调用为无操作 |

## 3. 契约测试（tests/contract/test_platform_adapter.py）

**同一套契约用例对两个实现各跑一遍**：花费上限、幂等键、状态机推进、指标 schema、
错误映射。真实实现无凭证（环境变量缺失）时跳过其用例但保留套件；模拟实现必须全过。

## 4. 模拟实现确定性

`SimulatedPlatform`：以 `blake3(material.artifact_hash + budget + 平台 salt)` 为种子产出
指标（分布参数来自 configs）；同输入逐字节可复现；内部账本记录每次扣费供对账。
