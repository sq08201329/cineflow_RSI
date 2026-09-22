# 契约：就绪核查器 / 冒烟器 / 清单同步

> 对应规格 US3 / FR-006~009。实现：`ops/check_credentials.py`、`ops/smoke_llm.py`、
> `docs/pilot-upgrade-manifest.json` 与机检锁。

## C8 就绪矩阵（以配置档案为权威）

- 核查器读配置 `llm.profiles` → 每档案：声明变量是否设置 / 设置了但连不通（沿用既有探测分型）
- 报告标注"变量名 + 所属档案 + 用途"；**环境中的无关 `OPENAI_*` 不再被隐式采纳**
  （仅当某档案显式声明该变量名时才算，并标注"沿用旧变量名"）
- 退出码沿用 0/1/2

### 场景

1. 档案声明 `CINEFLOW_LLM_API_KEY` 且已设置 → 就绪；环境另有的 `OPENAI_API_KEY` 不影响判定
2. 未设置 / 连不通 → 分型正确（unset vs unreachable）
3. 某档案显式声明 `OPENAI_API_KEY` → 就绪且报告标注"沿用旧变量名（建议改中立名）"

## C9 冒烟器按档案/角色

- `ops/smoke_llm.py` 支持 `--profile`（保留 `--model` 作为等价别名或映射到档案）
- `--round` 模式：改写配置里的**角色映射**（如把 `judge` 指向指定档案）而非散落的模型名
- 凭证缺失即退出码 1 并指向核查器

### 场景

1. `--profile deepseek-flash` → 用该档案调用，打印 tokens/成本/档案 id
2. `--profile` 不存在 → 报错列出可用档案
3. 缺凭证 → 退出码 1 + 提示

## C10 清单一致性机检锁

- `docs/pilot-upgrade-manifest.json` 登记的变量名与配置档案声明的变量名**逐项一致**（漂移即红）
- 清单记录档案 id → 变量 → 用途；`schema_version` 递增
- 机检失败信息必须列出两侧差异（可诊断）

### 场景

1. 一致 → 通过
2. 改清单一个变量名 → 红（列出差异）
3. 配置加档案但未登记 → 红
