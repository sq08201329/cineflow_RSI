# Quickstart：策略部署评估自动化（014-auto-deploy-eval）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "deployment or gate or shadow"     # 证据/门槛/模式/影子/抽检
uv run pytest tests/contract -k deployment                     # 契约 C1~C10
# 本特性无 DB 侧变更（纯文件化留痕 + yaml 定点改写）：集成层由既有 005/011 集成套件覆盖，不新增集成测试
uv run python ops/demo_deploy_gate.py                          # 端到端演示（六步）
```

## 端到端场景（demo 流程）

1. **门槛判定矩阵**：全满足 / 各单要件不满足 / 证据缺失 / 禁止名单 → 判定与理由齐全
2. **影子模式**：mode=shadow → 判定照跑、**指针不变**（机检）；产对照报告
3. **影子门禁**：影子期未满请求 auto → 拒绝并注明缺口
4. **自动部署**：mode=auto + eligible → 指针更新 + 证据快照 + 部署事件（source=auto）
5. **渐进抽检 + 否决回滚**：前 5 次全量复核；否决 → **三件事同时生效**（回滚 + manual + 重标定标记）
6. **误入率可重算**：从留痕重算 == 报告值

## 里程碑验收（立项书周 10~11 / SC-001）

门槛判定可机检 + 不满足门槛自动部署次数 0 + 影子期未满开启 auto 100% 被拒 +
抽检否决三件事 100% 生效 + 误入率可度量；覆盖率 ≥85% 不降。

> 真实 2 周影子期运行属运营：本特性交付机制与门禁，长期运行数据由运营积累。
