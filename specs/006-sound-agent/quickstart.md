# Quickstart：声音 Agent 闭环（006-sound-agent）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0005_sound_gen_jobs
uv run pytest tests/unit -k sound                 # 执行器/评估器/配置/幂等
uv run pytest tests/contract -k sound             # 适配器契约套件 + 周校准纳入
uv run pytest tests/integration -k sound          # 真实 PG 两段式落盘
uv run pytest tests/unbiasedness -k sound         # τ ≥ 0.95 + 注入偏差拒绝
uv run python ops/demo_sound_loop.py              # 声音闭环演示
uv run pytest tests/unit/test_sound_dreaming.py   # 声音策略一轮做梦（agent_id="sound"，M=8 演示档，dreaming 零改动接入）
```

## 端到端场景（demo 流程）

1. **一轮探索**：夹具 TimingSheet + 台词 + 情绪基调 → 4 组参数（2 TTS + 1 SFX + 1 music）
   → 4 wav 工件内容寻址落库，成本按类型分账
2. **预算门禁**：构造超界申请 → 拒绝 + 已执行部分入账
3. **幂等**：同 round_id 二次触发 → 结果重建、0 重复扣费
4. **评估**：四评估器分量 + gate 语义 + 定点归一重算一致
5. **无偏性**：回放 vs 真实重跑 τ ≥ 0.95
6. **做梦**：声音策略候选一轮 → reward 排名 + 首轮基线

## 里程碑验收（立项书周 3~5 / SC-001）

无偏性 τ ≥ 0.95 通过 + 首轮进化曲线产出；覆盖率 ≥85% 不降。
