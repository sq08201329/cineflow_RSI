# Quickstart：LLM 模型档案与角色路由（016-llm-model-routing）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "llm_profile or routing or gateway"     # 解析/校验/迁移/路由/折算
uv run pytest tests/contract -k llm                                  # 契约 C1~C10
uv run python ops/check_credentials.py                               # 档案化就绪矩阵（零网络）
uv run python ops/smoke_llm.py --profile deepseek-flash              # 按档案冒烟（需凭证）
uv run python ops/smoke_llm.py --config configs/movie.yaml --round --dry-run   # 装配校验（零花费）
```

## 端到端场景（demo 流程）

1. **档案解析**：单档案（现状等价）/ 多档案 / 缺价目报错 / 零价目需显式声明
2. **角色路由**：`judge` → 强模型、`dreaming_candidates` → 本地模型；未映射回默认；枚举外报错
3. **成本分解**：多档案一轮 → 账目分解到"角色 × 档案"，金额与各自价目相符
4. **凭证中立**：环境存在无关 `OPENAI_API_KEY` 时，不参与任何档案就绪判定
5. **迁移**：旧扁平配置 → 自动映射单档案 + 启动报告列出映射规则
6. **冻结可证伪**：改配置价目后重跑审计 → 历史节点成本不变

## 里程碑验收（SC-001~007）

换厂商 = 仅改配置（静态断言零厂商字面量）+ 历史成本不漂移 + 缺项 100% 报错 +
凭证假阳性归零 + 单档案等价现状（既有测试全绿）+ 机检锁可证伪；覆盖率 ≥85% 不降。

## 验证记录（2026-09-22 实测，命令与 ci.yml 口径一致）

| 命令 | 实测结果 |
| --- | --- |
| `uv run pytest tests/unit -k "llm_profile or routing or gateway"` | 全部通过（档案解析/迁移/路由/快照/冻结/成本分解/注入；见下明细） |
| `uv run pytest tests/contract -k llm` | **54 passed**（C1~C10 聚合 + SC-001~006 机检） |
| `uv run python ops/check_credentials.py`（真实环境、零网络） | deepseek-flash `ok` + 标注"沿用旧变量名（建议改中立名）"；local-qwen `missing`（LOCAL_LLM_* 未设置）；`ignored_environ` 列出 4 个未被任何档案声明的 `*_API_KEY`（含同厂商 `DEEPSEEK_API_KEY`）→ **假阳性归零**；A 可跑 / B 缺 13 / C 缺 2，**退出码 1** |
| `uv run python ops/smoke_llm.py --profile deepseek-flash`（无凭证环境） | `reason=credentials_missing`、`missing=["OPENAI_API_KEY"]`、`profile_id=deepseek-flash`、提示指向 `ops/check_credentials.py`，**退出码 1** |
| `uv run python ops/smoke_llm.py --config configs/movie.yaml --round --dry-run` | 最小档六阶段全 `done`（`--round` 改写 `llm.roles` 而非散落模型名；LLM 走 mock，零真实调用） |

明细（本特性新增/扩展的单测）：`test_llm_profiles.py` 15、`test_llm_legacy_migration.py` 6、
`test_llm_routing.py` 17、`test_llm_profile_snapshot.py` 5、`test_llm_price_freeze.py` 3、
`test_gateway_routing.py` 12、`test_gateway_cost_breakdown.py` 7、`test_no_vendor_literals.py` 5、
`test_http_backend_injection.py` 10、`test_check_credentials_profiles.py` 7、
`test_smoke_llm_profile.py` 8、`test_upgrade_manifest_lock.py` 6；
契约 `test_llm_profile_contracts.py` 54 条（C1~C10 + SC 机检）。

**改坏即红实证**（本仓已跑并还原）：业务代码塞模型名字面量 → SC-001 红；
摘掉某个调用点的 `role=` → 静态断言红；清单少登记一个变量 → C10 锁红（列出差异）；
`fetch_metrics` 改回返 0（US1 期）→ 宣发契约组 9 条红。

**诚实边界**：以上全部为**本地 stub / 无凭证环境**的验证——真实厂商 API、真实凭证、
真实账单均未跑过（需要运营侧凭证与厂商 API 校准）。
