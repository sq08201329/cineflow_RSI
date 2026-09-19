# CineFlow：影视全 Agents 自我进化生产系统

L1 基建第一批交付：**发现树（core/tree）+ 评估器框架（core/evaluators）**。

- 发现树：探索尝试以不可变节点落盘（frozen dataclass + PostgreSQL 触发器 +
  应用账号权限回收双保险），工件以 BLAKE3 内容寻址存储天然去重，谱系按
  （项目, Agent, 策略版本）三维可查；
- 评估器框架：评估器以 `evaluator_id@version` 全局唯一注册（版本冻结、
  非确定性拒绝、人类锚点例外），合成评分实现硬规则门禁 + 加权求和，
  权重来自形态配置（`configs/*.yaml`）并随树冻结快照。

工程约定以项目宪章 `.specify/memory/constitution.md` 为最高标准
（原则一/二不可协商：评估器版本冻结、节点 immutable + 成本必入账）。

## 目录结构

```text
core/
├── tree/             # 发现树：models / store / db / artifacts / errors
└── evaluators/       # 评估器：base / registry / composite / weights / errors
configs/
└── movie.yaml        # 形态配置示例（评估器权重，切换形态零代码改动）
tests/
├── stubs.py          # 桩评估器唯一定义来源
├── unit/             # SQLite 内存库 + 本地工件存储，全离线
└── integration/      # Docker 化 PostgreSQL + MinIO（无 Docker 自动跳过）
ops/
├── dev.compose.yml   # 本地开发依赖（PostgreSQL 16 + MinIO）
├── alembic.ini       # 迁移配置（DSN 走环境变量 CINEFLOW_PG_DSN）
├── migrations/       # Alembic 迁移（首个迁移含 immutable 触发器 + REVOKE）
├── audit_immutable.py  # immutable 审计（随机抽样复算 score，门禁脚本）
└── demo_tree_eval.py   # 端到端演示（六步场景，输出 JSON 报告）
specs/001-tree-evaluators/  # 本特性的 spec-kit 设计文档（spec/plan/tasks/contracts）
```

## 开发环境搭建

```bash
uv sync   # 安装依赖（Python 3.11 由 .python-version 锁定）
```

## 测试

```bash
# 单元测试（离线，SQLite 内存库）
uv run pytest tests/unit

# 单元测试 + 覆盖率门禁（宪章里程碑：core ≥ 85%）
uv run pytest tests/unit --cov=core --cov-report=term-missing --cov-fail-under=85

# 集成测试（需 Docker；不可达时自动跳过）
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
docker compose -f ops/dev.compose.yml up minio-init          # 建工件 bucket（一次性）
uv run alembic -c ops/alembic.ini upgrade head
uv run pytest tests/integration -m integration
```

注意：`ops/dev.compose.yml` 中的账号密码**仅用于本地开发**，不得用于任何共享环境。

## 快速演示

```bash
uv run python ops/demo_tree_eval.py
```

依次演示：immutable 拒绝与一致性哈希、三维谱系查询、失败节点成本入账、
注册校验（重复/非确定性拒绝 + 人类锚点放行）、合成评分（gate/加权）、
配置快照冻结；输出 JSON 报告，全部通过时退出码 0。
演示用 SQLite 内存库，生产切 PostgreSQL/MinIO 只需更换 DSN 与 ArtifactStore 实现。

## immutable 审计（每日门禁）

```bash
uv run python ops/audit_immutable.py   # 默认读 CINEFLOW_PG_DSN，可用 --dsn 覆盖
```

随机抽 100 个历史节点（不足则全量）按冻结快照复算 score 比对，
任一不一致即非零退出并输出 JSON 差异明细。

## 回放与沙箱（功能 002）

```bash
# 回放语义单测（进程内模拟器，离线）
uv run pytest tests/unit -k "replay or clock or kendall"

# 对抗测试套件（合并阻塞门禁；本地 Docker 用加固容器，CI 用 gVisor；
# 无 Docker 报错而非跳过）
uv run pytest tests/adversarial -m adversarial

# 无偏性验收（发布阻塞门禁：Kendall τ ≥ 0.95）
uv run pytest tests/unbiasedness -m unbiasedness

# 端到端演示：小树 → 模拟器 → 沙箱容器回放 → 轨迹 JSON（生成调用恒 0 断言）
uv run python ops/demo_replay.py

# 3 万节点基准（SC-006：全程 < 10 分钟；优先 PG，不可用退 SQLite）
uv run pytest tests/integration/test_replay_benchmark.py -m integration
```

门禁要点：回放零生成（max_generation_calls 装配强制归零）；probe 规范化
精确匹配、无匹配 UNKNOWN 不得分；策略沙箱无网络/无凭证/只读/限额；
作弊三件套（peek_latent / timing_side_channel / hash_oracle）全拦截。

## 宣发闭环（功能 003）

```bash
# 单元测试 + 覆盖率门禁（core + agents 合计 ≥ 85%）
uv run pytest tests/unit --cov=core --cov=agents --cov-fail-under=85

# 平台适配器契约套件（模拟实现全过；真实实现无凭证按用例跳过）
uv run pytest tests/contract

# 闭环端到端演示（模拟平台 + Mock 网关，离线可跑）：
# 一轮探索 → 合规/预算门禁 → 投放 → 幂等二次触发 → 回流冻结 → 入池回放 → 进化报告
uv run python ops/demo_promo_loop.py

# 回流管道（生产 PG；无 DSN 返回退出码 2）
uv run python ops/ingest_metrics.py --round-id <round_id>
```

门禁现状：预算门禁（单轮 ≤ 总预算 × pilot_ratio，分为单位事务扣减）、
轮次幂等（唯一键 + 确定性派生 ID，二次触发零重复扣费）、成本对账三方一致
（树内 + 待回流 == 网关 + 适配器）、回流指标越界拒绝、写入即冻结。

真实渠道接入是凭证配置的运维动作（代码路径不变）：平台适配器
`PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY`；LLM 网关
`OPENAI_BASE_URL` / `OPENAI_API_KEY`。缺凭证不假装投放（原则六）。

## 视觉闭环（功能 004）

```bash
# 单元测试 + 覆盖率（五评估器单测含退化输入不崩溃断言）
uv run pytest tests/unit -k visual

# 视频生成适配器契约套件（模拟实现全过；真实实现无凭证跳过）
uv run pytest tests/contract -k video_gen

# 一致性验收与视觉确定性（重算逐字节一致、注入漂移必拒）
uv run pytest tests/unit -k "consistency or visual"

# 闭环端到端演示（模拟生成器 + Mock 网关，离线可跑）：
# 一轮 3 候选片段（含违规/超限样例）→ 对账 → 幂等 → 冻结回放 → 一致性报告
uv run python ops/demo_visual_loop.py
```

门禁现状：五评估器全确定性（quantize 6 位小数定点归一，版本号携带实现/
采样/提示词/锚点哈希）；合规 0 分短路不跑 judge（省 LLM 成本）；judge 调用
全经网关计费；一致性验收为发布阻塞（一致率 100% + τ 分档门禁）。

真实生成平台接入是凭证配置的运维动作：`VISUAL_GEN_BASE_URL` /
`VISUAL_GEN_API_KEY`（缺凭证不假装生成，原则六）。

## 做梦层（功能 005）

```bash
# 单测 + 覆盖率（core + agents + dreaming 口径 ≥ 85%）
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85

# 静态拦截与过拟合判定
uv run pytest tests/unit -k "static or overfit"

# 5 轮做梦端到端演示（变异生成器 + 真实沙箱回放 + 模拟审批 + 曲线/谱系）
uv run python ops/demo_dreaming.py

# 做梦沙箱 e2e 与 M=128 全量基准（需 Docker）
uv run pytest tests/integration -m integration -k dreaming
```

做梦管线：digest（最近 K 轮落盘报告）→ 候选生成（MutatorGenerator 占位 /
LLMGenerator 经网关计费）→ 静态检查（违规不回放不记分）→ 沙箱串行回放
→ reward = pareto_auc（梯形归一化权威口径）− λ·并行惩罚 → 过拟合筛选
（最近树只做 validation）→ 人工审批闸门（未 approve 不得进部署指针，
SC-005 机检）→ DreamRound/meta.json 落盘（只增不改，git 承担审计）。

审批操作（生产形态）：
```bash
# 审批单生成于 dreaming/tickets/{round_id}.approval.json；人工确认后：
uv run python -c "from dreaming.approve import decide; decide(
    'dreaming/tickets/<round>.approval.json', approver='<姓名>',
    decision='approved', reason='<理由>', config_path='configs/movie.yaml')"
```

## spec-kit 工作流

本仓库由 spec-kit 驱动：

- `specs/001-tree-evaluators/`：发现树与评估器框架（T001–T036 全部完成）
- `specs/002-replay-sandbox/`：回放模拟器与沙箱化策略执行（T101–T138 全部完成）
- `specs/003-promo-loop/`：宣发 Agent 全闭环（T201–T228 全部完成）
- `specs/004-visual-loop/`：视觉 Agent 闭环（T301–T332 全部完成）
- `specs/005-dreaming/`：做梦层与谱系报表（T401–T425 全部完成）

## 一期里程碑全景

| 周 | 交付物 | 验收 | 状态 |
| --- | --- | --- | --- |
| 1~3 | core/tree + core/evaluators + 注册中心 | 覆盖率 ≥ 85% | ✅ 001 |
| 4~6 | core/replay + sandbox + 对抗测试套件 | 作弊全拦截；τ≥0.95 | ✅ 002 |
| 7~8 | agents/promo 全闭环（模拟投放，单轮 ≤ 预算 2%） | 首轮进化曲线，成本入账 | ✅ 003 |
| 9~10 | agents/visual 五评估器 + 闭环 | 回放打分与重算一致性验收 | ✅ 004 |
| 11~12 | dreaming 层 + 谱系报表 | 连续 5 轮 reward 曲线无塌缩 | ✅ 005 |

每个特性目录含 `spec.md`（用户故事与需求）、`plan.md` / `research.md` /
`data-model.md`（技术设计）、`contracts/`（接口契约）、`tasks.md`（任务分解）、
`quickstart.md`（端到端验证指南）。
