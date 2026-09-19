# 契约：做梦执行器

**模块**: `dreaming/pipeline.py` | **消费方**: 运营触发（CLI）、演示脚本

## 1. 执行入口

```python
def run_dream_round(agent_id: str, champion_source: str,
                    generator: CandidateGenerator, simulator_pool: SimulatorPool,
                    gateway: LLMGateway, config: DreamConfig) -> DreamRound: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 输入摘要 | digest = 最近 K 轮回放报告 + 评估器诊断摘要（K 来自 configs `dreaming.recent_k`）；digest 哈希入 DreamRound 可复核 |
| 候选数 | M 来自 configs `dreaming.candidates_per_round`（默认 128）；哈希去重后不足 M 如实记录 |
| 静态检查 | 复用 002 `policies/static_check.py` + 接口签名校验；rejected 候选**不得回放、不得记分**（FR-003/SC-002） |
| 回放 | 合格候选逐一经 002 沙箱对全池回放；超时/崩溃 → reward 记 0 且 diagnostics 注明（决策 6） |
| reward | `pareto_auc − λ·parallel_penalty`（λ 默认 0.5，configs `dreaming.lambda`）；口径见 data-model §1 |
| 零生成 | 全程生成 API 调用恒为 0；LLM 调用仅候选生成环节且全过网关入账（FR-012 审计断言） |
| 全灭/全 UNKNOWN | `failed_all_rejected` / `failed_all_unknown` 状态 + 告警诊断，不产出胜者 |
| 输出 | DreamRound（排名、winner_version、status）+ 候选明细可序列化 JSON |

## 3. 候选生成器协议

```python
class CandidateGenerator(Protocol):
    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]: ...
```

- `MutatorGenerator`：确定性模板变异（种子 = blake3(champion + round_id)）；标注 LLM 占位；
- `LLMGenerator`：digest → 提示词 → 网关 `chat` → 代码块切分；计费入账；
  网关失败不重试（003 分工约定）。

## 4. 边界语义

- M=1 合法；digest 为空历史（首轮做梦）时生成器收到空报告并注明；
- 胜出者进入审批流程（contracts/approval.md），pipeline 自身**不**更新部署指针。
