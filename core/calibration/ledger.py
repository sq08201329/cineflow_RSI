"""校准台账与锚点分布快照（功能 010 US2，契约 C6 + FR-009）。

- 台账 ledger/{agent_id}/{evaluator_id}.jsonl：每轮每评估器一行 BiasRecord JSON，
  应用层只追加（append-only）；冻结语义由 DB 锚点与 git 历史共同保证（决策 2）；
- read_latest：读取最新一行（US3 提案生效时填评估器 calibration 字段的来源）；
- 锚点得分分布快照 snapshots/{agent_id}/{evaluator_id}/{period}.json：
  [0,1] 十等分分桶计数 + p25/p50/p75/p90 分位数（线性插值口径），
  per 评估器 per 周期一条，与台账同轮次落盘，字段供 F7 漂移检测消费。
"""

import json
from dataclasses import asdict
from pathlib import Path

from core.calibration.models import BiasRecord, PairingRecord

_BUCKET_COUNT = 10  # [0,1] 十等分，桶宽 0.1
_QUANTILE_KEYS = (("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p90", 0.90))


def ledger_path(data_dir: str | Path, agent_id: str, evaluator_id: str) -> Path:
    return Path(data_dir) / "ledger" / agent_id / f"{evaluator_id}.jsonl"


def append_ledger(data_dir: str | Path, agent_id: str, records: list[BiasRecord]) -> None:
    """追加台账行（evaluator_id 取 evaluator_key 的 @ 前缀）；既有行永不修改。"""
    for record in records:
        evaluator_id = record.evaluator_key.split("@")[0]
        path = ledger_path(data_dir, agent_id, evaluator_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record), ensure_ascii=False, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def read_latest(data_dir: str | Path, agent_id: str, evaluator_id: str) -> dict | None:
    """读取台账最新一行（JSON dict）；无台账 → None。"""
    path = ledger_path(data_dir, agent_id, evaluator_id)
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    return json.loads(lines[-1])


def _quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数（与 numpy 默认口径一致）。"""
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def write_anchor_snapshots(
    data_dir: str | Path, agent_id: str, period: str, pairs: list[PairingRecord]
) -> list[Path]:
    """按配对记录写锚点得分分布快照（剔除/缺分量记录不计入分布）。

    返回落盘路径列表；per 评估器 per 周期一条。
    """
    scores_by_evaluator: dict[str, list[float]] = {}
    for pair in pairs:
        if pair.excluded or pair.auto_score is None:
            continue
        scores_by_evaluator.setdefault(pair.evaluator_key, []).append(pair.anchor_score)

    paths: list[Path] = []
    for evaluator_key, scores in sorted(scores_by_evaluator.items()):
        evaluator_id = evaluator_key.split("@")[0]
        ordered = sorted(scores)
        buckets = [0] * _BUCKET_COUNT
        for score in ordered:
            buckets[min(int(score * _BUCKET_COUNT), _BUCKET_COUNT - 1)] += 1  # 1.0 落末桶
        payload = {
            "agent_id": agent_id,
            "evaluator_id": evaluator_id,
            "period": period,
            "samples": len(ordered),
            "bucket_width": 1.0 / _BUCKET_COUNT,
            "buckets": buckets,
            "quantiles": {name: _quantile(ordered, q) for name, q in _QUANTILE_KEYS},
        }
        path = Path(data_dir) / "snapshots" / agent_id / evaluator_id / f"{period}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    return paths
