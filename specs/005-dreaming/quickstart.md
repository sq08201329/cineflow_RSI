# 快速验证指南：做梦层与谱系报表

**目的**: 端到端验证本特性。验收场景编号对应 [spec.md](spec.md)。

## 前置条件

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres   # 演示数据与集成测试需要
```

## 验证 1：单元测试 + 覆盖率门禁（SC-007）

```bash
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-report=term-missing
```

**预期**: 全过；覆盖率 ≥ 85%；含 reward 口径对照、静态拦截、过拟合判定、塌缩双向断言。

## 验证 2：静态拦截与过拟合（SC-002/SC-003）

```bash
uv run pytest tests/unit -k "static or overfit"
```

**预期**: 违规候选 100% 拦截不回放；注入的过拟合候选 100% 丢弃、泛化候选不误判。

## 验证 3：5 轮进化端到端演示（US1/US2/US3 + 里程碑验收线 SC-004）

```bash
uv run python ops/demo_dreaming.py
```

演示（确定性变异生成器 + promo 模拟池）：连续 5 轮做梦（每轮 M=8 演示档）→ 静态检查 →
沙箱回放 → reward 排名 → 过拟合筛选 → 审批单（脚本内模拟 approve）→ 部署指针更新 →
谱系报表 + 进化曲线 JSON；断言生成 API 调用恒为 0、LLM 调用全过网关入账。

**预期**: 退出码 0；`collapse.collapsed == false`；谱系报表任一版本父/树/子字段完整；
报告含每轮 reward 与审批记录引用。

## 验证 4：集成测试（真实池）

```bash
uv run pytest tests/integration -m integration -k dreaming
```

**预期**: 做梦端到端（真实沙箱回放候选）全过；PG 不可用时按集成约定跳过。

## 说明

- 接口语义见 [contracts/](contracts/)；实体与谱系 schema 见 [data-model.md](data-model.md)
- 任务分解见 `tasks.md`（由 `/skill:speckit-tasks` 生成）
