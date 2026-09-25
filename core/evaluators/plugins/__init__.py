"""业务无关通用评估器的落点（021 C3 / 裁决 2）：可被任意形态配置经 `impl` 声明后共用。

**目录不决定可用性**：本包内的模块级公开函数只有被某份 `configs/*.yaml` 的
`evaluators.plugins.<agent>.<slot>.<evaluator_id>.impl` 引用（`"<本包模块>:<函数>"`）才进入
装配面；未被声明的函数即"孤立插件"，由常驻机检报错（先例 `tests/contract/test_plugin_contracts.py`
的"插件文件零孤立"）。通用件落本包、仅当语义与某 Agent 绑定时落 `agents/<agent>/evaluators/`——
两类的生效路径完全相同（都要经 `impl` 声明）。

**业务无关四条**（每个模块逐个过文本 + AST 双层机检）：零 `agents.*` / `dreaming.*` import、
零形态字面量与形态判断分支、零环境变量读取与配置文件路径读取、零网络调用、零 LLM 或厂商 SDK 直连。
"""
