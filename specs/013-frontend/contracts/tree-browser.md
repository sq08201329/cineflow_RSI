# 契约：发现树浏览器（查询/详情/谱系）

> 对应规格 US2 / FR-003~004。实现：`web/queries.py` + `web/static/index.html` + `app.js`。

## C4 树清单与节点浏览

- 三维过滤（project/agent/policy_version，均可选且可组合）；分页（page/page_size，
  page_size ≤ 配置上限）；按 created_at 倒序
- 节点详情：prompt 摘要、observation_keys、工件引用（哈希 + 元信息键）、
  eval_breakdown（分量含 `evaluator_id@version`）、成本、状态

### 场景

1. 三维过滤各自生效（夹具树：不同项目/Agent/策略版本）
2. 节点详情字段齐全（含评估器版本标注）
3. 空项目 → 空态（items=[] + total=0，不报错）
4. 分页边界：>page_size 时分页无重复无遗漏

## C5 谱系链路

- policy_version → parents / trees（project_id 标注）/ children（跨项目归属，011 口径）
- 不存在版本 → 404 + 说明

### 场景

1. 跨项目谱系 → 归属标注齐全
2. 单项目版本 → 跨项目字段为空列表（非缺失）
3. 不存在版本 → 404

## C6 页面冒烟

- index.html 可服务；含三维过滤控件与节点表标记；app.js 请求路径与 API 路由一致
  （静态断言：JS 中的 fetch 路径都在路由表内）
