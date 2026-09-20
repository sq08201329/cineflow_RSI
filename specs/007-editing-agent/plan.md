# 实现计划：剪辑 Agent 闭环（粗剪→精剪→成片 + 策略自动进化）

**分支**: `007-editing-agent` | **日期**: 2026-09-20 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/007-editing-agent/spec.md` 的功能规格说明（含 2026-09-20 澄清会话两条决议）

## 概要

交付 `agents/editing/` 包：剪辑探索执行器（镜头库 + 场景分区约束 + 可选音轨 → 策略产
EDL → 执行前四层合法性校验 → 预算门禁 → 渲染合成 → mp4 内容寻址 → 五评估器 → 定点归一
→ 一次性 INSERT）、确定性模拟渲染器（EDL + 素材帧 → numpy 程序化拼接/叠化/混音 → mp4
逐字节复现，**编码单线程确定性档消除 004 x264 flake 根因**）、五评估器（时长/镜头分布/
转场三 gate + 节奏曲线 proxy（分段基准距离）+ judge.narrative_flow（EDL 摘要成对比较，
三段哈希入版本号））、回放接入与无偏性验收（τ ≥ 0.95 发布阻塞）、做梦层与 010 零改动
接入。澄清决议贯穿：judge 输入 = EDL 结构化文本摘要（可复现）、场景分区约束执行前
校验。**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——numpy（拼接/叠化/混音定点运算）、blake3、pyyaml、SQLAlchemy
Core；judge 走既有 LLM 网关（Mock 后端确定性）；τ-b 复用 `core/replay/unbiasedness.py`；
定点归一复用 `core/evaluators/quantize.py`；mp4 编码复用 004 `encode_mp4` 路径
（固定单线程参数）

**存储**: DB 新运营表 `edit_render_jobs`（迁移 0006，唯一键 `(round_id, edl_hash)`）；
成片 mp4 内容寻址入对象存储；节点走 001 immutable 树（visual 模式：渲染即评即落树）

**测试**: pytest；EDL 属性注入夹具（时长/分布/转场/节奏可控）；渲染适配器契约套件
（双实现同构）；无偏性套件 + 注入偏差拒绝

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `agents/editing/` 新业务包（→ core 单向依赖，004/006 同构）

**性能目标**: 单轮剪辑（≤3 组 EDL × 模拟渲染 + 评估）< 1 分钟（demo 预算内；
渲染为 numpy 毫秒级运算 + 确定性编码）

**约束**: 渲染仅限线上探索（原则三，回放零渲染审计）；评估器版本冻结（judge 三段哈希）；
EDL 非法执行前拒绝（0 渲染 0 成本）；基准曲线/规则库/价目全配置化（原则五）；
gate 短路不跑 judge（省 LLM 成本）；覆盖率 ≥85%

**规模/范围**: 1 个 agents 子包（约 14 模块）+ 1 张运营表 + champion 策略 + demo；
不含真实渲染凭证、镜头库真实供给契约（试水作品集成时对齐）、judge 漂移检测（F7 供数）、
部署自动化（F9）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 五评估器 deterministic + 实现哈希入版本；judge 版本 = 提示词 + 锚点集 + 摘要函数三段哈希；定点归一 | ✅ 满足 |
| 原则二：节点不可变与成本 | 两段式落盘；FAILED 渲染成本照常入账；成片内容寻址 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 渲染仅在探索执行器；回放零渲染零生成审计；价目缺失即报错 | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 策略候选回放走 002 沙箱（复用，无新路径） | ✅ 满足 |
| 原则五：单向依赖与配置化 | `agents/editing → core`；基准曲线分段配置化（短剧换段参数零代码）；转场规则库单一事实源 | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | judge 委员会 + 平局如实记录；剪辑纳入 010 盲评（F7 数据源）；approve 人工闸门不变 | ✅ 满足 |
| 测试纪律 | TDD；新增评估器三件套（单测 + 注册元数据 + 对比样本）；注入偏差拒绝；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/007-editing-agent/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── editing-loop.md        # EDL 校验与执行器（C1~C3）
│   ├── editing-evaluators.md  # 五评估器与合成（C4~C9）
│   ├── editing-platform.md    # 渲染适配器双实现（C10~C13）
│   └── editing-evolution.md   # 回放/无偏性/做梦/周校准（C14~C16）
└── tasks.md                   # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
agents/editing/
├── config.py              # EditingConfig（预算/时长容差/镜头限制/转场规则库/基准曲线/价目/judge）
├── db.py                  # edit_render_jobs 运营表定义
├── shots.py               # ShotEntry/SceneStructure（分区校验）
├── edl.py                 # EDL 模型 + 规范化 + 执行前四层合法性校验
├── render.py              # 程序化拼接/叠化/混音 → mp4（单线程确定性编码）
├── summary.py             # EDL 结构化文本摘要（judge 输入，哈希入版本）
├── evaluators/
│   ├── duration.py        # rule.duration_compliance（gate）
│   ├── shot_distribution.py  # rule.shot_distribution（gate）
│   ├── transitions.py     # rule.transition_rules（gate，与执行前校验同库）
│   ├── pacing.py          # proxy.pacing_curve（分段基准距离）
│   └── narrative.py       # judge.narrative_flow（摘要成对比较，三段哈希版本）
├── platform/
│   ├── base.py            # 适配器协议 + 错误分型 + RenderedFilm
│   ├── simulated.py       # 确定性模拟渲染器
│   └── http_real.py       # 真实渲染服务骨架（EDIT_RENDER_* 凭证）
└── loop.py                # 探索执行器（预算/幂等/两段式/评估/落树/freeze_round_tree）
ops/migrations/versions/0006_edit_render_jobs.py
ops/demo_editing_loop.py   # 闭环演示（quickstart 六步）
configs/movie.yaml         # 追加 editing 段 + evaluator_weights.editing
policies/history/editing/  # champion 策略 + meta.json
tests/unit/test_editing_*.py；tests/contract/test_editing_platform_contract.py；
tests/integration/test_editing_pg.py；tests/unbiasedness/test_editing_unbiased.py
```

**结构决策**: 剪辑 = 004/006 模式的第三次同构（执行器/适配器/评估器/池接线）；
特有模块三处：`edl.py`（决策对象与四层校验）、`summary.py`（judge 输入形成，
澄清 Q1）、`render.py`（拼接/转场/混音，单线程确定性编码——消除一期 flake 根因）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 落树 = visual 模式（渲染即评即落树）
2. 模拟渲染确定性：定点运算 + **编码单线程档**（消除 x264 多线程 flake）
3. EDL 四层执行前校验（引用/越界/场景分区/转场规则库——与门禁同一配置库）
4. 节奏基准 = 分段目标统计 + 段权重（承载短剧形态切换，DTW 不引）
5. judge 版本 = 提示词 + 锚点 EDL 集 + 摘要函数三段哈希（澄清 Q1）
6. judge 平局 0.5 如实记录；LLM 全过网关
7. 预算/幂等/两段式同构复用（不发明第二种）
8. dreaming/010 零改动接入（006 已实证泛化）
9. 音轨 = 时间戳摆放 + 定点混音，波形同步不重做（006 边界）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：edit_render_jobs schema、EDL/ShotEntry/SceneStructure/
  PacingBaseline、评估器组合、job 状态机
- [contracts/editing-loop.md](contracts/editing-loop.md)（C1~C3）、
  [editing-evaluators.md](contracts/editing-evaluators.md)（C4~C9）、
  [editing-platform.md](contracts/editing-platform.md)（C10~C13）、
  [editing-evolution.md](contracts/editing-evolution.md)（C14~C16）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

judge 三段哈希与 gate 短路纪律落进契约（原则一/三）；EDL 执行前校验保证非法决策
0 成本（原则二/三）；基准曲线分段配置承载形态切换（原则五）；盲评纳入与静态防特判
测试（原则六）。**无新增违规，门禁通过。**
