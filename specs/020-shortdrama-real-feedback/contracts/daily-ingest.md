# 契约：日级指标回流与（活动, 周期）唯一性（C6~C10）

> 对应规格 FR-001 / FR-002 / FR-004 / FR-006 / FR-013、SC-002 / SC-003 / SC-008、US1 场景 2~7、
> 边界情况第 3/4/5/6/7 条、Clarifications 第 9/11/12/14/15 条。
> 实现落点：**新增**业务侧模块 `agents/promo/daily.py`（回流/覆盖/归属日回退计数）+ 薄 CLI
> `ops/ingest_metrics.py`（参数解析与装配，不得承载第二份口径）+ 扩 `agents/promo/{db,ingest,anchors}.py` 与
> `agents/promo/platform/{base,simulated,http_real}.py`；core 侧只新增 `core/calibration/periods.py` 的纯函数
> （见 C1~C5）与 `calibration_anchors.metric_date` 扩列（DB 迁移 `0011_daily_feedback`）。
> 现状四处硬事实：回流"一次活动一次快照"（`agents/promo/db.py:33` `node_id` 注释"回填即终态不再变"、
> `agents/promo/db.py:44` `metrics` 注释"回流后写入一次"）；只处理 `status == "delivered"` 的行（`agents/promo/ingest.py:56-60`）
> 并随即置 `ingested`（`agents/promo/ingest.py:125-135`）；节点 id 固定为 `f"{material_id}-node"`（`agents/promo/ingest.py:91`）；
> 回流节点 `created_at` 取采集墙钟（`agents/promo/ingest.py:117`），而平台锚点 `created_at` 取平台时间戳（`agents/promo/anchors.py:22-28`、`:66`）；
> `MetricSnapshot` **只有** `platform_timestamp`（`agents/promo/platform/base.py:71`、`:79`）⇒ **归属日今天无从取得**。

## C6 日级分片与（活动, 周期）唯一性（快照层幂等拒绝 vs 外环产物层零覆盖，两条规则各管一层）

**目的**：把唯一性键从"活动"降到**（活动, 周期）**，使"同一活动跨多日各采一次"**合法且不覆盖**；
同时把"同（活动,周期）重复采集 = 幂等拒绝"与"同周期多轮的外环产物 = 并留存零覆盖"两条规则分开落地，
**不得互相吞并**（前者管 DB 快照层，后者管台账/报告层）。

**形状/接口**：

```yaml
# configs/{movie,shortdrama}.yaml 的 promo 段：**新增键 schema 两形态齐备**（都能加载、缺键即报错）
promo:
  attribution_date_required_since: 2026-09-25   # 归属日必填口径生效日（C7）
  simulated_platform: {...}                     # 既有键：模拟平台分布（离线/演示用）
# 注意："两形态均须声明"指**新增参数键**齐备，**不**指两形态登记同一渠道集合：
# configs/movie.yaml 不登记 media 投放渠道，configs/shortdrama.yaml 登记；两形态都必须能装配通过。
```

```python
# agents/promo/daily.py（新增，业务侧；库函数，CLI 只作薄封装——镜像 agents/promo/ingest.py 的分层纪律）
def daily_node_id(material_id: str, period: str) -> str:
    """每周期一个节点 id：f"{material_id}-node@{period}"（周期进 id，(活动,周期) 才是身份）。

    确定性派生：同一 (material_id, period) 恒等；不同 (material_id, period) 必不相同。
    现状固定 f"{material_id}-node"（agents/promo/ingest.py:91）⇒ 多日采集必撞节点主键。
    **历史 node_id 不回改**（既有 f"{material_id}-node" 节点原样保留）。
    """
def record_daily_ingest(engine, *, campaign_id, round_id, external_id, material_id,
                        snapshot, period, collected_at, source, node_id) -> bool:
    """插入一条 promo_daily_metrics 行；同 (campaign_id, period) 已存在 ⇒ 返回 False（幂等拒绝，零变更）。"""
def ingest_daily(round_id, store, adapter, engine, config, *, source, metric_date=None) -> dict:
    """按采集日分片回流一轮：delivered|ingested → 归属日解析 → 幂等插入 → 节点一次性落盘 → 运营表更新。

    返回 {round_id, ingested: [...], rejected: [{campaign_id, period, reason}], skipped: int, periods: [...]}。
    """
def daily_coverage(engine, *, end, min_window_days, gap_tolerance_days, period_days) -> dict:
    """日级覆盖窗口（**按归属日聚合**）：见 C9；只计 source == "real" 的归属日。"""
```

- **入口条件**：`WHERE status IN ('delivered', 'ingested')`（首日 `delivered`、后续日 `ingested`；
  `agents/promo/ingest.py:56-60` 的 `== "delivered"` 是"只能采一次"的直接原因，必须放宽）。
- **运营表语义更新（无 DDL）**：`promo_campaigns.node_id` 由"终态唯一标识"改为
  **"最近一次落盘节点"**、`promo_campaigns.metrics` 由"回流后写入一次"改为
  **"节点构建素材（`eval_fragments`/`material`/`cost`/`gen_params`）+ 最近一次快照"**
  （两处注释与口径必须同步改：`agents/promo/db.py:33`、`:44`）；多日快照的**正本**在新表
  `promo_daily_metrics`（列与约束见 [../data-model.md](../data-model.md) 实体 2）。
  **既有唯一键不动**：`promo_campaigns` 的 `(round_id, material_id)`（`agents/promo/db.py:47`）**不改**，
  该表**保持无 immutable 触发器**（`agents/promo/db.py:1-5`）。
- **每周期一个节点**：`daily_node_id` 进 `TreeNode.node_id`；`observation_context` 新增
  `metric_date` 与 `period` 两键（节点自描述其归属日，便于机检与事后归因）。
- **两条规则（独立机检，互不吞并）**：
  ① **快照层（按事件）**：唯一键 =（活动, 周期）⇒ 同键再次回流 = **幂等拒绝**（`record_daily_ingest` 返回 `False`、
  零变更、整批不中断）；
  ② **外环产物层（按轮）**：同一周期内多轮的台账行与信度报告**并留存、零覆盖**（见 C3 的路径规则与
  `reports/{period}.json` 兼容别名）。
  "同一活动跨多日"是**不同周期** ⇒ 合法追加；"同周期多轮"指**外环轮次**，不是同一活动在同一周期的重复采集。

**机检断言**：

- **唯一性与多日合法**：同一 `campaign_id`、不同 `metric_date` 的三次回流 ⇒
  `promo_daily_metrics` 三行、三个 `period`（日级下即三个日期）、三个节点 id（互不相同）、零覆盖
  （`SELECT count(*) GROUP BY campaign_id, period HAVING count(*) > 1` 恒为空集）。
- **幂等拒绝**：同一 `(campaign_id, period)` 第二次回流 ⇒ 返回值 `False`、`promo_daily_metrics` 行数不变、
  节点数不变、运营表 `updated_at` 不变；整批其余条目仍被处理（`ingested` 与 `rejected` 同时非空）。
- **层间不吞并**：同一周期内两轮 `close_round` 的**台账行数** `== 2`、**报告文件数** `== 2`；
  而同一周期内同一活动的第二次采集 **0 行新增**——两句话必须同时成立（前者红不得由后者解释）。
- **既有历史行可读且不被误判**：`promo_daily_metrics` 无该 `campaign_id` 行、而
  `promo_campaigns.metrics.platform_metrics` 非空的行 ⇒ 读作 **`legacy_single_snapshot`**：
  归属日**未标定**、**不**计入任何归属日、**不**计入 `covered_days`，并在覆盖视图的
  `legacy_single_snapshots` 计数中如实登记（**不得**当成"另一次采集"，也不得当成"今天的采集"）。
- 节点 id 形态：`daily_node_id("m1", "2026-09-25") == "m1-node@2026-09-25"`；日级下节点 id 必含周期标签；
  同一 `(material_id, period)` 重复调用恒返回同一 id（确定性派生）；
  **历史节点 `f"{material_id}-node"` 的字节与 `eval_breakdown`、得分零变化**（不回改、不重命名）。

**反例**：

- 同一活动同一天第二次采集**覆盖**运营表 `metrics`/`node_id` 并当作"新数据"⇒ 红
  （是**拒绝**不是覆盖：返回值必须为 `False`）。
- 同一次采集被写成**两行**（例如按 `(external_id, metric_date)` 与按 `(campaign_id, period)` 各写一份）⇒ 红
  （唯一性键只有一个：`(campaign_id, period)`）。
- 为了"每日一份"而给同一活动每日**新建** `promo_campaigns` 行（绕过 `(round_id, material_id)` 唯一键
  `agents/promo/db.py:47`）⇒ 红（活动是同一个，分片在日级记录层，不在活动层）。
- 把"同周期多轮零覆盖"当成"（活动,周期）重复采集要覆盖"或反之 ⇒ 红（两条规则必须分开成立）。
- 把历史 `legacy_single_snapshot` 行计入 `covered_days` 或计入某个归属日 ⇒ 红（归属日未标定就是未标定）。

**兼容规则（零回改承诺）**：

- **既有"一次活动一次快照"的历史行仍可读、不得被误判为另一次采集**：读路径**必须先**查
  `promo_daily_metrics`，无命中时按 `promo_campaigns.metrics` 的既有形状回退并打 `legacy_single_snapshot` 标记；
  历史行**不迁移、不回填、不重算**（`promo_campaigns` 表结构**零 DDL**，其 `(round_id, material_id)` 唯一键
  与"无 immutable 触发器"的既定性质**都不动**）。
- 既有 `agents/promo/ingest.py:37` `ingest_round`（单快照路径）**保留**（历史夹具与 003/015 的既有断言不破），
  新逻辑走 `ingest_daily`；两函数共用 `validate_metrics` 与节点构造纪律，**不得**各写一套校验。
- 既有节点（`node_id == f"{material_id}-node"`）与其 `eval_breakdown`、得分**逐字节不变**（**node_id 不回改**）；
  节点不可变纪律（原则二）不变：多日只**追加**新节点。
- 既有产品路径（`calibration/snapshots|ledger|reports|drift`）形状不变；本契约不写 `billing/` 任何字节。

## C7 归属日来源与缺失即失败（禁止以采集时刻兜底）

**目的**：周期归属由**平台指标所描述的日期（归属日）**决定，且该日期**必须由平台接口显式给出的指标日期字段承载**；
平台没给 ⇒ **显式失败**、该条不落锚点、不参与周期归属（**不**以拉取/采集时刻兜底）。

**形状/接口**：

```python
# agents/promo/platform/base.py
@dataclass(frozen=True)
class MetricSnapshot:                    # :71
    ctr: float
    completion_rate: float
    conversions: int
    impressions: int
    clicks: int
    platform_timestamp: float            # :79 既有：**平台时间戳（真值产生时刻）**，语义不变
    data_version: str
    metric_date: str | None = None       # 新增（**末位可选**）：**归属日**（ISO YYYY-MM-DD）；None = 平台未提供

def validate_metrics(snapshot: MetricSnapshot) -> None:      # :83
    """既有越界校验 + 归属日校验：metric_date is None ⇒ MetricValidationError
    「平台未提供指标归属日」；形态非法/非真实日历日 ⇒ MetricValidationError「指标归属日非法：…」。"""

# 两个适配器的**采集出口**都必须产出该字段（机检的落点是写入路径，不是数据类）
# agents/promo/platform/simulated.py:94 fetch_metrics：metric_date 缺省取 platform_timestamp 的 UTC 日期
#   （与 :110 的固定 platform_timestamp=1700000000.0 同源 ⇒ 逐字节可复现）；可显式注入以跨日推进夹具，
#   **禁止**由 now() 派生（确定性纪律）。
# agents/promo/platform/http_real.py:194 fetch_metrics / :208 _snapshot：从
#   GET /campaigns/{id}/metrics 响应读 "metric_date"；缺失 ⇒ MetricValidationError「平台未提供指标归属日」；
#   格式不符 ⇒ 「指标归属日非法：…」；模块 docstring 的协议形状（:12-22）须补该字段。

# agents/promo/anchors.py（业务侧适配，锚点写入的唯一出口仍是 core/calibration/anchors.py:25 insert_anchor）
def collect_platform_anchors(promo_engine, anchors_conn, *, round_id, config,
                             period=None, period_days=None) -> tuple[list[AnchorScore], dict]: ...
    # AnchorScore(..., metric_date=snapshot["metric_date"])；period 给定时只采**归属日落在该周期窗口内**的行
def attribution_fallback_count(anchors_conn, *, required_since: str) -> int:
    """source='platform_truth' ∧ metric_date IS NULL ∧ created_at 的日期 < required_since 的行数（历史回退计数）。"""
```

- **形态与机检边界（父级裁决）**：`MetricSnapshot.metric_date` 保持**末位可选 `str | None = None`**
  ——目的是兼容 `agents/promo/evaluators/platform_metrics.py:46` 的**历史 dict 重建**（该处按
  `raw.get("metric_date")` 重建，历史落盘 payload 无该键 ⇒ `None`，**不得**抛构造期异常）；
  因此**机检断言必须落在写入路径上，而不是"数据类必填"**：
  ① **新采集写入路径**（两个适配器的采集出口 + `agents/promo/daily.py` 的回流写入）产生 `None` 的次数恒 **0**；
  ② 缺失 ⇒ 显式失败（原因「平台未提供指标归属日」）、该条不落锚点、不落 `promo_daily_metrics` 行、不参与周期归属；
  ③ **历史行的 dict 重建**缺该字段 ⇒ 走 `created_at` 日期回退，并在报告登记「归属日缺失锚点数」（如实标注）。

- **周期归属**：`period = period_label(metric_date, cadence)`（C1 的派生函数）；
  **周期窗口按归属日过滤**——`collect_platform_anchors(period=...)` 与 `daily_coverage` 的选取键都是 `metric_date`，
  **不是** `collected_at`、**不是** `platform_timestamp`。
- **锚点两时间语义**：`AnchorScore.created_at` 对 platform_truth 行**保持**平台时间戳
  （`agents/promo/anchors.py:22-28` 的 `_snapshot_created_at` 不变，`:66` 不变）；回流节点的 `created_at`
  **保持**采集墙钟（`agents/promo/ingest.py:117` 不变）；归属日进 `AnchorScore.metric_date`。
  三者**不等同**（迟到/回补时必然不同）且必须并列可见（见 C9）。
- **FR-004 的"两项标识"与其承载（跨件登记，U-05）**：FR-004 要求锚点携带**归属日**与**采集墙钟**两项标识。
  本设计的承载分工（**锚点行不新增 `collected_at` 列**）：
  ① **归属日**落锚点行 `calibration_anchors.metric_date`（本契约 C7 的字段）；
  ② **采集墙钟**由 **`promo_daily_metrics.collected_at` + 覆盖视图 `days[].collected_at` 共同承载**
     （该活动该周期那一次采集的墙钟；锚点行不复制它，避免同一事实两处漂移）。
  两项标识因此**可联合检索**：`calibration_anchors.metric_date`（归属日）↔
  `promo_daily_metrics.(campaign_id, period, collected_at)`（周期 + 采集墙钟），连接键 = `period`（与节点 id
  `{material_id}-node@{period}` 同源）。机检：`calibration_anchors` 的列集合在迁移 0011 后**只多 `metric_date` 一列**
  （`collected_at` **不在**该表）；锚点行的归属日与 `promo_daily_metrics` 对应行的 `collected_at` 都能取到（两者齐备率 100%）。
- **迟到/回补**：按其**归属日**归入对应周期；命中已存在的 `(campaign_id, period)` ⇒ 幂等拒绝（C6）；
  该周期的快照若因新锚点而内容变化 ⇒ 按 C3 记账（新台账行携带新指纹）；**已冻结锚点不得改写**（C8）。

**机检断言**：

- **写入路径断言（主断言）**：两个适配器的采集出口（`agents/promo/platform/simulated.py:94`、
  `agents/promo/platform/http_real.py:194`/`:208`）与 `ingest_daily` 的写入路径上，
  对任一夹具输入的产出快照 `snapshot.metric_date is None` 的次数恒 **0**；
  平台响应缺该字段 ⇒ 该条 `rejected`（原因含「平台未提供指标归属日」）、`promo_daily_metrics` 零新增行、
  锚点零新增、整批不中断。
- `validate_metrics(MetricSnapshot(..., metric_date=None))` ⇒ 抛错且消息含「平台未提供指标归属日」。
- **数据类本身不承担必填**：`MetricSnapshot(..., metric_date=None)` 构造**合法**（供历史重建），
  仅 `validate_metrics` 与写入路径拒绝它——断言写成"构造成功 + 校验失败"，而不是"构造即抛错"。
- 两个适配器产出的快照均满足 `re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot.metric_date)`（日级/周级形态下都成立）。
- **归属日即周期**：对每条日级回流行，`row.period == period_label(row.metric_date, cadence_of(row.period))`
  且 `row.metric_date == row.metrics["metric_date"]`。
- **窗口按归属日过滤**：给定归属日集合构造的覆盖窗口，`days[]` 的日期集合 == 归属日集合
  （与 `collected_at` 无关：构造一组"归属日全同、采集日各不同"的夹具 ⇒ 窗口仍只有**一天**）。
- **缺失即失败率 100%**：新采集写入路径上 `metric_date` 缺失 ⇒ 该条**不落** `promo_daily_metrics` 行、**不落**锚点、
  按 `rejected` 计数并给出原因字符串「平台未提供指标归属日」（可 grep 断言）。
- **兜底次数恒 0**：新采集写入路径上 `metric_date` 由 `collected_at`/`now()`/`platform_timestamp` 派生的次数恒 **0**；
  机检以**反向扫描**落定：`agents/promo/` 内不得出现把 `platform_timestamp`（或采集时刻）赋给 `metric_date`
  的表达式（时间戳与归属日真实同日只允许作为**平台给定的值**出现，不得由代码派生）。
- **历史回退可计数**：`attribution_fallback_count(required_since)` 的结果与覆盖视图的
  `attribution_missing_anchors` 相等（回退只对历史行、且计数如实登记，不冒充已标定）。

**反例**：

- `metric_date = datetime.fromtimestamp(collected_at).date()`（或 `now().date()`）⇒ 红（兜底会把缺口 E 重新引入）。
- `metric_date = datetime.fromtimestamp(platform_timestamp).date()` 作为**默认口径** ⇒ 红
  （`platform_timestamp` 是"真值产生时刻"，迟到/回补时与归属日不同；两者相同只是巧合）。
- 平台响应缺 `metric_date` 时静默用别的字段顶替、或填空串后继续写入 ⇒ 红（该条必须失败且不落盘）。
- 把 `MetricSnapshot.metric_date` 改成**必填字段**（构造期即抛错）⇒ 红：会打断
  `agents/promo/evaluators/platform_metrics.py:46` 的历史 dict 重建（失败点必须在写入路径，不在数据类）。
- 用 `collected_at` 决定周期归属（例如"采集日即归属日"）⇒ 红（窗口必须按归属日过滤）。
- 对 `human_blind` 锚点填 `metric_date`（把"评价发生日"冒充"平台指标归属日"）⇒ 红
  （该列只对 `source == platform_truth` 有语义；人评行的归属日由 `created_at` 承载）。

**兼容规则**：

- **历史锚点无该字段**（`metric_date IS NULL`）⇒ 允许按 `created_at` 的日期回退，并在报告登记
  「归属日缺失锚点数」（`attribution_missing_anchors`），**如实标注**；
  回退的**唯一**边界 = `created_at` 的日期 < `promo.attribution_date_required_since`；
  **新写入不得走回退**（该日及之后的写入缺失 ⇒ C7 的显式失败）。
- 历史行**不回改**：`calibration_anchors` 的既有行 `metric_date` 恒 `NULL`，永不回填（触发器只挡 `UPDATE`/`DELETE`，
  `ALTER TABLE ADD COLUMN` 不动既有行）；`0004` 建立的唯一键与冻结语义不变（`core/calibration/db.py:41`、`:47-73`）。
- `platform_timestamp` 的语义与既有读取点（`agents/promo/anchors.py:22-28`、
  `agents/promo/evaluators/platform_metrics.py:46-53`）**不变**；新增的只有 `metric_date` 一个字段，
  且为**末位可选**（`str | None = None`）⇒ 既有夹具与历史 dict 重建仍可构造；一旦走**新采集写入路径**
  即被 `validate_metrics` 明确拒绝——这正是"缺失即失败"，失败点在写入路径而非数据类。
- 归属日口径的**字段名以代码读取点为权威**：本契约登记为 `metric_date`（定义处
  `agents/promo/platform/base.py:71`，读取处 `agents/promo/evaluators/platform_metrics.py:46` 与
  `agents/promo/anchors.py:30`）；实现与 plan **必须**逐字采用，改名即视为与 FR-006 失同步。

## C8 锚点冻结与幂等（多日只追加、迟到按归属日归入、存储层拒绝改写）

**目的**：`human` 锚点一经写入即冻结为常数（原则一）；日级多日回流只**追加**新锚点；
同键重复回流**幂等拒绝**（零变更、整批不中断）；任何改写/覆盖尝试在**存储层**被拒。

**形状/接口**：

- 写入唯一接口不变：`core/calibration/anchors.py:25` `insert_anchor(conn, anchor) -> bool`
  （`begin_nested()` + `IntegrityError → False`；`:29-41` 补 `metric_date` 列值）。
- 读取：`core/calibration/anchors.py:47` `load_anchors` 补 `metric_date`（历史行 `NULL` ⇒ `None`）。
- 人评录入：`core/calibration/anchors.py:74` `intake_anchors` 语义不变（同键重复 ⇒ 该条拒绝并计数、整批不中断）；
  人评锚点 `metric_date=None`（见 C7 反例）。
- 唯一键 = `(node_id, reviewer, round_id)`（`core/calibration/db.py:41`
  `uq_anchor_node_reviewer_round`）**保持不变**：日级下 `node_id` 含周期（C6 的 `daily_node_id`）⇒
  **同一活动不同日 = 不同键 = 合法追加**；**同一（活动, 周期）第二次采集 = 同键 = 幂等拒绝**。

**机检断言**：

- **多日只追加**：同一活动跨 3 天采集 ⇒ `calibration_anchors` 新增 **3** 行（`node_id` 互不相同）；
  既有行 `updated_at`（或行内容）**零变化**。
- **幂等拒绝**：同一（活动, 周期）重复回流 ⇒ `insert_anchor` 返回 `False`、表行数不变、
  该条进 `rejected` 并给原因；同批其余条目正常入库（`accepted > 0 ∧ rejected > 0`）。
- **存储层拒改写**：对 `calibration_anchors` 的 `UPDATE`/`DELETE`（SQLite 与 PG 双方言）100% 抛错，
  报错消息含 `anchor data is immutable`（`core/calibration/db.py:44`/`:47-73`）；表行数不变。
- **迟到不改写**：归属日 `d` 已在周期 `p` 有锚点，采集日 `d' > d` 的回补 ⇒ 新增行数 **0**、
  既有锚点字段**逐字节不变**（差异只出现在**新**的采集事实与报告 note 里）。
- **标注可见**：`collected_at` 的日期 ≠ `metric_date` 时，覆盖视图对应 `days[]` 条目两者都出现
  （机读可辨"迟到/回补"），且 note 含归属日与该采集日。

**反例**：

- 为"更新真值"而 `UPDATE calibration_anchors`、或 `DELETE` 后重插 ⇒ 红（改写必须被拒；
  真值确需更新只能走升版/换锚点 `DriftAction.REANCHOR`，不在本特性范围）。
- 把迟到/回补实现成"重写历史锚点得分" ⇒ 红（只追加新事实）。
- 单条重复导致**整批中断**（异常上抛使其余条目不入库）⇒ 红（整批不中断是硬要求）。
- 为绕开同键冲突而给 `round_id` 加随机后缀（同一次采集换轮 id 重插）⇒ 红
  （轮 id 由调用方声明，不得为绕幂等而伪造）。

**兼容规则**：

- 既有 `(node_id, reviewer, round_id)` 唯一键与其幂等语义**不变**；既有锚点行零回改；
  `metric_date` 为**可空新增列**（历史行恒 `NULL`）。
- 人评录入通道（010 US1）行为**逐字节不变**：`intake_anchors` 的逐条校验、轮次门禁、`open → intake` 迁移、
  拒绝计数口径全部保持；新增字段对它是"不适用"（`None`）。
- 触发器的安装路径（`core/calibration/db.py:76-93` 的 `create_anchor_triggers` / `create_anchor_schema`）
  与双方言实现不变；迁移 `0011_daily_feedback` 只加列（见 [../data-model.md](../data-model.md) "迁移件"）。

## C9 报告三时间可见 + 覆盖按归属日（断档逐段如实报、不插值）

**目的**：让"归属日 / 采集墙钟 / 平台时间戳"三者**并列可见**（FR-006），
并让日级窗口的**覆盖 ∧ 连续**判定可机检（FR-002），缺口**逐段如实列出**。

**形状/接口**：

```python
# agents/promo/daily.py
def daily_coverage(engine, *, end, min_window_days, gap_tolerance_days, period_days) -> dict: ...
```

输出键（**本块即键集的权威清单**；与 019 `core/billing/runlog.py:226-311` 的 `window_coverage` **同构**，便于对照）：

```
{end, period_days, window_semantics, attribution_based: true,
 covered_days, covered_dates[], gaps[{from,to,days}], max_gap_days, continuous,
 min_window_days, gap_tolerance_days, meets, coverage_shortfall_days, gap_shortfall_days, reasons[],
 days[{metric_date, collected_at, platform_timestamp, source, campaign_id, period}],   # 按归属日聚合，三时间并列
 legacy_single_snapshots, attribution_missing_anchors, evidence_claim, note}
```

- **键集权威与镜像**：本清单为**唯一权威**；`data-model.md` 实体 3 与 `contracts/period-cadence.md` 的
  镜像清单必须与本处**逐键一致**（含 `attribution_based`、`legacy_single_snapshots`；**无** `start`）。
- **无 `start` 键**：窗口起始端点由 `days[]` 的最小归属日派生（`covered_dates[0] == min(days[].metric_date)`），
  `end` 为窗口右端（半开语义见 C2）；`period_days` + `window_semantics` 使端点口径自描述。
- **`attribution_based: true`**：显式声明"本窗口按**归属日**聚合"（不是采集日/平台时间戳）；
  与该字段为 `false`/缺失的窗口**不可直接比较**（口径不同，如实标注）。
- **`legacy_single_snapshots`**：历史"一次活动一次快照"的行数（归属日未标定、不参与任何归属日、不计入
  `covered_days`，见 C6）；与 `attribution_missing_anchors`（锚点侧回退计数，见 C7）**各计各的**，不互相顶替。
- **判定的唯一实现（U-01）**：`core/calibration/periods.py::coverage_window` 是"覆盖 ∧ 连续"判定的**唯一实现**
  （`covered_days` / `gaps` / `max_gap_days` / `continuous` / `meets` / `coverage_shortfall_days` /
  `gap_shortfall_days` / `reasons` 的口径只在该函数内）；`agents/promo/daily.py` 的 `daily_coverage`
  **必须委托**它——自身只做"DB ⟶ 归属日集合 + 三个阈值"的取数与组装，**不得**重算上述任一量。
- **聚合键 = 归属日**：`days[]` 按 `metric_date` 排序去重；`covered_days` 只计 `source == "real"` 的归属日；
  同一归属日的多条记录合并为一条 `days[]` 条目（`platform_timestamp` 取该日**最新**一次读取值、
  `collected_at` 取该日**最晚**采集墙钟，并保留 `sources` 计数以便"模拟不得冒充真实"的机检）。
- **派生视图，不落盘**：与 `ops/billing.py runs` 同构（`core/billing/runlog.py:226` 返回 dict、CLI 打印 + 退出码）；
  可机检证据 = `promo_daily_metrics` 的 append-only 行 + 台账/报告，**不新增**覆盖文件（见 data-model 磁盘布局）。
- **口径来源与取值（父级裁决）**：`min_window_days` / `gap_tolerance_days` 取两形态的 `budget.runs.*`
  （`configs/shortdrama.yaml:600-602`、`configs/movie.yaml` 同段）。短剧态 **`min_window_days: 14`**
  （立项书 G4 原文"短剧线真实数据回流 ≥2 周"）、电影态 **7**；`gap_tolerance_days` **保持现值**
  （短剧态现为 `0`）并**留在开放问题**（是否留余量待运营与制片侧给出）——**不取码内默认**。
- **维度边界（如实登记）**：覆盖判定**不按渠道分片**（跨渠道按归属日合并）；投放渠道标识由锚点 `reviewer`
  （`agents/promo/anchors.py:64` 的渠道标识口径）与 `days[].source` 承载。若后续需按渠道分片覆盖，属新契约，不在本特性范围。
- **三时间并列可见的规范面 = 日级覆盖视图的 `days[]`**（三键齐备，见下条机检）：它是唯一同时握有
  DB 行（`promo_daily_metrics`）与三时间字段的读取面。**为什么不在信度报告里逐条列三时间**：
  `core/calibration/report.py:20` 的 `build_report` 是**文件层**函数（只读台账、无 DB 连接），
  与 019 把 `window_coverage` 放在**读账本的一侧**（`core/billing/runlog.py:226`）同一取舍。
- **FR-004 的"采集墙钟"在此可见（跨件登记，U-05）**：`days[].collected_at` 就是 FR-004 要求锚点携带的
  **采集墙钟**的承载面之一（另一承载 = `promo_daily_metrics.collected_at`）；
  锚点行**不新增** `collected_at` 列，故本视图是"锚点 ↔ 采集墙钟"的唯一并列面（详见 C7 的跨件条目）。
- **信度报告与台账行承载的是"归属周期 + 快照溯源"**：台账行携带 `period` / `period_days` / `round_id` /
  `snapshot_fingerprint` / `anchor_count`（C3），报告 payload 携带 `period` / `period_days` / `window{start,end}`；
  与锚点行的 `metric_date`（归属日）+ `created_at`（平台时间戳）、回流行的 `collected_at`（采集墙钟）
  合起来构成三时间的**可追溯链**（`round_id` → 台账行 → 快照 → 锚点 → 日级回流行）。
  报告顶层 `note` 与 `attribution_missing_anchors` 由 CLI 汇总时并入（口径见 C7 回退规则）。

**机检断言**：

- **三时间齐备率 100%**：`days[]` 每条同时含 `metric_date`（非空、ISO 日期）、`collected_at`（float>0）、
  `platform_timestamp`（float>0）三键；缺任一键 ⇒ 红。
- **键集一致（I-03）**：`data-model.md` 实体 3 与 `contracts/period-cadence.md` 的覆盖视图键清单
  与本 C9 块**逐键相同**（含 `attribution_based` / `legacy_single_snapshots`、无 `start`）；三处任一键增删 ⇒ 红。
- **判定的唯一实现（U-01，静态断言）**：`agents/promo/` 与 `ops/` 的**非测试**代码内
  不得出现自算的 `meets` / `max_gap_days` / `continuous` 口径——即不得出现
  `min_window_days` 与 `max_gap` 在同一表达式/同一函数内参与比较的写法；`daily_coverage` 必须调用
  `core/calibration/periods.coverage_window`（AST 断言：`agents/promo/daily.py` 内含该调用，
  且其函数体内**零**比较运算涉及 `"meets"` / `"max_gap_days"` 字面量）。反向扫描命中即红。
- **按归属日聚合**：夹具构造"归属日全同、采集日跨 3 天" ⇒ `covered_days == 1`、`days` 长度 `== 1`；
  夹具构造"归属日跨 15 天、采集日全同" ⇒ `covered_days == 15`（聚合键是归属日，不是采集日）。
- **覆盖 ∧ 连续双条件**：`meets == (covered_days >= min_window_days) ∧ (max_gap_days <= gap_tolerance_days)`；
  **短剧态配置**（`min_window_days=14, gap_tolerance_days=0`）："连续 15 天真实"夹具 ⇒ `meets is True`；
  "15 天真实但缺 2 天"夹具 ⇒ `meets is False` 且 `gaps` 逐段列出（段数 `== 缺口段数`、每段含 `from/to/days`）；
  电影态同断言以 `min_window_days=7` 复跑一遍（两形态都能装配通过、都能判定）。
- **不插值**：`covered_dates` 的日期集合 == `days[]` 中 `source == "real"` 的 `metric_date` 集合；
  `gaps` 与 `covered_dates` **无交集**（缺口日期不得出现在覆盖列表里）；
  插值/补零/以旧值顶替出现次数恒 **0**（机检：`days[]` 中每个 `metric_date` 都必须有对应的
  `promo_daily_metrics` 行，行数核对相等）。
- **模拟不顶真实日**：`simulated` 归属日出现在 `days[]` 但**不在** `covered_dates`；
  `covered_days` 与 `days[]` 中 `simulated` 条目数无关。
- **历史单快照不参与**：`legacy_single_snapshots > 0` 时 `covered_days` 不含这些行
  （归属日未标定 ⇒ 不参与周期归属）。
- **归属日缺失如实登记**：`attribution_missing_anchors == attribution_fallback_count(required_since)`
  （C7 的 DB 计数）；该值 > 0 时 `note` 必含"归属日缺失（按 created_at 回退）"字样。
- 退出码：CLI 覆盖检查在 `meets is False` 时退出 **1**（如实报未达标），
  参数/配置错误退出 **2**，全部达标退出 **0**（与既有工具语义一致）。

**反例**：

- 缺口处插值补齐、或把缺失日填 0/上一日值 ⇒ 红（"累计够天数但有断档不得通过"）。
- `covered_days` 计入 `simulated`/`fallback` 日 ⇒ 红（模拟不得计入真实覆盖）。
- 用 `collected_at`（采集日）代替 `metric_date` 聚合 ⇒ 红（与 C7 同一条禁令的另一面）。
- 只报"没有缺口"而不列 `gaps`/`max_gap_days` ⇒ 红（缺口必须逐段可见，失败可归因）。
- 三时间任一被省略、或以 `platform_timestamp` 冒充 `metric_date` ⇒ 红。

**兼容规则**：

- 019 的运行记录与覆盖口径**零改动**：`core/billing/runlog.py` 的 `RUN_SOURCES`、`window_coverage`、
  `billing/{channel}/runs/{date}.json` 布局与其 CLI 行为不变；本契约只在**日期集合**层面复用其判定形状
  （`coverage_window` 为纯函数，**不** import `core.billing` ⇒ 不引入新的 core 内耦合）。
- 010 的信度报告既有键（`period`/`agents`/`target`/`alerts`）与 `reports/{period}.json` 兼容别名**零变化**，
  既有读取点（`core/calibration/drift_report.py:98`、`web/queries.py:831-838`、`web/parity.py:45`）在
  周级单轮形态下取到同一份内容。
- 既有 `agents/promo/config.py` 的 `budget_cap_usd`/`model_prices` 等键与 015 的进程内按轮上限**不变**；
  本契约**不**触碰 019 的跨进程账本（两者口径不得混同，见 FR-008）。

## C10 运行来源纪律（`real` / `simulated` / `fallback`，模拟不得冒充真实）

**目的**：模拟与真实在产物上**机读可区分**；"模拟被标为真实"次数恒 0；
真实回流未达成时**禁止**输出"真实数据回流已达成"一类结论（诚实分层，FR-013）。

**形状/接口**：

```python
# 取值域唯一属主（不得在本特性内重声明）：core/billing/runlog.py:28
RUN_SOURCES = ("real", "simulated", "fallback")

# agents/promo/daily.py
def ingest_daily(round_id, store, adapter, engine, config, *, source, metric_date=None) -> dict:
    """source 由**装配面显式声明**（必填，取值域校验：域外 ⇒ 拒绝，不猜、不默认）。"""

# ops/ingest_metrics.py（薄 CLI；生产走真实适配器，离线走模拟适配器）
#   --source {real,simulated}   必填：real ⇒ HttpRealPlatform.from_env()（缺凭证 ⇒ 装配期显式拒绝，exit 2、零落盘、零扣费）
#                                 simulated ⇒ SimulatedPlatform(config.simulated_platform)（仅离线/演示）
#   --daily / --metric-date / --coverage / --end / --min-window-days / --gap-tolerance-days
```

- **唯一的来源标注点** = 装配面（镜像 `core/billing/runlog.py:313-346` `RecordingGateway` 的
  `source=` 装配参数纪律：来源是**装配面声明的事实**，不是被推断的属性）。
- **回落必须显式**：`fallback` 是保留值，写入时必须带**原因**（`fallback_reason` 非空，
  镜像 `core/billing/runlog.py:138-141` 的硬校验）；真实渠道失败**禁止**静默回落模拟并照常计费；
  本特性**不**在 `agents/promo/` 内新增任何回落路径（失败即如实失败）。
- **`evidence_claim`**（覆盖视图字段，取值域二元素）：
  `"mechanism_ready_real_feedback_pending"`（默认）| `"real_feedback_met"`（仅当 `meets is True`
  且 `covered_days` 全部来自 `source == "real"` 时允许）；报告与 CLI 输出**必须**同时给出真实覆盖天数与缺口。
- **两形态都能装配通过**：`configs/movie.yaml` **不登记** media 投放渠道（其 `channels` 只有既有 LLM 渠道），
  `configs/shortdrama.yaml` 登记；两者的新增键（`promo.attribution_date_required_since`、
  `calibration.window_semantics` / `window_semantics_change_date`、`budget.runs.*`）**都齐备**，
  缺任一键即报错（不取码内默认）；"两形态均须声明"指**键齐**，不指登记同一渠道集合。

**机检断言**：

- `source` 取值域外（如 `"mock"`、`"stub"`、`"test"`）⇒ `ingest_daily` 抛错、零落盘（不猜、不降级）。
- **取值域单点**：`agents/promo/`、`core/calibration/` 内**不得**出现
  `("real", "simulated", "fallback")` 字面量重复声明（反向扫描断言；字面量只允许在 `core/billing/runlog.py:28`）。
- **模拟不计覆盖**：`source == "simulated"` 的归属日**不**进 `covered_dates`；
  夹具"15 天全模拟" ⇒ `covered_days == 0`、`meets is False`、
  `evidence_claim == "mechanism_ready_real_feedback_pending"`。
- **不得冒充达成**：`evidence_claim == "real_feedback_met"` 且 `covered_days` 中含 `simulated` 日 ⇒ 红；
  含 `fallback` 日 ⇒ 红；`covered_days < min_window_days` ⇒ 红。
- **回落有因**：`source == "fallback"` 且原因为空 ⇒ 拒绝（报错消息含"必须声明原因"）；
  `agents/promo/` 内真实渠道失败路径上"回落并照常计费"的调用点数恒 **0**（静态扫描）。
- **零真实花费**：以 `simulated` 跑完整日级闭环（含覆盖检查）时，任何真实渠道装配点数恒 **0**
  （沿用 019 的"真实装配点清单常驻断言"纪律）；报告结论必须写成"机制已就绪 / 真实回流待运营"。
- **覆盖口径单点（U-01 在来源纪律侧的落点）**：`covered_days` / `meets` / `evidence_claim` 上的
  "真实来源"效应**只**经 `coverage_window` 的入参（归属日集合与 `source` 标注）生效；
  `agents/promo/` 内**不得**在调用 `coverage_window` 之后再对 `covered_days` / `meets` 做二次判定或修正。

**反例**：

- 模拟日被写 `source="real"`（或以模拟数据补齐真实断档）⇒ 红（"模拟被标为真实"次数必须恒 0）。
- 真实渠道失败时静默回落模拟并继续计费/继续计入覆盖 ⇒ 红。
- 在未满足 `meets` 时输出"短剧线真实数据回流 ≥2 周已达成"⇒ 红（运营侧墙钟 + 凭证前提未满足，只能如实标注）。
- 用 `agent_id`/`adapter` 的类名**推断**来源（而非装配面显式声明）⇒ 红。
- 把 `simulated` 的归属日计入 `gaps` 之外的"已覆盖"、或把 `fallback` 当 `real` 计数 ⇒ 红。

**兼容规则**：

- 019 的运行记录、账本与告警产物**零改动**：`RUN_SOURCES`、`RUN_ENTRY_FIELDS`（`core/billing/runlog.py:32-41`）、
  `append_run`（`:115`）与 `billing/**` 的目录形状都不变；本契约只**引用**其取值域与硬校验口径
  （`import` 使用，不复制、不派生第二份）。
- 015 的进程内按轮上限（`agents/promo/config.py:70-72`、`agents/promo/loop.py:400-418`、`_round_spent` `:240`）
  与 019 的跨进程文件账本**不得混同**：本契约不新增旁路门禁，也不改写既有估算门禁的拒绝语义；
  真实投放调用**必须**同时过两道门禁（估算 + 账本），拒绝理由须点名命中的是哪一条。
  投放渠道与其额度的绑定走 019 的既定分派接口（`declared_channels(cfg)` / `channel_for_adapter(cfg, adapter_id)`，
  弃 `channel_for_slot`；`channels.<id>.adapter` 取值域不变，既有 `pilot_llm` 原样保留），
  契约面归 `contracts/channel-budget.md`（C11~C14）。
- 既有 `ingest_round`（单快照路径）与既有夹具/断言（含 `tests/unit/test_ingest_metrics.py`）**不减项**：
  新行为以 `ingest_daily` 并列提供，既有调用点不动。

### 场景

1. 同一活动跨 3 天（三个归属日）各采一次 ⇒ 三个周期、三个节点（`{material_id}-node@{period}`，互不相同）、
   三行日级记录、零覆盖；第三次之后重跑第二天 ⇒ 该条幂等拒绝（零变更）、整批不中断
2. 平台响应缺 `metric_date` ⇒ 该条显式失败（原因「平台未提供指标归属日」）、不落锚点、不参与周期归属；
   同批其余条目照常入库
3. 短剧态 15 天日级夹具（`min_window_days=14, gap_tolerance_days=0`）：连续 ⇒ `meets=true`；
   缺 2 天 ⇒ `meets=false` 且 `gaps` 逐段列出、`days[]` 三时间并列、无插值
4. 全模拟跑同一套夹具 ⇒ `covered_days == 0`、`evidence_claim` 为"机制已就绪"、
   `attribution_missing_anchors` 如实登记，且零真实花费
5. 迟到/回补：归属日 `d` 已有锚点 ⇒ 新增行 0、既有锚点逐字节不变、两者时间在报告里都可见
6. 两形态装配：`configs/movie.yaml`（不登记 media 投放渠道）与 `configs/shortdrama.yaml`（登记）
   都能加载并装配通过；任一形态缺新增键即报错
