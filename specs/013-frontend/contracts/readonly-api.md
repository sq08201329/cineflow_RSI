# 契约：只读查询接口与门禁（三重机检）

> 对应规格 US1 / FR-001~002、FR-008、FR-010~012。实现：`web/server.py`、`web/queries.py`。

## C1 只读路由表（机检）

- 全部路由仅 GET；**路由表断言**：扫描注册路由，无 POST/PUT/DELETE/PATCH/PATCH
- 任意路径的写请求 → 405/404，零副作用（存储层零变更断言）

### 场景

1. 枚举路由 → 全部 GET
2. POST/PUT/DELETE 到 /api/* → 405/404 + DB 零变更
3. 静态资产 GET → 200；路径穿越（`../`）→ 拒绝

## C2 只读数据源（三重机检之二、三）

- **权限断言**：集成测试以 `cineflow_web` 角色连接，对其尝试 INSERT → DB 拒绝
  （角色无写权限）；`information_schema` 断言该角色对全部表仅 SELECT
- **静态断言**：`web/` 源码扫描——无 `INSERT/UPDATE/DELETE/DROP/TRUNCATE` SQL、
  无写模式文件打开（`open(..., "w")` 仅限导出模块的导出目录）；断言集防回归
- 查询层**不重算**：读已落盘的表与报告文件（不调用评估器/合成/回放逻辑）

### 场景

1. cineflow_web 执行 INSERT → 权限拒绝
2. web/ 源码静态扫描 → 无写调用
3. 接口响应 vs 既有 JSON 报告（曲线/信度/漂移/谱系）→ 逐字段一致（抽样断言集）

## C3 部署形态

- 默认 127.0.0.1；token 配置后经 header/query 校验；未配置 token 仅本机语义
- 配置化：host/port/dsn_env/token/page_size/data_dirs/export_dir

### 场景

1. 默认启动 → bind 127.0.0.1
2. 配置 token → 无 token 请求 401；带 token 200
3. DB 不可用 → 服务照常启动，树接口 503，文件面板可用（health 报 db: down）
