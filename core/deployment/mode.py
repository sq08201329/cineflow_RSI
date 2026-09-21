"""部署模式状态机（功能 014 阶段 2 / T1408，契约 C4 + FR-004~006/FR-009）。

- 持久化 `deployment/mode.json`（当前态 + 变更历史）**只增不改**：新历史必须是已存历史的
  前缀扩展，删改既有条目即拒绝（原则二：留痕不可回溯改写）；
- 每次状态变更落事件留痕 `deployment/mode/{ts}-{mode}.json`（人/时间/理由，只增不改；
  同刻同内容幂等，同刻不同内容拒绝）；
- 影子期计时 = **模式区间累计**（进入 shadow 起算、切出结算，跨多次切换累加）；
- 门禁：`manual → auto` 禁止直连；`shadow → auto` 需影子期双下限满足；`recalibration_required`
  存在时 → auto 一律拒绝（FR-009）；
- 清重标定标记 = 人工确认已重标定 → 影子期**重新计时**（须重跑影子验证）。

模型层（`models.DeployModeState`）承载迁移合法性与计时非负；本模块只负责门禁判据、
人/时间/理由的必填与文件化留痕——**不自行放宽**模型规则。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from core.deployment.config import DeploymentConfig
from core.deployment.errors import (
    DeploymentRecordConflictError,
    ModeTransitionError,
)
from core.deployment.models import DeployMode, DeployModeState

MODE_FILE = "mode.json"
MODE_EVENTS_DIR = "mode"


def mode_path(data_dir: str | Path) -> Path:
    """模式状态文件：deployment/mode.json（当前态 + 变更历史，只增不改）。"""
    return Path(data_dir) / MODE_FILE


def mode_events_dir(data_dir: str | Path) -> Path:
    """模式变更事件目录：deployment/mode/（每次切换一条事件留痕）。"""
    return Path(data_dir) / MODE_EVENTS_DIR


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp(at: str | None) -> str:
    """时刻 → 事件文件时间戳（秒级 UTC；非法时间即报错，不静默用当前时间）。"""
    moment = at or _now()
    try:
        parsed = datetime.fromisoformat(moment)
    except (TypeError, ValueError) as exc:
        raise ModeTransitionError(f"模式变更时刻必须为 ISO 字符串，实际为 {moment!r}") from exc
    return parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def mode_event_path(data_dir: str | Path, at: str | None, mode: DeployMode) -> Path:
    return mode_events_dir(data_dir) / f"{_stamp(at)}-{mode.value}.json"


def load_mode_state(
    data_dir: str | Path,
    *,
    mode_default: DeployMode = DeployMode.MANUAL,
    at: str | None = None,
) -> DeployModeState:
    """读取当前模式态；`mode.json` 缺失 → 初始态（`mode_default`，只读不落盘）。"""
    path = mode_path(data_dir)
    if not path.is_file():
        return DeployModeState.initial(mode_default, at=at or _now())
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DeployModeState.from_dict(payload)


def mode_state(data_dir: str | Path, **kwargs) -> DeployModeState:
    """契约 C4 名：当前模式态读取（等价 `load_mode_state`）。"""
    return load_mode_state(data_dir, **kwargs)


def save_mode_state(data_dir: str | Path, state: DeployModeState) -> Path:
    """写入模式态（只增不改）：新历史必须是已存历史的前缀扩展，否则拒绝。"""
    if not isinstance(state, DeployModeState):
        raise ModeTransitionError(f"state 必须为 DeployModeState，实际为 {type(state).__name__}")
    path = mode_path(data_dir)
    if path.is_file():
        stored = DeployModeState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if state.history[: len(stored.history)] != stored.history:
            raise DeploymentRecordConflictError(
                f"模式状态只增不改：{path} 的既有历史不得删改（新历史必须是已存历史的前缀扩展）"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def save_mode_event(data_dir: str | Path, state: DeployModeState, *, at: str | None = None) -> Path:
    """切换事件留痕：`mode/{ts}-{mode}.json`（同刻同内容幂等；同刻不同内容拒绝覆盖）。"""
    entry = state.history[-1]
    path = mode_event_path(data_dir, at, state.current)
    payload = {
        "mode": state.current.value,
        "since": entry["since"],
        "by": entry["by"],
        "reason": entry["reason"],
        "shadow_days_accumulated": state.shadow_days_accumulated,
        "shadow_candidate_count": state.shadow_candidate_count,
        "recalibration_required": state.recalibration_required,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != text:
            raise DeploymentRecordConflictError(
                f"模式切换留痕只增不改：{path} 已存在且内容不同（不覆盖既有事件）"
            )
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def set_mode(
    to: DeployMode,
    *,
    by: str,
    reason: str,
    cfg: DeploymentConfig,
    data_dir: str | Path,
    at: str | None = None,
) -> DeployModeState:
    """模式迁移（契约 C4）：门禁判据 + 区间累计 + 双重留痕（mode.json 与事件文件）。

    门禁顺序即拒绝理由的确定性：①禁止名单之外的重标定期间禁开 auto；②迁移合法性
    （manual → auto 禁止直连）；③影子期双下限（FR-006）。任一拒绝 → **零副作用**。
    """
    state = load_mode_state(data_dir, mode_default=cfg.mode_default, at=at)
    target = state.ensure_transition(to)
    if target is DeployMode.AUTO:
        if state.recalibration_required:
            raise ModeTransitionError(
                "重标定期间禁止开启 auto（门槛需重新标定 + 重跑影子期）："
                f"标记来源 {state.recalibration_reason}"
            )
        gap = state.with_elapsed_shadow(at or _now()).shadow_window_gap(
            min_days=cfg.shadow.min_days, min_candidates=cfg.shadow.min_candidates
        )
        if gap:
            raise ModeTransitionError(f"{gap}：不得开启 auto（宁可留在人工/影子）")

    new_state = state.transition(target, at=at or _now(), by=by, reason=reason)
    save_mode_event(data_dir, new_state, at=at)
    save_mode_state(data_dir, new_state)
    return new_state


def record_shadow_candidate(data_dir: str | Path, *, at: str | None = None) -> DeployModeState:
    """影子期候选计数 +1（只在 shadow 期；候选数是影子期下限之一）。"""
    state = load_mode_state(data_dir, at=at)
    new_state = state.record_shadow_candidate()
    save_mode_state(data_dir, new_state)
    return new_state


def set_recalibration(
    data_dir: str | Path,
    *,
    required: bool,
    reason: str = "",
    at: str | None = None,
) -> DeployModeState:
    """置/清"门槛需重新标定"标记（置时必须带来源理由；清标记走 `clear_recalibration`）。"""
    state = load_mode_state(data_dir, at=at)
    new_state = state.set_recalibration(required, reason)
    save_mode_state(data_dir, new_state)
    return new_state


def clear_recalibration(
    data_dir: str | Path,
    *,
    by: str,
    reason: str,
    at: str | None = None,
) -> DeployModeState:
    """清重标定标记（人工确认已重标定）：**影子期重新计时**（须重跑影子验证，FR-009）。"""
    if not by or not reason:
        raise ModeTransitionError("清重标定标记必须带人（by）与理由（reason）：这是人工决策")
    moment = at or _now()
    state = load_mode_state(data_dir, at=moment)
    cleared = state.set_recalibration(False).reset_shadow_window(at=moment)
    entry = {"mode": cleared.current.value, "since": moment, "by": by, "reason": reason}
    cleared = replace(cleared, history=(*cleared.history, entry))
    save_mode_state(data_dir, cleared)
    return cleared
