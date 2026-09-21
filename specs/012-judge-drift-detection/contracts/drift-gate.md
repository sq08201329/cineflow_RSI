# 契约：漂移状态机、合成门禁与人工处置

> 对应规格 US2 / FR-004~006、FR-011。实现：`core/calibration/drift_status.py`、`drift_gate.py`。

## C3 状态机（系统只写 suspect）

```
register_suspect(evaluator_key, metrics) -> DriftStatus      # 超阈判定 → suspect（系统自动）
dispose(evaluator_key, conclusion, by, reason, action) -> DriftDisposition  # 人工
```

- `normal → suspect`：超阈判定写入（trigger_metrics 引用）
- `suspect → confirmed_drift | false_alarm`：仅人工 `dispose()`；留痕不可改写
- `false_alarm → normal`（处置动作 = 恢复，`dispose` 的 action="restore"）；`confirmed_drift` 的版本不再参与合成
- 机检：系统写入仅限 `suspect`（SC-002）

### 场景

1. 超阈 → suspect 登记（trigger_metrics 引用）
2. 人工确认漂移 → confirmed_drift + 留痕（停用/换锚点升版）
3. 人工误报 → false_alarm + 留痕 → 恢复 normal
4. 系统尝试直接写终态 → 拒绝（机检）

## C4 合成门禁（分级处置，澄清 Q2）

```
gate_weights(weights, registry, cfg) -> dict[str, float]
```

- `suspect`：judge 分量权重 × `suspect_weight`（默认 0.5）；`confirmed_drift`：权重归零（排除）
- 各 Agent loop 在合成前调用；权重变化 → composite 版本哈希变化 → 自然升版
- 效果可区分（前/中/后三态对比断言，SC-004）

### 场景

1. normal → 权重不变
2. suspect → judge 权重 ×0.5（合成结果与正常态差异断言）
3. confirmed_drift → 权重归零（judge 不参与合成）

## C5 部署证据接口（F9 前置）

```
deploy_evidence_verdict(evaluator_key, registry) -> DeployEvidenceVerdict
```

- `suspect`/`confirmed_drift` → `allow=false` + 理由（含状态与处置引用）
- `normal`/`false_alarm` → `allow=true`
- 接口先就位（F9 未交付）；机检 SC-003

### 场景

1. suspect/confirmed_drift → 拒绝 + 理由
2. normal/false_alarm → 允许
