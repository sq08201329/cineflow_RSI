# 阶段 0 调研：回放模拟器与沙箱化策略执行

**日期**: 2026-09-18 | **关联计划**: [plan.md](plan.md)

## 决策 1：沙箱运行时——双后端

**决策**: 定义 `SandboxBackend` 窄接口（`run(policy_source, io_bridge, limits) -> RunResult`），
两个实现：

- `DockerGVisorBackend`：docker run `--runtime=runsc`，CI 权威后端
  （GitHub Actions ubuntu-latest 可 apt 安装 runsc 并注册为 docker runtime）；
- `DockerHardenedBackend`：本地兜底——`--network=none --read-only --cap-drop=ALL
  --security-opt=no-new-privileges --memory/--cpus/--pids-limit` + 自定义 seccomp 配置，
  隔离语义与 gVisor 等价（无网络、无凭证、只读挂载、资源限额），内核隔离强度较低。

**理由**: 宪章栈表写的是 Docker（gVisor runtime），但 Docker Desktop 的守护进程在其自有
VM 中运行，无法注入 runsc——本地开发环境物理上不可用 gVisor。双后端让 CI 以宪章规定的
gVisor 跑权威对抗门禁，本地保持开发循环可用。已在 plan.md 复杂度跟踪中声明。

**已评估的替代方案**: 直接以 runsc 跑 OCI bundle（绕开 Docker——可行但镜像分发与资源
管理要自建，过度设计）；仅加固容器（偏离宪章且无记录）；Kata（WSL/CI 安装更重）。

## 决策 2：IPC 协议

**决策**: stdio JSON Lines，三条请求消息（`observed`、`probe`、`shutdown`）与三类响应
（`ok`、`unknown`、`error`）。协议层做 schema 校验：字段白名单、单消息 1MB 上限、
每请求超时；只允许 JSON 值语义数据穿越边界（无引用、无自定义类）。

**理由**: stdio 天然随容器生命周期、无需端口与网络命名空间；JSON Lines 可调试、可录制
（回放轨迹可含 IPC 日志用于审计）；窄协议是最小攻击面。Unix socket/gRPC 都更重且无收益。

**已评估的替代方案**: Unix socket（需挂载点管理）；HTTP/gRPC（引入网络栈，与"无网络"
隔离语义冲突）。

## 决策 3：计时侧信道防护

**决策**: probe/observed 响应填充至**固定时延量子**（如 50ms 的整数倍，常量来自形态配置）：
处理完成后 sleep 至下一个量子边界再返回。确定性填充——回放可复现（同轨迹同耗时分档），
且响应时间与隐藏得分/节点数的线性相关性被量子化消除。

**理由**: "固定抖动"（dev doc §3.5）的确定性实现：常量填充既消除时序信号又不引入随机性
（回放确定性是原则一的精神延伸）。随机抖动会让回放不可复现，被否。

**已评估的替代方案**: 随机抖动（破坏可复现）；不防护+仅审计（宪章要求拦截而非检测）。

## 决策 4：Kendall τ 计算

**决策**: 手写 O(n²) τ-b 变体（处理同分对），位于 `core/replay/` 的无偏性工具函数；
不引入 scipy。

**理由**: 轨迹长度数十~数百，O(n²) 是微秒级；scipy 为单一函数引入重依赖不值
（宪章评审要求：新增依赖必须论证）。

**已评估的替代方案**: scipy.stats.kendalltau（重依赖）；τ-a（不处理同分，得分序列
同分常见，语义不符）。

## 决策 5：生成参数匹配语义

**决策**: `gen_params` 规范化（键排序、递归）后做精确相等比较。不做数值容差、不做缺省
补齐——缺字段即不匹配（返回 UNKNOWN）。

**理由**: dev doc §3.1 要求"与该生成参数匹配的真实历史节点"，宽松匹配等于模拟器编造
经验（原则三禁止）。覆盖不足的正确解法是扩大线上探索，不是放宽匹配。

**已评估的替代方案**: 字段子集匹配（语义含糊，被否）；数值容差（回放失真，被否）。

## 决策 6：策略静态检查

**决策**: `policies/static_check.py` 用 AST 扫描：import 白名单（math、random、
collections 等纯计算模块）、禁止 `open/socket/eval/exec/__import__/getattr` 字符串逃逸等
危险调用形态。静态检查是**第一**道防线；沙箱运行时隔离是**第二**道；两者独立失效才算失守。

**理由**: dev doc §5.1 要求做梦产出策略过静态检查；提前在宿主侧拦截可给出可读错误，
也缩小运行时攻击面。

**已评估的替代方案**: 仅运行时隔离（错误不可读、攻击面大）；RestrictedPython
（维护停滞，且仍需沙箱兜底）。

## 决策 7：模拟器进程内实现 + IPC 桥接

**决策**: 模拟器本体在宿主进程；策略在容器进程；宿主侧 `runner.py` 把策略的 IPC 请求
转发给模拟器。**单元测试用进程内策略**（同接口、无容器，快速验证回放语义）；
**对抗测试必须走容器后端**（隔离语义只在真实边界上成立）。

**理由**: 回放语义（匹配/UNKNOWN/时钟/轨迹）与隔离是两个正交关注点，分层测试最快；
对抗语义必须在真实边界上验证（宪章原则四：物理不可达，非同进程"约定不看"）。
