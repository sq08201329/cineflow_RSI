"""漂移检测器（功能 012 US1，契约 C1/C2；数据源 = 010 产物，只读）。

一轮检测：
`detect_drift(agent_id, evaluator_key, period, cfg, data_dir) -> DriftMetrics`

1. 读 010 快照序列 `calibration/snapshots/{agent}/{evaluator_id}/{period}.json`
   （**只读**，不修改 010 任何产物）；
2. 基线 = 滑动窗口（最近 `window` 周期、**不含当前周期**）的分桶合并分布 + 样本量加权
   分位数；窗口内缺口周期跳过不插值（如实标注）；
3. 指标 = PSI（主）+ 分位数位移向量（辅，p25/p50/p75/p90）；
   判定 = `PSI > psi_threshold` **或** `max|位移| > quantile_threshold` → `drift`；
4. 落盘 `calibration/drift/metrics/{agent}/{evaluator_id}/{period}.json`（只增不改：
   同口径同输入重复检测幂等，内容不同即拒绝改写——历史判定不回溯）。

诚实边界（原则六）：首周期无基线 → `no_baseline`（记基线不告警）；样本不足
（当前周期或基线窗口）→ `insufficient`；本周期无快照 → `no_data`；三种标注类
一律不带指标（不硬判、不伪造）；本模块不判原因、不处置、不改变评估器与树。

检测范围（FR-009）：默认仅 `kind=judge` 类（scope_kinds），范围外显式拒绝
（DriftOutOfScopeError，非静默跳过）。评估器升版即新 key → 新基线（旧版本数据不混入）。
"""

import json
import re
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import blake3

from core.calibration.drift_config import DriftConfig
from core.calibration.drift_models import (
    QUANTILE_NAMES,
    DriftBaseline,
    DriftMetrics,
    DriftVerdict,
)
from core.calibration.drift_stats import (
    bucket_proportions,
    max_abs_quantile_shift,
    merge_bucket_counts,
    psi,
    quantile_shifts,
)
from core.calibration.errors import DriftOutOfScopeError, DriftRecordConflictError
from core.evaluators.errors import ValidationError

DETECTOR_ID = "drift_detector"
DETECTOR_SEMVER = "1.0.0"
# 算法标识 = 分布距离口径 + 分位数合并口径（进口径哈希；算法变更必须改此串）
ALGORITHM = "psi+quantile.merged_weighted_mean.v1"

_PERIOD_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_WEEK = timedelta(days=7)


def _period_start(period: str) -> date:
    """周期标签（YYYY-Www）→ ISO 周首日（缺周/非法标签即报错，不禁默解析）。"""
    match = _PERIOD_RE.match(period) if isinstance(period, str) else None
    if match is None:
        raise ValidationError(f"周期标签必须为 YYYY-Www 形态，实际为 {period!r}")
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError as exc:
        raise ValidationError(f"周期标签非法（ISO 周不存在）：{period}") from exc


def _period_label(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def missing_periods(periods: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """序列中缺失的周期标签（相邻周期之间的 ISO 周缺口），升序；不插值不编造。"""
    ordered = sorted(periods, key=_period_start)
    missing: list[str] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        cursor = _period_start(previous) + _WEEK
        end = _period_start(current)
        while cursor < end:
            missing.append(_period_label(cursor))
            cursor += _WEEK
    return tuple(missing)


def kind_of(evaluator_key: str) -> str:
    """评估器类别（evaluator_id 前缀口径，与 010 composite 的 `judge.`/`rule.` 惯例一致）。

    接受 `evaluator_id@version` 或裸 `evaluator_id`（报表按快照目录枚举时只有裸键）。
    """
    if not isinstance(evaluator_key, str) or not evaluator_key:
        raise ValidationError(f"evaluator_key 必须为非空字符串，实际为 {evaluator_key!r}")
    evaluator_id = evaluator_key.partition("@")[0]
    return evaluator_id.split(".")[0] if "." in evaluator_id else ""


def in_scope(evaluator_key: str, cfg: DriftConfig) -> bool:
    """是否在检测范围内（calibration.drift.scope_kinds；默认仅 judge 类）。"""
    return kind_of(evaluator_key) in set(cfg.scope_kinds)


def _split_key(evaluator_key: str) -> tuple[str, str, str]:
    """`evaluator_id@version` → (evaluator_id, key, version)；缺版本即报错。"""
    if not isinstance(evaluator_key, str) or "@" not in evaluator_key:
        raise ValidationError(
            f"evaluator_key 必须为 evaluator_id@version 形态，实际为 {evaluator_key!r}"
        )
    evaluator_id, _, version = evaluator_key.partition("@")
    if not evaluator_id or not version:
        raise ValidationError(
            f"evaluator_key 必须为 evaluator_id@version 形态，实际为 {evaluator_key!r}"
        )
    return evaluator_id, evaluator_key, version


def metric_hash(cfg: DriftConfig) -> str:
    """口径哈希：算法标识 + 窗口 + 分桶 + 双维阈值 + 样本下限 + 检测范围。

    不含处置侧参数（suspect_weight / confirmed_exclude / double_signal）——那些属于
    门禁与报表口径，不改变"如何判漂移"；判定口径变更必须体现为新 detector_version。
    """
    payload = {
        "algorithm": ALGORITHM,
        "buckets": cfg.buckets,
        "window": cfg.window,
        "psi_threshold": cfg.psi_threshold,
        "quantile_threshold": cfg.quantile_threshold,
        "min_samples": cfg.min_samples,
        "scope_kinds": list(cfg.scope_kinds),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return blake3.blake3(canonical.encode("utf-8")).hexdigest()


def detector_version(cfg: DriftConfig) -> str:
    """口径版本：`drift_detector@1.0.0+{哈希前 12 位}`（进每条检测记录）。"""
    return f"{DETECTOR_ID}@{DETECTOR_SEMVER}+{metric_hash(cfg)[:12]}"


def snapshot_path(data_dir: str | Path, agent_id: str, evaluator_id: str, period: str) -> Path:
    """010 快照路径（只读路径，本模块从不写入）。"""
    return Path(data_dir) / "snapshots" / agent_id / evaluator_id / f"{period}.json"


def read_snapshot(
    data_dir: str | Path, agent_id: str, evaluator_id: str, period: str
) -> dict | None:
    """读 010 单周期快照；不存在 → None（不报错：缺口周期是常态）。"""
    path = snapshot_path(data_dir, agent_id, evaluator_id, period)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def available_periods(data_dir: str | Path, agent_id: str, evaluator_id: str) -> tuple[str, ...]:
    """某评估器已有快照的周期序列（升序）；无目录 → 空元组。"""
    root = Path(data_dir) / "snapshots" / agent_id / evaluator_id
    if not root.is_dir():
        return ()
    periods = [path.stem for path in root.glob("*.json")]
    return tuple(sorted(periods, key=_period_start))


def period_versions(data_dir: str | Path, agent_id: str, evaluator_id: str) -> dict[str, str]:
    """台账中 per 周期记录到的 evaluator_key（`ledger/{agent}/{evaluator_id}.jsonl`）。

    010 快照路径不含版本（`snapshots/{agent}/{evaluator_id}/{period}.json`），
    故版本边界以台账行为准（同周期多行取末行）：升版点即基线切分点（版本冻结，原则一）。
    台账缺某周期记录 → 该周期版本未知（调用方按同版本窗口合并，不做无依据的排除）。
    """
    path = Path(data_dir) / "ledger" / agent_id / f"{evaluator_id}.jsonl"
    if not path.is_file():
        return {}
    versions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        period = record.get("period")
        key = record.get("evaluator_key")
        if isinstance(period, str) and isinstance(key, str) and key:
            versions[period] = key
    return versions


def _bucket_counts(snapshot: dict, cfg: DriftConfig, *, where: str) -> tuple[int, ...]:
    counts = snapshot.get("buckets")
    if not isinstance(counts, list) or any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts
    ):
        raise ValidationError(f"{where} 的分桶计数非法（应为非负整数列表）：{counts!r}")
    if len(counts) != cfg.buckets:
        raise ValidationError(
            f"{where} 的分桶数 {len(counts)} 与 calibration.drift.buckets={cfg.buckets} 不一致"
            "（口径不匹配，拒绝静默比较）"
        )
    return tuple(counts)


def _snapshot_quantiles(snapshot: dict, *, where: str) -> dict[str, float]:
    quantiles = snapshot.get("quantiles")
    if not isinstance(quantiles, dict) or set(quantiles) != set(QUANTILE_NAMES):
        raise ValidationError(f"{where} 的 quantiles 必须恰好包含 {list(QUANTILE_NAMES)}")
    for name, value in quantiles.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"{where} 的 quantiles.{name} 必须为数值，实际为 {value!r}")
    return {name: float(quantiles[name]) for name in QUANTILE_NAMES}


def _snapshot_samples(snapshot: dict, *, where: str) -> int:
    samples = snapshot.get("samples")
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 0:
        raise ValidationError(f"{where} 的 samples 必须为 ≥ 0 的整数，实际为 {samples!r}")
    return samples


def select_window(
    data_dir: str | Path,
    agent_id: str,
    evaluator_key: str,
    period: str,
    cfg: DriftConfig,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """滑动窗口选取：返回（纳入基线的周期，因评估器升版被切分的周期）。

    - 候选 = 当前周期之前、最近 `window` 个有快照的周期（不含当前，缺口跳过不插值）；
    - 台账记录该周期版本 ≠ 当前版本 → 切分（旧版本数据不混入新版本基线，原则一）；
    - 台账无该周期记录 → 版本未知，按同版本窗口合并（如实口径，见 period_versions）。
    """
    evaluator_id, _, _ = _split_key(evaluator_key)
    current_start = _period_start(period)
    history = [
        candidate
        for candidate in available_periods(data_dir, agent_id, evaluator_id)
        if _period_start(candidate) < current_start
    ]
    versions = period_versions(data_dir, agent_id, evaluator_id)
    included: list[str] = []
    dropped: list[str] = []
    for window_period in history[-cfg.window :]:
        recorded = versions.get(window_period)
        if recorded is not None and recorded != evaluator_key:
            dropped.append(window_period)
        else:
            included.append(window_period)
    return tuple(included), tuple(dropped)


def _merge_baseline(
    agent_id: str,
    evaluator_key: str,
    window_periods: tuple[str, ...],
    dropped_periods: tuple[str, ...],
    cfg: DriftConfig,
    data_dir: str | Path,
) -> DriftBaseline | None:
    """把窗口内周期快照合并为基线（分桶相加 → 占比；分位数样本量加权平均）。"""
    if not window_periods:
        return None

    evaluator_id, _, _ = _split_key(evaluator_key)
    snapshots = []
    for window_period in window_periods:
        snapshot = read_snapshot(data_dir, agent_id, evaluator_id, window_period)
        if snapshot is None:  # 竞态保护：列表与文件不一致时按缺口跳过
            continue
        where = f"基线周期 {window_period} 的快照"
        snapshots.append(
            {
                "counts": _bucket_counts(snapshot, cfg, where=where),
                "quantiles": _snapshot_quantiles(snapshot, where=where),
                "samples": _snapshot_samples(snapshot, where=where),
            }
        )
    if not snapshots:
        return None

    periods = tuple(window_periods[: len(snapshots)])
    total_samples = sum(item["samples"] for item in snapshots)
    if total_samples > 0:
        quantiles = {
            name: sum(item["quantiles"][name] * item["samples"] for item in snapshots)
            / total_samples
            for name in QUANTILE_NAMES
        }
    else:  # 退化窗口（全部 0 样本）：等权平均，随后判为样本不足
        quantiles = {
            name: sum(item["quantiles"][name] for item in snapshots) / len(snapshots)
            for name in QUANTILE_NAMES
        }
    return DriftBaseline(
        evaluator_key=evaluator_key,
        agent_id=agent_id,
        periods=periods,
        buckets=bucket_proportions(merge_bucket_counts([item["counts"] for item in snapshots])),
        quantiles=quantiles,
        samples=total_samples,
        detector_version=detector_version(cfg),
        dropped_periods=dropped_periods,
    )


def build_baseline(
    agent_id: str,
    evaluator_key: str,
    period: str,
    cfg: DriftConfig,
    data_dir: str | Path,
) -> DriftBaseline | None:
    """滑动窗口基线（契约 C1）：最近 `window` 周期（不含当前）的分桶合并分布。

    - 无更早周期（首周期）→ None（调用方记基线不告警）；
    - 窗口内缺口周期跳过（不插值）；升版切分的周期不计入（dropped_periods 如实标注）。
    """
    _period_start(period)
    included, dropped = select_window(data_dir, agent_id, evaluator_key, period, cfg)
    return _merge_baseline(agent_id, evaluator_key, included, dropped, cfg, data_dir)


def record_path(data_dir: str | Path, agent_id: str, evaluator_key: str, period: str) -> Path:
    """检测记录路径：drift/metrics/{agent}/{evaluator_id}/{period}.json。"""
    evaluator_id, _, _ = _split_key(evaluator_key)
    return Path(data_dir) / "drift" / "metrics" / agent_id / evaluator_id / f"{period}.json"


def serialize_record(record: DriftMetrics) -> str:
    """记录序列化口径（缩进 2 + 键排序 + 末尾换行）：同输入同口径逐字节一致。"""
    return json.dumps(asdict(record), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_record(data_dir: str | Path, record: DriftMetrics) -> Path:
    """落盘检测记录（只增不改）。

    - 同周期同内容（同口径同输入重复检测）→ 幂等返回，不重复写、不报错；
    - 同周期内容不同 → 拒绝改写（口径变更只影响此后新周期，历史判定不回溯，原则一）。
    """
    path = record_path(data_dir, record.agent_id, record.evaluator_key, record.period)
    payload = serialize_record(record)
    if path.is_file():
        if path.read_text(encoding="utf-8") == payload:
            return path
        raise DriftRecordConflictError(
            f"漂移检测记录不可改写：{path}"
            f"（已存在且内容不同；口径变更须以新 detector_version 记录新周期，历史不回溯）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _gap_note(periods: tuple[str, ...]) -> str:
    missing = missing_periods(periods)
    if not missing:
        return ""
    return f"序列缺口周期：{', '.join(missing)}（不插值）"


def detect_drift(
    agent_id: str,
    evaluator_key: str,
    period: str,
    cfg: DriftConfig,
    data_dir: str | Path,
) -> DriftMetrics:
    """一轮漂移检测并落盘记录（契约 C1）；返回 DriftMetrics。

    范围外评估器显式拒绝（DriftOutOfScopeError）；判定类必须带两维指标，标注类不带
    指标（模型层约束）——检测只陈述，不处置、不写树、零生成/零 LLM 调用。
    """
    _split_key(evaluator_key)
    _period_start(period)
    if not in_scope(evaluator_key, cfg):
        raise DriftOutOfScopeError(
            f"评估器 {evaluator_key} 的类别 {kind_of(evaluator_key)!r} 未纳入漂移检测范围"
            f"（calibration.drift.scope_kinds={list(cfg.scope_kinds)}；"
            "proxy/rule 类默认关闭——其分布变化更可能是输入分布变化的镜像）"
        )

    evaluator_id, _, _ = _split_key(evaluator_key)
    version = detector_version(cfg)
    thresholds = cfg.thresholds_snapshot()
    included, dropped = select_window(data_dir, agent_id, evaluator_key, period, cfg)
    baseline = _merge_baseline(agent_id, evaluator_key, included, dropped, cfg, data_dir)
    snapshot = read_snapshot(data_dir, agent_id, evaluator_id, period)
    where = f"周期 {period} 的快照"

    gap_note = _gap_note((*included, period)) if included else ""
    version_note = f"升版切分基线（非本版本历史不混入）：{', '.join(dropped)}" if dropped else ""
    verdict = DriftVerdict.NORMAL
    psi_value: float | None = None
    shifts: dict[str, float] = {}
    baseline_ref: str | None = None
    note_parts: list[str] = []

    if snapshot is None:
        verdict = DriftVerdict.NO_DATA
        note_parts.append("本周期无快照（缺口周期无数据，不判定）")
        baseline_ref = baseline.period_range if baseline is not None else None
    else:
        counts = _bucket_counts(snapshot, cfg, where=where)
        current_quantiles = _snapshot_quantiles(snapshot, where=where)
        samples = _snapshot_samples(snapshot, where=where)
        if baseline is None:
            verdict = DriftVerdict.NO_BASELINE
            note_parts.append("首周期无基线（已记基线，不告警）")
        elif baseline.samples < cfg.min_samples:
            verdict = DriftVerdict.INSUFFICIENT
            baseline_ref = baseline.period_range
            note_parts.append(
                f"基线窗口样本不足（{baseline.samples} < min_samples={cfg.min_samples}，不判定）"
            )
        elif samples < cfg.min_samples:
            verdict = DriftVerdict.INSUFFICIENT
            baseline_ref = baseline.period_range
            note_parts.append(
                f"当前周期样本不足（{samples} < min_samples={cfg.min_samples}，不判定）"
            )
        else:
            baseline_ref = baseline.period_range
            psi_value = psi(baseline.buckets, counts)
            shifts = quantile_shifts(baseline.quantiles, current_quantiles)
            shift_max = max_abs_quantile_shift(shifts)
            triggered = ""
            if psi_value > cfg.psi_threshold:
                triggered = f"PSI {psi_value:.4f} > psi_threshold {cfg.psi_threshold}"
            if shift_max > cfg.quantile_threshold:
                by_shift = (
                    f"分位数最大位移 {shift_max:.4f} > quantile_threshold {cfg.quantile_threshold}"
                )
                triggered = f"{triggered}；{by_shift}" if triggered else by_shift
            if triggered:
                verdict = DriftVerdict.DRIFT
                note_parts.append(f"{triggered}；检测只判分布变化，不判原因（需结合人评锚点）")

    if gap_note:
        note_parts.append(gap_note)
    if version_note:
        note_parts.append(version_note)

    metrics = DriftMetrics(
        evaluator_key=evaluator_key,
        agent_id=agent_id,
        period=period,
        verdict=verdict,
        samples=_snapshot_samples(snapshot, where=where) if snapshot is not None else 0,
        detector_version=version,
        psi=psi_value,
        quantile_shifts=shifts,
        baseline_ref=baseline_ref,
        thresholds=thresholds,
        note="；".join(note_parts),
    )
    write_record(data_dir, metrics)
    return metrics
