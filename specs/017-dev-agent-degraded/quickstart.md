# Quickstart：开发 Agent 降级模式（017-dev-agent-degraded）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0010_dev_jobs
uv run pytest tests/unit -k dev                   # 工件/配置/模拟信号/四评估器/合成/循环/对比/判据项
uv run pytest tests/contract -k dev               # 三重机检 + 通用件与 009 行为等价
uv run pytest tests/integration -k dev            # 真实 PG：0010 迁移 + dev_jobs 两段式落盘
uv run pytest tests/unbiasedness -k dev           # FR-013 无偏性 τ ≥ 0.95（新增 Agent 发布阻塞）
uv run python ops/demo_dev_loop.py                # 降级模式演示（离线六步）
uv run python ops/dev.py produce --round r1 --policy <版本>      # CLI（退出码语义同 009）：产出落树
uv run python ops/dev.py submit --source-file <策略.py> --by <提交人>   # 人工策略版本化
uv run python ops/dev.py compare --new-version <版本> --deployed-version <版本>
uv run python ops/dev.py adopt --comparison <ID> --by <人> --reason <理由>   # reject 同参
uv run python ops/dev.py evidence --period 2026-W38   # → calibration/upgrade-events/dev/{周期}.json
```

## 端到端场景（demo 流程）

1. **立项组合产出**：立项约束 + 人工策略 → TopicSlate 内容寻址落树（含 `policy_version`），成本三方对账（树内 == 运营表）
2. **四评估器与 gate 短路**：`rule.slate_structure` / `rule.slate_combination` / `proxy.genre_regression` / `proxy.buzz_heat`；结构或组合违规 → 判 0 不跑后续；同输入重算逐位一致
3. **无偏性凭证与回放对比**：先跑够轮次积累树 → 回放 vs 真实重跑 τ ≥ 0.95 出凭证 → 对比报告（逐树/分项/pareto_auc）；**可比对树数 < 配置下限时 compare 拒绝产出并报错**（含实测树数与门槛）
4. **采纳/拒绝**：未采纳指针逐字节不变；采纳后部署指针更新 + AdoptionRecord 留痕（人/时间/依据/理由）
5. **禁止自动进化三重机检**：`run_dream_round(agent_id="dev")` 显式拒绝（0 候选 0 计费 0 落盘）+ 名单默认值断言 + 审计断言
6. **升级判据材料**：全量阈值快照 + 逐项"实测值 / 无法评价（来源缺失）" + 系统结论非达标 + 继续观察条件（待补齐阈值项清单）

## 诚实边界（本特性最核心的工程对象）

两个代理信号取自**模拟数据源**（产物/报告/材料三处标注"**非真实商业数据**"，真实票房与舆情接入归 G3）；
本环节**无 judge 层、无人类锚点**，且 **010 明确排除本 Agent**——故判据材料的相关性/漂移阈值项
必然读作"**无法评价（来源缺失）**"、系统结论恒非"达标"。这是既定结果，不是缺陷，不得用伪信号补齐。

## 里程碑验收（立项书周 3~5 / SC-001）

选题树全量落盘可回放（回放零 LLM 审计）+ 无偏性 τ ≥ 0.95 + 对比报告产出 + 采纳/拒绝留痕 + dreaming 候选 0 次。

## 验收映射

| 契约 | 验证命令 |
| --- | --- |
| C1~C3 通用件抽取与 009 行为等价 | `pytest tests/unit -k dev` + `pytest tests/unit -k screenplay`（009 回归不降） |
| C4~C6 立项组合工件 schema 与进入生产标记 | `pytest tests/unit -k dev` + `pytest tests/contract -k dev`；demo 步① |
| C7~C10 二门禁 + 二代理 + 合成 + 模拟源标注 | `pytest tests/unit -k dev`（SC-003/004）；demo 步② |
| C11~C15 轮次循环/幂等/成本 + 策略通道 + 最小池门槛 + 三重机检 | `pytest tests/unit -k dev` + `tests/contract -k dev` + `tests/integration -k dev`；demo 步①③⑤（SC-002/005/011） |
| C16~C18 全量阈值 + 逐项可评价性 + 留痕不可改写 | `pytest tests/unit -k dev`（SC-006/009）；demo 步⑥ |
| SC-001 / SC-008（无偏性 τ ≥ 0.95）/ SC-007（覆盖率 ≥ 85%，含 web） | `pytest tests/unbiasedness -k dev`；demo 步③；`pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85` |

## 验证记录（2026-09-23，T1747 实跑回填）

- `uv sync`：依赖与 `uv.lock` 一致 ✓（Resolved 35 packages / Checked 30 packages，无变更）
- `docker compose -f ops/dev.compose.yml up -d --wait postgres minio`：PG 16 + MinIO healthy ✓
- `uv run alembic -c ops/alembic.ini upgrade head`（`CINEFLOW_PG_DSN` 注入）：0001~0010 依次执行，
  **0010_dev_jobs 真实执行 ✓**（迁移前该库无 `alembic_version` ⇒ 从零到 head 的真实执行；
  `alembic current` = `0010_dev_jobs (head)`，真实 PG 16）
- `uv run pytest tests/unit -k dev`：**219 过** ✓（工件/配置/模拟信号/四评估器/合成/轮次循环/
  回放对比与采纳/判据材料/导出对接/CLI）
- `uv run pytest tests/contract -k dev`：**29 过** ✓（三重机检 + 通用件与 009 行为等价 + 契约 C1~C18）
- `uv run pytest tests/integration -k dev`（真实 PG）：**8 过** ✓（0010 迁移字段/枚举 CHECK/唯一键/
  幂等重建 + 两段式全链路 + 成本对账 + FAILED 成本照计）
- `uv run pytest tests/unbiasedness -k dev`：**10 过，实测 τ = 1.0 ≥ 0.95** ✓（8 档题材梯度 ×
  逐题材模拟源系数；回放序列与真实重跑序列**逐位相等** ⇒ τ = 1.0，与 demo 步③ 凭证同值；
  注入偏差 100% 拒绝；未达标不得产出对比报告）
- `uv run python ops/demo_dev_loop.py`：**退出码 0，六步全 ok ✓**（用时 ~1.8s）——
  ① 立项组合产出落树 + 成本对账（6 条方向、1 个进入生产标记、成本 $0.00146，树内 == 运营表；
  6 次网关调用）
  ② 门禁短路重算（违规组合两门禁判 0 并点名 6 项违规、两代理**未跑也未落分量键**；合规重算
  逐位一致 + 工件哈希一致，6 次缓存命中 0 生成）
  ③ 冷启动拒绝（实测 2 树 < 门槛 `min_comparable_trees=3`）→ 无偏性凭证 τ=1.0 → 回放对比
  verdict=`new_better`（4 棵可比对树、逐分项、pareto_auc 0.6 vs 0.3583，4 树 UNKNOWN 如实标注）
  ④ 未采纳指针逐字节不变 → 采纳后指针更新 + reject/adopt 双留痕（演示用临时配置副本，
  仓库配置指针未改）
  ⑤ `run_dream_round(agent_id="dev")` 显式拒绝（0 候选 0 计费 0 落盘；名单实值
  `["screenplay","dev"]`；策略 meta 审计位 `no_auto_evolve=true`）
  ⑥ 判据材料 `below`：`evaluated_nodes`/`gate_violation_rate`/`proxy_distribution` 为**实测值**、
  `reliability`（010 按设计排除）/`drift`（无 judge 层）为**无法评价（来源缺失）** + 继续观察条件
- `uv run ruff check .` + `uv run ruff format --check .`（与 ci.yml 逐字一致）：**双绿**（531 文件已格式化）✓
- 全量 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`：
  **3396 过 0 失败，覆盖率 92.65% ≥ 85%** ✓（用时 18 分 12 秒；新增 `agents/dev/export_slate.py`
  覆盖率 100%）
- 全量 `uv run pytest tests/contract`：**344 过 + 56 skip** ✓（skip = 真实实现无凭证按用例跳过，
  CI 口径同）；`CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`：**400 过** ✓（真实分支
  对着本地 stub 实跑）
- 全量 `uv run pytest tests/unbiasedness -m unbiasedness`：**40 过** ✓；
  `uv run pytest tests/adversarial -m adversarial`：**6 过** ✓（本机 Docker 加固容器后端；
  CI 为 gVisor 权威档）
- 全量 `uv run pytest tests/integration -m integration`（真实 PG）：**96 过**（用时 17 分 42 秒）✓
- 人工策略首版：`policies/history/dev/34525518074d.py` + `.meta.json`（谱系根，`parent_version=null`，
  `no_auto_evolve=true` 名单审计，静态检查 passed；版本 = 源码 BLAKE3 前 12 位）；部署指针
  `deployment.dev.current_policy_version = 34525518074d`
- 导出面复核（C6）：`export_slate` 两次调用逐字节一致；门禁违规组合仍导出**全部条目与标记**
  （含悬空标记）；导出含 `schema_version`；**无 `FieldParity` 声明**（字段级交接属 G2/018，
  静态断言常驻）

### 环境复核（2026-09-23）

本机 dev 库此前**未迁移**（无 `alembic_version`）。若在未迁移的库上导出 `CINEFLOW_PG_DSN` 再跑
单测，`tests/unit/test_screenplay_cli.py` 的两个 `evidence` 用例会走真实 PG 读而失败
（`relation "discovery_trees" does not exist`）——ci.yml 的 unit job **不注入**该变量
（单测走 SQLite 内存库），按 ci.yml 逐字执行即 3396 全过；先跑 `alembic upgrade head` 后，
两种口径皆绿（非本特性引入的失败，属本地库状态）。

