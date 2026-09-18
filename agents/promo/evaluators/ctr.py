"""CTR 历史代理评估器：proxy.ctr_history（research 决策 4，FR-012）。

分桶（平台 × 物料类型 × 题材标签）贝塔平滑：(clicks + α) / (impressions + α + β)；
稀疏桶回退全局先验 α/(α+β)；只用已冻结的历史回流数据；
版本号携带数据快照哈希（1.0.0+<快照哈希前12位>），数据变即版本变。
"""

import json

import blake3

from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)

EVALUATOR_ID = "proxy.ctr_history"
BASE_VERSION = "1.0.0"


def _bucket_key(material: dict) -> tuple:
    """分桶键：平台 × 物料类型 × 题材标签集合（标签顺序无关）。"""
    return (
        material.get("platform", ""),
        material.get("kind", ""),
        tuple(sorted(material.get("tags", []))),
    )


def _snapshot_hash(history: list[dict]) -> str:
    canonical = json.dumps(history, sort_keys=True, ensure_ascii=False)
    return blake3.blake3(canonical.encode()).hexdigest()[:12]


class CtrHistoryEvaluator(Evaluator):
    """基于已冻结历史回流数据的 CTR 平滑估计（确定性）。"""

    def __init__(self, history: list[dict], *, ctr_prior: dict, ctr_cap: float) -> None:
        self._alpha = float(ctr_prior["alpha"])
        self._beta = float(ctr_prior["beta"])
        self._ctr_cap = ctr_cap
        # 分桶聚合：同桶多条记录累计曝光/点击
        self._buckets: dict[tuple, list[int]] = {}
        for record in history:
            key = (record["platform"], record["kind"], tuple(sorted(record.get("tags", []))))
            bucket = self._buckets.setdefault(key, [0, 0])
            bucket[0] += int(record["impressions"])
            bucket[1] += int(record["clicks"])
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=f"{BASE_VERSION}+{_snapshot_hash(history)}",
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        key = _bucket_key(context["material"])
        bucket = self._buckets.get(key)
        if bucket is None or bucket[0] == 0:
            ctr = self._alpha / (self._alpha + self._beta)  # 稀疏桶回退先验
            fallback = "prior"
            impressions, clicks = 0, 0
        else:
            impressions, clicks = bucket
            ctr = (clicks + self._alpha) / (impressions + self._alpha + self._beta)
            fallback = None
        return EvalResult(
            score=min(1.0, ctr / self._ctr_cap),
            diagnostics={
                "ctr": ctr,
                "bucket": [key[0], key[1], list(key[2])],
                "impressions": impressions,
                "clicks": clicks,
                "fallback": fallback,
            },
        )
