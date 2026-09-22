# 契约：档案解析、校验与迁移

> 对应规格 US1 / FR-001~002、FR-010~011。实现：`core/llm_gateway/profiles.py`。

## C1 档案解析与校验

```
load_profiles(config: dict) -> (profiles: dict[str, ModelProfile], routing: RoleRouting, notes: list[str])
```

- 每条档案必须含：`base_url` **或** `base_url_env`；`api_key_env`；`prices` 两值；
  `price_note` 建议必填（缺则告警入 notes）
- **缺端点 / 缺凭证变量名 / 缺价目 → 报错**（不静默零成本、不回落默认价）
- `prices` 允许全 0，但必须 `zero_marginal: true`（否则报错：零价目必须显式声明）
- 端点若为 URL：快照只记 host；若为 env 名：快照记变量名

### 场景

1. 单档案合法 → 解析成功 + notes 空
2. 多档案各自独立（价目/端点/凭证名互不串用）
3. 缺价目 / 缺端点 / 缺 `api_key_env` → 报错（三类各一条）
4. `prices` 全 0 且未声明 `zero_marginal` → 报错；声明后通过且快照标注零边际成本
5. `price_note` 缺省 → 通过但 notes 含告警

## C2 默认档案与角色映射校验

```
resolve_routing(profiles, raw_roles, default_profile) -> RoleRouting
```

- 档案数 = 1 → 默认档案自动认定（notes 标注"自动认定：唯一档案"）
- 档案数 ≥ 2 且未声明默认 → 报错
- 角色名不在枚举内 → 报错（防"judge 拼错静默降级"）
- 映射值指向不存在档案 → 报错；多层/自指 → 报错
- 未被任何角色引用的档案 → 允许，notes 列出（提示可能笔误）

### 场景

1. 单档案 + 无默认声明 → 通过 + notes"自动认定"
2. 双档案 + 无默认声明 → 报错
3. 角色枚举外（如 `judeg`）→ 报错并给出合法枚举列表
4. 映射指向不存在档案 / 多层 → 报错
5. 未引用档案 `orphan` → 通过 + notes 列出

## C3 旧扁平配置迁移

```
migrate_legacy(config) -> (profiles, routing, migration_notes)
```

- 检测旧写法（`<agent>.model` + `<agent>.model_prices`）→ 映射为单档案
- 映射规则与结果写入 `migration_notes`（启动报告与快照可见）
- 新旧并存 → 以新写法为准（旧键忽略），notes 标注"旧键被忽略"

### 场景

1. 仅旧写法 → 单档案 + 映射说明（档案 id = 模型名）
2. 新旧并存 → 用新写法 + notes"旧键被忽略"
3. 旧写法缺价目 → 报错（与 C1 同纪律）
