# 契约：周期 cadence 派生与半开窗口（C1~C5）

> 对应规格 FR-003 / FR-005、SC-002、US1 场景 1、边界情况第 1/2 条、Clarifications 第 7/8/13 条。
> 实现落点：**新增** `core/calibration/periods.py`（业务无关，唯一新增 core 模块，不 import `core.billing`）
> + 修 `core/calibration/{rounds,selection,ledger,report,drift_config,drift_models,drift_metrics}.py`
> + `ops/calibrate.py`（另两处重复实现 `ops/dev.py:347-354`、`ops/screenplay.py:388-395` 收敛到同一函数）。
> 现状三处硬事实：周期标签一律取 ISO 周（`core/calibration/rounds.py:28`、调用点 `:69`；同标签进快照路径
> `core/calibration/ledger.py:84` 与报告路径 `core/calibration/report.py:55`，均 `write_text` 直写 ⇒ 同周期多轮互相覆盖）；
> 漂移恒定按周（`core/calibration/drift_metrics.py:53-54`，与 `configs/shortdrama.yaml:408` 的"3 天窗口"注释不符）；
> 窗口含首尾（`core/calibration/selection.py:28-35` 对 `period_end` 加一天 + `ops/calibrate.py:43` 的缺省
> `period_start = 今天 − period_days` ⇒ 缺省跨 `period_days + 1` 天）。
> 数据模型见 [../data-model.md](../data-model.md) 实体 1/5/6/7/8。

## C1 周期标签由 cadence 派生（与"一律取 ISO 周"脱钩，周级字节不变）

**目的**：把"日级"从配置数字变成**真的按日运转**：标签、路径、漂移窗口单位三处同时按 `calibration.period_days`
（cadence）派生，而**不动**周级任何既有字节。

**形状/接口**（`core/calibration/periods.py`，全部纯函数、无 IO、零业务概念）：

```python
WINDOW_SEMANTICS = "half_open"                 # 产物回写值（唯一取值）
UNSPECIFIED_WINDOW_SEMANTICS = "unspecified"   # 历史行缺失时的回退标注（不冒充 half_open）
SUPPORTED_CADENCES = (1, 7)                    # 取值域；其余值 ⇒ ValidationError「未支持的 cadence」
CADENCE_UNIT = {1: "day", 7: "week"}

def period_label(day: str | date, period_days: int) -> str: ...   # 1 ⇒ "YYYY-MM-DD"；7 ⇒ "YYYY-Www"（ISO 周）
def period_start(label: str, period_days: int) -> date: ...       # 标签 → 窗口首日；非法标签/不存在 ISO 周 ⇒ ValidationError
def period_window(start_day: str | date, period_days: int) -> tuple[date, date]: ...   # 半开 [start, start+period_days)
def period_regex(period_days: int) -> re.Pattern[str]: ...        # 1 ⇒ ^\d{4}-\d{2}-\d{2}$；7 ⇒ ^(\d{4})-W(\d{2})$
def cadence_of(label: str) -> int: ...                            # 标签形态 ⇒ {1,7}（双射）；非法 ⇒ ValidationError
```

标签必须**贯穿四处**（Rule A 的硬要求，四处都取同一个 `period` 值）：

1. 轮次收口：`core/calibration/rounds.py:69` `period = iso_week_label(round_.period_end)` ⇒
   `period = period_label(round_.period_end, config.period_days)`；
   `ops/calibrate.py:190`（propose 重建本轮证据）同步改，**必须**与收口口径同一函数、同一 cadence。
2. 台账与快照：`core/calibration/ledger.py:84` 的 `f"{period}.json"`（`BiasRecord.period` 同值）。
3. 信度报告：`core/calibration/report.py:55` 的 `f"{period}.json"`。
4. 漂移：`core/calibration/drift_metrics.py:53` `_PERIOD_RE` / `:54` `_WEEK` / `:57` `_period_start` /
   `:68` `_period_label` / `:73` `missing_periods` ⇒ 全部改走 `period_regex(cfg.period_days)` /
   `period_start(label, cfg.period_days)` / `period_label(day, cfg.period_days)` /
   `period_window(...)`（`cfg: DriftConfig` 新增 `period_days`，见 C4）。

**机检断言**（可直接写成测试断言）：

- `period_label("2026-09-25", 1) == "2026-09-25"`；`period_label("2026-09-20", 7) == "2026-W39"`。
- `period_regex(7).pattern == r"^(\d{4})-W(\d{2})$"`（逐字节等于 `core/calibration/drift_metrics.py:53` 的现值）；
  `period_regex(1).fullmatch("2026-09-25")` 为真、`fullmatch("2026-W39")` 为假。
- 往返恒等：对 `n ∈ {1,7}` 与任一合法日期 `d`，`period_label(period_start(period_label(d, n), n), n) == period_label(d, n)`；
  `cadence_of(period_label(d, n)) == n`。
- **运转断言（非配置数字断言）**：`configs/shortdrama.yaml:399` 的 `period_days: 1` 生效后，同一日级周期内
  两轮 `close_round` 的台账行数 `== 2`、报告文件数 `== 2`（路径不同）、且两者都保留；
  `tests/unit/test_form_switch.py:188-194` 的 `test_外环日级` 由"只断言配置数字"**扩展**为断言
  cadence 派生标签（日级 ⇒ 日期形态）、漂移窗口单位（日级 = 天）与同周期零覆盖（既有数字断言**保留**，不得删除）。
- 周级零变化：以 `configs/movie.yaml:392` 的 `period_days: 7` 跑既有周级夹具，
  `period_label(period_end, 7)` 与旧 `iso_week_label(period_end)` 对同一输入**逐字节相同**，
  产物路径与文件名逐字节相同。

**反例**（必须被拒/必须计数为 0）：

- `period_days = 2 / 3 / 30 / 0` ⇒ `ValidationError`「未支持的 cadence」（**不发明**"月""双周"等 cadence；
  取值域只有 `{1, 7}`；`0` 另在 `core/calibration/config.py:17` 的 `≥ 1` 校验就已拒绝）。
- **不存在**的 ISO 周标签（`"2026-W53"`）⇒ `ValidationError`（不静默解析）。
- 日级标签喂给周级派生（`period_start("2026-09-25", 7)`）⇒ `ValidationError`（标签与 cadence 不匹配即报错，不猜测）。
- "日级已运转"的**证据**不得是"配置里 `period_days == 1`"（`tests/unit/test_form_switch.py:193` 今天的唯一断言）；
  只断言配置数字而产物仍是 ISO 周 ⇒ 视为**未实现**。
- 同一 ISO 周内两轮日级运转落**同一**报告/快照路径 ⇒ 机检红（"后写覆盖前写"次数必须恒 0）。

**兼容规则（零回改承诺）**：

- 周级标签语义**逐字节沿用** `core/calibration/rounds.py:28` `iso_week_label`；该函数**保留**为
  `period_label(day, 7)` 的薄封装，`ops/dev.py:391`、`ops/screenplay.py:410` 的既有读取点**不改**。
- 既有周级产物（`calibration/{ledger,snapshots,reports,drift}/**`）**零回改**：不重写、不迁移、不重命名；
  新落盘件只允许**新增键**（`period_days` / `window_semantics` / `snapshot_fingerprint` 等），既有键名与取值**逐字不变**。
- 010/012 的既有契约断言（`tests/contract/test_calibration_contracts.py:1-10` 的端到端机检）**不减项**；
  因标签量纲与产物路径变红的既有夹具（`tests/unit/test_anchor_snapshots.py:16` 的 `_PERIOD = "2026-W39"`、
  `tests/unit/test_drift_detect.py:32` 的 `_CURRENT = "2026-W39"` 与 `:120-133` 的窗口用例）**按扩展更新，不得放宽断言**。

## C2 周期窗口统一半开区间（禁止 `period_days + 1` 天）

**目的**：把"缺省跨 `period_days + 1` 天"的含首尾口径统一为半开区间，并让窗口长度**恒等于 cadence**。

**形状/接口**：

```python
# core/calibration/periods.py
def period_window(start_day, period_days) -> tuple[date, date]       # == (start, start + period_days 天)；半开
# core/calibration/selection.py（改造 _period_window，:28）
def _period_window(period_start: str, period_end: str, period_days: int) -> tuple[float, float]:
    """period_end 是**含首尾口径的窗口末日**；window = period_window(period_start, period_days) 的秒级半开区间
    [start_ts, end_ts)；不一致（(period_end − period_start) + 1 天 != period_days）⇒ ValidationError。"""
# core/calibration/selection.py（:82 build_blind_list 增必填 period_days，透传给 _period_window）
def build_blind_list(store, *, agent_id, period_start, period_end, top_k, data_dir, period_days, ...) -> CalibrationRound: ...
# ops/calibrate.py（:41-44 缺省修正）
period_end = args.period_end or 今天
period_start = args.period_start or (今天 − timedelta(days=config.period_days - 1))   # 含首尾跨 period_days 天
```

**机检断言**：

- `end - start == period_days` 恒成立（`period_window` 的返回值；按日历日）：
  `period_window(date(2026,9,14), 7) == (date(2026,9,14), date(2026,9,21))`；
  `period_window(date(2026,9,25), 1) == (date(2026,9,25), date(2026,9,26))`。
- **缺省窗口长度 = cadence**：令 `period_end = 今天`、`period_start` 取缺省，则 `(period_end − period_start).days + 1 == period_days`
  （周级 = 7 天、日级 = 1 天）；`period_days + 1` 天窗口出现次数恒 **0**。
- **既有周级调用点字节不变**：`build_blind_list(period_start="2026-09-14", period_end="2026-09-20", period_days=7)`
  产出的 `(start_ts, end_ts)` 与改造前 `_period_window` 的返回值**逐字节相同**
  （旧式 `end = period_end + 1 天` = `2026-09-21T00:00:00Z`，新式 `start + 7 天` = 同值）
  ⇒ `tests/unit/test_blind_selection.py:18-19` / `tests/unit/test_close_round.py:46-50` /
  `tests/unit/test_anchor_intake.py:33-37` / `tests/unit/test_editing_replay.py:211-214` 的既有夹具
  （周级 7 天含首尾）**判定结果不变**，只须补 `period_days=7` 参数。
- `build_blind_list(..., period_days=)` **必填**；缺参即 `TypeError`（不取码内默认）。

**反例**：

- `period_start="2026-09-14", period_end="2026-09-21", period_days=7`（8 天跨度）⇒ `ValidationError`
  （窗口跨度与 cadence 不符，**禁止**静默按任一端口径截断）。
- `period_start > period_end` ⇒ `ValidationError`（非空窗口才可判）。
- 非 ISO 日期（`"2026/09/14"`）⇒ `ValidationError`（沿用 `core/calibration/selection.py:34` 的既报错语义）。
- 断言"窗口 = `[start, end]` 含首尾"的写法（例如 `end_ts == parse(period_end) + 1 天` 由调用方自己算）
  一律视为**绕过**：窗口端点只能来自 `period_window`。

**兼容规则**：

- 含首尾口径**只**作为**入参语义**保留（`period_end` 仍是"窗口末日"这个人工输入的日期），窗口端点一律由
  `period_window` 计算；故 CLI 的参数名与语义不变（`ops/calibrate.py` 的 `--period-start/--period-end` 不动），
  只修**缺省值**与实现。
- 既有周级窗口判定（含 `core/calibration/selection.py:113` 的 `start_ts <= node.created_at < end_ts` 过滤）
  对周级**零变化**；已落盘的 `rounds/*.json` 与由此产生的产物零回改。
- `ops/dev.py:347-354` 与 `ops/screenplay.py:388-395` 各有一份同名重复实现：**收敛**到
  `core/calibration/periods.py` 的同一函数（不得留第二份口径），二者产出的 `period_window` 元组与今天逐字节相同。

## C3 快照 = 周期物化（主键（评估器, 周期）+ 零覆盖 + 内容变化必记账）

**目的**：同周期多轮时快照不再"互相覆盖"，也不再"变了但无人知道"：
主键降到（评估器, 周期），路径形状不变，内容变化由台账行登记指纹与锚点数。

**形状/接口**：

```python
# core/calibration/ledger.py（新增；路径与既有 :84 逐字一致）
def snapshot_fingerprint(payload: Mapping) -> str:
    """快照指纹 = BLAKE3(canonical JSON of payload)。

    canonical 化口径：json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    的 UTF-8 字节。**稳定性定义**：① 对同一内容恒等（与缩进、键序、文件写入形态无关）；
    ② 只依赖 payload 的语义内容，不依赖时间/路径/进程/随机数（同内容重复物化 ⇒ 同指纹）；
    ③ payload 任一键或值变化 ⇒ 指纹变化（含新增 period_days / window_semantics 键）。
    **进两处产物**：台账行 snapshot_fingerprint 与漂移记录 snapshot_fingerprint。
    **不写进快照文件**（避免自指），写方与读方各自重算 ⇒ 可直接比对。
    """

@dataclass(frozen=True)
class SnapshotMaterialization:
    path: Path; agent_id: str; evaluator_id: str; period: str
    anchor_count: int; snapshot_fingerprint: str

def materialize_anchor_snapshots(data_dir, agent_id, period, pairs) -> list[SnapshotMaterialization]: ...
def write_anchor_snapshots(data_dir, agent_id, period, pairs) -> list[Path]:   # :55 保留为**薄封装**
def with_provenance(record: BiasRecord, *, period_days: int, window_semantics: str,
                    round_id: str, anchor_count: int, snapshot_fingerprint: str) -> BiasRecord: ...
```

注：`core/calibration/periods.py` 除 C1/C2 列出者外另有 `cadence_of(label) -> int`（标签形态 ⇒ cadence）与
`coverage_window(days, *, end, min_window_days, gap_tolerance_days) -> dict`（C9 的日级覆盖计算）两个纯函数——**同模块追加，不新增模块**。
两者的"为什么需要"见 [../data-model.md](../data-model.md) 实体 1；一句话：
`cadence_of` 让 `write_anchor_snapshots` / `append_ledger` 等**既有无 cadence 参数的调用点**不改签名即可把
`period_days` 写进产物（标签与 cadence 在 `{1,7}` 上双射，生产端仍传配置值），`coverage_window` 把 019 的
`core/billing/runlog.py:226-311` 口径纯函数化，避免 core 内新增 `calibration → billing` 耦合。

- 快照 payload 既有键**逐字保留**（`agent_id`/`evaluator_id`/`period`/`samples`/`bucket_width`/`buckets`/`quantiles`，
  见 `core/calibration/ledger.py:75-83`），**新增** `period_days` 与 `window_semantics`。
- cadence 由标签派生（`cadence_of(period)`）⇒ `write_anchor_snapshots` 的既有签名不变，
  `ops/demo_web.py:284`、`ops/demo_judge_drift.py:129`、`tests/conftest.py:1911` 的调用点零改动。
- 台账行（`core/calibration/models.py:163` 的 `BiasRecord`）新增 `period_days` / `window_semantics` / `round_id` /
  `anchor_count` / `snapshot_fingerprint` 五个可空字段；`core/calibration/bias.py:39` `compute_bias` **不改签名**，
  由 `core/calibration/rounds.py:74` 的 `append_ledger` 之前经 `with_provenance` 补全。
- 信度报告路径：`core/calibration/report.py` 新增
  `report_path(data_dir, period, run_id) -> Path` 与 `latest_report_path(data_dir, period) -> Path | None`；
  `build_report(data_dir, period, *, target, window_semantics, window_semantics_change_date, run_id=None) -> dict`。

**机检断言**：

- **路径形状不变**：`materialize_anchor_snapshots(...)[0].path == data_dir/"snapshots"/agent_id/evaluator_id/f"{period}.json"`
  == `core/calibration/drift_metrics.py:140-142` 的 `snapshot_path(...)`。
- **主键 =（评估器, 周期）**：同一周期多轮物化后，该目录下文件数 `== 该周期出现过快照的评估器数`
  （**不随轮数增长**；"同周期多轮零覆盖"由台账行与报告承担，不由快照文件承担）。
- **溯源配对**：`record.anchor_count == record.samples`（非 None 时）且 `record.anchor_count == on_disk.samples`；
  `record.snapshot_fingerprint == snapshot_fingerprint(on_disk)`。
- **静默改写恒 0**：内容变化（迟到/回补使 `samples`/`buckets`/`quantiles` 任一变化）⇒ **必有一条新台账行**携带新指纹；
  机检：遍历每 (evaluator_id, period)，disk 指纹与末行指纹不等 ⇒ 红（"内容变了但无人知道"）。
- **重复物化幂等**：同内容再物化 ⇒ 文件字节不变、台账无新增行、返回同一指纹。
- **同周期多轮零覆盖（外环产物层）**：同一 `(agent_id, period)` 下 `reports/{period}-*.json` 文件数 `== 轮数`；
  `reports/{period}.json`（兼容别名）写出后**永不改写**（mtime 与字节均不变）。

**反例**：

- 同周期第二轮直接 `write_text` 覆盖 `snapshots/{agent}/{evaluator}/{period}.json` 而**不**落台账行 ⇒ 红
  （违反"内容变化必须记账"）。
- 同周期第二轮覆盖 `reports/{period}.json` ⇒ 红（"后写覆盖前写"次数必须恒 0）；已存在路径的改写调用必须**抛错**，
  不得"覆盖成功"。
- 把快照当作"某一轮的产物"（例如按 `round_id` 另开 `snapshots/.../{period}-{round}.json`）⇒ 红
  （快照主键是（评估器, 周期），轮标识**不**进快照路径）。
- 在同一周期内为**同一内容**重写快照并追加台账行 ⇒ 红（幂等语义被破坏：新增行数必须为 0）。

**兼容规则**：

- `write_anchor_snapshots` 的**签名与返回类型不变**（`list[Path]`），既有调用点与夹具零改动；
  新代码走 `materialize_anchor_snapshots`（一个实现 + 一个兼容薄封装，**不得**留第二份实现）。
- 已落盘的 `snapshots/**`、`ledger/**`、`reports/**` **零回改**：不重写、不补指纹、不重命名；
  历史行缺失 `snapshot_fingerprint`/`anchor_count` ⇒ 读取端按 `None` 处理并**如实标注**（不冒充"已记账"）。
- 010 的 append-only 纪律（`core/calibration/ledger.py:25-33`）与"历史节点逐字节一致"机检
  （`tests/contract/test_calibration_contracts.py:1-10`）**并列成立**，不减项。

## C4 漂移读取口径（每周期一份快照 + 窗口同量纲 + 指纹/锚点数登记）

**目的**：012 的读取面（`core/calibration/drift_metrics.py`）适配"周期物化"语义与 cadence 同量纲，
并让"读的是哪一份快照"可追溯。

**形状/接口**：

```python
# core/calibration/drift_config.py
@dataclass(frozen=True)
class DriftConfig:                      # :96
    ...
    period_days: int                    # 新增（取自 calibration.period_days；缺项 ⇒ CalibrationConfigError）
    @property
    def window_unit(self) -> str: ...    # {1: "day", 7: "week"}[period_days]
    def thresholds_snapshot(self) -> dict: ...   # :185：新增 "window_unit" 与 "period_days"

# core/calibration/drift_metrics.py
def metric_hash(cfg: DriftConfig) -> str: ...    # :116：payload 新增 "period_days"（口径即版本）
def detector_version(cfg: DriftConfig) -> str: ...  # :135：签名不变
def read_snapshot(data_dir, agent_id, evaluator_id, period) -> dict | None: ...   # :145：语义不变（缺文件 ⇒ None）
def snapshot_path(data_dir, agent_id, evaluator_id, period) -> Path: ...          # :140：只读路径，不改

# core/calibration/drift_models.py
@dataclass(frozen=True)
class DriftMetrics:                     # :179
    ...
    snapshot_fingerprint: str | None = None   # 新增：所读快照指纹；判定类必非空、no_data 必为 None
```

- **每周期读一份**：一个 `(evaluator_id, period)` 只有一个快照路径（`snapshot_path`），读取该路径的**当前内容**
  = 该周期的**最后一次物化**；**缺文件** ⇒ `read_snapshot` 返回 `None` ⇒ 该周期**不参与**窗口、
  缺口如实报（沿用 `core/calibration/drift_metrics.py:395-398` 的 `no_data` 语义，
  同文件 `:155-161` `available_periods` 的枚举不变）。
- **同量纲**：`calibration.drift.window` 的单位 = `period_days` 的量纲（日级 `window: 3` = **3 天**、
  周级 = 3 周）；单位由 `thresholds.window_unit` 写进产物，并进制 `detector_version`
  （`metric_hash` 纳 `period_days`）⇒ 口径变更必须体现为新版本，历史判定不回溯
  （`core/calibration/drift_metrics.py:331-348` 的 "内容不同即拒绝改写"纪律不变）。
- **登记**：`detect_drift`（`core/calibration/drift_metrics.py:358`）构造 `DriftMetrics`（同文件 `:440`）时填
  `snapshot_fingerprint = snapshot_fingerprint(read_snapshot(...))`；**所读快照的锚点数 = 既有 `samples` 字段**
  （语义澄清为"该周期快照所依据的锚点数"，由同文件 `:445` 取 `_snapshot_samples(snapshot)`）——**不新增重复字段**。

**机检断言**：

- `thresholds["window_unit"] == {1:"day",7:"week"}[cfg.period_days]`，且
  `thresholds["period_days"] == cfg.period_days == cadence_of(record.period)`（三者一致率 100%）。
- **同量纲率 100%**：日级形态（`period_days == 1`）记录的 `window_unit == "day"`；周级形态 `== "week"`；
  不一致次数恒 **0**。
- **自描述率 100%**：判定类记录（`normal|drift`）的 `snapshot_fingerprint` 非空且为 64 位小写十六进制；
  `no_data` 记录的 `snapshot_fingerprint is None`；`record.samples == 所读快照的 samples`。
- **口径版本可区分**：`detector_version(replace(cfg, period_days=1)) != detector_version(cfg)`（周级 cfg）——
  日级与周级不共用同一口径哈希。
- **缺文件不成判**：缺该周期快照 ⇒ `verdict == no_data`、`psi is None`、`quantile_shifts == {}`（模型层约束
  `core/calibration/drift_models.py:243` 的 `_require_thresholds` 与判定类字段纪律不变）。
- `ops/calibrate.py:288-390` 的 `drift` 子命令输出新增 `period_days` / `window_unit` 两键（口径并列可见）。

**反例**：

- 一个周期读**多份**快照（如按轮拼接分布）⇒ 红（每周期只读一份，取最后一次物化）。
- 缺快照时插值/用上一周期顶替、或把 `no_data` 降级成 `normal` ⇒ 红（"不判、不伪造"）。
- 日级形态产出 `window_unit == "week"`（即"窗口 3"被当日级却是 3 周）⇒ 红。
- 把 `window_unit`/`period_days` 排除在 `metric_hash` 之外（日级与周级共用同一 `detector_version`）⇒ 红。
- 判定类记录漏填 `snapshot_fingerprint` ⇒ 红（"读的是哪一份"必须可追溯）。

**兼容规则**：

- 已落盘的 `calibration/drift/**` 记录**零回改**；**新增** `period_days` 进 `metric_hash` 会使**此后的**
  `detector_version` 哈希与今天不同（周级亦然）——这是"口径自描述"的预期后果：历史记录按其旧版本留在盘上、
  读取端按旧版本解释，**不得**回写；断言具体哈希字面量的既有用例（若有）按扩展更新，**不得**放宽语义断言。
- 历史记录缺失 `snapshot_fingerprint` / `thresholds.window_unit` ⇒ 读取端按 `None` / `"week"` 处理
  并在报表 `note` 如实标注"该记录为旧口径版本（未登记快照指纹/窗口单位）"，**不得**静默按新口径解释。
- `read_snapshot` / `snapshot_path` / `available_periods` / `select_window`（`core/calibration/drift_metrics.py:217-246`）/
  `write_record` 的既有语义与签名不变；010 侧**零写入**（012 只读 010 产物，纪律不变）。

## C5 口径进产物、变更留痕（`window_semantics` / `period_days` / 变更日）

**目的**：半开口径对既有周级窗口**不改变任何判定**，但口径本身必须可自描述；跨"口径变更日"的窗口**比较**
必须留痕，禁止静默比较两段不同口径的窗口。

**形状/接口**：

```yaml
# configs/{movie,shortdrama}.yaml 的 calibration 段：**新增键的 schema 两形态齐备**
# （都能加载、缺键即报错、两形态都能装配通过；"齐备"指键齐，**不**指两形态登记同一渠道集合）
calibration:
  period_days: 1|7                 # 既有键（短剧 1 / 电影 7）
  window_semantics: half_open      # 新增：取值域单元素（其它值 ⇒ 配置报错），镜像 019 单元素取值域纪律
  window_semantics_change_date: 2026-09-25   # 新增：口径生效日（ISO 日期；该日及之后产出的窗口按半开口径）
```

产物回写（四处，键名与取值统一）：

| 产物 | 新增键 |
| --- | --- |
| 台账行 `BiasRecord` | `period_days` / `window_semantics` / `round_id`（+ C3 的 `anchor_count` / `snapshot_fingerprint`） |
| 快照 payload | `period_days` / `window_semantics` |
| 信度报告 payload | `period_days` / `window_semantics` / `window_semantics_change_date` / `run_id` / `window{start,end,period_days}` / `note` |
| 漂移记录 `thresholds` | `window_unit` / `period_days` |

**标注规则（`note` 必须显式）**：

- 令 `d = window_semantics_change_date`、窗口 `[start, end)`：
  `spans_change_date = start < d < end`（窗口横跨变更日）；`before_change_date = end <= d`（窗口整体在变更日之前）。
- `spans_change_date` 为真 ⇒ 报告 `note` **必须**含该变更日与"口径变更日"字样（禁止静默）；
- 一切**比较**（漂移基线窗口 vs 当前周期；报告窗口 vs 既有报告窗口）中，两窗口分处变更日两侧
  （一个 `before_change_date`、一个 `start >= d`）⇒ `note` **必须**标注"跨口径变更日，不可直接比较"。
- 判定**不因标注而改变**（周级半开化不改变任何窗口的日集合；日级在变更日前无同量纲窗口），
  标注只负责"读者知道自己在比什么"。

**机检断言**：

- 窗口口径写入产物率 **100%**：每份新落盘产物同时含 `window_semantics == "half_open"` 与 `period_days`；
  `window_semantics` 取值域外或缺失 ⇒ 该产物**拒绝落盘**（不是落盘后补）。
- **跨口径变更日未标注即比较的次数恒 0**：`spans_change_date` 为真而 `note` 不含变更日 ⇒ 红。
- 报告 `window` 与标签自洽：`window.start == period_start(period, period_days).isoformat()`、
  `(window.end − window.start).days == period_days`（复用 C2 的恒等式在产物层面再检一次）。
- 配置声明与产物一致：`config["calibration"]["window_semantics"] == 产物["window_semantics"] == "half_open"`；
  `config["calibration"]["period_days"] == 产物["period_days"]`（不一致即红）。
- 周级可比性：`window_semantics_change_date` 之后产出的**周级**窗口与之前的周级窗口**日集合相同**
  （半开化对 `period_days == 7` 的既有调用点为恒等变换）⇒ 历史结论与新政结论**可直接并列**，
  `note` 仍按上条标注（口径留痕不等于结论失效）。

**反例**：

- `calibration.window_semantics: inclusive` / `"half-open"`（拼写或取值不合规）⇒ 配置报错（不静默接受）。
- 两形态之一未声明 `window_semantics` 或 `window_semantics_change_date` ⇒ 装配报错（不取码内默认，FR-014）。
  （"两形态均须声明"指**新增参数键**齐备，**不**指两形态登记同一渠道集合；
  `configs/movie.yaml` **不登记** media 投放渠道，两形态都**必须**能装配通过。）
- 把变更日写死进代码（而非配置）⇒ 红（形态差异必须配置化，原则五）。
- 报告窗口与台账行口径不一致（报告称 `half_open`、台账行 `window_semantics is None` 且无历史标注）⇒ 红。
- 以"口径变更"为由**重写**历史产物使其自描述 ⇒ 红（历史件零回改，回退标注只出现在**读取端**的标注里）。

**兼容规则**：

- 历史已落盘产物与已冻结结论**零回改**（原则一/二）：不重写、不补键、不重命名；变更只对此后新产出的窗口生效。
- 历史台账行缺失 `window_semantics` ⇒ 读取/统计端统一按 `UNSPECIFIED_WINDOW_SEMANTICS = "unspecified"` 处理并
  在报告 `note` 注明"该周期台账行口径未标注（历史行）"——**禁止**把它自称成 `half_open`。
- 既有 CLI 的退出码语义不变（`ops/calibrate.py` 的参数面只新增，不删除、不改名）；
  既有 `ops/billing.py` 与 019 侧的产物形状**零变化**（本契约不触碰 `billing/` 目录的任何字节）。

### 场景

1. 日级形态跑两轮：标签为日期、两轮台账行与报告各自留存（零覆盖）、漂移窗口单位 = `day`、窗口 `end − start == 1`
2. 周级形态跑既有夹具：标签仍为 `YYYY-Www`、窗口秒级端点与改造前逐字节相同、报告只多出 `period_days` 等新键
3. 跨越口径变更日的窗口：报告 `note` 含变更日；变更日后产出的周级窗口与之前的窗口日集合相同（结论可并列）
4. 同周期同内容重复物化 ⇒ 无字节写入、无新增台账行；内容变化 ⇒ 必有一条携带新指纹的台账行
