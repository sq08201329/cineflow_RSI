# 实现计划：角色 × 档案成本分解（022）

**Branch**: `022-role-profile-cost-breakdown` | **Date**: 2026-09-26 | **Spec**: [spec.md](spec.md)

> 按 AGENTS.md §2.1 省法：决策、数据形态、契约修订并为本文件小节，不单列 research/data-model/contracts/quickstart。

## Summary

为 `CostRecord` 增列 LLM 腿「角色 × 档案」分解字段（按扩展更新），写入侧由调用点手上的 `LLMResult`（已携带 `role`/`profile_id`/`usage`/`cost_usd`/`cached`）直接归集，报告层（`web/queries.py`）新增分解分组维度；同步修订 016 契约 C6 表述与宪章原则二字段口径。历史行零回改、缺键即「未标定」。

## Technical Context

**Language/Version**: Python（`.python-version`，uv 管理） | **Storage**: SQLAlchemy JSON 列 `tree_nodes.cost`（SQLite 文本 / PG jsonb），**零 DB 迁移**（纯 JSON 键新增） | **Testing**: pytest（unit/contract/integration/adversarial/unbiasedness）+ ruff | **约束**: 五件套逐字节比对面不含 `CostRecord` 序列化（已核实，FR-008 机检）；INSERT-only 触发器不受影响（无 UPDATE）

## Constitution Check

| 原则 | 落点 | 判定 |
| --- | --- | --- |
| 二 节点不可变与成本 | 仅增列扩展字段，无 UPDATE/DELETE；历史行零回改；FAILED 节点照计入账（含归属） | ✅（字段口径增补进原则二，见 §契约修订） |
| 三 昂贵动作仅限线上 | 零新增 LLM 调用面，纯记账口径 | ✅ |
| 五 单向依赖 | `core/tree` **不** import `core/llm_gateway`（校验只做结构校验，不耦合 Role 枚举；归属字符串由调用侧传入） | ✅ |
| 六 诚实边界 | 历史分解标「未标定」禁回填；缓存命中与「未标定」在原始 JSON 层可区分（见 D3） | ✅ |

无违规，无需 Complexity Tracking。

## 设计决策（并入本节的 research 结论）

- **D1 表示形态**：`CostRecord` 新增 `llm_breakdown: dict = field(default_factory=dict)`，形态对齐网关 C6 口径——`{role: {profile_id: {calls, prompt_tokens, completion_tokens, cost_usd}}}`。`asdict` / JSON 原生可读回；与网关 `cost_breakdown()` 可逐格机检（SC-002）。
- **D2 校验口径**（`__post_init__`，结构校验不耦合 Role 枚举）：各数值 ≥ 0、键为非空字符串；**Σ calls ≤ llm_calls、Σ tokens ≤ llm_tokens**（不超即合法；等号在无缓存命中时成立）。**不校验 cost_usd 总额**——六字段中无对应标量（网关折算与投放花费同入 `generation_api_cost_usd`），如实登记此口径局限。空 breakdown 合法（历史行 / 纯缓存命中 / 无 LLM 节点）。
- **D3 缓存命中与「未标定」的区分**：命中本地缓存的调用（`LLMResult.cached=True`）与网关 `_breakdown` 同口径**不进分解**（其 tokens/cost 仍按现状入六字段）。展示层读**原始 JSON**：键缺席 ⇒ 「未标定」（历史行）；键在场且空且 llm_calls>0 ⇒ 「缓存命中（零计费）」；键在场非空 ⇒ 正常分解。三者不得混标。
- **D4 写入侧归集**：新设 `core/tree/attribution.py` 薄工具（纯函数，零网关依赖）——`add_call(breakdown, *, role, profile_id, prompt_tokens, completion_tokens, cost_usd)`（role/profile_id 空串即 `ValidationError`，落实 FR-004）；`merge(a, b)`（同键累加，供聚合一节点多次调用与 `ReplayTrajectory.total_cost` 汇总）。各 loop 在现有组 `cost` dict / `CostRecord` 的 ~45 处构造点旁，用同一批 `LLMResult` 调 `add_call`（`cached=True` 跳过）。**失败/预估节点**（如 `agents/screenplay/loop.py:766` 的 estimated 入账）：按"拟走角色 × 档案"记录 `calls=1, tokens=0, cost_usd=已发生/预估额`（profile_id 取 `gateway.route(role)` 的判定值），口径备注如实登记。
- **D5 消费面**：仅 `web/queries.py` 成本查询新增 `group_by=role_profile` 维度（读 `tree_nodes.cost` 原始 JSON，按 D3 三态分组；「未标定」单列不摊入）。**不动** `cost.json` 五件套（018 契约冻结面）、不动 ledger stage 对账、不动 `ops/cost_regression.py`（按键取值不受影响）。
- **D6 规格假设闭环**：spec「假设」中"网关采样边界"问题不成立——`LLMResult` 逐调用携带归属，归集发生在调用点，无边界对齐问题。

## 契约修订（并入本节）

1. **016 契约 C6**（`specs/016-llm-model-routing/contracts/gateway-routing.md:33-38`）：仅替换末句"树节点 `CostRecord` 口径不变（总成本照旧入账）"→"树节点 `CostRecord` 六字段口径不变（总成本照旧入账）；LLM 腿（角色 × 档案）分解为 022 新增扩展字段，口径与本条 `cost_breakdown()` 一致"。无其他删改。
2. **宪章原则二**（`.specify/memory/constitution.md:47-57`）：`CostRecord` 条目后增补一句扩展字段口径（增句不删改；版本与日期按治理规则登记）。
3. **网关 docstring**（`core/llm_gateway/gateway.py:392-393`）"树节点 `CostRecord` 口径不变（分解只在报告层呈现）"同步改为新口径（过时段落随机制提交一并更新）。

## Project Structure

```text
core/tree/
├── models.py            # CostRecord 增列 llm_breakdown + D2 校验
├── attribution.py       # 新增：add_call / merge 薄工具
└── store.py             # 零改动（CostRecord(**row.cost) 自动兼容）
agents/*/loop.py 等      # ~45 处构造点接入 add_call（screenplay/promo/sound/visual/
                         #   storyboard/editing/dev/pilot + promo/ingest.py）
core/replay/trajectory.py# total_cost 汇总接入 merge
web/queries.py           # get_costs 新增 role_profile 分组（D3 三态）
specs/016-llm-model-routing/contracts/gateway-routing.md  # C6 末句替换
.specify/memory/constitution.md                          # 原则二增句
core/llm_gateway/gateway.py                              # docstring 口径更新
tests/unit/              # 模型校验/读回兼容/归集/合并/查询分组
tests/integration/       # 固定种子多 Agent 运行：逐节点 Σ 一致 + 分组==网关分解
```

## 机检与门禁（收口必跑，串行）

- 新增常驻断言：D2 校验单测（含负值/超和拒绝）、旧行读回（缺键 ⇒ 默认 + 「未标定」三态）、`merge` 可结合性、固定种子运行逐节点 Σ ≤ 总量且与网关 `cost_breakdown()` 逐格一致（SC-001/002）、五件套逐字节比对既有测试全绿（FR-008）。
- 既有门禁：覆盖率 ≥85%（`--cov=core --cov=agents --cov=dreaming --cov=web`）、契约两腿、集成 / 对抗 / 无偏性（τ≥0.95）、ruff check + format——零删除零放宽。
- 提交分批（§3）：**① 机制提交**（models.py + attribution.py + gateway docstring + 其单测）单独入库 → 以该提交为基线 → **② 接入提交**（loops / trajectory / web / 契约 / 宪章 / 集成测试）。
