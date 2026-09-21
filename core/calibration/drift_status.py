"""漂移状态机与人工处置留痕（功能 012 US2，契约 C3；原则六：处置权在人）。

- `register_suspect`：**系统唯一自动写入**——仅当检测判定为 `drift`（超阈）时登记
  `suspect`，并携带触发指标引用（检测记录路径）；已登记则幂等（不重复追加历史）；
- `dispose`：**人工唯一通路**——确认漂移（`confirmed_drift`，动作 = 停用/换锚点升版）
  或判为误报（`false_alarm` → 恢复 `normal`，动作 = `restore`）；人/时间/理由/动作
  缺一不可；留痕与状态历史只增不改，既有文件同名即拒绝（不可改写）；
- 状态登记文件化 `drift/status/{evaluator_key_sanitized}.json`（当前态 + 历史），
  处置留痕 `drift/dispositions/{evaluator_key_sanitized}/{timestamp}.json`；
- 机检口径（SC-002）：历史中的 `suspect` 必带触发引用且无人工留痕引用，其余状态
  必带人工留痕引用——即"系统自动写入仅 suspect，终态仅人工"。

状态机合法迁移由 `drift_models.DriftStatus.transition` 强制（模型层机检）；本模块只
负责文件化持久化与人工参数校验，不自行放宽迁移规则。
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.calibration.drift_models import (
    DriftAction,
    DriftConclusion,
    DriftDisposition,
    DriftMetrics,
    DriftState,
    DriftStatus,
    DriftVerdict,
)
from core.calibration.errors import DriftRecordConflictError
from core.evaluators.errors import ValidationError

_SANITIZE_RE = re.compile(r"[^0-9A-Za-z._@-]")
# 状态严重度（门禁按 evaluator_id 匹配多版本时取最严重；false_alarm 已由人工判非漂移）
_SEVERITY = {
    DriftState.NORMAL: 0,
    DriftState.FALSE_ALARM: 0,
    DriftState.SUSPECT: 2,
    DriftState.CONFIRMED_DRIFT: 3,
}


def sanitize_key(evaluator_key: str) -> str:
    """evaluator_key → 文件名（保守替换非法字符；常规键原样保留 `@` 与 `.`）。"""
    if not isinstance(evaluator_key, str) or not evaluator_key:
        raise ValidationError(f"evaluator_key 必须为非空字符串，实际为 {evaluator_key!r}")
    return _SANITIZE_RE.sub("_", evaluator_key)


def status_dir(data_dir: str | Path) -> Path:
    """状态登记目录：drift/status/。"""
    return Path(data_dir) / "drift" / "status"


def registry_path(data_dir: str | Path, evaluator_key: str) -> Path:
    return status_dir(data_dir) / f"{sanitize_key(evaluator_key)}.json"


def disposition_dir(data_dir: str | Path, evaluator_key: str) -> Path:
    """处置留痕目录：drift/dispositions/{evaluator_key_sanitized}/。"""
    return Path(data_dir) / "drift" / "dispositions" / sanitize_key(evaluator_key)


def disposition_path(data_dir: str | Path, evaluator_key: str, at: str) -> Path:
    """留痕文件路径：时间戳做文件名（`:` → `-`，文件系统安全且可读）。"""
    return disposition_dir(data_dir, evaluator_key) / f"{at.replace(':', '-')}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _optional_ref(data_dir: str | Path, path: Path) -> str:
    """引用口径：相对 data_dir 的路径（可读且跨机器稳定）。"""
    base = Path(data_dir)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _entry(status: DriftStatus) -> dict:
    return {
        "status": status.status.value,
        "since": status.since,
        "trigger_metrics": status.trigger_metrics,
        "disposition_ref": status.disposition_ref,
    }


def load_registry(data_dir: str | Path, evaluator_key: str) -> dict | None:
    """读状态登记（当前态 + 历史）；无登记 → None。"""
    path = registry_path(data_dir, evaluator_key)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _status_from_entry(evaluator_key: str, entry: dict) -> DriftStatus:
    return DriftStatus(
        evaluator_key=evaluator_key,
        status=entry["status"],
        since=entry["since"],
        trigger_metrics=entry.get("trigger_metrics"),
        disposition_ref=entry.get("disposition_ref"),
    )


def current_status(data_dir: str | Path, evaluator_key: str) -> DriftStatus | None:
    """当前状态；无登记 → None（语义等价 normal：从未检出漂移）。"""
    payload = load_registry(data_dir, evaluator_key)
    if payload is None:
        return None
    return _status_from_entry(evaluator_key, payload)


def history(data_dir: str | Path, evaluator_key: str) -> tuple[dict, ...]:
    """状态历史（只增不改；含系统写入的 suspect 与人工写入的终态/恢复态）。"""
    payload = load_registry(data_dir, evaluator_key)
    if payload is None:
        return ()
    return tuple(payload.get("history", ()))


def dispositions(data_dir: str | Path, evaluator_key: str) -> tuple[DriftDisposition, ...]:
    """人工处置留痕（按时间戳文件名升序读回；只增不改）。"""
    root = disposition_dir(data_dir, evaluator_key)
    if not root.is_dir():
        return ()
    return tuple(
        DriftDisposition(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(root.glob("*.json"))
    )


def _save_registry(data_dir: str | Path, entries: list[dict], evaluator_key: str) -> Path:
    path = registry_path(data_dir, evaluator_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = entries[-1]
    payload = {
        "evaluator_key": evaluator_key,
        "status": current["status"],
        "since": current["since"],
        "trigger_metrics": current["trigger_metrics"],
        "disposition_ref": current["disposition_ref"],
        "history": list(entries),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def register_suspect(
    data_dir: str | Path,
    evaluator_key: str,
    metrics: DriftMetrics,
    *,
    at: str | None = None,
) -> DriftStatus:
    """超阈判定 → 登记 suspect（系统唯一自动写入；contract C3/C4 之 C3）。

    - 仅 `verdict == drift` 可登记（insufficient/no_baseline/no_data 一律拒绝——
      不把"没判出"写成"有漂移"）；`normal` 同样拒绝（未超阈不登记）；
    - 触发指标引用 = 检测记录路径（`drift/metrics/{agent}/{evaluator_id}/{period}.json`）；
    - 已登记 suspect → 幂等返回（不重复追加历史）；`confirmed_drift` 终态不可逆 →
      原样返回（新版本应另起 evaluator_key，版本冻结）。
    """
    if not isinstance(metrics, DriftMetrics):
        raise ValidationError("register_suspect 必须提供 DriftMetrics（检测记录）")
    if metrics.evaluator_key != evaluator_key:
        raise ValidationError(
            f"检测记录与评估器不一致：{metrics.evaluator_key!r} != {evaluator_key!r}"
        )
    if metrics.verdict is not DriftVerdict.DRIFT:
        raise ValidationError(
            f"仅超阈判定（verdict=drift）可登记 suspect，实际为 {metrics.verdict.value!r}"
            "（不判定的如实标注不产生状态）"
        )

    from core.calibration.drift_metrics import record_path

    trigger_ref = _optional_ref(
        data_dir, record_path(data_dir, metrics.agent_id, evaluator_key, metrics.period)
    )
    timestamp = at or _now()
    entries = list(history(data_dir, evaluator_key))
    existing = current_status(data_dir, evaluator_key)
    if existing is not None and existing.status in (
        DriftState.SUSPECT,
        DriftState.CONFIRMED_DRIFT,
    ):
        return existing  # 已在册（suspect 幂等 / confirmed_drift 终态不可逆）

    status = DriftStatus(
        evaluator_key=evaluator_key,
        status=DriftState.SUSPECT,
        since=timestamp,
        trigger_metrics=trigger_ref,
    )
    entries.append(_entry(status))
    _save_registry(data_dir, entries, evaluator_key)
    return status


def dispose(
    data_dir: str | Path,
    evaluator_key: str,
    conclusion: DriftConclusion,
    *,
    by: str,
    reason: str,
    action: DriftAction,
    at: str | None = None,
) -> DriftDisposition:
    """人工处置（唯一通路）：确认漂移 / 判为误报 → 留痕 + 状态迁移。

    - `confirmed_drift`：状态 → confirmed_drift（动作 = deactivate 停用 / reanchor 换锚点升版）；
    - `false_alarm`：状态 → false_alarm → normal（动作必须 = restore 恢复）；
    - 留痕先落盘（人/时间/理由/动作，只增不改），随后追加状态历史；同名留痕已存在即拒绝；
    - 无 suspect 登记 / 当前态非 suspect（含终态）→ 拒绝（处置必须对应未处置的漂移登记）。
    """
    current = current_status(data_dir, evaluator_key)
    if current is None:
        raise ValidationError(
            f"评估器 {evaluator_key} 无 suspect 登记，无可处置（系统只写 suspect；"
            "处置须对应一次超阈判定）"
        )
    if conclusion is DriftConclusion.FALSE_ALARM and action is not DriftAction.RESTORE:
        raise ValidationError(
            f"误报处置的动作必须为 restore（恢复 normal），实际为 {action.value!r}"
        )

    timestamp = at or _now()
    disposition = DriftDisposition(
        evaluator_key=evaluator_key,
        conclusion=conclusion,
        by=by,
        at=timestamp,
        reason=reason,
        action=action,
    )
    path = disposition_path(data_dir, evaluator_key, timestamp)
    disposition_ref = _optional_ref(data_dir, path)

    # 先按模型规则推演迁移（非法即拒绝——终态不可逆、系统不写终态），再落痕落状态
    entries = list(history(data_dir, evaluator_key))
    if conclusion is DriftConclusion.CONFIRMED_DRIFT:
        final = current.transition(
            DriftState.CONFIRMED_DRIFT,
            since=timestamp,
            by_human=True,
            disposition_ref=disposition_ref,
        )
        entries.append(_entry(final))
    else:
        flagged = current.transition(
            DriftState.FALSE_ALARM,
            since=timestamp,
            by_human=True,
            disposition_ref=disposition_ref,
        )
        entries.append(_entry(flagged))
        restored = flagged.transition(
            DriftState.NORMAL,
            since=timestamp,
            by_human=True,
            disposition_ref=disposition_ref,
        )
        entries.append(_entry(restored))

    if path.is_file():
        raise DriftRecordConflictError(
            f"处置留痕不可改写：{path}（同一评估器同一时刻的留痕已存在）"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "evaluator_key": disposition.evaluator_key,
                "conclusion": disposition.conclusion.value,
                "by": disposition.by,
                "at": disposition.at,
                "reason": disposition.reason,
                "action": disposition.action.value,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _save_registry(data_dir, entries, evaluator_key)
    return disposition


@dataclass(frozen=True)
class DriftRegistry:
    """状态登记视图（只读装配）：data_dir → 当前态映射（门禁与证据接口消费）。"""

    data_dir: Path

    @classmethod
    def load(cls, data_dir: str | Path) -> "DriftRegistry":
        return cls(data_dir=Path(data_dir))

    def current(self) -> dict[str, DriftStatus]:
        """全部当前状态（evaluator_key → DriftStatus）；无登记 → 空映射。"""
        root = status_dir(self.data_dir)
        if not root.is_dir():
            return {}
        statuses: dict[str, DriftStatus] = {}
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            key = payload["evaluator_key"]
            statuses[key] = _status_from_entry(key, payload)
        return statuses

    def lookup(self, evaluator_key: str) -> DriftStatus | None:
        """精确键（evaluator_id@version）查询；无登记 → None。"""
        return current_status(self.data_dir, evaluator_key)

    def status_of(self, evaluator_id: str) -> DriftStatus | None:
        """按 evaluator_id 匹配（权重键无版本，故需跨版本匹配）；多版本取最严重者。"""
        candidates = [
            status
            for key, status in self.current().items()
            if key.partition("@")[0] == evaluator_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda status: _SEVERITY[status.status])
