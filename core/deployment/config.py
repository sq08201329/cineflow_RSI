"""部署形态配置解析（configs/*.yaml → DeploymentConfig，功能 014 / T1406）。

- **配置即形态**（原则五）：模式默认值、门槛三要件的阈值、影子期双下限、渐进抽检策略
  全走 configs，core 零硬编码；
- 缺段/缺字段/非法值即报错（`DeploymentConfigError`）——**不允许静默用默认值**：
  门槛阈值悄悄变化会让"什么可自动接班"的定义漂移（原则一：口径即版本）；
- 禁止名单复用 009 的 `dreaming.no_auto_evolve_agents`（不另立名单）：缺名单即报错，
  空名单等于放行一切，必须显式声明（原则六：宁可拦截）；
- 独立模块（不并入既有 CalibrationConfig/DreamConfig）：014 段可独立解析与演进，
  风格对齐 core/calibration/drift_config.py。
"""

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.deployment.errors import DeploymentConfigError
from core.deployment.models import DeployMode

_MODE_VALUES = tuple(mode.value for mode in DeployMode)


def _require_section(path: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise DeploymentConfigError(f"{path} 必须为映射段，实际为 {value!r}")
    return value


def _require_key(path: str, section: dict, key: str) -> object:
    if key not in section:
        raise DeploymentConfigError(f"{path} 缺少配置项 {key!r}")
    return section[key]


def _require_bool(path: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise DeploymentConfigError(f"{path} 必须为布尔值，实际为 {value!r}")
    return value


def _require_int(path: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DeploymentConfigError(f"{path} 必须为 ≥ {minimum} 的整数，实际为 {value!r}")
    return value


def _require_ratio(path: str, value: object) -> float:
    """比例域校验：∈ (0,1]（0 会让门槛/抽检失去意义，>1 是配置错误）。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= 1
    ):
        raise DeploymentConfigError(f"{path} 必须 ∈ (0,1] 的数值，实际为 {value!r}")
    return float(value)


@dataclass(frozen=True)
class GateConfig:
    """证据门槛配置：validation 排名比例 + 无偏性前置开关 + 无 judge 的放行开关。"""

    validation_top_ratio: float
    require_unbiasedness: bool
    allow_without_judge: bool


@dataclass(frozen=True)
class ShadowConfig:
    """影子期双下限（时长 + 覆盖候选数）；显式 0 表示关闭该下限（须写进配置）。"""

    min_days: int
    min_candidates: int


@dataclass(frozen=True)
class SpotCheckConfig:
    """渐进抽检策略：前 `first_n` 次自动部署全量复核，之后按 `ratio` 比例抽检。"""

    first_n: int
    ratio: float


@dataclass(frozen=True)
class DeploymentConfig:
    """deployment 段配置（模式默认值 / 门槛 / 影子 / 抽检 / 禁止名单）。"""

    mode_default: DeployMode
    gate: GateConfig
    shadow: ShadowConfig
    spot_check: SpotCheckConfig
    forbidden_agents: tuple[str, ...]

    @classmethod
    def from_dict(cls, config: dict) -> "DeploymentConfig":
        if not isinstance(config, dict):
            raise DeploymentConfigError("形态配置必须为映射（含 deployment 段）")
        section = _require_section("deployment", config.get("deployment"))

        raw_mode = _require_key("deployment", section, "mode_default")
        if raw_mode not in _MODE_VALUES:
            raise DeploymentConfigError(
                f"deployment.mode_default 必须 ∈ {list(_MODE_VALUES)}，实际为 {raw_mode!r}"
            )

        gate = _require_section("deployment.gate", _require_key("deployment", section, "gate"))
        gate_cfg = GateConfig(
            validation_top_ratio=_require_ratio(
                "deployment.gate.validation_top_ratio",
                _require_key("deployment.gate", gate, "validation_top_ratio"),
            ),
            require_unbiasedness=_require_bool(
                "deployment.gate.require_unbiasedness",
                _require_key("deployment.gate", gate, "require_unbiasedness"),
            ),
            allow_without_judge=_require_bool(
                "deployment.gate.allow_without_judge",
                _require_key("deployment.gate", gate, "allow_without_judge"),
            ),
        )

        shadow = _require_section(
            "deployment.shadow", _require_key("deployment", section, "shadow")
        )
        shadow_cfg = ShadowConfig(
            min_days=_require_int(
                "deployment.shadow.min_days",
                _require_key("deployment.shadow", shadow, "min_days"),
                minimum=0,
            ),
            min_candidates=_require_int(
                "deployment.shadow.min_candidates",
                _require_key("deployment.shadow", shadow, "min_candidates"),
                minimum=0,
            ),
        )

        spot_check = _require_section(
            "deployment.spot_check", _require_key("deployment", section, "spot_check")
        )
        spot_cfg = SpotCheckConfig(
            first_n=_require_int(
                "deployment.spot_check.first_n",
                _require_key("deployment.spot_check", spot_check, "first_n"),
                minimum=0,
            ),
            ratio=_require_ratio(
                "deployment.spot_check.ratio",
                _require_key("deployment.spot_check", spot_check, "ratio"),
            ),
        )

        return cls(
            mode_default=DeployMode(raw_mode),
            gate=gate_cfg,
            shadow=shadow_cfg,
            spot_check=spot_cfg,
            forbidden_agents=_load_forbidden_agents(config),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DeploymentConfig":
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise DeploymentConfigError(f"形态配置文件不可读：{path}（{exc}）") from exc
        return cls.from_dict(data)

    def is_forbidden(self, agent_id: str) -> bool:
        """是否属禁止自动进化的 Agent（009 名单；判定优先级最高）。"""
        return agent_id in self.forbidden_agents

    def thresholds_snapshot(self) -> dict:
        """判定阈值快照（进证据包/快照，使一次判定的口径自描述、可事后复算）。"""
        return {
            "validation_top_ratio": self.gate.validation_top_ratio,
            "require_unbiasedness": self.gate.require_unbiasedness,
            "allow_without_judge": self.gate.allow_without_judge,
            "forbidden_agents": list(self.forbidden_agents),
        }


def _load_forbidden_agents(config: dict) -> tuple[str, ...]:
    """禁止名单取自 009 的 `dreaming.no_auto_evolve_agents`（无独立名单，杜绝口径分叉）。"""
    dreaming = config.get("dreaming")
    if not isinstance(dreaming, dict) or "no_auto_evolve_agents" not in dreaming:
        raise DeploymentConfigError(
            "形态配置缺少 dreaming.no_auto_evolve_agents（禁止自动进化的 Agent 名单，"
            "009 口径复用；空名单等于放行一切，必须显式声明）"
        )
    raw = dreaming["no_auto_evolve_agents"]
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(name, str) or not name for name in raw)
    ):
        raise DeploymentConfigError(
            "dreaming.no_auto_evolve_agents 必须为非空的非空字符串列表，"
            f"实际为 {raw!r}（空名单不可接受）"
        )
    return tuple(raw)
