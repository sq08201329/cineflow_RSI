"""只读 HTTP 服务（标准库 `http.server`，默认 bind 127.0.0.1）。

**只读门禁**（宪章原则五新条款）：`ROUTES` 是全部路由的唯一登记处，每一项都必须为
GET——写请求（POST/PUT/DELETE/PATCH）一律 405（/api/*）或 404，零副作用，机器检查见
`tests/contract/test_web_readonly.py`。

另：token 访问控制（配置非空时 /api/* 需 `Authorization: Bearer <token>` 或
`?token=`；静态资产只含页面代码、不含任何权威数据，故不受 token 约束）、路径穿越拒绝、
DB 不可达时树接口 503 而文件化面板照常可用。
"""
