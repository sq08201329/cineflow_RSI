"""通用编排子包（功能 015）：轻量 DAG + 阶段执行器 + 账目汇总。

**零业务概念**（宪章原则五）：本包只认识 stage_id、依赖、执行入口引用与产物引用，
不认识任何环节语义与形态差异——形态差异全部由 `configs/*.yaml` 表达，业务侧链定义
与交接契约在 `agents/pilot/`。静态断言见 `tests/unit/test_orchestration_executor.py`
与 `tests/unit/test_form_switch.py`。

模块划分：
- `models.py`：StageSpec/StageState/RunRecord 等 frozen 模型 + 状态枚举与校验；
- `dag.py`：依赖校验、环检测与拓扑序（自研轻量 DAG，无外部编排框架依赖）；
- `executor.py`：阶段状态机 + 断点续跑 + 输入指纹校验（零业务概念）；
- `ledger.py`：按阶段/形态汇总成本并与各来源账目对账（不一致即报错）；
- `errors.py`：编排错误类型（DAG/执行/续跑拒绝/账目不一致）。
"""
