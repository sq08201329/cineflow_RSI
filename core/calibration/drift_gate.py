"""合成门禁与部署证据接口（功能 012 US2，契约 C4/C5；原则六：不自动停用、只降权/排除）。

- `gate_weights(weights, registry, cfg)`：**分级处置**（澄清 Q2）——
  `suspect` → 该分量权重 × `suspect_weight`（默认 0.5）；`confirmed_drift` → 权重归零
  （排除，`confirmed_exclude=false` 时退化为降权）；其余状态不变。
  降权/排除后按"可调分量"**重新归一**（Σ 保持原值）：改变的是该分量的影响力份额，
  而非把全部总分整体压低——否则"降权"会与"该轮探索整体变差"混淆；
- **权重键无版本、状态键带版本**：权重来自形态配置（`judge.cinematic`），状态登记为
  `judge.cinematic@1.0.0` → 按 `@` 前缀（evaluator_id）匹配；同一 evaluator_id 多版本
  并存时取**最严重**状态（confirmed_drift > suspect）；
- `deploy_evidence_verdict(evaluator_key, registry)`：F9 前置证据接口——`suspect` /
  `confirmed_drift` → 拒绝 + 理由（含状态与处置留痕引用）；`normal` / `false_alarm` →
  允许。接口先就位，部署流程不在本特性；
- 各 Agent loop 在**合成前**调用（`apply_gate` 一行）：权重变化 → composite 版本哈希
  变化 → 自然升版（原则一由既有哈希机制承载），历史节点不受影响（immutable）。

`registry` 允许两种形态：`DriftRegistry`（文件化登记）或 `Mapping[str, DriftStatus]`
（内存视图，测试与未来状态源）。`None` 视为"无任何登记"（权重原样返回）。
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from core.calibration.drift_config import DriftConfig
from core.calibration.drift_models import (
    DeployEvidenceVerdict,
    DriftState,
    DriftStatus,
)
from core.calibration.drift_status import DriftRegistry
from core.evaluators.errors import ValidationError

# 状态严重度（多版本并存取最严重者；normal 与 false_alarm 同为零级——后者已被人工判非漂移）
_SEVERITY = {
    DriftState.NORMAL: 0,
    DriftState.FALSE_ALARM: 0,
    DriftState.SUSPECT: 2,
    DriftState.CONFIRMED_DRIFT: 3,
}

RegistryLike = DriftRegistry | Mapping[str, DriftStatus] | None


def _statuses(registry: RegistryLike) -> Mapping[str, DriftStatus]:
    if registry is None:
        return {}
    if isinstance(registry, DriftRegistry):
        return registry.current()
    if isinstance(registry, Mapping):
        return registry
    raise ValidationError(
        "registry 必须为 DriftRegistry 或 {evaluator_key: DriftStatus} 映射，"
        f"实际为 {type(registry).__name__}"
    )


def status_for(registry: RegistryLike, evaluator_id: str) -> DriftStatus | None:
    """按 evaluator_id（裸权重键）匹配当前状态；多版本取最严重者；无登记 → None。"""
    candidates = [
        status
        for key, status in _statuses(registry).items()
        if key.partition("@")[0] == evaluator_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda status: _SEVERITY[status.status])


def component_factor(registry: RegistryLike, evaluator_id: str, cfg: DriftConfig) -> float:
    """分量权重系数（分级处置）：normal/false_alarm → 1.0；suspect → suspect_weight；
    confirmed_drift → 0.0（`confirmed_exclude=false` 时退化为 suspect_weight）。"""
    status = status_for(registry, evaluator_id)
    if status is None or status.status in (DriftState.NORMAL, DriftState.FALSE_ALARM):
        return 1.0
    if status.status is DriftState.SUSPECT:
        return cfg.suspect_weight
    return 0.0 if cfg.confirmed_exclude else cfg.suspect_weight


def _is_gate_literal(value: object) -> bool:
    """权重值为 "gate" 字面量（硬规则门禁）——不参与漂移降权与归一。"""
    return isinstance(value, str) and value.strip().lower() == "gate"


def gate_weights(weights: Mapping[str, float], registry: RegistryLike, cfg: DriftConfig) -> dict:
    """按漂移状态调整合成权重（合成前调用；返回新映射，不改写入参）。

    - 仅"可调分量"（数值权重键）参与处置与重新归一；`gate` 字面量原样保留；
    - 无任何处置生效时**逐字段原样返回**（normal 路径零扰动，便于逐字节回归）；
    - 全部可调分量被归零 → 返回全 0（合成 0 分，不伪造剩余权重）。
    """
    if not weights:
        return {}
    gated: dict = dict(weights)
    changed = False
    for key, value in weights.items():
        if _is_gate_literal(value):
            continue
        factor = component_factor(registry, key, cfg)
        if factor == 1.0:
            continue
        gated[key] = float(value) * factor
        changed = True
    if not changed:
        return gated

    before = sum(float(value) for key, value in weights.items() if not _is_gate_literal(value))
    after = sum(float(value) for key, value in gated.items() if not _is_gate_literal(value))
    if after <= 0 or before <= 0:
        return {key: (value if _is_gate_literal(value) else 0.0) for key, value in gated.items()}
    scale = before / after
    for key, value in gated.items():
        if not _is_gate_literal(value):
            gated[key] = float(value) * scale
    return gated


def deploy_evidence_verdict(evaluator_key: str, registry: RegistryLike) -> DeployEvidenceVerdict:
    """F9 前置证据接口：该评估器版本能否进入自动部署证据（C5）。

    - `suspect` / `confirmed_drift` → 拒绝 + 理由（含状态与触发指标/处置留痕引用）；
    - `normal` / `false_alarm`（含无登记）→ 允许。
    """
    evaluator_id, _, version = evaluator_key.partition("@")
    if not evaluator_id or not version:
        raise ValidationError(
            f"evaluator_key 必须为 evaluator_id@version 形态，实际为 {evaluator_key!r}"
        )
    statuses = _statuses(registry)
    status = statuses.get(evaluator_key)
    if status is None:
        return DeployEvidenceVerdict(
            evaluator_key=evaluator_key,
            allow=True,
            reason=f"状态 normal（无漂移登记），版本 {version} 可进入自动部署证据",
        )
    if status.status is DriftState.SUSPECT:
        return DeployEvidenceVerdict(
            evaluator_key=evaluator_key,
            allow=False,
            reason=(
                f"状态 suspect（漂移未处置）：触发指标 {status.trigger_metrics}；"
                "须经人工处置（确认漂移或判为误报）后方可进入自动部署证据"
            ),
        )
    if status.status is DriftState.CONFIRMED_DRIFT:
        return DeployEvidenceVerdict(
            evaluator_key=evaluator_key,
            allow=False,
            reason=(
                f"状态 confirmed_drift（人工确认漂移）：处置留痕 {status.disposition_ref}；"
                "该版本已停用/换锚点升版，不得进入自动部署证据"
            ),
        )
    return DeployEvidenceVerdict(
        evaluator_key=evaluator_key,
        allow=True,
        reason=f"状态 {status.status.value}（已由人工处置为非漂移），可进入自动部署证据",
    )


@dataclass(frozen=True)
class DriftGate:
    """漂移门禁装配（cfg + 状态登记视图）：各 loop 合成前调用 `apply_weights`。"""

    cfg: DriftConfig
    registry: RegistryLike = None

    @classmethod
    def load(cls, data_dir: str | Path, cfg: DriftConfig) -> "DriftGate":
        """从数据目录装配（读 drift/status/ 全部当前态；无目录 → 空登记）。"""
        return cls(cfg=cfg, registry=DriftRegistry.load(data_dir))

    @classmethod
    def from_config_path(cls, data_dir: str | Path, config_path: str | Path) -> "DriftGate":
        """便捷装配：从 configs 读 calibration.drift 段 + 数据目录读状态。"""
        return cls.load(data_dir, DriftConfig.from_yaml(config_path))

    def apply_weights(self, weights: Mapping[str, float]) -> dict:
        """合成前一行接线：返回处置后权重（无处置 → 原样）。"""
        return gate_weights(weights, self.registry, self.cfg)

    def deploy_evidence_verdict(self, evaluator_key: str) -> DeployEvidenceVerdict:
        return deploy_evidence_verdict(evaluator_key, self.registry)

    def with_registry(self, registry: RegistryLike) -> "DriftGate":
        """同口径换登记视图（测试/多源装配用）。"""
        return replace(self, registry=registry)


def apply_gate(weights: Mapping[str, float], gate: DriftGate | None) -> dict:
    """接线点便捷函数：gate 为 None（未接线）→ 权重原样返回。

    各 Agent loop 合成前调用一行：`weights = apply_gate(weights, drift_gate)`。
    """
    if gate is None:
        return dict(weights)
    return gate.apply_weights(weights)
