# Quickstart：真实渠道与账单对账（019-real-channel-billing）

## 验证命令（与 `.github/workflows/ci.yml` 逐字一致）

```bash
uv sync
uv run ruff check .                              # ci.yml unit job
uv run ruff format --check .                     # ci.yml unit job
uv run pytest tests/unit -k billing              # 本特性单元面：配置/门禁/账本/价目矩阵/账单/对账/运行记录/CLI/纯度
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/contract -k billing          # 契约面：C1~C16
uv run pytest tests/contract                     # ci.yml contract job（含 C13 差异集与纯度断言）
CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q   # ci.yml：真实分支对着本地 stub 实跑
uv run pytest tests/integration/test_http_real_stub.py -q  # ci.yml：真实适配器 stub 集成（本地 loopback）
uv run pytest tests/unbiasedness -m unbiasedness           # ci.yml：无偏性门禁不放松（SC-008）
uv run python ops/billing.py tiers     --channel llm
uv run python ops/billing.py calibrate --channel llm --tier screenplay --config configs/movie.yaml
uv run python ops/billing.py import-bill --channel llm --file <账单文件> --bill-id <批次> --period <周期>
uv run python ops/billing.py reconcile --channel llm --period <周期>   # 退出码：0 无告警 / 1 有告警 / 2 用法错误
uv run python ops/billing.py alert-check --channel llm                 # 告警门禁只读入口（0 无告警 / 1 有告警 / 2 用法错误）
uv run python ops/billing.py runs      --channel llm --window-days 7
uv run python ops/billing.py raise-tier --channel llm --tier screenplay --limit-usd <额> \
    --calibration <id> --by <人> --reason <理由>
uv run python ops/demo_billing.py                # 离线六步演示（零真实调用、零外部网络）
```

`--channel llm` 是**示例取值**：渠道 id 由配置 `budget.channels` 声明，命令中的取值须与配置一致
（码内无渠道字面量，写死即红）。本特性**无 DB 迁移**（账本与产物全为文件），故不需
`alembic`；跑 `tests/integration` 全量时才需 `docker compose -f ops/dev.compose.yml up -d --wait postgres minio`（CI 提供）。

## 端到端场景（demo 流程，离线六步）

1. **额度声明与缺项拒绝**：删 `budget` 段 / 删某档 → 装配报错（不取码内默认）；预估额超剩余额度 →
   **调用前拒绝**、后端 0 次调用、`call_count` 与 `total_cost_usd` 恒 0、原因落 `alerts.jsonl`（SC-004）
2. **最小规模校准**：Mock 后端 + 夹具账单 → 校准记录（当时价目快照 / 实测花费 / 偏差 / 口径备注 /
   样本量 / 时间）append-only；同键重产拒绝
3. **未校准不得扩量**：无记录 / `passed=false` / 超期 → `raise-tier` 拒绝且留痕，配置**未被改写**（SC-005）
4. **账单导入与对账**：夹具账单含六类差异（计费口径 / 未入账 / 时序错位 / 免费额度与折扣 / 币种汇率 /
   未结账）→ 报告逐项分类 + 实测偏差 + 口径备注；无分类项 = 不可解释 → 告警落盘；重复批次拒绝；
   未识别格式报错且零落盘（SC-002/006/007/009）
5. **跨进程账本并发**：两进程抢同一档 → 总入账 ≤ 额度、`revision` 单调、无丢失更新；锁超时即拒绝
   （不无锁写、不静默放行）
6. **两维取价与连续运行证据**：四格（峰/谷 × 命中/未命中）取价正确；改价后按历史节点快照复算**逐字节
   不变**；缺格装配报错；运行记录 `runs --window-days 7` **同时**满足覆盖 ≥7 天与连续
   （最长断档 ≤ `gap_tolerance_days`）即通过、散点天数（累计够但有断档）**不得**判通过，断档**如实报缺口**（不插值）

## 诚实边界（本特性核心）

- **真实 vs 模拟**：本特性交付**机制**（门禁 / 导入面 / 对账 / 两维价目 / 运行记录）与**离线可复现验证**；
  demo 与单元面全部走 **Mock 后端 + 夹具账单**，零真实花费。**真实渠道的最小规模校准与 ≥7 天连续运行
  属运营**：需运营侧给出凭证与预算档的最终数字（规格开放问题 1），本特性**不发明数字**——未标定期间
  按最小规模档运行并如实标注"未标定"。
- **媒体渠道不在本特性范围**：本特性只实例化 LLM 渠道；实现不得为媒体/生成侧渠道发明前置条件
  （账号、凭证、平台名），投放侧协议校准风险整条**结转 G4**（规格澄清第 2 条）。
- **厂商缓存命中 token 可能缺报**：厂商未报告命中 token 时按**未命中档**记账并登记
  「厂商未报告命中 token（按未命中计）」——这是保守**高估**不是漏记；命中折扣是否计费属口径校准内容。
- **上界估算仍可能被超**：预估价为保守上界（prompt `len//2` + completion 满额），若实际更高则余量可为负，
  如实入账 + `over_limit` 告警并拒绝后续调用（不回滚、不改写）。
- **账本边界**：跨进程账本为**单主机**文件账本（flock + 原子替换）；**多主机需换 PG，未做**，登记为边界。
- **账单来源形态未裁决**（规格开放问题 2）：导出（人工上传）与 API 拉取在本契约下同构（`source` 字段留痕），
  第一实现形态由运营/plan 裁决。

## 里程碑验收（立项书周 5~8 / SC-001，`docs/三期立项书.md:53`）

LLM 真实渠道**连续运行 ≥1 周**（运行记录机检**同时**满足覆盖 ≥7 天与连续——最长断档 ≤ 配置容差；
断档如实报缺口、不插值）+ **账单对账差异可解释**
（分类 + 口径备注 + 实测偏差，不可解释项 100% 告警）+ **两维价目可表达**（四格取价正确、改价不漂移）。
**新增门禁**：真实渠道调用的**预算与账单差异告警**（`docs/三期立项书.md:129`）以
`billing/{channel}/alerts.jsonl` + `ops/billing.py alert-check`（只读门禁入口，非零退出即告警）+
`ops/billing.py reconcile` 退出码 + 契约用例常驻（C14）承载。

## 验收映射

| 契约 | 验证命令 | 成功标准 |
| --- | --- | --- |
| C1~C4 包落点/业务无关性 + 账单导入面 + append-only + 布局 | `pytest tests/unit -k billing`（纯度/路径/导入/篡改）；`pytest tests/contract -k billing`；demo 步④ | SC-006/007 |
| C5~C8 两维价目 + 峰谷归属 + 缓存来源 + 快照与不漂移 | `pytest tests/unit -k billing`（价目矩阵/四格/legacy 形状/复算逐字节）；demo 步⑥ | SC-003 |
| C9~C12 分档声明 + 前置门禁 + 跨进程账本 + 校准先决条件 | `pytest tests/unit -k billing` + `pytest tests/contract -k billing`；demo 步①②③⑤ | SC-004/005 |
| C13~C16 对账分类 + 告警门禁 + 运行记录窗口 + CLI/演示 | `pytest tests/unit -k billing` + `pytest tests/contract -k billing`；`ops/billing.py reconcile` 退出码；demo 步④⑥ | SC-001/002/009 |
| SC-008 覆盖率 ≥85%（含 web）+ 对抗/无偏性不放松 | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`；`uv run pytest tests/adversarial -m adversarial`；`uv run pytest tests/unbiasedness -m unbiasedness` | SC-008 |
| SC-001 连续运行 ≥1 周 | `ops/billing.py runs --channel llm --window-days 7`（**同时**判覆盖 ≥7 天与连续，散点天数不判通过；运营积累后复核，机制面由 demo 步⑥ 机检） | SC-001 |

## 验证记录（待实现回填；字段标签镜像 `specs/017-dev-agent-degraded/quickstart.md`）

- `uv sync`：（待实现回填）
- `uv run ruff check .` + `uv run ruff format --check .`：（待实现回填）
- `uv run pytest tests/unit -k billing`：（待实现回填——逐面：配置缺项/门禁拒绝 0 调用/账本并发/价目四格/账单导入/对账六类/运行记录缺口/CLI）
- `uv run pytest tests/contract -k billing`：（待实现回填——C1~C16 逐条）
- `uv run pytest tests/integration/test_http_real_stub.py -q`：（待实现回填）
- `uv run pytest tests/unbiasedness -m unbiasedness`：（待实现回填——门禁不放松）
- `uv run python ops/demo_billing.py`：（待实现回填——退出码 0、六步全 ok、用时）
- `uv run python ops/billing.py tiers / calibrate / import-bill / reconcile / alert-check / runs / raise-tier`：
  （待实现回填——各命令退出码与关键输出；`reconcile` 有告警/无告警两态）
- 全量 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`：
  （待实现回填——通过数、覆盖率）
- 全量 `uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`：
  （待实现回填）
- 全量 `uv run pytest tests/adversarial -m adversarial`：（待实现回填）
- 全量 `uv run pytest tests/integration -m integration`（真实 PG；本特性无迁移）：（待实现回填——既有回归不降）
- 登记点同步复核（新顶层段 `budget`）：`tests/unit/test_form_switch.py:259-276`（差异集含 `budget`）、
  `tests/unit/test_config_integrity.py:23-37`/`:43-67`（`CONFIG_CLASSES`/`REQUIRED_PATHS` 含 budget）、
  `tests/contract/test_pilot_contracts.py:412` C13，**外加** `agents/pilot/pilot.py:101`
  （`config_completeness` 预检清单）与 `tests/conftest.py:3154`（精简 movie 夹具）——二者是否登记取决于
  "预检是否强制额度声明"的取舍（见 plan.md 缺口 6）：（待实现回填）
- `tests/unit/test_no_vendor_literals.py` 调用点断言扩展（8 处均声明 `stage=`，取值 ∈ 两形态档位键集）：
  （待实现回填）
- 真实渠道最小规模校准（运营侧，需凭证与额度数字）：（待实现回填/待运营）
- ≥7 天窗口复核 `ops/billing.py runs --channel llm --window-days 7`：（待实现回填/待运营积累）

### 环境复核（待实现回填）

（待实现回填——本机口径、CI 口径差异、以及任何与 ci.yml 逐字执行不一致处的如实说明）
