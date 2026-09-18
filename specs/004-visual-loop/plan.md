# 实现计划：视觉 Agent 闭环（五评估器与一致性验收）

**分支**: `004-visual-loop` | **日期**: 2026-09-18 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/004-visual-loop/spec.md` 的功能规格说明

## 概要

交付第二个业务 Agent 包 `agents/visual`：线上探索执行器（参数 → 适配器生成片段 →
五评估器打分 → 合成 → 两段式落盘 → 成本对账）+ 五个评估器的确定性实现（合规门禁 /
美学代理 / 主体一致性 / 闪烁检测 / 电影感 judge 委员会）+ 一致性验收工具（冻结版本
重算对账，逐字节一致率必须 100%）。视频处理引入 numpy + imageio + imageio-ffmpeg
三个依赖（ffmpeg 免系统安装，research 决策 1）；运营状态新增 `visual_gen_jobs` 表
（迁移 0003）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 新增 numpy、imageio、imageio-ffmpeg（帧解码与 ffmpeg 二进制）；
其余复用现有栈；judge 经 003 的 LLM 网关（Mock 后端确定性）

**存储**: 复用树/对象存储（视频工件内容寻址）；新增可变运营表 `visual_gen_jobs`
（迁移 0003，同 003 的两段式形态）

**测试**: pytest；模拟生成器确定性（参数哈希种子 → numpy 程序化帧 → mp4）；
适配器契约套件双实现同跑；评估器重算一致性为核心测试面

**目标平台**: Linux（WSL2 + CI）

**项目类型**: monorepo 第二个 `agents/` 包；core 仅增 `core/evaluators/quantize.py`
（定点归一纯函数，业务无关）

**性能目标**: 单轮闭环（模拟生成器，3 个候选片段，数秒级时长）< 5 分钟（SC-003）；
单片段五评估器评估 < 30s

**约束**: 评估器确定性与版本冻结（原则一）；节点 immutable + 成本入账（原则二）；
视频生成仅在线上探索（原则三）；agents → core 单向（原则五）；覆盖率 ≥85%

**规模/范围**: 1 个 Agent 包 + 5 个评估器 + 1 张运营表 + 一致性验收工具；
不含真实生成凭证、真实模型权重、做梦层

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器确定性与版本冻结 | 五评估器全部确定性实现；版本号携带实现/锚点/提示词哈希；帧采样规则进版本元信息；得分定点归一保证重算逐字节一致 | ✅ 满足 |
| 原则二：节点不可变与谱系 | 两段式落盘复用 003 形态；生成成本逐笔入账；工件内容寻址 | ✅ 满足 |
| 原则三：昂贵动作仅限线上探索 | 视频生成只在闭环执行器内发生；回放零生成；judge LLM 过网关计费 | ✅ 满足 |
| 原则四：沙箱隔离 | 本期策略执行复用 002 沙箱，无新增暴露面 | ✅ 不触及 |
| 原则五：单向依赖与配置化 | agents/visual → core/，与 agents/promo 互不引用；规格/权重/锚点全走 configs | ✅ 满足 |
| 原则六：诚实边界 | 真实生成/真实模型不接凭证；适配器契约约束双实现；启发式代理明确标注（版本哈希即实现哈希） | ✅ 满足 |
| 测试纪律 | TDD；覆盖率 ≥85%；一致性验收为发布阻塞 | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/004-visual-loop/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── visual-loop.md        # 执行器契约（预算门禁/幂等/崩溃隔离/对账）
│   ├── video-gen-adapter.md  # 生成适配器契约（双实现同跑）
│   └── consistency.md        # 一致性验收契约（报告 schema/判定）
└── tasks.md                  # 阶段 2 输出
```

### 源代码（仓库根目录，在 001/002/003 结构上增量）

```text
agents/
└── visual/
    ├── clip.py              # VideoClip 生成编排（适配器调用 → 工件内容寻址落库）
    ├── frames.py            # ffprobe 探测 + 确定性帧采样（采样规则进版本元信息）
    ├── evaluators/
    │   ├── format_compliance.py   # rule.format_compliance
    │   ├── aesthetic.py           # proxy.aesthetic（启发式统计代理）
    │   ├── identity.py            # proxy.identity_consistency（感知哈希嵌入余弦）
    │   ├── flicker.py             # proxy.flicker（亮度直方图抖动 + 频域伪影）
    │   └── cinematic.py           # judge.cinematic（3 提示词成对投票 → [0,1]）
    ├── platform/
    │   ├── base.py          # VideoGenAdapter Protocol + GenJob 模型 + 错误族
    │   ├── simulated.py     # 确定性程序化生成（种子 → numpy 帧 → mp4）
    │   └── http_real.py     # 真实生成 API 骨架（凭证注入）
    ├── loop.py              # 线上探索执行器 + freeze_round_tree（visual 版）
    └── consistency.py       # 一致性验收（重算对账 + 报告 + τ 对接）
core/evaluators/
└── quantize.py              # 得分定点归一（纯函数，业务无关，FR-012）
ops/migrations/versions/0003_visual_gen_jobs.py
ops/demo_visual_loop.py      # 端到端演示
configs/movie.yaml           # visual 段补全（片段规格/预算/锚点集/judge 提示词版本）
tests/unit/test_visual_*.py；tests/contract/test_video_gen_adapter.py；
tests/integration/test_visual_replay.py
```

**结构决策**: 沿用 monorepo 布局。与 003 的 promo 执行器**保持各自实现**（编排形态相似
但泛化为 core 组件为时尚早——YAGNI，第三个 Agent 出现时再提炼，见 research 决策 7）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. ffmpeg/帧解码依赖：numpy + imageio + imageio-ffmpeg（免系统安装）
2. 模拟生成器：参数哈希种子 → 程序化帧 → mp4，逐字节可复现
3. 帧采样确定性规则进评估器版本元信息
4. 启发式评估器的确定性设计（实现哈希即版本号组成部分）
5. judge 委员会：3 固定提示词 + 锚点集成对比较，胜率映射 [0,1]，提示词/锚点版本冻结
6. 得分定点归一（6 位小数）保证重算逐字节一致
7. 与 promo 执行器保持各自实现（YAGNI，不提前泛化）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：实体、visual_gen_jobs 表、视觉树形态、评估明细键约定
- [contracts/visual-loop.md](contracts/visual-loop.md)、[video-gen-adapter.md](contracts/video-gen-adapter.md)、
  [consistency.md](contracts/consistency.md)
- [quickstart.md](quickstart.md)：端到端验证场景

## 宪章复核（阶段 1 后）

五评估器版本号均携带实现/数据/提示词哈希（原则一）；生成只在线上闭环（原则三）；
得分定点归一使"重算逐字节一致"可达成（FR-012 支撑原则一的复现性）；visual 不引用
promo（原则五）。**无新增违规，门禁通过。**
