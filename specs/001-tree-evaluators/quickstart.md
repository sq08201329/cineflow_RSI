# 快速验证指南：发现树与评估器框架

**目的**: 任何人按本指南可在 15 分钟内验证本特性端到端可用。验收场景编号对应
[spec.md](spec.md) 用户故事的验收场景。

## 前置条件

- Python 3.11+、uv、Docker（仅集成测试与端到端演示需要）

## 环境搭建

```bash
uv sync                     # 安装依赖（含 dev 依赖 pytest / pytest-cov）
```

## 验证 1：单元测试 + 覆盖率门禁（对应 SC-001）

```bash
uv run pytest tests/unit --cov=core --cov-report=term-missing
```

**预期**: 全部通过；`core/tree` 与 `core/evaluators` 合计覆盖率 ≥ 85%。

## 验证 2：集成测试（Docker PG + MinIO，对应 SC-003/SC-004/SC-006）

```bash
docker compose -f ops/dev.compose.yml up -d   # 本地开发依赖（PostgreSQL 16 + MinIO）
uv run pytest tests/integration -m integration
```

**预期**:
- 对 `tree_nodes` 的 UPDATE/DELETE 尝试全部以 `ImmutableViolationError` 告终（SC-003）；
- 重复注册与"未升版本号变更"注册 100% 被拒（SC-004）；
- 同内容工件二次 `put` 不产生新对象（MinIO 对象数不变，SC-006）。

## 验证 3：端到端演示（对应 US1/US2/US3 验收场景）

```bash
uv run python ops/demo_tree_eval.py
```

演示脚本依次执行并打印 JSON 报告：

1. **US1-1 immutable**：建一棵树 → 追加 3 个节点 → 尝试改历史节点 → 报告拒绝结果
   与原文一致性哈希；
2. **US1-2 谱系查询**：按 `(project_id, agent_id, policy_version)` 过滤 → 打印命中的树
   与其节点层级；
3. **US1-3 失败节点**：模拟评估器崩溃 → 节点以 `failed` 落盘、score 为 null、cost 完整；
4. **US2-1/2 注册校验**：重复注册、非确定性注册分别演示被拒；human 锚点正常注册；
5. **US3-1/2 合成评分**：桩评估器构造 breakdown——硬规则 0 分时总分 = 0；正常时打印
   加权和与权重来源配置；
6. **US3-3 快照冻结**：演示修改 `configs/movie.yaml` 权重后，已落盘树的
   `config_snapshot` 不受影响。

**预期**: 脚本退出码 0；报告字段与上述每条结论一致。

## 清理

```bash
docker compose -f ops/dev.compose.yml down -v
```

## 说明

- 完整实现代码与测试套件见任务分解（`tasks.md`）与实现阶段，本指南只含验证路径；
- 接口语义细节见 [contracts/](contracts/)；表结构与校验规则见 [data-model.md](data-model.md)。
