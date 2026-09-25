# 契约：渠道命名空间与投放门禁分派（C11~C14）

> 对应规格 FR-007 / FR-008、SC-004、US2 场景 1/2/6、边界情况「渠道命名空间与单渠道硬拒绝」与
> 「与既有"按轮上限"的关系」、澄清第 10 条、裁决 F。实现面：`core/billing/budget.py`（配置形状 +
> 按渠道取档 + 装配分派）、`agents/pilot/backends.py`（链内两个真实装配点）、`ops/billing.py`
> （`--channel` 语义与只读渠道面）。
>
> **性质**：对 019 的**兼容性扩展**，不是削弱、不是第二套机制。`core/billing/{bill,reconcile,calibration,runlog}.py`
> 与 `billing/{channel}/…` 五类产物的**路径与形状逐字不变**，历史账本/账单/报告/校准记录/运行记录**零回改**；
> 019 既有断言**只按扩展更新、不删项、不放宽**（逐条清单见 C13）。

## C11 渠道命名空间配置形状（`channels.<id>.tiers.<环节>`）+ 旧扁平形状兼容读

**目的**：把 019 的扁平额度表升级为**按渠道分组**的命名空间，使"LLM 渠道 + 投放渠道并存"可在配置里
被**唯一表述**，从而支撑"同一档位不跨渠道串用"与"未声明渠道不得开工"两条纪律；同时让 019 现形状的
配置**继续可读**且归一口径**显式、可追溯、歧义即报错**（FR-007 / FR-014）。

**现形状（019）**：`budget.tiers.<环节>` 是**扁平**的环节 → 额度映射（`core/billing/budget.py:275`
的 dataclass 字段、`core/billing/budget.py:1063` 的 `_parse_tiers`），渠道只声明 `adapter` / `bill`
（`configs/movie.yaml:510-540`、`configs/shortdrama.yaml:513-543`）。

**新形状（020；`adapter` 保持 019 语义——该渠道"真实调用面的装配入口"标识，**取值域不变**）**：

```yaml
budget:
  channels:
    llm:                       # 渠道 id 由配置声明（core/agents/ops 零渠道 id 字面量、零形态分支）
      adapter: pilot_llm       # ← **既有取值原样保留、不重命名**（该渠道真实调用面的装配入口）
      bill: {…}                # 019 原样（格式 id / 来源形态 / 列映射 / 分类驱动列取值域）
      tiers:                   # 019 的扁平 budget.tiers 归位到这里；键 = 环节 id = .chat(stage=)
        screenplay: {limit_usd: 2.0, window: {kind: day}, on_exhausted: refuse, note: "未标定…"}
    media:                     # C 路径投放渠道：**只登记在短剧态**（投放属短剧线 C 路径，movie 不登记）
      adapter: promo_platform  # **权威定名**：本特性投放渠道的 adapter 取值 = `promo_platform`
      bill: {…}                # 投放平台账单导入面（复用 019 的 bill / reconcile，不新造）
      tiers:
        promo_launch: {limit_usd: <未标定>, window: {kind: day}, on_exhausted: refuse, note: "未标定…"}
```

**示例占位（不得照抄，不改 019 现形状）**：上例 `media.tiers.promo_launch.limit_usd: <未标定>` 里的尖括号
取值只是"此处须填**数值**"的位置标记——**实际配置必须写数值**（`limit_usd` > 0、非 bool、有限），并配
`note: "未标定（…最小规模档）：运营给定后只改本值"`（沿用 019 现形状与"未标定"标注口径，
`configs/shortdrama.yaml:546-549`）。`<…>` 尖括号占位**不是**合法配置取值，**不得**出现在任何真实配置
文件里（机检：`configs/*.yaml` 内不出现 `<`/`>` 占位；`BudgetConfigError` 对非数值 `limit_usd` 恒报错）。
同理，示例里的 `bill: {…}` 表示"019 原样、不展开"，**不是**可照抄的取值。

- **`adapter` 的取值域与语义不变**（019 原样：该渠道"真实调用面的装配入口"标识）；**权威定名**：
  LLM 渠道沿用既有 `pilot_llm`（**不重命名**），**本特性投放渠道的 `adapter` 取值 = `promo_platform`**
  （指 `agents/pilot/backends.py:399-406` 的投放适配器装配面；以**配置为权威**，此处给出的是唯一合法定名，
  **不是**"例如"）。渠道解析按**装配入口 id**（C12 的 `channel_for_adapter(cfg, adapter_id)`），
  **歧义即报错**；`adapter` 在快照/报告/运行记录里仍作可读引用（`core/billing/runlog.py` 的
  `adapter_ref` 语义不变）。
  **口径张力（如实登记 + 机检兜住）**：装配入口 id 因此会以字面量出现在装配层
  （`agents/pilot/backends.py`）、而它同时是配置取值。故加一条静态断言：**代码里传出的 adapter id 必须
  能在两形态配置的 `budget.channels.*.adapter` 声明中逐字找到**（本特性的两个取值即 `pilot_llm` /
  `promo_platform`）——改配置的 adapter 取值即须同步改装配点，反之新增装配入口而不登记配置即红。
- `ChannelSpec` 扩 **1** 键：`tiers`（必填非空映射，键 = 环节 id）；`core/billing/budget.py:1016` 的
  `_parse_channels` 逐键校验：缺 `tiers`、`tiers` 为空、`adapter` 缺失或为空串、`tiers` 内某档缺
  `limit_usd` / `window.kind` / `on_exhausted` 一律 `BudgetConfigError`（**缺项即报错、不取码内默认**，FR-014）；
  档位 `note` 的口径可追溯纪律逐字沿用 019（`core/billing/budget.py:1081-1084`）。
- **每个登记渠道必须声明 `bill`（账单导入面）**：形状**复用 019 不新造**——格式 id + 来源形态
  （`export|api`）+ 语义列映射 + 分类驱动列取值域（`line_kind` → 六类、`amount_sign` → 符号），
  缺任一键即 `BudgetConfigError`。**投放渠道的 `bill` 面因此可用**：账单导入 → 逐项对账 → 差异分类与
  告警全部走 019 的 `core/billing/bill.py:321` 与 `core/billing/reconcile.py:233`（验证序列见
  `quickstart.md` 的 B6），**不新造第二套对账**。
- **最小规模档 = 该渠道投放环节档位的 `limit_usd`**（不新增额度键）：未标定期间该档即"最小规模档"
  （019 现状注释口径，如短剧态各档 `note` 均标"未标定：运营给定前按最小规模档运行"，`configs/shortdrama.yaml:546-549`）；
  扩量仍是 `raise_tier`（C16）。
- **形态差异由配置承载（本契约的判断项，按裁决逐字落）**：**投放渠道只登记在短剧态**
  （投放属短剧线 C 路径）；`configs/movie.yaml` **不登记** media 渠道。FR-014 的"两形态均须声明"指
  **新增参数键的 schema 齐备**——两形态都能加载 `channels.<id>.tiers` 这套形状、缺键即报错
  （不取码内默认），**不是**要求两形态登记同一份渠道清单。两条硬要求：
  ① **两形态都必须能装配通过**（默认全模拟链路，`configs/movie.yaml` 与 `configs/shortdrama.yaml`
  各自可装配）；
  ② **movie 路径不得因投放渠道或其凭证而失败**：未启用真实投放时不触发投放渠道的任何判定
  （凭证矩阵 / 最小规模 / 账单面均不参与）；若在**未登记投放渠道**的形态上声明真实投放
  （`pilot.overrides.promo: http`）⇒ **装配期显式拒绝**并指出"该形态未登记投放渠道"
  （不静默降级为模拟、不发明渠道，原则三"未登记不得开工"）。
- **声明顺序约定**：LLM 渠道是 `budget.channels` 的首个条目（短剧态的投放渠道在其后）。顺序**不是**语义
  （解析与分派按 `adapter` / `channel_id`，不按位置），但既有夹具 `_channel(cfg) = next(iter(cfg.channels))`
  （`tests/contract/test_billing_contracts.py:146`）以首个条目取值，故写成约定并机检：两形态的
  `declared_channels(cfg)[0]` 必须是 LLM 渠道（`adapter` = `pilot_llm`）。
- 新增接口（`core/billing/budget.py`，**签名与 `sole_channel(cfg)` 既有模块级风格一致**）：
  `declared_channels(cfg) -> tuple[ChannelSpec, ...]`（配置声明的**全部**渠道，取代 `sole_channel` 的单渠道
  断言）、`channel_for_adapter(cfg, adapter_id) -> ChannelSpec`（按 `channels.<id>.adapter` 取值把某装配面
  映射到其渠道）、`tiers_of(channel_id)`、`tier_of(channel_id, tier_id)`，以及 `BudgetConfig.tiers_shape`
  （取值域 `("channels", "legacy_flat")`）；`BudgetConfig` 侧只作**同名薄委托**（单一实现）。
  既有 `tiers`（`:275`）与 `tier()`（`:318`）降级为**单渠道兼容视图**（见下）。
- 装配快照随行形状与分派事实：`SpendGuard.snapshot()`（`:798-806`）+ `to_snapshot`（`:338-349`）落
  **本渠道**档位、渠道 id、`adapter`、`tiers_shape`——改配置只影响此后新装配，历史节点不变（原则一）。

### 旧扁平形状兼容读（本契约的兼容条款：显式归入其声明的渠道、歧义即报错）

**旧扁平形状** = 配置只有 `budget.tiers.<环节>`、没有 `channels.<id>.tiers`（019 现形状，
`configs/movie.yaml:541`、`configs/shortdrama.yaml:544` 的现存键位）：

1. 该配置 `budget.channels` 恰好声明 **1** 个渠道 ⇒ **显式归入该渠道**，`tiers_shape = "legacy_flat"`，
   并往 `cfg.notes` 追加「旧扁平 tiers 显式归入渠道 `<id>`（迁移形状）」；装配快照随行 `tiers_shape`。
2. 声明 **≥2** 个渠道 ⇒ `BudgetConfigError`（**歧义即报错**：不静默归入首个/任一渠道，不静默误判为
   另一个渠道）。
3. 新旧两处**同时**出现（既有顶层 `tiers`、又有某渠道的 `tiers`）⇒ `BudgetConfigError`（两个来源，
   不静默择一）。
4. 归一后 `tiers_of(...)`、门禁、账本、窗口键一律走同一路径 ⇒ 旧形状与旧账本继续可读，
   `billing/{channel}/ledger.json` 形状不变（`core/billing/budget.py:480`）。

**单渠道兼容视图（Python 侧同一纪律）**：`BudgetConfig.tiers`（`:275`）与 `tier(tier_id)`（`:318`）
仅在"声明的渠道集合大小为 1"时可用（此时它 ≡ 那个渠道的档位）；多渠道配置下访问即抛
`BudgetConfigError`（提示改按渠道取档）——**同一档位不得跨渠道串用**在 API 层也成立。
`tier_of` 的缺档错误文案**必须保留「未在 budget.tiers 声明」**（`tests/unit/test_billing_config.py:47` 依存）。

### 机检断言

- **新增参数键的 schema 齐备率 100%（两形态都能加载）**：每个 `channels.<id>` 必带**非空 `tiers`** 与
  **非空 `adapter`**，缺任一 ⇒ `BudgetConfigError`（不取码内默认；新增
  `tests/unit/test_billing_channels.py`）——注意这是"键的 schema 齐备"，**不是**"两形态登记同一份渠道清单"
  （投放渠道只登记在短剧态，见下）。
- `tiers_shape` 可追溯率 **100%**（配置对象与装配快照里可见）；旧扁平形状归一后
  `tiers_shape == "legacy_flat"` 且 `notes` 含归一事实与渠道 id。
- **静默归并次数恒 0**：多渠道配置下访问 `tiers` / `tier(id)` ⇒ 抛错（不返回任一渠道的档位）；
  新旧并存 ⇒ 报错（不静默择一）。
- 旧配置**继续可装配**：用 019 现形状的配置（顶层扁平 `tiers` + 单渠道）跑通装配与超限拒绝，
  行为与新形状等价（新增单测：同一场景两形状结果逐字段一致）。
- 环节 id 仍 = `.chat(` 调用点声明的 `stage=` 取值；键集断言 `tests/contract/test_billing_contracts.py:608`
  按新形状更新（见 C13 附加表），**调用点计数仍为 8**（`:614`）。
- `core/billing/` 零渠道 id / 零形态字面量 / 零形态分支：`tests/unit/test_billing_core_purity.py:31-40`
  的扫描面常驻（新增的 `tiers` 键名与 `tiers_shape` 取值不得引入形态或渠道字面量）。
- 代码侧 adapter id 与配置一致率 **100%**：`agents/` 内传出的 adapter id ∈ 两形态之一的
  `budget.channels.*.adapter` 声明集（新增装配入口未登记配置 ⇒ 红；静态断言）。
- **两形态装配通过率 100%**：`configs/movie.yaml` 与 `configs/shortdrama.yaml` 都能装配成功
  （默认全模拟链路）；**movie 路径因投放渠道或其凭证而失败的次数恒 0**——movie 不登记投放渠道，
  未启用真实投放时投放面（凭证矩阵 / 最小规模 / 账单）不参与任何判定。

### 反例

1. 短剧态 `channels.media` 缺 `tiers` ⇒ 装配报错（投放环节无档一律拒绝，不取码内默认、不降级为模拟）。
2. 在 movie 上声明 `pilot.overrides.promo: http`（该形态未登记投放渠道）⇒ **装配期显式拒绝**并指出
   原因；若静默降级为模拟或凭空发明渠道 ⇒ 红。
3. 把 `pilot_llm` 重命名（或把 `adapter` 改成槽位 id 之类的另一套取值域）⇒ 既有配置与断言被迫改动，
   红（**本契约不要求也不允许重命名**）。
4. 多渠道 + 顶层旧 `tiers:`，实现"就近归入 LLM 渠道" ⇒ 静默误判为另一个渠道，红。
5. 用"渠道数 > 1 时把各渠道 `tiers` 合并返回"实现兼容视图 ⇒ 跨渠道串用，红。
6. 代码里写 `if channel_id == "media"` ⇒ 纯度断言红（渠道是配置数据，不是分支条件）。

### 兼容规则（对 019 零回改）

- `peak_windows` / `calibration` / `reconcile` / `ledger` / `runs` 五段形状**逐字不变**；`bill` 段形状不变。
- 五类产物路径与形状逐字不变：`core/billing/budget.py:475-486`（`channel_dir` / `ledger_path` /
  `alerts_path`）、`core/billing/bill.py:384`、`core/billing/reconcile.py:141`、
  `core/billing/calibration.py:93`、`core/billing/runlog.py:76-78`——历史文件**零回改**。
- 旧形状配置的历史产物（账本 / 告警 / 报告 / 校准 / 运行记录）**逐字节可用**；归一只发生在**读入内存**
  这一步，不触发任何落盘改写。本条款是"旧形状仍可读"的**唯一**实现面，不得在别处再写一份形状归一。
- 测试夹具口径不变：`tests/conftest.py:4109-4135`（`_billing_budget_payload`）派生时按 **LLM 渠道**
  （`adapter` = `pilot_llm`）收敛为**单渠道**并把该渠道档位以**旧扁平 `tiers`** 暴露 ⇒
  `billing_budget_factory(...)["tiers"]` 与 `budget_config_factory(tiers=…)` 的**调用体零改动**，
  且顺带覆盖本条款的读路径。
- 新增配置键须同步 019 的**五处登记点**（`specs/019-real-channel-billing/quickstart.md:99` 所列清单：
  形态差异集 / 配置完整性 / pilot 契约段差异集 / `config_completeness` 预检 / 夹具）。

## C12 装配与额度按配置声明的渠道集合分派（含投放调用接入该门禁）

**目的**：放开 019 的**单渠道硬拒绝**，改为"按配置声明的渠道集合分派额度"，并把 C 路径的**投放调用**
接进同一套前置门禁，**不另造旁路门禁**（FR-007 / FR-008）。调用语义（调用前拒绝、平台 0 次调用、
零入账）在 C14 收口，本契约负责"接线到哪个门禁、按哪个渠道取档"。

`sole_channel()`（`core/billing/budget.py:357-364`）的"必须恰好声明一个渠道"硬拒绝**退役**，改为
「**按配置声明的渠道集合分派额度**」：

- `declared_channels(cfg) -> tuple[ChannelSpec, ...]`：声明顺序的**全部**渠道；空 ⇒ `BudgetConfigError`。
- `channel_for_adapter(cfg, adapter_id) -> ChannelSpec`：恰好 **1** 个渠道的 `adapter` == `adapter_id`
  ⇒ 返回；**0** 个 ⇒ `BudgetConfigError`（该装配入口未登记渠道，不发明）；**≥2** 个 ⇒ `BudgetConfigError`
  （歧义，不猜用哪个账本）。
- `assemble_guard(config_path, *, channel_id=None, window_context=None, clock=None)`
  （`core/billing/budget.py:420-461` 扩参，装配序列与返回的 `GuardAssembly` 形状不变）：**装配点先用
  `channel_for_adapter(cfg, <该装配入口的 adapter 取值>)` 解析出渠道、再把 `channel_id=` 传入**（解析只有
  一个实现，装配点不各写一份）；`channel_id` 传入时须 ∈ `declared_channels(cfg)`（否则报错）；
  `channel_id` 缺省时：声明数 == 1 ⇒ 取该渠道（019 既有 `assemble_guard(config_path)` 单渠道调用
  **保留可用**）、> 1 ⇒ 报错（不猜）。
- **额度按渠道分派（同一档位名在不同渠道是两份额度、两本账）**：
  - `SpendGuard` 取档改走 `self.cfg.tiers_of(self.channel_id)`（`core/billing/budget.py:841`、`:848`）；
    `snapshot()` 只落**本渠道**档位（`:338-349`）；`channel_mismatch` 拒绝保留（`:830-840`）——
    请求渠道 ≠ 守卫渠道即拒绝，**跨渠道串用次数恒 0**。
  - 账本、告警、运行记录、账单、报告、校准记录全部落 `billing/{channel}/…`（`channel_dir`，`:475`）；
    `billing/llm/ledger.json` 与 `billing/media/ledger.json` 互不可见。
  - 装配期校验 `cfg.channel(self.channel_id)`（`:792`）保留：未登记渠道不得装配门禁。
- **链内两个真实装配点**（019 C10 ② 的"唯二"不变，**不新增第三个**）：
  - **LLM 面**：`agents/pilot/backends.py:280` → `assemble_guard(config_path, channel_id=…)`，其中
    `channel_id` 由 `channel_for_adapter(cfg, "pilot_llm")` 解析而来（该形态配置里 LLM 渠道的 adapter 取值
    现为 `pilot_llm`）；`spend_guard=` / `channel_id=` / `peak_windows=` 注入点
    `agents/pilot/backends.py:289-291` 不变。
  - **投放面**：`agents/pilot/backends.py:399-406` 的 `_promo` 真实分支 → 先
    `channel_for_adapter(cfg, "promo_platform")` 解析出投放渠道（未登记 ⇒ 报错，不猜），再外层包
    **投放调用门禁包装**
    （新增 `core/billing/runlog.py` 的 `RecordingChannelCall`，与既有 `RecordingGateway`
    `core/billing/runlog.py:313` 同构、**同处无第二份实现**）：包装在 `create_campaign(...)` **之前**
    取门禁判定、之后结算并落一条运行记录（`source` 由装配面声明，见 C18）；预算拒绝与实测超预估
    的行为见 C14。`_promo` 增入参 `config_path`（`build_backends` 已持有该参数，
    `agents/pilot/backends.py:296`）——**改签名、不加分支**。
- **CLI（薄转发，判定全在 `core/`）**：
  - `ops/billing.py:102-110` 的 `_channel_of` 改为**按声明渠道集合分派**：requested ∈ 声明的渠道 id 集
    （`[spec.channel_id for spec in declared_channels(cfg)]`）⇒ 用之；否则 `BudgetConfigError` ⇒
    **退出码 2**，错误文案**必须保留「不一致」子串**（`tests/contract/test_billing_contracts.py:1290`
    的调用点；断言在其下一行，同一用例）。
  - `tiers` 的档位行渲染改 `cfg.tiers_of(channel_id)`（`ops/billing.py:119`）；`calibrate` 的缺档判定改
    `cfg.tier_of(channel_id, args.tier)`（`ops/billing.py:172`，错误文案保留环节名）。
  - 新增只读子命令 `uv run python ops/billing.py channels [--config configs/*.yaml]`：声明渠道集合 →
    每渠道 `adapter`（装配入口）归属 → 每渠道 `tiers` 键集/余量/拒绝计数 → **凭证就绪矩阵**（`set`/`length`，
    **绝不回显值**，见 C16）→ 每渠道账本/告警路径。退出码 0；缺项或不一致 ⇒ 2。

### 机检断言

- 多渠道并存：`llm` 与 `media` 的 `tiers` / `ledger.json` / `alerts.jsonl` / `runs/` 各自独立；
  `media` 的档位名不出现在 `billing/llm/ledger.json` 的 `tiers` 键里，反之亦然（**跨渠道串用恒 0**）。
- 声明渠道 ⇒ 退出码 0（`tests/contract/test_billing_contracts.py:1144` 保留，**且在多渠道配置下也须成立**
  ——这正是"放开单渠道硬拒绝"的验收）；未声明渠道 ⇒ 退出码 2（`:1146` 保留）。
- 装配期缺项即拒绝：缺 `tiers` / 缺 `adapter` / 按 adapter id 解析不到或解析出多个渠道 ⇒
  `BudgetConfigError`（零落树、零扣费），不是等到第一次调用才炸（镜像 019 的装配期拒绝）。
- **movie 与短剧两条装配路径各自跑通**：movie（单渠道 + 默认模拟）与短剧（`llm` + `media`，投放面
  默认模拟）都能装配；movie 上不因未登记投放渠道而失败（见 C11）。
- `core/` 与 `agents/` 内**不得**出现渠道 id 字面量（`channel_id == "…"` 形态，纯度断言常驻）。
- **形态字面量守卫面的落差与补齐（本契约的判断项）**：既有守卫
  `tests/unit/test_form_switch.py:309` 的**形态字面量**扫描面**显式排除 `agents/pilot`**
  （`:310-312`：`if "pilot" not in path.parts`——该层允许**读**形态值），而形态**判断**分支扫描面
  （`:319-324`）是**全覆盖**的（含 `agents/pilot`）。本特性恰在 `agents/pilot/backends.py` **新增渠道
  解析**，故登记：该处的渠道/adapter 解析**不得**出现形态字面量、也不得出现形态判断（形态差异只经配置）
  ——"读形态值"的既有例外**不放宽**为"按形态选择渠道或适配器"。**若既有守卫面不覆盖该文件，则由本特性
  新增一条断言补齐**（`agents/pilot/backends.py` 的渠道解析路径不含形态字面量与形态判断）；
  **不得删改既有 `:309` 的扫描口径**（排除面是为"配置读取"留的，动它就是削弱既有断言）。
  该补齐**不新增分支代码**（原则五）。

### 反例

1. 两渠道都用 `screenplay` 档：`media` 的申请不得吃掉 `llm` 的余量（余量各自独立，串用即红）。
2. `--channel media --config` 某未登记 media 的配置 ⇒ 退出码 2，**不得**回落到 llm 的额度。
3. `adapter: promo` 同时声明在两个渠道上 ⇒ 装配报错（不猜用哪个账本）。
4. 保留 019 的 `sole_channel()` 并在多渠道配置上"取第一个" ⇒ 报错（歧义即报错），不得静默通过。
5. 在 `agents/promo/loop.py` 里再写一份门禁调用（绕过 `RecordingChannelCall`）⇒ 静态断言红。

### 兼容规则（对 019 零回改）

- `assemble_guard(config_path)` 单渠道调用**保留可用**；跨渠道装配由装配点先 `channel_for_adapter(cfg, …)`
  解析、再显式传 `channel_id=`。
- `--channel` 的三态语义（声明值 0 / 未声明值 2 / 用法错误 2）**逐字保留**；
  `.github/workflows/billing_alerts.yml:27` **一字不改**（见 C13 表）。
- 产物目录名 = 渠道 id ⇒ 历史单渠道目录（`billing/llm/`）逐字节可用：**零迁移、零回改**。
- `ops/billing.py` 的 docstring「子命令……**七条齐备**」（`ops/billing.py:5`）与 019 quickstart 的
  "七子命令"措辞随新增 `channels` 同步为八条——**属文档同步，不是断言削弱**
  （`tests/contract/test_billing_contracts.py:1129-1140` 对七条既有子命令逐条 `--help` 断言退出 0 的
  用例一字不改，新增第 8 条不破坏它）。

## C13 019 既有断言不削弱清单（逐个 `--channel` 调用点 + 每日工作流）

**目的**：把"既有断言按扩展更新、**不削弱**"从叙述变成**可核对的清单**——逐个 `--channel` 调用点与
每日工作流给出"保留 / 更新（怎么更新）"结论（裁决 F；SC-004）。

### C13.1 逐个 `--channel` 调用点（穷举）+ 每日工作流

穷举面：`tests/contract/test_billing_contracts.py` 内**全部**含 `--channel` 的调用点（**12 处**，逐行穷举
见表 1~12 行）+ `.github/workflows/billing_alerts.yml:27` 的每日只读告警调用 = **合计 13 个 `--channel`
实参**。口径：**保留 = 断言与退出码一字不改**；**更新 = 断言强度不变、只改实现点或文案子串**。
**本表零"删除"结论**。

| # | 位置 | 现状期望（本仓已实跑全绿，见下"覆盖用例"） | 结论 |
| --- | --- | --- | --- |
| 1 | `tests/contract/test_billing_contracts.py:995`（`import-bill --channel <声明渠道>`） | 退出码 0，账单落 `billing/{channel}/bills/` | **保留** |
| 2 | `:1013`（`reconcile --channel <声明渠道>`） | `unexplained` 非空且退出码 ≠ 0（有告警） | **保留** |
| 3 | `:1037`（`alert-check --channel <声明渠道>`） | 退出码 1 且 `has_alerts is True`（报告被改写后仍告警） | **保留** |
| 4 | `:1048`（`reconcile --channel <声明渠道> --bill-id ghost`） | 退出码 1 且错误含「拒绝产出」；不产"零差异"报告 | **保留** |
| 5 | `:1144`（`tiers --channel <声明渠道>`） | 退出码 0 | **保留**（且须在**多渠道**配置下也 0：`sole_channel()` 硬拒绝退役的验收） |
| 6 | `:1146`（`tiers --channel nope`） | **退出码 2**（未声明的渠道不得开工） | **保留（硬要求，不得改）**；实现点 = `_channel_of` 改按声明集判定 |
| 7 | `:1149`（`runs --channel <声明渠道> --end …`） | 退出码 1 且 `meets is False`（未达标如实报缺口） | **保留** |
| 8 | `:1155`（`raise-tier --channel <声明渠道> --calibration ghost`） | 退出码 1 且 `reason == "uncalibrated_raise"`、配置未被改写 | **保留** |
| 9 | `:1216`（`import-bill --channel <声明渠道>`，篡改用例前置） | 退出码 0 | **保留** |
| 10 | `:1238`（`reconcile --channel <声明渠道>`，账单被篡改） | 退出码 1 且错误含「拒绝产出」 | **保留** |
| 11 | `:1274`（`calibrate --channel <声明渠道> --tier ghost`） | 退出码 2 且错误含 `ghost` | **更新（最小）**：实现点改 `cfg.tier_of(channel_id, args.tier)`；文案**必须保留 `ghost`**，退出码仍 2 |
| 12 | `:1290`（`tiers --channel ghost`） | 退出码 2 且错误含「**不一致**」 | **更新（最小）**：实现点改按声明集判定；文案**必须保留「不一致」子串**，退出码仍 2；语义（未声明渠道不得开工）逐字保留 |
| 13 | `.github/workflows/billing_alerts.yml:27`（`alert-check --channel llm --config configs/movie.yaml`） | 每日只读告警：0 无告警 / 1 有告警 / 2 用法或配置错误；冷启动（无报告）放行 | **保留、文件一字不改**（`llm` 仍 ∈ movie 声明集）。前提：若 movie 的 LLM 渠道改名，必须同步改该行，否则每日非零退出即误告警 |

**覆盖用例**（本仓已实跑全绿：`uv run pytest tests/contract/test_billing_contracts.py -q` → **41 passed**；
行 1~4 由 `TestC14告警门禁::{test_报告含未解释项却返回0即红, test_无账单不得产零差异报告}` 覆盖、
行 5~8 由 `TestC16CLI退出码::test_七子命令的退出码语义` 覆盖、行 9~10 由
`Test对抗与篡改面::test_改写账单后再对账即拒绝` 覆盖、行 11~12 由
`Test对抗与篡改面::test_缺档与渠道不符即配置错误` 覆盖）——上表每行的"现状期望"都是**已核实的当前行为**。

### C13.2 附加落点（非 `--channel` 面，同一扩展的必改清单；断言强度逐条保留）

| 位置 | 现状 | 结论与怎么改 |
| --- | --- | --- |
| `tests/contract/test_billing_contracts.py:608` | `_section(form)["budget"]["tiers"]` 取键集 | **更新**：改取各渠道 `channels.<id>.tiers` 的键并集（调用点 `stage=` 仍须 ∈ 声明键集，计数仍 8） |
| `tests/contract/test_billing_contracts.py:603` | `payload["budget"]["tiers"]["screenplay"].pop("limit_usd")` | **更新**：改 nested 路径（缺 `limit_usd` 仍须报错，文案仍含 `limit_usd`） |
| `tests/contract/test_billing_contracts.py:1265`、`:146` | `cfg.channel(_channel(cfg)).adapter` 非空；`_channel(cfg) = next(iter(cfg.channels))` | **保留**（`adapter` 键仍在；声明顺序约定下首个渠道仍是 `llm`，见 C11） |
| `tests/unit/test_billing_config.py:43`、`:72`、`:80`、`:88`、`:95`、`:102`、`:140`（`section["tiers"]…`） | 从真实 movie 段派生后改档位 | **更新**：改 `section["channels"]["llm"]["tiers"]`（`llm` = LLM 渠道的装配引用）；逐条的取值域/缺项断言强度不变 |
| `tests/unit/test_billing_config.py:45`、`:47`、`:143`、`:149` | `cfg.tiers` / `cfg.tier("…")` | **更新**：改 `cfg.tiers_of(<llm 渠道>)` / `cfg.tier_of(<llm 渠道>, "…")`；文案**必须保留「未在 budget.tiers 声明」** |
| `tests/unit/test_billing_config.py:156`、`:160`、`:166`、`:182` | 两形态键集一致 / 逐档额度更小 / 窗口更短 / `payload["budget"]["tiers"]` | **更新**：movie 侧单渠道可用兼容视图、短剧侧按渠道取档（两侧都取 LLM 渠道），四条断言强度逐条保留；`:182` 改 nested 键 |
| `tests/unit/test_billing_snapshot_freeze.py:72-73` | `set(snapshot["tiers"]) == set(cfg.tiers)` | **更新**：两侧都改按渠道取档（快照只含本渠道档位，这是 C12 的加固） |
| `tests/unit/test_billing_ledger_concurrency.py:215` | `cfg.tiers.items()` | **更新**：改按渠道取档 |
| `tests/conftest.py:4109-4135`（`_billing_budget_payload`） | 按 `payload["tiers"]` 压额度/窗口 | **更新（夹具口径不变）**：见 C11 的夹具兼容规则——既有用例体零改动 |
| `agents/pilot/pilot.py:446-448` | `cfg.tiers` 非空校验 + 逐档 `limit_usd` | **更新**：改 `cfg.tiers_of(channel_for_adapter(cfg, "pilot_llm").channel_id)`；「预算不可用：budget.tiers 为空」文案保留 |
| `ops/billing.py:119`、`:172` | `cfg.tiers.items()`、`cfg.tier(...)` | **更新**：改 `cfg.tiers_of(channel_id)` / `cfg.tier_of(channel_id, …)`（同 C12） |
| `ops/demo_billing.py:126`、`:128`、`:839` | `assembly.cfg.tiers` | **更新**：按 `assembly.channel_id` 取档（离线六步的语义与退出码 0 不变） |

**结论**：**零删断言、零放宽**；改动集中在实现点与夹具（单渠道夹具走兼容视图 ⇒ 绝大多数用例体不动），
新增的多渠道路径由新增单测与新增契约用例覆盖。

### 机检断言

- 上表 C13.1 / C13.2 的每一行都是**常驻用例**（更新面见"结论与怎么改"列），不得以删断言换取通过。
- 一次性核对口径：`uv run pytest tests/contract/test_billing_contracts.py -q` 全绿（本仓当前 **41 passed**）
  且用例数**不减少**。
- `.github/workflows/billing_alerts.yml:27` 在 CI/定时执行中继续跑（本机不入定时面，属 CI 常驻）。

### 反例

1. 为让多渠道配置通过而删掉 `--channel nope ⇒ 2` 的断言 ⇒ 红（本表 6 行为硬要求）。
2. 把 `tier_of` 的缺档错误文案改成不含环节名 ⇒ `tests/contract/test_billing_contracts.py:1274` 断言红
   （文案是契约的一部分）。
3. 把 `_channel_of` 的未声明文案改成不含「不一致」⇒ `tests/contract/test_billing_contracts.py:1290`
   的用例红（同一用例下断言错误文案）。
4. 顺手把 `billing_alerts.yml` 的 `--channel llm` 改成别的取值以"适配新形状" ⇒ 红（未声明的渠道
   会让每日告警非零退出，且该文件本应一字不改）。

### 兼容规则（对 019 零回改）

- 019 的契约文件（`specs/019-real-channel-billing/contracts/*.md`）与既有用例**不被改写语义**：
  本契约只**按扩展更新实现点与夹具**，不改它们的结论面。
- 每日工作流的调用行、退出码语义与"冷启动放行"口径**逐字不变**。
- 既有 019 产物（账本/账单/报告/校准/运行记录）与 019 quickstart 的命令面**继续有效**。

## C14 投放调用受同一门禁 + 与 015 进程内按轮上限的分辨

**目的**：让 C 路径的投放调用**确定地**受 019 门禁约束（调用前拒绝、平台 0 次调用、零入账），并明确它与
015 既有的**进程内按轮上限**的关系——**共同生效、口径可区分、不得混同、不得互相替代**（FR-008；
裁决 F 的显式要求；边界情况「与既有按轮上限的关系」）。

| 维度 | 015 进程内按轮上限 | 019 跨进程文件账本（本特性接入投放渠道的门禁） |
| --- | --- | --- |
| 实现 | `agents/promo/config.py:70-72`（`budget_cap_usd = exploration_per_round_usd × promo_pilot_ratio`）+ 前置校验 `agents/promo/loop.py:400-421`；花费读运营表 `agents/promo/loop.py:240-249` | `core/billing/budget.py`（`FileLedger` + `SpendGuard`）+ 厂商账单口径 `core/billing/{bill,reconcile}.py` |
| 量纲 | **进程内、按轮、估算口径**（不落文件账本、不产账单对账） | **跨进程、按环节档 + 渠道分派、文件账本口径**（原则三要求的那一条） |
| 拒绝文案 | 「预算门禁：已耗 … + 申请 … > 上限 …（拒投）」（`agents/promo/loop.py:404-409`） | 「预算拒绝（`over_limit`）：环节 … 预估价 … / 余量 …」（`core/billing/budget.py:920-927`） |

- **共同生效**：投放调用先过 019 前置门禁（`RecordingChannelCall` 内 `guard.check`，**调用前**判定），
  再过 015 的按轮上限（既有循环内校验**一行不动**）；任一处拒绝 ⇒ **都不得申请、不得入账**
  （零成本分支，019 C10）；**拒绝理由必须点名命中的是哪一条**（两条文案如上，可分辨）。
- **不得互相替代**：不得把 015 的按轮上限当"原则三的门禁"（它不进文件账本、不产账单对账）；也不得因为
  019 门禁在位就删 015 的按轮上限（它守的是"单轮探索 ≤ 总预算 2%"的多样性纪律）。机检：
  `agents/promo/loop.py` 仍含按轮上限校验；`_promo` 真实分支必经 `guard.check`。
- **拒绝 ≠ 记账**：被拒的投放**不申请**（平台调用 0 次）、**不记成本**、不排队、不降级为模拟；
  同一轮**已发生**的花费照记（`promo_campaigns.spent_usd` 既有口径不动）；原因落
  `billing/{channel}/alerts.jsonl`（`kind=budget_refused`）+ 账本 `refusals` 计数（019 C10 口径）。
- **实测超预估**：`reservation.settle(actual_spent)` 使余量 < 0 ⇒ 如实入账 + `over_limit` 告警 +
  后续调用被拒（不回滚、不改写、不静默清零，019 C11 口径）。
- **分型互斥保留**（019 C10）：`budget_refused`（本系统门禁）≠ 认证失败（厂商 401/403）≠ 配额限流（429），
  三类各有独立错误类型与告警 `kind`，使对账可归因（否则差异无法分类）。
- 渠道失败**禁止**静默回落模拟并照常计费（诚实分层）：见 C18（本处只要求"投放面同样适用"）。

### 机检断言

- 超限投放：平台调用 **0** 次、成本入账 **0**、`alerts.jsonl` 有 `budget_refused` 行、原因点名环节与额度。
- 未声明投放环节（缺 `stage` / 环节不在该渠道 `tiers` 键集）⇒ 拒绝 `tier_undeclared`，同样 0 次调用零入账。
- 两腿一致性：同一次超预算投放被 019 门禁拒绝后，015 的按轮上限**不重复记账**（`spent_usd` 不变），
  "花了的钱"与"没花的钱"在节点、运营表与告警里都可分辨（019 C10 调用点分支规则的投放版）。
- 静态：`core/billing/runlog.py` 的 `RecordingChannelCall` 是投放面**唯一**的调用包装点
  （不得在 `agents/promo/loop.py` 再写一份门禁调用）。

### 反例

1. 只在 `agents/promo/loop.py:400-421` 校验就发起 `create_campaign` ⇒ 红（跨进程账本腿缺失）。
2. 只在 019 门禁校验、删掉按轮上限 ⇒ 红（015 的多样性纪律被拆）。
3. 门禁拒绝后仍照记 `estimated_usd` 顶替花费 ⇒ 红（零入账分支）。
4. 真实渠道 401 后回落模拟并照常计费 ⇒ 红（与 C18 的诚实分层联合机检）。
5. 把 `budget_refused` 与厂商 429 合并成同一 `kind` ⇒ 红（对账无法归因）。

### 兼容规则（对 019 / 015 零回改）

- 019 的 `SpendGuard` / `FileLedger` / `AlertLog` / 告警 `kind` 取值域、账本文件结构与拒绝语义
  **逐字不变**；本特性只增加"按渠道取档"与"投放面包装"两处接入。
- 015 的 `promo` 段的 `exploration_per_round_usd` / `promo_pilot_ratio` 语义与 `budget_cap_usd` 口径、
  `promo_campaigns` 表形状、`agents/promo/loop.py` 的按轮上限校验**一行不改**。
- 投放渠道的额度档与账本目录是**新增**（`billing/media/…`），既有 `billing/llm/…` 零回改。
