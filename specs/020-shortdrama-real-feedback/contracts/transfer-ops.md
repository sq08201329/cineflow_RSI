# 契约：迁移件与渠道运营面（C15~C18）

> 对应规格 FR-010 / FR-011 / FR-012 / FR-013、SC-001 / SC-006 / SC-007、US2 与 US3 全场景、
> 边界情况「迁移口径不可比时如实登记」「凭证缺失即拒绝启动」「模拟回流冒充真实」、澄清第 3/5 条与
> 裁决 G / H / I。实现面：`core/calibration/transfer.py`（新增，机制件）+ `ops/transfer.py`
> （新增，迁移面独立薄 CLI，与 `ops/billing.py` 同风格）+ `core/billing/{calibration,runlog}.py`
> （复用，不重做）+ `ops/billing.py`（凭证矩阵与最小规模入口）+ `ops/demo_shortdrama_feedback.py`
> （新增，离线端到端演示）。
>
> **分层边界**：本文件只做两件事——① **结论迁移**（不迁权重）；② **渠道运营面**（凭证就绪矩阵、
> 最小规模先行、CLI/演示、诚实分层）。日级回流的窗口口径、周期量纲、归属日等由
> `period-cadence.md`（C1~C5）与 `daily-ingest.md`（C6~C10）定义；本文件只**引用**其产物与来源纪律。

## C15 校准结论迁移件与可比性判定（只做结论迁移，不自动迁权重）

**目的**：把短剧线积累的评估器校准结论（信度 / 偏差 / 漂移）按**显式声明的迁移口径**回灌电影线作为
先验，每条带**可比性条件与判定**、不可迁移即**拒绝并记原因**；**只迁结论、不迁权重**（FR-012；原则一/二）。

**包落点与边界**：新增 `core/calibration/transfer.py`，业务无关机制件——零形态字面量、零形态分支
（形态经配置声明；该纪律由 `tests/unit/test_form_switch.py:309-324` 对 `core/` + `agents/` 的
形态字面量与形态判断断言常驻守护）；**不得**
import `core/calibration/refit.py`（静态断言）——**权重再拟合仍走 010 的提案 → 人工确认**
（`ops/calibrate.py:222-252`），迁移只搬结论。

**迁移件形状**（append-only：`{data_dir}/transfers/{transfer_id}.json`，
`transfer_id` 形状（**模板**，其中的尖括号是**字段拼接位**、不是配置取值）：
`<evaluator_id>-<period>-<source_form>-<target_form>`；**字段名与 `data-model.md`
§10「校准结论迁移件（CalibrationTransfer）」逐字对齐**，`target_form` 是 `source_form` 之外的
**扩展**——目标是本形态，写出来是为了让"短剧数字出现在电影线证据面"这件事**机检可判**）：

```json
{
  "schema": 1,
  "transfer_id": "judge.xxx-2026-09-25-shortdrama-movie",
  "source_form": "shortdrama", "target_form": "movie",
  "evaluator_key": "judge.xxx@1.0.0",
  "period": "2026-09-25",
  "samples": 42,
  "source_ref": [{"kind": "ledger", "path": "ledger/screenplay/judge.xxx.jsonl", "digest": "…"},
                 {"kind": "snapshot", "path": "snapshots/screenplay/judge.xxx/2026-09-25.json", "digest": "…"},
                 {"kind": "report", "path": "reports/2026-09-25.json", "digest": "…"},
                 {"kind": "drift", "path": "drift/metrics/screenplay/judge.xxx/2026-09-25.json", "digest": "…"}],
  "transfer_basis": "conclusion_only",
  "comparability": {
    "conditions": [{"id": "min_samples", "declared": 30, "observed": 42, "satisfied": true},
                   {"id": "min_real_days", "declared": 14, "observed": 9, "satisfied": false,
                    "reason": "来源真实覆盖 9 天 < 14 天"}],
    "verdict": "not_transferable",
    "reasons": ["min_real_days：来源真实覆盖 9 天 < 14 天"]
  },
  "conclusion": {"kendall_tau": 0.71, "mean_shift": 0.03, "samples": 42, "drift_status": "pass"},
  "status": "pending",
  "confirmed_by": "", "confirmed_at": "",
  "overrides": [],
  "created_at": "…", "system_digest": "…"
}
```

- `period` 由形态声明的 cadence **派生**（日级 ⇒ 日期、周级 ⇒ ISO 周；口径见 `period-cadence.md` C1~C5，
  现状实现点 `core/calibration/rounds.py:28` 的 `iso_week_label` 与 `:69` 消费点按该契约改造）。
- **归属日字段名统一为 `metric_date`**（`str | None = None`，**末位可选**、维度上**写入路径强制**）：
  若迁移件的来源明细需带来源锚点的归属日，字段名一律 `metric_date`（与 C6~C10 的锚点列同名、逐字一致，
  不得另起名；定义处 `agents/promo/platform/base.py:71` 的 `MetricSnapshot`、读取处
  `agents/promo/anchors.py:30`）；缺失即显式失败、不得以采集时刻兜底（口径见 `daily-ingest.md` C7）。
- `conclusion` **只含结论**：信度（`kendall_tau` / `pearson_r`）、偏差（`mean_shift`）、样本量与漂移状态；
  **禁止**出现 `candidate_weights` / `current_weights` / `fit_objective` / `ridge_lambda` 四键
  （010 提案形状见 `core/calibration/refit.py:145-159`）——出现即结构断言红。
- 来源只读（010 既有产物）：台账 `core/calibration/ledger.py:22`（`ledger/{agent_id}/{evaluator_id}.jsonl`）、
  锚点分布快照 `core/calibration/ledger.py:84`、信度报告 `core/calibration/report.py:55`、
  漂移产物 `core/calibration/drift_metrics.py:12`（`drift/metrics/{agent}/{evaluator_id}/{period}.json`）
  与漂移报告对信度报告的读取口径 `core/calibration/drift_report.py:96-100`。迁移**零写入**这些目录。
- **可比性条件配置化**：新增配置键 **`calibration.transfer.{basis, source_forms, target_forms,
  conditions, storage, adoption}`** ——**两形态均须声明、缺项即报错、不取码内默认**（FR-014 / 裁决 I）；
  两形态都声明的是这套**键的 schema 齐备**（都能加载、缺键即报错），**取值随形态不同**（`source_forms` /
  `target_forms` 各自声明；投放渠道只登记在短剧态，见 `channel-budget.md` C11）。

```yaml
calibration:
  transfer:                       # 020 新增段（形状两形态一致；取值随形态不同）
    basis: conclusion_only        # 单元素取值域：只迁结论，不迁权重
    source_forms: [shortdrama]    # 允许的来源形态（本特性 = 短剧线）
    target_forms: [movie]
    conditions:                   # 键集 = 判定项清单；产物里逐条落 declared/observed/satisfied
      min_samples: 30             # 来源周期样本量下限（样本不足 ⇒ 不可迁移）
      min_real_days: 14           # 来源真实覆盖天数下限（只计 source=real，复用 019 口径）
      reliability_floor: 0.6      # 来源信度下限（kendall_tau / pearson_r 取非空者）
      max_abs_mean_shift: 0.05    # 来源偏差幅度上限
      require_drift_pass: true    # 来源漂移状态必须 pass（012 口径）
    storage: {dir: transfers}     # append-only 目录（相对 data_dir）
    adoption: manual              # 人工两键（提案 → 人工采纳/搁置）；取值域单元素
```

- 判定：`comparability.verdict == "transferable"` ⇔ **全部**条件 `satisfied`；否则 `not_transferable` 且
  `comparability.reasons` **逐条点名命中的是哪一条**（不得只写"不可比"、不得降格为"仅供参考"的数字，
  US3 场景 2）。
- 采纳（人工两键，镜像 010 的 `pending → confirmed | shelved`，`core/calibration/models.py:60-63`）：
  `status ∈ {pending, confirmed, shelved}`，**由 `overrides[]` 末条派生**（采纳/搁置只**追加**一行：
  人 / 时间 / 理由，并回填 `confirmed_by` / `confirmed_at`），系统字段（`comparability` / `conclusion` /
  `source_ref` / `samples` / `period`）**逐字节不变**——镜像 `core/degraded/evidence.py:237-241`（同键重产
  拒绝）与 `core/billing/bill.py:406-453`（`system_digest` 机检 + `append_override` 只增）两条既有先例。
  **不变量**（`data-model.md` 的 I-13）：`comparability.verdict == "not_transferable" ⇒ status != "confirmed"`
  （不可迁移的**误迁移次数恒 0**）。
- **采纳不得改动任何既有节点**：迁移件**零**写树/写库入口；`eval_breakdown` 与得分逐字节一致的机检
  复用 `tests/contract/test_calibration_contracts.py:1-9`（SC-006 口径）。
- 无可迁移结论（真实回流待运营）：`transfer-report` **如实标注**「无可迁移结论（来源缺失）」并给出
  继续观察条件（来源真实覆盖 ≥ `min_real_days` 且样本 ≥ `min_samples`），**不得**以模拟回流的结论充当来源。
- 配置项 → 产物字段的映射（口径单一，不各写一份）：配置 `calibration.transfer.basis` → 产物
  `transfer_basis`；配置 `conditions` 的**键集** → 产物 `comparability.conditions[].id`；
  配置 `adoption: manual` → 产物 `status` 只能经人工两键迁移（无自动采纳路径）。

### 机检断言

- 条件完备率 **100%**：声明的每条条件在产物里都有 `declared` / `observed` / `satisfied`；缺一条即红。
- `comparability.verdict` 取值域固定 `{transferable, not_transferable}`；`not_transferable` ⇒
  `comparability.reasons` 非空且每条前缀点名条件 id；`status` 取值域固定 `{pending, confirmed, shelved}`
  且满足不变量"`not_transferable ⇒ status != confirmed`"（误迁移次数恒 0）。
- 迁移件零权重键（结构断言）；`core/calibration/transfer.py` 零 `core.calibration.refit` import（静态断言）。
- append-only：同 `transfer_id` 重产 ⇒ 拒绝；系统字段被改写 ⇒ `system_digest` 校验失败 ⇒ 拒采信。
- **异形态数值冒充本形态证据次数恒 0**：迁移件只落 `{data_dir}/transfers/`、必带
  `source_form`/`target_form`；
  电影线证据面（节点 `eval_breakdown`、信度报告、漂移产物）**零**写入迁移数值（落盘路径断言 + 静态断言）。
- 迁移件与 010 台账/快照/报告/漂移产物的读取**只读**：跑迁移后这些文件的摘要逐字节不变。

### 反例

1. 把短剧线的 `mean_shift` 直接写进电影线节点的 `eval_breakdown` ⇒ **不存在该入口**（机检为 0 次）。
2. `min_real_days` 未达仍产 `comparability.verdict = "transferable"`（或把 `status` 直接置 `confirmed`）
   ⇒ 红；把不满足的条件省略（不落 `satisfied`）⇒ 完备率红。
3. 迁移件里顺手带 `candidate_weights`（"权重也搬过去"）⇒ 结构断言红；`transfer.py` import `refit` ⇒ 静态红。
4. 样本不足时输出"结论仅供参考（数字照用，不写判定）" ⇒ 缺 `comparability.verdict` / `reasons` ⇒ 红。
5. 无短剧线结论时用模拟回流的结论生成迁移件 ⇒ 来源 `source != real` ⇒ 红（见 C18）。

### 兼容规则（对 010 零回改）

- 010 的台账 / 快照 / 信度报告 / 漂移产物 / 提案目录形状与路径**零改动、零回改**；
  `core/calibration/` 既有模块（`ledger.py` / `report.py` / `drift_*` / `refit.py`）**不新增形态分支**。
- 迁移件是**新目录** `transfers/`（与 `proposals/` 并列，`core/calibration/refit.py:141-142` 的先例）；
  `ops/calibrate.py` 既有**八个**子命令（`round` / `intake` / `close` / `report` / `propose` / `confirm` /
  `shelve` / `drift`，`ops/calibrate.py:417-475`）语义一字不变。
- 迁移**不改变**权重：即便迁移件被采纳，评估器权重仍须走 010 的提案 → 人工确认（原则一）。
- **存储与迁移面对齐（本契约只**引用**，不重复其断言）**：迁移件本身是**文件化 append-only**
  （`calibration/transfers/{transfer_id}.json`，**本契约面零 DDL**）；本特性的 DB 面迁移 =
  `ops/migrations/versions/0011_daily_feedback.py`（`down_revision = "0010_dev_jobs"`）**两步**——
  ① `calibration_anchors` 增一列**可空**的归属日 `metric_date`（历史行恒 **NULL、永不回填**，新写入非空；
  降级只 `DROP COLUMN`）；② 新增表 `promo_daily_metrics`（唯一键 **（campaign_id, period）** +
  INSERT-only 触发器）。**不改** `promo_campaigns` 既有唯一键 `(round_id, material_id)`（该表保持无触发器）、
  **不动**发现树与 `CostRecord`；若本契约面日后需要 DDL，**必须**取 `0012_*` 及之后的修订号（不与之分叉）。

## C16 凭证就绪矩阵与最小规模先行（先验证协议与计费口径，再谈扩量）

**目的**：把"能不能接真实渠道"从口头确认变成**可机检的矩阵与拒绝语义**（凭证缺失即装配期拒绝启动、
不静默回落模拟），并守住原则三末条"任一真实渠道必须先以最小规模验证协议与计费口径，验证通过方可扩量"
（FR-010 / FR-011；SC-004 / SC-006）。

**凭证就绪矩阵**（`CredentialMatrix`，只读、零网络、**不落凭证值**）：

| 项 | 权威/口径 | 引用 |
| --- | --- | --- |
| 变量名**以适配器代码为权威** | `PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY` | `agents/promo/platform/http_real.py:153-154`（读取点）；缺凭证文案 `:142-143` |
| 权威登记表 | C 路径 `credential_envs` | `docs/pilot-upgrade-manifest.json:134-140`；反向机检 `tests/unit/test_credential_env_lock.py` |
| 核查器先例（零网络就绪矩阵、`--probe` 才联网且只发 GET） | 缺失分型 `missing` / `unreachable` / `auth_rejected` | `ops/check_credentials.py:8-14`、`:38`（零凭证基线） |
| 只报"是否设置 + 长度"、**绝不回显值** | — | `ops/smoke_llm.py:132-139`、`ops/billing.py:563-569` |

- 形状：渠道 → `adapter`（装配入口）→ `{env: {set: bool, length: int}}` → `ready: bool` → **缺失时的拒绝语义**；
  矩阵**不落盘凭证值**、不进产物（产物与报告里只允许 `set`/`length`）。
- 声明真实而缺失 ⇒ **装配期显式拒绝启动**：装配点 `agents/pilot/backends.py:399-406`（`_promo` 真实分支 →
  `HttpRealPlatform.from_env()`），经 `agents/pilot/backends.py:323-331` `_guard` 收口为
  `BackendAssemblyError`（零落树、零扣费），错误**点名缺哪个变量**（沿用
  `agents/promo/platform/http_real.py:142-143` 原文）——
  **绝不静默回落模拟**（FR-011 / US2 场景 2）。
- 声明模拟 ⇒ 矩阵 `ready=true` 且该渠道的 `pilot` 后端取值 = `simulated`
  （`agents/promo/platform/simulated.py`）；两条路径在报告里**可辨**，不混同。
- **矩阵按形态声明生成**：投放渠道只登记在短剧态，故 movie 的矩阵只含其声明的渠道（未登记投放渠道
  ⇒ 不生成投放行、也不触发任何投放凭证判定）；**movie 路径不得因投放渠道或其凭证而失败**（C11）。

**最小规模先行**（原则三末条：任一真实渠道必须先以最小规模验证协议与计费口径）：

- **最小规模档 = 该渠道投放环节档位的 `limit_usd`**（不新增额度键，见 `channel-budget.md` C11）：
  首轮投放申请额 **≤ 该档 `limit_usd`**（未标定期间该档即"最小规模档"，`note` 如实标注），超出 ⇒
  019 门禁**调用前拒绝**并留痕（平台调用 0 次、零入账）。
- **投放渠道的账单导入面必须声明**：`budget.channels.<投放渠道>.bill`（格式 id + 来源形态 + 语义列映射 +
  分类驱动列取值域），**形状复用 019 的 `bill`、不新造**；缺任一键即 `BudgetConfigError`（C11）。
  导入与逐项对账走 `core/billing/bill.py:321`（`normalize_bill`）与 `core/billing/reconcile.py:233`
  （`reconcile`）——**每条差异带六类之一** + `delta_usd` 实测偏差 + 口径备注，**无分类即不可解释并告警
  （100%）**，**报告必引账单批次**（`bill_refs[]` 含 `bill_id` + `source`），网关/内部记账**不得**作
  "成本已核实"的唯一依据（019 C13 断言**不变**、门禁不放松）。
  **可复制验证序列见 `quickstart.md` 的 B6**（`import-bill --channel media` → `reconcile --channel media`
  → `alert-check --channel media`；退出码与 019 的 LLM 渠道口径**逐条一致**，只换渠道）。
- 校准记录**复用 019 不重做**：`core/billing/calibration.py:57`（`CalibrationRecord`）、`:131`
  （`record_calibration`）、`:226`（`load_calibration`）、`:242`（`require_calibration`）、`:295`
  （`calibration_status`）、`:343`（`raise_tier`）；阈值取 `budget.calibration.*`
  （短剧态 `configs/shortdrama.yaml:589-592`：`min_samples` / `deviation_tolerance` / `record_ttl_days`）。
- **投放渠道的最小规模入口必须是投放渠道自己的**：
  `uv run python ops/billing.py calibrate --channel media --tier <投放环节> --from-records --measured-usd <实测> --config configs/shortdrama.yaml`
  ——只读既有运行记录与账本（`ops/billing.py:262-293` 按渠道分目录统计 `source=real` 条数与记账值）、
  **不联网、不构造后端、不新测花费**（019 C12 口径）。
  **不得**假定 019 的 LLM 冒烟入口（`ops/smoke_llm.py`）适用于投放渠道——那一条只覆盖 LLM 渠道
  （`specs/019-real-channel-billing/spec.md:102`）。
- 扩量：`raise_tier` 六条先决（无 `calibration_id` / 记录不存在 / 渠道不符 / `passed=false` /
  样本不足 / 超期或超容差）**任一命中即拒绝并留痕**，配置**零改写**；拒绝理由**点名命中的是哪一条**。
- 合规前提（业务侧，不得被代码或配置发明或绕过）：合规审查未完成**不得**扩量
  （`docs/三期立项书.md:271` 列为**高**风险）；工程侧只做"未标定即如实标注"。

### 机检断言

- 凭证缺失 ⇒ 装配拒绝率 **100%**（零落树、零扣费、零模拟回落）；矩阵与产物**零凭证值**
  （只允许 `set`/`length`）。
- 无合格校准记录的扩量 **100% 拒绝**且配置未被改写（019 C12 断言保留）；首轮投放申请超出该档
  `limit_usd` ⇒ **100% 拒绝**且平台调用 **0** 次（019 C10 断言保留）。
- 渠道不符的校准记录不得用于扩量（LLM 渠道的记录不能给 `media` 渠道扩量）。

### 反例

1. `pilot.overrides.promo: http` 而 `PROMO_PLATFORM_API_KEY` 未设 ⇒ 装配报错；若回落模拟并跑通 ⇒ 红。
2. 拿 `ops/smoke_llm.py --round` 的 LLM 运行记录给 `media` 渠道扩量 ⇒ 拒绝（渠道不符）。
3. 把凭证值写进矩阵/报告/日志 ⇒ 红（只报 `set`/`length`）。
4. 未声明投放环节档就按渠道其他档跑首轮投放 ⇒ 装配期报错（缺档即拒绝、不取码内默认，`tier_undeclared`）。

### 兼容规则（对 019 零回改）

- 019 的 `CalibrationRecord` / `raise_tier` / `require_calibration` / `calibration_status` 形状与语义
  **逐字不变**（只多一个渠道与一组渠道分派的档位）。
- `ops/billing.py calibrate` / `raise-tier` 的参数面**向后兼容**：既有 LLM 渠道用法一字不变，
  `--channel media` 走同一路径、同一判定。
- 凭证登记面**不改名、不新增变量**（`docs/pilot-upgrade-manifest.json:134-140` 的 `credential_envs`
  只按交付状态更新 `status`/notes，反向机检 `tests/unit/test_credential_env_lock.py` 常驻）。

## C17 CLI / 离线演示与退出码语义

**目的**：给出本特性全部**可复制执行**的入口与**退出码语义**（0/1/2，与既有工具一致），并固定一个
**离线端到端演示**（零真实花费、零外部网络、零凭证）作为机制层的常驻证据（FR-014；裁决 I）。

```
uv run python ops/billing.py channels  [--config configs/shortdrama.yaml]          # C12：渠道集合/adapter/凭证矩阵
uv run python ops/billing.py tiers     --channel media --config configs/shortdrama.yaml
uv run python ops/billing.py calibrate --channel media --tier <投放环节> --from-records \
        --measured-usd <实测> --cost-source operator_reported --config configs/shortdrama.yaml

# 迁移面：**独立脚本** `ops/transfer.py`（与 ops/billing.py 同风格；判定全在 core/calibration/transfer.py）
uv run python ops/transfer.py transfer        --data-dir <dir> --from configs/shortdrama.yaml \
        --to configs/movie.yaml --evaluator <id@version> --period <周期> [--dry-run]
uv run python ops/transfer.py transfer-confirm --data-dir <dir> --transfer <id> --by <人> --reason <理由>
uv run python ops/transfer.py transfer-shelve  --data-dir <dir> --transfer <id> --by <人> --reason <理由>
uv run python ops/transfer.py transfer-report  --data-dir <dir>                    # 含「无可迁移结论（来源缺失）」
uv run python ops/demo_shortdrama_feedback.py                                      # 020 离线端到端演示
```

- **独立脚本（本契约的判断项，按裁决定名）**：迁移面 CLI = **`ops/transfer.py`**，四个子命令
  **`transfer` / `transfer-confirm` / `transfer-shelve` / `transfer-report`**（与 019 的 `ops/billing.py`
  同风格：薄转发 + JSON 输出 + 同一套退出码语义）；**不挂在 `ops/calibrate.py` 上**（那是 010 的周校准与
  权重提案入口，两者语义不同：010 的 `propose → confirm` 会改权重，本面的 `transfer → transfer-confirm`
  **只采纳结论**、四个子命令一字不改权重）。
- **新增配置键清单（两形态均须声明、缺项即报错、不取码内默认）**：`calibration.transfer.basis` /
  `.source_forms` / `.target_forms` / `.conditions`（键集 = 判定项）/ `.storage.dir` / `.adoption`——
  缺任一项即拒绝（退出码 2），**不取码内默认**；两形态都声明的是**键的 schema 齐备**，取值随形态不同
  （见 C15）。

- **命令行示例约定（示例参数与配置取值严格分开）**：上列命令里 `<>` 包裹的取值（`<dir>` / `<id>` /
  `<id@version>` / `<周期>` / `<投放环节>` / `<实测>` / `<人>` / `<理由>`）**一律是示例参数**，须替换为实际
  取值；它们**不是**配置取值，**不得**出现在 `configs/*.yaml` 里（配置里出现 `<`/`>` 占位即视为非法取值，
  缺项/非法即报错、不取码内默认）。
- **退出码语义**（沿用既有工具口径：`ops/billing.py:40-41`、`ops/check_credentials.py:16`）：
  `0` 成功（含 `channels` 全就绪、`transfer-report` 无不可迁移项）｜`1` 执行失败或拒绝
  （迁移被拒 / 扩量被拒 / 窗口未达标 / 有告警 / 装配拒绝）｜`2` 用法或配置错误（缺项即报错：
  跨形态未声明 `calibration.transfer`、可比性条件缺项、渠道未声明、`--channel nope`）。
- **薄转发纪律**：CLI 只解析参数与打印 JSON，判定全在 `core/`（`core/calibration/transfer.py`、
  `core/billing/{budget,calibration,runlog}.py`）；`ops/transfer.py` 的四个子命令**不写任何权重、不调
  `confirm_proposal`**（`ops/calibrate.py:222-252` 那条路径只服务 010 的权重提案，二者互不调用）。
- **`--dry-run`**：只做条件判定并打印预览、**零落盘**（离线复核用）；非 dry-run 落
  `{data_dir}/transfers/{transfer_id}.json`，同键已存在 ⇒ 拒绝重产（append-only）。`transfer-report`
  只读既有件 + 既有台账/快照/报告/漂移产物，**零写入**。
- **离线演示脚本名（本契约判断项，唯一入口）**：`ops/demo_shortdrama_feedback.py`——与
  `ops/demo_billing.py` / `ops/demo_pilot.py` / `ops/demo_calibration.py` 命名一致，**020 不新造第二个
  端到端演示入口**（C1~C10 的步骤并入同一脚本、各自的机检仍归各自契约）。七步（**退出码 0 = 全步 ok**，
  镜像 `ops/demo_billing.py` 风格）：
  1. **两形态配置形状与缺项拒绝**：`budget.channels.<id>.tiers`（`adapter` 域内）+ `calibration.transfer`
     齐备；删任一项 ⇒ 装配/加载报错（不取码内默认）；旧扁平形状仍可读并显式归一；
  2. **渠道分派**：LLM 渠道与投放渠道的档位/账本/告警/运行记录互不可见，同档位跨渠道串用 **0** 次
     （多渠道 + 顶层扁平 `tiers` ⇒ 报错，不静默归入任一渠道）；
  3. **凭证矩阵与装配期拒绝**：声明真实而凭证缺失 ⇒ 拒绝启动（点名变量、零落树零扣费、不回落模拟）；
  4. **最小规模先行**：Mock 平台 + 夹具账单跑最小规模档（= 投放环节档位 `limit_usd`）→ 校准记录
     （append-only）→ 未校准扩量被拒留痕；
  5. **投放调用受同一门禁**：超限申请 ⇒ **调用前拒绝、平台调用 0 次、零入账**；015 按轮上限与 019 账本
     两条腿的口径可分辨；
  6. **迁移件**：可迁移 / 不可迁移各一（不可迁移逐条记原因）；采纳走人工两键，既有节点
     `eval_breakdown` 与得分逐字节一致（**权重零改动**）；
  7. **诚实分层**：模拟来源不计入真实覆盖；`source` 可辨；窗口断档逐段报出；结论写
     「**机制已就绪 / 真实回流待运营**」（见 C18）。
- 演示的**零成本红线**：Mock 平台后端（`agents/promo/platform/simulated.py`）+ 夹具账单/指标 + 临时目录 +
  确定性时钟；`network: none`（镜像 `ops/billing.py:441` 的输出字段口径）。需要验证 HTTP 协议时对**本地
  stub** 跑（`tests/stubs_http.py` / `core/platform_http.py` 既有 stub 面），**该路径一律标
  `source=simulated`**——本地 stub 不是真实渠道，不得计入真实覆盖天数。

### 机检断言

- 各 CLI 入口的 `--help` 退出码 0（`ops/billing.py` 八子命令、`ops/transfer.py` 四个子命令、
  `ops/calibrate.py` 既有八子命令不变）；`--channel nope` ⇒ 2；缺 `calibration.transfer` 任一键
  （`basis` / `source_forms` / `target_forms` / `conditions` / `storage` / `adoption`）⇒ 2（缺项即报错）。
- `transfer` 非 dry-run ⇒ 落件 + 同键重产被拒（退出码 1）；`--dry-run` ⇒ 零落盘（目录不新增文件）。
- `ops/demo_shortdrama_feedback.py` 退出码 **0** 且七步全 ok；跑完仓库根**零真实花费产物**、
  **零外部网络**（无凭证亦可跑）。
- `channels` 输出含渠道集合、各渠道 `adapter`（装配入口）、凭证矩阵（`set`/`length`）与每渠道账本路径；
  **不含凭证值**；movie 配置下不出现投放渠道行。

### 反例

1. 迁移命令 `--dry-run` 仍落盘 / 落盘后允许重产 ⇒ 红（append-only 失守）。
2. `ops/transfer.py` 顺带调用 `confirm_proposal`（自动改权重）⇒ 静态断言红
   （`core/calibration/transfer.py` 零 `core.calibration.refit` import）。
3. 演示脚本把"真实回流 ≥2 周"标成已达成（模拟件凑数）⇒ 红（C18）。
4. 用第二个演示脚本名（与既有 `ops/demo_*.py` 命名不一致），或把迁移子命令挂到 `ops/calibrate.py`
   上（与 010 的权重提案入口混同）⇒ 命名/分层判断项红（命令与文档须逐字一致）。

### 兼容规则（对 019 / 010 零回改）

- `ops/billing.py` 既有七个子命令的参数与退出码**一字不变**；新增 `channels` 为**只读**子命令
  （不写账本、不写报告）。
- `ops/calibrate.py` 既有八个子命令（`round` / `intake` / `close` / `report` / `propose` / `confirm` /
  `shelve` / `drift`）**一字不变**；迁移面是**新脚本** `ops/transfer.py`（不往 `ops/calibrate.py` 加子命令），
  其件落独立目录 `transfers/`，**不触碰** `proposals/`。
- 演示脚本的产物只落**临时目录**（仓库根零残留，沿用 019 的单测不写仓库口径）。

## C18 诚实分层机检（模拟不得冒充真实）

**目的**：把"本特性交付机制与离线复现、真实回流待运营"写成**机检事实**——复用唯一的来源取值域、
模拟不计入真实覆盖、结论只允许两种取值、渠道失败不得静默回落模拟并照常计费（FR-013；SC-001 / SC-003）。

- **唯一来源取值域**：复用 `RUN_SOURCES = ("real", "simulated", "fallback")`
  （`core/billing/runlog.py:28`）——运行记录（`core/billing/runlog.py:313` 的 `RecordingGateway`、
  投放面的 `RecordingChannelCall`）、日级回流件与窗口覆盖判定**三处共用同一取值域**，不得各立一套。
- **"模拟被标为真实"次数恒 0**（三重机检，缺一不可）：
  1. **标注同源**：`source` 只能由**装配面**声明（`core/billing/runlog.py:313-346` 的口径；投放面同款），
     调用点不得自填、不得按"跑通了"推断；
  2. **判定只认真实**：窗口覆盖只计 `source=real` 的日期（`core/billing/runlog.py:226-310` 的
     `covered_days` 口径，断档逐段列出、**禁止插值**）；模拟件与本地 stub 调用**不计入**真实覆盖天数；
  3. **结论文案取值域固定**：`机制已就绪` / `真实回流待运营`；未满足前提（真实覆盖 ≥
     `budget.runs.min_window_days` **∧** 最长断档 ≤ `budget.runs.gap_tolerance_days`，
     `configs/shortdrama.yaml:600-602`）时输出"真实数据回流已达成"⇒ 红。**窗口下限取形态定值**：
     短剧态 `min_window_days: 14`（= 立项书 G4 验收原文"短剧线真实数据回流 ≥2 周"，**不是发明数字**）、
     电影态保持 **7**；`gap_tolerance_days` 保持现值（断档容差取 0 还是留余量仍属**开放问题**，
     未裁决前不发明）。该键由 **C6~C10** 的窗口产物消费（覆盖 ∧ 连续双条件 + 断档逐段报出），
     本契约只要求"按形态声明的下限判定、未达标如实报缺口"。
- **渠道失败禁止静默回落模拟并照常计费**：真实渠道失败/拒绝 ⇒ 运行记录 `result=failed|refused`
  （`core/billing/runlog.py:362`）、告警落 `alerts.jsonl`、花费按"已发生"如实入账；`fallback` 为
  **保留值、本特性无写入点**（沿用 019 C15 的判定）；将来若开回落，**必须**显式声明
  `fallback_reason`（`core/billing/runlog.py:138-141` 已强制）并标 `source=fallback`、
  **不计入真实覆盖**、不得照常计费。
- **交付面与运营面的分界（产物必须写）**：本特性交付 = **机制 + 离线复现**（Mock 平台 + 夹具账单/指标，
  零真实花费、零外部网络、零凭证）；「**短剧线真实数据回流 ≥2 周**」属**运营侧墙钟 + 凭证**前提
  （`docs/三期立项书.md:280`）⇒ 报告/演示产物写「机制已就绪 / 真实回流待运营」，并给出**机检到的**
  真实覆盖天数与逐段缺口；窗口按下限**形态定值**判定（短剧态 14 天 / 电影态 7 天，见上），
  **未达标如实报缺口与差值**，不得改口径凑绿（≥2 周窗口的产物落点与周期口径由 C6~C10 定义，
  本契约只守"诚实分层"这一条）。
- **零凭证可跑**：`uv run python ops/demo_shortdrama_feedback.py` 与全部单测/契约用例在无凭证环境
  恒可跑（全模拟链路），**不得**为通过而要求凭证；反之，声明真实而无凭证**必须**拒绝启动（C16）。

### 机检断言

- 静态：`source` 取值域只有 `RUN_SOURCES` 一个来源（新增字面量即红）；调用点不出现 `source=` 自填
  （装配面单点声明）。
- 契约用例：以夹具构造"某日只有 `simulated` 记录" ⇒ `covered_days` 不含该日、`meets=false`；
  构造"真实但缺 2 天" ⇒ 不通过且 `gaps` 逐段报出（不插值）。
- 结论文案：断言只允许 `机制已就绪` / `真实回流待运营`（取值域外的"已达成"⇒ 红）。
- 演练：demo 步 ⑦ 在**零凭证**环境跑到退出码 0，且产物里 `source` 逐条可辨、真实覆盖天数 = 0
  （机制已就绪、真实回流待运营如实呈现）。
- 窗口下限由**配置**承载且两形态取值可指认（短剧态 `min_window_days: 14` / 电影态 `7`、
  `gap_tolerance_days` 保持现值），`core/` 零形态分支（`tests/unit/test_form_switch.py:309-324` 常驻）；
  机检：短剧态按 14 天判、未达标如实报缺口与差值。

### 反例

1. 因为"跑通了离线演示"就在报告里写"短剧线真实数据回流已达成" ⇒ 红。
2. 真实渠道调用失败后写一条 `source=simulated` 的替代记录并照常计费 ⇒ 红（静默回落）。
3. 把本地 stub 调用记成 `source=real`（从而凑够覆盖天数）⇒ 红。
4. 断档日用上一日指标补齐（插值）⇒ 红（缺口不得被填平，与 019 C15 同构）。

### 兼容规则（对 019 零回改）

- `RUN_SOURCES` 取值域与 `window_coverage` 的双条件判定（覆盖 ∧ 连续、`gaps` 逐段、`meets` 与
  `continuous` 并列）**逐字不变**；本特性只把来源纪律**复用**到日级回流与投放调用面。
- `billing/{channel}/runs/{date}.json` 的形状、链式摘要与封存语义**不变**；历史运行记录零回改。
- 019 的"`source=fallback` 为保留值（无写入点）"结论**不变**——020 也不为它发明写入点。
