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
