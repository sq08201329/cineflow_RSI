# 契约：网关按角色路由、成本折算与分解

> 对应规格 US2 / FR-004~005、FR-012。实现：`core/llm_gateway/gateway.py` + `routing.py`。

## C4 路由决策

```
route(role: Role, routing: RoleRouting) -> RouteDecision
```

- 命中角色映射 → `reason=role_mapping`；未映射（枚举内）→ 默认档案 `reason=default_fallback`
- 决策携带 `profile_snapshot_ref`（可追溯）

### 场景

1. `judge` 映射到 `deepseek-pro` → 命中映射
2. `copywriting` 未映射 → 回落默认档案 + 原因标注
3. 角色不在枚举 → 报错（构造期即拦截，非运行期）

## C5 后端选择与调用

- 后端由档案参数构造（`base_url`/`api_key_env` 解析后的端点与密钥）；`pilot.llm_backend`
  仍决定 mock/http（档案只在 http 路径生效，mock 路径用档案价目记账以保持口径一致）
- **HttpBackend 不再隐式读 `OPENAI_*`**；端点/密钥由路由层注入
- 错误分型不变（瞬态可重试 / 永久失败）；**禁止**静默回落到其它档案

### 场景

1. 两档案分别调用 → 各自命中端点与价目（stub 验证）
2. 档案端点 5xx → 瞬态错误（网关重试策略不变）；4xx → 永久错误
3. 档案不可用时不尝试其它档案（无静默回落）

## C6 成本折算与分解

- 折算：`cost = prompt_tokens/1k × profile.prices.prompt_per_1k + completion_tokens/1k × profile.prices.completion_per_1k`
- `BackendResult` 携带 `role` / `profile_id`；网关累积
  `cost_breakdown() -> {role: {profile_id: {calls, prompt_tokens, completion_tokens, cost_usd}}}`
- 树节点 `CostRecord` 口径不变（总成本照旧入账）

### 场景

1. 两档案各一次调用 → 分解含两条目，金额与各自价目相符
2. 同档案多角色 → 按角色分开、档案可合并展示但来源可追溯
3. 零价目档案 → 成本 0.0 且报告标注"零边际成本（自建）"

## C7 快照冻结与审计

- `profile_snapshot()` → `ProfileSnapshot`（档案 + 价目 + 备注 + 端点 host + 迁移说明，无密钥）
- 各 Agent 构造 `config_snapshot` 时并入 `llm_profiles`
- **改价目后历史成本不变**：审计复算历史节点成本 == 快照价目折算值（断言）

### 场景

1. 运行落树 → 快照含 `llm_profiles`（价目与备注齐全、无密钥）
2. 修改配置价目 → 历史节点成本与快照一致（不漂移）；新节点用新价目
