# 实现计划：声音 Agent 闭环（配音/音效/配乐 + 策略自动进化）

**分支**: `006-sound-agent` | **日期**: 2026-09-20 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/006-sound-agent/spec.md` 的功能规格说明（含 2026-09-20 澄清会话两条决议）

## 概要

交付 `agents/sound/` 包：声音探索执行器（TimingSheet 时序元数据输入 → 策略产参数 →
预算门禁按类型分账 → 三类型适配器生成 → wav 内容寻址 → 四评估器 → 定点归一合成 →
一次性 INSERT）、确定性模拟生成器（参数种子 → numpy 波形 → PCM16 wav 逐字节复现，
声学属性可注入）、四评估器（响度/同步双 gate + ASR/情绪双 proxy，纯 numpy 零新增依赖）、
回放接入与无偏性验收（τ ≥ 0.95 发布阻塞）、做梦层零改动接入（agent_id 泛化 + champion
策略 + 首轮基线）。澄清决议贯穿：同步检测仅时序元数据（不碰视觉工件）、适配器按
TTS/音效/音乐三类型拆分、成本按类型分账。**零新增第三方依赖**。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——numpy（波形合成与响度测量）、标准库 wave（PCM16 写盘）、
blake3、pyyaml、SQLAlchemy Core；τ-b 复用 `core/replay/unbiasedness.py`；
定点归一复用 `core/evaluators/quantize.py`

**存储**: DB 新运营表 `sound_gen_jobs`（迁移 0005，可变中间态 + 唯一键
`(round_id, params_hash)`）；wav 工件 BLAKE3 内容寻址入对象存储；节点走 001 既有
immutable 树（两段式落盘，评估完成即一次性 INSERT——visual 模式，无外部回流）

**测试**: pytest；声学属性注入夹具（响度/同步偏移/错字率/情绪向量可控）；适配器契约
套件（三类型 × 双实现）；无偏性套件（002 口径）+ 注入偏差拒绝用例

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `agents/sound/` 新业务包（→ core 单向依赖，004 同构）

**性能目标**: 单轮探索（≤4 组参数 × 模拟生成 + 评估）< 1 分钟（demo 预算内即可，
无性能敏感路径；wav 合成采样率 16kHz × 秒级时长为 numpy 毫秒级运算）

**约束**: 昂贵动作仅限线上探索（原则三，回放零生成审计）；评估器版本冻结 + 定点归一
（原则一）；两段式落盘 + FAILED 成本照计（原则二）；评估权重/阈值/价目全配置化
（原则五）；人评锚点经 010 纳入（原则六）；覆盖率 ≥85%

**规模/范围**: 1 个 agents 子包（约 12 模块）+ 1 张运营表 + champion 策略 + demo；
不含真实 TTS/音乐凭证、波形-画面对齐（F2）、音乐版权审核链、部署自动化（F9）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 四评估器 deterministic + 实现哈希入版本号（004 `_versioning.py` 同款）；定点归一 6 位小数 | ✅ 满足 |
| 原则二：节点不可变与成本 | 两段式落盘（运营表可变侧 → 一次性 INSERT）；FAILED 成本照常入账；工件内容寻址 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | TTS/音效/音乐生成仅在探索执行器；回放零生成（C13/C15 审计断言）；价目缺失即报错 | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 策略候选回放走 002 沙箱（复用，无新路径） | ✅ 满足 |
| 原则五：单向依赖与配置化 | `agents/sound → core`；权重/阈值/价目/分档全走 configs；做梦层 agent_id 泛化零改动 | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | 模拟 ASR/情绪代理如实标注（实现哈希即版本）；人评锚点经 010 盲评纳入；approve 人工闸门不变 | ✅ 满足 |
| 测试纪律 | TDD；注入偏差拒绝用例；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/006-sound-agent/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── sound-loop.md        # 探索执行器（C1~C3）
│   ├── sound-evaluators.md  # 四评估器与合成（C4~C8）
│   ├── sound-platform.md    # 适配器三类型 × 双实现（C9~C12）
│   └── sound-evolution.md   # 回放/无偏性/做梦/周校准（C13~C16）
└── tasks.md                 # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在 001-005/010 结构上增量）

```text
agents/sound/
├── config.py              # SoundConfig（budget/响度分档/sync 阈值/价目/模拟参数）
├── db.py                  # sound_gen_jobs 运营表定义
├── timing.py              # TimingSheet 模型与校验（重叠/越界执行前拒绝）
├── audio.py               # 程序化音频合成（种子 → numpy 波形 → PCM16 wav，属性注入）
├── evaluators/
│   ├── loudness.py        # rule.loudness_compliance（简化 BS.1770，gate，分档）
│   ├── av_sync.py         # rule.av_sync（事件时间 vs TimingSheet，gate）
│   ├── asr.py             # proxy.asr_transcript（确定性转写代理 + CER 映射）
│   └── emotion.py         # proxy.emotion_music_match（情绪向量距离）
├── platform/
│   ├── base.py            # 适配器协议 + 错误分型 + GeneratedAudio
│   ├── simulated.py       # TTS/SFX/Music 三确定性模拟器
│   └── http_real.py       # 三真实 API 骨架（无凭证跳过）
└── loop.py                # 探索执行器（预算分账/幂等/两段式/评估/落树）
ops/migrations/versions/0005_sound_gen_jobs.py
ops/demo_sound_loop.py     # 闭环演示（quickstart 六步）
configs/movie.yaml         # 追加 sound 段 + evaluator_weights.sound
policies/history/sound/    # champion 策略 + meta.json（做梦接入）
tests/unit/test_sound_*.py；tests/contract/test_sound_platform_contract.py；
tests/integration/test_sound_pg.py；tests/unbiasedness/test_sound_unbiased.py
```

**结构决策**: 声音 = 视觉模式的音频镜像（research 决策 1/7），包结构逐层对齐 004
（platform 三件套 / evaluators 目录 / loop 执行器）；差异点仅三处：适配器按类型三拆分
（澄清 Q2）、同步检测走 TimingSheet 而非视觉工件（澄清 Q1）、评估器四个而非五个
（无 judge 层，技术方案 §2.2）。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 落树节奏 = visual 模式（生成即评即落树，无外部回流）
2. 程序化音频 = numpy 正弦叠加 + 标准库 wave，声学属性（响度/事件时间/错字率/情绪
   向量）由种子注入，零新增依赖
3. 响度 = 纯 numpy 简化 BS.1770，定点输出，不引 pyloudnorm
4. ASR/情绪代理 = 确定性启发式（实现哈希即版本），真实服务走骨架升版本替换
5. 同步 = 事件时间戳 vs TimingSheet（澄清 Q1），不做波形对齐
6. 适配器三类型拆分 + 成本分账（澄清 Q2）
7. 预算门禁/幂等 = 003/004 同构（申请前校验 + 事务复核 + 唯一键派生）
8. 做梦层与 010 周校准零改动接入（agent_id 泛化已成立）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：sound_gen_jobs schema、TimingSheet/SoundGenParams/
  AudioArtifactRef、评估器组合与类型不适用语义、job 状态机
- [contracts/sound-loop.md](contracts/sound-loop.md)（C1~C3）、
  [sound-evaluators.md](contracts/sound-evaluators.md)（C4~C8）、
  [sound-platform.md](contracts/sound-platform.md)（C9~C12）、
  [sound-evolution.md](contracts/sound-evolution.md)（C13~C16）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

回放零生成与预算门禁在契约层可断言（原则三）；评估器版本/定点归一复用 001/004 既有
机制（原则一）；两段式落盘与 FAILED 入账落进 schema 与场景（原则二）；模拟代理如实
标注、人评走 010（原则六）。**无新增违规，门禁通过。**
