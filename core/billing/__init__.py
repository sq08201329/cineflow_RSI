"""渠道计费机制件（业务无关）：预算门禁与账本、账单规范化与对账、校准记录、渠道运行记录。

五模块 = 渠道计费**纪律**的载体（前置预算门禁、账单规范化导入、逐项差异对账、校准先决、
连续运行证据），不是某个 Agent 或某个形态的业务：渠道 id、环节 id、账单格式 id、档案 id 与
端点**全部作参数或配置值传入**，包内零字面量、零 `channel_id == …` 分支、零反向依赖
（原则五：`agents → core`、`dreaming → core`）——`tests/unit/test_billing_core_purity.py` 常驻机检。

依赖方向：`core/billing → core/llm_gateway`（**单向**，取 `GatewayError` 作拒绝的分型基准）；
`core/llm_gateway` 对本包**零 import**（守卫以结构化调用接入，未注入时网关行为逐字节不变）。

磁盘布局（根可配，默认仓库根 `billing/`，见契约 C4）：
`{root}/{channel}/{bills,reports,calibrations,runs}/…` + `ledger.json` + `alerts.jsonl`。
"""
