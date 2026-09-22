# 数据模型：LLM 模型档案与角色路由（016-llm-model-routing）

> 无新 DB 表——档案/映射是**配置**，快照随树的 `config_snapshot` 冻结；成本分解在网关
> 内存累积并由报告层呈现。frozen dataclass 为第一道工序。

## 领域模型（`core/llm_gateway/`）

- **ModelProfile（模型档案）**：`profile_id` / `base_url`（或 `base_url_env`）/
  `api_key_env` / `prices`（`prompt_per_1k`、`completion_per_1k`）/ `price_note`
  （价目口径备注，如"峰时缓存未命中上限"）/ `zero_marginal`（零价目标记）/
  `legacy_env`（是否沿用旧变量名，用于报告标注）
- **RoleRouting（角色映射）**：`roles: dict[Role, profile_id]`（单层）/
  `default_profile: str | None`（单档案可省略，自动认定并标注）
- **Role（角色枚举）**：`generation` / `judge` / `dreaming_candidates` / `copywriting`
  （plan 阶段按既有调用点核对后定稿）
- **RouteDecision（路由决策）**：`role` / `profile_id` / `reason`（`role_mapping` |
  `default_fallback`）/ `profile_snapshot_ref`
- **ProfileSnapshot（档案快照）**：档案集合（含价目与备注）/ 默认档案与认定方式 /
  端点 host（**不含密钥**）/ 迁移映射说明（旧扁平配置 → 档案）——并入树的
  `config_snapshot["llm_profiles"]`
- **CostEntry / CostBreakdown**：`role` → `profile_id` → `{calls, prompt_tokens,
  completion_tokens, cost_usd}`；网关累积、报告层呈现
- **CredentialItem（凭证就绪项）**：`profile_id` / `variable` / `status`
  （`unset` / `set` / `unreachable`）/ `purpose`——核查器输出

## 配置 schema（`configs/*.yaml` 新增 `llm` 段）

```yaml
llm:
  profiles:
    deepseek-flash:
      base_url: https://api.deepseek.com
      api_key_env: CINEFLOW_DEEPSEEK_API_KEY
      prices: {prompt_per_1k: 0.0003, completion_per_1k: 0.0012}
      price_note: "峰时缓存未命中上限（保守高估；缓存命中与错峰更低）"
    local-qwen:
      base_url_env: CINEFLOW_LOCAL_LLM_BASE_URL
      api_key_env: CINEFLOW_LOCAL_LLM_API_KEY
      prices: {prompt_per_1k: 0.0, completion_per_1k: 0.0}
      zero_marginal: true          # 自建：零边际成本（仍非免费）
  roles:
    generation: deepseek-flash
    judge: deepseek-flash
    dreaming_candidates: local-qwen
  default_profile: deepseek-flash  # 单档案时可省略
```

**沿用旧写法**（`screenplay.model` + `model_prices`）→ 自动映射为单档案，映射结果在启动
报告与 `llm_profiles` 快照中列出。

## 状态与时序

- 初始化：解析 `llm` 段 → 校验（枚举角色、档案存在、价目齐全、默认档案规则）→
  构建 `ProfileSnapshot` → 装配后端（mock/http 由既有 `pilot.llm_backend` 选择）
- 一次调用：`role` → `RouteDecision` → 档案后端 → 结果带 `role`/`profile_id` →
  网关累积 `CostBreakdown` → 价目按档案折算
- 快照：各 Agent 构造 `config_snapshot` 时并入 `llm_profiles`（历史口径可复现）
