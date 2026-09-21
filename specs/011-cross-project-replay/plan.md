# 实现计划：跨项目发现树合并回放（池化复用）

**分支**: `011-cross-project-replay` | **日期**: 2026-09-21 | **规格说明**: [spec.md](spec.md)

**输入**: 来自 `specs/011-cross-project-replay/spec.md` 的功能规格说明（含 2026-09-21 澄清会话两条决议）

## 概要

交付 `core/replay/` 的池化扩展：合并池构建器（按 (Agent, 形态) 分组读入多项目树，
**评估器版本集分组**——跨版本不混池；前置条件 ≥3 棵；可重现 + 文件化快照）、跨项目
匹配（结构键 + 版本集 hash；**冲突即 UNKNOWN + 诊断**——澄清 Q1）、命中分布与稀释
告警（**命中占比判定** + 树数占比参考——澄清 Q2）、跨项目谱系查询（三维索引跨项目
链路）、一致性验收（同树双池得分序列一致）、合并口径无偏性（τ ≥ 0.95 发布阻塞）、
做梦开关（默认关闭 + 快照留痕）。**零新增第三方依赖，零新 DB 表**（只读扩展 +
文件化快照）。

## 技术背景

**语言/版本**: Python 3.11+（uv 管理）

**主要依赖**: 零新增——既有 002（SimulatorPool/规范化匹配/UNKNOWN/分树）、001（三维
索引/谱系）、009（结构键语义）、blake3、pyyaml；τ-b 复用 `core/replay/unbiasedness.py`

**存储**: 无新 DB 表（只读扩展）；`PoolSnapshot` 文件化（`replay/pools/{agent}/{form}/{pool_id}.json`，
git 版本化，同 005/010 惯例）；谱系走 001 三维索引的跨项目读路径

**测试**: pytest；多项目夹具（≥2 项目 × ≥2 树，含版本差异与得分冲突变体）；
合并口径无偏性套件 + 注入偏差拒绝

**目标平台**: Linux（WSL2 + CI）

**项目类型**: `core/replay/` 扩展（业务无关池化机制）+ dreaming 侧开关（最小改动）

**性能目标**: 池构建（≤10 棵树的规模）< 1 秒；回放沿用 002 池实现（无新优化，
前置条件下规模可控）

**约束**: 评估器版本集是匹配的组成（原则一）；回放零生成（原则三审计）；分组/阈值/
显式跨形态/做梦开关全配置化（原则五）；冲突与稀释如实暴露（原则六）；覆盖率 ≥85%

**规模/范围**: 4 个 core 扩展模块 + dreaming 开关 + 快照文件化；不含池化性能优化、
010 校准变更（锚点归属不变）、跨形态自动迁移（显式配置前置）

## 宪章检查

*门禁：阶段 0 调研前已评估；阶段 1 设计后复核（见文末复核结论）。*

| 宪章条款 | 本计划对应设计 | 结论 |
| --- | --- | --- |
| 原则一：评估器版本冻结 | 版本集分组（跨版本不混池）；版本集 hash 是匹配的组成；同版本 config 差异如实标注 | ✅ 满足 |
| 原则二：节点不可变与谱系 | 只读扩展（无树写入）；跨项目谱系查询（三维索引读路径）；快照只增不改 | ✅ 满足 |
| 原则三：昂贵动作仅限线上 | 回放零生成（审计断言）；池构建只读树存储 | ✅ 满足 |
| 原则四：沙箱与前缀不可泄露 | 匹配经 002 规范化精确匹配与观测白名单（复用，无新信息面） | ✅ 满足 |
| 原则五：单向依赖与配置化 | core/replay 扩展（业务无关）；分组/阈值/跨形态/做梦开关全走 configs | ✅ 满足 |
| 原则六：诚实边界与人类锚点 | **冲突即 UNKNOWN**（不编造）；稀释告警如实可见；做梦默认单项目池（未经批准不影响进化） | ✅ 满足 |
| 测试纪律 | TDD；注入偏差拒绝；覆盖率 ≥85% | ✅ 满足 |

**无违规项，复杂度跟踪表不需要。**

## 项目结构

### 文档（此功能）

```text
specs/011-cross-project-replay/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/
│   ├── pooling.md              # 合并池构建与快照（C1~C2）
│   ├── replay-hit.md           # 匹配/冲突/分布/稀释（C3~C5）
│   └── lineage-acceptance.md   # 谱系/一致性/τ/做梦开关（C6~C9）
└── tasks.md                    # 阶段 2 输出（/skill:speckit-tasks）
```

### 源代码（仓库根目录，在既有结构上增量）

```text
core/replay/
├── merged_pool.py       # 合并池构建器（分组/版本集分组/前置条件/可重现）
├── pool_snapshot.py     # 构建快照（文件化落盘 replay/pools/...，只增不改）
├── cross_match.py       # 跨项目匹配（结构键 + 版本集；冲突 → UNKNOWN + ScoreConflict）
├── hit_stats.py         # 命中分布（per-project/合并口径）+ 稀释告警（命中占比判定）
├── cross_lineage.py     # 跨项目谱系查询（三维索引跨项目链路）
dreaming/                # 开关最小改动：replay.pooling.enabled_for_dreaming 读取
configs/movie.yaml       # 追加 replay.pooling 段（min_trees=3、dilution_hit_ratio_threshold=0.7、
                         # allow_cross_form=false、enabled_for_dreaming=false）
replay/pools/            # 数据目录（构建快照，git 版本化）
tests/unit/test_merged_pool.py / test_cross_match.py / test_hit_stats.py / test_cross_lineage.py
tests/contract/test_pooling_contracts.py
tests/unbiasedness/test_merged_pool_unbiased.py
```

**结构决策**: 池化是树/回放基建（业务无关）→ core/replay 扩展（与 002 同层）；
做梦开关是最小改动（读取配置选择池，不特化 dreaming 逻辑）；快照文件化沿用
005/010 惯例。

## 阶段 0：调研（research.md）

产出：[research.md](research.md)。关键决策摘要：

1. 存放 = core/replay 扩展（业务无关池化机制）
2. 版本分组键 = 评估器版本集 hash（同语义的最小单位，跨版本不混池）
3. 冲突即 UNKNOWN + ScoreConflict 诊断（澄清 Q1）；冲突记录供 010/F7 消费
4. 命中占比判定 + 树数占比参考（澄清 Q2）；单项目构成如实标注
5. 前置条件 ≥3 + 可重现（(created_at, project_id) 字典序稳定排序）
6. 分树全局时间排序；一致性验收 = 同树双池得分序列一致
7. 做梦开关默认关闭 + 快照留痕
8. 快照文件化（git 版本化）；谱系只读扩展（无新表）

## 阶段 1：设计与契约

- [data-model.md](data-model.md)：MergedPool/VersionGroup/ScoreConflict/HitDistribution/
  DilutionAlert/PoolSnapshot/CrossProjectLineage 与关键约束（版本分组键/匹配键/排序/分树）
- [contracts/pooling.md](contracts/pooling.md)（C1~C2）、
  [replay-hit.md](contracts/replay-hit.md)（C3~C5）、
  [lineage-acceptance.md](contracts/lineage-acceptance.md)（C6~C9）
- [quickstart.md](quickstart.md)：验证命令 + demo 六步 + 里程碑验收口径

## 宪章复核（阶段 1 后）

版本集分组与匹配键组成把原则一落进匹配语义；冲突即 UNKNOWN 与稀释告警把原则六
落进可机检的诊断；做梦默认关闭防止池化未经批准影响进化；只读扩展与快照只增不改
对齐原则二。**无新增违规，门禁通过。**
