# 契约：无偏性验收

**模块**: `core/replay/unbiasedness.py`（计算）+ `tests/unbiasedness/`（门禁执行）

## 1. 验收函数

```python
def kendall_tau(a: Sequence[float], b: Sequence[float]) -> float: ...

def verify_unbiasedness(real_scores: Sequence[float],
                        replay_scores: Sequence[float],
                        *, threshold: float = 0.95) -> UnbiasednessReport: ...
```

## 2. 语义

| 规则 | 行为 |
| --- | --- |
| τ 计算 | Kendall τ-b（处理同分对）；两序列等长、长度 ≥ 2，否则 `ValidationError`（样本不足） |
| 门槛 | 默认 0.95（configs 的 `replay.unbiasedness_tau_threshold` 可覆盖）；`tau ≥ threshold` → pass，否则 reject |
| FAILED 轮次 | 得分序列为空的轮次在计算前剔除并计入报告 notes |
| 报告 | JSON（schema 见 data-model.md §4）：tau、threshold、verdict、两序列、notes |

## 3. 门禁语义（宪章质量门禁清单）

- 新评估器 / 新 Agent 上线前必须执行，verdict=reject 即**发布阻塞**；
- 回归用例（故意注入偏差的轨迹对）必须 100% reject——该回归用例本身是 CI 的一部分；
- 轨迹来源：在线真实轨迹（一期以录制夹具模拟）+ 无泄漏模拟器回放轨迹
  （由**其他树**组成的池，不得含产生真实轨迹的那棵树）。
