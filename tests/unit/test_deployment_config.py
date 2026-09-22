"""部署形态配置解析单测（功能 014 阶段 2 / T1405，先于实现编写；data-model 配置段 + FR-012）。

覆盖：
- `configs/movie.yaml` 的 deployment 段（mode_default/gate/shadow/spot_check）真实解析；
- 缺段/缺字段即报错（**不允许静默用默认值**——阈值悄悄变化会让判定口径漂移）；
- 阈值域校验（比例 ∈ (0,1]、计数 ≥ 0、布尔项必须为布尔）；
- 禁止名单复用 009 的 `dreaming.no_auto_evolve_agents`：缺名单即报错（空名单等于放行一切）。

口径来源：宪章原则五（阈值/影子下限/抽检策略全配置化）+ 原则六（默认保守）。
"""

from pathlib import Path

import pytest
import yaml

from core.deployment.config import (
    DeploymentConfig,
    GateConfig,
    ShadowConfig,
    SpotCheckConfig,
)
from core.deployment.errors import DeploymentConfigError
from core.deployment.models import DeployMode

REPO_ROOT = Path(__file__).resolve().parents[2]


def base_payload():
    return {
        "deployment": {
            "mode_default": "manual",
            "gate": {
                "validation_top_ratio": 0.2,
                "require_unbiasedness": True,
                "allow_without_judge": False,
            },
            "shadow": {"min_days": 14, "min_candidates": 20},
            "spot_check": {"first_n": 5, "ratio": 0.2, "pending_alert_days": 7},
        },
        "dreaming": {"no_auto_evolve_agents": ["screenplay", "dev"]},
    }


def test_real_config_parses_with_contract_defaults(deployment_config):
    """真实配置（configs/movie.yaml）逐字段对齐 data-model 的默认档。"""
    assert deployment_config.mode_default is DeployMode.MANUAL
    assert deployment_config.gate == GateConfig(
        validation_top_ratio=0.2, require_unbiasedness=True, allow_without_judge=False
    )
    assert deployment_config.shadow == ShadowConfig(min_days=14, min_candidates=20)
    assert deployment_config.spot_check == SpotCheckConfig(
        first_n=5, ratio=0.2, pending_alert_days=7
    )
    assert deployment_config.forbidden_agents == ("screenplay", "dev")
    assert deployment_config.is_forbidden("screenplay") is True
    assert deployment_config.is_forbidden("visual") is False


def test_from_yaml_reads_the_same_section_as_dict():
    path = REPO_ROOT / "configs" / "movie.yaml"
    from_yaml = DeploymentConfig.from_yaml(path)
    from_dict = DeploymentConfig.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert from_yaml == from_dict


def test_missing_sections_and_keys_are_rejected_with_path():
    """缺段/缺字段 → 报错并指明路径（不静默取默认值）。"""
    with pytest.raises(DeploymentConfigError, match="deployment"):
        DeploymentConfig.from_dict({"dreaming": {"no_auto_evolve_agents": ["screenplay"]}})

    for section, key in (
        ("gate", "validation_top_ratio"),
        ("gate", "require_unbiasedness"),
        ("gate", "allow_without_judge"),
        ("shadow", "min_days"),
        ("shadow", "min_candidates"),
        ("spot_check", "first_n"),
        ("spot_check", "ratio"),
        ("spot_check", "pending_alert_days"),
    ):
        payload = base_payload()
        del payload["deployment"][section][key]
        with pytest.raises(DeploymentConfigError, match=key):
            DeploymentConfig.from_dict(payload)

    for key in ("mode_default", "gate", "shadow", "spot_check"):
        payload = base_payload()
        del payload["deployment"][key]
        with pytest.raises(DeploymentConfigError, match=key):
            DeploymentConfig.from_dict(payload)


def test_forbidden_agents_come_from_009_list_and_may_not_be_empty():
    """禁止名单复用 009 配置：缺名单/空名单/空串一律报错（空名单 = 放行一切）。"""
    with pytest.raises(DeploymentConfigError, match="no_auto_evolve_agents"):
        DeploymentConfig.from_dict(
            {
                "deployment": base_payload()["deployment"],
            }
        )
    for bad in ([], ["screenplay", ""]):
        payload = base_payload()
        payload["dreaming"]["no_auto_evolve_agents"] = bad
        with pytest.raises(DeploymentConfigError, match="no_auto_evolve_agents"):
            DeploymentConfig.from_dict(payload)


def test_mode_default_must_be_a_known_mode():
    payload = base_payload()
    payload["deployment"]["mode_default"] = "automatic"
    with pytest.raises(DeploymentConfigError, match="mode_default"):
        DeploymentConfig.from_dict(payload)


def test_gate_threshold_domain_is_validated():
    """validation_top_ratio ∈ (0,1]；布尔项必须为布尔（1/True 混用即拒）。"""
    for bad in (0, 0.0, 1.5, -0.2, "0.2", True):
        payload = base_payload()
        payload["deployment"]["gate"]["validation_top_ratio"] = bad
        with pytest.raises(DeploymentConfigError, match="validation_top_ratio"):
            DeploymentConfig.from_dict(payload)
    for key in ("require_unbiasedness", "allow_without_judge"):
        payload = base_payload()
        payload["deployment"]["gate"][key] = "true"
        with pytest.raises(DeploymentConfigError, match=key):
            DeploymentConfig.from_dict(payload)


def test_shadow_limits_must_be_non_negative_ints():
    for key in ("min_days", "min_candidates"):
        for bad in (-1, 1.5, "14", True):
            payload = base_payload()
            payload["deployment"]["shadow"][key] = bad
            with pytest.raises(DeploymentConfigError, match=key):
                DeploymentConfig.from_dict(payload)


def test_spot_check_policy_domain_is_validated():
    """渐进抽检策略：first_n ≥ 0（0 = 不设全量档）、ratio ∈ (0,1]、超期告警阈值 ≥ 0。"""
    for bad in (-1, 2.5, "5", True):
        payload = base_payload()
        payload["deployment"]["spot_check"]["first_n"] = bad
        with pytest.raises(DeploymentConfigError, match="first_n"):
            DeploymentConfig.from_dict(payload)
    for bad in (0, 1.5, -0.1, "0.2"):
        payload = base_payload()
        payload["deployment"]["spot_check"]["ratio"] = bad
        with pytest.raises(DeploymentConfigError, match="ratio"):
            DeploymentConfig.from_dict(payload)
    for bad in (-1, 1.5, "7", True):
        payload = base_payload()
        payload["deployment"]["spot_check"]["pending_alert_days"] = bad
        with pytest.raises(DeploymentConfigError, match="pending_alert_days"):
            DeploymentConfig.from_dict(payload)


def test_zero_shadow_limits_are_explicitly_allowed():
    """显式声明 0 下限合法（运营可关掉时限门禁）——但必须写进配置，不是隐式默认。"""
    payload = base_payload()
    payload["deployment"]["shadow"] = {"min_days": 0, "min_candidates": 0}
    payload["deployment"]["spot_check"] = {
        "first_n": 0,
        "ratio": 1.0,
        "pending_alert_days": 0,  # 0 = 有任何待复核任务即告警（显式声明，非隐式默认）
    }
    cfg = DeploymentConfig.from_dict(payload)
    assert cfg.shadow == ShadowConfig(min_days=0, min_candidates=0)
    assert cfg.spot_check == SpotCheckConfig(first_n=0, ratio=1.0, pending_alert_days=0)


def test_两套形态的告警阈值各自声明且短剧更短():
    """运营节奏即形态：movie 7 天（周节奏），shortdrama 2 天（投放节奏密集，复核窗口更短）。"""
    movie = DeploymentConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
    shortdrama = DeploymentConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
    assert movie.spot_check.pending_alert_days == 7
    assert shortdrama.spot_check.pending_alert_days == 2
    assert shortdrama.spot_check.pending_alert_days < movie.spot_check.pending_alert_days


def test_unreadable_config_path_is_reported(tmp_path):
    with pytest.raises(DeploymentConfigError, match="不可读"):
        DeploymentConfig.from_yaml(tmp_path / "missing.yaml")


def test_config_serialises_thresholds_for_audit(deployment_config):
    """判定阈值快照（进证据包/快照，使判定口径自描述；阈值变化即历史可解释）。"""
    snapshot = deployment_config.thresholds_snapshot()
    assert snapshot["validation_top_ratio"] == 0.2
    assert snapshot["require_unbiasedness"] is True
    assert snapshot["allow_without_judge"] is False
    assert snapshot["forbidden_agents"] == ["screenplay", "dev"]
