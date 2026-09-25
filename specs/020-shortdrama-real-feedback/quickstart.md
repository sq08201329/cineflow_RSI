# Quickstart：短剧线真实投放与日级回流（020-shortdrama-real-feedback）

本文给出**可复制执行**的验证序列与期望结果。全部命令 **离线、零真实花费、零外部网络、零凭证**
（Mock 平台 + 夹具账单/指标）；真实投放与真实回流属**运营侧墙钟 + 凭证**前提，机制已就绪、真实回流待运营
（契约 C18）。命令与仓库既有工具链一致（`uv run …`，形态配置用 `configs/shortdrama.yaml` /
`configs/movie.yaml`）。

**当前状态如实标注**：本特性处于 spec/契约阶段，实现（`core/billing/budget.py` 的渠道命名空间、
`core/calibration/transfer.py`、`ops/demo_shortdrama_feedback.py`、新增测试文件）**尚未落地**——
下方 A 组命令今天即可跑（019/010 既有面），B 组命令在实现落地后才可通过；文末"验证记录"只回填
**已实跑**的结果，不预填未跑的结论。

## A. 现在即可跑（既有面，本文已实跑）

```bash
uv sync                                                     # 依赖与 uv.lock 一致（无新增依赖）
uv run ruff check .                                         # 静态检查
uv run ruff format --check .                                # 格式检查
uv run pytest tests/unit -k billing -q                      # 019 单元面：配置/门禁/账本/价目/账单/对账/运行记录/CLI/纯度
uv run pytest tests/contract/test_billing_contracts.py -q   # 019 契约面 C1~C16（含 --channel 全部调用点）
uv run pytest tests/contract/test_calibration_contracts.py -q  # 010 契约面 C1~C9（含"历史节点逐字节一致"机检）
uv run pytest tests/unit/test_form_switch.py -q             # 两形态差异（含 test_外环日级，本特性将扩展为"运转"断言）
uv run python ops/billing.py tiers     --channel llm --config configs/movie.yaml   # 退出码 0：余量/拒绝计数/未结算预留
uv run python ops/billing.py tiers     --channel nope --config configs/movie.yaml  # 退出码 2：未声明渠道不得开工（保留）
uv run python ops/billing.py runs      --channel llm --window-days 7 --config configs/movie.yaml
                                                            # 退出码 1：无运行记录 ⇒ 如实报缺口，不判通过
uv run python ops/billing.py alert-check --channel llm --config configs/movie.yaml # 退出码 0/1：每日只读告警门禁
uv run python ops/demo_billing.py                           # 019 离线六步演示：退出码 0
uv run python ops/demo_calibration.py                       # 010 离线校准演示：退出码 0
```

## B. 本特性新增面（实现落地后可跑）

```bash
# B1 单测子集（新增文件；实现落地后存在）
uv run pytest tests/unit/test_billing_channels.py -q        # C11~C14：渠道命名空间形状与兼容读/按渠道分派/019 断言清单/两腿分辨
uv run pytest tests/unit/test_calibration_transfer.py -q    # C15：迁移件形状/可比性判定/append-only/零权重键
uv run pytest tests/unit/test_form_switch.py -q             # 两形态切换机检（含"外环日级"运转断言，非配置数字断言）

# B2 契约子集（在既有契约文件上按扩展更新，不新增文件）
uv run pytest tests/contract/test_billing_contracts.py -q   # 含 C13 清单里逐条 --channel 调用点
uv run pytest tests/contract/test_calibration_contracts.py -q  # 含迁移后"历史节点逐字节一致"机检

# B3 离线端到端演示（唯一入口；七步全 ok ⇒ 退出码 0）
uv run python ops/demo_shortdrama_feedback.py

# B4 两形态切换与渠道分派（对照执行，看输出差异全在配置取值）
uv run python ops/billing.py channels --config configs/shortdrama.yaml   # 渠道集合 llm+media、各渠道 adapter、凭证矩阵、账本路径
uv run python ops/billing.py channels --config configs/movie.yaml        # 渠道集合 llm（**movie 不登记投放渠道**；该路径不因投放渠道失败）
uv run python ops/billing.py tiers --channel media --config configs/shortdrama.yaml   # 投放渠道档位余量（与 llm 各自独立）

# B5 迁移面（独立脚本 ops/transfer.py，四个子命令；离线：只读既有台账/快照/报告/漂移产物）
uv run python ops/transfer.py transfer         --data-dir calibration \
    --from configs/shortdrama.yaml --to configs/movie.yaml \
    --evaluator <id@version> --period <周期> --dry-run     # 零落盘预览：逐条可比性条件 + 判定
uv run python ops/transfer.py transfer-confirm --data-dir calibration \
    --transfer <id> --by <人> --reason <理由>              # 人工两键之一：采纳（**只采纳结论，不改权重**）
uv run python ops/transfer.py transfer-shelve  --data-dir calibration \
    --transfer <id> --by <人> --reason <理由>              # 人工两键之一：搁置（零变更；不可迁移件只能走这里）
uv run python ops/transfer.py transfer-report  --data-dir calibration
                                                            # 无可迁移结论时如实标注「无可迁移结论（来源缺失）」

# B6 投放渠道账单对账（复用 019 的 bill / reconcile 面；不联网、零真实花费、零凭证）
uv run python ops/billing.py import-bill --channel media --file <账单文件> --bill-id <批次> \
        --period <周期> --config configs/shortdrama.yaml     # 退出码 0；**批次幂等**：同批次重复导入 ⇒ 1（零部分导入）
uv run python ops/billing.py reconcile --channel media --period <周期> --bill-id <批次> \
        --gateway-report <网关账目 JSON> --config configs/shortdrama.yaml
                                                            # 退出码 0 无告警 / 1 有告警（未解释项或超阈值）
uv run python ops/billing.py alert-check --channel media --config configs/shortdrama.yaml
                                                            # 退出码 0/1：只读门禁（报告未解释项 + alerts.jsonl 增量）
```

**命令行示例约定**：`<>` 包裹的取值（如 `<账单文件>`、`<批次>`、`<周期>`、`<id@version>`、`<id>`、`<人>`、
`<理由>`、`<网关账目 JSON>`）一律是**示例参数**，须替换为实际取值——它们**不是**配置取值，配置文件里
（`configs/*.yaml`）**不得**出现 `<>` 占位（配置写数值/字符串，缺项即报错）。

**渠道 `adapter` 的权威定名（B4 的输出按此核对）**：LLM 渠道沿用既有 `pilot_llm`（**不重命名**）；
**本特性投放渠道的 `adapter` 取值 = `promo_platform`**（以**配置为权威**；代码侧传出的 adapter id 必须能在
配置声明里**逐字找到**）。故 B4 两行的期望输出：`--config configs/shortdrama.yaml` ⇒ 渠道 `llm`
（`adapter = pilot_llm`）+ `media`（`adapter = promo_platform`）；`--config configs/movie.yaml` ⇒ 仅 `llm`
（`adapter = pilot_llm`，**不登记**投放渠道）。输出里若出现别的 adapter 取值，即配置或装配点被改动，
应先核对配置（不得在代码里另起名字）。

**B6 的期望结果（与 019 既有 LLM 渠道口径**逐条对齐**，`--channel media` 只是换渠道）**：
① 报告逐项引用**账单批次**（`bill_refs[]` 含 `bill_id` + `source`；网关/内部记账**不得**作"成本已核实"的
唯一依据）；② 每条差异带**六类之一**的分类（`计费口径` / `未入账` / `时序错位` / `免费额度与折扣` /
`币种汇率` / `未结账`）+ `delta_usd` 实测偏差 + 口径备注；③ **无分类 ⇒ `unclassified` ⇒ 不可解释 ⇒ 告警
100%**（`unexplained_delta`）；④ 超 `budget.reconcile.alert_threshold_usd` ⇒ `delta_over_threshold` 告警；
⑤ 未识别账单格式 ⇒ 报错且**零落盘**；同批次重复导入 ⇒ 拒绝（幂等）；⑥ **无账单批次即拒绝产出**（不产
"零差异"报告）；⑦ `alert-check` 有告警 ⇒ 退出码 1（每日只读门禁，同 `billing_alerts.yml` 的口径）。

## 端到端场景（demo 七步，逐条对应契约）

1. **两形态配置形状与缺项拒绝**（C11 形状 + C13 清单 / FR-014）：新增键的 **schema 齐备**——两形态都能
   加载 `budget.channels.<id>.tiers`（非空 `tiers` + 非空 `adapter`）与
   `calibration.transfer.{basis,source_forms,target_forms,conditions,storage,adoption}`；删任一项 ⇒
   装配/加载报错（不取码内默认）。**两形态都必须能装配通过**，movie 路径不因投放渠道或其凭证而失败。
2. **渠道分派**（C12 / FR-007）：短剧态 `llm` 与 `media` 的档位、账本、告警、运行记录互不可见；
   同一档位名跨渠道串用 **0** 次；旧扁平 `tiers` 的配置仍可读并**显式归一**（多渠道下歧义即报错）；
   movie 态单渠道（`adapter` = `pilot_llm`）可正常装配与取档。
3. **凭证矩阵与装配期拒绝**（C16 / FR-011）：声明真实而 `PROMO_PLATFORM_*` 缺失 ⇒ 装配期拒绝启动、
   点名缺哪个变量、零落树零扣费、**不回落模拟**。
4. **最小规模先行**（C16 / FR-010）：Mock 平台 + 夹具账单跑最小规模档（= 投放环节档位 `limit_usd`）→
   校准记录（append-only）→ 未校准/过期/样本不足的扩量请求被拒并留痕，配置未被改写；投放渠道的
   最小规模入口是**投放侧**的（`ops/billing.py calibrate --channel media --from-records`），
   **不假定** `ops/smoke_llm.py` 的 LLM 冒烟口径。
5. **投放调用受同一门禁**（C12 接线 / C14 两腿分辨 / FR-008）：超限申请 **调用前拒绝**、平台调用 **0** 次、成本 **0** 入账；
   015 的进程内按轮上限与 019 的跨进程文件账本**两条腿口径可分辨**。
6. **迁移件**（C15 / FR-012）：可迁移与不可迁移各一（不可迁移**逐条**记原因）；采纳走人工两键；
   既有节点 `eval_breakdown` 与得分**逐字节一致**（权重零改动）。
7. **诚实分层**（C18 / FR-013）：模拟来源不计入真实覆盖；`source` 逐条可辨；窗口断档**逐段**报出；
   结论写「**机制已就绪 / 真实回流待运营**」。

## 如何看产物（离线跑完 demo 后逐件核对）

| 产物 | 路径 | 看什么 |
| --- | --- | --- |
| 额度账本 | `billing/{channel}/ledger.json` | 各档余量/已用/未结算预留/拒绝计数；`revision` 单调；两渠道各一本 |
| 告警留痕 | `billing/{channel}/alerts.jsonl` | `kind` 六值：`budget_refused` / `over_limit` / `unexplained_delta` / `delta_over_threshold` / `tier_raised` / `uncalibrated_raise` |
| 运行记录 | `billing/{channel}/runs/{date}.json` | 每条 entry 的 `stage` / `source`（`real`/`simulated`/`fallback`）/ `result` / 链式 `head_digest`；`sealed` 后拒绝追加 |
| 账单与对账报告 | `billing/{channel}/bills/{bill_id}.json`、`billing/{channel}/reports/{period}.json` | 六类差异逐项分类 + `delta_usd` + 口径备注；未解释项 100% 告警；报告引用账单批次 |
| 校准记录 | `billing/{channel}/calibrations/{id}.json` | 配置价目快照 / 实测花费 / 偏差 / 口径备注 / 样本量 / 时间；append-only |
| 校准台账 | `calibration/ledger/{agent_id}/{evaluator_id}.jsonl` | 010 每轮每评估器一行（append-only），迁移的**来源件** |
| 锚点分布快照 | `calibration/snapshots/{agent_id}/{evaluator_id}/{period}.json` | 该周期锚点分布（周期标签由 cadence 派生：日级 ⇒ 日期、周级 ⇒ ISO 周） |
| 信度报告 | `calibration/reports/{period}.json` | 每 agent 每评估器的相关系数与样本量；负相关只告警 |
| 漂移产物 | `calibration/drift/metrics/{agent}/{evaluator_id}/{period}.json` | 漂移判定 + 所读快照指纹与该周期锚点数（"读的是哪一份"可追溯） |
| **迁移件** | `calibration/transfers/{transfer_id}.json` | `source_form` / `target_form` / 评估器 `id@version` / 周期 / 样本量 / `source_ref` / `transfer_basis` / `comparability.conditions` 逐条判定 + `verdict` + `reasons` / `status` + `overrides` 采纳留痕；**不得**出现权重键 |
| DB 迁移（两步） | `ops/migrations/versions/0011_daily_feedback.py`（`down_revision = "0010_dev_jobs"`） | ① `calibration_anchors` 增**可空**归属日列 `metric_date`（历史行 NULL **不回改、永不回填**，新写入非空，降级只 `DROP COLUMN`）；② 新表 `promo_daily_metrics`（唯一键 **（campaign_id, period）** + INSERT-only 触发器）；`promo_campaigns` 既有唯一键 `(round_id, material_id)` **不改**、保持无触发器 |

## 如何判定「机制已就绪」/「真实回流待运营」

- **机制已就绪**（本特性可交付、可机检）：A 组 + B 组命令全绿；`ops/demo_shortdrama_feedback.py`
  退出码 **0**（七步全 ok）；两形态切换机检通过；迁移面在"无来源"时如实标注、在"有条件"时逐条判定；
  019 的 `--channel` 断言面**逐条不减项**（C13 清单）。
- **真实回流待运营**（运营侧墙钟 + 凭证，不属本特性可交付面）：`短剧线真实数据回流 ≥2 周` 需要真实
  平台凭证 + 真实预算 + 合规审查（`docs/三期立项书.md:280`、`:271`）。工程侧的判定命令是
  `uv run python ops/billing.py runs --channel media --window-days <N> --config configs/shortdrama.yaml`：
  **未达标即退出码 1 并逐段报出缺口**（不插值、不判通过）；报告只允许写「机制已就绪 / 真实回流待运营」。
  窗口下限取**形态定值**：短剧态 `budget.runs.min_window_days: 14`（= 立项书 G4 验收原文"回流 ≥2 周"，
  不是发明数字；`configs/shortdrama.yaml:600-602`）、电影态保持 **7**；`gap_tolerance_days` 保持现值
  （容差取值仍属开放问题）。该键由 C6~C10 的窗口产物消费。
  **禁止**用模拟件凑够天数后声称"已达成"（C18 机检：模拟被标为真实次数恒 0）。
- 常驻门禁（本文不展开跑法）：覆盖率 ≥85%（含 web）、集成、对抗、无偏性、Immutable 审计与成本回归
  四条定时/阻塞门禁**不放松**，由各处常驻 CI 与定时工作流承载；`billing_alerts.yml` 的每日只读告警
  （`--channel llm`）继续在位。

## 验收映射

| 契约 | 验证命令 | 成功标准 |
| --- | --- | --- |
| C11 渠道命名空间配置形状 + 旧扁平形状兼容读 | `pytest tests/unit/test_billing_channels.py`；`ops/billing.py channels`（按 `pilot_llm` / `promo_platform` 核对）；投放渠道 `bill` 面 = B6 的 `import-bill`；demo 步 ① | SC-004 |
| C12 装配与额度按声明渠道集合分派（含投放接入） | `pytest tests/unit/test_billing_channels.py`；`ops/billing.py tiers --channel media`；demo 步 ②⑤ | SC-004 |
| C13 019 既有断言不削弱清单 | `pytest tests/contract/test_billing_contracts.py`；`ops/billing.py tiers --channel nope`（⇒ 2）；workflow `.github/workflows/billing_alerts.yml:27` 每日跑 | SC-004 |
| C14 投放受同一门禁 + 与按轮上限分辨 | `pytest tests/unit/test_billing_channels.py`（超限前置拒绝、两腿可辨）；demo 步 ⑤ | SC-004 |
| C15 迁移件与可比性判定 | `pytest tests/unit/test_calibration_transfer.py` + `tests/contract/test_calibration_contracts.py`；`ops/transfer.py transfer-report`；demo 步 ⑥ | SC-001/007 |
| C16 凭证矩阵与最小规模先行 | `pytest tests/unit/test_billing_channels.py`（装配拒绝）；`ops/billing.py calibrate --channel media --from-records`；`ops/billing.py import-bill/reconcile/alert-check --channel media`（B6：六类分类 + 未解释 100% 告警 + 报告必引批次）；demo 步 ③④ | SC-004/005/006 |
| C17 CLI / 离线演示与退出码 | `ops/demo_shortdrama_feedback.py`（退出码 0）；各入口 `--help` 0 / `--channel nope` 2 | SC-001 |
| C18 诚实分层机检 | `pytest tests/unit/test_billing_channels.py`（模拟不计入真实覆盖）；demo 步 ⑦；`ops/billing.py runs` 未达标 ⇒ 1 | SC-001/003 |

## 验证记录

- **已实跑（019 既有面基线，2026-09-25）**：`uv run pytest tests/contract/test_billing_contracts.py -q -k "C16 or C12 or C13"`
  → **7 passed**；`uv run pytest "tests/contract/test_billing_contracts.py::TestC16CLI退出码"`
  `"tests/contract/test_billing_contracts.py::Test对抗与篡改面::test_缺档与渠道不符即配置错误" -q` → **2 passed**
  ——即 C13 清单里 `--channel nope ⇒ 2`、`--channel ghost ⇒ 2 且错误含「不一致」` 两条**今天为绿**，
  本特性对它们的要求是**保留**（见 `contracts/channel-budget.md` C13）。
- **已实跑（020 实现落地后复跑，2026-09-25，本仓）**：
  - **B1 单测子集**：`uv run pytest tests/unit/test_billing_channels.py -q` → **23 passed**（13.26s）；
    `uv run pytest tests/unit/test_calibration_transfer.py -q` → **54 passed**（21.39s）；
    `uv run pytest tests/unit/test_form_switch.py -q` → **17 passed**（8.22s）；本批自检的六文件子集
    （`test_calibration_transfer` / `test_form_switch` / `test_config_integrity` / `test_period_cadence` /
    `test_billing_channels` / `test_promo_daily_ingest`）→ **270 passed**（70.65s）。
  - **B2 契约子集**：`uv run pytest tests/contract/test_calibration_contracts.py tests/contract/test_billing_contracts.py
    tests/contract/test_pilot_contracts.py tests/contract/test_transfer_contracts.py -q` → **108 passed**（97.73s，退出码 0）
    ——用例数**不减**（010 契约 17 → **19**：新增 T2016 的两条日级运转用例；019 契约 41；018 契约 25；
    新增迁移面契约 23），**零断言删除、零放宽**（`test_calibration_contracts.py` 既有周级用例一字不改，
    仅补足新增必需配置键 `calibration.transfer`）。
  - **B3 离线端到端演示**：`uv run python ops/demo_shortdrama_feedback.py` → **退出码 0**，七步全 ok
    （`ok=true`，`elapsed_seconds≈7.6`，`network=none`，`credentials_required=false`；步⑦ 机检到
    **真实覆盖天数 = 0**、结论「机制已就绪 / 真实回流待运营」）。
  - **B4 两形态渠道对照**：`uv run python ops/billing.py channels --config configs/shortdrama.yaml` → **退出码 0**
    （声明渠道 `llm` + `media`，`adapter` = `pilot_llm` / `promo_platform`，含凭证矩阵与每渠道账本路径）；
    `--config configs/movie.yaml` → **退出码 0**（仅 `llm`，**不出现投放渠道行**）；
    `uv run python ops/billing.py tiers --channel media --config configs/shortdrama.yaml` → **退出码 0**。
  - **B5 迁移面四命令**：`uv run python ops/transfer.py transfer --dry-run` → **零落盘**（目录文件集合前后相等；
    有来源夹具时退出码 0 且判定 `transferable`，无来源时退出码 1 并如实标注）；非 dry-run 落件 ⇒ 同键重产
    **退出码 1**；`transfer-confirm` → **退出码 0** 且 `weights_changed=false`、已确认件再 `transfer-shelve`
    → **退出码 1**（终态不可逆）；`transfer-report --data-dir <dir>` 无来源时 → **退出码 0** 且含
    「无可迁移结论（来源缺失）」；缺 `calibration.transfer` 任一键（两形态配置文件各验一次）→ **退出码 2**。
  - **静态检查**：`uv run ruff check .` → **All checks passed**；`uv run ruff format --check .` → **585 files already formatted**。
- **待运营项（如实留白，按 C18 口径标注，不预填未跑结论）**：
  ① **真实平台凭证**（`PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY`）与真实账户未到位 ⇒ C 路径
  `status` 保持 `not_delivered`（机制已交付、真实凭证/账户/预算未到位）；
  ② **真实预算档数字**与投放环节档位取值未标定（未标定期间按最小规模档运行并在 `note` 标注）；
  ③ **合规审查完成判据**未定（业务判定；未定前**不得**扩量）；
  ④ **「≥2 周」起算日**：短剧态 `budget.runs.min_window_days: 14` 已就位（= 立项书 G4 验收原文），
  但真实覆盖天数的起算依赖凭证到位与投放启动日期 ⇒ `uv run python ops/billing.py runs --channel media
  --window-days 14 --config configs/shortdrama.yaml` 在真实窗口积累前**必然退出码 1 并逐段报出缺口**
  （这是如实结果，不是失败）；
  ⑤ ⑥ 的 **B6 投放渠道账单对账**（`import-bill` / `reconcile` / `alert-check --channel media`）的**完整两条腿**
  与真实渠道口径由父代理在宿主机执行（本仓已由 `tests/unit/test_billing_channels.py` 的
  `Test投放渠道账单对账` 用例覆盖：六类差异齐备 + 未解释项告警 + 报告必引账单批次 + 同批次幂等 + 未识别格式零落盘）。
