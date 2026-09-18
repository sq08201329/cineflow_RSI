"""确定性桩评估器（tests 唯一来源，T025）。

- rule / proxy_model / judge / human 各一，另含非确定性反例；
- 全部桩对同一输入返回固定结果，天然满足确定性回放前提（人类锚点例外：
  其产出写入即冻结为常数，故允许 deterministic=False 注册）。

注意：conftest 如需引用本模块，只做 fixture 包装，不得再定义桩。
"""

from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)


def make_spec(
    evaluator_id: str,
    kind: EvaluatorKind,
    *,
    version: str = "1.0.0",
    deterministic: bool = True,
    cost_per_call: float = 0.0,
    calibration: dict | None = None,
) -> EvaluatorSpec:
    """桩评估器的 spec 工厂。"""
    return EvaluatorSpec(
        evaluator_id=evaluator_id,
        version=version,
        kind=kind,
        deterministic=deterministic,
        cost_per_call=cost_per_call,
        calibration=calibration or {},
    )


class _FixedScoreStub(Evaluator):
    """返回固定分数的桩基类：deterministic 由 spec 声明。"""

    def __init__(self, spec: EvaluatorSpec, score: float = 1.0) -> None:
        self.spec = spec
        self._score = score
        self.calls = 0  # 便于测试断言调用次数

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        self.calls += 1
        return EvalResult(
            score=self._score,
            diagnostics={"stub": self.spec.evaluator_id, "artifact": artifact.artifact_hash},
        )


class StubRuleEvaluator(_FixedScoreStub):
    """硬规则桩：默认满分（通过门禁）。"""

    def __init__(self, evaluator_id: str = "rule.stub", score: float = 1.0, version: str = "1.0.0"):
        super().__init__(make_spec(evaluator_id, EvaluatorKind.RULE, version=version), score)


class StubProxyEvaluator(_FixedScoreStub):
    """代理模型桩：确定性，默认 0.8 分。"""

    def __init__(
        self, evaluator_id: str = "proxy.stub", score: float = 0.8, version: str = "1.0.0"
    ):
        super().__init__(make_spec(evaluator_id, EvaluatorKind.PROXY_MODEL, version=version), score)


class StubJudgeEvaluator(_FixedScoreStub):
    """judge 桩：evaluate 固定分；compare 按工件哈希确定性给出胜率。"""

    def __init__(
        self, evaluator_id: str = "judge.stub", score: float = 0.7, version: str = "1.0.0"
    ):
        super().__init__(make_spec(evaluator_id, EvaluatorKind.JUDGE, version=version), score)

    def compare(self, a: ArtifactRef, b: ArtifactRef, context: dict) -> float:
        # 确定性比较：哈希字典序大者胜，平局 0.5（纯函数，无随机性）
        if a.artifact_hash == b.artifact_hash:
            return 0.5
        return 1.0 if a.artifact_hash > b.artifact_hash else 0.0


class StubHumanEvaluator(_FixedScoreStub):
    """人类锚点桩：唯一允许 deterministic=False 注册的类型（宪章原则一例外）。"""

    def __init__(
        self, evaluator_id: str = "human.stub", score: float = 0.9, version: str = "1.0.0"
    ):
        super().__init__(
            make_spec(
                evaluator_id,
                EvaluatorKind.HUMAN,
                version=version,
                deterministic=False,  # 人类锚点例外：非确定性允许注册
                calibration={"samples": 0},
            ),
            score,
        )


class StubNonDeterministicEvaluator(_FixedScoreStub):
    """非确定性反例：proxy_model 类型却声明 deterministic=False，注册必须被拒。"""

    def __init__(self, evaluator_id: str = "proxy.nondet", version: str = "1.0.0"):
        super().__init__(
            make_spec(
                evaluator_id,
                EvaluatorKind.PROXY_MODEL,
                version=version,
                deterministic=False,
            ),
            score=0.5,
        )
