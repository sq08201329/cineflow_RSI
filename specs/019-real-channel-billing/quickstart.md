# Quickstart：真实渠道与账单对账（019-real-channel-billing）

## 验证命令（与 `.github/workflows/ci.yml` 逐字一致）

```bash
uv sync
uv run ruff check .                              # ci.yml unit job
uv run ruff format --check .                     # ci.yml unit job
uv run pytest tests/unit -k billing              # 本特性单元面：配置/门禁/账本/价目矩阵/账单/对账/运行记录/CLI/纯度
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/contract -k billing          # 契约面：C1~C16
uv run pytest tests/contract                     # ci.yml contract job（含 test_pilot_contracts.py 的段差异集与纯度断言）
CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q   # ci.yml：真实分支对着本地 stub 实跑
uv run pytest tests/integration/test_http_real_stub.py -q  # ci.yml：真实适配器 stub 集成（本地 loopback）
uv run pytest tests/unbiasedness -m unbiasedness           # ci.yml：无偏性门禁不放松（SC-008）
uv run python ops/billing.py tiers     --channel llm
uv run python ops/billing.py calibrate --channel llm --tier screenplay --config configs/movie.yaml
                                                 # 只读既有真实调用记录（ops/smoke_llm.py --round 产物），不联网、不构造后端
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
   **调用前拒绝**、后端 0 次调用、`call_count` 与 `total_cost_usd` 恒 0、调用点 `CostRecord` **全零**
   （零成本分支，C10）、原因落 `alerts.jsonl`（SC-004）
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
- **账单来源形态已决（本特性第一实现 = 导出导入）**：`import-bill` **不联网**（人工上传厂商导出文件，
  C16）；API 拉取按同构的 `source=api` 表达、**本特性不实现联网拉取**（留作后续，不引入新凭证面）。

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
| C9~C12 分档声明 + 前置门禁 + 跨进程账本 + 校准先决条件 | `pytest tests/unit -k billing` + `pytest tests/contract -k billing`（含**拒绝零成本分支** `test_billing_refusal_branch.py`）；demo 步①②③⑤ | SC-004/005 |
| C13~C16 对账分类 + 告警门禁 + 运行记录窗口 + CLI/演示 | `pytest tests/unit -k billing` + `pytest tests/contract -k billing`；`ops/billing.py reconcile` 退出码；demo 步④⑥ | SC-001/002/009 |
| SC-008 覆盖率 ≥85%（含 web）+ 对抗/无偏性不放松 | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`；`uv run pytest tests/adversarial -m adversarial`；`uv run pytest tests/unbiasedness -m unbiasedness` | SC-008 |
| SC-001 连续运行 ≥1 周 | `ops/billing.py runs --channel llm --window-days 7`（**同时**判覆盖 ≥7 天与连续，散点天数不判通过；运营积累后复核，机制面由 demo 步⑥ 机检） | SC-001 |

## 验证记录（2026-09-24 实跑回填；字段标签镜像 `specs/017-dev-agent-degraded/quickstart.md`）

- `uv sync`：依赖与 `uv.lock` 一致 ✓（Resolved 35 packages / Checked 30 packages，无变更）
- `uv run ruff check .` + `uv run ruff format --check .`：**双绿** ✓（`All checks passed!` / 552 files already formatted）
- `uv run pytest tests/unit -k billing`：**269 过** ✓（配置缺项即报错 / 门禁拒绝 0 调用 / 拒绝零成本分支 / 账本并发与锁超时 / 价目四格与 legacy 形状 / 账单导入与批次幂等 / 对账六类与未解释告警 / 运行记录缺口不插值 / CLI 七子命令 / `core/billing` 纯度）
- `uv run pytest tests/contract -k billing`：**41 过** ✓（C1~C16 逐条，含本特性对抗面）
- `uv run pytest tests/integration/test_http_real_stub.py -q`：**54 过** ✓（真实适配器 stub，本地 loopback）
- `uv run pytest tests/unbiasedness -m unbiasedness`：**40 过** ✓（门禁不放松，SC-008）
- `uv run python ops/demo_billing.py`：**退出码 0，六步全 ok ✓**（`elapsed_seconds = 3.55`）——① 额度声明与缺项拒绝 ② 最小规模校准记录 ③ 未校准扩量拒绝留痕 ④ 夹具账单导入与对账分类 ⑤ 跨进程账本并发共享额度 ⑥ 两维取价与窗口机检
- `uv run python ops/billing.py …`：**七子命令齐备** ✓（`--help` 退出码 0）。实测退出码：`tiers` **0**（余量 / 拒绝计数 / 未结算预留）、`runs --window-days 7` **1**（无运行记录 ⇒ 如实报缺口，**不判通过**）、`alert-check` **0**（无报告 ⇒ 冷启动放行，既不假绿也不误红）；阶段 5 另实跑 `import-bill` 0 / 重复批次 **1** / `reconcile` **1**（含未解释项与 `delta_over_threshold`/`unexplained_delta` 告警）/ 无账单批次 **1**（不产"零差异"报告）/ `raise-tier` 未校准 **1** 且**配置未被改写**
- 全量 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`：**3669 过 0 失败，覆盖率 92.65% ≥ 85%** ✓（20 分 21 秒；TOTAL 17043 语句 / 1253 未覆盖）
- 全量 `uv run pytest tests/contract`：**385 过 + 56 skip** ✓（skip = 真实实现无凭证按用例跳过，CI 同口径）；`CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`：**441 过** ✓
- 全量 `uv run pytest tests/adversarial -m adversarial`：**6 过** ✓（本机 Docker 加固容器后端；CI 为 gVisor 权威档）
- 全量 `uv run pytest tests/integration -m integration`（真实 PG + MinIO）：**96 过 / 54 deselected** ✓（17 分 23 秒；本特性**无 DB 迁移**，此套件为既有回归面）
- 登记点同步复核（新顶层段 `budget`）：**五处全部落地且全绿** ✓——`tests/unit/test_form_switch.py` 差异集含 `budget`（权重差异循环**不适用**，已注明理由）、`tests/unit/test_config_integrity.py` 的 `CONFIG_CLASSES`/`REQUIRED_PATHS` 含 budget、`tests/contract/test_pilot_contracts.py` 的段差异集（该文件内编号 C13）含 budget、`agents/pilot/pilot.py` 的 `config_completeness` 预检清单含 budget 加载器（并删掉 `budgets[agent]=0.0` 占位、LLM 腿改按**环节 id** 登记声明额度）、`tests/conftest.py` 精简 movie 夹具补段
- `tests/unit/test_no_vendor_literals.py` 调用点断言扩展：**8 处 `.chat(` 均声明 `stage=` 且取值 ∈ 两形态档位键集** ✓（调用点计数**仍为 8**，未新增；`ops/smoke_llm.py` 那一处**单列**，不在该扫描域）
- 门禁注入普查：**13 处** ✓（真实 2 处注入非 `None` 守卫 + 离线显式 `None` 8 处 + 离线但注入真守卫 3 处 = `ops/demo_billing.py`），三类清单各自常驻，新增任一点即红
- **真实渠道最小规模校准**（运营侧，需凭证与额度数字）：**待运营**——机制与离线复现已验证（demo 步②），真实校准需运营侧给出凭证与预算档
- **≥7 天窗口复核** `ops/billing.py runs --channel llm --window-days 7`：**待运营积累**——机制已机检（覆盖 ∧ 连续双条件 + 断档不插值），真实窗口需墙钟时间
- 单测不再向仓库写入运行期产物：跑完全量套件后仓库根**无 `billing/` 残留** ✓（`tests/unit/test_smoke_llm.py` 等已改用 tmp 账本根的配置副本）

### 环境复核（2026-09-24）

- 本机口径与 `ci.yml` **逐字一致**：覆盖率命令、契约两条腿（含 `CINEFLOW_CONTRACT_STUB=1`）、对抗、集成、无偏性、ruff 双绿——**全部实跑、无跳过**（`tests/contract` 的 56 项 skip 是"真实实现无凭证"的按用例跳过，CI 同口径）。
- 本机 Docker 提供 PostgreSQL + MinIO（`docker compose -f ops/dev.compose.yml up -d --wait postgres minio`），故 `tests/integration -m integration` **真实执行**（96 过）；本特性无迁移，该套件在此为既有回归面。
- 与 CI 的**唯一差异**：对抗套件本机走 Docker 加固容器后端（CI 的权威档为 gVisor/runsc），断言面不受影响。
- 口径登记：`alert-check` 在**无任何报告**时退出 0（冷启动放行，避免首日假红）；一旦存在报告，未解释项或超阈值即退出 1——该口径写在 `ops/billing.py` 的 docstring 与 C14 用例里。
