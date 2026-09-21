"""CineFlow 只读前端（宪章 v1.1.0 原则五新条款的唯一目录例外）。

- **只读视图**：本目录内不存在任何写路径——不写发现树/谱系/部署指针/任何权威数据；
  路由表无写动词、只读角色无写权限、源码无写调用三重机检分别由
  `web/server.py`（路由表）、迁移 0009（角色权限）与
  `tests/contract/test_web_readonly.py`（源码静态断言）承载；
- **物理独立**：零 import core/agents/dreaming——取数只经只读 SQL（只读角色
  `cineflow_web`）与文件化产物的只读读取，与既有 CLI/JSON 报告同源（不另建数据通道、
  不重算口径）；
- **零新增依赖**：Python 标准库服务（`http.server`）+ 无框架静态页（vanilla JS，
  图表手绘 SVG）。

模块分工：`queries.py`（只读查询层：SQL + 文件读取）、`server.py`（只读 HTTP 服务）、
`parity.py`（同源比对辅助，测试用）、`export.py`（静态导出，本目录唯一的写盘模块）、
`static/`（页面资产）。
"""
