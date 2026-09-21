# 数据模型：前端可视化（013-frontend）

> 本特性**无新数据产生**——全部是读路径。唯一 DB 变更 = 迁移 0009 的只读角色
> `cineflow_web`（权限层，非数据层）。

## API 响应 schema（`web/queries.py` 产出，全部 GET）

- **树清单** `GET /api/trees?project=&agent=&policy_version=&page=` →
  `{items: [{tree_id, project_id, agent_id, policy_version, node_count, created_at, form}],
    page, page_size, total}`——三维过滤（均可选）
- **节点列表** `GET /api/trees/{tree_id}/nodes?depth=&page=` →
  `{items: [{node_id, parent_id, depth, score, cost_usd, status, created_at}], page, page_size, total}`
- **节点详情** `GET /api/nodes/{node_id}` →
  `{node_id, tree_id, parent_id, depth, prompt, observation_keys, artifact: {hash, metadata_keys},
    eval_breakdown: [{evaluator_key, score, diagnostics_keys}], score, cost_usd, status, created_at}`
  ——eval_breakdown 携带评估器版本（`evaluator_id@version`）；工件只给哈希与元信息键
- **谱系链路** `GET /api/lineage/{policy_version}` →
  `{policy_version, parents: [...], trees: [{tree_id, project_id}], children: [{version, project_id}]}`
  （跨项目归属标注，011 口径）
- **进化曲线** `GET /api/evolution/{agent_id}` →
  `{rounds: [{round_id, best_reward, collapse_flag, cost_usd}], series_by_evaluator? }`
  （dreaming/history 口径；塌缩标注沿用 005）
- **摘要** `GET /api/summary` →
  `{calibration: {period, agents: [...], target, meets}, drift: {items: [{evaluator_key, status,
    since}], alerts: [...]}}`（010/012 产物；缺失如实空态）
- **健康** `GET /api/health` → `{ok, db: "up"|"down", files: "ok"}`

## DB：迁移 0009_web_readonly_role（权限层）

- 创建角色 `cineflow_web`（LOGIN，口令经环境变量）；`GRANT SELECT ON ALL TABLES`；
  `REVOKE` 写权限显式；默认拒绝未来表（ALTER DEFAULT PRIVILEGES 只 SELECT）
- **web 服务只允许用该角色连接**（配置 `web.dsn_env`，缺省拒绝启动树接口）

## 配置（configs `web` 段）

```yaml
web:
  host: 127.0.0.1
  port: 8080
  dsn_env: CINEFLOW_WEB_DSN        # 只读角色 DSN 的环境变量名
  token: ""                        # 空 = 仅本机语义
  page_size: 50
  data_dirs: {policies: policies/history, dreaming: dreaming/history,
              calibration: calibration, pools: replay/pools}
  export_dir: web/dist
```

## 静态资产与导出

- `web/static/`：index.html（树浏览器）、board.html（进化看板）、app.js、style.css——
  vanilla JS + 手绘 SVG 图表
- 导出：`web/export.py` 产 `web/dist/`（静态资产 + `data/*.json` 预生成）——
  与服务同一查询层
