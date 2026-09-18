# 契约：评估器基类、注册中心与合成评分

**模块**: `core/evaluators/` | **消费方**: 生产 Agent（实现评估器）、回放模拟器与做梦层（调用评分）

## 1. 评估器基类（`base.py`）

```python
class Evaluator(ABC):
    spec: EvaluatorSpec  # evaluator_id / version / kind / deterministic / cost_per_call / calibration

    @abstractmethod
    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult: ...

    def compare(self, a: ArtifactRef, b: ArtifactRef, context: dict) -> float:
        """仅 judge 类实现；返回 a 的胜率 ∈ [0,1]。默认 raise NotImplementedError。"""
        raise NotImplementedError
```

语义契约：

- `evaluate` 返回值必须满足 `0.0 ≤ score ≤ 1.0`，否则视为评估器故障（调用方落 FAILED 节点）；
- `diagnostics` 必须人可读，内容随节点 `eval_breakdown` 落盘；
- 确定性评估器对同一 `artifact` + `context` 的重复调用必须返回逐字节相同结果（回放正确性前提）；
- `artifact` 为内容寻址引用（哈希 + 可选元信息），评估器**不直接**接触对象存储客户端（FR-005 的边界）。

## 2. 注册中心（`registry.py`）

```python
def register(ev: Evaluator) -> None: ...
def get(evaluator_id: str, version: str) -> Evaluator: ...
def list_all() -> list[EvaluatorSpec]: ...
```

| 规则 | 行为 |
| --- | --- |
| 键 | `f"{spec.evaluator_id}@{spec.version}"`，全局唯一 |
| 重复注册 | 抛 `RegistrationError`，附冲突键（宪章原则一） |
| 确定性 | `deterministic=False` 且 `kind != HUMAN` → 抛 `RegistrationError` |
| 缺失 spec 字段 | 抛 `RegistrationError`（evaluator_id / version / kind 必填） |
| `get` 未命中 | 抛 `RegistrationError`，消息含可用版本列表 |

## 3. 合成评分（`composite.py`）

```python
def composite_score(breakdown: dict[str, EvalResult],
                    weights: dict[str, float]) -> float: ...
```

| 规则 | 行为 |
| --- | --- |
| 硬规则门禁 | 任一键以 `rule.` 开头且 `score == 0.0` → 返回 `0.0`（不可行解，无视其他得分） |
| 加权求和 | `sum(weights[k] * res.score)`；k 以 breakdown 与 weights 的交集之外出现 → 抛 `WeightMismatchError` |
| 权重来源 | 由调用方从形态配置（`configs/*.yaml`）注入；本函数**禁止**自行读取配置或硬编码权重（宪章原则五） |
| 权重总和 | 不做隐式归一化；Σweights 由配置作者保证（边界情况已在规格中声明） |

返回值 ∈ [0,1]（前提：各 EvalResult.score ∈ [0,1] 且 Σweights = 1，由校验保证）。
