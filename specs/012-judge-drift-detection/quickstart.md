# Quickstart：judge 漂移自动检测（012-judge-drift-detection）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "drift"                    # 指标/状态机/门禁/报表
uv run pytest tests/contract -k drift                  # 检测/门禁/证据接口契约
uv run python ops/calibrate.py drift --period 2026-W39 # CLI 一轮检测 + 报表
uv run python ops/demo_judge_drift.py                  # 端到端演示
```

## 端到端场景（demo 流程）

1. **基线建立**：连续若干周期快照 → 滑动窗口基线（首周期 no_baseline 不告警）
2. **漂移检出**：注入均值平移/方差展宽/双峰化 → 100% `drift` + suspect 登记
3. **稳定不误报**：稳定序列 → `normal`
4. **分级处置**：suspect 降权 ×0.5（合成差异断言）；人工确认 → confirmed_drift 排除
5. **证据接口**：suspect/confirmed_drift → 部署证据查询拒绝 + 理由
6. **报表与联动**：周期报表 + 双信号强化告警（漂移 ∧ 信度下降）

## 里程碑验收（立项书 WS2 / SC-001）

注入漂移 ≥3 形态 100% 检出 + 稳定 0 误报 + 首周期基线 100% + 样本不足 100% 标注 +
系统自动写入仅 suspect（机检）+ 证据接口拒绝 100%；覆盖率 ≥85% 不降。
