# 契约：进化曲线看板与静态导出

> 对应规格 US3 / FR-005~006、FR-009。实现：`web/queries.py` + `web/static/board.html` +
> `web/export.py`。

## C7 进化曲线与摘要

- 逐轮 reward（按 Agent 分线，读 dreaming/history 轮次文件）；塌缩标注（005 口径）；
  成本汇总（按 Agent/周期，与 CostRecord 对账一致——读树库聚合，不重算口径）
- 摘要：010 信度报告达标状态 + 012 漂移徽标（normal/suspect/confirmed_drift）；
  缺失如实空态

### 场景

1. 多轮数据 → 曲线序列与 dreaming/history 逐字段一致
2. 塌缩轮次 → 标注存在
3. 成本汇总 → 与 CostRecord 聚合对账一致
4. 无信度/漂移数据 → 摘要空态如实

## C8 静态导出

```
web/export.py → web/dist/（静态资产 + data/*.json 预生成）
```

- 导出与服务**同一查询层**（导出 = 查询产出的 JSON 写文件）
- 导出目录可脱离服务浏览（fetch 相对路径 data/*.json）

### 场景

1. 导出后断服务 → 导出目录两视图可浏览（数据完整）
2. 导出 JSON 与服务 API 响应逐字段一致（同一查询层的机检证据）

## C9 看板页面冒烟

- board.html 可服务；含曲线/成本/摘要区标记；图表为 SVG（无外部库引用——静态断言）
