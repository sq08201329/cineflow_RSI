# 实现计划：分镜 Agent 闭环（剧本→分镜脚本/动态预演 + 策略自动进化）

**分支**: `008-storyboard-agent` | **日期**: 2026-09-20 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/008-storyboard-agent/spec.md` 的功能规格说明（含 2026-09-20 澄清会话两条决议）

## 概要

交付 `agents/storyboard/` 包：分镜探索执行器（剧本段落 → 策略产 ShotList → 执行前三层
合法性校验 → 预算门禁 → 预演渲染 → animatic mp4 内容寻址 → 五评估器 → 定点归一 →
一次性 INSERT）、确定性模拟渲染器（ShotList → 分镜卡帧 → 拼接 → mp4 逐字节复现，编码
单线程档）、五评估器（景别语法/覆盖率/轴规则三 gate + 情绪对齐 proxy（读预演帧像素）+
judge.script_fit（摘要三段哈希））、回放接入与无偏性验收（τ ≥ 0.95 发布阻塞）、
做梦层与 010 零改动接入（复用 007 的观测双键教训）、ShotList schema 稳定性契约。
澄清决议贯穿：coverage = 场景级 + 必覆盖清单、情绪对齐读帧。**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——numpy（分镜卡帧与特征向量）、blake3、pyyaml、SQLAlchemy Core；
judge 走既有 LLM 网关（Mock 后端确定性）；τ-b 复用 `core/replay/unbiasedness.py`；
定点归一复用 `core/evaluators/quantize.py`；mp4 编码复用 007 单线程确定性参数

**存储**: DB 新运营表 `storyboard_render_jobs`（迁移 0007，唯一键 `(round_id, shotlist_hash)`）；
预演 mp4 内容寻址入对象存储；节点走 001 immutable 树（visual 模式）

**测试**: pytest；ShotList 属性注入夹具（景别序列/覆盖缺口/轴违规/情绪对齐可控）；
预演适配器契约套件；无偏性套件 + 注入偏差拒绝；schema 快照稳定性断言

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `agents/storyboard/` 新业务包（→ core 单向依赖；第四个同构闭环）

**性能目标**: 单轮分镜（≤3 组 ShotList × 模拟渲染 + 评估）< 1 分钟（demo 预算内）

**约束**: 预演渲染仅限线上探索（原则三，回放零渲染审计）；评估器版本冻结（judge 三段
哈希）；ShotList 非法执行前拒绝；规则库/必覆盖清单/情绪向量/价目全配置化（原则五）；
gate 短路不跑 judge；覆盖率 ≥85%

**规模/范围**: 1 个 agents 子包（约 14 模块）+ 1 张运营表 + champion 策略 + demo；
不含真实预演渲染凭证、剧本 Agent 真实产出契约（F4 接入时对齐）、下游视觉/剪辑接线、
judge 漂移检测（F7 供数）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 五评估器 deterministic + 实现哈希入版本；judge 三段哈希；定点归一 | ✅ 满足 |
| 原则二：节点不可变与成本 | 两段式落盘；FAILED 渲染成本照常入账；内容寻址 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 预演渲染仅在探索执行器；回放零渲染零生成审计；价目缺失即报错 | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 策略候选回放走 002 沙箱（复用，无新路径）；观测双键经白名单投影 | ✅ 满足 |
| 原则五：单向依赖与配置化 | `agents/storyboard → core`；景别/规则库/必覆盖清单/情绪向量全配置；下游 schema 契约化 | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 规则冲突如实暴露不自动豁免；必覆盖清单空则降级并注明；纳入 010 盲评；approve 人工闸门不变 | ✅ 满足 |
| 测试纪律 | TDD；新增评估器三件套（单测 + 注册元数据 + 对比样本）；注入偏差拒绝；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/008-storyboard-agent/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── storyboard-loop.md        # ShotList 校验与执行器（C1~C3）
│   ├── storyboard-evaluators.md  # 五评估器与合成（C4~C9）
│   ├── storyboard-platform.md    # 预演适配器与确定性合成（C10~C13）
│   └── storyboard-evolution.md   # 回放/无偏性/做梦/下游交接（C14~C17）
└── tasks.md                      # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
agents/storyboard/
├── config.py              # StoryboardConfig（预算/档位枚举/规则库/轴规则/情绪向量/价目/judge）
├── db.py                  # storyboard_render_jobs 运营表定义
├── script.py              # ScriptSegment（场景/行/关键行标注/情绪/轴向基准）+ 校验
├── shotlist.py            # ShotList 模型 + 规范化 + 前三层合法性校验
├── board_render.py        # 分镜卡帧生成 + 拼接 → animatic mp4（单线程确定性编码）
├── summary.py             # ShotList × 剧本 → 结构化摘要（judge 输入，哈希入版本）
├── evaluators/
│   ├── shot_grammar.py    # rule.shot_grammar（gate）
│   ├── coverage.py        # rule.coverage（gate，场景级 + 必覆盖清单）
│   ├── axis_rule.py       # rule.axis_rule（gate，侧别跳变需过渡）
│   ├── alignment.py       # proxy.emotion_alignment（读预演帧像素）
│   └── script_fit.py      # judge.script_fit（三段哈希版本）
├── platform/
│   ├── base.py            # 适配器协议 + 错误分型 + RenderedAnimatic
│   ├── simulated.py       # 确定性模拟渲染器
│   └── http_real.py       # 真实预演服务骨架（STORYBOARD_RENDER_*）
└── loop.py                # 探索执行器（预算/幂等/两段式/双键观测/freeze_round_tree）
ops/migrations/versions/0007_storyboard_render_jobs.py
ops/demo_storyboard_loop.py
configs/movie.yaml         # 追加 storyboard 段 + evaluator_weights.storyboard
policies/history/storyboard/  # champion 策略 + meta.json
tests/unit/test_storyboard_*.py；tests/contract/test_storyboard_platform_contract.py；
tests/integration/test_storyboard_pg.py；tests/unbiasedness/test_storyboard_unbiased.py
```

**结构决策**: 分镜 = 第四个同构闭环（执行器/适配器/评估器/池接线），特有模块四处：
`script.py`（输入模型含关键行标注）、`shotlist.py`（决策对象与三层校验）、
`board_render.py`（分镜卡帧——同时是渲染件与情绪对齐的评估输入，避免两套帧）、
`summary.py`（judge 输入）。观测双键与 freeze_round_tree 直接复用 006/007 结论。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 落树 = visual 模式（第四次验证）
2. ShotList = 决策对象与回放匹配键；三层执行前校验
3. coverage = 场景级 + 必覆盖清单（澄清 Q1）；空清单降级并注明
4. emotion_alignment 读预演帧像素（澄清 Q2）；分镜卡帧由同一函数产出（渲染件 = 评估输入）
5. 轴规则可从 ShotList 侧别元数据机检（无需画面分析），过渡镜头豁免
6. judge 三段哈希（提示词/锚点集/摘要函数）
7. 预算/幂等/两段式同构复用
8. 观测双键 + dreaming/010 零改动（007 教训直接复用）
9. ShotList schema 版本化契约，下游接线留给各自特性

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：storyboard_render_jobs schema、ScriptSegment/ShotEntry/
  ShotList/ShotGrammarRules、评估器组合、job 状态机
- [contracts/storyboard-loop.md](contracts/storyboard-loop.md)（C1~C3）、
  [storyboard-evaluators.md](contracts/storyboard-evaluators.md)（C4~C9）、
  [storyboard-platform.md](contracts/storyboard-platform.md)（C10~C13）、
  [storyboard-evolution.md](contracts/storyboard-evolution.md)（C14~C17）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

执行前校验 + 回放零渲染保证成本纪律（原则二/三）；judge 三段哈希与 gate 短路落进契约
（原则一）；规则库/清单/向量全配置（原则五）；冲突暴露不豁免、清单空降级注明
（原则六诚实边界）。**无新增违规，门禁通过。**
