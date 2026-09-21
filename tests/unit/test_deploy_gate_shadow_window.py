"""影子期门禁单测（功能 014 US2 / T1414，先于实现编写；契约 C4 场景 3~4 + FR-006/FR-009）。

与 `test_deploy_mode.py`（迁移合法性/区间累计）互补：此处**逐维隔离**门禁判据并机检
"拒绝零副作用"：

- 仅时长不足 → 拒绝（缺口只说时长，不误报候选数）；
- 仅候选数不足 → 拒绝（缺口只说候选数）；
- 双下限满足 → 允许（进行中的影子区间在判定时点结算，不切出也算数）；
- `recalibration_required` 时即使双下限满足 → 拒绝（FR-009）；
- 清重标定标记 → 影子期重新计时（须重跑影子验证，不因清标记而跳过影子）；
- 任何拒绝：`mode.json` 逐字节不变、切换事件零新增（零副作用）。
"""

from dataclasses import replace

import pytest

from core.deployment import mode
from core.deployment.config import ShadowConfig
from core.deployment.errors import ModeTransitionError
from core.deployment.models import DeployMode

T0 = "2026-09-21T00:00:00+00:00"
DAY = 24 * 3600


def _at(days: float) -> str:
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 9, 21, tzinfo=UTC)
    return (base + timedelta(days=days)).isoformat()


def _with_limits(deployment_config, *, min_days: int, min_candidates: int):
    return replace(
        deployment_config, shadow=ShadowConfig(min_days=min_days, min_candidates=min_candidates)
    )


def _open_shadow(data_dir, cfg, *, days: float = 0.0):
    return mode.set_mode(
        "shadow", by="ops", reason="开影子期", cfg=cfg, data_dir=data_dir, at=_at(days)
    )


def _fill(data_dir, *, candidates: int):
    for _ in range(candidates):
        mode.record_shadow_candidate(data_dir, at=_at(0.5))


def _try_auto(data_dir, cfg, *, days: float):
    return mode.set_mode(
        "auto", by="ops", reason="申请开自动", cfg=cfg, data_dir=data_dir, at=_at(days)
    )


def _fingerprint(data_dir):
    """零副作用机检口径：mode.json 字节 + mode/ 事件文件清单。"""
    path = mode.mode_path(data_dir)
    state = path.read_bytes() if path.is_file() else None
    events = sorted(item.name for item in (data_dir / "mode").glob("*.json"))
    return state, events


def test_window_rejects_when_only_duration_short(deployment_data_dir, deployment_config):
    """仅时长不足 → 拒绝且缺口只提时长（候选数已达下限，不误报）。"""
    cfg = _with_limits(deployment_config, min_days=14, min_candidates=2)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=2)
    with pytest.raises(ModeTransitionError) as excinfo:
        _try_auto(deployment_data_dir, cfg, days=1)
    message = str(excinfo.value)
    assert "14" in message and "时长" in message
    assert "候选" not in message.split("时长")[0]
    assert mode.load_mode_state(deployment_data_dir).current is DeployMode.SHADOW


def test_window_rejects_when_only_candidate_count_short(deployment_data_dir, deployment_config):
    """仅候选数不足 → 拒绝且缺口只提候选数（时长已够，不误报）。"""
    cfg = _with_limits(deployment_config, min_days=1, min_candidates=20)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=3)
    with pytest.raises(ModeTransitionError) as excinfo:
        _try_auto(deployment_data_dir, cfg, days=2)
    message = str(excinfo.value)
    assert "20" in message and "候选" in message
    assert "时长" not in message


def test_window_allows_auto_only_when_both_limits_met(deployment_data_dir, deployment_config):
    """双下限满足 → 才允许 auto；进行中的影子区间在判定时点结算。"""
    cfg = _with_limits(deployment_config, min_days=2, min_candidates=5)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=4)
    with pytest.raises(ModeTransitionError):
        _try_auto(deployment_data_dir, cfg, days=2)  # 时长够、候选差 1 个
    _fill(deployment_data_dir, candidates=1)
    state = _try_auto(deployment_data_dir, cfg, days=2)
    assert state.current is DeployMode.AUTO
    assert state.shadow_days_accumulated == pytest.approx(2.0)
    assert state.shadow_candidate_count == 5


def test_recalibration_blocks_auto_even_when_window_met(deployment_data_dir, deployment_config):
    """FR-009：重标定期间即使影子期满足，auto 一律拒绝（标记不被顺手清除）。"""
    cfg = _with_limits(deployment_config, min_days=1, min_candidates=1)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=1)
    mode.set_recalibration(
        deployment_data_dir, required=True, reason="抽检否决：门槛需重新标定", at=_at(1)
    )
    with pytest.raises(ModeTransitionError, match="重标定"):
        _try_auto(deployment_data_dir, cfg, days=2)
    state = mode.load_mode_state(deployment_data_dir)
    assert state.current is DeployMode.SHADOW
    assert state.recalibration_required is True


def test_cleared_recalibration_still_requires_shadow_window(deployment_data_dir, deployment_config):
    """清标记 ≠ 免影子：重新计时后仍须跑够双下限（C4 场景 3 + FR-009）。"""
    cfg = _with_limits(deployment_config, min_days=2, min_candidates=5)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=5)
    mode.set_recalibration(deployment_data_dir, required=True, reason="门槛待重标定", at=_at(1))
    cleared = mode.clear_recalibration(
        deployment_data_dir, by="ops", reason="门槛已重标定（阈值 v2）", at=_at(2)
    )
    assert cleared.shadow_days_accumulated == 0.0
    assert cleared.shadow_candidate_count == 0
    assert cleared.current is DeployMode.SHADOW  # 仍在影子期，但计时已清零
    with pytest.raises(ModeTransitionError, match="影子期"):
        _try_auto(deployment_data_dir, cfg, days=3)


def test_every_rejection_leaves_mode_state_untouched(deployment_data_dir, deployment_config):
    """拒绝零副作用：mode.json 逐字节不变、切换事件零新增（宁可留在影子，不带病迁移）。"""
    cfg = _with_limits(deployment_config, min_days=14, min_candidates=20)
    _open_shadow(deployment_data_dir, cfg)
    _fill(deployment_data_dir, candidates=1)
    before = _fingerprint(deployment_data_dir)
    for _ in range(3):
        with pytest.raises(ModeTransitionError):
            _try_auto(deployment_data_dir, cfg, days=1)
    assert _fingerprint(deployment_data_dir) == before
    assert mode.load_mode_state(deployment_data_dir).shadow_since == T0


def test_manual_to_auto_is_rejected_but_shadow_to_auto_is_not_limited_by_window(
    deployment_data_dir, deployment_config
):
    """两条拒绝理由互不遮蔽：manual→auto 报直连；shadow→auto 才报影子期缺口。"""
    cfg = _with_limits(deployment_config, min_days=0, min_candidates=0)
    with pytest.raises(ModeTransitionError, match="manual"):
        _try_auto(deployment_data_dir, cfg, days=0)
    _open_shadow(deployment_data_dir, cfg)
    assert _try_auto(deployment_data_dir, cfg, days=DAY / DAY).current is DeployMode.AUTO
