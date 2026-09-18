# 实现计划：回放模拟器与沙箱化策略执行

**分支**: `002-replay-sandbox` | **日期**: 2026-09-18 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/002-replay-sandbox/spec.md` 的功能规格说明

## 概要

交付 `core/replay`（回放模拟器：`observed()`/`probe()`、精确匹配与 UNKNOWN 语义、虚拟时钟、
轨迹产出、同 Agent 多树合并池）+ `core/sandbox`（策略隔离执行：stdio IPC 窄协议、
资源限额、无网络/无凭证，gVisor 与加固容器双后端）+ `policies/`（策略基类、BLAKE3 版本、
静态检查）+ 对抗测试套件（tests/adversarial，合并阻塞）+ 无偏性验收（tests/unbiasedness，
τ ≥ 0.95 发布阻塞）。零新增重依赖（Kendall τ 手写实现）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 复用功能 001 栈（SQLAlchemy/psycopg/blake3/uuid-utils/pyyaml）；**不新增**
第三方运行时依赖（Kendall τ 手写 O(n²)，轨迹长度数十~数百足够）；沙箱依赖系统级
Docker（本地已可用）与可选 gVisor runsc（CI 安装）

**存储**: 无新表——模拟器只读消费功能 001 的 TreeStore（冻结树 + 节点）；策略代码以文件
存于 `policies/history/{agent_id}/{version}.py`（version = 代码 BLAKE3 前 12 位）

**测试**: pytest；单元层用进程内模拟器（无容器）；对抗/沙箱测试用真实容器后端；
无偏性测试用录制轨迹夹具

**目标平台**: Linux（WSL2 开发 + GitHub Actions ubuntu-latest CI）

**项目类型**: 内部库（monorepo `core/` 扩展 + `policies/`）

**性能目标**: 3 万节点树构建模拟器 + 回放一套参考策略全程 < 10 分钟（SC-006）；
probe 响应填充至固定时延量子（计时侧信道防护）

**约束**: 回放零生成（原则三）；未揭示状态对策略进程物理不可达（原则四）；
core/ 零业务依赖（原则五）；单元覆盖率 ≥85%；对抗测试为合并阻塞、无偏性为发布阻塞

**规模/范围**: 3 个新包（core/replay、core/sandbox、policies）、15 条 FR、3 份契约；
不含做梦层与跨项目回放

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则三：昂贵动作仅限线上探索 | 回放 `max_generation_calls` 恒 0、无匹配返回 UNKNOWN、模拟器只读冻结树 | ✅ 满足 |
| 原则四：沙箱隔离与前缀不可泄露 | 策略独立容器进程 + stdio IPC 双入口；`_latent` 不进策略进程；对抗三件套合并阻塞；响应固定时延量子 | ✅ 满足（一处偏差见复杂度跟踪） |
| 原则一：评估器版本冻结 | 回放只读 001 的冻结数据，不新增评估路径 | ✅ 不触及 |
| 原则二：节点不可变与谱系 | 回放零写入；轨迹携带策略版本（BLAKE3 前 12 位）维持谱系 | ✅ 满足 |
| 原则五：单向依赖与配置化 | core/replay、core/sandbox 不依赖业务；W（worker_count）来自 configs | ✅ 满足 |
| 原则六：诚实边界 | 无自动进化；无偏性为人工上线门禁的工具化 | ✅ 满足 |
| 测试纪律 | TDD；对抗套件 CI 常驻；覆盖率 ≥85% | ✅ 满足 |

## 复杂度跟踪

| 违规项 | 为何需要 | 被拒绝的简化方案 |
|--------|----------|-----------------|
| 沙箱提供"加固 Docker"兜底后端，宪章栈表写的是 Docker（gVisor runtime） | Docker Desktop 的守护进程运行在其自有 VM 中，无法注入 runsc，本地开发环境无法使用 gVisor；gVisor 后端在 CI（ubuntu-latest 可装 runsc 并注册 docker runtime）真实生效，对抗门禁在 CI 以 gVisor 运行 | 仅支持 gVisor：本地无法跑对抗测试，开发循环断裂；仅加固容器：偏离宪章栈表且无兜底记录 |

## 项目结构

### 文档（此功能）

```text
specs/002-replay-sandbox/
├── plan.md              # 本文件
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── contracts/           # 阶段 1 输出
│   ├── replay-api.md
│   ├── sandbox-ipc.md
│   └── unbiasedness.md
└── tasks.md             # 阶段 2 输出（/skill:speckit-tasks 创建）
```

### 源代码（仓库根目录，在 001 结构上增量）

```text
core/
├── replay/
│   ├── simulator.py       # ReplaySimulator：observed()/probe()、揭示状态机
│   ├── pool.py            # 模拟器池：同 Agent 多树合并、冻结校验
│   ├── clock.py           # VirtualClock：决策轮 + 有效串行轮 ⌈k/W⌉
│   ├── trajectory.py      # ReplayTrajectory：轨迹记录与 JSON 报告
│   ├── matching.py        # 生成参数规范化与精确匹配
│   ├── observation.py     # Observation/ProbeResult 与字段白名单投影
│   └── unbiasedness.py    # Kendall τ 与无偏性验收计算
├── sandbox/
│   ├── runner.py          # 沙箱执行入口：起容器、IPC 桥接、超时/资源限额
│   ├── protocol.py        # stdio JSON Lines 窄协议（消息 schema 校验）
│   ├── policy_side.py     # 沙箱内策略侧 IPC 客户端（容器入口）
│   └── backends/
│       ├── docker_gvisor.py   # 首选：docker --runtime=runsc（CI）
│       └── docker_hardened.py # 兜底：加固旗标容器（本地开发）
policies/
├── base.py                # ExplorationPolicy ABC + Budget + SimulatorEnv 协议
├── static_check.py        # 策略代码静态检查（AST import 白名单、禁 IO 调用）
├── versioning.py          # 策略版本：BLAKE3 前 12 位 + history/ 落盘
└── history/               # 策略历史版本 {agent_id}/{version}.py
configs/movie.yaml         # 追加 replay.worker_count 等形态参数
tests/
├── unit/                  # 进程内模拟器、时钟、轨迹、协议、静态检查、τ 计算
├── adversarial/           # 作弊策略套件 + 对抗测试（合并阻塞）
├── unbiasedness/          # 无偏性验收测试 + 轨迹夹具（发布阻塞）
└── integration/           # 沙箱容器端到端（Docker 可用时）
ops/
└── demo_replay.py         # 端到端演示：小树构建 → 手工策略回放 → 报告
```

**结构决策**: 沿用 monorepo 既定布局。`dreaming/` 不创建（做梦层属后续特性）；
`tests/adversarial/`、`tests/unbiasedness/` 按宪章目录约定建立。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. **沙箱双后端**：gVisor（CI 权威）+ 加固容器（本地兜底），见复杂度跟踪
2. **IPC 协议**：stdio JSON Lines 三消息窄协议，值语义、字节上限、超时
3. **计时侧信道防护**：响应填充到固定时延量子（确定性，回放可复现）
4. **Kendall τ**：手写 O(n²)，零新依赖
5. **生成参数匹配**：规范化 JSON 后精确相等，无模糊匹配
6. **策略静态检查**：AST 白名单（禁网络/文件 IO/危险内建），与运行时隔离双保险

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：回放侧内存实体（Observation/ProbeResult/VirtualClock/
  ReplayTrajectory/Budget）与校验规则；无新 DB 表
- [contracts/replay-api.md](contracts/replay-api.md)：模拟器、时钟、轨迹、策略接口契约
- [contracts/sandbox-ipc.md](contracts/sandbox-ipc.md)：IPC 消息 schema、错误语义、资源限额
- [contracts/unbiasedness.md](contracts/unbiasedness.md)：τ 验收函数与报告 schema
- [quickstart.md](quickstart.md)：端到端验证场景（映射规格验收场景）

## 宪章复核（阶段 1 后）

设计产物逐条复核：模拟器只读 TreeStore 且无写路径（原则二/三）；策略进程内不存在
模拟器对象（原则四）；worker_count 由 configs 注入（原则五）；轨迹携带策略版本
（原则二谱系）。除复杂度跟踪已声明的沙箱后端偏差外，**无新增违规，门禁通过。**
