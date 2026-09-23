# 契约：开发 Agent 四评估器与合成评分

> 对应规格 US2 / FR-003~005。实现：`agents/dev/evaluators/`。组合恒为 **2 gate + 2 代理**——
> 无 judge、无 human 锚点（澄清 Q1：技术方案 §2.2 只给本环节"代理信号"一层）。

## C7 rule.slate_structure（gate）

立项组合工件的结构完整性：组合条目数 ∈ 配置区间、方向标识组合内唯一、必填字段齐全。
任一违规 → score 0.0 → **gate 短路**（总分直接 0，不跑后续分量、不浪费调用）；诊断必须
**指明具体违规项**（越界的实测条目数与区间 / 重复的方向标识 / 缺失字段名），不得只回一个 0；
失败以 `EvalResult(score=0.0, diagnostics.violations)` 返回、**不抛异常**（先例
`agents/screenplay/evaluators/beat_structure.py:84`）；阈值全配置化（`dev` 段），缺项即报错。

## C8 rule.slate_combination（gate，组合层核心门禁）

组合层约束：组合内方向多样性/去重率 ≥ 配置下限、条目数 ≤ 配置上限、**进入生产标记数量 ∈
配置区间**（默认区间为 1）且标记**必须指向组合内已存在条目**（指向不存在条目即违规）。
violation ⇒ total 0 且原因**点名违规项**；组合内无达标条目时如实产出 0 标记 + 原因
（门禁违规项或分数未达阈值），**不得**降格硬凑一个标记（SC-010）；0 标记与标记区间下界
冲突时以区间为准（判 0 并如实记录，见 dev-artifact.md C5）。越界不由代码兜底。

## C9 proxy.genre_regression + proxy.buzz_heat（确定性代理）

两代理共用同一份契约（合并陈述，避免两份会漂移的口径）：

- 实现 = 确定性函数（零 LLM、零网络）：`genre_regression.py`（历史同类型票房回归预测）、
  `buzz_heat.py`（舆情检索热度）；驱动源 = `agents/dev/signals.py` 的**模拟数据源**（参数化自 `dev` 配置段，零边际成本、可回放）
- 版本号 = `1.0.0+<实现文件与口径参数哈希前 12 位>`（`evaluators/_versioning.py` 按包复制，
  004~009 口径；`agents/screenplay/evaluators/_versioning.py:12` 同位）
- **数据源即行为口径**（原则一）：模拟数据源任一参数变更 ⇒ 新 `evaluator_id@version`；
  旧节点的 `eval_breakdown` **永不重算**（版本随树冻结）
- 标注纪律：数据来源与"**非真实商业数据**"必须随产物、对比报告、判据材料**三处**机读可见
  （SC-009；标注不可省，口径同 `agents/pilot/package.py:34` 的 `SIMULATED_NOTE`）
- 同 `evaluator_id@version` 重复注册被拒（`core/evaluators/registry.py:37`）；非确定性
  评估器禁止注册（`registry.py:29`，原则一）

## C10 合成与权重（`composite.py`）

合成 = gate 短路 + 适用权重归一 + quantize 定点 6 位（`core/evaluators/quantize.py:10`），
镜像 `agents/screenplay/evaluators/composite.py:29`：`rule.*` 且 score == 0.0 → 0.0；
gate 权重恒 0 不入归一；缺席分量跳过且分母不含其权重（不伪造 0 分拖底）；无适用连续分量
→ 0.0（确定性退化口径）。阈值与权重取自 `evaluator_weights.dev`（段在 `configs/movie.yaml:6`，
与 `screenplay` 块同位 `configs/movie.yaml:38`），两形态均须声明，缺失即报错。
**零形态分支**：`dev` 段差异只经配置，静态断言在 `tests/unit/test_form_switch.py:288`
（`:299` 禁形态字面量、`:309` 禁 `form ==` 类判断，扫描 `core/` 与 `agents/`）。

### 断言/场景（每评估器 ≥2 条）

1. 条目数越界 / 方向标识重复 / 必填字段缺失 → `rule.slate_structure` 判 0 并点名违规项
2. 方向重复率超限 / 标记数量越界 / 标记指向不存在条目 → `rule.slate_combination` 判 0
3. gate 违规 → 总分 0 且两代理不参与合成（短路，诊断含具体违规项）
4. 合规工件同输入重算 → 四分量与合成总分逐位一致（确定性、定点 6 位）
5. 改模拟数据源参数 → 新 `evaluator_id@version`；历史节点 breakdown 逐字节不变
6. 同 id 同 version 重复注册 → 拒绝；实现变更未升版 → 拒绝
7. 产物/报告/材料的来源标注缺失 → 断言失败（"非真实商业数据"须可机读）
