# 实现计划：前端可视化（发现树浏览器 + 进化曲线看板）

**分支**: `013-frontend` | **日期**: 2026-09-21 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/013-frontend/spec.md` 的功能规格说明（含 2026-09-21 澄清会话两条决议）

## 概要

交付 `web/` 独立目录（宪章唯一例外）：stdlib http.server 只读服务（仅 GET 路由 +
只读 DB 角色 + 静态断言三重机检）、独立查询层（只读 SQL + 文件化产物读取，
**不 import core/agents/dreaming**）、两个 vanilla JS 页面（树浏览器 + 进化看板，
图表手绘 SVG）、静态导出（同一查询层预生成 JSON）。澄清决议贯穿：**同源校验分层**
（有报告产物的面板逐字段机检、树接口以 DB 为权威源）、**零新增依赖**（标准库服务 +
无框架静态页）。唯一 DB 变更 = 迁移 0009 的只读角色 `cineflow_web`。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）+ vanilla JS（无构建链）

**主要依赖**: 零新增——Python 标准库（http.server/json/urllib）；SQLAlchemy Core
（既有，只读引擎）；yaml（读 configs）；图表手绘 SVG（无图表库）

**存储**: 唯一变更 = 迁移 `0009_web_readonly_role`（只读角色 `cineflow_web`：全表仅
SELECT、显式 REVOKE 写、默认权限只 SELECT）；数据读路径 = 树库只读 SQL + 文件化产物
（policies/history、dreaming/history、calibration/、replay/pools/）只读读取

**测试**: pytest；路由表机检（无写动词）、权限断言（集成，真实 PG 只读角色写拒绝）、
静态断言（web/ 源码无写调用）、同源抽样（接口 vs JSON 报告逐字段）、页面冒烟（资产
可服务 + fetch 路径在路由表内 + 无外部库引用）

**目标平台**: Linux（WSL2 + CI）；页面在常规浏览器打开

**项目类型**: `web/` 独立目录（宪章唯一例外；不 import core/agents/dreaming）

**性能目标**: 内部工具——单页节点列表（≤ page_size）< 200ms 本地；无高并发需求

**约束**: 只读三重机检（原则五新条款 + 原则二"触发器不认来源"）；不另建数据通道/
不重算口径；零新增依赖（YAGNI）；默认 127.0.0.1 + 可选 token；覆盖率 ≥85%
（web/ 接口层计入口径）

**规模/范围**: web/（server/queries/export/parity + 静态资产）+ 迁移 0009 + demo；
不含工件媒体播放、写操作入口（录入/审批/部署永远走 CLI）、登录/多租户、HTTPS

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：版本冻结 | 只读展示评估器版本（eval_breakdown 携带 `id@version`）；无任何口径重算 | ✅ 满足 |
| 原则二：不可变与谱系 | **只读角色物理保证**（cineflow_web 仅 SELECT）+ 三重机检；谱系只读查询 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 前端零生成/零渲染（不触碰任何执行路径）；读已落盘产物 | ✅ 满足 |
| 原则四：沙箱与前缀 | 无策略执行；观测字段经既有白名单投影口径展示（observation_keys 而非内部态） | ✅ 满足 |
| 原则五：单向依赖与配置化 | **web/ 独立目录，零 import core/agents/dreaming**；端口/路径/token 全配置 | ✅ 满足（新条款的工程落点） |
| 原则六：诚实边界 | 看板无操作入口（录入/审批/部署留 CLI）；缺失数据如实空态 | ✅ 满足 |
| 测试纪律 | TDD；三重机检为契约测试；覆盖率 ≥85%（web/ 接口层计入） | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/013-frontend/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── readonly-api.md    # 只读路由/三重机检/部署形态（C1~C3）
│   ├── tree-browser.md    # 树浏览查询/详情/谱系（C4~C6）
│   └── board-export.md    # 看板/摘要/静态导出（C7~C9）
└── tasks.md               # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，新增 web/ 目录）

```text
web/
├── server.py            # stdlib ThreadingHTTPServer：只读路由表 + JSON 响应 + token 校验
├── queries.py           # 只读查询层（SQL 只读 + 文件读取；零 import core/agents/dreaming）
├── parity.py            # 同源校验辅助（接口响应 vs JSON 报告逐字段比对——测试用）
├── export.py            # 静态导出（同一查询层预生成 JSON + 资产拷贝 → web/dist/）
└── static/              # index.html（树浏览器）/ board.html（进化看板）/ app.js / style.css
                         # vanilla JS + 手绘 SVG 图表
ops/migrations/versions/0009_web_readonly_role.py   # 只读角色 cineflow_web
ops/demo_web.py          # 起服务演示（三重机检结果 + 两视图冒烟）
configs/movie.yaml       # 追加 web 段（host/port/dsn_env/token/page_size/data_dirs/export_dir）
tests/unit/test_web_*.py；tests/contract/test_web_*.py；tests/integration/test_web_pg.py
```

**结构决策**: `web/` 为宪章唯一目录例外；**物理独立**（不 import core——只读纪律的
可审计形式）；查询层与导出共用（一套取数逻辑，杜绝双口径）；页面零构建链。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. web/ 完全独立（只读 SQL + 文件读取，零 import core/agents/dreaming）
2. stdlib http.server + vanilla JS + 手绘 SVG（澄清 Q2：零新增依赖）
3. 只读三重机检 = 路由表断言 + **独立只读 DB 角色（迁移 0009）** + 源码静态断言
4. 同源校验分层（澄清 Q1）：报告产物面板逐字段比对；树接口与 001 落库字段断言
5. 静态导出 = 同一查询层预生成（杜绝两套取数）
6. DB 不可用照常启动（文件面板可用，树接口 503）
7. 默认 127.0.0.1 + 可选 token（最小充分，不引认证框架）
8. 节点分页（page_size 配置上限）；曲线/谱系轮次级数据不分页

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：API 响应 schema（树/节点/谱系/曲线/摘要/健康）、
  只读角色迁移、web 配置段、静态资产与导出形态
- [contracts/readonly-api.md](contracts/readonly-api.md)（C1~C3）、
  [tree-browser.md](contracts/tree-browser.md)（C4~C6）、
  [board-export.md](contracts/board-export.md)（C7~C9）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

只读纪律的三重机检（路由/角色/静态）各自有契约测试承载；原则五的"前端独立"由
"零 import + 静态断言"物理证明；同源分层校验可证伪；数据更新中读已提交状态与
两段式落盘语义一致。**无新增违规，门禁通过。**
