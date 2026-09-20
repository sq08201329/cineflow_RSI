# Quickstart：剪辑 Agent 闭环（007-editing-agent）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0006_edit_render_jobs
uv run pytest tests/unit -k editing               # EDL 校验/评估器/执行器/配置
uv run pytest tests/contract -k editing           # 渲染适配器契约套件
uv run pytest tests/integration -k editing        # 真实 PG 两段式落盘
uv run pytest tests/unbiasedness -k editing       # τ ≥ 0.95 + 注入偏差拒绝
uv run python ops/demo_editing_loop.py            # 剪辑闭环演示
uv run pytest tests/unit -k "editing_dreaming"    # 做梦一轮接入验证
```

## 端到端场景（demo 流程）

1. **一轮剪辑**：夹具镜头库（6 镜头 3 分区）+ 可选音轨 → 3 组 EDL → 3 成片落树，成本入账
2. **EDL 执行前校验**：非法 EDL（越界/跨分区/非法转场）→ 拒绝、0 渲染 0 成本
3. **预算门禁与幂等**：超界拒绝；同 round_id 重建 0 重复扣费
4. **评估**：五评估器分量 + gate 短路（违规不跑 judge）+ 定点归一重算一致
5. **无偏性**：回放 vs 真实重跑 τ ≥ 0.95
6. **做梦**：剪辑策略候选一轮 → reward 排名 + 首轮基线（落盘形态 `history_root/editing/dream-editing-1.json`，演示档 M=8 见 `uv run pytest tests/unit -k "editing_dreaming"`）

## 里程碑验收（立项书周 4~6 / SC-001）

无偏性 τ ≥ 0.95 通过 + 首轮进化曲线基线落盘；覆盖率 ≥85% 不降。

## 验证记录（2026-09-20，T734 实跑回填）

- `alembic upgrade head`：0006_edit_render_jobs 真实执行 ✓（真实 PG 16）
- `pytest tests/unit -k editing`：152 过 ✓
- `pytest tests/contract -k editing`：9 过 + 7 skip（真实渲染无凭证按用例跳过）✓
- `pytest tests/integration -k editing`：5 过（真实 PG：迁移/唯一键/两段式/对账/幂等）✓
- `pytest tests/unbiasedness -k editing`：3 过，**τ = 1.0 ≥ 0.95** ✓
- `python ops/demo_editing_loop.py`：退出码 0，六步全 ok ✓
- `pytest tests/unit -k "editing_dreaming"`：6 过（M=8 全流程 + 首轮基线落盘）✓
- 全量 `pytest tests/unit`：1065 过 0 失败（无回归）✓
- 覆盖率门禁：core+agents+dreaming 合计 **92.22% ≥ 85%** ✓
- `ruff check .` + `ruff format --check .`：双绿（与 ci.yml 逐字一致）✓
- 运维事项（tasks.md 备注）：004 单线程编码参数已安全回移（encode_mp4 固定
  threads=1；无持久化钉死哈希，视觉 51 单测 + 契约 73 条全绿），004 flake 根因消除
