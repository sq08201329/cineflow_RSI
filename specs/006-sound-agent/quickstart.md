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

## 验证记录（2026-09-20，T628）

| 命令 | 结果 |
| --- | --- |
| `uv run alembic -c ops/alembic.ini upgrade head`（真实 PG） | 0001~0005 全量执行，`0005_sound_gen_jobs (head)` |
| `uv run pytest tests/unit -k sound` | 107 passed |
| `uv run pytest tests/contract -k sound` | 22 passed, 18 skipped（真实骨架无凭证按用例 skip） |
| `uv run pytest tests/integration -k sound -m integration`（真实 PG） | 5 passed |
| `uv run pytest tests/unbiasedness -k sound` | 3 passed（τ=1.0 ≥ 0.95；四种注入偏差 100% 拒绝） |
| `uv run python ops/demo_sound_loop.py` | 退出码 0（六步全通：分账/预算 2 过 2 拒/幂等 0 重复/gate 短路 0 分/τ=1.0/做梦 M=8 基线零生成） |
| `uv run pytest tests/unit tests/contract` | 935 passed, 43 skipped |
| 覆盖率 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85` | TOTAL 92%（≥85% 达标，退出码 0）；agents/sound 各模块 92%~100%（http_real.py 0%——真实骨架无凭证不假装生成，契约 skip 语义同 004 惯例） |
| `uv run ruff check agents tests policies dreaming ops` | All checks passed |

注：做梦接入为 dreaming 零改动（005 泛化已成立，静态证明见
tests/unit/test_sound_dreaming.py::test_dreaming_零改动证明）；
首轮进化基线形态 = `history_root/sound/dream-sound-1.json`（演示档 M=8，落盘只增不改）。
