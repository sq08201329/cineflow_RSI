"""校准台账与锚点分布快照（功能 010 US2，契约 C6 + FR-009；功能 020：快照 = 周期物化）。

- 台账 ledger/{agent_id}/{evaluator_id}.jsonl：每轮每评估器一行 BiasRecord JSON，
  应用层只追加（append-only）；冻结语义由 DB 锚点与 git 历史共同保证（决策 2）。
  功能 020 起行内登记**口径与快照溯源**（`period_days` / `window_semantics` / `round_id` /
  `anchor_count` / `snapshot_fingerprint`），由 `with_provenance` 在追加前补全
  （`core/calibration/bias.py` 的 `compute_bias` **不改签名**）。
- read_latest：读取最新一行（US3 提案生效时填评估器 calibration 字段的来源）；
- 锚点得分分布快照 snapshots/{agent_id}/{evaluator_id}/{period}.json：
  [0,1] 十等分分桶计数 + p25/p50/p75/p90 分位数（线性插值口径），
  per 评估器 per 周期一条，与台账同轮次落盘，字段供 F7 漂移检测消费。

**功能 020 的两条语义**（契约 C3）：

1. **快照 = 周期物化**：主键 =（评估器, 周期），内容 = 该周期**全部**锚点的分布；
   轮标识**不进**快照路径 ⇒ 同周期多轮**不产生第二份路径**（"同周期多轮零覆盖"由台账行
   与信度报告承担，不由快照文件承担）。
2. **内容变化必须记账、静默改写恒 0**：`snapshot_fingerprint` 是内容指纹，
   进**台账行**（本模块写）与**漂移记录**（012 写），**不写进快照文件**（避免自指），
   写方与读方各自重算 ⇒ 可直接比对；同内容重复物化是**幂等**的（零字节写入、返回同一指纹）。
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import blake3

from core.calibration.models import BiasRecord, PairingRecord
from core.calibration.periods import WINDOW_SEMANTICS, cadence_of
from core.evaluators.errors import ValidationError

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


def with_provenance(
    record: BiasRecord,
    *,
    period_days: int,
    window_semantics: str,
    round_id: str,
    anchor_count: int,
    snapshot_fingerprint: str | None,
) -> BiasRecord:
    """给台账行补全**口径与快照溯源**五个字段（纯函数，`compute_bias` 签名不变）。

    `anchor_count` = 该周期该评估器计入分布的锚点数（`None` 快照时传 0），
    与 `record.samples` 恒等（同一行的两个字段不得漂移）；
    `snapshot_fingerprint` = 所物化快照的指纹（无快照文件时为 `None`——如实标注，
    **不得**冒充"已记账"）。
    """
    if window_semantics != WINDOW_SEMANTICS:
        raise ValidationError(
            f"窗口口径取值域外：{window_semantics!r}（唯一取值 {WINDOW_SEMANTICS}），拒绝落盘"
        )
    if period_days != cadence_of(record.period):
        raise ValidationError(
            f"台账行口径不自洽：period_days={period_days} 与标签 {record.period!r} 的 cadence "
            f"{cadence_of(record.period)} 不一致"
        )
    return replace(
        record,
        period_days=period_days,
        window_semantics=window_semantics,
        round_id=round_id,
        anchor_count=anchor_count,
        snapshot_fingerprint=snapshot_fingerprint,
    )


def _quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数（与 numpy 默认口径一致）。"""
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def snapshot_fingerprint(payload: Mapping) -> str:
    """快照指纹 = BLAKE3(canonical JSON of payload)。

    canonical 化口径：`json.dumps(payload, ensure_ascii=False, sort_keys=True,
    separators=(",", ":"))` 的 UTF-8 字节。**稳定性定义**：① 对同一内容恒等
    （与缩进、键序、文件写入形态无关）；② 只依赖 payload 的**语义内容**，
    不依赖时间/路径/进程/随机数（同内容重复物化 ⇒ 同指纹）；③ payload 任一键或值变化
    ⇒ 指纹变化（含新增 `period_days` / `window_semantics` 键）。
    **进两处产物**：台账行 `snapshot_fingerprint` 与漂移记录 `snapshot_fingerprint`；
    **不写进快照文件**（避免自指），写方与读方各自重算 ⇒ 可直接比对。
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return blake3.blake3(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SnapshotMaterialization:
    """一次周期物化的回报：路径 + 身份 + 锚点数 + 内容指纹（供台账行与漂移记录引用）。"""

    path: Path
    agent_id: str
    evaluator_id: str
    period: str
    anchor_count: int
    snapshot_fingerprint: str


def _snapshot_payload(agent_id: str, evaluator_id: str, period: str, scores: list[float]) -> dict:
    """快照 payload：既有键**逐字保留**（010 的读取面不破），新增口径自描述两键。"""
    ordered = sorted(scores)
    buckets = [0] * _BUCKET_COUNT
    for score in ordered:
        buckets[min(int(score * _BUCKET_COUNT), _BUCKET_COUNT - 1)] += 1  # 1.0 落末桶
    return {
        "agent_id": agent_id,
        "evaluator_id": evaluator_id,
        "period": period,
        "samples": len(ordered),
        "bucket_width": 1.0 / _BUCKET_COUNT,
        "buckets": buckets,
        "quantiles": {name: _quantile(ordered, q) for name, q in _QUANTILE_KEYS},
        # 功能 020 新增（口径自描述；path 规则与主键均不变）
        "period_days": cadence_of(period),
        "window_semantics": WINDOW_SEMANTICS,
    }


def materialize_anchor_snapshots(
    data_dir: str | Path, agent_id: str, period: str, pairs: list[PairingRecord]
) -> list[SnapshotMaterialization]:
    """按配对记录物化锚点得分分布快照（剔除/缺分量记录不计入分布）。

    返回 `list[SnapshotMaterialization]`（路径 + 锚点数 + 内容指纹）；per 评估器 per 周期一条。
    **主键 =（评估器, 周期）**：cadence 由标签派生（`cadence_of`）⇒ 既有调用点签名零改动。
    **同内容重复物化 = 幂等**（零字节写入）；内容变化则重写并由调用方落台账行（必记账）。
    """
    scores_by_evaluator: dict[str, list[float]] = {}
    for pair in pairs:
        if pair.excluded or pair.auto_score is None:
            continue
        scores_by_evaluator.setdefault(pair.evaluator_key, []).append(pair.anchor_score)

    materializations: list[SnapshotMaterialization] = []
    for evaluator_key, scores in sorted(scores_by_evaluator.items()):
        evaluator_id = evaluator_key.split("@")[0]
        payload = _snapshot_payload(agent_id, evaluator_id, period, scores)
        path = Path(data_dir) / "snapshots" / agent_id / evaluator_id / f"{period}.json"
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            # 内容变化才写（同内容 ⇒ 零字节写入：mtime 与字节均不变）
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        materializations.append(
            SnapshotMaterialization(
                path=path,
                agent_id=agent_id,
                evaluator_id=evaluator_id,
                period=period,
                anchor_count=len(scores),
                snapshot_fingerprint=snapshot_fingerprint(payload),
            )
        )
    return materializations


def write_anchor_snapshots(
    data_dir: str | Path, agent_id: str, period: str, pairs: list[PairingRecord]
) -> list[Path]:
    """`materialize_anchor_snapshots` 的**兼容薄封装**（签名与返回类型不变，`list[Path]`）。

    既有调用点（`ops/demo_web.py`、`ops/demo_judge_drift.py`、测试夹具）零改动。
    """
    return [item.path for item in materialize_anchor_snapshots(data_dir, agent_id, period, pairs)]
