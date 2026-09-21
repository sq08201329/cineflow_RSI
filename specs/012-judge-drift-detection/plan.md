# 实现计划：judge 漂移自动检测

**分支**: `012-judge-drift-detection` | **日期**: 2026-09-21 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/012-judge-drift-detection/spec.md` 的功能规格说明（含 2026-09-21 澄清会话三条决议）

## 概要

交付 `core/calibration/` 的漂移监控扩展：漂移检测器（滑动窗口基线 + PSI 分布距离 +
分位数偏移双维指标，口径版本化）、状态机与人工处置留痕（系统只写 `suspect`）、合成
门禁（分级处置：suspect 降权 0.5 / confirmed_drift 排除；权重变化自然升版）、部署证据
接口（F9 前置拒绝语义）、周期报表与 010 信度双信号联动。**数据源零新增**（只读 010
产物），检测全程只读。**零新增第三方依赖，零新 DB 表**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——math（PSI 对数）、json/blake3（口径版本与留痕哈希）；
010 的 `core/calibration`（快照/台账/信度读取）；各 Agent loop 的合成前接线点（现成）

**存储**: 无新 DB 表；漂移产物文件化 `calibration/drift/{metrics|status|dispositions|reports}/`
（git 版本化，只增不改）

**测试**: pytest；已知漂移形态注入（均值平移/方差展宽/双峰化）与稳定序列双向断言；
状态机写入权限机检；合成门禁三态对比；证据接口拒绝断言

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/calibration/` 扩展（业务无关监控机制）+ 各 Agent loop 的一行接线

**性能目标**: 一轮检测（≤10 评估器 × ≤10 周期快照）< 1 秒（纯统计计算）

**约束**: 系统只写 `suspect`（原则六机检）；处置人工留痕不可改写；只读纪律（SC-005
审计）；口径版本化（历史判定不回溯）；窗口/阈值/范围全配置化（原则五）；覆盖率 ≥85%

**规模/范围**: 4 个 core 扩展模块 + CLI 子命令 + 五处 loop 接线 + demo；不含自动停用
评估器、F9 部署流程（接口预留）、非 judge 类默认判定（配置可开）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | 检测口径版本化（detector_version 进记录）；评估器升版独立基线；权重变化自然升版 | ✅ 满足 |
| 原则二：不可变与谱系 | 检测只读（树零写入）；处置留痕与状态历史只增不改 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 检测零生成/零 LLM（审计断言）；数据源是既有产物 | ✅ 满足 |
| 原则四：沙箱与前缀 | 不涉及策略执行；无新信息面 | ✅ 满足 |
| 原则五：单向依赖与配置化 | core/calibration 扩展（业务无关）；窗口/阈值/范围/联动全 configs | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | **本特性核心**：系统只写 suspect（处置权在人）；漂移只判分布不判原因；双信号强化告警；部署证据拒绝 | ✅ 满足 |
| 测试纪律 | TDD；双向断言（检出/不误报）；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/012-judge-drift-detection/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── drift-detect.md    # 检测与指标口径（C1~C2）
│   ├── drift-gate.md      # 状态机/门禁/处置/证据接口（C3~C5）
│   └── drift-report.md    # 报表与双信号联动（C6~C8）
└── tasks.md               # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/calibration/
├── drift_metrics.py     # PSI + 分位数偏移 + 滑动窗口基线 + 判定（口径版本化）
├── drift_status.py      # 状态机（normal→suspect→confirmed_drift|false_alarm）+ 处置留痕
├── drift_gate.py        # gate_weights（分级处置）+ deploy_evidence_verdict（F9 前置）
└── drift_report.py      # 周期报表 + 010 信度双信号联动
agents/{visual,editing,storyboard,screenplay}/loop.py  # 合成前 gate_weights 接线（各一行）
configs/movie.yaml       # calibration.drift 段（window=5/buckets=10/psi_threshold=0.2/
                         # quantile_threshold/min_samples/suspect_weight=0.5/scope_kinds=[judge]/
                         # double_signal 规则）
calibration/drift/       # 数据目录（metrics/status/dispositions/reports，git 版本化）
ops/calibrate.py         # 新增 drift 子命令（检测 + 报表 + 处置入口）
ops/demo_judge_drift.py  # 端到端演示
tests/unit/test_drift_*.py；tests/contract/test_drift_*.py
```

**结构决策**: 漂移监控是 010 校准体系的同包延伸（数据源同源、节奏同步）；合成门禁
的接线点收敛为"合成前一处"（各 loop 一行 + gate_weights 统一实现）；检测口径版本化
沿用"版本号即元信息"惯例（detector_version 进每条记录）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 存放 = core/calibration 扩展（与 010 同包同源）
2. 指标 = PSI（主）+ 分位数偏移（辅），任一超阈即漂移（规格双维）
3. 滑动窗口基线（澄清 Q1），缺口跳过不插值，升版切分基线
4. 状态机系统只写 suspect；处置留痕不可改写
5. 合成门禁接线点 = 合成前一处；权重变化自然升版（原则一自动承载）
6. 部署证据接口先就位（F9 前置拒绝语义）
7. 双信号联动 = 漂移 ∧ 010 信度低于 target（配置规则）
8. 口径版本化 + 全程只读（审计断言）
9. ops/calibrate.py 新增 drift 子命令（close → drift → report 节奏）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：DriftBaseline/DriftMetrics/DriftStatus/DriftDisposition/
  DriftReport/DeployEvidenceVerdict 与文件 schema、状态机
- [contracts/drift-detect.md](contracts/drift-detect.md)（C1~C2）、
  [drift-gate.md](contracts/drift-gate.md)（C3~C5）、
  [drift-report.md](contracts/drift-report.md)（C6~C8）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

原则六的三重落点（系统只写 suspect / 处置人工留痕 / 证据接口拒绝）均有契约测试承载；
口径版本化与升版切分基线对齐原则一；只读纪律与数据源零新增对齐原则二/三。**无新增
违规，门禁通过。**
