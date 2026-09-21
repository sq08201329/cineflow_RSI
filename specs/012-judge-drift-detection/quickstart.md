# Quickstart：judge 漂移自动检测（012-judge-drift-detection）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "drift"                    # 指标/状态机/门禁/报表
uv run pytest tests/contract -k drift                  # 检测/门禁/证据接口契约
uv run pytest tests/unit -k "drift_wiring"             # 四处 loop 接线（合成前一行）
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

## 验证记录（2026-09-21，T1123）

| 命令 | 结果 |
| --- | --- |
| `uv sync` | Resolved 35 packages / Checked 30 packages（零新增依赖） |
| `uv run pytest tests/unit -k "drift"` | 204 passed, 1986 deselected |
| `uv run pytest tests/contract -k drift` | 14 passed, 194 deselected（C1~C8 全场景 + SC-002/003/005 机检 + CLI 三路径） |
| `uv run pytest tests/unit tests/contract` | 2342 passed, 56 skipped（跳过为真实适配器无凭证用例） |
| `uv run python ops/calibrate.py drift --period 2026-W39` | **退出码 0**（仓库无 010 快照 → `detected=[]`、`errors=[]`、空报表落盘并注明；验证后清理该空报表保持工作树干净） |
| `uv run python ops/demo_judge_drift.py` | **退出码 0**（六步全 ok，约 0.13s；报告 JSON 落 stdout，`ok=true`） |
| `uv run ruff check .` | All checks passed! |
| `uv run ruff format --check .` | 384 files already formatted |
| `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-report=term-missing --cov-fail-under=85` | 2190 passed；**TOTAL 93.55%**（≥85% 达标）；012 新模块 96%（`drift_config.py` 100% / `drift_stats.py` 99% / `drift_metrics.py` 98% / `drift_models.py` 98% / `drift_gate.py` 94% / `drift_report.py` 94% / `drift_status.py` 91%） |

**实测口径（demo 六步输出摘要）**

1. 基线建立：首周期 `2026-W34` → `no_baseline`（记基线不告警，`psi=None`）；
   `2026-W38`（窗口 `2026-W34..2026-W37`）→ `normal`，PSI = 0.0047 ≪ 0.2；
2. 漂移检出 3/3：均值平移（visual/judge.cinematic，PSI 13.364）/ 方差展宽
   （editing/judge.narrative_flow，PSI 2.538）/ 双峰化（storyboard/judge.script_fit，PSI 25.694）
   → 全部 `drift` + 自动登记 `suspect`（registry 三条suspect）；
3. 稳定不误报：screenplay/judge.dramatic_tension → `normal`，PSI = 0.0013（0 误报）；
4. 分级处置：`suspect` 降权 judge.cinematic 0.25 → 0.142857（其余分量归一，Σ 保持 1.0），
   合成分 0.650 → 0.714（judge 低分不再主导）；人工确认（`reanchor`，留痕"校准负责人"）
   → `confirmed_drift` 权重归零，合成分 0.800——三态互相可区分（SC-004）；
5. 证据接口：拒绝 3/3（`confirmed_drift` 1 + `suspect` 2，理由含状态与触发指标/处置留痕引用），
   未登记（默认 normal）允许——`suspect`/`confirmed_drift` 拒绝率 **100%**（SC-003）；
6. 报表与联动：010 信度 `kendall_tau=0.3 < target 0.6` ∧ 漂移 → **强化告警**
   `level=critical`（`double_signal=true`，信号 `["drift","reliability_below_target"]`），
   其余两条单信号告警为 `warning`；F6 ScoreConflict 附注 = "无持久化来源（F6 未落盘命中分布）"
   且 `participates_in_judgement=false`（不参与阈值判定）。

**状态机机检口径（SC-002）**：全部状态登记中，`suspect` 条目必带 `trigger_metrics`
且无人工留痕引用（系统写入）；其余状态（`false_alarm` / `normal` / `confirmed_drift`）
必带 `disposition_ref` 人工留痕引用；终态构造与迁移在模型层要求 `by_human=True` +
处置引用，系统路径无"直接写终态"通路（`tests/contract/test_drift_contracts.py::TestC3状态机`）。

**只读审计口径（SC-005）**：检测 + 登记 + 报表全流程中，010 产物（`snapshots/`、
`ledger/`、`reports/`）零写入、树节点数不变、`core/evaluators` 与 `configs` 指纹不变、
网关 `call_count == 0` 且 `total_cost_usd == 0`；新增文件仅限本特性自己的
`calibration/drift/**`（`tests/contract/test_drift_contracts.py::TestC8只读审计`）。

**附注口径注**：F6（011）当前只在内存产出 `HitDistribution`（含 `conflicts`），未落盘；
故报表 `residual_signals.score_conflict` 恒为 `available=false` + "无持久化来源"，**不伪造**
冲突数据（若 F6 后续按 `replay/hit_distributions/*.json` 约定落盘，报表自动读取并附注，
仍不参与阈值判定）。
