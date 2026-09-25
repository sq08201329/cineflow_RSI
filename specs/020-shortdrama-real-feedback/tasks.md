# 任务列表：短剧形态的真实投放与日级回流（C 路径投放接入 + 日级周期量纲 + 校准结论迁移）

**输入**: `specs/020-shortdrama-real-feedback/` 的设计文档（spec.md 14 FR / 9 SC / 15 条澄清 / 17 条边界情况；plan.md 299 行，阶段 0~6；research.md 610 行，决策 1~11；data-model.md 387 行，10 实体 + 15 条可机检不变量；contracts/ C1~C18；quickstart.md）

**前置条件**: 宪章 **v2.0.0**（原则一 `human` 锚点**写入即冻结**、原则二节点不可变、**原则三三条真实渠道纪律**（① 前置预算门禁 ② 厂商账单对账 ③ 最小规模先行）、原则五形态差异经配置表达 + 例外只能是"新增配置项"、原则六指标口径**必须可被证伪**）；019 已交付 `core/billing/` 五模块与两条真实渠道纪律并把**媒体渠道整条结转 G4**（`specs/019-real-channel-billing/spec.md:18`）；010（外环校准）/012（漂移）/015（promo 平台适配器与回流管道）已交付；015 的按轮上限与 019 的账本**必须共同生效且口径可辨**（`specs/020-shortdrama-real-feedback/spec.md:123`）

**测试说明**: TDD——**测试任务排在对应实现任务之前**（沿用 019 体裁）；全部用例走 **Mock 平台 + 夹具指标/账单**，**零真实花费、零外部网络、零凭证**；既有断言**按扩展更新、不削弱**（原则：不得以删断言换取通过，逐条见 `specs/020-shortdrama-real-feedback/research.md:529` 决策 10 的 19 项清单）；本特性的关键在于**四处标签一致**（台账/快照/报告/漂移）、**两条不同层的规则分别机检**（快照层（活动,周期）幂等拒绝 vs 外环产物层同周期多轮零覆盖，`specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:15`）、**归属日缺失即失败且新写入恒 0 None**、**019 既有 `--channel` 断言零删除**（`specs/020-shortdrama-real-feedback/contracts/channel-budget.md:249`）、**外环日级的"运转"断言而非配置数字断言**（`specs/020-shortdrama-real-feedback/spec.md:109`）

**组织方式**: 阶段 0 前置与勘查核对 → 阶段 1 契约与 TDD 骨架 → 阶段 2（US1 前半：周期量纲与半开窗口）→ 阶段 3（US1 后半：日级分片/归属日/漂移量纲/快照物化）→ 阶段 4（US2：C 路径投放接入与 019 渠道命名空间）→ 阶段 5（US3：校准结论迁移）→ 阶段 6（端到端演示/配置登记/文档与门禁同步）→ 验收与复核。**慢门禁单列在"验收与复核"段并由父代理在宿主机执行**（子代理不得跑：会超时）。

**⚠️ 执行者分工（硬约束）**: 子代理只跑**单文件快速子集**（阶段 0~6 各自的"快速核对"任务）。覆盖率、契约两条腿、`tests/integration -m integration`、`tests/adversarial -m adversarial`、`tests/unbiasedness -m unbiasedness`、`ruff check . && ruff format --check .` **六类慢门禁全部由父代理在宿主机执行**（见文末 T2077~T2082）。

---

## 图一：阶段依赖图

```text
                    ┌──────────────────────────────────────────────┐
                    │ 阶段 0 前置与勘查核对（T2001~T2007，只读复核）│
                    └───────────────────┬──────────────────────────┘
                                        │ 事实基线：五处名不副实以行号核实 / 迁移头 0010 /
                                        │ C13 的 12 处 --channel 穷举 / 变红清单与夹具现状
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │ 阶段 1 契约与 TDD 骨架（T2008~T2018 + T2086） │
                    │ 新增 8 个测试文件 + 扩展 3 个既有断言面       │
                    │ **全部先写，确认失败**                        │
                    └───┬──────────────┬──────────────┬────────────┘
                        │              │              │
        （periods.py 是所有 │              │              │
         标签口径的唯一实现）│              │              │
                        ▼              ▼              ▼
        ┌───────────────────────┐  ┌──────────────┐  ┌────────────────────┐
        │ 阶段 2 US1 前半        │  │ 阶段 4 US2   │  │ 阶段 5 US3         │
        │ 周期量纲 + 半开窗口    │  │ 渠道命名空间 │  │ 结论迁移           │
        │ T2019~T2027            │  │ + 投放接入   │  │ T2059~T2065        │
        │（core/calibration 侧） │  │ T2045~T2058  │  │（core/calibration/ │
        └───────────┬───────────┘  │（core/billing│  │  transfer.py 新增）│
                    │              │  + agents/   │  └─────────┬──────────┘
                    │              │  pilot 侧）  │            │
                    │              └──────┬───────┘            │
                    ▼                     │                    │
        ┌─────────────────────────────────┴────────────────────┐
        │ 阶段 3 US1 后半：日级分片 / 归属日 / 漂移量纲 / 快照物化│
        │ T2028~T2044（**依赖阶段 2 的 periods.py 与半开窗口**）  │
        │ 阶段 4 与阶段 3 文件不重叠 ⇒ **可并行**                │
        └───────────────────────────┬──────────────────────────┘
                                    │
                                    ▼
        ┌──────────────────────────────────────────────────────┐
        │ 阶段 6 端到端演示 / 配置登记 / 文档与门禁同步          │
        │ T2066~T2077（**依赖阶段 2~5 全部完成**）               │
        └───────────────────────────┬──────────────────────────┘
                                    ▼
        ┌──────────────────────────────────────────────────────┐
        │ 验收与复核 T2078~T2085（慢门禁**由父代理在宿主机执行**）│
        └──────────────────────────────────────────────────────┘
```

**可并行关系（文件不重叠）**:
- 阶段 2（`core/calibration/{periods,rounds,selection,report,config}.py`）与阶段 4（`core/billing/*` + `agents/pilot/*` + `ops/billing.py`）**可并行**；两者都只被阶段 6 汇聚。
- 阶段 3（`agents/promo/*` + `core/calibration/{models,db,ledger,drift_*}.py`）与阶段 4（`core/billing/*`）**可并行**，但阶段 3 **必须**在阶段 2 之后（`periods.period_label` / `period_window` / `cadence_of` 是其唯一口径来源）。
- 阶段 5（`core/calibration/transfer.py` + `ops/transfer.py`）与阶段 4 **可并行**；对阶段 2/3 是**只读依赖**（来源件 = 台账/快照/报告/漂移产物）。
- 阶段 4 内部的**串行点**：`core/billing/budget.py` 的三个任务（T2045 → T2046 → T2047，同文件）；`agents/pilot/backends.py`（T2050）与 `ops/smoke_llm.py`（T2051）可并行。

## 图二：跨阶段的关键依赖链

```text
链 1（周期量纲链 —— 四处标签必须同值）
  core/calibration/periods.py（新增，T2019）
    → 台账 core/calibration/ledger.py:84 快照文件名 / 台账行 period（T2039）
    → 快照 payload period_days + window_semantics（T2039）
    → 报告 core/calibration/report.py:55 文件名 + payload（T2024 → T2040）
    → 漂移 core/calibration/drift_metrics.py:53-54 正则/步长 + :116 metric_hash（T2041）
    → 归属日过滤 core/calibration/selection.py:108-113（T2038；`:108` 是 `_period_window(...)` 调用、过滤语句在 `:113`）
    → 日级窗口机检 agents/promo/daily.py 的 daily_coverage（T2035）
    → SC-002 的"量纲一致率 100% / 同周期多轮零覆盖 / 窗口 end−start == period_days"

链 2（DB 链 —— 两件 DDL 各自撑一条纪律）
  ops/migrations/versions/0011_daily_feedback.py（新增，T2028）
    ├─ 步骤① 锚点表可空 metric_date 列 → core/calibration/models.py:70 + core/calibration/db.py:23
    │    → core/calibration/anchors.py:25/:47 → 归属日过滤（T2038）→ 报告"归属日缺失锚点数"（T2037）
    └─ 步骤② promo_daily_metrics 表（唯一键（campaign_id, period）+ INSERT-only 触发器）
         → agents/promo/db.py（T2030）→ 日级分片幂等拒绝（T2035/T2036）
         → 同一活动跨多日 100% 合法且零覆盖（SC-002）
  注：promo_campaigns 的既有唯一键 (round_id, material_id)（agents/promo/db.py:47）**零改动**

链 3（渠道链 —— 019 兼容性扩展的举正面）
  core/billing/budget.py 渠道命名空间（T2045）
    → sole_channel 退役 / declared_channels + channel_for_adapter + tiers_of + tier_of（T2046）
    → assemble_guard 扩参 channel_id=（T2047）
    → 两个既有装配点 agents/pilot/backends.py:280（T2050）与 ops/smoke_llm.py:206（T2051）
    → RecordingChannelCall 投放门禁包装（T2049）
    → 投放调用前拒绝 / 平台 0 次调用 / 零入账 + 与 015 按轮上限两腿可辨（T2013/T2050）
    → C13 的 12 处 --channel 断言逐条"零删除"（T2055）+ .github/workflows/billing_alerts.yml:27 一字不改（T2057）

链 4（归属日链 —— 写入路径必须非空，读路径允许回退）
  MetricSnapshot.metric_date（末位可选，T2031）
    → 适配器采集出口 agents/promo/platform/simulated.py:94（T2032）/ agents/promo/platform/http_real.py:194+:208（T2033）
    → agents/promo/daily.py 回流写入（T2035）→ agents/promo/anchors.py 锚点写入（T2037）
    → "新采集写入路径产生 None 次数恒 0"（SC-003）
    → 历史重建回退 agents/promo/evaluators/platform_metrics.py:46 + 报告登记归属日缺失锚点数（T2034/T2037）

链 5（迁移链 —— 只迁结论、不迁权重）
  core/calibration/transfer.py（新增，T2059）→ append-only 存储（T2060）→ ops/transfer.py 四子命令（T2061）
    → 可比性判定（配置化，T2062）→ 零权重键 + 零 refit import（T2063）
    → 不改既有节点 eval_breakdown 与得分（逐字节，T2064）→ SC-007
```

## 格式：`[ID] [P] [Story] 描述`

- **[P]** = 可与同阶段其它任务并行（不同文件、无相互依赖）
- **[Story]** = 所属用户故事（US1 日级回流与日级运转 / US2 C 路径投放接入 / US3 校准结论迁移）
- 每条任务 = **一句话目标** + **精确文件路径**（新增文件路径一并写全）+ **完成判据**（可机检的断言或命令）

---

## 阶段 0：前置与勘查核对（只读，不改任何文件）

**目的**: 把 plan 的"五处名不副实"与 research 的决策清单变成**带行号的事实基线**，使后续任务的失败断言有据可依。本阶段**零代码改动**，只产出核对结论（写入任务评论/交付说明，不写仓库权威文件）。

- [x] T2001 [P] **核对"五处名不副实"逐条成立** — 复算 `specs/020-shortdrama-real-feedback/plan.md:44` 起的勘查表 5 行与 `specs/020-shortdrama-real-feedback/research.md:12`、`:62`、`:107`、`:150`、`:207`、`:272` 的决策 1~6 所引行号与事实 — **完成判据**: 每条结论写"成立 / 已变（新行号）"；五条事实各给出至少一条 `grep -n` 命中（如 `grep -n "iso_week_label" core/calibration/rounds.py` 命中 `:28` 与 `:69`；`grep -n "_WEEK = timedelta" core/calibration/drift_metrics.py` 命中 `:54`；`grep -n "sole_channel" core/billing/budget.py` 命中 `:357` 与 `:433`）。

  **勘核（批次后回填）**：五处名不副实**逐条均已随本次实现位移/消解**，故逐条「已变」——① 旧 `configs/shortdrama.yaml:399` → 新 `:403`（`period_days: 1` 仍在，但周期标签已改由 cadence 派生）、旧 `core/calibration/rounds.py:28`/`:69`（`iso_week_label`）→ 新 `:24`（改为从新模块 `core/calibration/periods.py` 再导出；`__all__` 在 `:30`）+ `:83`（`close_round` 改用 `period_label(round_.period_end, config.period_days)`）；② 旧 `core/calibration/drift_metrics.py:54` 的 `_WEEK = timedelta(days=7)` → **已删（grep 零命中）**、新 `:87-89` `_step(period_days)`、旧 `configs/shortdrama.yaml:408` 注释 → 新 `:429`（已改「单位 = cadence 同量纲」）；③ 旧 `ops/calibrate.py:43`（`今天 − period_days`）→ 新 `:42` `default_period_bounds(...)`、旧 `core/calibration/selection.py:28`（`_period_window` 给 `period_end` 加一天）→ 新 `:32`（半开）与 `:48`（跨度校验）；④ 旧 `agents/promo/platform/base.py:79`（`platform_timestamp`）→ 新 `:91`，并新增 `:93` `metric_date` 与 `:115` `validate_metrics`；⑤ 旧 `core/billing/budget.py:357` 的 `sole_channel()` → **已退役（`grep -n sole_channel` 仅剩 `:397` 注释、全仓无 `def`）**、旧 `:433` `assemble_guard` → 新 `:511`，按渠道分派落 `:401` `declared_channels`/`:409` `channel_for_adapter`/`:346` `tiers_of`/`:350` `tier_of`。证据（均已实跑复核）：`grep -n "iso_week_label" core/calibration/rounds.py` ⇒ `:24`/`:30`（非旧 `:28`/`:69`）；`grep -n "_WEEK = timedelta" core/calibration/drift_metrics.py` ⇒ **零命中**；`grep -n "sole_channel" core/billing/budget.py` ⇒ 仅 `:397`（非旧 `:357`/`:433`）。

- [x] T2002 [P] **核对 DB 迁移面与"只加不删"的边界** — 读 `ops/migrations/versions/0010_dev_jobs.py:22`（当前迁移头）与 `:23`（`down_revision`）、`ops/migrations/versions/0004_calibration_anchors.py`、`core/calibration/db.py:23`（锚点表定义）、`:41`（唯一键 `uq_anchor_node_reviewer_round`）、`:47`（`sqlite_trigger_statements`）、`agents/promo/db.py:33`、`:44`、`:47` — **完成判据**: 结论写清"0011 的 `down_revision` 必须 = `0010_dev_jobs`"、"锚点唯一键与 INSERT-only 触发器**不动**"、"`promo_campaigns` 既有唯一键 `(round_id, material_id)` **零改动**且该表**保持无触发器**"三条；并确认 `core/calibration/db.py:23`–`:42` 的列清单**不含归属日列**（故归属日今天确实无处可取）。

  **勘核（批次后回填）**：三条结论**成立**、原「归属日无处可取」**已变**——① 新迁移件 `ops/migrations/versions/0011_daily_feedback.py:28` 的 `down_revision = "0010_dev_jobs"` ✅（`ops/migrations/versions/0010_dev_jobs.py:22` = `revision`、`:23` = `down_revision`，原位未动）；② 锚点唯一键与 INSERT-only 触发器未动：`core/calibration/db.py:50` `uq_anchor_node_reviewer_round`、`:56` `sqlite_trigger_statements`（`0011` 只做 `ALTER TABLE … ADD COLUMN metric_date TEXT NULL`，不改约束/触发器）；③ `promo_campaigns` 唯一键 `(round_id, material_id)` 零改动（`agents/promo/db.py:100` `uq_promo_round_material`）且该表**保持无触发器**（触发器只落在 `promo_daily_metrics`：`agents/promo/db.py:108`/`:122`）。**已变**：旧 `core/calibration/db.py:23`（锚点表定义）→ 新 `:27`、旧 `:41`（唯一键）→ 新 `:50`、旧 `:47`（触发器）→ 新 `:56`；旧 `agents/promo/db.py:33`/`:44`/`:47` → 新 `:82`（`promo_campaigns.node_id`）/`:97`（`metrics`）/`:100`（唯一键）；原因 = 新增 `promo_daily_metrics` 表前置 + 锚点表增列。**另一处已变**：原判据「`:23`–`:42` 列清单不含归属日列」→ 现 `core/calibration/db.py:48` 由 `0011` 增补可空 `Column("metric_date", Text, nullable=True)`（历史行恒 `NULL`、永不回填）。

- [x] T2003 [P] **穷举 019 的 `--channel` 调用点并落"零删除"结论** — 逐行核对 `tests/contract/test_billing_contracts.py` 的 12 处（`:995`、`:1013`、`:1037`、`:1048`、`:1144`、`:1146`、`:1149`、`:1155`、`:1216`、`:1238`、`:1274`、`:1290`）与 `.github/workflows/billing_alerts.yml:27`，对照 `specs/020-shortdrama-real-feedback/contracts/channel-budget.md:249` 的 C13.1 表逐行 — **完成判据**: 13 行结论**全部**为"保留"或"更新（最小）"，**零"删除"**；其中 `:1146`（`--channel nope` ⇒ 退出 2）与 `:1290`（⇒ 2 且错误含「不一致」）两条**硬要求**单列；本机复核命令 `uv run python ops/billing.py tiers --channel llm --config configs/movie.yaml`（⇒ 0）与 `--channel nope`（⇒ 2）**今天已实跑通过**（本仓已核，2026-09-25）。

  **勘核（批次后回填）**：C13.1 十三行**零「删除」**——`tests/contract/test_billing_contracts.py` 的 12 处 `--channel` 调用点逐条仍在（行号整体位移，旧→新：`:995→:1023`、`:1013→:1041`、`:1037→:1065`、`:1048→:1076`、`:1144→:1172`、`:1146→:1174`、`:1149→:1177`、`:1155→:1183`、`:1216→:1244`、`:1238→:1266`、`:1274→:1302`、`:1290→:1318`；原因 = 该文件按扩展新增用例而位移），`.github/workflows/billing_alerts.yml:27` **一字不改**（该文件末次改动仍为 019 的 `aaf5990`，`git diff 873dbc5 HEAD` 对该文件零改动；`:27` 仍是 `alert-check --channel llm --config configs/movie.yaml`）；两条硬要求单列已实跑：`:1174` 断言 `tiers --channel nope` ⇒ **2**、`:1319` 断言 `tiers --channel ghost` ⇒ **2 且错误含「不一致」**；证据：`uv run python ops/billing.py tiers --channel llm --config configs/movie.yaml` ⇒ **退出 0**、`--channel nope` ⇒ **退出 2**（本机实跑，2026-09-25）。

- [x] T2004 [P] **逐项核对"会变红的既有测试与夹具"清单现状** — 逐文件打开 `specs/020-shortdrama-real-feedback/research.md:539` 起决策 10 表中 19 项所引位置（含 `tests/unit/test_form_switch.py:188`、`:193`、`tests/unit/test_anchor_snapshots.py:16`、`tests/unit/test_drift_detect.py:32`、`tests/unit/test_drift_config.py:76`、`tests/unit/test_platform_anchors.py:1`、`tests/unit/test_ingest_metrics.py:137`、`tests/conftest.py:441`、`:462`、`:4109`、`:4127`） — **完成判据**: 复核结论里 `pytest tests/unit/test_form_switch.py tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py -q` ⇒ **55 passed**（本仓已实跑，2026-09-25）；并确认 `tests/unit/test_form_switch.py:193` 今天的唯一断言确实只验配置数字（"日级名不副实"的容忍源）。

  **勘核（批次后回填）**：research 决策 10 的 19 项**逐项已按扩展更新、零删断言**；**已变**：判据基线「55 passed」→ 现 `uv run pytest tests/unit/test_form_switch.py tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py -q` ⇒ **66 passed**（原因 = T2015/T2072 按扩展新增运转用例、原断言未删）；`tests/unit/test_form_switch.py` 的「唯一只验配置数字」断言**已变**：旧 `:193` → 新 `:205`（`assert short.period_days == 1 and short.period_days < movie.period_days` **保留未删**，其后新增 T2015 三条运转断言 ⇒ 已不再是该用例的唯一断言）。

- [x] T2005 [P] **核对 015 按轮上限与 019 账本两套门禁的调用点与拒绝文案** — 读 `agents/promo/config.py:70`（`budget_cap_usd`）、`agents/promo/loop.py:403`、`:240`（`_round_spent`）、`:425`（`create_campaign` 调用点）、`core/billing/budget.py:763`（`SpendGuard`）、`:824`（`check`）、`:920`（`BudgetRefusedError`） — **完成判据**: 两条拒绝文案**互不相同且可辨**（按轮上限 = 中文"预算门禁…（拒投）"；账本 = `over_limit` + 余量），且 `create_campaign` 的调用点行号确认，为 T2013 的"平台调用 0 次"断言提供落点。

  **勘核（批次后回填）**：两条拒绝文案**互不相同且可辨**、调用点确认——015 按轮上限 = `agents/promo/loop.py:408-409` 的中文「预算门禁：已耗 $… + 申请 $… > 上限 $…（拒投）」；019 账本 = `core/billing/budget.py:975`/`:978`/`:995`/`:998` 的 `over_limit` + `remaining_usd` 余量；`create_campaign` 调用落点 = `agents/promo/loop.py:427`（`_create_campaign_with_retry`）→ `:519`（`adapter.create_campaign`），为 T2013「平台调用 0 次」提供落点。**已变**：旧 `agents/promo/config.py:70`（`budget_cap_usd`）→ 新 `:117`（属性，`:119` 计算）、旧 `agents/promo/loop.py:403` → 新 `:402`（前置校验块 `:402-410`）、旧 `:240` → 新 `:242`（`_round_spent`）、旧 `:425` → 新 `:427`/`:519`、旧 `core/billing/budget.py:763` → 新 `:872`（`SpendGuard`）、旧 `:824` → 新 `:933`（`check`）、旧 `:920` → 新 `:94`（`BudgetRefusedError`）。

- [x] T2006 [P] **核对平台协议面与凭证权威读取点 + 登记归属日字段的运营侧问题** — 读 `agents/promo/platform/base.py:71`、`:79`、`:83`、`agents/promo/platform/http_real.py:143`（缺凭证文案）、`:194`、`:208`、`agents/promo/platform/simulated.py:94`、`:104`、`agents/promo/evaluators/platform_metrics.py:46`、`docs/pilot-upgrade-manifest.json:136`（C 路径 `status: not_delivered`） — **完成判据**: 结论写清"归属日字段名由 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:111` 的 C7 与 `specs/020-shortdrama-real-feedback/data-model.md:95` 实体 2 **定名为 `metric_date`**（末位可选）"；并把"平台侧是否提供该字段"逐字登记为开放问题 1（**不得发明字段名与语义**，未确认期如实标"未标定"）。

  **勘核（批次后回填）**：结论**成立**——归属日字段名由 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:111` 的 C7 与 `specs/020-shortdrama-real-feedback/data-model.md:95` 实体 2 **定名为 `metric_date`**（末位可选，实现在 `agents/promo/platform/base.py:93`），「平台侧是否提供该字段」已逐字登记为 `specs/020-shortdrama-real-feedback/spec.md:179` 开放问题 1「未标定」（不发明字段名与语义）；`agents/promo/evaluators/platform_metrics.py:46`（`deterministic=False`）与 `docs/pilot-upgrade-manifest.json:136`（C 路径 `status: not_delivered`）**行号未变**。**已变**：旧 `agents/promo/platform/base.py:71` → 新 `:73`（`class MetricSnapshot`）、旧 `:79`（`platform_timestamp`）→ 新 `:91`、旧 `:83` → 新 `:93`（新增 `metric_date`）+ `:115`（`validate_metrics`）；旧 `agents/promo/platform/http_real.py:143`（缺凭证文案）→ 新 `:166`、旧 `:194`/`:208`（采集出口）→ 新 `:232`/`:254`；旧 `agents/promo/platform/simulated.py:94`/`:104` → 新 `:128`（`fetch_metrics`）/`:147`（`metric_date` 注入）。

- [x] T2007 [P] **核对两形态配置现状与 019 五处登记点，登记本特性增量清单** — 读 `configs/movie.yaml:392`、`:509`、`:541`、`:597`、`:598` 与 `configs/shortdrama.yaml:399`、`:408`、`:512`、`:544`、`:600`、`:601`，以及 `specs/019-real-channel-billing/quickstart.md:99` 所列 019 五处登记点（`tests/unit/test_form_switch.py` 差异集 / `tests/unit/test_config_integrity.py:23` 与 `:46` / `tests/contract/test_pilot_contracts.py:418` / `agents/pilot/pilot.py:377` / `tests/conftest.py` 夹具） — **完成判据**: 产出本特性的**增量登记清单**（只做增量、**不新造第六处**）；确认短剧态 `:601` 现为 `min_window_days: 7`（须改 14）、`:408` 的注释"日级外环 → 3 天窗口"与实际（3 周）不符两条事实。

  **勘核（批次后回填）**：增量登记清单**只做增量、未新造第六处**——019 五处登记点均在既有面内更新：`tests/unit/test_form_switch.py` 差异集（`:34-36` 的 `budget_*` 权重项 + `:368` 的 `"budget"` 段）、`tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES:38` 与 `REQUIRED_PATHS:48`、`tests/contract/test_pilot_contracts.py:418`（`test_c13_两套配置差异可归因且无形态分支`，**行号未变**）、`agents/pilot/pilot.py:377`（`config_completeness`，**行号未变**；budget 加载器 `:411`）、`tests/conftest.py:4148`（`_billing_budget_payload`；`billing_budget_factory` `:4194`）。两条「名不副实」**已变**：短剧态 `min_window_days` 旧 `configs/shortdrama.yaml:601`（值 7）→ 新 `:658`（值 **14**，已改）、`window: 3` 注释旧 `:408`（「日级外环 → 3 天窗口」与实际 3 周不符）→ 新 `:429`（已改「单位 = cadence 同量纲：日级 = 3 天、周级 = 3 周」）；其余行号位移：`configs/movie.yaml` `:392→:396`、`:509→:530`、`:541→:562`、`:597/:598→:620/:621`；`configs/shortdrama.yaml` `:399→:403`、`:512→:533`、`:544→:565`、`:600→:657`。

**检查点**: ✅ 五处名不副实全部带行号复核；`0011` 的 `down_revision` 与"只加不删"边界钉死；C13 的 13 行"零删除"结论就位；变红清单 19 项现状核实（本仓基线 55 passed 已复核）；两形态登记点增量清单就位。**跨阶段前置（如实登记）**: T2003~T2007 的结论是阶段 1 全部失败断言的**依据**，故阶段 1 的"红"不经本轮核对不得宣告。

---

## 阶段 1：契约与 TDD 骨架（新增/扩展测试**先写**，确认失败）（T2008~T2018 + T2086）

**目的**: 先落 **8 个新增测试文件 + 3 个既有断言面扩展**，使每条契约都有失败断言作为施工图。**本阶段不写任何实现代码**。

**⚠️ 关键**: 本阶段的"红"是**预期红**——`core/calibration/periods.py`、`core/calibration/transfer.py`、`agents/promo/daily.py`、`ops/transfer.py`、`ops/demo_shortdrama_feedback.py`、`0011_daily_feedback` **今天都不存在**；新测试文件必须能在**收集期或运行期**明确暴露缺失（`ImportError` / `AssertionError` / `OSError`），不得写成"跳过即绿"。

### 阶段 1 的测试任务（先写，确认失败）

- [x] T2008 [P] **新增周期量纲与半开窗口单测（C1/C2/C3/C5，先写、预期红）** — 新增 `tests/unit/test_period_cadence.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/period-cadence.md:14` C1 与 `:81` C2 的全部机检断言，并按 `:138` C3（快照 = 周期物化）与 `:311` C5（口径进产物与变更留痕）各补一条跨模块断言（C3：同周期多轮物化后该目录文件数**不随轮数增长**；C5：产物写 `window_semantics == "half_open"` + `period_days`，缺或域外 ⇒ 拒绝落盘）——① **逐字节相同**：对全部既有周级标签样本断言 `period_label(day, 7) == iso_week_label(day)`（`from core.calibration.rounds import iso_week_label`，`core/calibration/rounds.py:28`）；② `period_label("2026-09-25", 1) == "2026-09-25"`；③ `period_regex(7).pattern == r"^(\d{4})-W(\d{2})$"` 且 `period_regex(1).fullmatch("2026-W39") is None`；④ 往返恒等 `period_label(period_start(period_label(d, n), n), n) == period_label(d, n)` 与 `cadence_of(period_label(d, n)) == n`（n ∈ {1,7}）；⑤ `period_days ∈ {2,3,30,0}` ⇒ `ValidationError`「未支持的 cadence」（**不发明第三档量纲**）；⑥ **半开**：`period_window(date(2026,9,14), 7) == (date(2026,9,14), date(2026,9,21))`、`period_window(date(2026,9,25), 1) == (date(2026,9,25), date(2026,9,26))`，且 `(end − start).days == period_days`；⑦ **缺省窗口跨 cadence 天**：`period_end = 今天`、`period_start` 取缺省 ⇒ `(period_end − period_start).days + 1 == 7`（周级），`period_days + 1` 天窗口出现次数恒 **0**；⑧ `build_blind_list(..., period_days=)` **必填**（`core/calibration/selection.py:82`），缺参 ⇒ `TypeError`；⑨ `period_start="2026-09-14", period_end="2026-09-21", period_days=7`（8 天跨度）⇒ `ValidationError`（禁止静默截断）；⑩ C5 口径进产物：报告 payload 含 `window_semantics == "half_open"` 与 `period_days`，且 `window_semantics` 取值域外 ⇒ **拒绝落盘**。

- [x] T2009 [P] **新增归属日与三时间并列单测（C7/C9，先写、预期红）** — 新增 `tests/unit/test_metric_attribution_date.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:111` C7 与 `:276` C9——① **写入路径主断言**：两个适配器采集出口（`agents/promo/platform/simulated.py:94`、`agents/promo/platform/http_real.py:194`/`:208`）与 `agents/promo/daily.py` 的回流写入，对任一夹具输入产出 `snapshot.metric_date is None` 的次数恒 **0**；② **数据类不承担必填**：`MetricSnapshot(..., metric_date=None)` **构造成功**，而 `validate_metrics(...)`（`agents/promo/platform/base.py:83`）抛错且消息含「平台未提供指标归属日」（断言写成"构造成功 + 校验失败"，不得写成"构造即抛错"）；③ 平台响应缺该字段 ⇒ 该条 `rejected`、`promo_daily_metrics` 零新增行、锚点零新增、**整批不中断**；④ **兜底次数恒 0**：反向扫描 `agents/promo/`，不得出现把 `platform_timestamp`（`agents/promo/platform/base.py:79`）或采集时刻（`agents/promo/ingest.py:117`）派生为 `metric_date` 的表达式；⑤ `re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot.metric_date)` 对两适配器产出恒成立；⑥ **按归属日聚合**：夹具"归属日全同、采集日跨 3 天" ⇒ `covered_days == 1`、`days` 长度 `== 1`；夹具"归属日跨 15 天、采集日全同" ⇒ `covered_days == 15`；⑦ **三时间齐备率 100%**：`days[]` 每条同时含 `metric_date` / `collected_at` / `platform_timestamp`；⑧ 迟到/回补：归属日 `d` 已有锚点 ⇒ 新增行 **0**、既有锚点字段逐字节不变，且两个时间在该日条目里都出现；⑨ `attribution_fallback_count(required_since)`（`agents/promo/anchors.py`）与覆盖视图 `attribution_missing_anchors` 相等。

  **勘核（批次后回填）**：`tests/unit/test_metric_attribution_date.py` 存在、**14 用例全绿**（`uv run pytest tests/unit/test_metric_attribution_date.py -q` ⇒ 14 passed），覆盖 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:111` C7 与 `:276` C9 的 ①~⑨ 断言面（写入路径 `metric_date is None` 恒 0、`MetricSnapshot(metric_date=None)` 构造成功 + `validate_metrics` 抛「平台未提供指标归属日」、归属日聚合 1/15 天、三时间齐备、迟到回补不改写锚点、`attribution_fallback_count` == `attribution_missing_anchors`）。

- [x] T2010 [P] **新增日级分片与覆盖窗口单测（C6/C8/C9/C10，先写、预期红）** — 新增 `tests/unit/test_promo_daily_ingest.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:15` C6、`:229` C8、`:276` C9、`:383` C10——① **唯一性与多日合法**：同一 `campaign_id`、三个不同 `metric_date` ⇒ `promo_daily_metrics` **三行**、三个 `period`、三个 `node_id`（互不相同）、零覆盖（`GROUP BY campaign_id, period HAVING count(*) > 1` 恒为空集）；② **幂等拒绝**：同 `(campaign_id, period)` 第二次回流 ⇒ `record_daily_ingest` 返回 `False`、行数不变、节点数不变、运营表 `updated_at` 不变，且 `ingested` 与 `rejected` **同时非空**（整批不中断）；③ **层间不吞并**：同周期两轮 `close_round` 的**台账行数 == 2**、**报告文件数 == 2**，而同一活动在同一周期的第二次采集 **0 行新增**——两句话**必须同时成立**；④ **节点 id 确定性**：`daily_node_id("m1", "2026-09-25") == "m1-node@2026-09-25"`，同 `(material_id, period)` 恒同 id、不同 `(material_id, period)` 必不同；历史节点 `f"{material_id}-node"`（`agents/promo/ingest.py:91`）的字节与 `eval_breakdown`、得分**零变化**；⑤ **历史行可读且不被误判**：`promo_daily_metrics` 无该 `campaign_id` 行而 `promo_campaigns.metrics.platform_metrics` 非空 ⇒ 读作 `legacy_single_snapshot`，**不计入**任何归属日、**不计入** `covered_days`，并在 `legacy_single_snapshots` 计数中如实登记；⑥ **存储层拒改写**：对 `promo_daily_metrics` 的 `UPDATE`/`DELETE` 100% 抛错、行数不变（镜像 `core/calibration/db.py:47` 的双方言触发器范式）；⑦ **覆盖 ∧ 连续双条件**（短剧态 `min_window_days=14, gap_tolerance_days=0`）：连续 15 天真实夹具 ⇒ `meets is True`；"15 天真实但缺 2 天" ⇒ `meets is False` 且 `gaps` 逐段列出（段数 == 缺口段数、每段含 `from/to/days`）；电影态以 `min_window_days=7` 复跑一遍；⑧ **不插值**：`covered_dates ∩ gaps == ∅`；`days[]` 中每个 `metric_date` 必须有对应 `promo_daily_metrics` 行（行数核对相等）；⑨ **来源纪律**：`source` 取值域外（`"mock"`/`"stub"`/`"test"`）⇒ 抛错且零落盘；`source == "simulated"` 的归属日不进 `covered_dates`；"15 天全模拟" ⇒ `covered_days == 0`、`meets is False`、`evidence_claim == "mechanism_ready_real_feedback_pending"`；`fallback` 无 `fallback_reason` ⇒ 拒绝；⑩ **取值域单点**：`agents/promo/`、`core/calibration/` 内**不得**重复声明 `("real", "simulated", "fallback")` 字面量（属主 = `core/billing/runlog.py:28`）。

  **勘核（批次后回填）**：`tests/unit/test_promo_daily_ingest.py` 存在、**22 用例全绿**（`uv run pytest tests/unit/test_promo_daily_ingest.py -q` ⇒ 22 passed），覆盖 `contracts/daily-ingest.md:15` C6、`:229` C8、`:276` C9、`:383` C10（`daily_node_id` 确定性、`record_daily_ingest` 幂等返回 `False`、`legacy_single_snapshot` 不计覆盖、存储层 `UPDATE`/`DELETE` 拒改写、覆盖∧连续双条件与逐段 `gaps`、`evidence_claim` 二值、C9 输出键**逐键对齐**、取值域单点）。

- [x] T2011 [P] **新增漂移量纲与可追溯单测（C4，先写、预期红）** — 新增 `tests/unit/test_drift_cadence.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/period-cadence.md:237` C4——① `thresholds["window_unit"] == {1:"day",7:"week"}[cfg.period_days]` 且 `thresholds["period_days"] == cfg.period_days == cadence_of(record.period)`（三者一致率 100%）；② **口径版本可区分**：`detector_version(replace(cfg, period_days=1)) != detector_version(cfg)`（周级 cfg），即 `core/calibration/drift_metrics.py:116` 的 `metric_hash` 纳入 `period_days`；③ **自描述率 100%**：判定类（`normal|drift`）记录的 `snapshot_fingerprint` 非空且为 64 位小写十六进制，**三种如实标注类**（`insufficient` / `no_baseline` / `no_data`）记录为 `None`，且 `record.samples == 所读快照的 samples`；③′ **五值枚举与模型层约束常驻**：`DriftVerdict` 取值域恒为 `{normal, drift, insufficient, no_baseline, no_data}`（`core/calibration/drift_models.py:74`，成员 `:80-84`、`JUDGED_VERDICTS` `:87`），且 **非判定类记录携带 `psi` / `quantile_shifts` ⇒ `ValidationError`**（`:219-227`）、**判定类缺 `psi` ⇒ `ValidationError`**（`:209-218`）；④ **每周期读一份**：同一 `(evaluator_id, period)` 只有一个快照路径（`snapshot_path`，`core/calibration/drift_metrics.py:140`），读取取**最后一次物化**；⑤ **三值各归其位**：缺该周期快照 ⇒ `verdict == no_data`、`psi is None`、`quantile_shifts == {}`（`core/calibration/drift_metrics.py:145` 返回 `None` 的既有语义不变）；样本量 < `calibration.min_samples` ⇒ `verdict == insufficient`（**不是** `no_data`）；首周期无基线 ⇒ `verdict == no_baseline`；⑥ 日级形态产出 `window_unit == "week"` ⇒ 红（"窗口 3"被当日级却是 3 周）；⑦ `ops/calibrate.py` 的 `drift` 子命令输出含 `period_days` / `window_unit` 两键。

  **勘核（批次后回填）**：`tests/unit/test_drift_cadence.py` 存在、**15 用例全绿**（`uv run pytest tests/unit/test_drift_cadence.py -q` ⇒ 15 passed），覆盖 `contracts/period-cadence.md:237` C4（`window_unit`/`period_days` 与 `cadence_of(record.period)` 三者同量纲、口径不一致即**拒绝静默比较**、`detector_version` 纳 `period_days`、`snapshot_fingerprint` 判定类非空/标注类 `None`、`no_data`/`insufficient`/`no_baseline` 三值各归其位）。

- [x] T2012 [P] **新增渠道命名空间与兼容读单测（C11/C12/C13/C16/C18，先写、预期红）** — 新增 `tests/unit/test_billing_channels.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/channel-budget.md:12` C11、`:158` C12、`:249` C13、`specs/020-shortdrama-real-feedback/contracts/transfer-ops.md:149` C16、`:323` C18——① **schema 齐备**：每个 `channels.<id>` 必带非空 `tiers` 与非空 `adapter`，缺任一 ⇒ `BudgetConfigError`（不取码内默认）；② `tiers_shape ∈ {"channels","legacy_flat"}` 可追溯率 100%，旧扁平形状归一后 `tiers_shape == "legacy_flat"` 且 `notes` 含归一事实与渠道 id；③ **静默归并次数恒 0**：多渠道下访问 `cfg.tiers` / `cfg.tier(id)`（`core/billing/budget.py:275`、`:318`）⇒ 抛错；新旧并存 ⇒ 报错；④ **旧配置继续可装配**：019 现形状（顶层扁平 `tiers` + 单渠道）与新形状在**同一场景结果逐字段一致**；⑤ **分派确定性**：`declared_channels(cfg)` 返回全部声明渠道、`channel_for_adapter(cfg, a)` 对未声明 `a` **恒抛错**、≥2 个命中 ⇒ 歧义报错；⑥ **跨渠道串用恒 0**：`llm` 与 `media` 的 `tiers` / `ledger.json` / `alerts.jsonl` / `runs/` 各自独立，`media` 的档位名不出现在 `billing/llm/ledger.json`；⑦ **装配快照只含本渠道档位**且随行 `tiers_shape`；⑧ **凭证矩阵**：声明真实而 `PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY` 缺失 ⇒ 装配期拒绝启动、点名缺哪个变量、零落树零扣费、**不回落模拟**；矩阵只允许 `set`/`length`、**零凭证值**；⑨ **两形态装配通过**：`configs/movie.yaml`（**不登记** media）与 `configs/shortdrama.yaml` 各自装配成功，**movie 路径因投放渠道或其凭证而失败的次数恒 0**；movie 上声明 `pilot.overrides.promo: http` ⇒ 装配期显式拒绝并指出"该形态未登记投放渠道"；⑩ **诚实分层（**断言本体写在本文件**）**：零凭证环境跑完整日级闭环，真实渠道装配点数恒 0，**"模拟被标为真实"次数恒 0**、`source` 逐条可辨、覆盖只计 `real`，结论文案 ∈ {"机制已就绪","真实回流待运营"}（分工见 T2071 / T2066⑦）；具体三条断言本体：**(a) `source` 只能由装配面声明**（调用点不得自填、不得按"跑通了"推断）；**(b) 覆盖判定只认 `source == "real"`**（模拟件与本地 stub 调用不计入真实覆盖天数）；**(c) 结论文案取值域固定**（未满足前提时输出"真实数据回流已达成" ⇒ 红）；⑪ 声明顺序约定：两形态 `declared_channels(cfg)[0].adapter == "pilot_llm"`（`tests/contract/test_billing_contracts.py:146` 的 `_channel(cfg)` 依存）。

  **勘核（批次后回填）**：`tests/unit/test_billing_channels.py` 存在、**23 用例全绿**（`uv run pytest tests/unit/test_billing_channels.py -q` ⇒ 23 passed），覆盖 `contracts/channel-budget.md:12` C11、`:158` C12、`:249` C13、`contracts/transfer-ops.md:149` C16、`:323` C18（命名空间齐备与旧扁平归一 `tiers_shape`、`declared_channels`/`channel_for_adapter` 分派确定性、`--channel` 三态在**多渠道**下成立、凭证矩阵只报 `set`/`length` 零凭证值且不回退模拟、`evidence_claim` 二元素与「模拟不冒充真实」）。

- [x] T2013 [P] **新增投放前置门禁与两腿分辨单测（C14，先写、预期红）** — 新增 `tests/unit/test_promo_delivery_gate.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/channel-budget.md:327` C14 与 `specs/020-shortdrama-real-feedback/spec.md:123` FR-008——① 超限投放 ⇒ **调用前拒绝**、`create_campaign`（`agents/promo/loop.py:425`）**0 次调用**、成本 **0 入账**、`alerts.jsonl` 有 `kind=budget_refused` 行、原因点名环节与额度；② 未声明投放环节（缺 `stage` / 环节不在该渠道 `tiers` 键集）⇒ 拒绝 `tier_undeclared`，同样 0 次调用零入账；③ **两腿同时生效且可辨**：同一次超预算投放，015 按轮上限（`agents/promo/loop.py:403`）与 019 账本（`core/billing/budget.py:824`）**两道都在位**，拒绝理由**点名命中的是哪一条**；被 019 拒后 015 的按轮口径**不重复记账**（`spent_usd` 不变）；④ **两道不得互相替代**：静态断言 `agents/promo/loop.py` 仍含按轮上限校验、`_promo` 真实分支必经 `guard.check`；⑤ **唯一包装点**：`core/billing/runlog.py` 的 `RecordingChannelCall` 是投放面**唯一**的调用包装点（`agents/promo/loop.py` 内不得再写一份门禁调用）；⑥ **分型互斥**：`budget_refused` ≠ 厂商 401/403 ≠ 429，三类各有独立错误类型与告警 `kind`。

  **勘核（批次后回填）**：`tests/unit/test_promo_delivery_gate.py` 存在、**10 用例全绿**（`uv run pytest tests/unit/test_promo_delivery_gate.py -q` ⇒ 10 passed），覆盖 `contracts/channel-budget.md:327` C14 与 `spec.md:123` FR-008（调用前拒绝 + `alerts.jsonl` 落 `kind=budget_refused`、未声明环节 ⇒ `tier_undeclared`、015 按轮上限与 019 账本两腿同时在位且拒绝理由**点名命中哪一条**、`RecordingChannelCall` 为投放面**唯一**包装点、`budget_refused` ≠ 厂商 401/403 ≠ 429 分型互斥）。

- [x] T2014 [P] **新增迁移件与迁移面 CLI 单测/契约（C15/C17，先写、预期红）** — 新增 `tests/unit/test_calibration_transfer.py` 与新增 `tests/contract/test_transfer_contracts.py` — **完成判据**: 覆盖 `specs/020-shortdrama-real-feedback/contracts/transfer-ops.md:14` C15、`:228` C17——① **条件完备率 100%**：声明的每条条件在产物里都有 `declared` / `observed` / `satisfied`，缺一条即红；② `comparability.verdict` 取值域固定 `{transferable, not_transferable}`，`not_transferable` ⇒ `comparability.reasons` 非空且每条**前缀点名条件 id**；`status` 取值域固定 `{pending, confirmed, shelved}` 且 `not_transferable ⇒ status != "confirmed"`（误迁移次数恒 0）；③ **零权重键**：`conclusion` 内**禁止**出现 `candidate_weights` / `current_weights` / `fit_objective` / `ridge_lambda` 四键（对照 `core/calibration/refit.py:141` 的提案形状）；`core/calibration/transfer.py` **零** `core.calibration.refit` import；④ **append-only**：同 `transfer_id` 重产 ⇒ 拒绝；系统字段被改写 ⇒ `system_digest` 校验失败 ⇒ 拒采信；⑤ **异形态数值不得冒充本形态证据**：迁移件只落 `{data_dir}/transfers/`、必带 `source_form`/`target_form`；电影线证据面（节点 `eval_breakdown`、信度报告、漂移产物）**零**写入迁移数值；⑥ CLI：`ops/transfer.py` 四个子命令 `--help` 退出 0；`--dry-run` **零落盘**（目录不新增文件）；非 dry-run 落件后同键重产 ⇒ 退出 1；缺 `calibration.transfer` 任一键 ⇒ 退出 2；⑦ 来源只读：跑迁移后 010 的台账/快照/报告/漂移产物的摘要**逐字节不变**。

  **勘核（批次后回填）**：`tests/unit/test_calibration_transfer.py`（**54 用例全绿**，⇒ 54 passed）+ `tests/contract/test_transfer_contracts.py`（**23 用例全绿**，⇒ 23 passed），覆盖 `contracts/transfer-ops.md:14` C15、`:228` C17（条件 `declared`/`observed`/`satisfied` 完备、`transferable`/`not_transferable` 与 `status` 取值域、零权重键与零 `core.calibration.refit` import、append-only 与 `system_digest` 拒采信、迁移件只落 `transfers/` 必带 `source_form`/`target_form`、CLI `--help`/`--dry-run` 零落盘/同键重产退出 1/缺键退出 2、来源只读）。

- [x] T2015 **扩展 `test_外环日级` 为"运转"断言（⑫ 硬要求，先写、预期红）** — 扩展 `tests/unit/test_form_switch.py:188` 的 `test_外环日级`（现行唯一断言在 `:193`，只验配置数字） — **完成判据**: **既有数字断言一字不删**（`short.period_days == 1 and short.period_days < movie.period_days` **保留**），在其后**新增**三条**运转**断言：① **cadence 派生标签**：短剧态（`configs/shortdrama.yaml:399` 的 `period_days: 1`）产出的周期标签为**日期形态**（`^\d{4}-\d{2}-\d{2}$`）、周级形态仍为 ISO 周；② **漂移窗口单位与 cadence 同量纲**：日级 `window: 3` 的 `window_unit == "day"`（`configs/shortdrama.yaml:408` 的注释今天与实际（3 周）不符，须修正）；③ **同周期零覆盖**：同一日级周期内两轮的台账行数 `== 2`、报告文件数 `== 2`（路径不同）且两者**都保留**。**明示纪律**：不得把这组"运转"证据写成"配置里 `period_days == 1`"（即不得只断言配置数字）。

- [x] T2016 **扩展 010 契约套件以覆盖日级运转（另立用例，不改周级用例）** — 扩展 `tests/contract/test_calibration_contracts.py`（既有 C1~C9 端到端含"权重生效后历史节点逐字节一致"与"昂贵动作调用计数为 0"两条机检，见 `:1`） — **完成判据**: ① **既有周级用例一字不改、判据不放松**（两条机检必须继续通过）；② **新增**日级运转契约用例：日级两轮的产物路径不同且并留存、台账行登记 `anchor_count` + `snapshot_fingerprint`、报告 payload 含 `window_semantics` / `period_days` / `run_id` / `window{start,end,period_days}` / `note`；③ 新增"跨口径变更日未标注即比较的次数恒 0"用例（`spans_change_date` 为真而 `note` 不含变更日 ⇒ 红）。

  **勘核（批次后回填）**：已满足且**如实登记一处夹具扩展**——`git diff 873dbc5 HEAD -- tests/contract/test_calibration_contracts.py` = **152 insertions / 0 deletions**；日级运转用例**另立**（`test_日级两轮零覆盖且产物自描述`、`test_跨口径变更日未标注即比较的次数恒零`，含台账行 `anchor_count` + `snapshot_fingerprint`、报告 payload `window_semantics`/`period_days`/`run_id`/`window{start,end,period_days}`/`note`）；**已变/如实登记**：既有 C7/C8 周级用例的**配置夹具**补入了新增必需段 `calibration.transfer`（0 删除行、既有断言一字未改、判据未放松）；`uv run pytest tests/contract/test_calibration_contracts.py -q` ⇒ 19 passed。

- [x] T2017 **扩展 `core/billing` 纯净性机检的扫描面（不删断言）** — 扩展 `tests/unit/test_billing_core_purity.py:110`（五模块被扫描）、`:151`（零配置声明的渠道 id）、`:157`（零配置声明的环节 id） — **完成判据**: ① 反向扫描的**配置路径**由 `budget.tiers` 键改为 `budget.channels.*.tiers.*`；② 新增写入面（渠道/档位解析、`RecordingChannelCall`）纳入被扫描源文件集；③ `:151` / `:157` **断言本体不删**（否则"零渠道字面量"失去牙齿）；④ 新增渠道 id 或环节 id 到白名单 ⇒ 红。

  **勘核（批次后回填）**：已满足、**零删断言**——① `_declared_tier_ids` 的配置路径由 `budget.tiers` 改为**各渠道 `budget.channels.<id>.tiers` 的键并集**（`git diff 873dbc5 HEAD -- tests/unit/test_billing_core_purity.py` = 36 insertions / 5 deletions，删的 5 行是旧取键语句与 docstring、**非断言**）；② 新增写入面（渠道/档位解析、`RecordingChannelCall`）都在 `core/billing/**/*.py` 的**整包扫描面**内（`_sources()` = `PACKAGE.rglob("*.py")`）⇒ 自动纳入；③ 旧 `:151` 的 `test_无配置声明的渠道_id`（新 `:172`）**未被触碰**（`git diff` 零命中）；旧 `:157` 的 `test_无配置声明的环节_id`（新 `:178`）的计数断言由 `len(declared) == 8` 改为 `len(llm_declared) == 8` + `declared ⊇ llm_declared` + `len(declared) > len(llm_declared)`（因并集含投放环节），**扫描断言 `_scan_ids(declared) == []` 本体保留**（牙齿未失）；④ 白名单只有格式键（`test_账单格式白名单常驻`），渠道/环节 id **无白名单** ⇒ 写死即红；`uv run pytest tests/unit/test_billing_core_purity.py -q` ⇒ 17 passed。

  **批次 1 登记**: 010 契约套件（`tests/contract/test_calibration_contracts.py` 与 `tests/contract/test_drift_contracts.py`）的**周级判定结果不变**由批次 1 自检 2(c) 提供证据（两文件全绿，含"历史节点逐字节一致"与"昂贵动作调用计数为 0"两条机检）；契约文件按扩展更新、**零断言删除、零放宽**（`set(report)` 4 键 → 10 键、报告存在性断言改 `latest_report_path(...) == report_path(..., round_id)` 均属加强）。

- [x] T2086 **【跨阶段补号，镜像 019 的 T1950/T1951 先例：任务号接在末尾、文档位置在阶段 1】新增对抗面薄封装（C-01）** — 新增 `tests/adversarial/test_shortdrama_feedback_adversarial.py` — **完成判据**: ① 文件**打 `@pytest.mark.adversarial`**，使本特性的对抗场景**纳入既有的合并阻塞门禁**（`uv run pytest tests/adversarial -m adversarial`，由 T2081 执行）；② **薄封装**：只调用 unit/contract 侧既有用例体或共享夹具（`tests/unit/test_promo_daily_ingest.py` / `tests/unit/test_billing_channels.py` / `tests/unit/test_promo_delivery_gate.py` / `tests/unit/test_calibration_transfer.py`），**不复制第二份断言逻辑**（unit/contract 侧保留为实现级细粒度断言）；③ 覆盖场景：伪造 `promo_daily_metrics` 行、改写已落盘日级记录/快照、伪造 `covered_days`、**门禁绕过**（不传 guard 的投放装配、伪造 `--tier`/`stage`）、迁移件系统字段改写与省略、把模拟件标为真实、媒体渠道账单篡改后再对账；④ **与其它测试任务同批先写并确认失败**（对抗用例在机制落地前应因实现缺失而失败，不得写成"跳过即绿"）；⑤ `uv run pytest tests/adversarial/test_shortdrama_feedback_adversarial.py -m adversarial -q` 在阶段 1 结束时应为**红**（缺失件指向 T2010/T2012/T2013/T2014 的实现面）。

- [x] T2018 **TDD 红确认（本阶段验收）** — 跑新增与扩展用例的**单文件快速子集** — **完成判据**: `uv run pytest tests/unit/test_period_cadence.py tests/unit/test_metric_attribution_date.py tests/unit/test_promo_daily_ingest.py tests/unit/test_drift_cadence.py tests/unit/test_billing_channels.py tests/unit/test_promo_delivery_gate.py tests/unit/test_calibration_transfer.py -q` **逐文件失败**（非"跳过"）、失败原因**指向缺失的实现件**（`core/calibration/periods.py` / `agents/promo/daily.py` / `core/calibration/transfer.py` / `0011_daily_feedback` / 渠道命名空间）；`uv run pytest tests/adversarial/test_shortdrama_feedback_adversarial.py -m adversarial -q` **红**（T2086）；`uv run pytest tests/unit/test_form_switch.py -q` **因 T2015 新断言而红**、`uv run pytest tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py -q` **保持绿**（周级路径不变）。产出红清单（文件 → 失败条数 → 缺失件）备阶段 2~5 逐条转绿。

  **勘核（批次后回填）**：**替代满足**：按批次 TDD 取代一次性全红（见本条既有登记）；各批次红→绿的过程证据分散在各批汇报里。

  **刻意偏离登记（批次 1 = 阶段 1 与阶段 2）**: **测试文件按批次创建**（本批只落 C1/C2 相关用例，其余新测试文件留给对应实现批次）——理由是每批必须自洽可绿；**T2086 对抗薄封装顺延至阶段 4 之后**。

**检查点**: ✅ **9 个新测试文件**（含 T2086 的 `tests/adversarial/test_shortdrama_feedback_adversarial.py`）+ 3 个扩展断言面就位；每条契约（C1~C18）都有失败断言作施工图；"运转 vs 配置数字"的分野已在 T2015 显式钉死；019 的 C13 红线（`:1146`、`:1290`）已在 T2003/T2012 登记为"不得改"；**本特性的对抗面已纳入既有合并阻塞门禁**（T2086 薄封装 + `@pytest.mark.adversarial`，由 T2081 执行）。**本阶段零实现代码**。

---

## 阶段 2：US1 第一半 —— 周期量纲由 cadence 派生 + 半开窗口（T2019~T2027）

**目标**: 把"日级只在配置里"变成"标签、路径、漂移窗口单位三处同时按 `calibration.period_days` 派生"，并把缺省窗口由 `period_days + 1` 天统一为**半开区间** `[start, start + period_days)`；**周级路径逐字节不变**。

**独立测试**: `uv run pytest tests/unit/test_period_cadence.py tests/unit/test_form_switch.py -q`（本阶段完成时 T2008/T2015 转绿）；`uv run pytest tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py tests/unit/test_blind_selection.py -q` **保持绿**（周级零变化）。

**⚠️ 关键（风险最高的一条）**: `period_label(day, 7)` 与 `iso_week_label` 的**逐字节一致**断言（T2008①）必须**先于**标签接线（T2020）落地——顺序颠倒即等于改写 010 的冻结证据（`specs/020-shortdrama-real-feedback/plan.md:265` 的风险缓解明文）。

### 阶段 2 的实现

- [x] T2019 **新增业务无关模块 `core/calibration/periods.py`（口径唯一实现）** — 新增 `core/calibration/periods.py` — **完成判据**: 提供 `period_label` / `period_start` / `period_window` / `period_regex` + `cadence_of` + `coverage_window`（**同模块追加，不新增模块**）；常量 `WINDOW_SEMANTICS = "half_open"` / `UNSPECIFIED_WINDOW_SEMANTICS = "unspecified"` / `SUPPORTED_CADENCES = (1, 7)` / `CADENCE_UNIT = {1: "day", 7: "week"}`；`period_days == 7` 时**逐字复用** `core/calibration/rounds.py:28` 的 `iso_week_label` 语义（**唯一实现移入本模块**），`== 1` 走 `YYYY-MM-DD`，`∉ {1,7}` ⇒ `ValidationError`「未支持的 cadence」；`coverage_window(days, *, end, min_window_days, gap_tolerance_days) -> dict` 把 `core/billing/runlog.py:226` 的口径**纯函数化**（输出同名键），**不 import `core.billing`**（避免 core 内新增 `calibration → billing` 耦合）；**零 IO、零业务概念、不 import `core/calibration/rounds.py`**（否则成环）；`uv run pytest tests/unit/test_period_cadence.py -q` 的 C1/C2 用例转绿。

- [x] T2020 **标签接线：`close_round` 的周期改由 cadence 派生（周级取值不变）** — 改 `core/calibration/rounds.py:69`（现 `period = iso_week_label(round_.period_end)`） — **完成判据**: 改为 `period_label(round_.period_end, config.period_days)`；`iso_week_label`（`:28`）**保留为公开函数**并**再导出**（`from core.calibration.periods import iso_week_label`），使 `ops/screenplay.py:473`、`ops/dev.py:423` 与既有 `from core.calibration.rounds import iso_week_label` 的读取点**零改动继续可用**；**不可反向 import**（`periods.py` 不得 import `rounds.py`）；`uv run pytest tests/unit/test_close_round.py tests/unit/test_anchor_snapshots.py -q` 保持绿（周级标签逐字节相同）。

- [x] T2021 **三处标签调用点收敛到同一口径（周级取值不变）** — 改 `ops/calibrate.py:190`、`ops/screenplay.py:473`、`ops/dev.py:423` — **完成判据**: 三处改经 `periods.period_label(config.period_days)`；**`ops/calibrate.py` 的 `propose` 与 `close` 两条子命令的 period 取值口径必须一致**（同函数、同 cadence），否则台账与提案会看到两个周期；周级形态下三处取值**逐字节不变**（`uv run pytest tests/contract/test_screenplay_calibration.py -q` 保持绿）。

- [x] T2022 **半开窗口：`_period_window` 去 `+1 天` + `build_blind_list` 增必填 `period_days`** — 改 `core/calibration/selection.py:28`（`_period_window`）与 `:82`（`build_blind_list`） — **完成判据**: `end = period_start + period_days` 天（**不再 `+1`**）；`(period_end − period_start) + 1 != period_days` ⇒ `ValidationError`（**禁止**静默按任一端口径截断）；`period_start > period_end` ⇒ `ValidationError`；`build_blind_list(..., period_days=)` **必填**、缺参 ⇒ `TypeError`（**不取码内默认**）；窗口端点**只能**来自 `period_window`；**既有周级判定零变化**：`build_blind_list(period_start="2026-09-14", period_end="2026-09-20", period_days=7)` 的 `(start_ts, end_ts)` 与改造前**逐字节相同**（旧式 `end = period_end + 1 天` = `2026-09-21T00:00:00Z` = 新式 `start + 7 天`）⇒ `uv run pytest tests/unit/test_blind_selection.py tests/unit/test_anchor_intake.py tests/unit/test_editing_replay.py -q` 只须补 `period_days=7` 参数、**判定结果不变**。

- [x] T2023 **缺省窗口半开：`ops/calibrate.py` 的缺省 `period_start` 修正** — 改 `ops/calibrate.py:43`（现 `今天 − period_days`） — **完成判据**: 改为"含首尾跨 `period_days` 天"的缺省（`今天 − timedelta(days=config.period_days - 1)`），与 T2022 的半开合成后 **`(period_end − period_start).days + 1 == period_days`** 恒成立（周级 = 7 天、日级 = 1 天）；`period_days + 1` 天窗口出现次数恒 **0**（机检，T2008⑦）；**CLI 参数名与语义不变**（`--period-start`/`--period-end` 不动，只修缺省与实现）。

- [x] T2024 [P] **口径进产物：报告 payload 与路径单点（含两处既有调用点接线）** — 改 `core/calibration/report.py:20`（`build_report`）与 `:55`（`path = data_dir / "reports" / f"{period}.json"`）；**同步改两处既有调用点**：`core/calibration/rounds.py:76`（现 `report = build_report(data_dir, period, target=config.reliability_target)`）与 `ops/calibrate.py:150`（`report` 子命令，现 `report = build_report(args.data_dir, args.period, target=config.reliability_target)`） — **完成判据**: ① 签名 `build_report(data_dir, period, *, target, window_semantics, window_semantics_change_date, run_id=None)` —— **两个新增必填关键字参数**（`window_semantics` / `window_semantics_change_date`，口径与变更日**必须来自形态配置**）**+ 一个可选 `run_id`**（缺省 ⇒ **兼容别名路径** `reports/{period}.json`）；**机检**：缺 `window_semantics` / `window_semantics_change_date` ⇒ `TypeError`；`run_id` 不传 ⇒ 走别名路径、**不报错**；② **调用点接线（本任务必须承载，否则新签名即 `TypeError`）**：`core/calibration/rounds.py:76` 传 `window_semantics=config.window_semantics`、`window_semantics_change_date=config.window_semantics_change_date`、`run_id=round_.round_id`；`ops/calibrate.py:150` 同样传口径两项（`run_id` 由 `--run-id` 可选给出，缺省走别名路径）；③ payload 既有键（`period`/`agents`/`target`/`alerts`）**逐字保留**，新增顶层 `period_days` / `window_semantics` / `window_semantics_change_date` / `run_id` / `window{start,end,period_days}` / `note`；④ 新增 `report_path(data_dir, period, run_id) -> Path` 与 `latest_report_path(data_dir, period) -> Path | None`（**路径规则单点**：`run_id` 给定 ⇒ `reports/{period}-{run_id}.json`，否则 `reports/{period}.json` 兼容别名）；⑤ `window_semantics` 取值域外 ⇒ **该产物拒绝落盘**（不是落盘后补）；⑥ `window.start == period_start(period, period_days).isoformat()` 且 `(window.end − window.start).days == period_days`。

- [x] T2025 [P] **收敛两处重复的 `_period_window` 实现（不留第二份口径）** — 改 `ops/dev.py:347` 与 `ops/screenplay.py:388` 的同名重复实现 — **完成判据**: 两处改为调用 `core/calibration/periods.py` 的同一函数；两处产出的 `period_window` 元组与今天**逐字节相同**（既有剧本线/开发线产物零回改）；`grep -rn "def _period_window" ops/ core/` 只剩一处实现（+ `core/calibration/selection.py:28` 的秒级适配层）。

  **批次 1 登记**: `ops/dev.py` / `ops/screenplay.py` 的**缺省**窗口改为"含首尾跨 `period_days` 天"（`今天 − (period_days − 1)`，依据 FR-003"禁止含首尾口径"与"`period_days + 1` 天窗口出现次数恒 0"机检）；显式 `--period-start/--period-end` 行为不变（原样返回、逐字节相同）。

- [x] T2026 [P] **两形态配置登记量纲/窗口口径 + 配置解析改为必需读取** — 改 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 的 `calibration` 段 + 改 `core/calibration/config.py:33`（`from_dict`） — **完成判据**: 新增 `window_semantics: half_open`（**取值域单元素**，其它值 ⇒ 配置报错）与 `window_semantics_change_date: <ISO 日期>`（口径生效日，**由配置声明、不得写死进代码**）；`core/calibration/config.py` 的 `from_dict` 改为**必需读取**（**缺项即报错、不取码内默认**）；`payload["calibration"]["window_semantics"] == 产物["window_semantics"] == "half_open"`、`config["calibration"]["period_days"] == 产物["period_days"]`（不一致即红）；两形态之一未声明 ⇒ 装配报错（FR-014）；**注意**: `period_days` 的 `≥ 1` 校验已在 `core/calibration/config.py:17`，`{1,7}` 的取值域由 `periods.py` 把守，两者**不得互相替代**。

- [x] T2027 **阶段 2 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: ① `uv run pytest tests/unit/test_period_cadence.py tests/unit/test_form_switch.py -q` **转绿**（T2008①~⑩ 与 T2015 的三条运转断言）；② `uv run pytest tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py tests/unit/test_blind_selection.py tests/unit/test_close_round.py tests/unit/test_anchor_intake.py -q` **保持绿**；③ `uv run pytest tests/contract/test_screenplay_calibration.py -q` 保持绿（`iso_week_label` 再导出可用）；④ **`build_report` 调用点接线已核**（T2024 新增两个必填关键字参数 ⇒ 任一未接线的调用点都会 `TypeError`）：`core/calibration/rounds.py:76`（`close_round`）与 `ops/calibrate.py:150`（`report` 子命令）**均已传** `window_semantics` / `window_semantics_change_date`，且 `close_round` 传 `run_id=round_.round_id` ⇒ 复核命令：`uv run pytest tests/unit/test_close_round.py -q` 绿 且 `uv run python ops/calibrate.py report --data-dir <tmp> --period <p> --config configs/movie.yaml` 退出 0。

**检查点**: ✅ 周级标签与窗口端点**逐字节不变**（`period_label(day,7) == iso_week_label(day)` 常驻）；日级标签为日期形态、漂移窗口单位 = 天、同周期多轮零覆盖（T2015 运转断言转绿）；`end − start == period_days` 机检通过、`period_days + 1` 天窗口出现次数恒 0；口径（`window_semantics` / `period_days` / `window_semantics_change_date` / `note`）已进产物；`build_report` 的两处调用点已按新签名接线。**本阶段不做什么（明确登记，防施工者按 plan 阶段 2 步骤误改）**: 本阶段**不**改归属日过滤——`core/calibration/selection.py:108-113` 的候选过滤键改造**顺延至阶段 3 的 T2038**（依赖 `0011_daily_feedback` 的锚点归属日列，先改会"过滤键无处取"）；本阶段对 `selection.py` 只做 `:28` 的半开修正与 `:82` 的 `period_days` 必填扩参（T2022）。

---

## 阶段 3：US1 第二半 —— 日级分片 / 归属日 / 漂移量纲 / 快照物化（T2028~T2044）

**目标**: 让"日"真的落进证据件：**归属日**由平台显式字段承载（缺失即失败）、日级回流按**采集日分片**且唯一性键 =（活动, 周期）、锚点写入即冻结、快照 = **周期物化**、漂移每周期读一份并登记所读快照指纹与锚点数、日级窗口的**覆盖 ∧ 连续**双条件可机检且**断档逐段如实报、不插值**。

**独立测试**: `uv run pytest tests/unit/test_metric_attribution_date.py tests/unit/test_promo_daily_ingest.py tests/unit/test_drift_cadence.py -q`（本阶段完成时 T2009/T2010/T2011 转绿）；`tests/contract/test_calibration_contracts.py` 的周级用例**保持绿**。

**⚠️ 关键**: 迁移 `0011_daily_feedback`（T2028）是**本阶段与阶段 4 之前的硬前置**（锚点归属日列 + `promo_daily_metrics` 表）；**归属日的字段名一律 `metric_date`**（`specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:111` C7 定名），实现**必须逐字采用，改名即视为与 FR-006 失同步**。

### 阶段 3 的实现

- [x] T2028 **迁移 `0011_daily_feedback` = 两件 DDL（本特性唯一的 DB 迁移）** — 新增 `ops/migrations/versions/0011_daily_feedback.py`（`down_revision = "0010_dev_jobs"`，当前头见 `ops/migrations/versions/0010_dev_jobs.py:22`）+ 同步改 `core/calibration/db.py:23`（表定义） — **完成判据**: ① **步骤①** `ALTER TABLE calibration_anchors ADD COLUMN metric_date TEXT NULL`（**无 `server_default`** ⇒ 历史行恒 `NULL`；`ADD COLUMN` 是 DDL、不触发 `UPDATE`/`DELETE` 触发器 ⇒ **历史锚点零回改**）；`core/calibration/db.py:23`–`:42` 的表定义同步加列；② **步骤②** `CREATE TABLE promo_daily_metrics`（列见 `specs/020-shortdrama-real-feedback/data-model.md:95` 实体 2）+ 唯一约束 `uq_promo_daily_campaign_period`（`campaign_id`, `period`）+ **INSERT-only 触发器**（镜像 `core/calibration/db.py:47` 的 SQLite `RAISE(ABORT)` 与 PG `reject_*_mutation()` 双方言），并回收应用账号的 `UPDATE`/`DELETE`（镜像 `ops/migrations/versions/0004_calibration_anchors.py` 的授权做法）；③ **锚点唯一键 `(node_id, reviewer, round_id)`（`core/calibration/db.py:41`）与既有触发器不动**；④ **`promo_campaigns` 表结构零 DDL**、其唯一键 `(round_id, material_id)`（`agents/promo/db.py:47`）**不改**、**保持无触发器**；⑤ `downgrade()` 对锚点表只 `DROP COLUMN`、对新表只 `DROP TABLE`，**不触碰任何既有行数据**；⑥ 若日后需 DDL **必须取 `0012_*` 及之后**（不得与本件并行分叉）。

  **批次 2 登记**: 迁移件按 0011 的两件 DDL 落地；`upgrade head` 在临时 SQLite 上需先 `stamp 0010_dev_jobs`（0001 的 JSONB 无法在 SQLite 渲染属既有事实，与 0011 无关）；PG 侧 DDL 已用 alembic 离线模式渲染核对，真实执行留给 T2080。

- [x] T2029 **锚点模型与读写带归属日（形态 = 末位可选）** — 改 `core/calibration/models.py:70`（`AnchorScore`，字段区 `:71-81`）、`core/calibration/anchors.py:25`（`insert_anchor`）、`:47`（`load_anchors`） — **完成判据**: `AnchorScore` 新增 **末位可选** `metric_date: str | None = None`（`__post_init__` 校验：非空时必须是合法 ISO `YYYY-MM-DD`）；**取可选的理由只有一个**：兼容 `agents/promo/evaluators/platform_metrics.py:46` 的**历史 dict 重建**（历史 payload 无该键 ⇒ 取 `None`，**不得**抛构造期异常）；`insert_anchor` 的显式列清单补 `metric_date`（既有 INSERT 语句行为逐字节不变，`agents/promo/anchors.py:57` 之外的人评通道 `core/calibration/anchors.py:74` `intake_anchors` 语义**不变**、其行 `metric_date = None`）；`load_anchors` 读出 `NULL` ⇒ `None`；**锚点写入唯一接口仍是 `insert_anchor`**、同键重复 ⇒ `False`（幂等拒绝、整批不中断）。

- [x] T2030 **`promo_daily_metrics` 落 `agents/promo/db.py` + 运营表口径注释同步（结构零改动）** — 改 `agents/promo/db.py`（新表定义 + `:33`、`:44` 注释口径） — **完成判据**: ① 新增 `promo_daily_metrics` 表定义（列见 data-model 实体 2；唯一键（campaign_id, period）+ INSERT-only 触发器触发点）；② `promo_campaigns` 的 **`:47` 唯一键 `(round_id, material_id)` 原样保留**、表**保持无触发器**；③ `:33` 的 `node_id` 注释由"回填即终态不再变"改为**"最近一次落盘节点"**、`:44` 的 `metrics` 注释由"回流后写入一次"改为**"节点构建素材（`eval_fragments`/`material`/`cost`/`gen_params`）+ 最近一次快照"**（口径必须同步，否则后续读者会按"终态唯一"误解数据）。

  **批次 2 登记**: 新表 `source` 的 DDL CHECK **由 `core/billing/runlog.py` 的 `RUN_SOURCES` 派生**（不在本特性内重复声明取值域字面量，C10/T2010⑩）；迁移件内保留字面量（DDL 必须冻结）；`create_campaigns_schema` 同时安装 `promo_daily_metrics` 的 INSERT-only 触发器，`promo_campaigns` 仍**无触发器**。

- [x] T2031 [P] **归属日字段进 `MetricSnapshot`（末位可选）+ 缺失即失败** — 改 `agents/promo/platform/base.py:71`（`MetricSnapshot`）、`:83`（`validate_metrics`） — **完成判据**: `metric_date: str | None = None` **加在末位**；`validate_metrics` 增归属日校验——`None` ⇒ `MetricValidationError`「平台未提供指标归属日」；形态非法/非真实日历日 ⇒「指标归属日非法：…」；`platform_timestamp`（`:79`）语义**不变**（真值产生时刻）；**命名约束**：不复用 `platform_timestamp`、不复用 `data_version`（那是平台数据版本）；`uv run pytest tests/unit/test_ingest_metrics.py -q` 保持绿（字段末位可选 ⇒ 既有直接构造点零改动）。

  **批次 2 登记**: `validate_metrics` = 值域 + 归属日存在性（写入路径闸门）；另抽出 `validate_metric_ranges`（仅值域）供**读取/重建路径**使用——否则历史 payload 重建会被归属日检查拦下，与 T2034「存在性检查只在写入路径强制」一致（属强化而非放宽：写入路径仍严格拒绝 `None`）。

- [x] T2032 [P] **模拟适配器采集出口产出归属日（确定性、可复现）** — 改 `agents/promo/platform/simulated.py:94`（`fetch_metrics`）与 `:104`（`MetricSnapshot(...)` 构造点） — **完成判据**: 采集出口产出 `metric_date`，缺省取**自身** `platform_timestamp` 的 **UTC 日期**（与既有固定时间戳同源 ⇒ **逐字节可复现**）；可显式注入以推进跨日夹具；**禁止由 `now()` 派生**（确定性纪律）；产出满足 `re.fullmatch(r"\d{4}-\d{2}-\d{2}", ...)`。

- [x] T2033 [P] **真实适配器读取平台指标日期字段、缺失即拒（不兜底）** — 改 `agents/promo/platform/http_real.py:194`（`fetch_metrics`）与 `:208`（`_snapshot`），并同步模块 docstring 的协议形状 — **完成判据**: 从 `GET /campaigns/{id}/metrics` 响应读 `"metric_date"`；缺失 ⇒ `MetricValidationError`「平台未提供指标归属日」、格式不符 ⇒「指标归属日非法：…」（沿用该函数既有的"缺字段即拒、不补零"风格）；**禁止**以拉取/采集时刻兜底；docstring 的协议形状补该字段；凭证读取点（`:143` 的文案与 `PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY`）**不改名、不新增变量**。

- [x] T2034 [P] **历史 dict 重建容忍缺失 + 回退只作用于历史行** — 改 `agents/promo/evaluators/platform_metrics.py:46` — **完成判据**: 重建路径按 `raw.get("metric_date")` 取值，历史 payload 缺该键 ⇒ `None`、**不抛构造期异常**；回退口径**唯一边界** = `created_at` 的日期 < `promo.attribution_date_required_since`（`configs/*.yaml` 的 `promo` 段新增键，两形态 schema 齐备、缺键即报错）；`platform_timestamp` 的既有读取语义不变；**存在性检查只在写入路径强制**（数据类末位可选 ⇒ 读路径保留）。

  **批次 2 登记**: `PromoConfig` 新增 `attribution_date_required_since`（`promo` 段）与 `period_days`（跨段取自 `calibration.period_days`，缺项即报错）——日级回流按后者派生周期标签与节点 id。

- [x] T2035 **新增 `agents/promo/daily.py`（业务侧库函数：日级回流 + 归属日归入 + 幂等拒绝 + 覆盖窗口）** — 新增 `agents/promo/daily.py` — **完成判据**: 提供四个入口——① `daily_node_id(material_id, period) -> str` = `f"{material_id}-node@{period}"`（确定性派生：同 `(material_id, period)` 恒等、不同必不同）；② `record_daily_ingest(engine, *, campaign_id, round_id, external_id, material_id, snapshot, period, collected_at, source, node_id) -> bool`（插入一条 `promo_daily_metrics` 行；同 `(campaign_id, period)` 已存在 ⇒ 返回 `False`，**零变更**、整批不中断）；③ `ingest_daily(round_id, store, adapter, engine, config, *, source, metric_date=None) -> dict`（按采集日分片回流一轮：`delivered|ingested` → 归属日解析 → 幂等插入 → 节点一次性落盘 → 运营表更新；返回 `{round_id, ingested, rejected[{campaign_id, period, reason}], skipped, periods}`；`source` 由**装配面显式声明**、取值域外 ⇒ 拒绝且零落盘；缺失归属日 ⇒ 该条 `rejected` 且原因含「平台未提供指标归属日」）；④ `daily_coverage(engine, *, end, min_window_days, gap_tolerance_days, period_days) -> dict`（**按归属日聚合**；`covered_days` 只计 `source == "real"`；**覆盖 ∧ 连续的判定必须委托** `core/calibration/periods.py::coverage_window`（**唯一实现**），本模块**不得**再写第二份 `meets` / `max_gap_days` / `continuous` 口径——`agents/promo/daily.py` 只做"取行 → 组装归属日集合 → 调 `coverage_window` → 回填日级专属键"）；**输出键以 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:291` 的 C9 为权威**（逐键对齐，缺键即红）：`{end, period_days, window_semantics, attribution_based: true, covered_days, covered_dates[], gaps[{from,to,days}], max_gap_days, continuous, min_window_days, gap_tolerance_days, meets, coverage_shortfall_days, gap_shortfall_days, reasons[], days[{metric_date, collected_at, platform_timestamp, source, campaign_id, period}], legacy_single_snapshots, attribution_missing_anchors, evidence_claim, note}`；**常驻静态断言（落在 `tests/unit/test_promo_daily_ingest.py`，T2010 的同一文件）**：`agents/promo/` 内**不得**出现自算的 `meets` / `max_gap` 逻辑（反向扫描"`meets`、`max_gap`、`gap_tolerance` 的比较与赋值"，白名单仅 `coverage_window` 的调用与返回值回填））；**分层纪律**：库函数承载全部判定，CLI（T2043）只作薄封装（镜像 `agents/promo/ingest.py` 的分层）；`uv run pytest tests/unit/test_promo_daily_ingest.py -q` 转绿。

  **批次 2 登记**: 两个入口各加**可选**关键字（`ingest_daily(collected_at=)` 供离线跨日可复现、`record_daily_ingest(fallback_reason=)` 落 C10 的「回落必须有因」）——列出签名的必填参数与返回键**逐字不变**；`INGEST_CANDIDATE_STATUSES` 定义在本模块（供 `ingest.py` 导入，避免循环 import）；`days[]` 条目除 C9 列出的六键外**多一个 `sources`**（该日各来源计数）——C9 正文明确要求「并保留 sources 计数以便『模拟不得冒充真实』的机检」，属加键而非缺键。

- [x] T2036 **回流管道按采集日分片（既有单快照路径保留）** — 改 `agents/promo/ingest.py:58`（候选选取）、`:91`（`node_id`）、`:117`（`created_at`）、`:130`（置 `ingested`） — **完成判据**: ① 候选选取由"仅 `status == "delivered"`"放宽为 `status IN ('delivered', 'ingested')`（首日 `delivered`、后续日 `ingested`）——这正是"同一活动无法连续多日各采一次"的直接根因；② `node_id` 改为**含周期的确定性派生**（走 `daily_node_id`），**历史 `f"{material_id}-node"` 节点一律不回改**；③ `created_at` **保持**采集墙钟（`time.time()`，语义不变）；④ 回填按（活动, 周期）落分片记录；⑤ `agents/promo/anchors.py:30` 之外的全表取行（按 `status == "ingested"`）在多日分片下按周期过滤；⑥ **既有 `ingest_round`（单快照路径）保留**（003/015 的既有断言不破），新旧两路径**共用** `validate_metrics` 与节点构造纪律（**不得各写一套校验**）；⑦ 节点 `observation_context` 新增 `metric_date` 与 `period` 两键（节点自描述其归属日）。

  **批次 2 登记**: 候选放宽后 `ingest_round` 第二次调用返回 `skipped`（节点撞主键）而 `ingested == []`，`tests/unit/test_ingest_metrics.py::test_重复回流幂等` 的既有断言**零改动**仍绿；节点构造纪律抽为 `build_metrics_node` 供两条路径共用。

  **批次 2 修正（父代理，由全量集成门禁发现）**: 候选放宽引入的真实缺陷——`fetch_metrics` 原先在 `try` 之外，**一条取不到（如历史活动已不在平台侧）就让整轮回流中断**；`tests/integration/test_promo_replay.py`（module 级共享库 + 函数级平台实例）因此 5 个用例 setup error。修法：**两条路径（`ingest_round` 与 `ingest_daily`）都把 `fetch_metrics` 纳入按条兜底**——平台侧异常 ⇒ 记入 `report["rejected"]`（带 `retryable: True` 与原因）、**不改运营表状态**（改状态会让该条永久退出候选面）、**整批不中断**（FR-004）；`validate_metrics` 失败仍走既有 `_mark_failed` 语义（零改动）。`agents/promo/ingest.py` 与 `agents/promo/daily.py` 两处对称修改；全量集成复跑见 T2080。

- [x] T2037 **锚点写入带归属日 + 历史回退计数（回退只作用于历史行）** — 改 `agents/promo/anchors.py:30`（`collect_platform_anchors`）、`:57`（`AnchorScore(...)` 构造点）、`:64`（渠道标识 `reviewer=material["platform"]`），并新增 `attribution_fallback_count` — **完成判据**: ① 写锚点时带上 `metric_date=snapshot["metric_date"]`；② **`_snapshot_created_at`（`:22`）的"平台时间戳语义"不改**、锚点 `created_at` 仍取平台时间戳；③ `collect_platform_anchors(period=...)` 给定时**只采归属日落在该周期窗口内**的行；④ 新增 `attribution_fallback_count(anchors_conn, *, required_since) -> int`（`source='platform_truth' ∧ metric_date IS NULL ∧ created_at 的日期 < required_since` 的行数）与覆盖视图 `attribution_missing_anchors` **相等**；⑤ **机检边界（必须常驻）**：**新采集写入路径产生 `metric_date is None` 的次数恒 0**、历史回退值**必须被计数并在报告可见**（不得静默补值）；⑥ 人评行（`human_blind`）**不得**填 `metric_date`（该列只对 `source == platform_truth` 有语义）。

  **批次 2 登记**: `collect_platform_anchors` 的返回类型保持 `list[AnchorScore]`（未采用 C7 草图的 `tuple[list[AnchorScore], dict]`）——T2037 完成判据未要求换签名，而换签名会打断 `tests/unit/test_platform_anchors.py` 的保留读路径；回退计数改由 `attribution_fallback_count` + 覆盖视图 `attribution_missing_anchors` 承载（与 C9 同值）。另：缺归属日且 `created_at` 的日期 ≥ 生效日的行**不落锚点**（新写入不得走回退）。

- [x] T2038 **周期窗口按归属日过滤（候选过滤键改造）** — 改 `core/calibration/selection.py:108-113`（`:108` 是 `start_ts, end_ts = _period_window(period_start, period_end)` **调用**，`:113` 才是过滤语句 `if node.score is None or not start_ts <= node.created_at < end_ts:`） — **完成判据**: 过滤键由节点 `created_at` 改为**归属日**（无归属日的历史行按 `created_at` 的**日期**回退，并在报告登记**归属日缺失锚点数**）；半开区间（T2022）与该过滤语句对周级**零变化**；迟到/回补按其归属日归入对应周期，**不得静默改写已冻结的历史锚点**；`uv run pytest tests/unit/test_promo_daily_ingest.py tests/contract/test_calibration_contracts.py -q` 中归属日过滤用例转绿、周级用例保持绿。

- [x] T2039 [P] **快照 = 周期物化 + 台账行登记锚点数与指纹（零覆盖、静默改写恒 0）** — 改 `core/calibration/ledger.py:25`（`append_ledger`）、`:55`（`write_anchor_snapshots`）、`:75`（payload 键区） — **完成判据**: ① 新增 `snapshot_fingerprint(payload) -> str` = BLAKE3(canonical JSON：`json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 的 UTF-8 字节)；**稳定性定义**：对同一内容恒等（与缩进、键序、写入形态无关）、只依赖语义内容（**同内容重复物化 ⇒ 同指纹**）、payload 任一键/值变化 ⇒ 指纹变化（含新增 `period_days` / `window_semantics`）；**不写进快照文件**（避免自指），写方与读方各自重算；② 新增 `materialize_anchor_snapshots(...) -> list[SnapshotMaterialization]`（`{path, agent_id, evaluator_id, period, anchor_count, snapshot_fingerprint}`）与 `SnapshotMaterialization` 数据类；`write_anchor_snapshots`（`:55`）保留为**薄封装**（`list[Path]` 签名与返回类型不变，`ops/demo_web.py:284`、`ops/demo_judge_drift.py:129`、`tests/conftest.py:1911` 调用点**零改动**）；③ cadence 由**标签派生**（`cadence_of(period)`）⇒ 既有无 cadence 参数的调用点签名不变；④ 快照 payload 既有键（`core/calibration/ledger.py:75` 起）**逐字保留**，新增 `period_days` 与 `window_semantics`；⑤ 台账行新增五个**可空**字段 `period_days` / `window_semantics` / `round_id` / `anchor_count` / `snapshot_fingerprint`（`core/calibration/models.py:163` 的 `BiasRecord`），由新增纯函数 `with_provenance(record, *, period_days, window_semantics, round_id, anchor_count, snapshot_fingerprint)` 在 `append_ledger` 之前补全（`core/calibration/bias.py:39` 的 `compute_bias` **不改签名**）；⑥ **静默改写恒 0**：内容变化 ⇒ **必有一条新台账行**携带新指纹；同内容重复物化 ⇒ 幂等（零字节写入、零新增台账行、返回同一指纹）；⑦ **主键 =（评估器, 周期）**：该目录下文件数**不随轮数增长**；轮标识**不进**快照路径；⑧ 不变量：`record.anchor_count == record.samples`（非 None 时）、`record.snapshot_fingerprint == snapshot_fingerprint(on_disk)`；⑨ **样本不足不硬判（台账侧）**：`samples < calibration.min_samples`（`configs/*.yaml` 的 `calibration.min_samples`）⇒ 该评估器的台账行**只带 `note`**（含"样本不足"字样、点名评估器），**偏差值为 `None`**（`mean_shift` / `pearson_r` / `kendall_tau` 全 `None`），**不得**产出任何偏差值、**不得**以 `0` 或上一周期值顶替；`run_id` 照常登记（轮次已 closed 这一事实不因样本不足而改变，沿用 `core/calibration/rounds.py:79` 的既有 `insufficient` 口径）；**漂移侧的三值归位见 T2041⑧**（`insufficient` / `no_data` / `no_baseline` 各归其位，本侧只负责"台账行不产偏差值"）。

  **批次 2 登记**: `write_anchor_snapshots` 保持 `list[Path]` 薄封装；`close_round` 改为「先物化 → 取锚点数/指纹 → `with_provenance` → `append_ledger`」，故台账行与快照内容一一对应（内容变化必记账）；无该评估器快照时 `anchor_count=0`、`snapshot_fingerprint=None`（如实标注，不冒充已记账）。

- [x] T2040 **报告按轮并留存（零覆盖）+ 读取口径单点改造** — 改 `core/calibration/report.py:36`（"同周期取末行"）与 `:55`（单文件写）；同步改读取点 `core/calibration/drift_report.py:98`、`web/queries.py:831`、`:842`、`web/parity.py:45` — **完成判据**: ① `reports/{period}.json`（**兼容别名**）只在**文件不存在时**写一次、此后**永不改写**（mtime 与字节均不变）；② 同周期多轮各落 `reports/{period}-{run_id}.json` ⇒ **文件数 == 轮数**、**"后写覆盖前写"次数恒 0**；任何已存在路径的**改写调用必须抛错**（不得"覆盖成功"）；③ `:36` 的"同周期取末行"改为"按轮并留存"；④ 四个读取点改用 `latest_report_path`（优先 `reports/{period}-*.json` 排序末者，否则兼容别名，两者皆无 ⇒ `None`、**不静默取空**）——**周级单轮形态下取到同一份内容** ⇒ `uv run pytest tests/contract/test_web_parity.py -q` 与 `tests/contract/test_drift_contracts.py -q` 保持绿（web 仍**只读**、本特性**不加写入口**）。

  **批次 2 登记（需裁决→见文末「批次 2 待裁决」1）**: ② 的「任何已存在路径的改写调用必须抛错」与「变红清单 #8 的 `tests/contract/test_drift_contracts.py` 保留」冲突——该用例同周期第二次写兼容别名且内容不同（依赖旧覆盖语义）；本批按 T2040② 与不变量 I-4 保留严格零覆盖，并给该用例补 `run_id`（断言零删除、零放宽）。

  **批次 1 登记**: 读取点迁移（`latest_report_path` + 别名回退）**已随批次 1 收口完成**（`core/calibration/drift_report.py`、`web/parity.py`、`web/queries.py` 三处读取口改为"该周期最新轮级报告、无则兼容别名、皆无 ⇒ 无报告"的确定性规则，web 侧按宪章零 import core 只做命名规则定位）；T2040 的其余部分（多轮留存"写一次永不改写"语义）仍留阶段 3。

- [x] T2041 [P] **漂移量纲随 cadence + 登记所读快照指纹与锚点数** — 改 `core/calibration/drift_metrics.py:53`（`_PERIOD_RE`）、`:54`（`_WEEK`）、`:116`（`metric_hash`）、`:358`（`detect_drift`）、`:440`（`DriftMetrics(...)` 构造点） — **完成判据**: ① 标签正则/周期步长/`_period_start`/`_period_label`/`missing_periods` 全部改走 `period_regex(cfg.period_days)` / `period_start(label, cfg.period_days)` / `period_label(day, cfg.period_days)` / `period_window(...)`；② `metric_hash` 的 payload 新增 `period_days` ⇒ **口径变更即新 `detector_version`**（日级与周级不共用同一口径哈希），历史判定**不回溯**（`:331` 的 `write_record` "同内容幂等 / 内容不同即拒绝改写"纪律不变）；③ `detect_drift` 填 `snapshot_fingerprint = snapshot_fingerprint(read_snapshot(...))`；**所读快照的锚点数 = 既有 `samples` 字段**（语义澄清为"该周期快照所依据的锚点数"，**不新增重复字段**）；④ `snapshot_path`（`:140`）/`read_snapshot`（`:145`）**只读路径与签名不变**、缺文件 ⇒ `None`（该周期**不入窗口**、缺口如实报）；⑤ `no_data` 记录 `snapshot_fingerprint is None`；⑥ 日级形态产出 `window_unit == "week"` ⇒ 红；⑦ **010 的产物零写入**（012 只读 010 产物）纪律不变；⑧ **"样本不足 / 无快照 / 首周期无基线"三值各归其位（不硬判，模型层约束常驻）**：`DriftVerdict` 是**五值枚举**（`core/calibration/drift_models.py:74`；成员 `:80-84` = `normal` / `drift` / `insufficient` / `no_baseline` / `no_data`；判定类与如实标注类的划分见 `:75` 的说明与 `:87` 的 `JUDGED_VERDICTS`）——**三值各归其位**：**窗口内样本量 < `calibration.min_samples` ⇒ `verdict == insufficient`**（`psi is None`、`quantile_shifts == {}`、`snapshot_fingerprint is None`；**`baseline_ref` 必须非空**——序列有基线但样本不足，`core/calibration/drift_models.py:237-238`）；**该周期无快照**（`read_snapshot` 返回 `None`）⇒ `verdict == no_data`（该周期**不入窗口**、缺口逐段如实报；`baseline_ref` 可空，`:240`）；**首周期无基线** ⇒ `verdict == no_baseline`（**不得**携带 `baseline_ref`，`:234-236`）；**非判定类（`insufficient` / `no_baseline` / `no_data`）一律不得携带 `psi` / `quantile_shifts`**（模型层约束 `core/calibration/drift_models.py:219-227`，违反即 `ValidationError`），**判定类（`normal` / `drift`）必须携带**（`:209-218`）；**不得**把"样本不足"写成 `no_data`、**不得**把 `insufficient` 降级成 `normal`/`drift`、**不得**插值或用上一周期顶替；三种标注类的原因必须在记录的 `note` 里可见（分别含"样本不足" / "无快照" / "首周期无基线"字样，`core/calibration/drift_models.py:185` 的 `note` 口径）。

  **批次 2 登记**: 新增 cadence 一致性校验（`cadence_of(period) != cfg.period_days` ⇒ `ValidationError`），使「日级形态产出 window_unit == week」不可能发生；`DriftMetrics.snapshot_fingerprint` 的模型层约束只做**禁止**（标注类携带即 `ValidationError`）而**不做**「判定类必填」——后者会打断 `test_drift_gate.py` / `test_drift_report.py`（非变红清单、属保留）的直接构造点，故「判定类必非空」的机检落在 `detect_drift` 的**产出**上（T2011③ 的用例）。

- [x] T2042 [P] **漂移配置增 `period_days` / `window_unit`（缺项即报错）** — 改 `core/calibration/drift_config.py:96`（`DriftConfig`）与 `:185`（`thresholds_snapshot`） — **完成判据**: `DriftConfig` 新增 `period_days: int`（取自 `calibration.period_days`，**缺项 ⇒ `CalibrationConfigError`**、不取码内默认）+ 属性 `window_unit = {1:"day", 7:"week"}[period_days]`；`thresholds_snapshot()` 新增 `"window_unit"` 与 `"period_days"` 两键；**`window: 3` 的单位 = cadence 同量纲**（日级 = **3 天**、周级 = 3 周），修掉 `configs/shortdrama.yaml:408` 的注释与实际不符；`uv run pytest tests/unit/test_drift_config.py -q` 的阈值快照全等断言按**扩展**更新（`tests/unit/test_drift_config.py:76` 的断言**不得删键换取通过**）、`tests/unit/test_drift_config.py:131` 的"缺项即红"参数化须增 `period_days` 缺项条目；`tests/unit/test_drift_versioning.py:49` 保持绿（按 `detector_version(...)` 现算、不写死哈希）。

  **批次 2 登记**: `period_days` 由 `calibration.period_days` 必需读取（缺项/域外 ⇒ `CalibrationConfigError`），`tests/unit/test_drift_config.py` 的两处夹具随之扩展（`_write_drift` 补 cadence 键、阈值快照断言**加键不删键**）；`tests/unit/test_form_switch.py` 的 T2015② 已按批次 1 登记强化为对 `DriftConfig.period_days` / `window_unit` / `thresholds_snapshot` 的**直接**断言，并修正 `configs/shortdrama.yaml` 的 `window: 3` 注释为「单位 = cadence 同量纲」。

  **批次 1 登记**: 阶段 3 实施 T2042 时，须把 T2015② 的**间接**断言（`CADENCE_UNIT[cadence_of(period_label(...))]`）**强化**为对 `DriftConfig.period_days` / `window_unit` / `thresholds_snapshot` 的**直接**断言（含 `configs/shortdrama.yaml:408` 注释与实际不符的修正）。

- [x] T2043 **`ops/ingest_metrics.py` 薄参数（CLI 不承载第二份口径）** — 改 `ops/ingest_metrics.py` — **完成判据**: 新增 `--source {real,simulated}`（**必填**；`real` ⇒ `HttpRealPlatform.from_env()`，缺凭证 ⇒ 装配期显式拒绝、**退出 2**、零落盘零扣费；`simulated` ⇒ 仅离线/演示）与 `--daily` / `--metric-date` / `--coverage` / `--end` / `--min-window-days` / `--gap-tolerance-days`；覆盖检查在 `meets is False` 时**退出 1**（如实报未达标）、参数/配置错误**退出 2**、达标**退出 0**；覆盖窗口是**派生视图、不落盘**（可机检证据 = `promo_daily_metrics` 的 append-only 行 + 台账/报告）；`--min-window-days` / `--gap-tolerance-days` 缺省取自 `budget.runs.*`（**不取码内默认**）；所有判定仍只在 `agents/promo/daily.py`（**CLI 零判定逻辑**）。

  **批次 2 登记**: `--source simulated` 经 CLI **子进程**无法取到另一进程创建的活动（模拟平台是进程内状态，015 既有性质）⇒ 该组合的端到端只在同进程（demo/单测）内验证，生产走 `--source real`；`ops/check_credentials.py` 的提示串同步补 `--source real`（文档同步，无断言变更）；CLI 执行异常收口为退出 1。

- [x] T2044 **阶段 3 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: `uv run pytest tests/unit/test_metric_attribution_date.py tests/unit/test_promo_daily_ingest.py tests/unit/test_drift_cadence.py -q` **转绿**；`uv run pytest tests/unit/test_platform_anchors.py tests/unit/test_drift_detect.py tests/unit/test_anchor_snapshots.py tests/unit/test_drift_config.py -q` 按扩展更新后**绿**；`uv run pytest tests/contract/test_calibration_contracts.py -q` 的**周级用例一字不改且绿**、日级新用例绿；`uv run pytest tests/unit/test_ingest_metrics.py -q` 保持绿（末位可选 ⇒ 既有构造零改动）；**必须逐条点名核对的三项**：① **样本不足 / 无快照 / 首周期无基线三值各归其位**：`samples < calibration.min_samples` ⇒ 台账行只带 `note`、偏差值为 `None`，漂移 `verdict == **insufficient**`（`psi is None`、`quantile_shifts == {}`）；**无快照 ⇒ `verdict == no_data`**；**首周期无基线 ⇒ `verdict == no_baseline`**；**非判定类（`insufficient`/`no_baseline`/`no_data`）不得携带 `psi` / `quantile_shifts`**（`core/calibration/drift_models.py:219-227` 的模型层约束，违反即 `ValidationError`）——由 `tests/unit/test_promo_daily_ingest.py` 与 `tests/unit/test_drift_cadence.py` 各一条用例覆盖；② 覆盖 ∧ 连续判定**只有一处实现**（`coverage_window`），`agents/promo/` 零自算 `meets`/`max_gap`（静态断言）；③ `daily_coverage` 的输出键与 `specs/020-shortdrama-real-feedback/contracts/daily-ingest.md:291` 的 C9 清单**逐键对齐**（缺键即红）。**登记**: `ops/migrations/versions/0011_daily_feedback.py` 的真实迁移执行与双方言触发器验证属 `tests/integration`（见 T2080，**由父代理在宿主机执行**）。

**检查点**: ✅ 同一活动跨多日各采一次**合法且零覆盖**（唯一性键 =（活动, 周期））、同（活动, 周期）重复采集**幂等拒绝**；锚点写入即冻结（存储层拒改写）；快照 = 周期物化、内容变化必记账、**静默改写恒 0**；漂移窗口同量纲、每周期读一份、登记所读快照指纹与锚点数；日级窗口**覆盖 ∧ 连续**双条件机检通过、断档**逐段**报出、插值次数恒 0；**新采集写入路径产生 `metric_date is None` 的次数恒 0**。

---

## 阶段 4：US2 —— C 路径投放接入 + 019 渠道命名空间（T2045~T2058）

**目标**: 给 019 的 `budget` 段补**渠道命名空间**（`channels.<id>.tiers.<环节>`）并**放开单渠道硬拒绝**（`sole_channel()` 退役 → 按配置声明的渠道集合分派额度），把 C 路径的**投放调用**接进**同一套前置门禁**（**禁止旁路门禁**）；凭证缺失即**装配期拒绝启动**（绝不静默回落模拟）；首次接入按**最小规模**验证协议与计费口径。

**独立测试**: `uv run pytest tests/unit/test_billing_channels.py tests/unit/test_promo_delivery_gate.py -q`（本阶段完成时 T2012/T2013 转绿）；**019 既有断言面按扩展更新后全绿且用例数不减**（C13 清单）。

**⚠️ 关键（对 019 的兼容性扩展，不是削弱）**: 处理原则**只有一条**——"**按扩展更新、不削弱**"（`specs/020-shortdrama-real-feedback/research.md:529` 决策 10）；**零删断言、零放宽**；两条红线单列回归：`tests/contract/test_billing_contracts.py:1146`（`--channel nope` ⇒ 2）与 `.github/workflows/billing_alerts.yml:27`（每日只读告警，**文件一字不改**）。

### 阶段 4 的实现

- [x] T2045 **渠道命名空间：`tiers` 由扁平改为按渠道分组（同一档位不得跨渠道串用）** — 改 `core/billing/budget.py:275`（`tiers: Mapping[str, BudgetTier]`）、`:318`（`tier()`）、`:344`（`to_snapshot()`）、`:1016`（`_parse_channels`）、`:1063`（`_parse_tiers`） — **完成判据**: `channels.<id>.tiers.<环节>` 成为唯一形状；`ChannelSpec` 扩 **1** 键 `tiers`（**必填非空映射**，键 = 环节 id）；`_parse_channels` 逐键校验：缺 `tiers`、`tiers` 为空、`adapter` 缺失或空串、`tiers` 内某档缺 `limit_usd` / `window.kind` / `on_exhausted` ⇒ **一律 `BudgetConfigError`**（**缺项即报错、不取码内默认**）；`to_snapshot()` 只落**本渠道**档位；`bill` 与档位 `note` 的可追溯纪律**逐字沿用 019**；**`adapter` 的取值域与语义不变**——既有 `pilot_llm` **原样保留、不重命名**（**不做**"收敛为槽位 id"的改造）。

- [x] T2046 **`sole_channel()` 退役 → 按配置声明的渠道集合分派（含旧扁平形状兼容读）** — 改 `core/billing/budget.py:357`（`sole_channel`），新增四个接口 — **完成判据**: ① 新增 `declared_channels(cfg) -> tuple[ChannelSpec, ...]`（声明顺序的**全部**渠道；空 ⇒ `BudgetConfigError`）与 `channel_for_adapter(cfg, adapter_id) -> ChannelSpec`（**恰好 1** 个渠道的 `adapter == adapter_id` ⇒ 返回；**0** 个 ⇒ 报错"该装配入口未登记渠道，不发明"；**≥2** 个 ⇒ 报错"歧义，不猜用哪个账本"）；② 另有 `tiers_of(channel_id)` / `tier_of(channel_id, tier_id)` 与 `BudgetConfig.tiers_shape`（取值域 `("channels", "legacy_flat")`）；`BudgetConfig` 侧只作**同名薄委托**（单一实现）；③ 既有 `tiers`（`:275`）与 `tier()`（`:318`）降级为**单渠道兼容视图**（仅在声明集合大小为 1 时可用；多渠道下访问 ⇒ `BudgetConfigError`）；`tier_of` 的缺档错误文案**必须保留「未在 budget.tiers 声明」**（`tests/unit/test_billing_config.py:47` 依存）；④ **旧扁平形状仍可读**（配置恰好声明 1 个渠道 ⇒ 显式归入该渠道、`tiers_shape = "legacy_flat"`、`cfg.notes` 追加归一事实与渠道 id；**≥2 个渠道 + 扁平形状 ⇒ 报错**；新旧两处同时出现 ⇒ 报错）；⑤ **静默归并次数恒 0**；⑥ 归一**只发生在读入内存**这一步，**不触发任何落盘改写**；⑦ `core/billing/` 零渠道 id / 零形态字面量 / 零形态分支；⑧ 声明顺序约定：两形态 `declared_channels(cfg)[0].adapter == "pilot_llm"`。

- [x] T2047 **`assemble_guard` 扩参 `channel_id=` + 守卫按渠道取档** — 改 `core/billing/budget.py:433`（`assemble_guard`，签名 `:420-461`）、`:841`、`:848`（`SpendGuard.check` 的档位查表） — **完成判据**: ① `assemble_guard(config_path, *, channel_id=None, window_context=None, clock=None)`：**装配序列与返回的 `GuardAssembly` 形状不变**；装配点先用 `channel_for_adapter(cfg, <该装配入口的 adapter 取值>)` 解析再传 `channel_id=`（**解析只有一个实现**，装配点不各写一份）；`channel_id` 传入时须 ∈ `declared_channels(cfg)`（否则报错）；`channel_id` 缺省时：声明数 == 1 ⇒ 取该渠道（**019 既有 `assemble_guard(config_path)` 单渠道调用保留可用**）、> 1 ⇒ 报错（不猜）；② `:841` / `:848` 改走 `self.cfg.tiers_of(self.channel_id)`；`snapshot()` 只落本渠道档位；**`channel_mismatch` 拒绝保留**（请求渠道 ≠ 守卫渠道即拒绝 ⇒ **跨渠道串用次数恒 0**）；③ 装配期 `cfg.channel(self.channel_id)` 校验保留（**未登记渠道不得装配门禁**）；④ 五类产物路径与形状**逐字不变**（`core/billing/budget.py:475` 的 `channel_dir` 等），历史文件**零回改**。

- [x] T2048 **`ops/billing.py`：`--channel` 语义保留 + 新增只读 `channels` 子命令** — 改 `ops/billing.py:102`（`_channel_of`）、`:119`（档位行渲染）、`:172`（缺档判定）、`:5`（docstring 子命令清单） — **完成判据**: ① `_channel_of` 改为**按声明渠道集合分派**：`requested ∈ {spec.channel_id for spec in declared_channels(cfg)}` ⇒ 用之，否则 `BudgetConfigError` ⇒ **退出 2**，错误文案**必须保留「不一致」子串**（`tests/contract/test_billing_contracts.py:1290` 依存）；② `:119` 改 `cfg.tiers_of(channel_id)`、`:172` 改 `cfg.tier_of(channel_id, args.tier)`（错误文案**保留环节名**，`tests/contract/test_billing_contracts.py:1274` 的 `ghost` 子串依存）；③ 新增 `channels` 子命令（**只读**，不写账本/报告）：声明渠道集合 → 每渠道 `adapter`（装配入口）归属 → 每渠道 `tiers` 键集/余量/拒绝计数 → **凭证就绪矩阵**（只 `set`/`length`，**绝不回显值**）→ 每渠道账本/告警路径；退出码 0，缺项或不一致 ⇒ **2**；④ `:5` 的「**七条齐备**」措辞同步为**八条**（**属文档同步、不是断言削弱**，`tests/contract/test_billing_contracts.py:1129` 对**既有七条**逐条 `--help` 断言退出 0 的用例**一字不改**）；⑤ `ops/billing.py:40` 的退出码语义**逐字保留**。

- [x] T2049 [P] **新增投放调用门禁包装 `RecordingChannelCall`（与 `RecordingGateway` 同构，同处无第二份实现）** — 改 `core/billing/runlog.py`（新增，参照 `:313` 的 `RecordingGateway`；`RUN_SOURCES`（`:28`）与 `RUN_ENTRY_FIELDS`（`:32`）与 `append_run`（`:115`）**不变**） — **完成判据**: ① 包装在 `create_campaign(...)` **之前**取门禁判定（`guard.check(request)`，`stage` = 该渠道的投放环节 id）、之后 `reservation.settle(actual_spent)`（**实测超预估如实入账 + `over_limit` 告警**，019 口径不变）；② `source` 由**装配面显式声明**（取值域 `RUN_SOURCES`；`fallback` 必须带非空 `fallback_reason`，`core/billing/runlog.py:138` 的硬校验口径；本特性**不新增回落路径**）；③ 拒绝 ⇒ 平台调用 **0 次**、成本 **0 入账**、`source` 记 `refused`（`core/billing/runlog.py:362` 的既有语义）、`billing/{channel}/alerts.jsonl` 落 `kind=budget_refused`；④ 真实渠道失败**禁止**静默回落模拟并照常计费；⑤ 静态断言：`RecordingChannelCall` 是投放面**唯一**的调用包装点。

- [x] T2050 **装配点接线：链内 LLM 装配点 + 投放装配点（不新增第三个装配点）** — 改 `agents/pilot/backends.py:280`（`budget = assemble_guard(config_path)`）、`:289`（`spend_guard=` 注入点）、`:399` 起的 `_promo` 真实分支 — **完成判据**: ① `:280` 改为 `assemble_guard(config_path, channel_id=channel_for_adapter(cfg, "pilot_llm").channel_id)`（`channel_id` 由**装配引用解析**而来，**不再**经 `sole_channel`）；`:289` 的 `spend_guard=` / `channel_id=` / `peak_windows=` 注入点**不变**（`:296`）；② **投放面**：`_promo` 增入参 `config_path`（`build_backends` 已持有该参数；**改签名、不加分支**），其真实分支外层包 `RecordingChannelCall`（T2049）；③ 声明真实而 `PROMO_PLATFORM_*` 缺失 ⇒ **装配期显式拒绝启动**、点名缺哪个变量、**零落树零扣费**、**绝不静默回落模拟**（经 `:323` 的 `_guard` 收口为装配错误）；④ **不新增第三个真实装配点**（019 C10②"唯二"不变：`agents/pilot/backends.py:280` + `ops/smoke_llm.py:206`）；⑤ **代码侧 adapter id 与配置一致率 100%**：`agents/` 内传出的 adapter id ∈ 两形态之一的 `budget.channels.*.adapter` 声明集（新增装配入口未登记配置 ⇒ 红）。

- [x] T2051 **最小规模校准入口按新口径（LLM 渠道语义不变）** — 改 `ops/smoke_llm.py:206`（`budget = assemble_guard(config_path)`） — **完成判据**: 同 T2050 的 `channel_for_adapter(cfg, "pilot_llm")` 口径；**019 的 LLM 冒烟口径不变**（`--help` 退出 0、既有校准记录字段与阈值语义逐字保留）；**明确登记**：投放渠道的最小规模入口**不是** `ops/smoke_llm.py`，而是投放侧（`uv run python ops/billing.py calibrate --channel media --tier <投放环节> --from-records --measured-usd <实测> --config configs/shortdrama.yaml`，只读既有运行记录与账本、**不联网、不构造后端、不新测花费**；见 T2012⑧ 与 T2054）；**不得**为投放渠道假定 019 的 LLM 冒烟入口（`specs/019-real-channel-billing/spec.md:102`）。

- [x] T2052 [P] **配置：短剧态登记投放渠道（两形态 schema 齐备；movie 不登记投放渠道）** — 改 `configs/shortdrama.yaml:512`（`budget:`）与 `configs/movie.yaml:509`（`budget:`） — **完成判据**: ① `channels.<llm 渠道>.tiers.<环节>`：把既有扁平 `tiers`（`configs/shortdrama.yaml:544`、`configs/movie.yaml:541`）**归位**到 LLM 渠道下，`adapter` 取值 `pilot_llm` **原样保留**；② 短剧态新增 `channels.<media>（adapter 按该渠道真实装配入口命名）` + `bill`（格式 id + 来源形态 + 列映射 + 分类驱动列取值域，复用 019 的导入面）+ `tiers.<投放环节>`（`limit_usd` 按**最小规模档**并在 `note` 标注"未标定：运营给定前按最小规模档运行"）；③ **`configs/movie.yaml` 不登记 media 投放渠道**——"两形态均须声明"指**新增参数键的 schema 齐备**（两形态都能加载、缺键即报错），**不是**要求两形态登记同一渠道集合；④ 投放渠道的**账单导入与对账复用 019 不重造**（走 `core/billing/bill.py:321` 的 `normalize_bill` 与 `core/billing/reconcile.py:233` 的 `reconcile`）；⑤ 额度数字**先按最小规模档 + `note`"未标定"**，**不得发明数字**（属运营侧输入，spec 开放问题 2）；⑥ **媒体（投放）渠道的账单对账专属机检（U-04）**：夹具账单以 `uv run python ops/billing.py import-bill --channel media --bill <夹具> --config configs/shortdrama.yaml` 导入（⇒ 退出 0、落 `billing/media/bills/`），再以 `uv run python ops/billing.py reconcile --channel media --period <周期> --config configs/shortdrama.yaml` 对账 ⇒ **六类差异逐项分类齐备**（计费口径 / 未入账 / 时序错位 / 免费额度与折扣 / 币种汇率 / 未结账）、**不可解释项 100% 告警**（无分类 ⇒ `unexplained` ⇒ `alerts.jsonl` 落 `unexplained_delta` 且退出码 ≠ 0）、**报告必须含所引账单批次（`bill_id`）**（缺即拒绝产出，不产"零差异"报告）；**全部复用 019 的 `core/billing/bill.py:321` 与 `core/billing/reconcile.py:233`，不得新造第二套对账**；断言落 `tests/unit/test_billing_channels.py`（T2012 的同一文件）并经 T2086 纳入 `tests/adversarial/` 的薄封装。

- [x] T2053 [P] **`config_completeness` 预检按新形状取档（文案保留）** — 改 `agents/pilot/pilot.py:446`（`cfg.tiers` 非空校验 + 逐档 `limit_usd`） — **完成判据**: 改经 `cfg.tiers_of(channel_for_adapter(cfg, "pilot_llm").channel_id)`；**文案「预算不可用：budget.tiers 为空」保留**；`:377` 的 `config_completeness` 加载器清单的**增量登记**归 T2067（同文件 ⇒ 与 T2067 **串行**执行，不得并行）。

- [x] T2054 [P] **凭证就绪矩阵增投放渠道条目（只报 `set`/`length`，零凭证值）** — 改 `ops/check_credentials.py` — **完成判据**: 形状 = 渠道 → `adapter`（装配入口）→ `{env: {set: bool, length: int}}` → `ready: bool` → **缺失时的拒绝语义**；变量名以 `agents/promo/platform/http_real.py:143` 与 `PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY` 为**权威**（`docs/pilot-upgrade-manifest.json:136` 的 `credential_envs` **不改名、不新增变量**，反向机检由 `tests/unit/test_credential_env_lock.py` 承担）；**绝不回显值**、**不进产物**；**矩阵按形态声明生成**——movie 的矩阵**只含其声明的渠道**（**不生成投放行、不触发任何投放凭证判定**）；声明模拟 ⇒ 该渠道 `ready=true` 且 `pilot` 后端取值 = `simulated`，两条路径在报告里**可辨、不混同**。

- [x] T2055 **019 契约断言按扩展更新（C13.1 十二处 + C13.2 附加表，逐条零删除）** — 改 `tests/contract/test_billing_contracts.py`（12 处 `--channel` 调用点 + `:603`、`:608`、`:146`、`:1129`、`:986`、`:1042`、`:1207`、`:1270`） — **完成判据**: 逐条按 `specs/020-shortdrama-real-feedback/contracts/channel-budget.md:249` 的 C13.1 表与 `:283` 起的 C13.2 附加表执行——① **10 处"保留"**（`:995`/`:1013`/`:1037`/`:1048`/`:1144`/`:1146`/`:1149`/`:1155`/`:1216`/`:1238`：**断言与退出码一字不改**；其中 `:1144` **须在多渠道配置下也退出 0**——这正是"退役单渠道硬拒绝"的验收点；`:1146` 是**硬要求、不得改**）；② **2 处"更新（最小）"**（`:1274` 文案**必须保留 `ghost`**、`:1290` 文案**必须保留「不一致」子串**，退出码仍 2，**用例体不改**）；③ 附加表：`:608` 改取各渠道 `channels.<id>.tiers` 的键**并集**（调用点 `stage=` 仍须 ∈ 声明键集，**计数仍为 8**）、`:603` 改 nested 路径（缺 `limit_usd` 仍须报错、文案仍含 `limit_usd`）、`:146` 与 `:1129` **保留**（`adapter` 键仍在；新增第 8 条子命令不破坏既有七条 `--help` 循环）；④ **零"删除"结论、用例数不减少**。

- [x] T2056 **019 单测与夹具按扩展更新（含"夹具假绿"高风险点）** — 改 `tests/unit/test_billing_config.py:45`、`:47`、`:156`、`tests/unit/test_billing_paths.py:114`、`tests/unit/test_billing_snapshot_freeze.py:72`、`tests/unit/test_billing_ledger_concurrency.py:215`、`tests/unit/test_billing_core_purity.py`（见 T2017）、`tests/conftest.py:4109` 与 `:4127`、`ops/demo_billing.py` 与 `tests/unit/test_billing_{cli,alerts,reconcile,calibration,bill_import}.py` 的 `--channel` 调用点 — **完成判据**: ① `tests/unit/test_billing_config.py` 的 `:45`/`:47` 改 `cfg.tiers_of(<llm 渠道>)` / `cfg.tier_of(<llm 渠道>, "…")`（**文案「未在 budget.tiers 声明」保留**）、`:156` 改 nested 键（**四条取值域/缺项断言强度逐条保留**）；② `tests/unit/test_billing_paths.py:114`（`test_两渠道同周期互不覆盖`）**扩展构造面**（把 `tiers` 键移到渠道下）而**语义断言保留**（两渠道账本互不覆盖、同一档位不跨渠道串用），并**新增**"多渠道 + 扁平 `tiers` ⇒ 歧义报错"负例；③ `tests/unit/test_billing_snapshot_freeze.py:72` 与 `tests/unit/test_billing_ledger_concurrency.py:215` 改按渠道取档（快照只含本渠道档位）；④ **`tests/conftest.py:4109` 的 `_billing_budget_payload` 与其 `:4127` 的档位遍历必须同步**——按**装配引用**收敛为**单渠道**并把该渠道档位以**旧扁平 `tiers`** 暴露，使 `billing_budget_factory(...)["tiers"]` 与 `budget_config_factory(tiers=…)` 的**调用体零改动**，同时顺带覆盖 C11 的旧形状读路径（**这是"夹具假绿"的高风险点**：夹具若静默不压额度，门禁用例会以错误前提通过）；**分工（去重登记，D-01①）**：**本项 = 夹具改造的实现处**，T2067⑤ **只做一致性核销**（不重复实现）；⑤ `ops/demo_billing.py` 的 `assembly.cfg.tiers` 改按 `assembly.channel_id` 取档（**离线六步语义与退出码 0 不变**）；⑥ 019 单元套件的 `--channel` 调用点（`tests/unit/test_billing_cli.py:125` 缺 `--channel` 与 `:162` 的 `no-such-channel` **仍必须退出 2**）逐条按同规则处理。

- [x] T2057 **每日告警门禁：工作流文件一字不改 + 回归断言** — 不改 `.github/workflows/billing_alerts.yml`（`:27` = `uv run python ops/billing.py alert-check --channel llm --config configs/movie.yaml`） — **完成判据**: ① 该文件**零字节改动**（`git diff --exit-code .github/workflows/billing_alerts.yml` 为空，或人工核对哈希不变）；② `alert-check --channel llm --config configs/movie.yaml` **必须继续通过**（`llm` 仍 ∈ movie 的声明集；退出码 0 无告警 / 1 有告警 / 2 用法或配置错误；**冷启动无报告放行**口径逐字不变）；③ 本机复核命令 `uv run python ops/billing.py alert-check --channel llm --config configs/movie.yaml`；④ **禁止**为适配新形状而改该行的 `--channel` 取值（改了等于制造"工作流随特性漂移的伪证据"）。

- [x] T2058 **阶段 4 快速核对（子代理可跑的单文件子集）** — 无新文件 — **完成判据**: `uv run pytest tests/unit/test_billing_channels.py tests/unit/test_promo_delivery_gate.py -q` **转绿**；`uv run pytest tests/unit/test_billing_config.py tests/unit/test_billing_paths.py tests/unit/test_billing_core_purity.py tests/unit/test_billing_snapshot_freeze.py -q` **绿且用例数不减**；`uv run pytest tests/contract/test_billing_contracts.py -q` **全绿且用例数不减（本仓当前基线 41 passed，不得减少）**；三条命令行核对：`uv run python ops/billing.py channels --config configs/shortdrama.yaml`（⇒ 0，含 `llm` + `media` 与凭证矩阵）、`--config configs/movie.yaml`（⇒ 0，**不出现投放渠道行**）、`uv run python ops/billing.py tiers --channel nope --config configs/movie.yaml`（⇒ **2**）。**登记**: 019 契约套件的完整两条腿（含 stub 口径）属 T2079（**由父代理在宿主机执行**）。

**检查点**: ✅ 多渠道并存时额度与账本按渠道分派正确率 100%（跨渠道串用恒 0）；旧扁平形状可读率 100% 且零静默误判；**两形态都能装配通过**、movie 路径不因投放渠道或其凭证失败；超预算投放**调用前拒绝、平台 0 次调用、零入账**；015 按轮上限与 019 账本**共同生效且两腿可辨**；凭证缺失 ⇒ 装配期拒绝启动、零静默降级；`billing_alerts.yml:27` 与 `:1146` 两条红线**未削弱**。

---

## 阶段 5：US3 —— 校准结论迁移回灌电影线（T2059~T2065）

**目标**: 把短剧线积累的评估器校准结论（信度 / 偏差 / 漂移）按**显式声明的迁移口径**回灌电影线作为**先验**：每条迁移件带**来源标识 + 迁移口径 + 可比性条件与判定**；可比性不成立 ⇒ **拒绝迁移并如实登记原因**（**不得**降格为"仅供参考"）；迁移动作 append-only、走**人工两键**；**只迁结论、不迁权重**（**禁止**自动改权重、**禁止**改动任何既有节点的 `eval_breakdown` 与得分）。

**独立测试**: `uv run pytest tests/unit/test_calibration_transfer.py tests/contract/test_transfer_contracts.py -q`（本阶段完成时 T2014 转绿）；无常量断言（哈希类断言按 `detector_version(...)` 现算）。

**⚠️ 关键**: 迁移面的**只读承诺**——跑迁移后 010 的台账/快照/报告/漂移产物**逐字节不变**；迁移件落**新目录** `transfers/`（与 `proposals/` 并列），**不触碰** `proposals/`、**不调用** `confirm_proposal`。

### 阶段 5 的实现

- [x] T2059 **新增 `core/calibration/transfer.py`（机制件：迁移件生成 + 可比性判定 + append-only 读回）** — 新增 `core/calibration/transfer.py` — **完成判据**: ① **业务无关**——零形态字面量、零形态分支（形态经**配置**声明；该纪律由 `tests/unit/test_form_switch.py:309` 既有断言常驻守护）；**不得** import `core/calibration/refit.py`（静态断言）；② 迁移件字段面与 `specs/020-shortdrama-real-feedback/data-model.md:293` 实体 10 **逐字对齐**：`transfer_id` / `source_form` / `target_form` / `evaluator_key`（`id@version`）/ `period` / `samples` / `source_ref`（来源件引用：台账行或报告/快照/漂移路径 + `digest`）/ `transfer_basis` / `comparability`（`conditions[]` + `verdict` + `reasons[]`）/ `conclusion`（**只含** `kendall_tau` / `pearson_r` / `mean_shift` / `samples` / `drift_status`）/ `status` / `confirmed_by` / `confirmed_at` / `overrides[]` / `created_at` / `system_digest`；③ `period` 由形态声明的 cadence **派生**（日级 ⇒ 日期、周级 ⇒ ISO 周，走 T2019 的 `period_label`）；④ `transfer_id = <evaluator_id>-<period>-<source_form>-<target_form>`；⑤ **来源只读**：只读台账（`core/calibration/ledger.py:25` 的产物）、锚点分布快照（`:84`）、信度报告（`core/calibration/report.py:55`）、漂移产物（`core/calibration/drift_metrics.py:140`）与 `core/calibration/drift_report.py:98` 的读取口径，**零写入**这些目录；⑥ 判定：`verdict == "transferable"` ⇔ **全部**条件 `satisfied`，否则 `not_transferable` 且 `reasons` **逐条点名命中的是哪一条**；⑦ 不变量：`verdict == "not_transferable" ⇒ status != "confirmed"`（**误迁移次数恒 0**）。

- [x] T2060 **迁移件 append-only 存储（同键重产拒绝、改写拒绝）** — 落点：`core/calibration/transfer.py` + 目录 `{data_dir}/transfers/`；复用 `core/billing/bill.py:406`（`system_digest`）与 `:416`（`write_snapshot`）的摘要与写入语义 — **完成判据**: ① 落盘 `{data_dir}/transfers/{transfer_id}.json`（镜像 `core/calibration/refit.py:141` 的 `proposals/{proposal_id}.json` 先例）；② **同 `transfer_id` 重产 ⇒ 拒绝**；③ **系统字段**（`comparability` / `conclusion` / `source_ref` / `samples` / `period`）**逐字节不变**——改写 ⇒ `system_digest` 校验失败 ⇒ 拒采信；④ 采纳/搁置只**追加**一行 `overrides[]`（人 / 时间 / 理由）并回填 `confirmed_by` / `confirmed_at`，`status` 由 `overrides[]` **末条派生**（`pending → confirmed | shelved`，终态不可逆）；⑤ **镜像 `core/degraded/evidence.py` 的摘要范式**（复用既有实现，**不新造第二套**）；⑥ 静态断言：`transfer.py` 零 `core.calibration.refit` import；**零写树/写库入口**。

- [x] T2061 **新增独立脚本 `ops/transfer.py`（四子命令，薄转发 + 同套退出码语义）** — 新增 `ops/transfer.py` — **完成判据**: ① 四个子命令 `transfer` / `transfer-confirm` / `transfer-shelve` / `transfer-report`，与 019 的 `ops/billing.py` **同风格**（`ops/billing.py:40` 的退出码语义 **0/1/2**）——`0` 成功（含 `transfer-report` 无不可迁移项）｜`1` 执行失败或拒绝（迁移被拒 / 同键重产被拒）｜`2` 用法或配置错误（跨形态未声明 `calibration.transfer`、可比性条件缺项、`--channel nope`）；② **`--dry-run` 只做条件判定并打印预览、零落盘**（目录不新增文件）；③ **薄转发纪律**：CLI 只解析参数与打印 JSON，判定与落盘全在 `core/calibration/transfer.py`；**四个子命令不写任何权重、不调 `confirm_proposal`**（`ops/calibrate.py:222-252` 那条路径只服务 010 的权重提案，二者**互不调用**）；④ `transfer-report` 只读既有件 + 既有台账/快照/报告/漂移产物，**零写入**；⑤ **不挂在 `ops/calibrate.py` 上**（`ops/calibrate.py` 既有八个子命令**一字不变**）；⑥ 各入口 `--help` 退出 0。

- [x] T2062 [P] **可比性条件配置化（两形态均须声明、缺项即报错、不取码内默认）** — 改 `configs/movie.yaml` 与 `configs/shortdrama.yaml` 的 `calibration` 段（新增 `transfer`） — **完成判据**: ① 六键齐备 `calibration.transfer.{basis, source_forms, target_forms, conditions, storage, adoption}`（`basis` 取值域单元素 `conclusion_only`、`adoption` 取值域单元素 `manual`、`storage.dir = transfers`）；`conditions` 的**键集 = 判定项清单**，至少覆盖五类：来源评估器在目标形态**同 id 同 version** 已注册（版本冻结，原则一）／来源样本量满足目标形态的样本下限／两形态判定口径哈希一致（如漂移 `detector_version`）／**周期量纲必须明确**（日级 → 周级的结论**不得直接比**，要迁移必须声明显式换算口径，未声明即不可迁移）／来源必须是**真实来源**（`RUN_SOURCES` 口径，`core/billing/runlog.py:28`；模拟回流结论**不得**迁移）；② **缺任一项 ⇒ 拒绝（退出码 2）**、不取码内默认（FR-014）；③ 两形态都声明的是**键的 schema 齐备**，取值随形态不同（`source_forms` / `target_forms` 各自声明）；④ 配置项 → 产物字段的映射**口径单一**（`basis` → `transfer_basis`；`conditions` 的**键集** → `comparability.conditions[].id`；`adoption: manual` → `status` 只能经人工两键迁移，**无自动采纳路径**）。

- [x] T2063 **迁移件"零权重键 + 零自动改权重"的结构与静态断言** — 落点：`tests/unit/test_calibration_transfer.py`（断言本体）+ `core/calibration/transfer.py` — **完成判据**: ① **结构断言**：`conclusion` 内出现 `candidate_weights` / `current_weights` / `fit_objective` / `ridge_lambda` 任一键 ⇒ 红（对照 `core/calibration/refit.py:141` 起的提案形状）；② **静态断言**：`core/calibration/transfer.py` 与 `ops/transfer.py` 零 `core.calibration.refit` import、零 `confirm_proposal` 调用；③ 采纳动作**零写树/写库入口**（排查 `agents/` 与 `core/tree/` 的引用为 0）；④ 迁移件**必带** `source_form` / `target_form`，短剧线数字**不得**出现在电影线证据面（节点 `eval_breakdown`、信度报告、漂移产物）——**落盘路径断言 + 静态断言**双条；⑤ 迁移件与 010 产物的读取**只读**：跑迁移后这些文件的摘要**逐字节不变**。

- [x] T2064 **"不改既有节点"的逐字节机检 + 人工两键** — 落点：`tests/contract/test_transfer_contracts.py` 复用 `tests/contract/test_calibration_contracts.py:1` 的"历史节点逐字节一致"机检口径（SC-006 口径） — **完成判据**: ① **采纳不改变任何既有节点的 `eval_breakdown` 与得分**（逐字节一致；权重零改动）；② **人工两键**：`transfer`（提案）→ `transfer-confirm` / `transfer-shelve`（人工），状态机与 010 的 `ProposalStatus`（`core/calibration/models.py:60`、`:226` 的 `confirm`/`shelve` 纪律）**同构**；③ **不可迁移 ⇒ 拒绝迁移**并如实登记原因（**不得**静默丢弃、**不得**降级为"参考"数字）；④ `not_transferable ⇒ status != confirmed` 的**误迁移次数恒 0**；⑤ 改写/省略尝试 **100% 被拒**（append-only）。

- [x] T2065 **无可迁移结论时如实标注 + 阶段 5 快速核对** — 落点：`core/calibration/transfer.py` 的 `transfer-report` 路径 + 新增 `tests/unit/test_calibration_transfer.py` 与 `tests/contract/test_transfer_contracts.py` 用例 — **完成判据**: ① 无短剧线结论可迁移（**真实回流待运营**）⇒ 如实标注「**无可迁移结论（来源缺失）**」并给出**继续观察条件**（来源真实覆盖 ≥ `min_real_days` 且样本 ≥ `min_samples`），**不得**以模拟回流的结论充当来源；② **不得**把"跑通离线演示"当成本可迁移结论的证据；③ 快速核对：`uv run pytest tests/unit/test_calibration_transfer.py tests/contract/test_transfer_contracts.py -q` **转绿**；`uv run pytest tests/contract/test_calibration_contracts.py -q` **保持绿**（010 的产物零写入、历史节点逐字节一致）；④ 命令行核对：`uv run python ops/transfer.py transfer-report --data-dir <tmp>`（⇒ 0 且含「无可迁移结论（来源缺失）」或逐条判定）、`uv run python ops/transfer.py transfer --data-dir <tmp> --from configs/shortdrama.yaml --to configs/movie.yaml --evaluator <id@version> --period <周期> --dry-run`（⇒ 零落盘）、缺 `calibration.transfer` 键 ⇒ **2**。

**检查点**: ✅ 迁移件条件完备率 100%；不可迁移**逐条**记原因且误迁移恒 0；迁移件 append-only、改写 100% 被拒；**只迁结论不迁权重**（零权重键 + 零 `refit` import）；采纳后既有节点 `eval_breakdown` 与得分**逐字节不变**；无可迁移结论时如实标注「无可迁移结论（来源缺失）」。

---

## 阶段 6：端到端演示 / 配置登记 / 文档与门禁同步（T2066~T2077）

**目标**: 用一个**离线端到端演示**（七步、退出码 0、零真实花费、零外部网络、零凭证）把机制闭合；把新增参数在**五处登记点增量登记**（**不新造第六处**）；把两形态配置的**取值差异**（短剧态 `min_window_days: 14` / 电影态 7、投放渠道仅短剧态）落实；把**会变红的既有测试与夹具**逐项按扩展更新；文档与交付状态收口。

**⚠️ 关键**: 本阶段是"逃逸门禁"的集中审查面——**新增必需配置项未登记 ⇒ 无任何门禁会发现**（`specs/020-shortdrama-real-feedback/plan.md:242` 的配置登记清单）；**夹具静默失效会造成假绿**（比测试变红更危险，`specs/020-shortdrama-real-feedback/research.md:567`）。

### 阶段 6 的实现

- [x] T2066 **新增离线端到端演示 `ops/demo_shortdrama_feedback.py`（七步、退出码 0、零真实花费）** — 新增 `ops/demo_shortdrama_feedback.py` — **完成判据**: 七步全 ok ⇒ **退出码 0**（镜像 `ops/demo_billing.py` 风格）：① **两形态配置形状与缺项拒绝**（`budget.channels.<id>.tiers` 非空 + 非空 `adapter` + `calibration.transfer` 六键齐备；删任一项 ⇒ 装配/加载报错；旧扁平形状仍可读并显式归一）；② **渠道分派**（`llm` 与 `media` 的档位/账本/告警/运行记录互不可见，**同档位跨渠道串用 0 次**；多渠道 + 顶层扁平 `tiers` ⇒ 报错）；③ **凭证矩阵与装配期拒绝**（声明真实而凭证缺失 ⇒ 拒绝启动、点名变量、零落树零扣费、**不回落模拟**）；④ **最小规模先行**（Mock 平台 + 夹具账单跑最小规模档 = 投放环节档位 `limit_usd` → 校准记录 append-only → 未校准扩量被拒留痕）；⑤ **投放调用受同一门禁**（超限申请 ⇒ **调用前拒绝、平台调用 0 次、零入账**；015 按轮上限与 019 账本**两腿口径可分辨**）；⑥ **迁移件**（可迁移 / 不可迁移各一，不可迁移**逐条**记原因；采纳走人工两键；既有节点 `eval_breakdown` 与得分**逐字节一致**）；⑦ **诚实分层 —— 本步只做 demo 演练**（模拟来源不计入真实覆盖；`source` 逐条可辨；窗口断档**逐段**报出；结论写「**机制已就绪 / 真实回流待运营**」）——**分工（去重登记，D-02）**：**断言本体在 T2012⑩**、**"三处共用同一 `RUN_SOURCES` 取值域"的登记与静态断言在 T2071**，**本步只把两者在离线演练里跑一遍并打印真实覆盖天数与逐段缺口**（不重复写断言）；**零成本红线**：Mock 平台（`agents/promo/platform/simulated.py`）+ 夹具账单/指标 + 临时目录 + 确定性时钟，跑完仓库根**零残留、零真实花费产物、零外部网络**（无凭证亦可跑）；`--help` 退出 0；**不新造第二个端到端演示入口**。

- [x] T2067 **配置登记清单（五处登记点增量登记，缺一即"逃逸门禁"）** — 改 `tests/unit/test_form_switch.py:256`（顶层差异集）、`tests/unit/test_config_integrity.py:23`（`CONFIG_CLASSES`）与 `:46`（`REQUIRED_PATHS`）、`tests/contract/test_pilot_contracts.py:418`（段差异集）、`agents/pilot/pilot.py:377`（`config_completeness` 预检清单）、`tests/conftest.py:4109`（夹具） — **完成判据**: ① `tests/unit/test_form_switch.py:256` 的顶层差异集：`calibration` 与 `budget` **已在集合内**、新增键在**既有段内** ⇒ 该断言**本身不变**，但**本次新增的取值差异**必须登记（短剧态 `budget.runs.min_window_days: 14` vs 电影态 7；`budget.channels` 的投放渠道条目**仅短剧态**）；② `test_config_integrity.py`：`calibration` / `drift` / `budget` **已在表内、不新增类**；`:46` 的 `REQUIRED_PATHS` 增"缺项即红"条目（含 `calibration` 的 `window_semantics` / `window_semantics_change_date` / `transfer.*`、`promo.attribution_date_required_since`、以及 `budget.channels.<id>.tiers` 的**嵌套**路径——旧扁平键仍可读，但**缺嵌套 `tiers` 即报错**）；③ `tests/contract/test_pilot_contracts.py:418` 的段差异集：本次**不新增顶层段** ⇒ 若该断言遍历顶层段集合则不变（核对后登记结论）；④ `agents/pilot/pilot.py:377` 的 `config_completeness` 预检清单登记新增项（与 T2053 **同文件 ⇒ 串行**）；⑤ `tests/conftest.py:4109` 的夹具同步——**分工（去重登记，D-01①）：本项只做一致性核销**（读 T2056④ 的改造结果并确认登记口径与之一致），**夹具改造的实现处是 T2056④**，本项不重复实现；⑥ 权威面是 019 已列的**五处**（`specs/019-real-channel-billing/quickstart.md:99`），本特性**只做增量登记、不新造第六处**。

- [x] T2068 **短剧态运行窗口下限改 14 天（"≥2 周"来自立项书 G4 验收原文，不是发明数字）** — 改 `configs/shortdrama.yaml:601`（`min_window_days: 7` → `14`） — **完成判据**: ① 短剧态 `budget.runs.min_window_days: 14`、`gap_tolerance_days` **保持现值**（`:602` 现为 0）并**留在开放问题**（起算前后是否留余量待运营与制片侧给出，**不得**由本特性代劳放宽）；② **电影态 `configs/movie.yaml:598` 零改动**（保持 7；C 路径投放属短剧线，改电影态属越界）；③ 该数字**忠实编码** `docs/三期立项书.md:166` 的 G4 原文与 `:212` 的行（"短剧线真实数据回流 ≥2 周"）；④ 既有断言逐条核对为"不红"：`tests/unit/test_billing_runlog.py:61` 的 `min_window_days == 7` 用 movie 派生夹具（**保留**）、`tests/conftest.py:3512` 的内联夹具自声明 7（**保留**）；⑤ 两形态取值差异**可指认**（机检：短剧态按 14 判、未达标如实报缺口与差值）。

- [x] T2069 **"缺项即报错、不取码内默认"机检（两形态）** — 落点：`tests/unit/test_config_integrity.py:46` 的 `REQUIRED_PATHS` 条目 + 各配置类的 `from_dict` 必需读取 — **完成判据**: 逐项删除后**必须报错**：`calibration.window_semantics`、`calibration.window_semantics_change_date`、`calibration.transfer.{basis,source_forms,target_forms,conditions,storage,adoption}`、`promo.attribution_date_required_since`、`budget.channels.<id>.tiers`（嵌套）、`budget.channels.<id>.adapter`、各档 `limit_usd` / `window.kind` / `on_exhausted`；错误类型与文案逐条可指认（`CalibrationConfigError` / `BudgetConfigError`）；**两形态各自跑一遍**（电影态与短剧态都必须报错，不得有一侧静默取默认）。

- [x] T2070 **"两形态都能装配通过" + movie 不因投放渠道/凭证失败（机检）** — 落点：`tests/unit/test_billing_channels.py`（T2012⑨）+ 装配入口 `agents/pilot/backends.py:280` — **完成判据**: ① `configs/movie.yaml` 与 `configs/shortdrama.yaml` **各自装配成功**（默认全模拟链路）；② **movie 路径因投放渠道或其凭证而失败的次数恒 0**——movie **不登记**投放渠道，未启用真实投放时投放面（凭证矩阵 / 最小规模 / 账单）**不参与任何判定**；③ movie 上声明 `pilot.overrides.promo: http`（该形态未登记投放渠道）⇒ **装配期显式拒绝**并指出"该形态未登记投放渠道"（**不静默降级为模拟、不发明渠道**）；④ **回滚口径**：投放渠道条目只保留在短剧态配置里（撤回即回到电影态路径）。

- [x] T2071 **诚实分层机检：取值域单点登记 + 静态断言（**分工：断言本体在 T2012⑩，本任务做取值域单点的登记与静态断言**，D-02）** — 落点：`tests/unit/test_billing_channels.py`（T2012⑩）+ `agents/promo/daily.py` 的 `evidence_claim` + 报告/CLI 结论文案 — **完成判据**: ① **唯一来源取值域（本任务的核心增量）**：复用 `core/billing/runlog.py:28` 的 `RUN_SOURCES`，**运行记录（`core/billing/runlog.py:313` 的 `RecordingGateway` / 投放面的 `RecordingChannelCall`）、日级回流件（`promo_daily_metrics.source`）、窗口覆盖判定（`coverage_window` 的 `covered_dates`）三处共用同一取值域**（静态扫描断言：`agents/promo/`、`core/calibration/` 内重复声明 `("real","simulated","fallback")` 字面量 ⇒ 红）；② 与 T2012⑩ 的**分工**：断言本体（"模拟被标为真实"恒 0、结论文案取值域、`evidence_claim` 二元素）**写在 T2012⑩**，本任务不重复实现，只负责"取值域单点"的登记 + 静态断言 + 三处共用事实的核对；③ `evidence_claim` 取值域二元素：默认 `"mechanism_ready_real_feedback_pending"`、仅当 `meets is True` 且 `covered_days` **全部**来自 `real` 时才允许 `"real_feedback_met"`；④ **渠道失败不得静默回落模拟并照常计费**：真实渠道失败/拒绝 ⇒ 运行记录 `result=failed|refused`、告警落 `alerts.jsonl`、已发生花费如实入账；`fallback` 为**保留值、本特性无写入点**（将来若开回落必须显式声明 `fallback_reason`、标 `source=fallback`、**不计入真实覆盖**、不得照常计费）；⑤ **零凭证可跑**：demo 与全部单测/契约用例在无凭证环境**恒可跑**（全模拟链路），**不得**为通过而要求凭证；反之声明真实而无凭证**必须**拒绝启动。

- [x] T2072 **"会变红的既有测试与夹具"逐项按扩展更新（⑪ 硬要求，原则统一为"按扩展更新、不削弱"）** — 改 `tests/unit/test_anchor_snapshots.py:16`（`_PERIOD` 周级夹具，**保留既有断言**；快照/台账新增字段 ⇒ **扩展**）、`tests/unit/test_drift_detect.py:32`（`_CURRENT` 周级，**保留**；`thresholds_snapshot` 新增键 ⇒ **扩展**；另立日级 `window: 3` = 3 天新用例）、`tests/unit/test_form_switch.py:188`（**扩展**运转断言，T2015）、`tests/unit/test_blind_selection.py:18`、`tests/unit/test_close_round.py:46`、`tests/unit/test_anchor_intake.py:33`、`tests/unit/test_editing_replay.py:211`（四者补 `period_days=7` 参数，**周级判定结果不变**）、`tests/unit/test_drift_config.py:76`（阈值快照全等断言 ⇒ **扩展**，**不得删键**）、`tests/unit/test_platform_anchors.py:1`（夹具快照补归属日字段 + 补"缺该字段 ⇒ 显式失败"负例夹具；读路径**保留**）、`tests/unit/test_ingest_metrics.py:137`（末位可选 ⇒ 构造零改动，**保留**）、`tests/conftest.py:441` 与 `:462`（promo 回流夹具补归属日字段，docstring 口径同步）、`tests/contract/test_calibration_contracts.py:1`（**保留且不得改判据**）、`tests/contract/test_screenplay_calibration.py`（**保留**）、`tests/contract/test_drift_contracts.py`（**保留**，含"010 产物零写入"机检）、`tests/contract/test_web_parity.py:39`（周级**保留**，日级新增 `YYYY-MM-DD` 形状）、`ops/demo_*.py`（`ops/demo_billing.py`、`ops/demo_calibration.py`、`ops/demo_web.py:284`、`ops/demo_judge_drift.py:129`、`ops/demo_promo_loop.py` 按新口径微调，**语义与退出码 0 不变**）、`web/queries.py:831`/`:842` 与 `web/parity.py:45` 与 `core/calibration/drift_report.py:98` 的报告路径读取改经 `latest_report_path`（**周级单轮形态呈现不变**） — **完成判据**: ① 逐项核销 `specs/020-shortdrama-real-feedback/research.md:539` 起决策 10 表的 **19 项**，每项给出"保留 / 扩展"结论与**具体改动点**；② **零删断言、零放宽**（尤其 `tests/unit/test_drift_config.py:76` 的全等、`tests/unit/test_billing_core_purity.py` 的三条扫描）；③ `tests/adversarial/`、`tests/unbiasedness/` 与 `tests/contract/test_pilot_film_contracts.py`、`tests/unit/test_promo_loop.py`（按轮上限语义不动）**零改动**（如实登记"明确不改"清单）；④ 快速核对：`uv run pytest tests/unit/test_anchor_snapshots.py tests/unit/test_drift_detect.py tests/unit/test_drift_config.py tests/unit/test_platform_anchors.py tests/unit/test_ingest_metrics.py tests/unit/test_close_round.py tests/unit/test_blind_selection.py tests/unit/test_anchor_intake.py tests/unit/test_editing_replay.py -q` 全绿。

- [x] T2073 [P] **文档收口：《README》新增本特性章节并交叉引用既有章节** — 改 `README.md`（新增章节 + 交叉引用 `:418` 的 019 章节、`:927` 的 015 短剧章节） — **完成判据**: 新增"短剧形态的真实投放与日级回流（功能 020）"章节，内容含：日级量纲与半开窗口口径、日级回流与 `promo_daily_metrics`、归属日三时间并列、渠道命名空间与 `ops/billing.py channels`、迁移面 `ops/transfer.py` 四子命令、离线演示命令、**诚实边界**（`min_window_days: 14` 的真实窗口**待运营**、平台名/凭证/预算档**未标定**、合规审查属业务侧）；措辞与 `specs/020-shortdrama-real-feedback/quickstart.md:130` 的"机制已就绪 / 真实回流待运营"口径**逐字一致**。

- [x] T2074 [P] **文档收口：《三期立项书》G4 行标注交付状态** — 改 `docs/三期立项书.md:166`（G4 行） — **完成判据**: G4 行按 019/G2/G3 的同一体裁标注交付状态；**验收列**逐条如实登记——① "短剧线真实数据回流 ≥2 周"标注 **机制已就绪 / 真实回流待运营**（运营侧墙钟 + 凭证前提，`docs/三期立项书.md:280`）；② "评估器校准结论按迁移思路回灌电影线"标注**口径与可比性判定可机检落盘、不可迁移 100% 如实登记原因**；并登记本特性的**交付面**（机制 + 离线复现）与**未交付面**（真实凭证、真实预算、合规审查判据，`docs/三期立项书.md:271`）；`:212` 的周 9~12 里程碑行同批复核。

- [x] T2075 [P] **文档收口：`docs/pilot-upgrade-manifest.json` 的 C 路径状态** — 改 `docs/pilot-upgrade-manifest.json:136`（C 路径 `status: not_delivered`）与其 `notes` — **完成判据**: ① `credential_envs` **不改名、不新增变量**（反向机检 `tests/unit/test_credential_env_lock.py` 常驻）；② `status` 按本特性的**实际交付面**更新（机制与离线复现已交付、真实投放与真实回流**待运营**）并如实写清"尚未真实跑过"；③ `notes` 追加归属日字段（`metric_date`）与渠道命名空间两处协议面影响；④ 若决定保持 `not_delivered`，**必须**在 `notes` 里写清"机制已交付、真实凭证/账户/预算未到位"以免读者误读（**不得**为了好看改成 `delivered`）。

- [x] T2076 **`quickstart.md` 验证记录逐条回填（不预填未跑结论）** — 改 `specs/020-shortdrama-real-feedback/quickstart.md`（`:130` 的验证记录区） — **完成判据**: 按 A→B 顺序复跑并把**退出码、用例数、demo 用时**逐条回填（镜像 019 的回填格式）：① A 组（019/010 既有面基线）保持已实跑结论；② B 组（B1 单测子集、B2 契约子集、B3 demo 七步退出码 0、B4 两形态 `channels` 对照、B5 迁移面四命令）逐条回填；③ **待运营项如实留白并按 C18 口径标注**（真实平台凭证、真实预算档数字、合规审查完成判据、">=2 周"起算日）；④ **不得**预填未跑结论。

- [x] T2077 **静态纯度与登记点终检** — 落点：`tests/unit/test_billing_core_purity.py:110`、`:151`、`:157`、`tests/unit/test_form_switch.py:309`、`tests/unit/test_credential_env_lock.py`、`tests/unit/test_no_vendor_literals.py` — **完成判据**: ① `core/billing/` 零渠道 id / 零形态字面量 / 零形态分支**常驻通过**；② `core/` 与 `agents/` **零形态分支**（零 `form ==`、无 cadence 的 `if` 分支树——`1`/`7` 的判定即量纲本身，其他值一律拒绝）；②′ **渠道解析不得出现形态字面量（C-02 守卫面落差）**：本特性在 `agents/pilot/backends.py` 新增渠道解析（`channel_for_adapter(cfg, "pilot_llm")` 等），而 `tests/unit/test_form_switch.py:309` 的字面量扫描面**显式排除 `agents/pilot`**（`:310-312` 的 `if "pilot" not in path.parts`；形态**判断**的扫描面 `:321` 才覆盖全部）⇒ **必须新增断言补齐**：`agents/pilot/backends.py` 的渠道解析路径**不得**出现形态字面量与形态判断（新增扫描面落在 `tests/unit/test_form_switch.py` 与 `tests/unit/test_billing_channels.py`，**不得**删改既有 `:309` 的扫描口径）；③ `.chat(` 调用点计数**仍为 8**（未新增；`ops/smoke_llm.py` 那一处**单列**）；④ 凭证变量名反向机检通过（`tests/unit/test_credential_env_lock.py`）；⑤ "**不新增旁路门禁**"静态断言常驻：`RecordingChannelCall` 是投放面唯一包装点、`agents/promo/loop.py` 内零门禁调用；⑥ 快速核对子集全绿并登记结果。

**检查点**: ✅ 离线端到端演示**退出码 0 且七步全 ok**、零真实花费、零外部网络、零凭证可跑；五处登记点**全部落地且全绿**（含"缺项即报"的嵌套路径）；短剧态 14 天 / 电影态 7 天两形态取值可指认且两形态都能装配通过；变红清单 19 项逐条"按扩展更新、不削弱"核销；文档与交付状态口径与 quickstart 一致。

---

## 验收与复核

**⚠️ 执行者硬约束**: 本节 T2078~T2083 **六类慢门禁全部由父代理在宿主机执行**（覆盖率、契约两条腿、集成、对抗、无偏性、ruff 双绿）——**子代理不得跑**（会超时）。T2084/T2085 为可本地执行的结果核对与口径复核。

- [ ] T2078 **【由父代理在宿主机执行】** 覆盖率门禁（口径不降，含 web） — 命令原文：`uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85` — **完成判据**: **0 失败**且 `TOTAL` 覆盖率 **≥ 85%**（含 `web`）；覆盖率口径**不因本特性降低**；跑完仓库根**无运行期产物残留**（沿用 019 的"单测不写仓库"口径）。

- [ ] T2079 **【由父代理在宿主机执行】** 契约两条腿（含 stub 口径） — 命令原文：`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q` — **完成判据**: 两条腿**均全绿**（skip 仅允许"真实实现无凭证按用例跳过"的既有口径）；`tests/contract/test_billing_contracts.py` 的**用例数不减少**（基线 41）、C13 的 12 处 `--channel` 断言逐条在位；`tests/contract/test_calibration_contracts.py` 的"历史节点逐字节一致"与"昂贵动作调用计数为 0"两条机检**继续通过**；`tests/contract/test_transfer_contracts.py` 新增用例全绿。

- [ ] T2080 **【由父代理在宿主机执行】** 集成门禁（本特性的 `0011_daily_feedback` 双方言触发器验证） — 命令原文：`uv run pytest tests/integration -m integration` — **完成判据**: 全绿；**本特性新增验证点**：`0011_daily_feedback` 在 SQLite 与真实 PG 上均能升级/降级（`upgrade` 后锚点表多出可空 `metric_date` 列、`promo_daily_metrics` 表与 INSERT-only 触发器就位；`downgrade` 只 `DROP COLUMN` / `DROP TABLE`、**不触碰任何既有行**）；**历史行 `metric_date` 恒 `NULL` 且永不回填**（SC-008 的存储层面）；锚点 `UPDATE`/`DELETE` 仍 100% 抛错且报错消息含 `anchor data is immutable`。

- [ ] T2081 **【由父代理在宿主机执行】** 对抗测试门禁（**合并阻塞**，**不放松**） — 命令原文：`uv run pytest tests/adversarial -m adversarial` — **完成判据**: ① 全绿；② **本特性的对抗面纳入既有阻塞门禁（C-01）**：新增 **`tests/adversarial/test_shortdrama_feedback_adversarial.py`（新增，薄封装）** **并打 `@pytest.mark.adversarial`**，把本特性的对抗场景**作为既有合并阻塞门禁的一部分**跑起来——伪造 `promo_daily_metrics` 行（自造 `period`/`source`）、改写已落盘日级记录与快照、伪造 `covered_days`（自造归属日）、**门禁绕过**（不传 guard 的投放装配、伪造 `--tier`/`stage`）、迁移件系统字段改写与省略、把模拟件标为真实、媒体渠道账单篡改后再对账；③ **薄封装只调用 unit/contract 侧的细粒度断言**（`tests/unit/test_promo_daily_ingest.py` / `tests/unit/test_billing_channels.py` / `tests/unit/test_promo_delivery_gate.py` / `tests/unit/test_calibration_transfer.py` 的既有用例体或共享夹具），**不复制第二份断言逻辑**——unit/contract 侧保留为**实现级细粒度断言**，`tests/adversarial/` 侧是**阻塞门禁的纳入口**；④ 执行方式：**由父代理在宿主机执行**（子代理不得跑）；⑤ 新任务见 **T2086**（在阶段 1 与其它测试同批落地、先写并确认失败）。

- [ ] T2082 **【由父代理在宿主机执行】** 无偏性门禁（发布阻塞，**不放松**） — 命令原文：`uv run pytest tests/unbiasedness -m unbiasedness` — **完成判据**: 全绿；本特性**零改动**该套件（如实登记"明确不改"）。

- [ ] T2083 **【由父代理在宿主机执行】** 静态检查双绿（**本仓 CI 逐字一致**） — 命令原文：`uv run ruff check . && uv run ruff format --check .` — **完成判据**: 两条命令均退出 0（`ruff check` 零告警、`ruff format --check` 零待格式化文件）。

- [ ] T2084 **离线端到端演示与产物核对（可本地执行）** — 命令：`uv run python ops/demo_shortdrama_feedback.py` — **完成判据**: **退出码 0**（七步全 ok）；仓库根**零真实花费产物**、**零外部网络**（无凭证亦可跑）；逐件核对 `specs/020-shortdrama-real-feedback/quickstart.md:114` 起的"如何看产物"表：`billing/{channel}/ledger.json`（两渠道各一本、`revision` 单调）、`billing/{channel}/alerts.jsonl`（`kind` 六值取值域）、`billing/{channel}/runs/{date}.json`（链式 `head_digest`、`sealed` 后拒绝追加）、`billing/{channel}/bills/{bill_id}.json` 与 `reports/{period}.json`（六类差异逐项分类 + 报告引账单批次）、`calibration/snapshots/{agent}/{evaluator}/{period}.json`（周期标签由 cadence 派生）、`calibration/reports/{period}-{run_id}.json`（同周期多轮并留存）、`calibration/drift/metrics/{agent}/{evaluator}/{period}.json`（含所读快照指纹与锚点数）、`calibration/transfers/{transfer_id}.json`（**不得出现权重键**）。

- [ ] T2085 **SC 映射、统计与一致性复核（本清单的自检出口）** — 无新文件 — **完成判据**: ① 逐条核对 `specs/020-shortdrama-real-feedback/spec.md:148` 起的 SC-001~SC-009 均有承载任务（见下表"SC 映射"）；② 核对**全部 14 条 FR**（`specs/020-shortdrama-real-feedback/spec.md:116`~`:129`）在任务里有落点；③ 统计"任务总数 / 各阶段条数"与实际条目一致；④ 核对每条任务的**文件路径真实存在**（新增件路径写全且可被创建）且**无 `path:line` 引用越界**；⑤ 登记"未覆盖项"（见文末）。

### SC 映射

| 成功标准 | 承载任务 |
| --- | --- |
| SC-001 里程碑验收（日级机制机检 + 迁移口径与可比性判定；"机制已就绪 / 真实回流待运营"） | T2015 / T2016 / T2066 / T2071 / T2078~T2085 |
| SC-002 量纲一致率 100% / 同周期多轮并留存 / 静默改写恒 0 / 半开窗口机检 / 运转断言 / （活动,周期）唯一性 | T2008 / T2010 / T2011 / T2015 / T2039 / T2040 / T2041 |
| SC-003 断档如实报 / 按归属日聚合 / 三时间可见 / 平台未给归属日即失败 / 模拟不冒充真实 | T2009 / T2010 / T2033 / T2035 / T2037 / T2038 |
| SC-004 超预算投放 0 次 / 凭证缺失启动拒绝 / 多渠道分派 / 旧扁平形状可读 / 019 断言不削弱 | T2012 / T2013 / T2045 / T2046 / T2047 / T2049 / T2050 / T2054 / T2055 / T2056 / T2057 |
| SC-005 对账差异可解释率 100% / 未解释项告警 / 报告必引账单批次 | T2052 / T2084（复用 019 的 `core/billing/reconcile.py:233` 与 `core/billing/bill.py:321`，**不新造**） |
| SC-006 无合格校准记录的扩量 100% 拒绝并留痕 | T2051 / T2054 / T2066④ |
| SC-007 迁移判定完备率 100% / 误迁移恒 0 / append-only / 不改既有节点 | T2014 / T2059 / T2060 / T2061 / T2062 / T2063 / T2064 |
| SC-008 `human` 锚点冻结（改写 100% 被拒、同键幂等拒绝、多日只追加） | T2010 / T2028 / T2029 / T2037 / T2080 |
| SC-009 覆盖率 ≥85% + 四道常驻门禁不放松 + 零形态分支与"不新增旁路门禁" | T2077 / T2078 / T2080 / T2081（纳入口 = T2086）/ T2082 / T2083 |

### 任务总数 / 各阶段条数

| 阶段 | 任务号区间 | 条数 |
| --- | --- | --- |
| 阶段 0：前置与勘查核对 | T2001~T2007 | 7 |
| 阶段 1：契约与 TDD 骨架 | T2008~T2018 **+ T2086（跨阶段补号）** | 12 |
| 阶段 2：US1 第一半（周期量纲与半开窗口） | T2019~T2027 | 9 |
| 阶段 3：US1 第二半（日级分片/归属日/漂移量纲/快照物化） | T2028~T2044 | 17 |
| 阶段 4：US2（C 路径投放接入 + 019 渠道命名空间） | T2045~T2058 | 14 |
| 阶段 5：US3（校准结论迁移） | T2059~T2065 | 7 |
| 阶段 6：端到端演示 / 配置登记 / 文档与门禁同步 | T2066~T2077 | 12 |
| 验收与复核（含 6 条**由父代理执行**的慢门禁） | T2078~T2085 | 8 |
| **合计** | **T2001~T2086**（含跨阶段补号 T2086） | **86** |

### 未覆盖项（如实说明）

1. **真实回流 ≥2 周不可在本特性内达成**（运营侧墙钟 + 凭证前提）：本清单交付的是**机制与离线复现**（T2066 七步步骤全 ok + T2078~T2083 门禁），真实窗口**待运营积累**；产物一律写「机制已就绪 / 真实回流待运营」，**禁止**以模拟回流冒充（分工：断言本体 = T2012⑩、取值域单点与静态断言 = T2071、demo 演练 = T2066⑦）。
2. **平台名、凭证取值、预算档数字、投放环节档位取值**：属运营侧输入（`spec.md` 开放问题 1/2）——本清单只交付"缺项即报错 + 最小规模档 + `note` 标未标定"的形状（T2052 / T2069），**不发明数字**。
3. **平台侧是否提供"指标归属日"字段**：未确认（开放问题 1）。本清单的机制后果是"未标定 ⇒ 回流条数为 0 且原因明确"（T2033 的显式失败），**不得**以拉取/采集时刻兜底；字段名钉死 `metric_date`（T2031）。
4. **合规审查的完成判据**（业务判定，`docs/三期立项书.md:271` 列为高风险）：本清单**不发明**凭据形式；未定前**不得**扩量，代码与配置里**不得**出现"视为通过"的分支（T2062④ / T2074）。
5. **`gap_tolerance_days` 取值**：保持现值并留在开放问题（T2068），**未**代运营侧放宽。
6. **`tests/integration` / 对抗 / 无偏性 / 覆盖率 / ruff 的**实际执行结果：本清单**不预填**结论（T2078~T2083 由父代理执行后回填；quickstart 的验证记录区同理，T2076）。
7. **不属本特性范围（防回潮）**：G5 多形态插件验证、B 路径真实生成厂商对接、多租户/公网服务化、自动权重迁移、Decimal 金额重构、多主机共享额度、适配器协议重写、`CostRecord` 与发现树改动、web 侧写入口（T2077 的静态断言与本表共同守住）。

---

## 依赖关系与执行顺序

### 阶段依赖（含跨阶段前置，如实登记）

- **阶段 0**：无依赖、可立即开始；**只读**，产出事实基线。
- **阶段 1**：依赖阶段 0 的事实基线（尤其行号与 C13 结论）；**零实现代码**，全部用例**预期红**。
- **阶段 2**：依赖阶段 1 的 T2008/T2015。**跨阶段前置（硬）**: `period_label(day, 7) == iso_week_label(day)` 的逐字节断言（T2008①）**必须先用**，否则 T2020 的接线等于改写 010 的冻结证据。
- **阶段 3**：依赖阶段 2（`periods.py` 是标签/窗口/覆盖口径的唯一来源）；T2028 的 `0011_daily_feedback` 是 T2030/T2035/T2036/T2037/T2038 的前置；与阶段 4 **可并行**（文件不重叠）。
- **阶段 4**：依赖阶段 1 的 T2012/T2013；`core/billing/budget.py` 内部**串行**（T2045 → T2046 → T2047）；T2050/T2051 依赖 T2046 与 T2049；与阶段 2/3/5 **可并行**。
- **阶段 5**：依赖阶段 2（`period_label` 派生 `period`）与阶段 3（来源件：台账/快照/报告/漂移产物存在且自描述）；与阶段 4 **可并行**。**跨阶段前置（如实登记）**: 若阶段 3 尚未落地，"无可迁移结论（来源缺失）"分支可先测（T2065① 允许），但"可迁移"分支的夹具必须等阶段 3 的产物口径稳定。
- **阶段 6**：依赖阶段 2~5 全部完成；T2067 与 T2053 共用 `agents/pilot/pilot.py` ⇒ **串行**。
- **验收与复核**：依赖阶段 6；T2078~T2083 **由父代理在宿主机执行**。

### 并行机会

- 阶段 1：T2008~T2014（七个新测试文件 + 一个契约文件）**互不重叠**，可并行；T2015/T2016/T2017 扩展三个不同既有文件，亦可并行。
- 阶段 2：T2024 / T2025 / T2026 文件互不重叠，可并行；T2019 → T2020/T2021/T2022/T2023 串行（同一口径链）。
- 阶段 3：T2031/T2032/T2033/T2034（三个适配器 + 一个评估器）可并行；T2039/T2041/T2042（`ledger` / `drift_metrics` / `drift_config`）可并行；T2035 是 T2036/T2037/T2038 的前置。
- 阶段 4：T2049 / T2052 / T2053 / T2054 可并行；T2055/T2056/T2057 可并行（不同文件）。
- 阶段 5：T2062 与 T2059/T2060/T2061 可并行（不同文件）；T2063/T2064 依赖 T2059。
- 阶段 6：T2073 / T2074 / T2075 / T2076 四份文档互不重叠，可并行；T2066 依赖阶段 4/5 的实现；T2067 依赖 T2053。

### MVP 优先（US1）

1. 阶段 0 → 阶段 1 → 阶段 2 → 阶段 3（**不可跳过**：`periods.py` 与 `0011_daily_feedback` 是所有日级证据的承载面）。
2. 独立验证：`uv run pytest tests/unit/test_period_cadence.py tests/unit/test_metric_attribution_date.py tests/unit/test_promo_daily_ingest.py tests/unit/test_drift_cadence.py tests/unit/test_form_switch.py -q` + `uv run pytest tests/contract/test_calibration_contracts.py -q`。
3. 此时即交付"**真的按日运转**"的价值：日级标签、半开窗口、归属日、日级分片、覆盖 ∧ 连续双条件与断段如实报。

### 增量交付

1. US1（阶段 2+3）→ 日级量纲与回流机制（MVP，SC-001① / SC-002 / SC-003）
2. US2（阶段 4）→ C 路径投放接入与渠道命名空间（SC-004 / SC-005 / SC-006）
3. US3（阶段 5）→ 结论迁移与可比性判定（SC-007）
4. 阶段 6 + 验收（SC-001 / SC-008 / SC-009）

## 备注

- **原则一落点**: T2028（只加可空列、历史行 NULL 不回改）、T2029（锚点写入仍走唯一 `insert_anchor`）、T2037（新写入非空、回退只对历史行并计数）、T2039（快照周期物化 + 内容变化必记账、静默改写恒 0）、T2041（窗口单位进口径哈希 ⇒ 口径变更即新 `detector_version`、历史判定不回溯）、T2064（迁移不改既有节点得分与 `eval_breakdown`）。
- **原则二落点**: T2028（`promo_campaigns` 表结构零 DDL、既有唯一键不改）、T2036（日级分片是**追加**新节点，不是改写旧节点）、T2060/T2063/T2064（迁移件 append-only、零写树写库入口）。
- **原则三落点**: ① 门禁 = T2013 / T2045~T2047 / T2049 / T2050；② 账单对账 = T2052（媒体渠道 `bill` 声明面）+ T2054（最小规模入口的渠道归属）；③ 最小规模 = T2051 / T2066 步④；**禁止旁路门禁**由 T2013⑤ 与 T2077⑤ 常驻。
- **原则五落点**: T2019（`periods.py` 业务无关、cadence 作参数、零形态分支）、T2059（`transfer.py` 业务无关、零 `refit` import）、T2026 / T2052 / T2062 / T2068（新增参数全部配置化、两形态 schema 齐备）、T2067 / T2069 / T2070（登记点与"缺项即报"、两形态都能装配）。
- **原则六落点**: T2009（归属日来源可指认、缺失即失败给原因）、T2024（窗口口径进产物）、T2037（归属日缺失锚点数如实登记）、T2041（所读快照指纹可追溯）、T2040（跨口径变更日必须标注）、T2065（无可迁移结论如实标注）、T2071（诚实分层三重机检）。
- **本清单的"判断调用"（如实登记，不掩盖）**: ① `core/calibration/selection.py:108-113` 的归属日过滤**从 plan 的阶段 2 第 4 步移到阶段 3**（T2038）——理由是它依赖 `0011_daily_feedback` 的锚点归属日列，先改会"过滤键无处取"（plan 自身也标注"见阶段 3 的锚点归属日列"）；② `core/calibration/periods.py` 的 `cadence_of` / `coverage_window` 两个纯函数来自 `data-model.md` 与契约（`specs/020-shortdrama-real-feedback/contracts/period-cadence.md:169` 明确"同模块追加，不新增模块"），故并入 T2019 而非另开模块；③ **T2086 的任务号接在末尾而文档位置在阶段 1**（镜像 019 的 T1950/T1951 先例）：**编号与文档位置的这一处不一致是刻意的**——避免重编 86 条任务号与全部交叉引用；**阶段分布统计按文档归位计数**（T2086 计入阶段 1 ⇒ 阶段 1 = 12 条、合计 86 条）。
- **慢门禁纪律**: T2078~T2083 的命令**逐字**取自本仓既有 CI 口径（`specs/019-real-channel-billing/tasks.md:167` 的 T1948 同源）；**由父代理在宿主机执行**，子代理只跑各阶段的"快速核对"单文件子集。
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过。

---

## 批次 2 待裁决（阶段 3，T2028~T2044；子代理如实登记，不自行改设计件）

1. **T2040② 与「变红清单 #8 保留」互斥（需裁决）**：`tests/contract/test_drift_contracts.py::TestC7双信号与附注::test_强化与常规两路径` 对**同一周期**两次写**兼容别名** `reports/{period}.json` 且内容不同（靠旧覆盖语义刷新面板），而 T2040② 要求「任何已存在路径的改写调用必须抛错」、`data-model.md` 的不变量 **I-4** 要求「任一已存在路径的改写次数恒 0」。两条**不可能同时成立**。本批处置：按 T2040②/I-4 保留**严格零覆盖**（同内容幂等、异内容抛 `CalibrationReportConflictError`），并把该用例的两次落盘改为带轮标识（`run_id="round-1"` / `"round-2"`）——**断言零删除、零放宽**，只是让"同周期第二轮"落轮级报告（这正是本特性的语义）。若裁决为"别名可改写"，则回退为"别名存在即改写、仅轮级路径拒改写"，并撤销该用例的 `run_id` 改动。
2. **C7 草图的 `collect_platform_anchors -> tuple[list[AnchorScore], dict]` 未采用**：改签名会打断 `tests/unit/test_platform_anchors.py`（变红清单 #9 判为"读路径保留"）的既有调用形式；T2037 的完成判据也未要求换签名。回退计数与"报告可见"由 `attribution_fallback_count` + 覆盖视图 `attribution_missing_anchors` 承载（两者同值，有机检）。若裁决要求按 C7 换签名，需同批改该测试文件的调用形式（断言不变）。
3. **`PromoConfig` 跨段读取 `calibration.period_days`**：T2034 只要求 `promo.attribution_date_required_since` 两形态齐备；但 `ingest_daily` 需要 cadence 才能派生周期标签与节点 id，而列出签名只有 `config` 一个配置入参。故 `PromoConfig` 增加 `period_days`（跨段读取，与既有 `evaluator_weights.promo` 同一做法），代价是**旧式最小 dict 构造**（只有 `promo` + `evaluator_weights`）现在会因缺 `calibration.period_days` 报错。若裁决要求不动 `PromoConfig`，则需给 `ingest_daily` 增列 cadence 参数（偏离列出的签名）。

---

## 批次 3 登记（阶段 4，T2045~T2058；子代理如实登记，不自行改设计件）

**交付面**: `core/billing/budget.py`（渠道命名空间 + `declared_channels` / `channel_for_adapter` /
`tiers_of` / `tier_of` / `tiers_shape`、`assemble_guard(channel_id=)`、单渠道兼容视图）、
`core/billing/runlog.py`（`RecordingChannelCall`）、`core/billing/calibration.py`（按渠道取档 +
定点改写路径随形状）、`ops/billing.py`（`_channel_of` 按声明集、`channels` 只读子命令、八条齐备）、
`ops/check_credentials.py`（`channel_matrix`）、`agents/pilot/backends.py`（两个装配点接线 + 投放面包装）、
`ops/smoke_llm.py`、`agents/pilot/pilot.py`、`agents/promo/loop.py`（调用点零成本分支）、
两份形态配置（`configs/movie.yaml` 只登记 `llm`；`configs/shortdrama.yaml` 登记 `llm` + `media`）、
新增 `tests/unit/test_billing_channels.py` 与 `tests/unit/test_promo_delivery_gate.py`。

**本批的判断调用（如实登记，不掩盖）**:

1. **`sole_channel()` 按"退役"字面落：函数被移除**（不留兼容壳），取证面迁到
   `declared_channels` / `channel_for_adapter`；`assemble_guard(config_path)` 的**单渠道缺省仍可用**
   （声明数 == 1 时取该渠道；≥2 ⇒ 报错不猜）。
2. **投放面的 `stage`（投放环节 id）派生规则 = 该渠道"恰好一个"档位键**：设计件只写"`stage` = 该渠道的
   投放环节 id"，未定"如何取得"；为守住"code 内零环节字面量"，实现按"键即环节 id"派生，多于一个 ⇒
   **装配期拒绝**（不猜用哪一档，fail-closed）。若裁决要求"多档位投放渠道"，则须新增显式配置键（属"新增配置项"例外）。
3. **`RecordingChannelCall` 的平台失败分支以 `settle(0.0)` 释放预留**：019 的 `Reservation` 无 release
   入口；平台失败的这一笔**未发生花费**，以 0 结算可释放预留且不留"崩溃残留"标记（否则账本出现永不结清的
   pending）。语义已写入该包装的 docstring。
4. **`agents/promo/loop.py` 的 `create_campaign` 调用点新增 `except BudgetRefusedError` 零成本分支**
   （C10"调用点分支规则"的投放版）：被 019 拒的那一条落 `rejected` 节点（节点成本只含**已发生**的生成费用）、
   整轮不中断；**未新增任何门禁调用**（唯一包装点仍是 `RecordingChannelCall`，静态断言常驻）。
5. **凭证矩阵单一实现落在 `ops/check_credentials.py::channel_matrix`**（`ops/billing.py channels`
   复用同一实现），避免"两份矩阵"；投放渠道的环境变量名由 `ADAPTER_CREDENTIAL_PREFIX`
   从前缀拼出（不从别处手抄字面量）。
6. **`tests/unit/test_config_integrity.py` 的 `REQUIRED_PATHS` 预算条目改为嵌套路径**
   （`("budget", "channels", "llm", "tiers")` 与 `… "adapter"`）：原扁平条目在配置迁移后**必然失效**
   （删一个不存在的键不再触发拒绝）⇒ 不提前修则本批立即红。属 T2067② 增量登记的**提前落地**（断言加强、非放宽）。
7. **两处"配置与 cfg 形状必须同源"的用例调整**（断言零删除）：
   ① `tests/unit/test_billing_calibration.py::Test扩量落地::test_合格时定点改写额度…` 与
   `tests/contract/test_billing_contracts.py::TestC12::test_扩量定点改写且留痕` 的配置来源改为
   **夹具旧扁平段**（原组合是"真实 movie.yaml（新形状）+ 夹具 cfg（旧形状）"，命名空间落地后定点改写路径
   无从唯一——`cfg.tiers_shape` 与文件形状必须一致）；
   ② **新形状（`channels.<id>.tiers`）的定点改写**由 `tests/unit/test_billing_cli.py::TestRaiseTier`
   在**真实 movie.yaml（含注释）** 上覆盖（该用例的 payload 读取改 nested 路径）。
8. **`tests/unit/test_billing_ledger_concurrency.py::_budget_section` 以旧扁平形态落给子进程**
   （子进程走 C11 的旧形状归一，顺带覆盖兼容读路径），其档位键集改按渠道取。
9. **`tests/unit/test_billing_core_purity.py`（T2017）**：`_declared_tier_ids` 改为各渠道键**并集**；
   `:157` 的"8"改为"**LLM 渠道 = 8 处 `.chat(` 调用点** + 投放渠道的投放环节 ≥1，且扫描面在并集上"
   （断言本体保留、扫描面反而变宽）；`tests/unit/test_billing_config.py` 新增
   `_llm_channel` / `_tiers` 助手（按**装配引用**定位渠道，用例内不写死渠道 id）。
10. **发现一处"逃逸门禁"（如实登记，未擅自改动 `.gitignore` / `.github`）**：`.gitignore:27` 的
    `billing/` 匹配到 `core/billing/`，使 `uv run ruff check .` 与 `uv run ruff format --check .`
    的树扫描**跳过整个 `core/billing/`**（证据：`git check-ignore --no-index -v core/billing/runlog.py`
    ⇒ `.gitignore:27:billing/`；`git ls-files core/billing` 仍列出全部文件 ⇒ 未被 git 丢弃）。
    逐文件复核（`uv run ruff check core/billing/*.py` / `ruff format --check core/billing/*.py`）暴露
    **两处既有**问题（`git diff` 为空 ⇒ 非本批引入）：`core/billing/runlog.py:362` 的 B009
    （`getattr(self._gateway, "chat")` 是**刻意**写法——换成属性访问会新增第 9 处 `.chat(` 调用点，
    破坏 019 的调用点计数断言）、`core/billing/reconcile.py:148` 的 format 漂移。
    **需裁决**：是否把 `core/billing/` 从该 `.gitignore` 规则的影响面中排除（如加 `!core/billing/`），
    以及是否收编上述两处既有问题。本批**未**绕过、**未**修改门禁文件。

    **父代理裁决与收口（批次 3 后）**：**采纳并已修**——根因是裸 `billing/` 会匹配任意层级的同名目录；
    改为**根锚定** `/billing/`（`.gitignore:29`，附注理由），运行期产物根仍被忽略（`git check-ignore
    --no-index -v billing/llm` ⇒ `/billing/`）、`core/billing/` 重新纳入扫描（ruff 文件数 573 → **579**，
    恰为 6 个源码模块）。两处既有问题一并收编：`core/billing/runlog.py:362` 加 `# noqa: B009 - 见 docstring：
    刻意的转发取法`（保住"8 处 `.chat(` 调用点"的静态计数纪律），`core/billing/reconcile.py:148` 做纯格式化
    （1 insertion / 3 deletions）。收口后 `uv run ruff check .` 与 `uv run ruff format --check .` **真正覆盖
    `core/billing/`** 且双绿（579 files）——**此前各批"ruff 双绿"的覆盖口径不含 `core/billing/`，如实更正**。
11. **`tests/unit/test_pilot_backend_selection.py::Test声明驱动装配::test_声明http凭证齐即装配真实实现类`
    按扩展更新**（投放面包装改变了 `backends.promo` 的**外层**类名）：断言"装配到真实实现类"的**强度不变**，
    只是拆成两条——外层 `RecordingChannelCall`（020 的门禁包装）+ 包内 `HttpRealPlatform`
    （`RecordingChannelCall.adapter` 为公开只读属性）。该文件不在 T2056 的显式清单内，但属本批**触碰面**
    （`agents/pilot/backends.py` 的投放装配签名变更），故一并登记。
12. **本批未做（父代理明示的范围）**: T2086 对抗薄封装（T2013⑤ 与 T2052⑥ 的断言本体已落在 unit 侧）、
    阶段 5/6（`transfer.py` / `ops/transfer.py` / `ops/demo_shortdrama_feedback.py` / 文档收口 /
    `quickstart.md` 回填）。

**本批自检实测（子代理单文件子集）**: `tests/contract/test_billing_contracts.py` **41 passed**（基线不减）；
`tests/unit/test_billing_channels.py tests/unit/test_promo_delivery_gate.py` **33 passed**；
`tests/unit/test_billing_config.py … test_billing_{paths,core_purity,snapshot_freeze,runlog,reconcile}.py`
**105 passed**；C13 逐条实测（`tiers --channel llm` 多渠道 ⇒ 0、`--channel nope`/`ghost` ⇒ 2 且文案含
「不一致」、`calibrate --tier ghost` ⇒ 2 且含 `ghost`、`runs` ⇒ 1、`raise-tier --calibration ghost` ⇒ 1、
`reconcile --bill-id ghost` ⇒ 1 含「拒绝产出」）；`git diff --exit-code .github/workflows/billing_alerts.yml`
⇒ **0**；`alert-check --channel llm --config configs/movie.yaml` ⇒ **0**；两形态 `channels` ⇒ 0
（短剧含 `llm`+`media` 与凭证矩阵、电影无投放行）、`tiers --channel media` ⇒ 0；
媒体渠道 `import-bill`⇒0 / 同批次重复⇒拒绝 / `reconcile`⇒1（六类齐备 + `unexplained` + 报告引 `bill_id`）；
超限投放 ⇒ `BudgetRefusedError(over_limit)`、平台调用 0 次、账本 `spent_usd=0`；
`ops/demo_billing.py` 六步全 ok 退出 0、`ops/demo_pilot.py` 退出 0；`uv run ruff check .` ⇒ 0、
`uv run ruff format --check .` ⇒ 573 files already formatted（**但见上第 10 条的树扫描逃逸**）。

---

## 批次 4 登记（阶段 5 + 阶段 6 + T2086，T2059~T2077 与 T2086；子代理如实登记，不自行改设计件）

**交付面（新增/改动文件）**：

- 新增 `core/calibration/transfer.py`（迁移件生成 + 可比性判定 + append-only 读回；业务无关、
  零 `refit` import、零写树/写库入口）、`ops/transfer.py`（四子命令薄转发）、
  `ops/demo_shortdrama_feedback.py`（离线七步演示）；
- 改 `core/calibration/config.py`（新增 `TransferConfig` + `TRANSFER_CONDITION_IDS`，
  `CalibrationConfig.transfer` **必需读取**）、`configs/{movie,shortdrama}.yaml`
  （`calibration.transfer` 六键 + 短剧态 `budget.runs.min_window_days: 14`）、
  `agents/pilot/pilot.py`（预检清单新增 `transfer` 条目）、
  `tests/unit/{test_calibration_transfer.py,test_calibration_config.py,test_refit_gate.py,test_close_round.py,test_config_integrity.py,test_form_switch.py,test_billing_core_purity.py}`、
  `tests/contract/{test_calibration_contracts.py,test_transfer_contracts.py,test_pilot_contracts.py}`、
  `tests/adversarial/test_shortdrama_feedback_adversarial.py`、
  文档 `README.md` / `docs/三期立项书.md` / `docs/pilot-upgrade-manifest.json` / `quickstart.md`（仅验证记录段）。

**本批的判断调用与偏离（如实登记，不掩盖）**：

1. **`TransferConfig` 的落点**：并入 `core/calibration/config.py` 并由 `CalibrationConfig.from_dict`
   **必需读取** ⇒ `tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES` **不新增类**（与 T2067② 一致），
   `REQUIRED_PATHS` 增 14 条"缺项即红"条目（含 `transfer.*` 六键与嵌套 `channels.llm.tiers.*`），
   该文件的"缺项即红"用例**同时两形态各跑一遍**（T2069）。
2. **必填读取连带的既有夹具"按扩展更新"（断言零删除、零放宽）**：`test_calibration_config.py` 的
   `_VALID_SECTION`（另加一条"缺 `transfer` 即报错"参数化）、`test_refit_gate.py` 的 `_CFG`、
   `test_close_round.py` 的 `_CONFIG`、`test_calibration_contracts.py` 的 `_CONFIG_YAML` 与两处
   `from_dict` —— 各补 `transfer` 六键。
3. **可比性条件 id 的定名与判定实现面**（设计件只给"至少五类"、未给键名）：本批定名 8 个条件 id
   （`evaluator_registered` / `min_samples` / `detector_version_match` / `cadence_conversion` /
   `real_coverage_days` / `reliability_floor` / `max_abs_mean_shift` / `require_drift_pass`），
   **配置面键集 == 实现面键集**（`transfer.py` 的模块级断言常驻）；配置声明未实现的键 ⇒ 配置报错。
4. **`detector_version_match` 的判定口径（需裁决）**：严格哈希相等会使命中率恒 0——日级与周级的
   `metric_hash` 必然不同（`period_days` 与窗口单位都在哈希里，见 `core/calibration/drift_metrics.py`）。
   本批按「**算法标识一致 ∧（哈希一致 ∨ 已声明显式换算口径）**」判定，理由文案明写"量纲差异必须由显式
   换算口径承接"。若裁决要求严格哈希相等，则跨量纲迁移恒判 `not_transferable`（可迁移分支只剩同量纲形态，
   需新增"来源按目标量纲重算"的机制）。
5. **`real_coverage_days` 的观测来源（需裁决）**：设计件未给"来源真实覆盖天数"的读取面（迁移面只读件
   不含 DB 与运行记录）。本批把它做成**装配面显式声明的观测**（`--real-covered-days` +
   `--coverage-source`，镜像 019 的 `--measured-usd` / `--cost-source` 纪律），并在条件条目里**记下来源标注**：
   离线演示的"可迁移"用例标 `fixture_drill`、步⑦ 同时机检生产路径**真实覆盖 = 0**（不冒充）。
   若裁决要求"必须从日级覆盖视图（C9）读出"，需给 `ops/transfer.py` 增 DB/运行记录参数（属新契约面）。
6. **`transfer` 的退出码语义**：来源缺失与"判定不可迁移"两种拒绝都退出 **1**（C17 的"1 = 执行失败或拒绝"）；
   不可迁移件**仍然落盘**（拒绝迁移的留痕，C15 明文"拒绝迁移并如实登记原因"）；`transfer-report` 在无来源时
   退出 **0** 并如实标注（T2065④）。`--dry-run` 一律零落盘（用"目录文件集合前后相等"断言）。
7. **`transfer-report` 的观察条件阈值来源**：命令面只给 `--data-dir`（C17）⇒ 阈值取可选的 `--config`；
   两者皆无 ⇒ 输出 `null` + "未标定（不发明数字）"（不假装已标定）。
8. **T2068 的连带核对**：短剧态 `budget.runs.min_window_days: 14`（电影态保持 7、`gap_tolerance_days`
   两形态均保持 0 并留在开放问题）；`tests/unit/test_billing_runlog.py` 的 `== 7` 用 movie 派生夹具、
   `tests/conftest.py` 的内联夹具自声明 7 ⇒ 逐条核为"不红"（实测绿）。
9. **T2075 的强制选择**：`docs/pilot-upgrade-manifest.json` 的 C 路径 `status` **保持 `not_delivered`**
   ——既有断言 `tests/unit/test_pilot_upgrade_path.py::test_B_C_路径凭证名规范且非空` 明文要求 B/C 为
   `not_delivered`；按 T2075④ 在 `notes` 写清"机制已交付、真实凭证/账户/预算未到位"，并追加归属日字段
   （`metric_date`）与渠道命名空间两处协议面影响。`credential_envs` **不改名、不新增变量**（反向机检绿）。
10. **T2067⑤ 一致性核销（零改动）**：`tests/conftest.py` 的 `_billing_budget_payload` 已按 T2056④ 把档位
    以**旧扁平 `tiers`** 单渠道暴露（顺带覆盖 C11 旧形状读路径），与登记口径一致 ⇒ 本批不改夹具。
11. **T2072 的 19 项核销结论**：19 项中 17 项为"保留/已按扩展更新"（本批及批次 1~3 落地）；两项如实偏离——
    ① 第 9 项（`tests/unit/test_platform_anchors.py` 夹具补归属日 + 负例）：归属日**只在新写入路径强制**、
    读路径保留，该文件的既有读路径用例**未改**；"缺该字段 ⇒ 显式失败"的负例由 `tests/unit/test_promo_daily_ingest.py`
    与 `tests/unit/test_metric_attribution_date.py` 承担；② 第 3 项（`test_drift_detect.py` 另立日级新用例）：
    日级窗口单位的直接断言落在 `tests/unit/test_drift_config.py`（`window_unit == "day"`）与
    `tests/unit/test_form_switch.py::test_外环日级`，该文件既有断言**保留**。
12. **T2077②′ 的补齐**：`tests/unit/test_form_switch.py` 的既有字面量扫描面**显式排除 `agents/pilot`**，
    故新增 `test_渠道解析不得出现形态字面量或形态判断`（扫 `agents/pilot/backends.py` 的渠道解析路径），
    **不删改既有 `:309` 的扫描口径**；`.chat(` 调用点计数断言与 `RecordingChannelCall` 唯一包装点断言
    继续在位（本批未新增 `.chat(` 调用点；`ops/demo_shortdrama_feedback.py` 的 Mock 网关已登记进
    `tests/unit/test_billing_core_purity.py` 的 `OFFLINE_ASSEMBLIES`，构造点计数 13 → 14）。
13. **需报告的疑似设计件矛盾**：T2086⑤ 要求"阶段 1 结束时该对抗文件应为红"——本批（阶段 5/6）落地后它为
    **绿**（19 passed），阶段 1 的"红确认"未在本批范围，如实登记而非回写结论。

**本批自检实测（子代理单文件子集，2026-09-25）**：`ops/demo_shortdrama_feedback.py` ⇒ **退出码 0、七步全 ok**
（`network=none`、`credentials_required=false`、真实覆盖 = 0）；`ops/transfer.py` 四子命令 `--help` ⇒ **0**、
`--dry-run` **零落盘**（前后文件集合相等）、同键重产 ⇒ **1**、缺 `calibration.transfer` 任一键（两形态）⇒ **2**、
`transfer-report` 无来源 ⇒ **0** 且含「无可迁移结论（来源缺失）」；单测六文件 **270 passed**；
契约四文件 **108 passed**（用例数不减）；新建对抗文件 `-m adversarial` ⇒ **19 passed**；
`uv run ruff check .` ⇒ **All checks passed**、`uv run ruff format --check .` ⇒ **585 files already formatted**；
两形态 `ops/billing.py channels` ⇒ **0**（movie 无投放行）、`ops/ingest_metrics.py --help` ⇒ **0**、
`ops/calibrate.py --help` ⇒ **0**；既有节点 `score` + `eval_breakdown` 迁移与采纳前后**逐字节一致**、
迁移件**零权重键**（独立机检脚本）。
