# 任务列表：前端可视化（发现树浏览器 + 进化曲线看板）

**输入**: 来自 `specs/013-frontend/` 的设计文档（plan.md、research.md、data-model.md、contracts/、quickstart.md）

**前置条件**: 宪章 v1.1.0（**原则五新条款为核心**：web/ 唯一目录例外 + 前端必须只读 + 独立于 core/agents/dreaming）；功能 001-012 已交付；规格含 2026-09-21 澄清两条（同源分层校验、stdlib + vanilla JS 零依赖）

**测试说明**: 宪章要求 TDD 与覆盖率 ≥85%（**web/ 接口层计入口径**——pyproject coverage source 需加 web）；只读三重机检（路由表/角色权限/静态断言）为契约测试；**本地验证命令与 ci.yml 逐字一致**。

**组织方式**: 按用户故事分组（US1 只读接口与同源 → US2 树浏览器 → US3 看板与导出）。

## 格式：`[ID] [P] [Story] 描述`

## 阶段 1：搭建（共享基础设施）

- [x] T1301 创建 web/ 目录骨架（server/queries/export/parity/static/）与 configs/movie.yaml 追加 web 段（host=127.0.0.1、port=8080、dsn_env=CINEFLOW_WEB_DSN、token="" 、page_size=50、data_dirs、export_dir=web/dist）
- [x] T1302 [P] conftest 夹具扩展：夹具树（多项目/多 Agent/多版本）+ 做梦轮次文件 + 010 信度报告 + 012 漂移状态 + 谱系 meta 夹具 + web 临时目录与配置夹具；不改坏既有夹具

## 阶段 2：基础（阻塞性前置条件）

**⚠️ 关键**: 此阶段完成前，不能开始任何用户故事的工作

- [x] T1303 迁移测试 tests/unit/test_migration_0009.py（先写：只读角色 cineflow_web 创建语句、GRANT SELECT / REVOKE 写 / DEFAULT PRIVILEGES 只 SELECT、角色口令经环境变量不落盘；注明 PG-only——SQLite 无角色语义，单测做 SQL 文本与迁移纪律断言，权限行为断言在 T1313 真实 PG）
- [x] T1304 实现迁移 ops/migrations/versions/0009_web_readonly_role.py（down_revision=0008）
- [x] T1305 [P] 查询层测试 tests/unit/test_web_queries.py（先写：六类查询（树清单/节点列表/节点详情/谱系/曲线/摘要）的字段 schema 与空态；**零 import core/agents/dreaming 的静态断言**）
- [x] T1306 [P] 实现 web/queries.py（只读 SQL + 文件读取；惰性 DB 连接；字段语义与 001 落库口径一致）
- [x] T1307 [P] 服务测试 tests/unit/test_web_server.py（先写：路由表注册、token 校验、静态资产服务、路径穿越拒绝、DB 不可用 503 + 文件面板可用）
- [x] T1308 [P] 实现 web/server.py（stdlib ThreadingHTTPServer；仅 GET；默认 127.0.0.1）

**检查点**: 迁移/查询层/服务三件套就绪——用户故事可开始

---

## 阶段 3：用户故事 1 - 只读查询接口与数据同源（优先级：P1）🎯 MVP

**目标**: 只读三重机检 + 同源分层校验（门禁先于页面）

**独立测试**: 路由表/权限/静态断言三重机检；同源抽样逐字段一致

### 用户故事 1 的测试（先写，确认失败后再实现）

- [x] T1309 [P] [US1] 契约测试 tests/contract/test_web_readonly.py（C1~C3：路由表机检无写动词、写请求 405/404 且 DB 零变更、web/ 源码静态断言无写调用与写 SQL 关键字——**导出模块对导出目录的写是白名单例外**、token 401/200、DB 不可用韧性、路径穿越拒绝）
- [x] T1310 [P] [US1] 同源测试 tests/contract/test_web_parity.py（C2 场景 3 + 分层口径：进化曲线/信度/漂移/**谱系**面板——接口响应 vs 既有 JSON 报告逐字段一致（抽样断言集；**谱系面板的比对是对冲 web/ 侧重写汇聚逻辑的口径漂移**）；树/节点接口字段集与类型与 001 落库 schema 一致断言）
- [x] T1311 [US1] 集成测试 tests/integration/test_web_pg.py（真实 PG：cineflow_web 角色对全部表仅 SELECT（information_schema 断言）、以该角色 INSERT 被 DB 拒绝、树清单查询真实返回）

### 用户故事 1 的实现

- [x] T1312 [US1] 实现 web/parity.py（同源比对辅助：面板 → 报告文件路径映射 + 逐字段比对 + 差异报告；依赖 T1306）

**检查点**: 三重机检 + 同源分层校验成立——只读数据面 MVP 成立

---

## 阶段 4：用户故事 2 - 发现树浏览器（优先级：P2）

**目标**: 三维过滤 + 节点详情 + 谱系链路的页面与查询

**独立测试**: 过滤/分页/详情字段/谱系归属/空态逐断言 + 页面冒烟

### 用户故事 2 的测试（先写，确认失败后再实现）

- [ ] T1313 [P] [US2] 浏览查询测试 tests/unit/test_web_tree_browser.py（C4/C5：三维过滤各自生效、分页边界无重复无遗漏、节点详情含 eval_breakdown 版本标注与工件哈希、谱系跨项目归属、空态 items=[]、不存在版本 404）
- [ ] T1314 [P] [US2] 页面冒烟测试 tests/unit/test_web_pages.py（C6：index.html/board.html 可服务、JS 的 fetch 路径全部在路由表内（静态断言）、控件标记存在、无外部库引用）

### 用户故事 2 的实现

- [ ] T1315 [US2] 实现 web/static/index.html + web/static/app.js（树浏览器页面：三维过滤控件 + 节点表 + 详情面板 + 谱系视图；vanilla JS fetch /api/*）
- [ ] T1316 [US2] 查询层树浏览/谱系接口完善（依赖 T1306、T1313 测试驱动）

**检查点**: 树浏览器两视图（清单/详情/谱系）可用且冒烟通过

---

## 阶段 5：用户故事 3 - 进化曲线看板与静态导出（优先级：P3）

**目标**: 曲线/塌缩/成本/摘要面板 + 静态导出（同一查询层）

**独立测试**: 曲线与报告一致、成本对账一致、摘要空态、导出离线可浏览

### 用户故事 3 的测试（先写，确认失败后再实现）

- [ ] T1317 [P] [US3] 看板查询测试 tests/unit/test_web_board.py（C7：reward 序列与 dreaming/history 逐字段一致、塌缩标注、成本汇总与 CostRecord 聚合对账一致、信度/漂移徽标状态与缺失空态）
- [ ] T1318 [P] [US3] 导出测试 tests/unit/test_web_export.py（C8：导出目录含静态资产 + data/*.json、导出 JSON 与 API 响应逐字段一致、断服务后导出目录两视图可浏览）

### 用户故事 3 的实现

- [ ] T1319 [US3] 实现 web/static/board.html + app.js 扩展（进化看板：曲线/塌缩/成本/摘要面板；图表手绘 SVG——无外部库引用静态断言）
- [ ] T1320 [US3] 实现 web/export.py（同一查询层预生成 JSON + 资产拷贝 → web/dist/）

**检查点**: 看板可用 + 导出离线可浏览——里程碑验收线成立

---

## 阶段 6：打磨与横切关注点

- [ ] T1321 端到端演示 ops/demo_web.py（quickstart 六步：起服务 + 三重机检结果输出 + 两视图冒烟 + 同源抽样输出 + 写拒绝演示 + 静态导出；断言退出码 0）
- [ ] T1322 运行 quickstart.md 全部验证步骤并记录（验证记录回填；**pyproject coverage source 加 web** 后复核 ≥85%；命令与 ci.yml 逐字一致）
- [ ] T1323 [P] 更新 README.md（看板用法：起服务/导出/访问控制）与 docs/二期立项书.md 里程碑表（F8 已交付注明）

---

## 依赖关系与执行顺序

### 阶段依赖

- **搭建（阶段 1）**: 无依赖
- **基础（阶段 2）**: T1303→T1304 一链；T1305→T1306、T1307→T1308 两链并行
- **用户故事（阶段 3+）**: US1 依赖基础（门禁与同源不依赖页面）；US2/US3 依赖 US1 的查询层与机检框架
- **打磨（阶段 6）**: T1321 依赖全部故事；T1323 可在基础完成后开始

### 并行机会

- 阶段 2：三条链并行
- US1：T1309/T1310/T1311 三测试并行
- US2：T1313/T1314 并行；US3：T1317/T1318 并行

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成阶段 1 + 阶段 2
2. 完成 US1：只读三重机检 + 同源分层校验（无页面也有价值——数据面成立）
3. **停下并验证**：写拒绝、权限、同源断言全绿

### 增量交付

1. 搭建 + 基础 → 角色/查询层/服务就绪
2. US1 → 只读数据面（MVP，门禁先于页面）
3. US2 → 树浏览器
4. US3 → 看板与导出（里程碑验收线）
5. 阶段 6 → demo/文档

---

## 备注

- 宪章原则五新条款落点（本特性核心）：T1303/T1304（只读角色物理保证）+ T1309（路由表与静态断言机检）+ T1311（真实 PG 权限断言）+ **零 import core 的静态断言（T1305）**
- 澄清决议落点：同源分层校验（T1310：报告面板逐字段 + 树接口字段断言）；stdlib + vanilla JS 零依赖（T1307/T1308、T1314 无外部库静态断言、T1319 手绘 SVG）
- 只读纪律的可审计形式 = "不存在 import + 角色无写权限 + 源码无写调用"三重；任一失效即红
- 看板无操作入口（录入/审批/部署永远走 CLI）——页面模板不含任何表单/按钮写路径（T1314 静态断言可顺带覆盖）
- web/ 计入覆盖率口径（T1322 改 pyproject source 并复核）
- 本地验证纪律：命令与 ci.yml 逐字一致（含 `ruff format --check .`）
- 每个任务或逻辑组完成后提交（git）；检查点必须独立验证通过
