"""角色名实体一致性代理评估器：proxy.entity_consistency（连续分量，C8）。

扫描**行级归属指称**（`lines[].character`），按配置别名表（`character_aliases`）规范化
后逐条判定（确定性启发式，实现哈希 + 别名表哈希入版本号，原则一）：
- **同名异写**：未登记写法，但与某登记名同源（去常见前缀后相等/互为子串）→ 扣 0.2；
- **未登记指称**：角色表外写法且与任何登记名不同源 → 扣 0.3（人物表与正文脱节）；
- **指代歧义**：**对白行**未标注说话人 → 扣 0.1（无从归属）。动作行可无主体（群体
  动作）**不扣分**。

得分 = max(0, 1 − 扣分合计)（定点 6 位，不伪造高于实际的分）；每条缺陷逐条进
diagnostics（人可读诊断，供校准与人工核对）。场景出场角色清单的**存在性**由
`rule.scene_character` 门禁负责（C6），本代理只管指称写法口径——同一缺陷不重复计。
"""

import json

from agents.screenplay.config import ScreenplayConfigError
from agents.screenplay.evaluators._versioning import implementation_version
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.entity_consistency"

# 常见口语前缀（去前缀后与登记名同源 → 判为同一角色的另一种写法）
_VARIANT_PREFIXES = ("小", "老", "阿", "大")
# 缺陷扣分口径（实现常量，随实现哈希入版本号；变更即新版本）
_PENALTY_SAME_NAME_VARIANT = 0.2
_PENALTY_UNREGISTERED = 0.3
_PENALTY_PRONOUN_AMBIGUITY = 0.1


def _strip_prefix(name: str) -> str:
    for prefix in _VARIANT_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :]
    return name


def _canonical_variant(spelling: str, registered: frozenset[str]) -> str | None:
    """同源判定：返回疑似同源的最短登记名（确定性），不同源返回 None。

    同源关系（均为字符串级确定性判据，不调模型）：去常见前缀后相等 / 一方是另一方
    的子串。判定口径保守：命中即"疑似同名异写"，逐条诊断供人工核对（原则六）。
    """
    stripped = _strip_prefix(spelling)
    candidates = sorted(registered, key=lambda name: (len(name), name))
    for name in candidates:
        if stripped == name or stripped == _strip_prefix(name):
            return name
        if stripped in name or name in stripped:
            return name
    return None


class EntityConsistencyEvaluator(Evaluator):
    """角色名实体一致性代理（确定性、零成本；别名表 = 规范化口径）。"""

    def __init__(self, character_aliases: dict) -> None:
        if not isinstance(character_aliases, dict) or not character_aliases:
            raise ScreenplayConfigError(
                "proxy.entity_consistency 缺角色表（character_aliases），拒绝启动"
                "——规范化无依据时不得静默打分（原则五）"
            )
        self._aliases = {name: tuple(aliases) for name, aliases in character_aliases.items()}
        self._registered = frozenset(
            spelling for name, aliases in self._aliases.items() for spelling in (name, *aliases)
        )
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=implementation_version(
                json.dumps(self._aliases, sort_keys=True, ensure_ascii=False)
            ),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        script = context["artifact"]
        # 登记写法 = 配置别名表 ∪ 工件角色表（两处同源口径，任一登记即"正确写法"）
        registered = self._registered | script.registered_names()
        same_name_variants: list[dict] = []
        unregistered: list[dict] = []
        pronoun_ambiguities: list[str] = []
        for line in script.lines:
            if line.character is None:
                if line.kind == "dialogue":
                    pronoun_ambiguities.append(line.line_id)
                continue  # 动作行可无主体（群体动作），不扣分
            if line.character in registered:
                continue
            canonical = _canonical_variant(line.character, registered)
            if canonical is None:
                unregistered.append({"line_id": line.line_id, "spelling": line.character})
            else:
                same_name_variants.append(
                    {"line_id": line.line_id, "spelling": line.character, "canonical": canonical}
                )
        penalty = (
            len(same_name_variants) * _PENALTY_SAME_NAME_VARIANT
            + len(unregistered) * _PENALTY_UNREGISTERED
            + len(pronoun_ambiguities) * _PENALTY_PRONOUN_AMBIGUITY
        )
        score = quantize_score(max(0.0, 1.0 - penalty))
        return EvalResult(
            score=score,
            diagnostics={
                "applicable": True,
                "line_count": len(script.lines),
                "references": list(script.character_references()),
                "registered_names": sorted(registered),
                "same_name_variants": same_name_variants,
                "unregistered": unregistered,
                "pronoun_ambiguities": pronoun_ambiguities,
                "penalty": round(penalty, 6),
                "deductions": {
                    "same_name_variant": _PENALTY_SAME_NAME_VARIANT,
                    "unregistered": _PENALTY_UNREGISTERED,
                    "pronoun_ambiguity": _PENALTY_PRONOUN_AMBIGUITY,
                },
            },
        )
