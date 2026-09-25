# 数据模型：短剧形态的真实投放与日级回流（020-shortdrama-real-feedback）

> 存储分层：**配置**（`configs/*.yaml` 的 `calibration:` 段扩展 + `promo:` 段扩展 + 019 `budget:` 段的渠道命名空间）
> → **运行期事实**双轨：① DB 侧 append-only（`calibration_anchors` **扩列** + 新表 `promo_daily_metrics`）；
> ② 文件侧 append-only（`calibration/{ledger,snapshots,reports,drift}/`）。
> **本特性唯一的 DB 迁移 = `0011_daily_feedback`**（当前头 `ops/migrations/versions/0010_dev_jobs.py:22`
> `revision: str = "0010_dev_jobs"`）；除此之外**零 DDL、零既有行回改**。
> 字段级约定见 [contracts/period-cadence.md](contracts/period-cadence.md)（C1~C5）与
> [contracts/daily-ingest.md](contracts/daily-ingest.md)（C6~C10）；019 侧渠道/分档额度与迁移件的契约
> 分别在 `contracts/channel-budget.md`（C11~C14）与 `contracts/transfer-ops.md`（C15~C18），本文件只登记其**实体面**。

## 现状（本次勘查确认的代码事实，逐条带引用）

- **"日级"只在配置里**：`configs/shortdrama.yaml:399` 声明 `period_days: 1`（`configs/movie.yaml:392` 为 `7`），
  但周期标签一律取 ISO 周——`core/calibration/rounds.py:28` `iso_week_label`、调用点 `core/calibration/rounds.py:69`、
  `ops/calibrate.py:190`；台账 `BiasRecord.period`、快照文件名（`core/calibration/ledger.py:84`）、
  信度报告文件名（`core/calibration/report.py:55`）全部继承该标签并以 `write_text` 落盘。
- **漂移按周、与注释不符**：`core/calibration/drift_metrics.py:53` 标签正则 `_PERIOD_RE = ^(\d{4})-W(\d{2})$`、
  `:54` `_WEEK = timedelta(days=7)`；故 `configs/shortdrama.yaml:408` 的"日级外环 → 3 天窗口"注释与实际（3 周）不符。
- **窗口含首尾**：`core/calibration/selection.py:28` `_period_window` 对 `period_end` 加一天（`:33`），
  而 `ops/calibrate.py:43` 的缺省 `period_start = 今天 − period_days` ⇒ 缺省窗口实际跨 `period_days + 1` 天。
- **回流"一次活动一次快照"**：`agents/promo/db.py:33` `node_id`（注释"回填即终态不再变"）、`:44` `metrics`（注释"回流后写入一次"）、
  `:47` 唯一键 `(round_id, material_id)`；`agents/promo/ingest.py:56-60` 只取 `status == "delivered"`、
  `:91` 节点 id = `f"{material_id}-node"`、`:117` `created_at=time.time()`、`:125-135` 随即置 `ingested`。
- **归属日无从取得**：`agents/promo/platform/base.py:71` `MetricSnapshot` 只有 `:79` `platform_timestamp: float`；
  `agents/promo/anchors.py:22` `_snapshot_created_at` 把它当"真值产生时刻"、缺失才以采集时刻兜底（`:26`），
  锚点 `created_at` 取它（`:66`）；写快照的只有两处适配器 `agents/promo/platform/simulated.py:104` 与
  `agents/promo/platform/http_real.py:219`，以及从 dict 重建的 `agents/promo/evaluators/platform_metrics.py:46`。

## `calibration:` 段扩展（两形态均须声明，缺项即报错）

```yaml
calibration:
  period_days: 1                  # 既有键（configs/shortdrama.yaml:399 / movie.yaml:392）：cadence，取值域 {1,7}
  window_semantics: half_open     # 新增：窗口口径（取值域单元素；其它值装配报错）
  window_semantics_change_date: 2026-09-25   # 新增：口径生效日（ISO 日期）；跨该日的窗口比较必须标注
  drift:
    window: 3                     # 既有键：**单位 = cadence 同量纲**（日级 = 3 天、周级 = 3 周）
```

`period_days` 的取值域由 `core/calibration/periods.py` 把守（`{1, 7}`，其余 ⇒ `ValidationError`「未支持的 cadence」）；
`window_semantics` 由配置声明并由产物回写（口径可追溯，镜像 019 `budget.peak_windows.attribution` 的单元素取值域纪律，
`core/billing/budget.py:162` 的 `PeakWindows`）；三者都**不取码内默认**（FR-014）。

## `promo:` 段扩展（新增键 schema 两形态齐备）

```yaml
promo:
  attribution_date_required_since: 2026-09-25   # 新增：归属日**必填口径生效日**（ISO 日期）
```

- `attribution_date_required_since` 是"缺失字段回退口径"的**唯一边界**：`created_at` 的日期 **< 该日**的 platform_truth 锚点
  允许 `metric_date IS NULL`，读路径按 `created_at` 的日期回退并在报告登记「归属日缺失锚点数」；
  该日**及之后**的真实采集写入路径 `metric_date IS NULL` ⇒ **显式失败**、该条不落锚点（见 C7）。
- **"两形态均须声明"的精确含义（父级裁决）**：指**新增参数键的 schema 齐备**——`configs/movie.yaml` 与
  `configs/shortdrama.yaml` 都能加载、缺键即报错、两形态都能**装配通过**；**不是**要求两形态登记同一渠道集合。
  故 **`configs/movie.yaml` 不登记 media 投放渠道**，`configs/shortdrama.yaml` 登记；
  两形态的 `budget.runs.*` 取值可不同（见实体 3）。


## 领域模型

### 1. 周期量纲口径（PeriodGranularity，新增；业务无关）

新模块 `core/calibration/periods.py`（唯一新增的 core 业务无关模块；不 import 任何业务概念）：

```
period_label(day, period_days) -> str        # period_days==1 ⇒ "YYYY-MM-DD"；==7 ⇒ "YYYY-Www"（ISO 周）；其它 ⇒ ValidationError
period_start(label, period_days) -> date     # 标签 → 窗口首日（非法标签/不存在 ISO 周 ⇒ ValidationError）
period_window(start_day, period_days) -> (date, date)   # **半开** [start, start + period_days)
period_regex(period_days) -> re.Pattern      # 标签形态正则（周级逐字节等于 core/calibration/drift_metrics.py:53 的 _PERIOD_RE）
cadence_of(label) -> int                     # 标签 → cadence（标签形态 ⇒ {1,7} 双射；非法 ⇒ ValidationError）
coverage_window(days, *, end, min_window_days, gap_tolerance_days) -> dict   # 019 core/billing/runlog.py:226-311 口径的纯函数化
WINDOW_SEMANTICS = "half_open"               # 产物回写值
UNSPECIFIED_WINDOW_SEMANTICS = "unspecified" # 历史行回退值（不冒充 half_open）
```

- **`cadence_of(label) -> int` 为什么需要**：`core/calibration/ledger.py:55` `write_anchor_snapshots(data_dir, agent_id, period, pairs)`
  与 `:25` `append_ledger` 等**既有无 cadence 参数的调用点**（含 `ops/demo_web.py:284`、`ops/demo_judge_drift.py:129`、
  `tests/conftest.py:1911`）必须在不改签名的前提下把 `period_days` 写进产物；标签形态与 cadence 在 `{1,7}` 上**双射**，
  故由标签反推 cadence 是**唯一确定性**且不引入码内默认的做法。生产端的 `period_days` 仍来自形态配置
  （`rounds.py:69` / `ops/calibrate.py:190` 传 `config.period_days`），`cadence_of` 只作产物侧的自洽推导与机检
  （机检见下"不变量"与 C1）。
- **`coverage_window(days, *, end, min_window_days, gap_tolerance_days) -> dict` 为什么需要**：FR-002 要求日级窗口的**覆盖 ∧ 连续**判定与 019
  `core/billing/runlog.py:226-311` **同构**，而 019 的实现在 `core/billing/`（依赖 `BudgetConfig` 与渠道账本文件）；
  直接 import 会在 core 内引入 `calibration → billing` 的新耦合（原则五的依赖纪律）。本函数把该口径**纯函数化**
  （输入 = 归属日集合与两个阈值，输出 = 同名键的 dict），跨形态与跨消费者复用同一份判定，且不产生第二份口径。
- **关系**：**新增**模块。`core/calibration/rounds.py:28` `iso_week_label` 保留为 `period_label(day, 7)` 的薄封装
  （既有读取点 `ops/dev.py:391`、`ops/screenplay.py:410` 不变）；`core/calibration/drift_metrics.py` 的
  `_PERIOD_RE`/`_WEEK`/`_period_start`/`_period_label` 改为委托本模块（标签语义逐字节不变）。
- **不变量**（可机检）：`period_label(period_start(p, n), n) == p`（往返恒等，n ∈ {1,7}）；
  `cadence_of(period_label(d, n)) == n`；`period_regex(7).pattern == r"^(\d{4})-W(\d{2})$"`；
  `period_label(d, 1) == d.isoformat()`。

### 2. 日级回流记录（DailyMetricIngest，新增；DB 表 `promo_daily_metrics`）

| 字段 | 类型 | 约束 | 来源 | 可空 |
| --- | --- | --- | --- | --- |
| `ingest_id` | Text | 主键（`core/tree/models.new_id` 的 uuid7，时间有序） | 写入方 | 否 |
| `campaign_id` | Text | 引用 `promo_campaigns.campaign_id`；**活动标识的分量之一** | `agents/promo/loop.py:537-566` 落的行 | 否 |
| `round_id` | Text | 该活动所属投放轮 | 同上 | 否 |
| `external_id` | Text | 平台活动 id（平台返回，不编造） | `agents/promo/platform/base.py:64` | 否 |
| `material_id` | Text | 物料标识 | 同上 | 否 |
| `period` | Text | **归属周期标签** = `period_label(metric_date, period_days)` | 派生 | 否 |
| `metric_date` | Text | **归属日**（平台指标所描述的日期，ISO `YYYY-MM-DD`）；`length = 10` CHECK | `MetricSnapshot.metric_date` | 否 |
| `collected_at` | Float | **采集墙钟**（`time.time()`，与 `agents/promo/ingest.py:117` 同一取值） | 回流管道 | 否 |
| `platform_timestamp` | Float | **平台时间戳（真值产生时刻）**；与归属日**不等同** | `MetricSnapshot.platform_timestamp` | 否 |
| `source` | Text | CHECK `IN ('real','simulated','fallback')`（取值域唯一属主 = `core/billing/runlog.py:28`） | 装配面显式声明 | 否 |
| `snapshot_fingerprint` | Text | BLAKE3（64 位小写十六进制） | `snapshot_fingerprint(metrics)` 派生 | 否 |
| `node_id` | Text | 该周期落树节点的 id | 回流管道 | 否 |
| `metrics` | JSON(B) | 完整 `MetricSnapshot` 快照（`asdict` 形态） | 平台适配器 | 否 |
| `created_at` | Float | 落盘墙钟 | DB | 否 |

- **唯一性键 = （活动, 周期）** ← `UniqueConstraint("campaign_id", "period", name="uq_promo_daily_campaign_period")`。
  同键再次写入 ⇒ `IntegrityError` ⇒ **幂等拒绝**（零变更、整批不中断），语义与 `core/calibration/anchors.py:25`
  `insert_anchor` 的 `begin_nested() + IntegrityError → False` 逐字对齐。
- **关系**：**新增**表（**不**改 `promo_campaigns` 的表结构，也**不改**其既有唯一键 `(round_id, material_id)`
  `agents/promo/db.py:47`；`promo_campaigns` **保持无 immutable 触发器**，`agents/promo/db.py:1-5`）。
  `promo_campaigns.node_id` 由"终态唯一标识"改为 **"最近一次落盘节点"**（须同步改注释，`agents/promo/db.py:33`）；
  `promo_campaigns.metrics` 由"回流后写入一次"改为 **"节点构建素材 + 最近一次快照"**（须同步改注释，
  `agents/promo/db.py:44`），多日快照的**正本**在本表。
- **节点标识（父级裁决）**：每个周期的落树节点 id 改为**含周期的确定性派生**
  `f"{material_id}-node@{period}"`（由 `agents/promo/daily.py` 的 `daily_node_id(material_id, period)` 产出；
  现状固定 `f"{material_id}-node"`，`agents/promo/ingest.py:91`）；**历史 node_id 不回改**，
  既有节点（`node_id == f"{material_id}-node"`）与其 `eval_breakdown`、得分逐字节不变。
- **不变量**：本表插入即冻结（**镜像 0004 的双方言触发器**，`core/calibration/db.py:47-73`）⇒
  `UPDATE`/`DELETE` 恒被拒（SC-008）；`(campaign_id, period)` 行数 ≤ 1（恒成立）；
  `period == period_label(metric_date, cadence_of(period))`；`source` 命中取值域外 ⇒ 插入前拒绝；
  由**新采集写入路径**产生的行 `metric_date IS NULL` 的行数恒 **0**（回退只作用于历史行，见实体 4）。

### 3. 日级回流窗口（DailyCoverageWindow，派生视图；复用 019 口径）

由 `core/calibration/periods.py` 的 `coverage_window(...)` 计算（**不 import `core.billing`**，纯函数化复用）；
**键集以 `contracts/daily-ingest.md` C9 为权威**（下表与 C9 逐键一致，本文件不另立取舍）：

```
{end, period_days, window_semantics, attribution_based: true,
 covered_days, covered_dates[], gaps[{from,to,days}], max_gap_days, continuous,
 min_window_days, gap_tolerance_days, meets, coverage_shortfall_days, gap_shortfall_days, reasons[],
 days[{metric_date, collected_at, platform_timestamp, source, campaign_id, period}],
 legacy_single_snapshots, attribution_missing_anchors, evidence_claim, note}
```

- **无 `start` 键**（按 C9 取舍）：窗口起始端点由 `days[]` 的最小归属日派生，
  `covered_dates[0] == min(days[].metric_date)`；`end` 为窗口端点（判定的右端，半开语义见 C2）。
- **按归属日聚合**：`days[]` 的排序键与去重键都是 `metric_date`（**不是** `collected_at`）；
  `covered_days` **只计 `source == real`** 的归属日（与 `core/billing/runlog.py:263` 的 `covered_dates` 同构）。
- **判定的唯一实现（U-01）**：`coverage_window` 是"覆盖 ∧ 连续"（`covered_days` / `max_gap_days` / `continuous` /
  `meets` / `gaps` / `reasons`）的**唯一实现**；`agents/promo/daily.py` 的 `daily_coverage` **必须委托**它，
  `agents/promo/` 内**不得**自算 `meets` 或 `max_gap_days`（静态断言见 C9）。
- **口径来源**：下限/容差取两形态的 `budget.runs.min_window_days / gap_tolerance_days`（`configs/shortdrama.yaml:600-602`、
  `configs/movie.yaml` 同段；与 FR-002 明文"同构"）。**父级裁决的取值**：短剧态 `min_window_days: 14`
  （立项书 G4 原文"短剧线真实数据回流 ≥2 周"，`docs/三期立项书.md:166`）、电影态 **7**；
  `gap_tolerance_days` **保持现值**（短剧态现为 `0`）并**留在开放问题**（起算前后是否留余量需运营与制片侧给出）；
  缺口**逐段如实列出**、**禁止插值补零**。
- **关系**：**复用**（019 的 `window_coverage` 是文件账本读取版，本处是其日期集合输入版；两版输出键名一致，
  便于对照）。**不新增落盘文件**（与 `ops/billing.py runs` 同构：按需计算的派生视图 + CLI 输出 + 退出码）。

### 4. 平台锚点（AnchorScore，复用 010 并**扩展一个字段**）

`core/calibration/models.py:70` 的 `AnchorScore`（`:71-81` 为字段区）新增：

| 字段 | 类型 | 约束 | 来源 | 可空 |
| --- | --- | --- | --- | --- |
| `metric_date` | `str \| None`（**末位可选**，默认 `None`） | 非空时必须是合法 ISO `YYYY-MM-DD`（`__post_init__` 校验） | 新采集：`agents/promo/anchors.py:30` `collect_platform_anchors` 从 `snapshot["metric_date"]` 读；历史：`None` | **是**（仅历史行/仅 `human_blind` 行） |
| `collected_at`（**非本表列**，跨件承载） | Float | `> 0` 的采集墙钟（`time.time()`） | **不在 `calibration_anchors`**：由 `promo_daily_metrics.collected_at` + 覆盖视图 `days[].collected_at` 承载 | 否 |

- **FR-004 的"两项标识"与其承载（跨件登记，U-05）**：FR-004 要求锚点携带**归属日**与**采集墙钟**两项标识。
  本设计的承载分工是：**归属日**落锚点行（`calibration_anchors.metric_date`，见上表第一行）；
  **采集墙钟**落 `promo_daily_metrics.collected_at`（该活动该周期那一次采集的墙钟）并在覆盖视图 `days[].collected_at`
  中可见——**锚点行不新增列**（不把 `collected_at` 复制进 `calibration_anchors`，避免同一事实两处漂移）。
  两项标识因此**可联合检索**：`calibration_anchors.metric_date`（归属日）
  ↔ `promo_daily_metrics.(campaign_id, period, collected_at)`（周期 + 采集墙钟），
  连接键 = `(node_id/period)`；契约面见 `contracts/daily-ingest.md` C7（锚点侧）与 C9（视图侧）。

- **形态（父级裁决）**：保持**末位可选** `metric_date: str | None = None`——为兼容
  `agents/promo/evaluators/platform_metrics.py:46` 的**历史 dict 重建**（该处从 `raw.get(...)` 重建快照，
  历史落盘 payload 无该键 ⇒ 取 `None`，不得抛构造期异常）。
- `metric_date` **只对 `source == platform_truth` 有语义**（= 平台指标归属日）；`human_blind` 行写 `NULL`
  （人评的"归属日"即评价发生日，已由 `created_at` 承载，不另列——**不发明**第二套语义）。
- `created_at` 语义**不变**：platform_truth 行 = 平台时间戳（`agents/promo/anchors.py:22` 的既有口径），
  人评行 = 录入墙钟。**归属日 ≠ `created_at`**（迟到/回补时必然不同）。
- **关系**：**扩展**（DB 侧另有 `core/calibration/db.py:23` 的表定义须同步加列，见"迁移件"）。
- **不变量（回退只作用于历史行；新采集的失败点在写入路径，不在数据类）**：
  ① **新采集写入路径**（两个适配器的采集出口 `agents/promo/platform/simulated.py:94` 与
  `agents/promo/platform/http_real.py:194`/`:208`，以及 `agents/promo/daily.py` 的回流写入）产生
  `metric_date is None` 的次数恒 **0**（缺失 ⇒ 显式失败，见 C7）；
  ② `source == platform_truth ∧ metric_date IS NULL ⇒ created_at 的日期 < required_since`（历史行回退，报告登记计数）；
  ③ 锚点写入后 `UPDATE`/`DELETE` 恒被拒（既有触发器，`core/calibration/db.py:47-73`）。

### 5. 周期物化快照（SnapshotMaterialization，扩展 010 产物语义）

- **主键 =（评估器, 周期）**，路径形状**不变**：`snapshots/{agent_id}/{evaluator_id}/{period}.json`
  （`core/calibration/ledger.py:84` 写出、`core/calibration/drift_metrics.py:140-142` `snapshot_path` 只读）。
  同周期多轮**不产生第二份路径**——快照是"周期物化"（内容 = 该周期**全部**锚点的分布），不是"某一轮的产物"。
- payload 既有键逐字保留（`agent_id`/`evaluator_id`/`period`/`samples`/`bucket_width`/`buckets`/`quantiles`，
  `core/calibration/ledger.py:75-83`），**新增** `period_days` 与 `window_semantics`（口径自描述）。
- **指纹（签名与稳定性，父级裁决）**：`core/calibration/ledger.py`
  `def snapshot_fingerprint(payload: Mapping) -> str` = BLAKE3(canonical JSON：
  `json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 的 UTF-8 字节)。
  **稳定性定义**：① 对**同一内容**恒等——与缩进、键序、文件写入形态无关（以 canonical 化后的字节为唯一输入）；
  ② 只依赖 payload 的**语义内容**，不依赖时间、路径、进程、随机数（同内容重复物化 ⇒ 同指纹）；
  ③ payload 任一键/值变化 ⇒ 指纹变化（含新增 `period_days` / `window_semantics` 键）。
  **进两处产物**：台账行 `snapshot_fingerprint`（C3）与漂移记录 `snapshot_fingerprint`（C4）。
  **不写进快照文件**（避免自指），由写入方与读取方各自重算 ⇒ 可直接比对（指纹属"快照/台账"关注点，
  故落 `ledger.py`，**不**进 `periods.py`）。
- **物化回报**：`materialize_anchor_snapshots(...) -> list[SnapshotMaterialization]`，
  `SnapshotMaterialization = {path, evaluator_id, period, anchor_count, snapshot_fingerprint}`；
  `core/calibration/ledger.py:55` 的 `write_anchor_snapshots(...) -> list[Path]` 保留为**薄封装**
  （既有读取点 `ops/demo_web.py:284`、`ops/demo_judge_drift.py:129`、`tests/conftest.py:1911` 不变）。
- **静默改写次数恒 0**：内容变化（迟到/回补的锚点后到）**必须**由台账行登记（见实体 6）；
  同一内容重复物化 ⇒ 幂等（零字节写入、零新增台账行）。
- **不变量**：`fingerprint(on_disk) == 该 (evaluator_id, period) 最后一条台账行的 snapshot_fingerprint`；
  `on_disk.samples == 该台账行的 anchor_count`；`on_disk.period == 台账行的 period`。

### 6. 台账行（BiasRecord，扩展 010 模型）

`core/calibration/models.py:163` 的 `BiasRecord` 既有字段（`evaluator_key`/`period`/`samples`/`mean_shift`/
`pearson_r`/`kendall_tau`/`note`）**逐字保留**，新增（全部可空，历史行为 `None`）：

| 字段 | 类型 | 语义 | 可空 |
| --- | --- | --- | --- |
| `period_days` | `int \| None` | 该行所用 cadence（= `cadence_of(period)`） | 是（历史行） |
| `window_semantics` | `str \| None` | `half_open`；历史行缺失 ⇒ 读取端用 `unspecified` | 是（历史行） |
| `round_id` | `str \| None` | 写出该行的轮次（同周期多轮可回溯到轮） | 是（历史行） |
| `anchor_count` | `int \| None` | **快照物化的自描述指针**：该周期该评估器计入分布的锚点数 | 是（历史行） |
| `snapshot_fingerprint` | `str \| None` | 所物化快照的指纹（与 `anchor_count` 配对定位"读的是哪一份"） | 是（历史行） |

- **追加纪律不变**：`core/calibration/ledger.py:25` `append_ledger` 只追加、既有行永不修改（append-only）；
  **同一周期多轮的台账行并留存、零覆盖**（"后写覆盖前写"次数恒 0）。
- **关系**：**扩展**。生产者 `core/calibration/bias.py:39` `compute_bias` **不改签名**，由 `close_round` 在
  `append_ledger` 前用 `with_provenance(record, ...)`（`core/calibration/ledger.py` 新增的纯函数）补全。
- **不变量**：`anchor_count == samples`（同一行的两个字段恒等；机检 `anchor_count is None or anchor_count == samples`）；
  `snapshot_fingerprint` 非空时必须是 64 位小写十六进制；新写入行 `window_semantics == "half_open"` 且
  `period_days == cadence_of(period)`。

### 7. 信度报告（ReliabilityReport，扩展 010 产物）

- **路径规则（单点）**：`report_path(data_dir, period, run_id) -> Path`：
  `run_id` 给定 ⇒ `reports/{period}-{run_id}.json`（轮标识 = 轮次 `round_id`，uuid7 时间有序）；
  `run_id is None` ⇒ `reports/{period}.json`（**兼容别名**，仅当文件不存在时写）。
- **零覆盖**：任何已存在路径的**改写**一律拒绝（同内容 ⇒ 幂等返回）；故"同一周期多轮的后写覆盖前写"次数恒 0，
  且历史 `reports/{period}.json` 与既有读取点（`core/calibration/drift_report.py:98`、
  `web/queries.py:842`、`web/parity.py:45`）**仍可读**。
- **读取口径单点**：`latest_report_path(data_dir, period) -> Path | None`（优先取 `reports/{period}-*.json` 中排序末者，
  无则由 `reports/{period}.json` 回退；两者皆无 ⇒ `None`，不静默取空）。`core/calibration/drift_report.py:98` 与
  `web/queries.py:831-838` 的读取点改用该函数（周级单轮形态下两者取同一份 ⇒ 面板呈现不变）。
- payload 既有键逐字保留（`period`/`agents`/`target`/`alerts`），新增顶层
  `period_days` / `window_semantics` / `window_semantics_change_date` / `run_id` / `window{start,end,period_days}` / `note`。
  `note` 在**窗口跨口径变更日**时必须显式标注该变更日（见 C5）。
- `build_report(data_dir, period, *, target, window_semantics, window_semantics_change_date, run_id=None)`
  ——**两个新增必填关键字参数**（`window_semantics` / `window_semantics_change_date`，口径与变更日**必须来自形态配置**）
  + **一个可选参数** `run_id`（默认 `None` = 兼容别名路径 `reports/{period}.json`；给定轮标识则走
  `reports/{period}-{run_id}.json`）。机检：缺 `target` / `window_semantics` / `window_semantics_change_date`
  ⇒ `TypeError`（必填在语法层面即失败）；不传 `run_id` ⇒ 走兼容别名路径且**不新增**轮次产物。

### 8. 漂移产物的窗口口径字段（DriftMetrics / DriftConfig，扩展 012）

- `core/calibration/drift_config.py:96` 的 `DriftConfig` **新增** `period_days: int`（取自 `calibration.period_days`，
  缺项即报错）；`:185` `thresholds_snapshot()` 新增 `window_unit`（`{1:"day",7:"week"}[period_days]`）与 `period_days`。
- `core/calibration/drift_metrics.py:116` `metric_hash` 的 payload 新增 `period_days`
  ⇒ 日级与周级是**两个不同的口径版本**（`detector_version` 不同），这正是"窗口同量纲"的机检落点；
  `:135` `detector_version(cfg)` 签名不变。
- `core/calibration/drift_models.py:179` 的 `DriftMetrics` **新增** `snapshot_fingerprint: str | None = None`
  （判定类 `normal|drift` 必非空；`no_data` 必为 `None`）；**所读快照的锚点数 = 既有 `samples`**
  （语义澄清为"该周期快照所依据的锚点数"，不再新增重复字段）。
- `:331` `write_record` 的"同内容幂等 / 内容不同即拒绝改写"纪律**不变**；`no_data`（无快照 ⇒ 该周期不入窗口、
  缺口如实报）语义**不变**（`:145-152` `read_snapshot` 返回 `None`）。
- **兼容**：已落盘漂移记录零回改；缺失 `snapshot_fingerprint`（历史记录）的读取端按 `None` 处理并如实标注
  （不冒充"已记账"）；缺失 `thresholds.window_unit` 的历史记录按 `week` 解释并在报表 `note` 标注。

### 9. 投放渠道登记与分档额度（ChannelSpec / BudgetTier，复用 019 并扩展）

- **命名空间**：`budget.channels.<id>.tiers.<环节>`（现为扁平 `budget.tiers.<环节>`，`core/billing/budget.py:275`
  的 `tiers: Mapping[str, BudgetTier]`、解析点 `:1063` `_parse_tiers`）；`core/billing/budget.py:357` `sole_channel`
  的"恰好一个渠道"硬拒绝改为"**按配置声明的渠道集合分派额度**"（调用点 `:433`、`ops/billing.py:104` 同步）。
- **分派接口（父级裁决定名，弃 `channel_for_slot`）**：`declared_channels(cfg) -> tuple[ChannelSpec, ...]`
  （配置声明的渠道集合，取代 `sole_channel` 的单渠道断言）与
  `channel_for_adapter(cfg, adapter_id) -> ChannelSpec`（按 `channels.<id>.adapter` 取值把某装配面映射到其渠道；
  未声明的 `adapter_id` ⇒ 报错，**不猜**）。`channels.<id>.adapter` 的**取值域不变**——既有 `pilot_llm`
  等取值**原样保留、不重命名**；投放渠道以**新键**登记，不改既有键名与取值。
- **实体字段**：渠道 = `channel_id` / `adapter`（真实调用面装配引用，`:255` `ChannelSpec`）/ `bill`（格式 id + 来源形态）；
  分档额度 = `tier_id`（= 环节 id）/ `limit_usd` / `window{kind: run|day|period}` / `on_exhausted: refuse` /
  `note`；账本键 = `(渠道, 环节, 窗口实例)`。
- **两形态的登记集合可不同（父级裁决）**：`configs/movie.yaml` **不登记** media 投放渠道，
  `configs/shortdrama.yaml` 登记；"两形态均须声明"只指**新增参数键的 schema 齐备**（都能加载、缺键即报错），
  两形态都**必须**能装配通过。
- **不变量**：同一档位**不跨渠道串用**（账本与告警按渠道分目录，`billing/{channel}/…`）；
  旧扁平形状可读且**显式归一**到其声明的渠道（歧义即报错，不静默误判）；
  `channel_for_adapter(cfg, a)` 对未声明的 `a` 恒抛错（不回落、不猜测）。
- **契约归** `contracts/channel-budget.md`（C11~C14，**他人撰写**）；本文件不重复其机检断言。

### 10. 校准结论迁移件（CalibrationTransfer，新增）

- **实体字段**：`transfer_id` / `source_form`（来源形态）/ `evaluator_key`（`id@version`）/ `period` /
  `samples` / `source_ref`（来源件引用：台账行或报告路径）/ `transfer_basis`（迁移口径）/
  `comparability`（可比性条件 + 判定 `transferable | not_transferable` + 原因）/ `status`
  （`pending → confirmed | shelved`，与 `core/calibration/models.py:60` 的 `ProposalStatus` 同构）/
  `confirmed_by` / `confirmed_at`。
- **append-only**：落盘 `calibration/transfers/{transfer_id}.json`（镜像
  `core/calibration/refit.py:141-142` 的 `proposals/{proposal_id}.json`；同 id 重产拒绝、改写拒绝）。
- **不变量**：`comparability.verdict == not_transferable ⇒ status != confirmed`（不可迁移的**误迁移次数恒 0**）；
  迁移件**不改动**任何既有节点的 `eval_breakdown` 与得分（逐字节一致）；
  短剧线数字**不得**出现在电影线证据面（机检恒 0）。
- **本特性无 DDL**（文件化 append-only）。**契约归** `contracts/transfer-ops.md`（C15~C18，**他人撰写**）。

## 状态机

- **日级回流记录**：`写入 →（唯一键 (campaign_id, period)）→ 冻结`；同键重复 ⇒ **拒绝**（零变更，不是覆盖）。
- **投放活动（`promo_campaigns`）**：`delivered →（首日回流）→ ingested →（后续日回流）→ ingested`（`ingested` **不再**是终态；
  `node_id` 与 `metrics` 随每期更新为"最近一次"），`failed` 行不参与回流（`agents/promo/ingest.py:139` 既有口径）。
- **快照物化**：`无 → 物化 →（内容变化）→ 再物化并新台账行`；**同一内容重复物化 = 幂等**（无新增行、无字节写入）。
- **信度报告**：`首轮落兼容别名 reports/{period}.json → 后续轮各落 reports/{period}-{run_id}.json`；已存在路径的改写恒被拒。
- **锚点**：`写入即冻结`；重复回流（同活动 + 同周期 + 同渠道 + 同轮）⇒ 唯一键拒绝；
  真值确需更新时**必须**走升版/换锚点（`DriftAction.REANCHOR`），**禁止**就地改锚点（不在本特性范围）。
- **迁移件**：`pending → confirmed | shelved`（终态不可逆，镜像 `core/calibration/models.py:222-240`）。

## 不变量总表（全部可机检）

- **I-1 量纲往返**：对任一落盘周期标签 `p`，`cadence_of(p) ∈ {1,7}` 且 `period_label(period_start(p, cadence_of(p)), cadence_of(p)) == p`。
- **I-2 标签即 cadence**：台账行 / 快照 payload / 报告 payload / 漂移 `thresholds` 的 `period_days` 均 `== cadence_of(period)`，
  且 `== 形态配置声明的 calibration.period_days`（不一致即红）。
- **I-3 半开窗口**：`period_window(s, n) == (s, s + n 天)`，即 `end - start == period_days`；
  `period_days + 1` 天窗口出现次数恒 **0**。
- **I-4 零覆盖（外环产物层）**：任一已存在路径的改写次数恒 **0**；
  同一 `(agent_id, period)` 的多轮报告文件数 `== 轮数`（兼容别名不计入）。
- **I-5 静默改写恒 0（快照层）**：任一时刻 `fingerprint(快照) == 该 (evaluator_id, period) 末条台账行的 snapshot_fingerprint`。
- **I-6 唯一性键**：`promo_daily_metrics` 中 `(campaign_id, period)` 重复行数恒 **0**；
  同一活动跨多日的行数 `== 不同归属日数`；同一 (活动, 周期) 的第二次回流 100% 幂等拒绝。
- **I-7 锚点冻结**：`calibration_anchors` 的 `UPDATE`/`DELETE` 成功后行数变化恒 **0**（触发器）；
  **新采集写入路径**（`agents/promo/platform/simulated.py:94`、`agents/promo/platform/http_real.py:194`/`:208`
  的采集出口 + `agents/promo/daily.py` 的回流写入）产生 `metric_date IS NULL` 的次数恒 **0**。
- **I-8 归属日不回退**：`source == platform_truth ∧ metric_date IS NULL ∧ created_at 的日期 ≥ required_since` 的行数恒 **0**；
  历史行的归属日缺失数**只在**报告里如实登记（`attribution_missing_anchors`，可与 DB 查询对齐，不冒充已标定）。
- **I-9 三时间并列**：日级覆盖视图每条 `days[]` 条目同时含 `metric_date` / `collected_at` / `platform_timestamp` 三键（齐备率 100%）。
- **I-10 覆盖只计真实**：`covered_days == |{d ∈ days : source(d) == "real"}|`；
  插值/补零/以旧值顶替出现次数恒 **0**；`meets ⇒ covered_days ≥ min_window_days ∧ max_gap_days ≤ gap_tolerance_days`。
- **I-11 来源不冒充**：`source` 取值域外写入次数恒 **0**（取值域唯一属主 `core/billing/runlog.py:28`）；
  "模拟被标为真实"次数恒 **0**；`source=simulated` 的归属日**不**计入 `covered_days`。
- **I-12 漂移自描述**：判定类漂移记录的 `snapshot_fingerprint` 非空比率 100%，`samples == 所读快照的 samples`。
- **I-13 迁移件 immutable**：迁移件改写/省略尝试 100% 被拒；`not_transferable ⇒ 非 confirmed`；节点 `eval_breakdown` 逐字节不变。
- **I-14 节点标识确定性**：每周期节点 id `== daily_node_id(material_id, period) == f"{material_id}-node@{period}"`
  （同一 `(material_id, period)` 恒等；历史 `f"{material_id}-node"` 节点**不被改写**、不被重命名）；
  `(material_id, period)` 不同的两个节点 id 必不相同。
- **I-15 渠道分派确定性**：`declared_channels(cfg)` 返回配置声明的**全部**渠道；
  `channel_for_adapter(cfg, a)` 对未声明的 `a` 恒抛错（不回落、不猜）；`channels.<id>.adapter` 取值域**不变**
  （既有 `pilot_llm` 等原样保留）。

## 迁移件（本特性唯一的 DDL）

`ops/migrations/versions/0011_daily_feedback.py`：

- `revision = "0011_daily_feedback"`，`down_revision = "0010_dev_jobs"`（当前头，
  `ops/migrations/versions/0010_dev_jobs.py:22-23`）。
- 步骤 ①：`ALTER TABLE calibration_anchors ADD COLUMN metric_date TEXT NULL`（**无** `server_default` ⇒ 历史行恒 `NULL`，
  触发器只管 `UPDATE`/`DELETE`，故 `ADD COLUMN` **不动任何既有行** ⇒ 历史锚点零回改）；
  同步更新 `core/calibration/db.py:23-42` 的表定义（新增列 + `__post_init__` 校验的镜像说明），
  `core/calibration/anchors.py:25` `insert_anchor` / `:47` `load_anchors` 两处读写补 `metric_date`。
- 步骤 ②：`CREATE TABLE promo_daily_metrics`（列见实体 2）+ 唯一约束 `uq_promo_daily_campaign_period` +
  **INSERT-only 触发器**（镜像 `core/calibration/db.py:47-73` 的 SQLite `RAISE(ABORT)` 与 PG `reject_*_mutation()`
  两版），并回收应用账号的 `UPDATE`/`DELETE`（镜像 `0004` 的授权做法，`ops/migrations/versions/0004_calibration_anchors.py:17-18`）。
- **兼容规则（零回改承诺）**：历史行不回改（`metric_date` 恒 `NULL`，永不回填）；缺失字段的**回退口径**只对
  `created_at` 的日期 < `promo.attribution_date_required_since` 的历史行生效（按 `created_at` 的日期回退 + 报告登记计数）；
  新采集写入路径走回退的次数恒 **0**；**不改** `promo_campaigns` 既有唯一键 `(round_id, material_id)`
  （`agents/promo/db.py:47`），`promo_campaigns` **保持无触发器**（`agents/promo/db.py:1-5`），
  其表结构**零 DDL**（仅 `node_id`/`metrics` 两列的**语义**与注释更新）。
- 若 020 的其他契约面（channel-budget / transfer-ops）需要 DDL，**必须**取 `0012_*` 及之后的修订号，不得与本件并行分叉。

### 与 `plan.md` / `research.md` 的对齐（并行写就，逐条登记）

- **一致**：迁移号 `0011_*`（`specs/020-shortdrama-real-feedback/plan.md:28` 与同文件 `:136`、同目录
  `research.md` 决策 9）、`down_revision = "0010_dev_jobs"`、
  只加**可空**归属日列 + 历史行 `NULL` 不回改 + 读取侧按"归属日缺失锚点数"如实登记；`periods.py` 的
  `period_label / period_start / period_window / period_regex` 四函数（+ 本文件追加的 `cadence_of` / `coverage_window`，
  同模块，见实体 1）与 `window_semantics` / `half_open`、台账行 `anchor_count` + `snapshot_fingerprint`、
  报告文件名带轮标识——均与本文件一致。
- **DDL 承载面（已由父裁决定为两步方案）**：`0011_daily_feedback` = 步骤 ①（`calibration_anchors` 增可空 `metric_date` 列）
  + 步骤 ②（`CREATE TABLE promo_daily_metrics`，唯一键 **（campaign_id, period）** + INSERT-only 触发器）；
  `agents/promo/db.py:47` 的 `(round_id, material_id)` **不改**，`promo_campaigns` **保持无触发器**。
  与 `specs/020-shortdrama-real-feedback/research.md:183-187` 的"运营表唯一键的迁移段"一致；`plan.md` 侧同步（本文件不代改）。
- **历史行的回退解释面（与 `research.md` 同义，已对齐）**：`specs/020-shortdrama-real-feedback/research.md:167-170` 把回退描述为"历史行缺少**周期键** ⇒ 按其 `created_at`
  的日期回退解释"；本文件把同一语义落在"**无日级记录**的历史行（`promo_campaigns.metrics.platform_metrics` 非空
  且 `promo_daily_metrics` 无对应行）⇒ 归属日未标定、按 `created_at` 回退并按 `legacy_single_snapshot` 登记，
  **不**视为已被采集"——语义逐条一致（不回改、不阻止首次真正的日级采集）。
- **归属日字段名与形态（已定名）**：`specs/020-shortdrama-real-feedback/research.md` 决策 5 明确"键名由 `contracts/daily-ingest.md` C7 与 `data-model.md` 定名"，
  本文件据此钉死为 **`metric_date`**（`agents/promo/platform/base.py:71` 为定义处、`agents/promo/anchors.py:30` 为读取处）
  且为**末位可选** `str | None = None`；plan/research 未另定名，实现**必须**逐字采用。

## 磁盘布局（`--data-dir` 根，默认 `calibration/`）

```
calibration/ledger/{agent_id}/{evaluator_id}.jsonl          # 台账（append-only；新增口径与快照溯源字段）
calibration/snapshots/{agent_id}/{evaluator_id}/{period}.json   # 周期物化快照（路径不变；period 为 cadence 派生标签）
calibration/reports/{period}.json                           # 兼容别名（仅首轮写一次，永不改写）
calibration/reports/{period}-{run_id}.json                  # 每轮报告（同周期多轮并留存、零覆盖）
calibration/rounds/{agent_id}/{round_id}.json               # 轮次 + 盲评清单（不变）
calibration/drift/metrics/{agent_id}/{evaluator_id}/{period}.json   # 漂移记录（新增快照指纹）
calibration/drift/reports/{period}.json                     # 漂移报表（不变）
calibration/proposals/{proposal_id}.json                    # 010 权重提案（不变）
calibration/transfers/{transfer_id}.json                    # 迁移件（新增，append-only；契约见 transfer-ops.md）
billing/{channel}/{tiers…,ledger.json,alerts.jsonl,runs/{date}.json}   # 019 账本（按渠道分目录；本特性不改形状）
```

- **零新增落盘文件**：日级覆盖窗口是**派生视图**（`ops/ingest_metrics.py` 的 CLI 输出 + 退出码，与
  `ops/billing.py runs` 同构），其可机检证据 = `promo_daily_metrics` 的 append-only 行 + 台账/报告。
- **禁止**写入其它特性目录与仓库权威配置；Web 只读不变（原则五）。
