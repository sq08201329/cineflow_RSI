"""渐进抽检、否决回滚与部署后漂移的回滚评估（功能 014 US3 / T1423，契约 C8~C10）。

- **C8 渐进抽检**：前 `first_n` 次自动部署**全量产复核任务**（样本少时全量复核信息价值
  最高），之后按 `ratio` 比例产任务（每 `ceil(1/ratio)` 次抽 1 次，确定性口径）；
  未抽中即如实返回 `None`（不产空任务）；同一部署事件重复开任务幂等；
  长期未复核只**告警**、绝不自动视为通过（`pending` 是如实状态，结论只能由人签署）；
- **C9 否决回滚 = 一个逻辑事务三件事**：①指针回滚到前一部署版本 ②模式回 `manual`
  ③写 `recalibration_required` 标记。②③先落地（保证"绝不停留在不确定状态"），
  ①失败（目标工件缺失）→ 显式 `RollbackTargetMissingError`，人工已回到全人工审批；
- **C10 部署后漂移的回滚评估**：judge 转 `suspect`/`confirmed_drift` → 产
  `RollbackEvent(trigger=drift_assessment)` 记录（**不自动回滚**：指针与模式都不动，
  处置权在 012 的人工流程）；
- 所有留痕文件化、只增不改（`deployment/spot_checks/`、`deployment/rollbacks/`）。
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from core.calibration.drift_models import DriftState
from core.deployment import mode
from core.deployment.auto_deploy import deploy_events, read_pointer
from core.deployment.config import DeploymentConfig
from core.deployment.errors import (
    DeploymentError,
    RollbackTargetMissingError,
)
from core.deployment.models import (
    DeployMode,
    RollbackEvent,
    RollbackTrigger,
    SpotCheckConclusion,
    SpotCheckRecord,
    SpotCheckTrigger,
)
from core.evaluators.errors import ValidationError
from core.yaml_edit import upsert_section_entries

SPOT_CHECKS_SUBDIR = "spot_checks"
ROLLBACKS_SUBDIR = "rollbacks"
DEFAULT_HISTORY_ROOT = Path("policies/history")
SYSTEM_AUTHOR = "system"


def spot_checks_dir(data_dir: str | Path) -> Path:
    """抽检留痕目录：deployment/spot_checks/。"""
    return Path(data_dir) / SPOT_CHECKS_SUBDIR


def rollbacks_dir(data_dir: str | Path) -> Path:
    """回滚留痕目录：deployment/rollbacks/。"""
    return Path(data_dir) / ROLLBACKS_SUBDIR


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp(at: str | None) -> str:
    moment = at or _now()
    try:
        return datetime.fromisoformat(moment).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    except (TypeError, ValueError) as exc:
        raise DeploymentError(f"时刻必须为 ISO 字符串，实际为 {moment!r}") from exc


def _write_append_only(directory: Path, record_id: str, payload: dict) -> Path:
    """留痕落盘（只增不改）：同 id 同内容幂等；同 id 不同内容拒绝覆盖。"""
    path = directory / f"{record_id}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return path
        raise DeploymentError(f"抽检/回滚留痕只增不改：{path} 已存在且内容不同")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def spot_check_records(data_dir: str | Path, agent_id: str | None = None) -> list[dict]:
    """抽检留痕（任务 + 人工结论，按时间序）；每条附加 `_path`。"""
    return _load_records(spot_checks_dir(data_dir), agent_id)


def rollback_events(data_dir: str | Path, agent_id: str | None = None) -> list[dict]:
    """回滚/漂移评估留痕（按时间序）；每条附加 `_path`。"""
    return _load_records(rollbacks_dir(data_dir), agent_id)


def _load_records(directory: Path, agent_id: str | None) -> list[dict]:
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if agent_id is not None and payload.get("agent_id") != agent_id:
            continue
        records.append({**payload, "_path": str(path)})
    return records


def record_path_from(record: SpotCheckRecord, data_dir: str | Path) -> Path:
    """由留痕记录定位文件（`record_id` = 文件名主干）。"""
    if not record.record_id:
        raise DeploymentError("留痕记录缺少 record_id：无法定位文件（不接受匿名记录）")
    return spot_checks_dir(data_dir) / f"{record.record_id}.json"


def _resolve_deploy_event(
    deploy_event: str | Path | Mapping, data_dir: str | Path, agent_id: str | None
) -> dict:
    """解析部署事件（路径或已加载 payload）：返回带 `_path` 的 payload。"""
    if isinstance(deploy_event, Mapping):
        payload = dict(deploy_event)
        if "_path" not in payload:
            payload["_path"] = str(deploy_event_path_for(data_dir, payload))
        return payload
    path = Path(deploy_event)
    if not path.is_file():
        raise DeploymentError(f"部署事件留痕不存在：{path}（抽检只针对已落痕的部署）")
    return {**json.loads(path.read_text(encoding="utf-8")), "_path": str(path)}


def deploy_event_path_for(data_dir: str | Path, payload: Mapping) -> Path:
    """按 payload 重算留痕路径（用于只给 payload 的场景）：命中已有文件即返回，否则抛错。"""
    agent_id = payload.get("agent_id")
    deployed_at = payload.get("deployed_at")
    if not agent_id or not deployed_at:
        raise DeploymentError("部署事件必须携带 agent_id 与 deployed_at（无法定位留痕）")
    candidates = [
        event
        for event in deploy_events(data_dir, agent_id)
        if event.get("deployed_at") == deployed_at
        and event.get("candidate_version") == payload.get("candidate_version")
    ]
    if not candidates:
        raise DeploymentError(
            f"部署事件未落痕（{agent_id}@{deployed_at}）：先部署再抽检（不产无源任务）"
        )
    return Path(candidates[0]["_path"])


def _sequence_of(data_dir: str | Path, agent_id: str, deploy_event_path: str) -> int:
    """部署序号（1 起，按留痕时间序）：渐进抽检的"第几次自动部署"。"""
    for index, event in enumerate(deploy_events(data_dir, agent_id), start=1):
        if event["_path"] == deploy_event_path:
            return index
    raise DeploymentError(f"部署事件不在留痕序列中：{deploy_event_path}")


def _trigger_for(seq: int, cfg: DeploymentConfig) -> SpotCheckTrigger | None:
    """渐进策略：前 first_n 次全量；之后每 ceil(1/ratio) 次抽 1 次；未中即 None。"""
    first_n = cfg.spot_check.first_n
    if seq <= first_n:
        return SpotCheckTrigger.FIRST_N
    stride = max(1, round(1 / cfg.spot_check.ratio))
    return SpotCheckTrigger.RATIO if (seq - first_n) % stride == 0 else None


def open_spot_check(
    deploy_event: str | Path | Mapping,
    *,
    data_dir: str | Path,
    cfg: DeploymentConfig,
    sequence: int | None = None,
    at: str | None = None,
) -> SpotCheckRecord | None:
    """按渐进策略开复核任务（契约 C8）；未抽中 → None；同一部署事件重复开任务幂等。"""
    event = _resolve_deploy_event(deploy_event, data_dir, None)
    agent_id = event["agent_id"]
    if sequence is None:
        sequence = _sequence_of(data_dir, agent_id, event["_path"])
    if sequence < 1:
        raise ValidationError(f"部署序号必须 ≥ 1，实际为 {sequence}")
    deploy_ref = str(event["_path"])

    for existing in spot_check_records(data_dir, agent_id):
        if existing["deploy_event"] == deploy_ref and existing["seq"] == sequence:
            return _record_from_dict(existing)  # 幂等：同一部署事件不重复产任务

    trigger = _trigger_for(sequence, cfg)
    if trigger is None:
        return None

    moment = at or _now()
    record_id = f"{_stamp(moment)}-{agent_id}-seq{sequence}"
    record = SpotCheckRecord(
        agent_id=agent_id,
        deploy_event=deploy_ref,
        seq=sequence,
        trigger=trigger,
        conclusion=SpotCheckConclusion.PENDING,
        by=SYSTEM_AUTHOR,
        at=moment,
        reason=(
            f"渐进抽检任务（第 {sequence} 次自动部署，触发={trigger.value}）："
            f"复核候选 {event['candidate_version']}；长期未复核只告警、不视为通过"
        ),
        record_id=record_id,
    )
    _write_append_only(spot_checks_dir(data_dir), record_id, record.to_dict())
    return record


def _record_from_dict(payload: Mapping) -> SpotCheckRecord:
    return SpotCheckRecord(
        agent_id=payload["agent_id"],
        deploy_event=payload["deploy_event"],
        seq=payload["seq"],
        trigger=payload["trigger"],
        conclusion=payload["conclusion"],
        by=payload["by"],
        at=payload["at"],
        reason=payload["reason"],
        record_id=payload.get("record_id", ""),
    )


def decide_spot_check(
    record_path: str | Path,
    *,
    conclusion: SpotCheckConclusion | str,
    by: str,
    reason: str,
    data_dir: str | Path,
    at: str | None = None,
) -> SpotCheckRecord:
    """人工抽检结论落痕（新档，不覆盖任务档）：`pass` 保持 auto；`veto` 走回滚事务。"""
    path = Path(record_path)
    if not path.is_file():
        raise DeploymentError(f"抽检任务不存在：{path}")
    task = json.loads(path.read_text(encoding="utf-8"))
    conclusion = SpotCheckConclusion(conclusion)
    if conclusion is SpotCheckConclusion.PENDING:
        raise ValidationError("人工结论不得为 pending（pending 是系统产任务的初始态）")
    if not by or by == SYSTEM_AUTHOR:
        raise ValidationError("抽检结论必须由人签署（by 必填且不得为 system）：系统不代签人工判断")
    if not reason:
        raise ValidationError("抽检结论必须带理由（留痕不可空白）")

    moment = at or _now()
    record_id = f"{_stamp(moment)}-{task['agent_id']}-seq{task['seq']}-{conclusion.value}"
    record = SpotCheckRecord(
        agent_id=task["agent_id"],
        deploy_event=task["deploy_event"],
        seq=task["seq"],
        trigger=task["trigger"],
        conclusion=conclusion,
        by=by,
        at=moment,
        reason=reason,
        record_id=record_id,
    )
    _write_append_only(spot_checks_dir(data_dir), record_id, record.to_dict())
    return record


def _conclusion_record(data_dir: str | Path, task: Mapping) -> dict | None:
    """该部署事件的既有结论留痕（除 pending 以外的人工作答）；无 → None。"""
    for record in spot_check_records(data_dir, task["agent_id"]):
        if (
            record["deploy_event"] == task["deploy_event"]
            and record["seq"] == task["seq"]
            and record["conclusion"] != SpotCheckConclusion.PENDING.value
        ):
            return record
    return None


def _require_artifact(agent_id: str, version: str, history_root: str | Path) -> Path:
    """回滚目标工件必须存在（缺失即显式报错——不悄悄回退到"看起来还行"的状态）。"""
    path = Path(history_root) / agent_id / f"{version}.py"
    if not path.is_file():
        raise RollbackTargetMissingError(
            f"回滚目标版本工件缺失：{path}（版本 {version} 的策略源码不在谱系内）——"
            "回滚未执行，模式已回 manual 且已标记门槛需重新标定：请人工补齐工件或指定其他目标"
        )
    return path


def veto_and_rollback(
    record_path: str | Path,
    *,
    by: str,
    reason: str,
    data_dir: str | Path,
    cfg: DeploymentConfig,
    config_path: str | Path,
    history_root: str | Path = DEFAULT_HISTORY_ROOT,
    at: str | None = None,
) -> RollbackEvent:
    """抽检否决 → 回滚（契约 C9，三件事一个逻辑事务）。

    顺序即失败语义：否决留痕 → ②模式回 `manual` → ③写重标定标记 → ①指针回滚。
    ①②③ 先落地保证"绝不停留在不确定状态"；①失败（目标工件缺失）显式报错，
    此时人工已恢复全人工审批（不会在 auto 上带病继续跑）。

    **失败可恢复**：目标工件缺失即报错后，补齐工件 / 指定其他目标可再次调用本函数据以
    完成 ①（否决结论已留痕，不重复落）；若指针已在目标版本则视为已完成（幂等）。
    """
    path = Path(record_path)
    if not path.is_file():
        raise DeploymentError(f"抽检任务不存在：{path}")
    task = json.loads(path.read_text(encoding="utf-8"))
    existing = _conclusion_record(data_dir, task)
    if existing is not None and existing["conclusion"] != SpotCheckConclusion.VETO.value:
        raise DeploymentError(
            f"该部署事件（{task['deploy_event']}）已复核**通过**：不得反向否决"
            "（留痕不可回溯改写；确有质量问题请按人工流程立新记录）"
        )
    moment = at or _now()
    agent_id = task["agent_id"]
    deploy_event = _resolve_deploy_event(task["deploy_event"], data_dir, agent_id)
    current_version = deploy_event["pointer_after"]
    target_version = deploy_event["from_version"]

    veto = (
        _record_from_dict(existing)
        if existing is not None
        else decide_spot_check(
            path,
            conclusion=SpotCheckConclusion.VETO,
            by=by,
            reason=reason,
            data_dir=data_dir,
            at=moment,
        )
    )
    state = mode.load_mode_state(data_dir, mode_default=cfg.mode_default, at=moment)
    if state.current is not DeployMode.MANUAL:
        mode.set_mode(
            DeployMode.MANUAL,
            by=by,
            reason=f"抽检否决（部署 {current_version}）：立即恢复全人工审批",
            cfg=cfg,
            data_dir=data_dir,
            at=moment,
        )
    mode.set_recalibration(
        data_dir,
        required=True,
        reason=f"抽检否决（部署 {current_version}，复核人 {by}）：门槛需重新标定",
        at=moment,
    )

    _require_artifact(agent_id, target_version, history_root)
    config_path = Path(config_path)
    config_path.write_text(
        upsert_section_entries(
            config_path.read_text(encoding="utf-8"),
            ("deployment", agent_id),
            {"current_policy_version": target_version},
        ),
        encoding="utf-8",
    )

    event = RollbackEvent(
        agent_id=agent_id,
        from_version=current_version,
        to_version=target_version,
        trigger=RollbackTrigger.SPOT_CHECK_VETO,
        at=moment,
        mode_after=DeployMode.MANUAL,
        recalibration_required=True,
        by=by,
        reason=reason,
        note=(
            f"抽检任务 {veto.record_id}：三件事同时生效——指针回滚到 {target_version}、"
            "模式回 manual、门槛需重新标定（重开 auto 须重新标定 + 重跑影子期）"
        ),
    )
    _write_append_only(rollbacks_dir(data_dir), f"{_stamp(moment)}-{agent_id}", event.to_dict())
    return event


def record_drift_assessment(
    agent_id: str,
    drift_status: DriftState | str | Mapping,
    *,
    data_dir: str | Path,
    config_path: str | Path,
    at: str | None = None,
) -> RollbackEvent | None:
    """部署后漂移的回滚评估（契约 C10）：产评估记录，**不自动回滚**。

    仅 `suspect` / `confirmed_drift` 需要评估（normal/false_alarm 返回 None，不制造噪音）；
    评估对象 = 最近一次自动部署（无留痕即报错，不伪造对象）；结论是"建议人工按 012 处置
    流程决定是否回退到前一版本"，指针与模式一律不动。
    """
    if isinstance(drift_status, Mapping):
        status = drift_status.get("status")
        evaluator_key = drift_status.get("evaluator_key", "")
    elif hasattr(drift_status, "status") and hasattr(drift_status, "evaluator_key"):
        # 012 的 DriftStatus（文件化登记读回的对象）：状态与评估器键都在
        status = drift_status.status
        evaluator_key = drift_status.evaluator_key
    else:
        status = drift_status
        evaluator_key = ""
    state = DriftState(status)
    if state not in (DriftState.SUSPECT, DriftState.CONFIRMED_DRIFT):
        return None

    ledger = deploy_events(data_dir, agent_id)
    if not ledger:
        raise DeploymentError(f"无自动部署留痕（{agent_id}）：漂移评估必须有评估对象（不伪造记录）")
    last = ledger[-1]
    current_version = read_pointer(config_path, agent_id)
    if not current_version:
        raise DeploymentError(
            f"部署指针缺失（deployment.{agent_id}.current_policy_version）：无法确定评估对象"
        )
    target_version = last["from_version"]
    if target_version == current_version:
        raise DeploymentError(
            f"漂移评估无可回退目标（当前部署 {current_version} 无更早版本可退回）"
        )
    moment = at or _now()
    current_mode = mode.load_mode_state(data_dir, at=moment).current
    event = RollbackEvent(
        agent_id=agent_id,
        from_version=current_version,
        to_version=target_version,
        trigger=RollbackTrigger.DRIFT_ASSESSMENT,
        at=moment,
        mode_after=current_mode,
        recalibration_required=False,
        by=SYSTEM_AUTHOR,
        reason=(
            f"部署后漂移：{evaluator_key or '评估器'} 状态 {state.value}"
            "（012 检测/处置口径）——产回滚评估记录"
        ),
        note=(
            f"评估结论：建议人工按 012 处置流程决定是否回退到 {target_version}；"
            "本记录**不自动回滚**（指针未变、模式未变），处置权在人"
        ),
    )
    record_id = f"{_stamp(moment)}-{agent_id}-seq{last.get('candidate_version', '')}"
    _write_append_only(rollbacks_dir(data_dir), record_id, event.to_dict())
    return event


def _with_candidate(records: list[dict], data_dir: str | Path) -> list[dict]:
    """只读补全候选版本号（供运营清单直接可读；不改动任何留痕文件）。"""
    by_path = {event["_path"]: event.get("candidate_version") for event in deploy_events(data_dir)}
    return [
        {**record, "candidate_version": by_path.get(record["deploy_event"])} for record in records
    ]


def pending_spot_checks(data_dir: str | Path, *, agent_id: str | None = None) -> list[dict]:
    """待复核任务（已产任务、尚无人工结论）：长期未复核只告警，不自动通过。"""
    records = spot_check_records(data_dir, agent_id)
    concluded = {
        (record["deploy_event"], record["seq"])
        for record in records
        if record["conclusion"] != SpotCheckConclusion.PENDING.value
    }
    return _with_candidate(
        [
            record
            for record in records
            if record["conclusion"] == SpotCheckConclusion.PENDING.value
            and (record["deploy_event"], record["seq"]) not in concluded
        ],
        data_dir,
    )


def stale_pending_checks(
    data_dir: str | Path,
    *,
    max_age_days: float,
    agent_id: str | None = None,
    at: str | None = None,
) -> list[dict]:
    """长期未复核任务（超期）→ 告警清单（阈值由调用方给，core 不硬编码运营节奏）。"""
    moment = datetime.fromisoformat(at) if at else datetime.now(UTC)
    stale = []
    for record in pending_spot_checks(data_dir, agent_id=agent_id):
        created = datetime.fromisoformat(record["at"])
        age_days = (moment - created).total_seconds() / 86400.0
        if age_days >= max_age_days:
            stale.append({**record, "age_days": age_days})
    return stale
