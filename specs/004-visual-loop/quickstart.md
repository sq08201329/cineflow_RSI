# 快速验证指南：视觉 Agent 闭环

**目的**: 端到端验证本特性。验收场景编号对应 [spec.md](spec.md)。

## 前置条件

```bash
uv sync    # 新增 numpy/imageio/imageio-ffmpeg
docker compose -f ops/dev.compose.yml up -d postgres   # 集成测试需要
```

## 验证 1：单元测试 + 覆盖率门禁（SC-005）

```bash
uv run pytest tests/unit tests/contract --cov=core --cov=agents --cov-report=term-missing
```

**预期**: 全过；覆盖率 ≥ 85%；五评估器单测含退化输入（全黑/全白/单镜头）不崩溃断言。

## 验证 2：评估器确定性（SC-001/SC-002 核心）

```bash
uv run pytest tests/unit -k "consistency or visual" 
```

**预期**: 同工件重算逐字节一致；注入的非确定性变体（乱序采样）验收判拒绝。

## 验证 3：闭环端到端演示（US1/US2/US3 验收场景）

```bash
uv run python ops/demo_visual_loop.py
```

演示（模拟生成器）：触发一轮探索（3 个候选片段，含不合规与预算超限样例）→ 生成 →
五评估器打分 → 合成 → 落盘冻结 → 成本对账 → 幂等二次触发 → 冻结入池回放（零生成断言）
→ 一致性验收报告 JSON。

**预期**: 退出码 0；对账 `consistent=true`；一致性报告 `consistent_rate=1.0`、`verdict=pass`；
耗时 < 5 分钟断言入报告。

## 验证 4：适配器契约套件（SC-007 双实现）

```bash
uv run pytest tests/contract -k video_gen
```

**预期**: 模拟实现全过（预估/实际花费、幂等、状态机、工件可解码、错误映射）；
真实实现无凭证跳过。

## 说明

- 接口语义见 [contracts/](contracts/)；状态机与表结构见 [data-model.md](data-model.md)
- 任务分解见 `tasks.md`（由 `/skill:speckit-tasks` 生成）
