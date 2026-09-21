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

## 验证记录（2026-09-21 收官复跑，实测数字）

命令与 CI 逐字一致（`uv run` + pytest / ruff）。

| 验证项 | 命令 | 实测结果 |
| --- | --- | --- |
| 本特性单测 | `uv run pytest tests/unit -k "deployment or deploy_ or shadow or gate or mode"` | **451 passed** |
| 本特性契约 | `uv run pytest tests/contract -k deployment` | **19 passed** |
| 契约全量 | `uv run pytest tests/contract` | **228 passed / 56 skipped**（skip = 无凭证的真实平台/DB 用例，既有约定） |
| 单元全量 + 覆盖率门禁（CI 口径） | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85` | **2581 passed / 0 failed**（0:07:03），TOTAL **92.43%**（门槛 85%，通过） |
| 端到端演示 | `uv run python ops/demo_deploy_gate.py` | 退出码 **0**；六步输出如上；两次连跑输出逐字节一致（除临时目录路径） |
| ruff 检查 | `uv run ruff check .` | All checks passed |
| ruff 格式 | `uv run ruff format --check .` | 428 files already formatted |

**SC 机检口径对照**（均由测试承载）

- **SC-001** 门槛判定可机检（组合矩阵双向断言）+ **不满足门槛的自动部署次数 0**（auto 期非 eligible 一律不调部署，契约测试断言 deploys 零留痕）+ 影子期未满开启 auto 100% 被拒（含缺口说明，拒绝零副作用）；
- **SC-002** 证据缺失与禁止名单 100% 非 eligible（`test_c2…`/`test_sc002…`）；
- **SC-003** 影子期指针变更 0（configs 副本逐字节 + deploys 零留痕机检）；对照报告字段齐全（差异分类四类、误入率分子分母与口径说明）；
- **SC-004** 抽检否决三件事同时生效 100%（指针回滚 + 模式 manual + 重标定标记三断言）+ 失败路径保持人工（目标工件缺失 → 显式报错且模式已回 manual，补齐工件后可续做回滚）；
- **SC-005** 自动部署留痕完整（证据快照 + 部署事件 source=auto）+ **历史节点零修改**（树库行数与策略工件逐字节不变，且部署模块静态无 DB/无 meta.json 写入路径）；
- **SC-006** 指针与留痕不一致（外部绕过）100% 拒绝 + 告警留痕（`deploys/alerts.jsonl`）；
- **SC-007** 误入率可度量且可从事件留痕重算 == 报告值（影子期口径：分子 = 会放行但人工拒绝 ∪ 放行样本中被判不可接受，按候选去重；分母 = 影子放行数）。

**实测发现（如实记录）**

1. **同族留痕污染部署序列**（演示第 4 步当场抓出）：`deploys/` 目录里同时放部署事件与"同周期择一记录"后，按 `*.json` 全量读取会让"最后一条部署"漂移到择一记录上（`KeyError: 'pointer_after'`）。已改为按事件字段完整性过滤，并补回归测试。
2. **否决失败路径必须可续做**：回滚目标工件缺失时否决结论已留痕，若把"已复核"一律判为重复否决，运营补齐工件后无法完成回滚。现语义：已否决可续做回滚（否决留痕不重复落），已复核**通过**则不得反向否决。
3. **`RollbackEvent.mode_after` 的例外**：漂移评估记录不执行回滚，模式如实记录当前值——模型层只对 `trigger=drift_assessment` 放开该约束，实际回滚（抽检否决/人工）仍强制 `manual`。
4. **抽检逾期的告警阈值不入 core**：`max_age_days` 由调用方给（CLI 默认 14 天）——运营节奏不该硬编码在 core（原则五），后续若需固化可入 configs。
5. **影子报告按 (周期, Agent) 分档**：同周期第二个 Agent 会撞名 → 显式 `DeploymentRecordConflictError`；多 Agent 并行影子需错开周期档或扩展命名（已写入 `shadow.py` docstring）。

**诚实边界**

- **真实 2 周影子期的运行属运营**：本特性交付机制、计时门禁与对照报告，长期数据由运营积累后供人工决策；
- "部署" = 更新部署指针（005 语义），**真实发布系统对接不在本特性**；
- 池化回放对比/无偏性/漂移证据产物由既有路径（005/011/012）产出，本特性只读与判定（不重跑回放）；
- 抽检由人工执行（CLI 通道），Web 看板仍只读、不开放抽检操作。
