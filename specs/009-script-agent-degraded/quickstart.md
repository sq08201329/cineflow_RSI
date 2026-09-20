# Quickstart：剧本 Agent 降级模式（009-script-agent-degraded）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0008_screenplay_jobs
uv run pytest tests/unit -k screenplay            # 产出/评估器/提交通道/对比/判据
uv run pytest tests/contract -k screenplay        # 禁用自动进化 + 对比与采纳契约
uv run pytest tests/integration -k screenplay     # 真实 PG 分阶段落盘
uv run pytest tests/unbiasedness -k screenplay    # FR-013 无偏性 τ ≥ 0.95（新增 Agent 发布阻塞）
uv run python ops/demo_screenplay_loop.py         # 降级模式演示
```

## 端到端场景（demo 流程）

1. **分阶段产出**：题材输入 + 人工策略 → outline/scenes/script 三阶段落树，成本入账
2. **七评估器**：四门禁 + 两代理 + judge（仅 outline）；gate 短路不跑 judge；重算一致
3. **人工改策略**：提交新策略版本（静态检查）→ 回放对比报告（逐树/分项/pareto_auc）
4. **采纳门禁**：未采纳指针不变；采纳后指针更新 + 记录落盘
5. **禁止自动进化**：`run_dream_round(agent_id="screenplay")` 显式拒绝，0 候选 0 计费
6. **升级判据**：生成判据材料（阈值快照 + 自动结论 + 推翻留痕）；不达标如实标注

## 里程碑验收（立项书周 7~9 / SC-001）

剧本树全量落盘可回放（回放零 LLM 审计通过）+ **无偏性 τ ≥ 0.95（FR-013，新增 Agent 发布阻塞）**
+ 回放对比报告产出 + 人工采纳/拒绝留痕 + **dreaming 为剧本生成候选的次数为 0**；覆盖率 ≥85% 不降。

## 验证记录（2026-09-20，T935 实跑回填）

- `uv sync`：依赖与 `uv.lock` 一致（35 包解析，无变更）✓
- `docker compose -f ops/dev.compose.yml up -d --wait postgres minio`：PG 16 + MinIO healthy ✓
- `uv run alembic -c ops/alembic.ini upgrade head`（`CINEFLOW_PG_DSN` 注入）：0001~0008 依次执行，
  **0008_screenplay_jobs 真实执行 ✓**（`alembic current` = `0008_screenplay_jobs (head)`，真实 PG 16）
- `uv run pytest tests/unit -k screenplay`：**405 过** ✓（工件/配置/摘要/导出对接/七评估器/合成/执行器/
  回放对比与采纳/判据材料/CLI 六子命令）
- `uv run pytest tests/contract -k screenplay`：**20 过** ✓（C12 禁用自动进化 11 + C16 周校准接入 9）
- `uv run pytest tests/integration -k screenplay`（真实 PG）：**8 过** ✓（0008 迁移字段/枚举 CHECK/唯一键/
  CHECK 约束/应用账号权限 + 两段式全链路 + 幂等重建 + 成本对账 + 阶段失败成本照计）
- `uv run pytest tests/unbiasedness -k screenplay`：**9 过，实测 τ = 1.0 ≥ 0.95** ✓
  （8 组结构梯度 × 2 树；6 形态注入偏差 100% 拒绝，逆序实测 τ = −1.0；未达标不得产出对比报告）
- `uv run python ops/demo_screenplay_loop.py`：**退出码 0，六步全 ok ✓**（用时 ~0.9s）——
  ① 三阶段落树对账 spent $0.0073（树内 == 运营表 + judge 计费增量）② 七分量齐全 + gate 短路 judge 0 调用 +
  重算逐位一致 ③ 无偏性凭证 τ=1.0 + 新策略提交 + 回放对比 verdict=new_better（逐树/七分项/pareto_auc/
  3 树 UNKNOWN 如实标注）④ 未采纳 yaml 逐字节不变 → 采纳后指针更新 + reject/adopt 双留痕
  ⑤ `run_dream_round(agent_id="screenplay")` 显式拒绝（0 候选 0 计费 0 落盘，消息注明原则六）
  ⑥ 判据材料 meets（带内漂移 + 达标台账）/ below（无台账 + 漂移未测量，标注"不得据此升级"）
- `uv run ruff check .` + `uv run ruff format --check .`（与 ci.yml 逐字一致）：**双绿**（346 文件已格式化）✓
- 全量 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85`：
  **1767 过 0 失败，覆盖率 93.24% ≥ 85%** ✓
- 全量 `uv run pytest tests/contract`：**104 过 + 56 skip** ✓；全量 `uv run pytest tests/unbiasedness`：**25 过** ✓
- 全量 `uv run pytest tests/integration -m integration`（真实 PG）：**74 过**（用时 16 分 25 秒）✓
- 人工策略首版：`policies/history/screenplay/fa6b7bca77ed.py` + `.meta.json`（谱系根，`parent_version=null`，
  `no_auto_evolve=true` 名单审计）；部署指针 `deployment.screenplay.current_policy_version = fa6b7bca77ed`
