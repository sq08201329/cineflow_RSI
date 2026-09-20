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
