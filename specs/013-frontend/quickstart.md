# Quickstart：前端可视化（013-frontend）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0009_web_readonly_role
uv run pytest tests/unit -k web                   # 查询层/门禁/同源
uv run pytest tests/contract -k web               # 路由表/权限/静态断言契约
uv run pytest tests/integration -k web            # 真实 PG 只读角色断言
uv run python ops/demo_web.py                     # 起服务演示（含三重机检结果输出）
uv run python -m web.export                        # 静态导出到 web/dist/
```

## 端到端场景（demo 流程）

1. **起服务**：夹具数据 + 只读角色 → 三重机检结果输出（路由表/权限/静态断言）
2. **树浏览**：三维过滤 → 节点详情（eval_breakdown 含版本）→ 谱系链路
3. **看板**：进化曲线 + 塌缩标注 + 成本汇总 + 信度/漂移徽标
4. **同源**：接口响应 vs 既有 JSON 报告逐字段一致（抽样输出）
5. **写拒绝**：POST/DELETE 全部 405/404，DB 零变更
6. **静态导出**：web/dist/ 脱离服务可浏览

## 里程碑验收（立项书周 10~11 / SC-001）

同源分层校验通过 + 只读三重机检通过 + 两视图可用；覆盖率 ≥85% 不降。
