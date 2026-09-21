"""只读查询层：发现树只读 SQL + 文件化产物只读读取（同一查询层的唯一取数实现）。

**依赖纪律**（宪章原则五新条款）：本模块零 import core/agents/dreaming——表结构按迁移
0001 的列名用只读 SQL 对接（`discovery_trees` / `tree_nodes`），文件化产物按既有落盘
schema 直接解析（policies/history 谱系 meta、dreaming/history 轮次报告、
calibration 的 010 信度报告与 012 漂移状态/报表）。任何取数都不重算口径。

**连接**：惰性——首个需要 DB 的请求才建引擎；DSN 取自配置的环境变量名（只读角色
`cineflow_web`，口令不落盘）。DB 不可达时抛 `DatabaseUnavailableError`，服务侧映射为
503 + 明确说明（文件化面板不受影响）。
"""
