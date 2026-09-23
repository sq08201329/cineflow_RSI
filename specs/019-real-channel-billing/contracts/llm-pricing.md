# 契约：两维价目（峰谷 × 厂商缓存命中）与快照语义

> 对应规格 FR-010/011、SC-003、US3 场景 1~3/5、澄清第 3 条。实现：`core/llm_gateway/profiles.py`
> （价目解析 + 快照）、`gateway.py`（取价 + 折算）、`backends/http.py`（命中 token 来源）。
> 现状基线：价目恰好两键（`profiles.py:51` `_PRICE_KEYS`）、硬编码线性式（`:92-96`）、
> 快照按两键固定（`:98-112`）。

## C5 两维价目数据模型 + 兼容规则

```yaml
prices:        {prompt_per_1k, completion_per_1k}          # 基础两键（保留不变）
price_matrix:                                              # 可选；声明即四格齐备
  peak_miss / peak_hit / off_peak_miss / off_peak_hit: {prompt_per_1k, completion_per_1k}
```

- 格位键 = `<峰谷>_<缓存>`；**四格全必需**（缺任一格、任一格缺键 ⇒ `ProfileConfigError`），
  每格取值校验沿用 `_parse_prices`（`profiles.py:260-292`：≥ 0、非 bool、缺项不回落默认价）。
- **兼容规则**：未声明 `price_matrix` ⇒ 四格皆取 `prices`（既有配置零改动、行为逐字节不变），
  报告/快照口径备注记「未区分峰谷/缓存」；声明后**不得**回落基础价（缺格即报错，不静默取 `prices`）。
- 单点取价：`price_cell(profile, *, moment, cache_hit) -> (cell_key, prices)`；网关折算（`gateway.py:311-314`）
  与预算估算（C10）**同取该函数**（"估算与折算同源"，`gateway.py:144-159` 遗留 1 收敛点的延伸）。
- **第二条折算路径必须委派**：`ModelProfile.cost_usd(prompt_tokens=…, completion_tokens=…)`
  （`profiles.py:92-96`）今天是一条独立的硬编码线性式——与网关折算**两套口径**，改两维价目后会与
  `price_cell` 脱钩。本特性把它改为**委派 `price_cell` 的实现**（函数签名与返回语义不变、调用方零改动），
  保证全仓**只有一份**折算口径。
- 零价目纪律沿用：四格全 0 仍须显式 `zero_marginal: true`（`profiles.py:279-291`）；**单格为 0 合法**
  （如厂商不对命中计费须以校准记录支撑，规格边界情况：命中档位不得默认按 0）。
- `cached_prompt_tokens`（C7）与本地缓存标记**正交**：网关本地内容哈希命中零成本、无后端调用、
  不占额、不进任何格位（`gateway.py:283-294`）；两维价目的缓存维度**只**指厂商 prompt 缓存。

### 场景

1. 同一 prompt 在四格（峰/谷 × 命中/未命中）下取价正确，`cost_usd` 分别等于格位价目折算值
2. 缺一格 / 某格缺 `completion_per_1k` ⇒ 装配报错；未声明 `price_matrix` 的既有档案取价与改造前逐字节一致
3. 全 0 四格未声明 `zero_marginal` ⇒ 报错；单格 0 合法且进报告口径备注

## C6 峰谷时段 + 时区 + 归属口径

- `budget.peak_windows` 三键必填：`timezone`（IANA 名）、`windows`（峰时区间列表，支持跨夜
  `start > end`，空列表 = 全谷时**显式声明**）、`attribution`（**取值域单元素 `call_start`**；
  其它取值报错 ⇒ 不静默换口径）。
- `is_peak(moment, cfg)` 按调用**开始时刻**在其本地时区判定；区间口径 = `[start, end)`（闭开，写明，
  免"恰好落在边界"含糊）；一次调用**只取一个格位**（跨切换时刻**不拆分**、不按 token 比例摊分）。
- 归属口径**必须**在三处可见：① 差异报告与运行记录的口径备注；② 快照
  `peak_windows_snapshot{timezone, attribution, windows}`（随 `config_snapshot["budget_tiers"]` 冻结，C9）；
  ③ 校准记录 `note`。
- 日历单点：额度时间窗（`window.kind == day`）与运行记录 `{date}` 同用 `peak_windows.timezone`，
  不得各用一套（UTC/本地混用即口径分叉）。
- **登记边界**：厂商若按 token 计费时刻或账单周期摊分峰谷（与 `call_start` 不同），属**口径校准**内容，
  由校准记录登记偏差，不在本特性做双口径换算。

### 场景

1. 跨峰谷切换时刻的调用按其**开始时刻**取档，报告口径备注写明 `attribution=call_start` 与 `[start,end)` 口径
2. 缺 `timezone` / 缺 `attribution` / `attribution` 取值非法 ⇒ 装配报错
3. 跨夜窗口（如 `08:30–00:30`）在 `23:00` 判峰时、`01:00` 判谷时；边界时刻按闭开区间裁定

## C7 缓存维度的来源（厂商响应）

- `BackendResult`（`gateway.py:38-52`）**末位追加**可选字段 `cached_prompt_tokens: int | None = None`：
  既有后端实现与测试构造**零变化**（默认 None）；`LLMResult` 如实透传（命中 token 数、所用格位键）。
- 来源：厂商响应 usage 的命中 token 计数，由 `backends/http.py` 读取（协议字段；`core/llm_gateway/` 是
  `tests/unit/test_no_vendor_literals.py:21-24` 显式排除的协议实现区）。**不新增档案配置键**（字段名属
  协议面，随厂商差异在 `backends/` 内调整，属"协议校准"）；读取失败/缺失字段 ⇒ `None`。
- **保守记账**：有值且 ≥ 0 ⇒ `*_hit` 格；缺失/`None` ⇒ `*_miss` 格，并在报告与校准记录登记
  「厂商未报告命中 token（按未命中计）」；`cached_prompt_tokens > prompt_tokens` ⇒ 报错（不静默钳制）。
- 命中档位折扣口径（是否计费、折扣多少）属**口径校准**内容：校准记录登记实测偏差；未校准时
  命中档**不得**默认为 0（规格边界情况）。

### 场景

1. 后端报命中 token → 取 `*_hit` 格、报告标注命中数；后端不报 → 取 `*_miss` 格 + 口径备注（不按 0 计）
2. 命中数 > prompt token 数 → 报错；旧后端实现（不设该字段）与既有单测逐条不变

## C8 快照形状升级 + 旧语义 + 改价不漂移

- `ModelProfile.to_snapshot()`（`profiles.py:98-112`）新增**可选**键 `price_matrix` 与 `declared_dimensions`
  （峰谷时区/窗口/归属口径随 `config_snapshot["budget_tiers"]` 冻结，见 C6/C9——**不重复进档案快照**，
  避免档案快照依赖 `budget:` 段）；`prices` 与既有键逐字保留、**仍不含密钥**；
  `ProfileSnapshot.fingerprint/ref`（`:131-138`）自然覆盖新键（改矩阵 ⇒ 指纹变 ⇒ 路由引用可追溯）。
- **旧快照原语义**：历史节点 `config_snapshot["llm_profiles"]` 中无 `price_matrix` 键的条目 ⇒ 四格同价
  （等价"未声明"）；**冻结快照永不重写**，读取端同时支持新旧两形状（按 `price_matrix` 是否在键集中分派，
  不按版本号猜）。
- **改价不漂移（原则一 / SC-003）**：历史节点成本按冻结快照复算，配置改价（含改 `price_matrix`
  与 `peak_windows`）后**逐字节不变**；新节点用新价目 → 断言四件套（覆盖断言清单，全部常驻）：
  ① 改配置后历史复算逐字节不变；② 新旧形状读取等价（无 matrix 快照 vs 四格同价快照取价一致）；
  ③ 缺格装配报错（同 C5）；④ 快照含 matrix 且无密钥、指纹随 matrix 变化。
- 落地位置：`tests/unit/test_billing_price_matrix.py`（新增）+ `tests/contract/test_billing_contracts.py`；
  既有 `tests/contract/test_llm_profile_contracts.py` / `tests/unit/*llm_profile*` 全绿不降。

### 场景

1. 改配置四格价目后按历史节点 `config_snapshot` 复算 → 逐字节不变；新节点按新价目取价
2. 旧快照（无 `price_matrix`）读取取基础价、报告记「未区分峰谷/缓存」，且文件**未被改写**（mtime/字节不变）
3. 快照含 `price_matrix` 与 `declared_dimensions`，无任何密钥字段；改矩阵 ⇒ 指纹变化
