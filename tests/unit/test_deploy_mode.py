"""部署模式状态机单测（功能 014 阶段 2 / T1407，先于实现编写；契约 C4 + FR-004~006/FR-009）。

覆盖：
- 合法迁移（manual ⇄ shadow ⇄ auto）与非法迁移拒绝（**manual → auto 禁止直连**）；
- 影子期区间累计（切换即暂停/恢复，跨多次切换累加）与候选数累计；
- 影子期双下限门禁（时长 + 候选数，未满 → 拒绝并注明缺口）；
- `recalibration_required` 阻断 auto（重标定期间一律拒绝；清标记需重新标定 + 重跑影子期）；
- 持久化 `deployment/mode.json` **只增不改**（历史删改即拒绝）+ 每次状态变更的事件留痕
  `deployment/mode/{ts}-{mode}.json`（只增不改，同刻同内容幂等）。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from core.deployment import mode
from core.deployment.errors import (
    DeploymentRecordConflictError,
    ModeTransitionError,
)
from core.deployment.models import DeployMode, DeployModeState
from core.evaluators.errors import ValidationError

T0 = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)


def _at(days: float = 0.0) -> str:
    """基准时刻 + 偏移天数（ISO；影子期区间累计的确定时钟）。"""
    return (T0 + timedelta(days=days)).isoformat()


def _open_shadow(data_dir, cfg, *, days: float = 0.0) -> DeployModeState:
    return mode.set_mode(
        DeployMode.SHADOW,
        by="ops",
        reason="开影子期（判定照跑、指针不动）",
        cfg=cfg,
        data_dir=data_dir,
        at=_at(days),
    )


def _to_auto(data_dir, cfg, *, days: float) -> DeployModeState:
    return mode.set_mode(
        DeployMode.AUTO,
        by="ops",
        reason="影子期达标（14 天 / 20 候选）",
        cfg=cfg,
        data_dir=data_dir,
        at=_at(days),
    )


def _to_manual(data_dir, cfg, *, days: float, reason="人工切回全人工") -> DeployModeState:
    return mode.set_mode(
        DeployMode.MANUAL,
        by="ops",
        reason=reason,
        cfg=cfg,
        data_dir=data_dir,
        at=_at(days),
    )


def _fill_window(data_dir, *, candidates: int = 20) -> None:
    for _ in range(candidates):
        mode.record_shadow_candidate(data_dir, at=_at(1))


def test_initial_state_comes_from_config_default_without_writing(
    deployment_data_dir, deployment_config
):
    """模式默认值取自配置；未发生切换时**不落盘**（现状不变，mode/pointer 零变更）。"""
    state = mode.load_mode_state(
        deployment_data_dir, mode_default=deployment_config.mode_default, at=_at()
    )
    assert state.current is DeployMode.MANUAL
    assert state.shadow_days_accumulated == 0.0
    assert state.shadow_candidate_count == 0
    assert state.history[0]["mode"] == "manual"
    assert not mode.mode_path(deployment_data_dir).exists()


def test_set_mode_persists_state_and_switch_event(deployment_data_dir, deployment_config):
    """切换 → mode.json（当前态 + 历史）与事件留痕同落；重读逐字段一致。"""
    state = _open_shadow(deployment_data_dir, deployment_config)
    payload = json.loads(mode.mode_path(deployment_data_dir).read_text(encoding="utf-8"))
    assert payload["current"] == "shadow"
    assert [entry["mode"] for entry in payload["history"]] == ["manual", "shadow"]
    assert payload["shadow_since"] == _at()
    events = sorted((deployment_data_dir / "mode").glob("*-shadow.json"))
    assert len(events) == 1
    assert json.loads(events[0].read_text(encoding="utf-8"))["reason"].startswith("开影子期")
    assert mode.load_mode_state(deployment_data_dir) == state


def test_manual_to_auto_direct_is_rejected_without_side_effects(
    deployment_data_dir, deployment_config
):
    """manual → auto 直连禁止（未经影子期）；拒绝即零副作用（不落态、不落事件）。"""
    with pytest.raises(ModeTransitionError, match="manual"):
        mode.set_mode(
            DeployMode.AUTO,
            by="ops",
            reason="直接开自动",
            cfg=deployment_config,
            data_dir=deployment_data_dir,
            at=_at(),
        )
    assert not mode.mode_path(deployment_data_dir).exists()
    assert not list((deployment_data_dir / "mode").glob("*.json"))


def test_shadow_to_auto_rejected_when_window_unmet(deployment_data_dir, deployment_config):
    """影子期未满（时长与候选数缺口）→ 拒绝并注明缺口（FR-006 机检门禁）。"""
    _open_shadow(deployment_data_dir, deployment_config)
    _fill_window(deployment_data_dir, candidates=1)
    with pytest.raises(ModeTransitionError) as excinfo:
        _to_auto(deployment_data_dir, deployment_config, days=1)
    message = str(excinfo.value)
    assert "14" in message and "20" in message
    assert mode.load_mode_state(deployment_data_dir).current is DeployMode.SHADOW


def test_shadow_to_auto_allowed_when_window_met(deployment_data_dir, deployment_config):
    """影子期双下限满足 → 允许开 auto（一次合法迁移）。"""
    _open_shadow(deployment_data_dir, deployment_config)
    _fill_window(deployment_data_dir)
    state = _to_auto(deployment_data_dir, deployment_config, days=14)
    assert state.current is DeployMode.AUTO
    assert state.shadow_days_accumulated == pytest.approx(14.0)
    assert state.shadow_candidate_count == 20


def test_recalibration_blocks_auto_even_when_window_met(deployment_data_dir, deployment_config):
    """重标定期间 → auto 一律拒绝（即使影子期已满，FR-009）。"""
    _open_shadow(deployment_data_dir, deployment_config)
    _fill_window(deployment_data_dir)
    _to_auto(deployment_data_dir, deployment_config, days=14)
    _to_manual(
        deployment_data_dir,
        deployment_config,
        days=15,
        reason="抽检否决：立即回滚并恢复全人工",
    )
    mode.set_recalibration(
        deployment_data_dir,
        required=True,
        reason="抽检否决（部署 evt-001）：门槛需重新标定",
        at=_at(15),
    )
    _open_shadow(deployment_data_dir, deployment_config, days=16)
    with pytest.raises(ModeTransitionError, match="重标定"):
        _to_auto(deployment_data_dir, deployment_config, days=30)


def test_clearing_recalibration_restarts_shadow_window(deployment_data_dir, deployment_config):
    """清标记 = 人工确认已重标定 → 影子期重新计时（须重跑影子验证，FR-009）。"""
    _open_shadow(deployment_data_dir, deployment_config)
    _fill_window(deployment_data_dir)
    _to_auto(deployment_data_dir, deployment_config, days=14)
    _to_manual(deployment_data_dir, deployment_config, days=15, reason="抽检否决")
    mode.set_recalibration(deployment_data_dir, required=True, reason="门槛待重标定", at=_at(15))
    cleared = mode.clear_recalibration(
        deployment_data_dir, by="ops", reason="门槛已重标定（新阈值 v2）", at=_at(20)
    )
    assert cleared.recalibration_required is False
    assert cleared.shadow_days_accumulated == 0.0
    assert cleared.shadow_candidate_count == 0
    _open_shadow(deployment_data_dir, deployment_config, days=20)
    with pytest.raises(ModeTransitionError, match="影子期"):
        _to_auto(deployment_data_dir, deployment_config, days=21)


def test_shadow_days_accumulate_across_intervals(deployment_data_dir, deployment_config):
    """影子计时按模式区间累计（切出暂停、再入恢复），候选数同步累计。"""
    _open_shadow(deployment_data_dir, deployment_config, days=0)
    _fill_window(deployment_data_dir, candidates=2)
    _to_manual(deployment_data_dir, deployment_config, days=3, reason="暂停影子（人工介入）")
    paused = mode.load_mode_state(deployment_data_dir)
    assert paused.shadow_days_accumulated == pytest.approx(3.0)
    assert paused.shadow_since is None
    _open_shadow(deployment_data_dir, deployment_config, days=10)
    _fill_window(deployment_data_dir, candidates=3)
    ended = _to_manual(deployment_data_dir, deployment_config, days=12.5, reason="再暂停")
    assert ended.shadow_days_accumulated == pytest.approx(5.5)
    assert ended.shadow_candidate_count == 5


def test_record_shadow_candidate_requires_shadow_mode(deployment_data_dir, deployment_config):
    """候选计数只在影子期进行（manual/auto 期计数会污染影子期证据）。"""
    with pytest.raises(ValidationError):
        mode.record_shadow_candidate(deployment_data_dir)


def test_mode_json_history_is_append_only(deployment_data_dir, deployment_config):
    """mode.json 只增不改：历史被删改即拒绝写入（留痕不可回溯改写，原则二）。"""
    state = _open_shadow(deployment_data_dir, deployment_config)
    # 回退历史（少一条切换）——形态自洽但与已存留痕不构成前缀 → 拒绝
    rolled_back = replace(
        state, current=DeployMode.MANUAL, history=state.history[:1], shadow_since=None
    )
    with pytest.raises(DeploymentRecordConflictError, match="只增不改"):
        mode.save_mode_state(deployment_data_dir, rolled_back)
    # 改写既有历史条目 → 拒绝
    tampered = replace(
        state,
        history=({**state.history[0], "reason": "改写后的历史（应被拒绝）"}, *state.history[1:]),
    )
    with pytest.raises(DeploymentRecordConflictError, match="只增不改"):
        mode.save_mode_state(deployment_data_dir, tampered)
    assert json.loads(mode.mode_path(deployment_data_dir).read_text(encoding="utf-8"))[
        "history"
    ] == [dict(entry) for entry in state.history]


def test_mode_switch_event_files_are_append_only(deployment_data_dir, deployment_config):
    """切换事件只增不改：同刻同内容幂等，同刻不同内容拒绝覆盖。"""
    state = _open_shadow(deployment_data_dir, deployment_config)
    event = next((deployment_data_dir / "mode").glob("*-shadow.json"))
    original = event.read_text(encoding="utf-8")
    assert mode.save_mode_event(deployment_data_dir, state, at=_at()) == event
    assert event.read_text(encoding="utf-8") == original
    tampered = replace(state, history=({**state.history[-1], "reason": "被改写的切换理由"},))
    with pytest.raises(DeploymentRecordConflictError, match="只增不改"):
        mode.save_mode_event(deployment_data_dir, tampered, at=_at())
    assert event.read_text(encoding="utf-8") == original


def test_set_mode_requires_human_audit_fields(deployment_data_dir, deployment_config):
    """切换必须带人/理由（FR-004：模式变更留痕）。"""
    for kwargs in ({"by": "", "reason": "开影子"}, {"by": "ops", "reason": ""}):
        with pytest.raises(ValidationError):
            mode.set_mode(
                DeployMode.SHADOW,
                cfg=deployment_config,
                data_dir=deployment_data_dir,
                at=_at(),
                **kwargs,
            )


def test_unknown_mode_literal_is_rejected_before_any_write(deployment_data_dir, deployment_config):
    with pytest.raises(ValidationError, match="mode"):
        mode.set_mode(
            "automatic",
            by="ops",
            reason="非法模式",
            cfg=deployment_config,
            data_dir=deployment_data_dir,
            at=_at(),
        )
    assert not mode.mode_path(deployment_data_dir).exists()
