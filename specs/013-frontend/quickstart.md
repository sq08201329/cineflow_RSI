# Quickstart：前端可视化（013-frontend）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0009_web_readonly_role
uv run pytest tests/unit -k web                   # 查询层/门禁/同源
uv run pytest tests/contract -k web               # 路由表/权限/静态断言契约
uv run pytest tests/integration -k web            # 真实 PG 只读角色断言
uv run python ops/demo_web.py                     # 起服务演示（含三重机检结果输出）
uv run python -m web.export                        # 静态导出到 web/dist/
```

## 端到端场景（demo 流程）

1. **起服务**：夹具数据 + 只读角色 → 三重机检结果输出（路由表/权限/静态断言）
2. **树浏览**：三维过滤 → 节点详情（eval_breakdown 含版本）→ 谱系链路
3. **看板**：进化曲线 + 塌缩标注 + 成本汇总 + 信度/漂移徽标
4. **同源**：接口响应 vs 既有 JSON 报告逐字段一致（抽样输出）
5. **写拒绝**：POST/DELETE 全部 405/404，DB 零变更
6. **静态导出**：web/dist/ 脱离服务可浏览

## 里程碑验收（立项书周 10~11 / SC-001）

同源分层校验通过 + 只读三重机检通过 + 两视图可用；覆盖率 ≥85% 不降。

## 验证记录（2026-09-21 实跑回填）

环境：WSL2 + Python 3.11（uv）；PostgreSQL 16（`ops/dev.compose.yml`，真实容器）；
node v24（页面 DOM 冒烟用；缺失则相关用例跳过）。命令与 `.github/workflows/ci.yml` 逐字一致。

| 步骤 | 命令 | 实测结果 |
| --- | --- | --- |
| 依赖 | `uv sync` | 零新增第三方依赖（Resolved 35 packages；web 仅用 stdlib + SQLAlchemy + pyyaml） |
| 起依赖 | `docker compose -f ops/dev.compose.yml up -d --wait postgres minio` | postgres Healthy |
| 迁移 | `uv run alembic -c ops/alembic.ini upgrade head`（含 `0009_web_readonly_role`） | 0001→0009 全通；角色 `cineflow_web` 最小特权（LOGIN / NOSUPERUSER / NOCREATEDB / NOCREATEROLE） |
| 单测 | `uv run pytest tests/unit -k web` | **210 passed**（查询层 76 / 服务 45 / 迁移 19 / 树浏览器 31 / 看板 16 / 页面 22+ / 导出 20） |
| 契约 | `uv run pytest tests/contract -k web` | **57 passed**（只读三重机检 38 + 同源分层 19；含扫描器合成违规自检） |
| 集成 | `uv run pytest tests/integration -k web -m integration` | **14 passed**（真实 PG：对全部表仅 SELECT（information_schema 逐表）/ INSERT·UPDATE·DELETE·建表被 DB 拒绝 / 未授权系统表不可读 / 未来表默认只读 / 只读角色真实读树与谱系） |
| 集成全量 | `uv run pytest tests/integration -m integration` | **88 passed**（16m24s，含 013 新增 14 条；既有集成套件无回归） |
| 演示 | `uv run python ops/demo_web.py` | **退出码 0**（约 2.1s）：①三重机检结果（路由表 14 条全 GET、迁移 0009 授权纪律四项、源码静态断言零违规）②树浏览③看板④同源四面板差异 0⑤写拒绝 405/404 且树库 8→8 零变更⑥导出 13 文件 → 停服务后纯静态服务器仍读出全部数据 |
| 导出 | `uv run python -m web.export` | 导出 `web/dist/`（13 文件：两页面 + static 资产 + data/8 份快照 + manifest）；无 DSN 时降级并在 manifest 标注 `db=down`（树快照为空，文件面板照常） |
| 本地门禁 | `uv run ruff check .` / `uv run ruff format --check .` | 双绿（401 文件） |
| 全量单测+契约 | `uv run pytest tests/unit tests/contract` | **2628 passed, 56 skipped**（4m34s；跳过项为需 Docker/凭证的既有用例） |
| 覆盖率（CI 口径） | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-report=term --cov-fail-under=85` | **93.55%**（TOTAL 9772 行） |
| 覆盖率（含 web 口径，T1322） | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term --cov-fail-under=85` | **92.22%** ≥85%；`web/` 分模块：queries 91% / server 89% / export 83% / parity 0%（parity 由契约测试与 demo 覆盖） |
| 覆盖率（unit+contract，含 web） | `uv run pytest tests/unit tests/contract --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term` | **94%**（TOTAL 10691 行）；`web/` 分模块：queries 91% / parity 93% / server 89% / export 85% |
| 收官复核（接手复跑） | 同上游命令 | `unit+contract -k web` **267 passed**；`tests/unit` 全量 **2419 passed**；`integration -k web` **14 passed**；demo 退出码 0（1.78s）；覆盖率（含 web）**TOTAL 92%** gate 通过 |
| CI 口径同步（T1322 补） | `.github/workflows/ci.yml` | 覆盖率命令补 `--cov=web`（与 `pyproject [tool.coverage.run].source` 一致——否则"web 计入覆盖率"只在本地成立） |

**已知 flake（非本特性）**：`test_visual_consistency` / `test_visual_flicker` 在覆盖率插桩 + 高负载下偶发失败，隔离复跑必过（004 视觉线既有问题，010/006 记录同源）。

### SC 机检口径摘要

- **SC-001（里程碑验收，同源分层）**：有报告产物的四面板（进化曲线 ↔ 005 `build_curve`、
  信度 ↔ 010 报告、漂移 ↔ 012 报表、谱系 ↔ 005 `build_lineage`）逐字段一致（差异 0，
  demo 第 4 步输出 + `tests/contract/test_web_parity.py` 19 条）；树/节点接口以 001 落库为
  权威源（逐列对齐断言）。
- **SC-002（写端点 0 / 写请求 100% 被拒且存储层零变更）**：路由表 14 条全 GET（机检）；
  POST/PUT/DELETE/PATCH 命中只读路径 → 405、未注册路径 → 404，树库行数与产物指纹前后一致
  （契约测试 3 条 + demo 第 5 步）。
- **SC-003（树浏览字段齐全/空态/分页）**：三维过滤各自生效与组合、页长上限封顶、逐页无
  重复无遗漏、越界页空页、空态 `items=[]`、详情字段齐全（含 `evaluator_id@version`）——
  31 条浏览器契约测试 + 45 条服务测试。
- **SC-004（曲线与成本对账）**：逐轮 reward 与轮次报告逐字段一致；成本按 (Agent, ISO 周)
  合计与 core 侧 CostRecord 独立聚合逐项相等（16 条看板测试）。
- **SC-005（静态导出可离线浏览）**：导出目录在**纯静态文件服务（无任何 API）**下两视图
  仍取数渲染（node DOM 桩静态模式冒烟）+ 导出 JSON 与 API 响应逐字段一致（20 条导出测试）。
- **SC-006（DB 不可用韧性）**：服务照常启动、树接口 503 + 说明、文件面板与静态资产可用、
  `health` 报 `db: down`（服务测试 2 条 + 契约测试 1 条 + 导出降级 1 条）。
- **SC-007（覆盖率）**：见上表，含 web 口径 92.22% ≥85%（core+agents+dreaming 口径 93.55%）。

### 实测发现（本批修正）

1. **PG 工具语句不接受绑定参数**：`ALTER ROLE ... PASSWORD $1` 在真实 PG 报
   `syntax error at or near "$1"` → 迁移 0009 改为客户端单引号加倍转义（口令仍不落盘）。
2. **PG 未类型化 NULL 参数歧义**：恒真式过滤（`:x IS NULL OR col = :x`）报
   `AmbiguousParameter` → 查询层改为按实际过滤条件拼装 WHERE（两种方言一致）。
3. **页面冒烟超时判据必须用单调时钟**：WSL2 墙钟跳变（实测 ~34.6s）会让 `Date.now()`
   判据瞬间超时（轮询仅 1 次即退出），造成"页面正在正常渲染却被判超时"的假失败 →
   改 `process.hrtime`。
4. **测试不得占用配置端口**：活服务夹具原先直接绑 8080，一旦本地正跑着只读服务
   （本特性启用后的常态）即整批 `Address already in use` → 夹具一律用临时端口。
5. **`configs/movie.yaml` 的 `web` 段必须放在 `deployment` 段之前**：`core/yaml_edit.py`
   定点补全不穿越其后的段，段尾追加会改变既有改写的插入位点（既有 approve 测试变红）。

### 诚实边界

- 页面渲染自动化覆盖"能取数、能渲染"（node + DOM 桩真实执行两视图）；**视觉观感与交互
  细节未自动化**（无浏览器环境），需人工 `uv run python -m web.server` 打开两页查看。
- 权限行为断言依赖真实 PG（`-m integration`）；无 Docker 环境自动跳过（单测只做 SQL 文本
  与迁移纪律断言，不假装验证了 DB 权限）。
- 导出快照的节点详情默认上限 2000（`--max-nodes` 可调），超出在 manifest 如实列出
  `truncated`；超大库请用在线服务。
- 看板成本周期为节点 `created_at` 换算的 ISO 周（UTC），与 010/012 的周期标签同形。
