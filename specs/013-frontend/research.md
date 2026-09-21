# 阶段 0 调研：前端可视化（013-frontend）

> 每个决策 = 分歧点 → 备选 → 结论 → 依据。规格：[spec.md](spec.md)（含 2026-09-21 澄清两条）。

## 决策 1：web/ 完全独立——不 import core/agents/dreaming

- **结论**：`web/` 内的查询层经**只读 SQL**（SQLAlchemy Core 引擎 + 只读角色）与
  **文件读取**（policies/history、dreaming/history、calibration/、replay/pools/）取数；
  树表 schema 以迁移为准（web/ 侧以只读 SQL 列名对接，字段语义断言锁定与 001 一致）
- **依据**：宪章 v1.1.0 原则五（前端代码独立于 core/agents/dreaming）；spec 假设
  （不引入对 core 的代码依赖）；单向依赖的物理保证 = 不存在 import。

## 决策 2：技术形态——stdlib http.server + vanilla JS（澄清 Q2 落地）

- **结论**：`web/server.py` 用标准库 `http.server.ThreadingHTTPServer`；页面为静态
  HTML + vanilla JS（fetch `/api/*`）；**图表手绘 SVG**（不引图表库）；零新增依赖
- **依据**：澄清决议；机能需求 = 只读 GET + JSON + 两页面，stdlib 全覆盖；
  零新增依赖符合全项目惯例；默认 127.0.0.1 内部工具场景下并发短板无实际风险。

## 决策 3：只读三重机检的权限层——独立只读 DB 角色

- **结论**：迁移 `0009_web_readonly_role` 创建 `cineflow_web` 角色（仅 SELECT，无
  INSERT/UPDATE/DELETE/TRUNCATE/DDL）；web 服务**必须**用该角色的 DSN（配置
  `web.dsn_env`）；三重机检 = ①路由表断言（无写动词）②集成测试断言 cineflow_web
  对全部表仅有 SELECT ③静态断言（web/ 源码 AST/扫描无写调用与写 SQL 关键字）
- **依据**：宪章"存储层触发器不认来源"——所以只读纪律要在 API 层独立证明；
  角色级权限是物理保证（即使代码写错，DB 也拒绝写）。

## 决策 4：同源校验分层（澄清 Q1 落地）

- **结论**：`web/parity.py`（测试辅助）——有报告产物的面板（进化曲线 ← dreaming/history；
  信度 ← calibration/reports；漂移 ← calibration/drift；谱系 ← 005 报表口径）逐字段比对；
  树/节点接口断言字段集与类型与 001 落库 schema 一致（无第二口径）
- **依据**：澄清决议——"同源"必须可证伪；树接口的权威源就是 DB 本身。

## 决策 5：静态导出 = 同一查询层预生成

- **结论**：`web/export.py`——用同一查询层把各面板数据预生成为 JSON 文件 +
  拷贝静态资产 → 导出目录可脱离服务直接浏览（fetch 相对路径 JSON）
- **依据**：规格 FR-009；导出与服务共用查询层，杜绝两套取数逻辑漂移。

## 决策 6：服务韧性——DB 不可用时照常启动

- **结论**：DB 连接惰性（首个树接口请求才连接）；连接失败 → 树接口 503 + 明确说明，
  文件化面板（曲线/信度/漂移/谱系）照常可用
- **依据**：规格边界情况（开发环境未起 PG 时看板不应整体不可用）。

## 决策 7：部署与访问控制

- **结论**：默认 bind 127.0.0.1；可选 token（配置 `web.token`，经 `Authorization:
  Bearer` 或 `?token=` 校验；未配置 = 仅本机访问语义）；无 HTTPS（本机回环）
- **依据**：规格 FR-011 保守部署；token 是最小充分手段（内部工具，不引认证框架）。

## 决策 8：分页与大体量

- **结论**：节点列表分页（`page`/`page_size`，page_size 配置上限）；谱系与曲线数据量
  小（轮次级），不分页；树清单按更新时间倒序分页
- **依据**：规格边界情况（万级节点不全量加载）。
