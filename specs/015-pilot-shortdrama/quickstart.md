# Quickstart：短剧形态试水作品（015-pilot-shortdrama）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "orchestration or pilot or handoff"   # DAG/执行器/交接
uv run pytest tests/contract -k pilot                            # 契约 C1~C13
uv run python ops/pilot.py run --form shortdrama --config configs/shortdrama.yaml
uv run python ops/pilot.py run --form movie --config configs/movie.yaml   # 零代码切换对照
uv run python ops/pilot.py resume --run <run_id>                 # 断点续跑
uv run pytest tests/unit -k "config_integrity"                   # 短剧配置完整性
```

## 端到端场景（demo 流程）

1. **配置完整性**：shortdrama.yaml 过全部加载器（缺项即红）
2. **一次试水运行**：六阶段按 DAG 执行 → 样片包（成片 + 产物引用 + 清单 + 账目 + 快照）
3. **可复现**：同输入同配置两次运行逐字节一致
4. **形态切换零代码**：movie 与 shortdrama 同链各跑一轮，差异全部可归因配置；静态扫描零分支
5. **断点续跑**：中途失败 → 修复续跑（已完成阶段不重跑）；输入指纹变更 → 拒绝续跑
6. **拒绝语义**：上游不合格 → 下游不启动；环节候选全判 0 → 运行终止并记录全部理由

## 里程碑验收（立项书周 11~12 / SC-001）

自包含样片包产出 + 逐字节可复现 + 四段交接双向断言通过 + 成本对账零差异 +
形态切换零代码（静态扫描零分支）；覆盖率 ≥85% 不降。

> 样片为**模拟生成**（全模拟链路）：可用于技术验证与评审，不得对外作为真实作品发布。
