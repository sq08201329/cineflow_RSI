# Quickstart：分镜 Agent 闭环（008-storyboard-agent）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0007_storyboard_render_jobs
uv run pytest tests/unit -k storyboard            # ShotList 校验/评估器/执行器/配置/摘要
uv run pytest tests/contract -k storyboard        # 预演适配器契约套件
uv run pytest tests/integration -k storyboard     # 真实 PG 两段式落盘
uv run pytest tests/unbiasedness -k storyboard    # τ ≥ 0.95 + 注入偏差拒绝
uv run python ops/demo_storyboard_loop.py         # 分镜闭环演示
uv run pytest tests/unit -k "storyboard_dreaming" # 做梦一轮接入验证
```

## 端到端场景（demo 流程）

1. **一轮分镜**：夹具剧本（3 场景 9 镜含关键行）→ 3 组 ShotList → 3 预演落树，成本入账
2. **ShotList 执行前校验**：引用不存在行/场景无镜头/关键行未承接/档位越界 → 拒绝、0 渲染 0 成本
3. **预算门禁与幂等**：超界拒绝；同 round_id 重建 0 重复扣费
4. **评估**：五分量 + gate 短路（违规不跑 judge）+ 定点归一重算一致
5. **无偏性**：回放 vs 真实重跑 τ ≥ 0.95
6. **做梦**：分镜策略候选一轮 → reward 排名 + 首轮基线（落盘形态 `history_root/storyboard/dream-storyboard-1.json`，演示档 M=8 见 `uv run pytest tests/unit -k "storyboard_dreaming"`；champion 手工首版 `policies/history/storyboard/{版本}.py` + `.meta.json` 谱系根）

## 里程碑验收（立项书周 6~8 / SC-001）

无偏性 τ ≥ 0.95 通过 + 首轮进化曲线基线落盘；覆盖率 ≥85% 不降。

## 验证记录（2026-09-20，T834 实跑回填）

- `uv sync`：依赖与 `uv.lock` 一致（35 包解析，无变更）✓
- `docker compose -f ops/dev.compose.yml up -d --wait postgres minio`：PG 16 + MinIO healthy ✓
- `uv run alembic -c ops/alembic.ini upgrade head`：0001~0007 依次执行，**0007_storyboard_render_jobs 真实执行 ✓**（`alembic current` = 0007_storyboard_render_jobs (head)，真实 PG 16）
- `uv run pytest tests/unit -k storyboard`：**218 过** ✓（含执行器/五评估器/合成/schema 快照/做梦）
- `uv run pytest tests/contract -k storyboard`：**11 过 + 6 skip**（真实预演渲染无凭证按用例跳过）✓
- `uv run pytest tests/integration -k storyboard -m integration`：**5 过**（真实 PG：0007 迁移字段/唯一键/CHECK、两段式 rendered→inserted、幂等重建 0 重复行、成本对账）✓
- `uv run pytest tests/unbiasedness -k storyboard`：**4 过，实测 τ = 1.0 ≥ 0.95** ✓（8 组 ShotList 梯度 × 2 树，6 形态注入偏差 100% 拒绝）
- `uv run python ops/demo_storyboard_loop.py`：退出码 0，六步全 ok ✓（3 组落树对账 spent $1.62；四类非法 0 渲染 0 成本；预算门禁 1 过 2 拒 + 幂等 0 重复渲染；五分量 + gate 短路 judge 0 调用 + 定点重算一致；τ=1.0；做梦 M=8 首轮基线落盘零生成；用时 ~1.0s）
- `uv run pytest tests/unit -k "storyboard_dreaming"`：**9 过**（M=8 全流程 + 首轮基线 `history_root/storyboard/dream-storyboard-1.json`）✓
- `uv run ruff check .` + `uv run ruff format --check .`（与 ci.yml 逐字一致）：**双绿**（304 文件已格式化）✓
- 全量 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85`：**1338 过 0 失败**，覆盖率 **92.42% ≥ 85%** ✓
- 全量 `uv run pytest tests/contract`：84 过 + 56 skip ✓
- 全量 `uv run pytest tests/unbiasedness`：16 过 ✓
- 全量 `uv run pytest tests/integration -m integration`（真实 PG）：**66 过**（含 0007 迁移/两段式/回放基准/3 万节点基准，用时 16 分 27 秒）✓
- champion 版本：`policies/history/storyboard/c0660f2b7fe8.py`（谱系根 meta.json，人工 approve）

