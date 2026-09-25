"""通用件（业务无关）：交付工件**归因元信息**门禁 `rule.artifact_metadata`（021 T2165）。

**为什么需要这个抽象**：形态接入要求"新增形态 = 新增配置 + 插件"，而插件落点分两处——
语义确实与某 Agent 绑定的薄工厂落 `agents/<agent>/evaluators/`，**与任何 Agent 无关的通用件**
落本包（`core/evaluators/plugins/`）。本模块就是通用件一侧的**最小可行样例**：它零 `agents.*`
import、零形态字面量与形态判断分支、零环境变量与网络读取、零配置文件读取，参数面为空
（签名无默认值参数 ⇒ 声明面 `params: {}`，无需任何形态取值），因而是"同一份插件代码可被多个
形态配置共用、形态差异只在配置值"的直接举证对象。

**判据（纯结构、无业务阈值、无形态概念）**：工件引用的 `metadata` 必须是**非空映射**且**逐键非空**
（`None`、空串/纯空白串、空容器视为未声明）——交付物必须带可归因的元信息，否则评估结论无法
归因到具体交付物、也无法随节点留痕回溯。本门禁**只判"有无与是否非空"**，不判业务正确性
（业务口径属形态配置与既有 Agent 绑定评估器的事，本模块不发明业务数字、不设业务阈值）。
缺项 ⇒ 判 0（不可行解）并在 `diagnostics` 中**逐键点名**；**不抛异常**（先例：既有 `rule.` 门禁）。

**版本口径**（原则一）：`spec.version` = **实现身份版本**（本模块字节 + `evaluator_id` 的摘要），
由唯一装配点的 `implementation_identity_version` 产出 ⇒ 行为变更（改本文件一字节）必然换版本；
形态/参数的变化不进版本（裁决 2026-09-25）。
"""

from core.evaluators.base import ArtifactRef, EvalResult, Evaluator, EvaluatorKind, EvaluatorSpec
from core.evaluators.plugin import implementation_identity_version

EVALUATOR_ID = "rule.artifact_metadata"
#: 版本前缀（实现身份版本 = 本前缀 + `+` + 12 位摘要）
BASE_VERSION = "1.0.0"


def _is_declared(value: object) -> bool:
    """该元信息取值是否构成"已声明"（空值一律视为未声明；`0`/`False` 是合法取值）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True


class ArtifactMetadataGate(Evaluator):
    """交付工件归因元信息门禁（确定性、零成本、零外部依赖）。"""

    def __init__(self) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=f"{BASE_VERSION}+pending000000",
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )
        # 实现身份版本由唯一装配点的同源函数产出（不另写一份摘要公式 ⇒ 零漂移）
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_identity_version(self, base=BASE_VERSION),
            kind=EvaluatorKind.RULE,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        metadata = artifact.metadata
        if not isinstance(metadata, dict):
            return EvalResult(
                score=0.0,
                diagnostics={
                    "applicable": True,
                    "declared_keys": [],
                    "empty_keys": [],
                    "violations": [
                        f"交付工件的归因元信息必须是映射，实测 {type(metadata).__name__}"
                    ],
                },
            )
        empty_keys = sorted(key for key, value in metadata.items() if not _is_declared(value))
        violations: list[str] = []
        if not metadata:
            violations.append("交付工件未声明任何归因元信息（metadata 为空映射）")
        if empty_keys:
            violations.append(f"归因元信息逐键非空：以下键未声明取值 {empty_keys}")
        return EvalResult(
            score=0.0 if violations else 1.0,
            diagnostics={
                "applicable": True,
                "declared_keys": sorted(metadata),
                "empty_keys": empty_keys,
                "violations": violations,
            },
        )


def artifact_metadata() -> Evaluator:
    """薄工厂（纯关键字签名、零参数）：一评估器一函数，与本包其余模块同一纪律。"""
    return ArtifactMetadataGate()
