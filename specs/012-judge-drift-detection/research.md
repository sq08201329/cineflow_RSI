# 阶段 0 调研：judge 漂移自动检测（012-judge-drift-detection）

> 每个决策 = 分歧点 → 备选 → 结论 → 依据。规格：[spec.md](spec.md)（含 2026-09-21 澄清三条）。

## 决策 1：存放位置——core/calibration 扩展（010 同包）

- **结论**：漂移检测属评估器健康监控（业务无关），落 `core/calibration/` 扩展四个模块：
  `drift_metrics.py`（指标）、`drift_status.py`（状态机与处置留痕）、
  `drift_gate.py`（合成门禁 + 部署证据接口）、`drift_report.py`（报表与联动）
- **依据**：与 010 数据源（快照/台账/信度）同包同源；原则五（业务无关入 core）。

## 决策 2：分布距离算法——PSI（Population Stability Index）+ 分位数偏移

- **备选**：(a) PSI（分桶分布稳定性指数）；(b) JS 散度；(c) K-S 检验
- **结论**：**(a) PSI**（主指标）+ 分位数偏移（辅指标，p25/p50/p75/p90 位移向量）
- **依据**：PSI 是分布漂移监控的行业标准口径（分桶离散、对小样本稳健、阈值经验值
  成熟：<0.1 稳定 / 0.1~0.2 关注 / >0.2 显著）；010 的快照已是 [0,1] 十等分分桶，
  PSI 天然适配；JS/K-S 对小样本噪声更敏感。判定 = `PSI > psi_threshold` **或**
  `max|分位数偏移| > quantile_threshold`（两维任一超阈即漂移——规格 FR-002 双维）。

## 决策 3：滑动窗口基线（澄清 Q1 落地）

- **结论**：基线 = 最近 `window`（默认 5）周期的分桶合并分布（不含当前周期）；
  窗口内样本不足（周期数 < window 且总样本 < min_samples）→ "样本不足"如实标注；
  缺口周期跳过（不插值）；升版切分基线（版本冻结）
- **依据**：澄清决议；`window`/`min_samples` 全配置。

## 决策 4：状态机与"系统只写 suspect"

- **结论**：`normal → suspect → confirmed_drift | false_alarm`；系统的全部自动写入
  仅限 `suspect`；终态由 `dispose()`（人工处置）写入，留痕（人/时间/理由/动作）
  不可改写；状态登记为文件化 registry（`calibration/drift/status/{evaluator_key}.json`，
  只增不改的历史 + 当前态）
- **依据**：原则六可机检（SC-002：系统自动写入仅 suspect）。

## 决策 5：合成门禁的接线点与版本语义

- **分歧**：降权/排除在哪个环节生效？
- **结论**：`drift_gate.gate_weights(weights, registry, cfg)` 在**合成前**调整权重
  （suspect × suspect_weight；confirmed_drift 权重归零）；各 Agent 的 loop 在合成前调用
  （五处一行接线）；**权重变化 → composite 版本哈希变化 → 自然升版**（原则一自动成立）；
  历史节点不受影响（immutable）
- **依据**：原则一（口径变化即版本变化，哈希机制自动承载）；接线点最小（合成前一处）；
  检测本身只读（不写树、不改评估器、零调用——SC-005）。

## 决策 6：部署证据接口（F9 前置）

- **结论**：`drift_gate.deploy_evidence_verdict(evaluator_key, registry) -> {allow, reason}`；
  `suspect`/`confirmed_drift` → 拒绝 + 理由；接口先就位（F9 未交付，但其证据门槛
  依赖本状态——立项书 F9 前置）
- **依据**：规格 FR-005；F9 立项书"F7 就位"的前置条件。

## 决策 7：报表与双信号联动

- **结论**：`drift_report.build_report(period, cfg)` 读检测记录 + 010 信度报告；
  双信号规则（配置）：漂移告警 ∧ 信度低于 target → 强化告警（级别升级 + "双信号"
  标注）；单信号常规告警；报表 JSON 落 `calibration/drift/reports/{period}.json`
- **依据**：规格 FR-007；010 信度报告已含 per 评估器相关系数与达标标记（数据源现成）。

## 决策 8：口径版本化与只读纪律

- **结论**：检测口径（算法 + 阈值集）携带版本号（`drift_detector@1.0.0+{哈希}`）进
  每条检测记录；口径变更不回溯改写历史判定；检测全程只读（审计断言：树零写入、
  评估器零变更、零生成/零 LLM）
- **依据**：规格 FR-010（口径版本化）+ FR-008（只读）。

## 决策 9：010 接入点（ops/calibrate.py）

- **结论**：`ops/calibrate.py` 新增 `drift` 子命令（检测 + 报表 + 处置入口）；
  周校准节奏为：close（010）→ drift（012）→ report；检测不产生新采集
- **依据**：数据源零新增（FR-008）；CLI 是唯一操作面（Web 前端只读，F8）。
