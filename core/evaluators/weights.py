"""形态配置权重读取（configs/*.yaml → weights dict，FR-011）。

- 配置即形态：评估器组合与权重从 configs/*.yaml 读取，core 零硬编码（宪章原则五）；
- gate 语义：配置值 "gate" 转为 0.0 权重——硬规则不参与加权求和，
  门禁由 composite_score 的 rule. 前缀检查承担；
- 读出的 weights 应由调用方冻结进 DiscoveryTree.config_snapshot（快照随树冻结）。
"""

from pathlib import Path

import yaml

from core.evaluators.errors import WeightConfigError

_GATE_VALUE = "gate"


def load_evaluator_weights(config_path: str | Path, agent_id: str) -> dict[str, float]:
    """读取形态配置中指定 Agent 的 evaluator_weights，产出注入 composite_score 的权重。"""
    path = Path(config_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WeightConfigError(f"形态配置文件不可读：{path}（{exc}）") from exc

    if not isinstance(data, dict) or "evaluator_weights" not in data:
        raise WeightConfigError(f"{path}: 缺少 evaluator_weights 配置节")
    section = data["evaluator_weights"]
    if not isinstance(section, dict) or agent_id not in section:
        raise WeightConfigError(f"{path}: evaluator_weights 中缺少 {agent_id!r} 的权重配置")

    raw = section[agent_id]
    if not isinstance(raw, dict) or not raw:
        raise WeightConfigError(f"{path}: evaluator_weights.{agent_id} 必须为非空映射")

    weights: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise WeightConfigError(f"{path}: 权重键必须为非空字符串，实际为 {key!r}")
        if isinstance(value, str):
            if value.strip().lower() != _GATE_VALUE:
                raise WeightConfigError(
                    f"{path}: {key} 的权重值非法 {value!r}（仅允许数值或 'gate'）"
                )
            weights[key] = 0.0  # 硬规则门禁：不参与加权求和
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise WeightConfigError(f"{path}: {key} 的权重必须为 ≥ 0 的数值，实际为 {value!r}")
        weights[key] = float(value)
    return weights
