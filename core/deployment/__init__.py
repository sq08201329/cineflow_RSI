"""部署自动化子包（功能 014）：证据门槛 + 影子模式 + 自动部署与抽检回滚。

业务无关的部署机制（宪章原则五：core 不依赖 dreaming/agents）；触发收敛为唯一入口
`evaluate_candidate`（做梦轮次收口后一处接线）；产物全部文件化（`deployment/`，
git 版本化，只增不改），唯一"写"是部署指针的定点改写（复用 `core/yaml_edit`）。

模块划分：
- `models.py`：10 个 frozen 模型 + 枚举（判定优先级/模式迁移/影子计时/误入率等硬约束）；
- `config.py`：deployment 段的门槛/影子/抽检配置解析（缺项即报错，不静默用默认值）；
- `mode.py`：模式状态机（manual/shadow/auto）+ 影子期区间累计 + `deployment/mode.json`；
- `evidence.py`：证据包收集（前置无偏性 + 三要件，口径复用 005/011/012）；
- `gate.py`：门槛判定（优先级 + 缺证据即拦截）+ 证据快照落盘（幂等）。
"""
