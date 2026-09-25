# CineFlow：影视全 Agents 自我进化生产系统

L1 基建第一批交付：**发现树（core/tree）+ 评估器框架（core/evaluators）**。

- 发现树：探索尝试以不可变节点落盘（frozen dataclass + PostgreSQL 触发器 +
  应用账号权限回收双保险），工件以 BLAKE3 内容寻址存储天然去重，谱系按
  （项目, Agent, 策略版本）三维可查；
- 评估器框架：评估器以 `evaluator_id@version` 全局唯一注册（版本冻结、
  非确定性拒绝、人类锚点例外），合成评分实现硬规则门禁 + 加权求和，
  权重来自形态配置（`configs/*.yaml`）并随树冻结快照。

工程约定以项目宪章 `.specify/memory/constitution.md` 为最高标准
（原则一/二不可协商：评估器版本冻结、节点 immutable + 成本必入账）。

## 目录结构

```text
core/
├── tree/             # 发现树：models / store / db / artifacts / errors
└── evaluators/       # 评估器：base / registry / composite / weights / errors
configs/
└── movie.yaml        # 形态配置示例（评估器权重，切换形态零代码改动）
tests/
├── stubs.py          # 桩评估器唯一定义来源
├── unit/             # SQLite 内存库 + 本地工件存储，全离线
└── integration/      # Docker 化 PostgreSQL + MinIO（无 Docker 自动跳过）
ops/
├── dev.compose.yml   # 本地开发依赖（PostgreSQL 16 + MinIO）
├── alembic.ini       # 迁移配置（DSN 走环境变量 CINEFLOW_PG_DSN）
├── migrations/       # Alembic 迁移（首个迁移含 immutable 触发器 + REVOKE）
├── audit_immutable.py  # immutable 审计（随机抽样复算 score，门禁脚本）
└── demo_tree_eval.py   # 端到端演示（六步场景，输出 JSON 报告）
specs/001-tree-evaluators/  # 本特性的 spec-kit 设计文档（spec/plan/tasks/contracts）
```

## 开发环境搭建

```bash
uv sync   # 安装依赖（Python 3.11 由 .python-version 锁定）
```

## 测试

```bash
# 单元测试（离线，SQLite 内存库）
uv run pytest tests/unit

# 单元测试 + 覆盖率门禁（宪章里程碑：core ≥ 85%）
uv run pytest tests/unit --cov=core --cov-report=term-missing --cov-fail-under=85

# 集成测试（需 Docker；不可达时自动跳过）
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
docker compose -f ops/dev.compose.yml up minio-init          # 建工件 bucket（一次性）
uv run alembic -c ops/alembic.ini upgrade head
uv run pytest tests/integration -m integration
```

注意：`ops/dev.compose.yml` 中的账号密码**仅用于本地开发**，不得用于任何共享环境。

## 快速演示

```bash
uv run python ops/demo_tree_eval.py
```

依次演示：immutable 拒绝与一致性哈希、三维谱系查询、失败节点成本入账、
注册校验（重复/非确定性拒绝 + 人类锚点放行）、合成评分（gate/加权）、
配置快照冻结；输出 JSON 报告，全部通过时退出码 0。
演示用 SQLite 内存库，生产切 PostgreSQL/MinIO 只需更换 DSN 与 ArtifactStore 实现。

## immutable 审计（每日门禁）

```bash
uv run python ops/audit_immutable.py   # 默认读 CINEFLOW_PG_DSN，可用 --dsn 覆盖
```

随机抽 100 个历史节点（不足则全量）按冻结快照复算 score 比对，
任一不一致即非零退出并输出 JSON 差异明细。

## 回放与沙箱（功能 002）

```bash
# 回放语义单测（进程内模拟器，离线）
uv run pytest tests/unit -k "replay or clock or kendall"

# 对抗测试套件（合并阻塞门禁；本地 Docker 用加固容器，CI 用 gVisor；
# 无 Docker 报错而非跳过）
uv run pytest tests/adversarial -m adversarial

# 无偏性验收（发布阻塞门禁：Kendall τ ≥ 0.95）
uv run pytest tests/unbiasedness -m unbiasedness

# 端到端演示：小树 → 模拟器 → 沙箱容器回放 → 轨迹 JSON（生成调用恒 0 断言）
uv run python ops/demo_replay.py

# 3 万节点基准（SC-006：全程 < 10 分钟；优先 PG，不可用退 SQLite）
uv run pytest tests/integration/test_replay_benchmark.py -m integration
```

门禁要点：回放零生成（max_generation_calls 装配强制归零）；probe 规范化
精确匹配、无匹配 UNKNOWN 不得分；策略沙箱无网络/无凭证/只读/限额；
作弊三件套（peek_latent / timing_side_channel / hash_oracle）全拦截。

## 宣发闭环（功能 003）

```bash
# 单元测试 + 覆盖率门禁（core + agents 合计 ≥ 85%）
uv run pytest tests/unit --cov=core --cov=agents --cov-fail-under=85

# 平台适配器契约套件（模拟实现全过；真实实现无凭证按用例跳过）
uv run pytest tests/contract

# 闭环端到端演示（模拟平台 + Mock 网关，离线可跑）：
# 一轮探索 → 合规/预算门禁 → 投放 → 幂等二次触发 → 回流冻结 → 入池回放 → 进化报告
uv run python ops/demo_promo_loop.py

# 回流管道（生产 PG；无 DSN 返回退出码 2）
uv run python ops/ingest_metrics.py --round-id <round_id>
```

门禁现状：预算门禁（单轮 ≤ 总预算 × pilot_ratio，分为单位事务扣减）、
轮次幂等（唯一键 + 确定性派生 ID，二次触发零重复扣费）、成本对账三方一致
（树内 + 待回流 == 网关 + 适配器）、回流指标越界拒绝、写入即冻结。

真实渠道接入是凭证配置的运维动作（代码路径不变）：平台适配器
`PROMO_PLATFORM_BASE_URL` / `PROMO_PLATFORM_API_KEY`；LLM 网关
`OPENAI_BASE_URL` / `OPENAI_API_KEY`。缺凭证不假装投放（原则六）。

## 视觉闭环（功能 004）

```bash
# 单元测试 + 覆盖率（五评估器单测含退化输入不崩溃断言）
uv run pytest tests/unit -k visual

# 视频生成适配器契约套件（模拟实现全过；真实实现无凭证跳过）
uv run pytest tests/contract -k video_gen

# 一致性验收与视觉确定性（重算逐字节一致、注入漂移必拒）
uv run pytest tests/unit -k "consistency or visual"

# 闭环端到端演示（模拟生成器 + Mock 网关，离线可跑）：
# 一轮 3 候选片段（含违规/超限样例）→ 对账 → 幂等 → 冻结回放 → 一致性报告
uv run python ops/demo_visual_loop.py
```

门禁现状：五评估器全确定性（quantize 6 位小数定点归一，版本号携带实现/
采样/提示词/锚点哈希，judge.cinematic 另含输出预算）；合规 0 分短路不跑 judge
（省 LLM 成本）；judge 调用全经网关计费；一致性验收为发布阻塞（一致率 100% + τ 分档门禁）。

真实生成平台接入是凭证配置的运维动作：`VISUAL_GEN_BASE_URL` /
`VISUAL_GEN_API_KEY`（缺凭证不假装生成，原则六）。

## 声音闭环（功能 006）

```bash
# 单元测试（执行器/TimingSheet/音频合成/配置/四评估器/合成/回放/做梦接入）
uv run pytest tests/unit -k sound

# 生成适配器契约套件（三类型 × 双实现同构；真实实现无凭证跳过，置 CINEFLOW_CONTRACT_STUB=1
# 可让真实实现分支对着本地 stub 实跑）
uv run pytest tests/contract -k sound

# PG 集成（0005 迁移真实执行 + 两段式落盘全链路 + 分账对账，需 Docker PG）
uv run pytest tests/integration -k sound -m integration

# 无偏性验收（发布阻塞：回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝）
uv run pytest tests/unbiasedness -k sound

# 闭环端到端演示（模拟生成器，离线可跑）：
# 一轮 4 组参数（2 TTS+1 SFX+1 music）落树分账 → 预算门禁 → 幂等 →
# 四评估器 + gate 短路 + 定点重算 → 无偏性 τ → 做梦首轮基线
uv run python ops/demo_sound_loop.py

# 声音策略做梦接入（agent_id="sound" 演示档 M=8，dreaming 零改动）
uv run pytest tests/unit/test_sound_dreaming.py
```

门禁现状：四评估器全确定性（双 gate 响度分档/音画同步 + 双 proxy
ASR/情绪匹配，实现哈希入版本号，quantize 6 位定点归一；类型不适用分量
跳过并按适用权重归一，口径进 config_snapshot.composite_policy）；预算门禁
按 gen_type 三类型分账（缺价目即报错）；无偏性 τ≥0.95 为发布阻塞；
做梦层 agent_id 泛化零改动接入（champion 策略
`policies/history/sound/` + meta.json 谱系）。

真实声音平台接入是凭证配置的运维动作：`SOUND_TTS_*` / `SOUND_SFX_*` /
`SOUND_MUSIC_*` 环境变量（缺凭证不假装生成，原则六）。

## 剪辑闭环（功能 007）

```bash
# 单元测试（镜头库/场景分区/EDL 四层校验/确定性渲染/配置/五评估器/合成/执行器/回放/做梦接入）
uv run pytest tests/unit -k editing

# 渲染适配器契约套件（双实现同构；真实实现无凭证跳过，置 CINEFLOW_CONTRACT_STUB=1 可实跑）
uv run pytest tests/contract -k editing

# PG 集成（0006 迁移真实执行 + 唯一键 (round_id, edl_hash) 幂等 + 两段式全链路 + 对账，需 Docker PG）
uv run pytest tests/integration -k editing -m integration

# 无偏性验收（发布阻塞：回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝）
uv run pytest tests/unbiasedness -k editing

# 闭环端到端演示（确定性模拟渲染器 + Mock 网关，离线可跑）：
# 一轮 3 组 EDL 落树对账 → 非法 EDL 0 渲染 0 成本 → 预算门禁与幂等 →
# 五评估器 + gate 短路 + 定点重算 → 无偏性 τ → 做梦首轮基线
uv run python ops/demo_editing_loop.py

# 剪辑策略做梦接入（agent_id="editing" 演示档 M=8，dreaming 零改动）
uv run pytest tests/unit/test_editing_dreaming.py
```

门禁现状：五评估器全确定性（三 gate 时长/镜头分布/转场规则库——与执行前
校验同一配置库 + proxy.pacing_curve 分段基准距离 + judge.narrative_flow
EDL 摘要成对比较，版本号 = 提示词+锚点集+摘要函数+输出预算四段哈希（末段取自
形态配置 `editing.judge.max_tokens`）；gate 短路不跑 judge，quantize 6 位定点
归一）；渲染编码固定单线程确定性档（同 EDL 逐字节复现，004 x264 flake 根因
同源消除并已回移 004）；EDL 非法执行前拒绝
（0 渲染 0 成本）；无偏性 τ≥0.95 为发布阻塞（实测 τ=1.0）；做梦层
agent_id 泛化零改动接入（champion 策略 `policies/history/editing/` +
meta.json 谱系）。

真实渲染服务接入是凭证配置的运维动作：`EDIT_RENDER_BASE_URL` /
`EDIT_RENDER_API_KEY` 环境变量（缺凭证不假装渲染，原则六）。

## 分镜闭环（功能 008）

```bash
# 单元测试（剧本/ShotList 三层校验/分镜卡渲染/配置/摘要/五评估器/合成/执行器/回放/schema 快照）
uv run pytest tests/unit -k "storyboard or shotlist"

# 预演渲染适配器契约套件（双实现同构；真实实现无凭证跳过，置 CINEFLOW_CONTRACT_STUB=1 可实跑）
uv run pytest tests/contract -k storyboard

# PG 集成（0007 迁移真实执行 + 唯一键 (round_id, shotlist_hash) 幂等 + 两段式全链路 + 对账，需 Docker PG）
uv run pytest tests/integration -k storyboard -m integration

# 无偏性验收（发布阻塞：回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝）
uv run pytest tests/unbiasedness -k storyboard

# 闭环端到端演示（确定性模拟渲染器 + Mock 网关，离线可跑）：
# 一轮 3 组 ShotList 落树对账 → 四类非法 0 渲染 0 成本 → 预算门禁与幂等 →
# 五评估器 + gate 短路 + 定点重算 → 无偏性 τ → 做梦首轮基线
uv run python ops/demo_storyboard_loop.py

# 分镜策略做梦接入（agent_id="storyboard" 演示档 M=8，dreaming 零改动）
uv run pytest tests/unit -k "storyboard_dreaming"
```

门禁现状：五评估器全确定性（三 gate 景别语法/覆盖率/轴规则——与执行前校验同一配置
规则库 + proxy.emotion_alignment 读预演画面帧像素（`board_render.storyboard_cards`
同一帧产出函数，帧哈希与渲染元数据校验一致，禁止两套帧）+ judge.script_fit
ShotList 摘要成对比较，版本号 = 提示词+锚点集+摘要函数+输出预算四段哈希（末段
取自形态配置 `storyboard.judge.max_tokens`）；gate 短路不跑 judge，quantize
6 位定点归一）；ShotList 三层执行前校验（引用行存在/场景承接含关键
行/档位枚举）违规 0 渲染 0 成本；预演渲染编码固定单线程确定性档（同 ShotList 逐字节
复现）；无偏性 τ≥0.95 为发布阻塞（实测 τ=1.0）；做梦层 agent_id 泛化零改动接入
（champion 策略 `policies/history/storyboard/` + meta.json 谱系）。

真实预演渲染服务接入是凭证配置的运维动作：`STORYBOARD_RENDER_BASE_URL` /
`STORYBOARD_RENDER_API_KEY` 环境变量（缺凭证不假装渲染，原则六）。ShotList schema
（`schema_version=1.0.0`：字段名/枚举值文档化）为视觉线（004）与剪辑线（007）的
下游接入契约，快照稳定性由 `tests/unit/test_storyboard_replay.py` 断言。

## 剧本降级模式（功能 009）

```bash
# 单元测试（工件/配置/摘要/导出对接/七评估器/合成/执行器/回放对比与采纳/判据材料/CLI）
uv run pytest tests/unit -k screenplay

# 契约套件（C12 禁用自动进化 + C16 周校准接入）
uv run pytest tests/contract -k screenplay

# PG 集成（0008 迁移真实执行 + 唯一键 (round_id, stage, params_hash) 幂等 +
# 分阶段两段式落盘 + 成本对账，需 Docker PG）
uv run pytest tests/integration -k screenplay -m integration

# 无偏性验收（发布阻塞：回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝；
# 未达标不得产出对比报告）
uv run pytest tests/unbiasedness -k screenplay

# 闭环端到端演示（确定性夹具 + Mock 网关 + SQLite，离线可跑；六步见 quickstart）
uv run python ops/demo_screenplay_loop.py

# 产出与人工策略提交通道（CLI：produce / submit / compare / adopt / reject / evidence）
uv run python ops/screenplay.py produce --round r1 --topic "病房里的三个月" --policy <版本>
uv run python ops/screenplay.py submit --source-file <策略.py> --by <提交人>
```

### 为什么这个 Agent 不自动进化（宪章原则六，可机检）

剧本环节的评估信号**过弱**（判据数据尚未积累），按原则六"诚实边界"以**降级模式**接入：

- **策略由人编写与提交**：`policies/history/screenplay/{版本}.py` + `.meta.json`
  （版本 = 源码 BLAKE3 前 12 位；父版本/提交人/时间/静态检查结果/名单审计）；
- **dreaming 禁止为剧本生成候选**：配置 `dreaming.no_auto_evolve_agents: [screenplay, dev]`
  ——`run_dream_round(agent_id="screenplay")` 命中名单即在**候选生成之前**抛
  `AutoEvolutionForbiddenError`（**显式拒绝、非静默跳过**；0 候选 0 计费 0 落盘）。
  契约套件三重保证：拒绝行为断言 + 默认配置实值断言（防配置漂移）+ 审计断言
  （候选生成次数恒 0、守卫早于生成调用的源码顺序、dreaming/010 源码无 Agent 名字面量）；
- **人工改策略走沙盘**：候选策略过静态检查（import 白名单/禁危险内建/禁私有属性）后在
  模拟器池上**回放对比**（零 LLM，只读历史节点）→ **人工采纳才更新部署指针**
  （未采纳指针逐字节不变，机检；拒绝同样留痕且理由非空）；
- **升级判据持续积累**：`calibration/upgrade-events/{agent_id}/{周期}.json` 不可变快照
  （按 agent 分目录：同周期双写不互相覆盖；阈值快照 + judge 信度/漂移/门禁违规分布/人评
  锚点数 + 逐项取值形态 + **系统结论 meets|below** + 人推翻留痕，推翻不改写系统结论字段）；
  **升级为自动进化不在本特性范围**，须另立决议并修订宪章。

### 技术事实与口径

- **七评估器**：四门禁（`rule.beat_structure` 节拍结构 / `rule.page_minutes` 页数-时长换算 /
  `rule.scene_character` 场景-角色一致性 / `rule.dialogue_action_ratio` 对白行占比）+
  两代理（`proxy.entity_consistency` 实体一致性 / `proxy.timeline_conflict` 时间线冲突）+
  `judge.dramatic_tension`（**仅大纲阶段**；非大纲阶段"不适用"，合成按适用权重归一，
  不伪造 0 分）；gate 短路不跑 judge，quantize 6 位定点。
- **分阶段产出**：outline → scenes → script 各自独立节点与评估（`stage` 字段）；网关缓存键
  与响应哈希落盘（回放核对），同输入跨轮次命中缓存零成本复现。
- **回放匹配槽 = 策略可复现结构键**（stage/策略版本/模型与采样档/目标时长/计划摘要）：
  生成产物摘要不进匹配键——回放只读历史节点、零 LLM（原则三）；UNKNOWN = 该结构无历史
  覆盖，记 0 分并提示扩大记录（不编造）。
- **无偏性 τ ≥ 0.95 为发布阻塞**（FR-013/SC-008）：未达标不得产出回放对比报告；实测 τ=1.0。
- **010 周校准接入**：盲评对象 = 大纲阶段 top-k（`observation_match={"stage": "outline"}`
  为 010 的**通用**观测槽过滤机制，010 内无 screenplay 特判）。
- **与 008 分镜 schema 对接**：`export_segment(工件) -> ScriptSegment`，字段名/枚举值由
  双向快照断言锁定（任一侧漂移即红）。
- **人工策略首版**：`policies/history/screenplay/fa6b7bca77ed.py`（谱系根；三阶段工艺由粗到
  细：大纲 135 行/块 → 分场 45 行/场 → 剧本 27 行/场，按目标页数铺满）。

体量提示：正文按目标页数铺满（90 分钟 × 45 行/页 = 4050 行），故 90 分钟档单轮生成提示词为
MB 级（实测 4 秒、Mock 价目下约 $2.5/轮，含 judge）；演示档用缩放页数窗口控制体量。

## 开发 Agent 降级模式（功能 017）

链首的开发 Agent（选题 / IP 评估 / 立项组合）以**降级模式**接入：人工编写的**题材方向探索
策略**驱动一轮产出（立项组合工件 = 本轮推荐立项的多个选题 + 组合内"进入生产"指向）→ 全量
落发现树（记录）→ 2 门禁 + 2 模拟代理信号打分 → 人工改策略过静态检查后在**回放沙盘**上与
部署版本零成本对比 → **人工采纳才更新部署指针**。机制复用 009 的降级骨架：通用件已下沉到
业务无关的 `core/degraded/`（策略通道 / 回放对比 / 采纳留痕 / 判据材料），009 与 017 共用同一份
实现（009 侧只做薄适配，导出名/签名/默认目录逐字不变）。

```bash
# 单元测试（工件/配置/模拟信号/四评估器/合成/轮次循环/回放对比与采纳/判据材料/导出对接/CLI）
uv run pytest tests/unit -k dev

# 契约套件（C1~C18 聚合 + 禁用自动进化三重机检）
uv run pytest tests/contract -k dev

# PG 集成（0010 迁移真实执行 + 唯一键 (round_id, params_hash) 幂等 +
# 分两段落盘 + 成本三方对账 + FAILED 成本照计，需 Docker PG）
uv run pytest tests/integration -k dev -m integration

# 无偏性验收（发布阻塞：回放 vs 真实重跑 τ ≥ 0.95；注入偏差 100% 拒绝；
# 未达标不得产出对比报告）
uv run pytest tests/unbiasedness -k dev

# 闭环端到端演示（确定性夹具 + Mock 网关 + SQLite，离线可跑；六步见 quickstart）
uv run python ops/demo_dev_loop.py

# 产出与人工策略提交通道（CLI：produce / submit / compare / adopt / reject / evidence）
uv run python ops/dev.py produce --round r1 --policy <版本>
uv run python ops/dev.py submit --source-file <策略.py> --by <提交人>
uv run python ops/dev.py evidence --period 2026-W38   # → calibration/upgrade-events/dev/{周期}.json
```

### 为什么这个 Agent 不自动进化（宪章原则六，可机检）

选题环节的评估信号**噪声最大**（技术方案 §2.2 警示；判据数据尚未积累），按原则六"诚实边界"
以**降级模式**接入：

- **策略由人编写与提交**：`policies/history/dev/{版本}.py` + `.meta.json`（版本 = 源码 BLAKE3
  前 12 位；父版本/提交人/时间/静态检查结果/名单审计）；首版 `34525518074d` 为谱系根，
  部署指针 `deployment.dev.current_policy_version = 34525518074d`；
- **dreaming 禁止为开发 Agent 生成候选**：配置 `dreaming.no_auto_evolve_agents: [screenplay, dev]`
  ——`run_dream_round(agent_id="dev")` 命中名单即在**候选生成之前**抛
  `AutoEvolutionForbiddenError`（**显式拒绝、非静默跳过**；0 候选 0 计费 0 落盘），
  部署门禁对该 Agent 一律返回禁止名单且优先于"证据不足"。契约套件三重保证：
  拒绝行为断言 + 默认配置实值断言（防配置漂移）+ 审计断言（候选生成次数恒 0、
  守卫早于生成调用的源码顺序、dreaming/010 源码无 Agent 名字面量）；
- **人工改策略走沙盘**：候选策略过静态检查（import 白名单/禁危险内建/禁私有属性）后在
  模拟器池上**回放对比**（零 LLM、零生成，只读历史节点；策略执行带**超时**，且策略只被喂
  `plan(inputs, config)`——**不交付任何环境对象**，探测由宿主代执行）→ **人工采纳才更新
  部署指针**（未采纳指针逐字节不变，机检；拒绝同样留痕且理由非空）；
- **升级判据持续积累**：`calibration/upgrade-events/dev/{周期}.json` 不可变快照（全量判据阈值
  快照 + 逐项取值形态 + **系统结论恒非"达标"** + 待补齐阈值项清单 + 人推翻留痕，推翻不改写
  系统结论字段）；**升级为自动进化不在本特性范围**，须另立决议并修订宪章。

### 技术事实与口径

- **四评估器**：二门禁（`rule.slate_structure` 条目数区间/方向标识唯一/要点齐备 +
  `rule.slate_combination` **组合层核心门禁**：方向重复率、标记数量与指向合法性）+
  二代理（`proxy.genre_regression` 模拟历史同类型票房回归 / `proxy.buzz_heat` 模拟舆情热度）；
  权重来自 `evaluator_weights.dev`（电影 0.6/0.4、短剧 0.4/0.6，按适用权重归一，不伪造 0 分）；
  gate 判 0 ⇒ 总分 0 且**不跑代理分量**，quantize 6 位定点。
- **单一产出**：一轮一份立项组合工件（条目数区间与"进入生产"标记数区间由形态配置约束，
  越界即门禁判 0 并点名具体违规项；**不由代码兜底**）；工件内容寻址（canonical JSON → BLAKE3）
  落对象存储，节点含 `policy_version`；`slate_match_key` 只含策略可复现结构键（生成产物摘要
  不进匹配键）——回放只读历史节点、零 LLM（原则三）。
- **最小池门槛**：可比对树数 < `dev.min_comparable_trees`（两形态默认 3）即**拒绝产出**对比报告
  并报错（含实测树数与门槛），不得用极少样本产出"谁更好"的结论。
- **无偏性 τ ≥ 0.95 为发布阻塞**（FR-013/SC-008）：未达标不得产出回放对比报告。
- **策略执行的隔离口径**：回放对比路径的策略为 `plan(inputs, config)` 纯规划形态、不交互
  模拟器，依**宪章 v2.0.0 原则四例外条款**适用；**附两项落地义务**——**执行超时** +
  **"不向策略执行交付任何环境对象"的守护断言**（策略既不 `observed()` 也不 `probe()`）。
- **与下游交接（G2/018）**：`export_slate(工件) -> dict`——导出面含 `schema_version` + 逐条方向
  要点 + 组合级模拟源标注，供 018 立**字段级** `FieldParity`（**字段级声明属 G2**）；本特性只
  保证导出确定、可反解、**不因缺陷丢条目**（含悬空标记），**不预写对侧** parity 声明。工件
  schema 版本 `1.0.0`，字段/枚举变更须同时升版并更新快照断言。

### 诚实边界（本特性最核心的工程对象）

- 两个代理信号取自**模拟数据源**（产物 / 对比报告 / 判据材料**三处**标注"**非真实商业数据**"，
  标注携带数据源参数摘要；真实票房库/舆情接口属 G3 渠道范围，受预算门禁与账单对账约束）；
- 本环节**无 judge 层、无人类锚点**，且 **010 明确排除本 Agent**
  （`specs/010-weekly-calibration/spec.md:157` 保持原样）——故判据材料的**相关性/漂移阈值项
  必然读作"无法评价（来源缺失）"**、系统结论**恒非"达标"**、并列出待补齐阈值项作为继续观察
  条件。**这是既定结果，不是缺陷**，不得用伪 judge / 伪锚点补齐（补齐须另立决议，不含本特性）；
- 冷启动池子不足 → 拒绝出报告；回放未命中的树记 0 分并提示扩大记录（不编造、不放宽匹配）。

## 真实渠道与账单对账（功能 019）

真实渠道不再靠"网关自报"：调用前有**分档预算门禁**（预估额超余量即拒，被拒的那一笔**不入账**），
调用后落**按时间索引的运行记录**（"连续运行 ≥7 天"从叙述变成可机检事实），**厂商账单**可导入并与
网关记账**逐项对账**（六类差异 + 不可解释项 100% 告警），**两维价目**（峰谷 × 厂商缓存命中）可表达
且改价后历史复算**逐字节不变**。判定全在业务无关的 `core/billing/`（五模块：`budget` / `bill` /
`reconcile` / `calibration` / `runlog`），CLI 与演示只做薄转发（原则五）。

```bash
# 单元面（配置缺项/门禁拒绝/拒绝零成本分支/账本并发/价目四格/账单导入/对账六类/运行记录/CLI/纯度）
uv run pytest tests/unit -k billing

# 契约面（C1~C16 聚合 + 本特性的对抗/篡改面）
uv run pytest tests/contract -k billing

# 离线六步演示（Mock 后端 + 夹具账单；零真实调用、零外部网络、零凭证）
uv run python ops/demo_billing.py

# 额度台账 / 最小规模校准 / 扩量（raise-tier 定点改写配置额度）
uv run python ops/billing.py tiers      --channel llm
uv run python ops/billing.py calibrate  --channel llm --tier screenplay --from-records --measured-usd <实测>
uv run python ops/billing.py raise-tier --channel llm --tier screenplay --limit-usd <额> \
    --calibration <id> --by <人> --reason <理由>

# 账单导入与对账（import-bill 不联网：人工上传厂商导出文件）
uv run python ops/billing.py import-bill --channel llm --file <账单> --bill-id <批次> --period <周期>
uv run python ops/billing.py reconcile  --channel llm --period <周期> --bill-id <批次> \
    --gateway-report <网关账目 JSON>       # 退出码：0 无告警 / 1 有告警 / 2 用法或配置错误

# 每日告警门禁（只读既有产物）与连续运行窗口机检
uv run python ops/billing.py alert-check --channel llm
uv run python ops/billing.py runs        --channel llm --window-days 7
```

`--channel llm` 是**示例取值**：渠道 id 由配置 `budget.channels` 声明，命令取值须与配置一致
（`core/billing/` 零渠道/环节/格式字面量，写死即红）。

### 技术事实与口径

- **产物布局** `billing/{channel}/`：`ledger.json`（跨进程账本：flock + 原子替换 + 单调 `revision`）、
  `alerts.jsonl`（只增告警留痕，`kind` 取值域六值固定）、`bills/{批次}.json`（一次性快照 + 同批次重产拒绝）、
  `reports/{周期}.json`（差异报告）、`calibrations/{id}.json`（校准记录）、`runs/{日期}.json`（运行记录）。
- **分档额度** `budget.tiers` 的**键 = 各 `.chat(` 调用点声明的 `stage=` 环节 id**（静态断言：8 处调用点
  的取值 ∈ 两形态档位键集，不发明 agent 名 ↔ 环节 id 映射）；缺 `budget` 段 / 缺档 / 缺峰谷声明在
  **装配期**拒绝启动（不取码内默认，FR-001）。
- **零成本分支**：预算拒绝走 `BudgetRefusedError` 的**显式前置分支**——被拒的那一笔
  `llm_calls` / `llm_tokens` / `generation_api_cost_usd` **全零**、后端 **0 次调用**、网关
  `call_count` / `total_cost_usd` 不变；同轮**已发生**的花费照记（"花了的钱"与"没花的钱"可分辨）。
- **两维价目** `llm.profiles.*.price_matrix`：四格 `peak_miss` / `peak_hit` / `off_peak_miss` /
  `off_peak_hit`，**声明即四格齐备**（缺格或某格缺键即装配报错、不回落基础价）；未声明的档案四格同价
  （既有配置零改动）。峰谷归属 = **调用开始时刻**按 `budget.peak_windows.timezone` 判定，区间口径
  **闭开 `[start, end)`**（跨峰谷切换不拆分）；归属口径三处可见（账目报告口径备注 / 档位快照 /
  校准记录 `note`）。
- **本地网关缓存与厂商缓存正交**：同一提示词二次调用命中网关内内容哈希缓存 ⇒ **零成本、零后端调用、
  不占额、不进任何格位**；两维价目的"命中"**只**指**厂商** prompt 缓存（来源 = 响应 usage 的命中 token
  数；未报告 ⇒ 取未命中档 + 登记「厂商未报告命中 token（按未命中计）」，保守高估不按 0 计）。
- **对账分类**：按（档案 id, 周期）逐项配对，六类固定（计费口径 / 未入账 / 时序错位 / 免费额度与折扣 /
  币种汇率 / 未结账）；分类**只由账单声明的驱动列**（`line_kind` / `amount_sign`）决定，映射不到即
  `unclassified` ⇒ 不可解释 ⇒ 告警（不用金额阈值或符号启发式）。报告必带 `bill_refs[]`；**无账单批次
  拒绝产出**（不产"零差异"报告）；时序错位/未结账**不得**据此判定网关记账有误（留待下期）。
- **运行记录与窗口机检**：一次真实调用一条 entry，`head_digest` **链式摘要**（改写或删除任一条即报错，
  不静默取）；`runs --window-days 7` 判**覆盖 ∧ 连续双条件**（`covered_days` **只计 `source=real`**；
  断档逐段如实列出、**禁止插值补齐**；容差放开时可 `meets=true` 而 `continuous=false`，通过不谎报"连续"）。
- **改价不漂移 + 定点改写（口径与版本纪律）**：历史节点快照**永不重写**，快照形状按**键集**分派
  （`price_matrix` 在不在键集中，不按版本号猜），改价后按冻结快照复算逐字节不变；
  `raise-tier` 经 `core/yaml_edit.py` **定点改写**配置额度（其余段与注释逐字节不变）+ 写 `calibrated_by`
  + `alerts.jsonl` 留痕，拒绝时配置**一字不改**。
- **校准先决**：校准记录 append-only（同键重产拒绝，`system_digest` 机检）；`raise-tier` 六条先决
  （有记录 / 同渠道同环节 / `passed` / 样本量 ≥ `min_samples` / 未超期 / 偏差在容差内）任一不满足即拒绝
  并留 `uncalibrated_raise`。

### 新增门禁：预算与账单差异告警（每日）

`.github/workflows/billing_alerts.yml`（cron `47 2 * * *` 错峰 + `workflow_dispatch`）跑
`uv run python ops/billing.py alert-check --channel llm`：报告存在未解释项/超阈值，或 `--since` 之后
新增门禁类告警 ⇒ **非零退出即告警**（与 `ops/cost_regression.py` 同一定位）。该门禁**只读既有产物、
零真实调用、零凭证**；报告尚未产出（冷启动）时放行。

### 诚实边界（本特性最核心的工程对象）

- **只做 LLM 渠道**：本特性只实例化一个 LLM 渠道（`budget.channels` 恰好一个，声明多个即拒绝装配）。
  媒体渠道（分镜渲染 / 视觉 / 声音）与投放侧协议校准整条**结转 G4**，实现不得为它们发明前置条件
  （账号 / 凭证 / 平台名）；生成侧真实厂商对接属 **G2**。
- **模拟 vs 真实**：本特性交付**机制**与**离线可复现验证**——单元面、契约面与演示全走 **Mock 后端 +
  夹具账单**，零真实花费、零外部网络、零凭证。**真实渠道的最小规模校准与 ≥7 天连续运行属"待运营"**：
  需运营给出凭证与额度档的最终数字，未标定期间按最小规模档运行并在配置 `note` 与报告里如实标注"未标定"。
- **厂商费率数字不由代码发明**：交付配置**不声明** `price_matrix`（矩阵取值是厂商费率，运营给定前不发明）；
  四格取价能力由夹具举证，两形态真实配置的 `price_note` 以**文字**写明峰时/缓存口径。
- **单主机账本**：跨进程账本为**单主机**文件账本；多主机共享额度需换 PostgreSQL，**未做**（登记为边界）。
- **上界估算仍可能被超**：预估额为保守上界（prompt `len//2` + completion 满额），实际更高则余量可为负，
  **如实入账 + `over_limit` 告警并拒绝后续调用**（不回滚、不改写）。
- **账单来源形态**：本特性第一实现 = **导出导入**（`import-bill` 不联网）；API 拉取按同构的 `source=api`
  表达、**不实现联网拉取**（不引入新凭证面）。
- **不做**：`Decimal` 金额重构（沿用 float + 容差二分）、自动比价路由与汇率引擎、`CostRecord` 增列
  角色/档案（走报告层，历史节点不可按角色/档案回溯，如实登记）、web 侧写入与 billing 看板（前端只读）。

## 短剧形态的真实投放与日级回流（功能 020）

把"日级只在配置里"变成**真的按日运转**，并把 C 路径投放接进 019 的同一套门禁；再把短剧线积累的
评估器校准结论按**显式声明的可比性条件**回灌电影线作先验（**只迁结论、不迁权重**）。
门禁与账单面的权威口径见 [真实渠道与账单对账（功能 019）](#真实渠道与账单对账功能-019)；
短剧形态的模拟生成边界见 [短剧形态试水作品（功能 015）](#短剧形态试水作品功能-015)。

- **周期量纲由 cadence 派生**（`calibration.period_days`）：日级 ⇒ `YYYY-MM-DD`、周级 ⇒ ISO 周；
  口径唯一实现落在业务无关的 `core/calibration/periods.py`（周级标签**逐字节不变**）；
  窗口统一**半开** `[start, start + period_days)`（`end - start == period_days` 机检），
  窗口口径与生效日**进产物**（`window_semantics` / `period_days` / `note`），跨口径变更日的比较必须标注；
- **日级回流**（`agents/promo/daily.py` + 新表 `promo_daily_metrics`）：按**归属日**（平台显式给出的
  `metric_date`，缺失即**显式失败**、不以采集时刻兜底）分片，唯一性键 =（活动, 周期）；
  同一活动跨多日各采一次**合法且零覆盖**，同（活动, 周期）重复采集**幂等拒绝**；
  锚点写入即冻结（INSERT-only 触发器），覆盖判定**只计 `source=real`**、断档**逐段如实列出、不插值**；
- **归属日 / 采集墙钟 / 平台时间戳**三者在日级覆盖视图 `days[]` 里**并列可见**（齐备率 100%）；
- **渠道命名空间**（019 的兼容性扩展）：`budget.channels.<id>.tiers.<环节>` + `declared_channels` /
  `channel_for_adapter` / `tiers_of` / `tier_of`；`sole_channel()` 的硬拒绝退役为"按声明渠道集合分派"，
  **旧扁平 `tiers` 仍可读并显式归一**（多渠道 + 扁平 ⇒ 报错，不静默误判）；同一档位不得跨渠道串用；
- **投放受同一门禁**：投放调用走 `core/billing/runlog.py` 的 `RecordingChannelCall`（投放面**唯一**
  包装点）——超限**调用前拒绝、平台 0 次调用、零入账**；与 015 进程内**按轮上限**两腿同时生效且
  **口径可分辨**；声明真实而 `PROMO_PLATFORM_*` 缺失 ⇒ **装配期拒绝启动**，绝不静默回落模拟；
- **校准结论迁移**（`core/calibration/transfer.py` + 独立脚本 `ops/transfer.py`）：迁移件 append-only
  （`calibration/transfers/{transfer_id}.json`，同键重产拒绝、系统字段改写即 `system_digest` 校验失败），
  可比性条件**由配置声明**（`calibration.transfer.conditions`，缺项即报错），不可比 ⇒ **拒绝迁移并逐条
  记原因**；采纳走**人工两键**（`transfer-confirm` / `transfer-shelve`），**不改变任何既有节点的
  `eval_breakdown` 与得分**、**不自动改权重**（权重再拟合仍走 010 的提案 → 人工确认）。

```bash
# 单元面（周期量纲/半开窗口/归属日/日级分片/漂移量纲/渠道命名空间/投放门禁/迁移件/两形态差异）
uv run pytest tests/unit/test_period_cadence.py tests/unit/test_promo_daily_ingest.py \
               tests/unit/test_billing_channels.py tests/unit/test_calibration_transfer.py -q
# 契约面（010 周级机检保持绿 + 迁移面 C15/C17 + 019 既有断言）—— 与功能 019 的契约面同一入口
uv run pytest tests/contract/test_calibration_contracts.py tests/contract/test_transfer_contracts.py -q
uv run pytest tests/contract/test_billing_contracts.py -q

# 离线端到端演示（七步；零真实花费、零外部网络、零凭证）
uv run python ops/demo_shortdrama_feedback.py

# 两形态渠道面与迁移面
uv run python ops/billing.py channels --config configs/shortdrama.yaml   # llm + media（含量矩阵）
uv run python ops/billing.py channels --config configs/movie.yaml        # 仅 llm（不登记投放渠道）
uv run python ops/transfer.py transfer-report --data-dir calibration
```

### 诚实边界（本特性最核心的工程对象）

- **交付的是机制 + 离线复现**：产物与报告一律写「**机制已就绪 / 真实回流待运营**」；演示与全部单测
  在**无凭证**环境恒可跑（全模拟链路），**"模拟被标为真实"次数恒 0**（`RUN_SOURCES` 单点取值域 +
  覆盖只认 `real` + 结论文案取值域三重机检）；
- **"短剧线真实数据回流 ≥2 周"属运营侧墙钟 + 凭证前提**：短剧态窗口下限取 **14 天**
  （= 立项书 G4 验收原文，不是发明数字；电影态保持 7），未达标时 `ops/billing.py runs --channel media`
  **退出码 1 并逐段报出缺口与差值**，**不得**以模拟件凑数后声称"已达成"；
- **平台名 / 凭证取值 / 预算档数字 / 投放环节档位取值 / 合规审查完成判据**：均属运营侧输入
  （`spec.md` 开放问题 1~4）——本特性只交付"**缺项即报错 + 最小规模档 + `note` 标未标定**"的形状，
  **不发明数字**；`PROMO_PLATFORM_*` 变量名**不改名、不新增**（反向机检常驻）；
- **归属日字段未标定**：平台侧是否提供"指标归属日"字段仍未确认 ⇒ 未标定期间回流条数为 0 且原因明确
  （「平台未提供指标归属日」），**禁止**以拉取/采集时刻兜底（字段名钉死 `metric_date`）；
- **迁移不迁权重**：`conclusion` 内**禁止**出现权重键（结构断言），`transfer.py` 零 `refit` import、
  零 `write` 入口到树/库；跨形态迁移的**真实覆盖天数**是**装配面声明的观测**并在条件条目里标注其来源
  （离线演练夹具标 `fixture_drill`，**不得**据以宣称真实回流已达成）；
- **明确不做**：G5 多形态插件验证、B 路径真实生成厂商对接（见
  [docs/二期升级路径-真实生成与投放.md](docs/二期升级路径-真实生成与投放.md)）、多租户/公网服务化、
  自动权重迁移、Decimal 金额重构、web 侧写入口。

## 电影长片全链路（功能 018）

七环节链 `dev → script → storyboard → visual → sound → editing → promo`：链首插入 017 的开发
Agent（选题产出 → 剧本输入的**字段级交接**），链路拓扑与交接契约一行不动，体量按形态配置声明的
**排练档**缩档；样片包补三处证据面（可复现口径不变 / 成本三方对账 / 逐环节评估分量与标注），
性能画像落**报告侧**。

```bash
# 单元面（七环节阶段表与清单同步不变量 / 排练档与两处时长一致 / 交接守恒 / 分量与标注入包 /
# 成本三方 / 性能画像与预算门禁）
uv run pytest tests/unit -k pilot
uv run pytest tests/unit -k storyboard   # 索引块网格：容量下界/量子上界、逐镜往返、版本含生效网格参数

# 契约面（C1~C13 聚合 + 本特性的对抗/篡改面）
uv run pytest tests/contract -k pilot

# 七环节串链（排练档按配置生效；`--minutes` 取**生效档值**，浮点分钟）
uv run python ops/pilot.py run     --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id film-run
uv run python ops/pilot.py resume  --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id film-run
uv run python ops/pilot.py inspect --data-dir pilot --run-id film-run --package

# 性能画像 → pilot/profiles/{run_id}.json（报告侧，不进五件套）；`--clock` 必填无默认
# 退出码：0 达标 / 1 未达标或不可评价 / 2 用法错误
uv run python ops/pilot.py perf --form movie --config configs/movie.yaml \
    --data-dir pilot --run-id film-run --clock system

# 离线七步演示（确定性夹具 + 临时目录 + 固定时钟；零真实花费、零外部网络）
uv run python ops/demo_pilot.py
```

`--minutes` 取**生效档值**：须等于**生效**成片时长 ÷ 60（排练档下 = `pilot.rehearsal.scale` 的
`target_duration_s` ÷ 60，示范 0.5 = 30 秒；形态原值档下长片为 90），与预检的"两处时长一致"
不变量同批定稿——不一致即拒绝启动并点名两处实测值。`--genre-bounds` / `--audience` 是链首立项
环节的运行级输入映射，缺项即预检拒绝。

### 七环节与排练档

- **链条与环节不随形态改变**：阶段元组恰为七元组，每环只调对应 Agent 的既有 loop 入口——链首
  `dev` 只调 017 的轮次入口，编排层**零新增落树路径**（常驻静态断言）。把 `dev` 从阶段表移除
  （模拟漏同步）即红：阶段元组 / 阶段表 / `AgentConfigs` / 运行时装配含建表 / 预检四处清单 /
  产物 kind 与内容类型 / 两形态声明**七处同批**，漏一处即多条一致性断言同时红。
- **缩档只改配置**：`pilot.scene_count` / `pilot.lines_per_scene` / `pilot.rehearsal` 两形态均须
  声明，**缺项即拒绝启动**（不取码内默认，唯一解析者是 `PilotConfig`，消费侧经参数注入）。
  `status: declared` 时生效体量为 `scale` 覆盖值；`unstandardized` 时**不覆盖**（形态原值在 force）
  并如实标注"未标定"；`work_kind` 区分排练与真实作品——排练产物**不得**被标为真实作品。
- **两处时长一致（SC-012①）**：`screenplay.target_duration_min × 60` 必须等于**生效**
  `editing.target_duration_s`（容差 `1e-6`），运行级目标时长 ×60 亦然；任一不一致即拒绝启动并
  点名两处实测值（不静默择一）。长片形态原值此前的 120 秒与 90 分钟矛盾按长片语义修正为 5400 秒。
- **索引码泛化为 R×C 块网格**：容量 `2**(R·C)`，约束 `2**C ≤ render.width` 与
  `2**(R·C) ≥ 该形态派生镜头数`（长片原值 2700 镜 ⇒ 如 `rows: 2, cols: 8`；要更少镜头就改
  `clip_spec.duration_seconds`，**不是**放宽容量校验）；派生镜头数只有一份公式
  （`agents/pilot/scale.py`）；网格参数进评估器实现哈希 ⇒ **改网格即升版本**（含仅改配置取值），
  旧 `evaluator_id@version` 的既有节点与已落盘工件逐字节不变。
- **`dev → script` 字段级交接**：守恒等式 `下游读取集 == renames(上游 − 丢弃) ∪ 运行级 ∪ 派生`；
  `reads` 三类（承接含改名 / 运行级 / 派生）每键**恰一类**，`dropped` 是与 `reads` **并列**的独立集，
  取数依据（`entries`/`production_marks`）单列；取数入口要求"本轮进入生产"标记**恰好一条**且指向
  组内条目——悬空 / 越界（多条）/ 要点缺失即**下游拒绝启动**并点名（不静默取第一条、不伪装成
  选题为空）。链上无 `dev` 时回落运行级 `pilot_inputs`，生效来源随机读可见（禁止静默择一）。

### 样片包三处补强（新增字段落既有件内，仍五件套）

1. **可复现口径不变**：仍是 `PACKAGE_FILES` 五件套 + `verify_package`；同输入同配置、独立工件根
   重跑**五件套逐字节一致**，新增字段（`source` / `channels` / `work_kind` / `eval_breakdown` /
   `volume`）同样确定性——无墙钟、无绝对路径、无进程内顺序依赖。
2. **成本三方对账**：`cost.json` 覆盖七环节，口径 = 运行记录阶段成本 ⨯ 各 Agent 落盘账目 ⨯
   **网关记账增量**（环节边界**只读**采样 `LLMGateway.total_cost_usd`，不构造网关、普查仍 13 处）；
   可比性分级声明（`dev`/`script` 为 LLM 腿专属 ⇒ 逐项相等；混合腿 ⇒ 至少；`sound` 无 LLM 调用 ⇒ 恒 0）；
   `FAILED` 照常入账，被预算门禁**拒绝**的那一笔零入账且与已发生花费可分辨；平台腿与 `day`/`period`
   窗口累计的不可比情形**如实备注**（不静默比对、也不静默跳过）。
3. **逐环节评估分量与来源标注**：`state.json` 逐环节 `eval_breakdown`（`evaluator_id@version` 与分量值）
   **取自树节点原文**（不改写、不归一化、不重算；派生产物显式标 `derived: true`，不与缺项混同），
   缺任一环即**拒绝装配**；`manifest.json` 的 `stages[].source`/`channel` + 顶层 `channels` 汇总 +
   `work_kind`，预检报告同步 `stages: {stage_id: source}`——取值只来自装配面声明，不推断、不默认。

### 诚实边界（本特性最核心的工程对象）

- **"真实渠道" = LLM 腿真实 + 平台侧按配置（默认模拟）**：今天真实可用的只有 LLM 腿（019 已交付
  前置预算门禁、账单对账与运行记录）；平台五环节（分镜/视觉/声音/剪辑/宣发）的真实适配器**代码在位
  但未交付使用**——`docs/pilot-upgrade-manifest.json` 的 B 路径（视频/音频生成）与 C 路径（真实投放）
  状态均为 `not_delivered`。**禁止**产出"真实渠道全链路已跑通"这类结论（该表述只在七环节 `source`
  全为 `real` 时成立，机检）；全模拟（默认）与部分真实（如 `llm: http` 而平台模拟）都必须如实分层
  标注，模拟被标为真实的次数恒为 0。
- **真实生成的账号、凭证、平台名与预算档属运营侧前置输入**：本特性不发明凭证名、平台名与额度数字，
  也不重写适配器协议、不新开凭证面；未具备期间保留"模拟生成"标注与预检报告 `credentials_checked: false`
  （不假装验过凭证）。渠道失败**禁止**静默回落模拟并照常计费。
- **排练档与性能阈值的数字属运营侧输入**：表达机制全部落地（配置声明 + `work_kind` + 未标定如实
  登记），**待给定的是数字**；未给定期间按"未标定"登记、不发明数字，也不把排练产物标为真实作品。
- **性能画像的边界**：墙钟只进报告侧 `pilot/profiles/{run_id}.json`、**不进**逐字节比对的五件套；
  **固定时钟运行恒 `not_evaluable`**（那组时间戳是常量，只能证明可复现），各环节时间戳全等时即便
  声明系统时钟也被证据推翻；「专用基准环境下的绝对标定」属三期 WS3 第 3 项，**不在本特性**。
  单次运行无法自证用过系统时钟，故口径由"入口显式声明 + 退化时间戳交叉核验"两层承担，残余风险如实登记。
- **`dev` 仍是人工策略驱动的降级模式**：策略来源是人、必须过静态检查、必须人工采纳；例外三项替代
  约束（装载即静态检查 / 执行超时 / 策略零环境对象·不触网关）落机检，**不升级为自动进化**。
- **测试与演示全走模拟后端 + 夹具 + 确定性时钟**：零真实花费、零外部网络；真实运行属运营动作。

## 做梦层（功能 005）

```bash
# 单测 + 覆盖率（core + agents + dreaming 口径 ≥ 85%）
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85

# 静态拦截与过拟合判定
uv run pytest tests/unit -k "static or overfit"

# 5 轮做梦端到端演示（变异生成器 + 真实沙箱回放 + 模拟审批 + 曲线/谱系）
uv run python ops/demo_dreaming.py

# 做梦沙箱 e2e 与 M=128 全量基准（需 Docker）
uv run pytest tests/integration -m integration -k dreaming
```

做梦管线：digest（最近 K 轮落盘报告）→ 候选生成（MutatorGenerator 占位 /
LLMGenerator 经网关计费）→ 静态检查（违规不回放不记分）→ 沙箱串行回放
→ reward = pareto_auc（梯形归一化权威口径）− λ·并行惩罚 → 过拟合筛选
（最近树只做 validation）→ 人工审批闸门（未 approve 不得进部署指针，
SC-005 机检）→ DreamRound/meta.json 落盘（只增不改，git 承担审计）。

审批操作（生产形态）：
```bash
# 审批单生成于 dreaming/tickets/{round_id}.approval.json；人工确认后：
uv run python -c "from dreaming.approve import decide; decide(
    'dreaming/tickets/<round>.approval.json', approver='<姓名>',
    decision='approved', reason='<理由>', config_path='configs/movie.yaml')"
```

## 外环周校准（功能 010）

人评/平台真值锚点 → 偏差检测 → append-only 台账与信度报告 → 权重再拟合提案
（约束岭回归自动拟合候选，人工仅 confirm/shelve 两键，无编辑路径）。
锚点落 `calibration_anchors` 表（迁移 0004，INSERT-only 触发器双侧强制冻结）；
派生产物文件化于 `calibration/rounds|ledger|reports|proposals|snapshots/`（随 git 版本化）。

```bash
# 单测 / 契约（零泄露、提案门禁、版本不变机检）/ PG 集成（触发器双侧证明）
uv run pytest tests/unit -k calibration
uv run pytest tests/contract -k calibration
uv run pytest tests/integration -m integration -k calibration   # 需 Docker PG

# 端到端演示（确定性夹具 + SQLite + 配置临时副本，离线可跑）
uv run python ops/demo_calibration.py
```

周校准操作（生产形态，`ops/calibrate.py`，DSN 经 --dsn 或 CINEFLOW_PG_DSN）：
```bash
# 1. 生成 top-k 盲评清单并落盘轮次（周期默认上一 calibration.period_days 周）
uv run python ops/calibrate.py round --agent visual --period-start 2026-09-14 --period-end 2026-09-20

# 2. 人评录入（JSON 条目文件 [{node_id, score, reviewer}]；非法/重复条目拒绝并计数）
uv run python ops/calibrate.py intake --round <round_id> --file anchors.json

# 3. 收口轮次：配对 → 偏差 → 台账/快照/信度报告落盘 → 轮次 closed
uv run python ops/calibrate.py close --round <round_id>

# 4. 按周期重建信度报告（读台账）
uv run python ops/calibrate.py report --period 2026-W38

# 5. 超阈偏差生成权重再拟合提案（首轮记基线不判超阈；负相关禁止提案）
uv run python ops/calibrate.py propose --round <round_id>

# 6. 人工两键门禁：confirm 生效（新版本 1.0.0+w{哈希} 注册 + configs 定点改写保注释）
#    或 shelve 搁置（零变更）；--by 为确认人，强制必填
uv run python ops/calibrate.py confirm --proposal <proposal_id> --by <姓名>
uv run python ops/calibrate.py shelve  --proposal <proposal_id> --by <姓名>
```

## 跨项目池化（功能 011）

多个项目的发现树按 `(Agent, 形态)` 分组合并进同一个模拟器池：回放时同一**结构键**
（策略可复现的 gen_params）+ 同一**评估器版本集**在池内全部项目树中查找，命中即复用该
历史得分（不再 UNKNOWN）。池化是 `core/replay` 的**只读扩展**（无新 DB 表、无新依赖），
快照文件化于 `replay/pools/{agent}/{form}/{pool_id}.json`（只增不改 + git 版本化）。

```bash
# 单元测试（构建/快照/匹配/分布/谱系/一致性与做梦开关）
uv run pytest tests/unit -k "merged_pool or cross_match or hit_stats or cross_lineage"
uv run pytest tests/unit -k "pooling_acceptance"

# 契约聚合（C1~C9 全场景 + SC-002/003/004/006 机检）
uv run pytest tests/contract -k pooling

# 合并口径无偏性（发布阻塞：τ ≥ 0.95；注入偏差 100% 拒绝）
uv run pytest tests/unbiasedness -k merged

# 端到端演示（quickstart 六步：构建 → 跨项目命中 → 冲突 UNKNOWN → 稀释告警 →
# 一致性 + τ → 做梦开关默认关闭；退出码 0，报告 JSON 落 stdout）
uv run python ops/demo_merged_pool.py
```

配置（`configs/movie.yaml` 的 `replay.pooling` 段，全配置化，缺项即拒绝）：

| 配置项 | 默认 | 含义 |
| --- | --- | --- |
| `min_trees` | 3 | 前置条件：同 Agent 同形态树 ≥ N 棵才可建池（不足即拒绝并注明） |
| `dilution_hit_ratio_threshold` | 0.7 | 稀释告警判定口径：某项目**命中占比**（该项目命中数 / 总命中数）超阈即告警 |
| `allow_cross_form` | false | 跨形态合并需显式开启（未开启即拒绝并入其他形态的树） |
| `enabled_for_dreaming` | false | 做梦层是否使用合并池（默认关闭 → 单项目池，保守闸门） |

**稀释控制**：`hit_stats` 产出 per-project 与合并口径**双报告**；判定只看**命中占比**
（树数占比同报告作参考维度）；单项目构成（占比 1.0）必然超阈并如实标注"单项目构成"；
告警**如实可见但不阻止**回放，也不自动回退（原则六）。

**诚实边界**：跨项目同结构键同版本集但**得分不同 → UNKNOWN + ScoreConflict 诊断**
（不取均值、不取最新、不编造）；跨版本集不命中（版本集是匹配的组成，原则一）；
冲突记录进命中分布 `conflicts` 字段，供 010 校准与 F7 漂移检测消费。

**做梦接入（最小改动）**：`dreaming/pooling.py::select_dreaming_pool` 读配置选池——
开启且前置条件满足 → 合并池（`MergedSimulatorPool` 与 002 `SimulatorPool` 同 `build` 接口，
`dreaming.pipeline` 零改动）；默认关闭或前置不足 → 单项目池并注明"未启用：…"；
开关状态与前置判定 100% 入构建快照。

## judge 漂移监控（功能 012）

按 `(agent, evaluator_id@version)` 读 010 已积累的**锚点得分分布快照序列**，对比
**滑动窗口基线**（最近 `window` 周期、不含当前周期）给出**两维指标**——分布距离 PSI（主）
+ 分位数位移 p25/p50/p75/p90（辅）——任一超阈即判漂移；**只读** 010 产物、零生成/零 LLM。
产物文件化于 `calibration/drift/{metrics,status,dispositions,reports}/`（只增不改 + git 版本化）。

```bash
# 单元测试（模型/配置/统计原语/检测/口径版本/状态机/门禁/报表/接线）
uv run pytest tests/unit -k "drift"

# 契约聚合（C1~C8 全场景 + SC-002 系统只写 suspect / SC-003 证据拒绝 100% / SC-005 只读）
uv run pytest tests/contract -k drift

# 一轮检测 + 报表（CLI；数据目录默认 calibration/，无 010 快照时产出空报表）
uv run python ops/calibrate.py drift --period 2026-W39

# 端到端演示（quickstart 六步，退出码 0，报告 JSON 落 stdout）
uv run python ops/demo_judge_drift.py
```

配置（`configs/movie.yaml` 的 `calibration.drift` 段，全配置化，缺项即报错）：

| 配置项 | 默认 | 含义 |
| --- | --- | --- |
| `window` | 5 | 滑动窗口基线周期数（不含当前周期；升版点切分基线） |
| `buckets` | 10 | 分桶数（须与 010 快照分桶数一致，不一致即报错） |
| `psi_threshold` | 0.2 | 分布距离超阈线（> 即判漂移；0.1~0.2 为关注带） |
| `quantile_threshold` | 0.1 | 分位数最大位移超阈线 |
| `min_samples` | 3 | 样本下限：当前周期或基线窗口不足即"样本不足"（不硬判） |
| `suspect_weight` | 0.5 | `suspect` 的 judge 权重系数（分级处置降权） |
| `confirmed_exclude` | true | `confirmed_drift` 是否排除出合成（权重归零） |
| `scope_kinds` | `["judge"]` | 检测范围（evaluator_id 前缀）；proxy/rule 需显式纳入 |
| `double_signal` | 见配置 | 双信号规则：漂移 ∧ 010 信度低于 target → 强化告警（级别升级） |

**判定与版本**：`detector_version = drift_detector@1.0.0+{算法+阈值哈希}` 进每条检测记录；
同一口径同输入 → 记录逐字节一致（重复检测幂等）；口径升级 → 新版本且**历史判定不回溯**；
评估器升版 → 新版本**独立记基线**（版本边界取自 010 台账），旧版本数据不混入。

**分级处置（原则六：系统只说不确定，处置权在人）**：

| 状态 | 谁写 | 合成门禁 | 部署证据接口（F9 前置） |
| --- | --- | --- | --- |
| `normal` | 默认/人工恢复 | 权重不变 | 允许 |
| `suspect` | **仅系统**（超阈自动登记） | judge 权重 ×0.5 | **拒绝**（含触发指标引用） |
| `confirmed_drift` | 仅人工处置 | judge 权重归零（排除） | **拒绝**（含处置留痕引用） |
| `false_alarm` | 仅人工处置 | 恢复原权重 | 允许 |

处置流程（人工留痕不可改写）：`register_suspect`（系统，超阈）→ `dispose`（人工两键）：
确认漂移 → `confirmed_drift`（动作 `deactivate` 停用 / `reanchor` 换锚点升版，新版本新基线）；
判为误报 → `false_alarm` → 恢复 `normal`（动作 `restore`）。命令行：

```bash
# 处置入口（缺 --by/--reason 即拒绝；处置后自动重建报表）
uv run python ops/calibrate.py drift --period 2026-W39 \
  --dispose judge.cinematic@1.0.0 --conclusion false_alarm --action restore \
  --by 校准负责人 --reason "样本骤降导致分布抖动，非真实漂移"
```

**接线**：visual / editing / storyboard / screenplay 四处 loop 在**合成前**调用
`apply_gate(weights, drift_gate)`（各一行，未接线时权重原样）；权重变化 →
composite 版本哈希变化 → 自然升版，历史节点不受影响。promo 与 sound 无 judge 层，不接线。

**诚实边界**：漂移只判分布变化、**不判原因**（需结合人评锚点，010 信度即该锚点量化）；
样本不足/首周期/缺口周期/无数据一律如实标注不硬判；F6 的 ScoreConflict 只作报表附注
（无持久化来源即注明"无持久化来源"），**不参与阈值判定**。

## 前端可视化（功能 013）

只读视图：**发现树浏览器**（三维过滤 + 节点详情 + 谱系链路）与**进化曲线看板**
（逐轮 reward + 塌缩标注 + 成本汇总 + 010 信度/012 漂移徽标）。`web/` 是宪章 v1.1.0
原则五新条款下的 monorepo **唯一目录例外**，只读纪律由**三重机检**证明：

| 机检 | 落点 | 命令 |
| --- | --- | --- |
| ① 路由表无写动词 | `web/server.py` 的 `ROUTES`（仅 GET；写请求 405/404 零副作用） | `uv run pytest tests/contract -k web` |
| ② 只读角色无写权限 | 迁移 `0009_web_readonly_role` 建角色 `cineflow_web`（全表仅 SELECT、写动词 REVOKE、未来表默认只 SELECT） | `uv run pytest tests/integration -m integration -k web`（真实 PG） |
| ③ 源码无写调用与写 SQL | `web/` 零 import `core/agents/dreaming`；无写 SQL；唯一写盘模块 `web/export.py`（写入仅限配置的导出目录，经 `_target()` 包含性校验） | `uv run pytest tests/contract -k web` |

```bash
# 起只读服务（默认 127.0.0.1:8080；仅 GET；无写端点）
export CINEFLOW_WEB_DSN="postgresql+psycopg://cineflow_web:<口令>@localhost:5432/cineflow"
uv run python -m web.server --config configs/movie.yaml
#   浏览器打开 http://127.0.0.1:8080/（树浏览器）与 http://127.0.0.1:8080/board（看板）

# 静态导出（同一查询层预生成 JSON + 资产拷贝 → web/dist/，可离线浏览）
uv run python -m web.export            # 需要 DSN：树相关面板读只读库；文件面板不依赖 DB
python -m http.server -d web/dist 8081 # 任意静态服务器挂载导出目录即可离线看两视图

# 测试与演示
uv run pytest tests/unit -k web        # 查询层/页面/导出/门禁/同源
uv run pytest tests/contract -k web    # 路由表/静态断言/同源分层校验契约
uv run python ops/demo_web.py          # 端到端演示（quickstart 六步，退出码 0）
```

配置（`configs/movie.yaml` 的 `web` 段，全部配置化；缺项即报错）：

| 配置项 | 默认 | 含义 |
| --- | --- | --- |
| `host` / `port` | `127.0.0.1` / `8080` | 仅回环默认（内部工具，不做公网暴露假设；端口 0 = 临时端口，测试用） |
| `dsn_env` | `CINEFLOW_WEB_DSN` | **只读角色** DSN 的环境变量名（口令不落盘；DSN 缺失时服务照常起，树接口 503） |
| `token` | `""`（空） | 非空则 `/api/*` 需 `Authorization: Bearer <token>` 或 `?token=`（静态资产不含数据，不受约束） |
| `page_size` | 50 | 单页容量上限（请求参数不得超过该值） |
| `data_dirs` | `policies/dreaming/calibration/pools` | 文件化产物只读根目录（与 CLI/JSON 报告同源） |
| `export_dir` | `web/dist` | 静态导出目录 |

**数据同源（分层校验，机检）**：有既有 JSON 报告产物的面板逐字段比对——进化曲线 ↔ 005
`build_curve`、信度 ↔ 010 报告、漂移 ↔ 012 报表、谱系 ↔ 005 `build_lineage`；树/节点接口
以 DB 为权威源（字段语义与 001 落库口径一致）。取数不重算：成本汇总 = 节点成本记录的
`generation_api_cost_usd` 按 (Agent, ISO 周) 合计。

**诚实边界**：看板不承载任何操作入口（录入/审批/部署永远走 CLI，页面无表单无写请求）；
无登录/多租户/HTTPS（内部工具，默认回环）；工件只给哈希与元信息，**不做媒体播放**；
缺失数据一律如实空态（不伪造）；导出快照有节点详情上限（默认 2000，超出在 manifest 如实标注），
超大库请用在线服务；页面自动化覆盖"能取数、能渲染"（node + DOM 桩真实执行两视图），
视觉观感与交互细节需人工打开页面查看。

## 策略部署自动化（功能 014）

达到证据门槛的候选策略**自动接班**，人工 approve 转为**事后抽检**；抽检否决立即回滚并恢复
全人工审批（宪章 v1.1.0 原则六新条款）。机制全在 `core/deployment/`（业务无关，dreaming 侧
只在轮次收口处调用一次），产物文件化在 `deployment/`（git 版本化、只增不改），唯一"写"
是部署指针的定点改写。

**证据门槛（三要件 + 前置，"缺证据即拦截"）**

| 要件 | 口径来源 | 判定 |
| --- | --- | --- |
| 前置：无偏性验收 | 002 `verify_unbiasedness` 结论 | 未通过/缺失 → 整体**证据不足** |
| ① 回放 reward 对比 | 011 池化回放产物（同池同口径） | 候选必须**严格高于**现部署 |
| ② validation 排名 | 005 防过拟合口径（并列取最劣名次） | 不跌出前 20%（`gate.validation_top_ratio`） |
| ③ judge 漂移 verdict | 012 `deploy_evidence_verdict`（全部相关版本） | 任一 suspect/confirmed_drift → 拦截 |

判定优先级：`forbidden_agent`（009 名单 screenplay/dev）> `insufficient_evidence` >
`blocked` > `eligible`；**只有 `eligible` 会部署**。无 judge 层的 Agent（promo/sound）
漂移要件标 `not_applicable` 且**默认保守拦截**（`allow_without_judge=false`）。

**模式状态机与影子模式**：`manual`（默认，现状不变）→ `shadow` → `auto`；
**`manual → auto` 禁止直连**，`shadow → auto` 需影子期**双下限**（时长 + 覆盖候选数）满足，
`recalibration_required` 存在时 auto 一律拒绝。影子期判定照跑、**部署指针零变更**，产对照报告
（放行/拦截、拦截理由分布、与人工决策的**差异分类四类**、误入率分子分母与口径）；
误入率可从事件留痕重算 == 报告值（SC-007）。

**渐进抽检与否决回滚**：前 `spot_check.first_n` 次（默认 5）自动部署**全量复核**，之后按
`ratio`（默认 0.2，即每 5 次抽 1 次）；长期未复核**只告警、不自动视为通过**（超期阈值
`spot_check.pending_alert_days`：电影 7 天、短剧 2 天——投放节奏密集则复核窗口更短）。
**抽检否决 = 一个逻辑事务三件事**：①指针回滚到前一部署版本 ②模式回 `manual`
③标记"门槛需重新标定"——回滚目标工件缺失时显式报错且**模式已回全人工**（绝不停留在不确定
状态）；重新开 auto 必须重新标定 + **重跑影子期**。部署后漂移（F7 转
suspect/confirmed_drift）→ 产**回滚评估记录**（`trigger=drift_assessment`），**不自动回滚**，
处置权在 012 的人工流程。

```bash
# 模式：查看 / 切换（切换需 --by/--reason；门禁在 core 内判定，拒绝会打印缺口）
uv run python ops/deploy.py mode --data-dir deployment
uv run python ops/deploy.py mode --set shadow --by ops --reason "开启影子期" --data-dir deployment

# 评估（唯一入口；模式从 deployment/mode.json 读真实状态；证据由命令行注入）
uv run python ops/deploy.py evaluate --agent visual --candidate <版本> \
  --unbiasedness att.json --validation val.json --judges judge.cinematic@1.0.0 \
  --drift-dir calibration --reward-candidate 0.62 --reward-deployed 0.55 \
  --reward-source replay/pools/pool-x.json --data-dir deployment

# 影子对照报告（含误入率与机检重算）
uv run python ops/deploy.py shadow-report --period 2026-W39 --agent visual --data-dir deployment

# 抽检：产任务 / 列待复核与逾期告警；否决回滚（三件事）；部署后漂移的回滚评估
uv run python ops/deploy.py spot-check --deploy-event deployment/deploys/<ts>-visual.json --data-dir deployment
uv run python ops/deploy.py spot-check --list --pending-alert-days 14 --data-dir deployment
uv run python ops/deploy.py veto --record deployment/spot_checks/<ts>-visual-seq1.json \
  --by reviewer --reason "产出质量不达线" --data-dir deployment
uv run python ops/deploy.py assess-drift --agent visual --drift-dir calibration --data-dir deployment

# 测试与演示
uv run pytest tests/unit tests/contract -k "deployment or deploy_ or shadow or gate or mode"
uv run python ops/demo_deploy_gate.py    # 端到端六步演示（退出码 0）
```

配置（`configs/movie.yaml` 的 `deployment` 段，全部配置化；缺项即报错）：

| 配置项 | 默认 | 含义 |
| --- | --- | --- |
| `mode_default` | `manual` | 模式默认值（manual/shadow/auto），未切换时不落盘 |
| `gate.validation_top_ratio` | 0.2 | validation 排名容许线（005 口径） |
| `gate.require_unbiasedness` | `true` | 无偏性是否作前置（显式 false 才豁免） |
| `gate.allow_without_judge` | `false` | 无 judge 的 Agent 是否放宽（默认保守拦截） |
| `shadow.min_days` / `min_candidates` | 14 / 20 | 影子期双下限（**立项书要求的 ≥2 周**） |
| `spot_check.first_n` / `ratio` | 5 / 0.2 | 渐进抽检：前 N 次全量，之后按比例 |
| `spot_check.pending_alert_days` | 7（短剧 2） | 待复核超期告警阈值（天）；只告警，结论仍只能由人签署 |
| 禁止名单 | 复用 `dreaming.no_auto_evolve_agents` | 不另立名单（缺项即报错，空名单等于放行一切） |

**诚实边界（原则六）**：
- **真实 2 周影子期的运行属运营**——本特性交付机制、计时门禁与对照报告，长期数据由运营积累；
- "部署" = 更新部署指针（005 语义）：**真实发布系统对接不在本特性**；
- 池化回放对比等**证据产物由既有路径（005/011/012）产出**，本特性只读取与判定（不重跑回放）；
- 同周期多候选按 reward 择一并如实记录（**不批量连推**）；影子报告按 (周期, Agent) 分档，
  同周期多 Agent 需错开周期档；
- 无偏性/漂移证据缺失一律判"证据不足"（宁可拦截，不推测放行）。

## 短剧形态试水作品（功能 015）

**一句话**：形态差异全部在 `configs/*.yaml`（零代码切换），链式交接 + 通用轻量 DAG
执行器 + 可复现样片包；全链路由**确定性模拟生成器**产出。（功能 018 起链首插入 `dev`，
链条为**七环节**——见「电影长片全链路（功能 018）」。）

```bash
# 端到端七步演示（配置完整性 → 七环节串链出样片包 → 可复现对照 → movie 对照 → 断点续跑 →
# 拒绝语义 → 排练档标注）
uv run python ops/demo_pilot.py

# 单次试水运行（预检 → 七环节 → 样片包 pilot/packages/{run_id}/）
uv run python ops/pilot.py run --form shortdrama --config configs/shortdrama.yaml \
    --topic 夜班记录 --minutes 2 --characters 林静,陈默 --constraints 单场景为主 \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id demo-run --fixed-clock
uv run python ops/pilot.py inspect --data-dir pilot --run-id demo-run --package
uv run python ops/pilot.py resume  --form shortdrama --config configs/shortdrama.yaml \
    --topic 夜班记录 --minutes 2 --characters 林静,陈默 --constraints 单场景为主 \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id demo-run

# 后端切换（A → B 是一行命令，不是改装配代码）：缺省取配置 pilot 段，此处为运行时覆盖
uv run python ops/pilot.py run ... --backend http --llm-backend http   # 缺凭证 → 装配期明确报错，零落树零扣费
uv run python ops/pilot.py precheck ... --backend http                 # precheck 不验凭证（只报配置完整性）
```

**后端选择（配置驱动）**：形态配置 `pilot` 段（`backend: simulated|http`、
`llm_backend: mock|http`、`overrides: {环节: 取值}`）决定六个后端（网关 + 分镜/视觉/声音/
剪辑/宣发适配器）的装配，唯一装配点是 `agents/pilot/backends.py`；段缺失即默认模拟
（A 路径零配置可跑）。声明 `http` 而凭证缺失 = **装配期显式失败**（指出缺哪个环境变量，
先于落树/生成，**不静默回落模拟**）；取值非法同样装配期拒绝。

**分层**（宪章原则五）：`core/orchestration/` 通用执行器（**零业务概念**：DAG/状态机/断点续跑
/账目，静态断言机检）→ `agents/pilot/` 交接纯映射 + 七环节定义 + 样片包装配 →
`configs/{movie,shortdrama}.yaml` 形态配置（权重/阈值/节奏曲线/预算/规格/外环频率）。

**样片包五件套**：`manifest.json`（**「模拟生成」标注** + 形态 + 配置指纹 + 产物清单 + 阶段状态）、
`reel.mp4`（竖屏成片）、`products.json`（各阶段产物引用与哈希）、`cost.json`
（按阶段/来源/形态汇总，与各 Agent 落盘成本**零差异**对账）、`state.json`（评分构成 + 坍缩/漂移摘要）。

### 诚实边界（「模拟生成」）

- **本特性产出为模拟生成**：模拟视频/音频生成器 + 模拟投放平台 + Mock LLM 后端，
  零外部计费、零凭证、零真实投放；样片包与清单均强制标注「模拟生成」，**不得作为对外发布素材**；
- **不使用版权素材**：夹具与全部生成内容均为合成（程序化帧/波形/伪文本）；
- **真实生成与投放（B/C 路径）未交付**：切换开关（`pilot` 段 / `--backend`）已配置化；**全部适配器族**
  （视觉/分镜/声音×3/剪辑/宣发/LLM）已落地为**协议实现**并用本地 stub 端到端验证（`uv run pytest
  tests/integration/test_http_real_stub.py -q`；`CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract`
  可让契约套件真实分支实跑），但**真实厂商对接与真实计费未跑过**（能连 stub ≠ B/C 路径已验证）——
  另有一处如实拒绝：声音响度标定基于模拟合成器口径，切 `sound: http` 时直接拒绝（零生成零扣费，
  替代做法见升级路径文档）——**装配成功 ≠ 链路可用**，切换方式与凭证清单见
  [docs/二期升级路径-真实生成与投放.md](docs/二期升级路径-真实生成与投放.md) 与
  [docs/pilot-upgrade-manifest.json](docs/pilot-upgrade-manifest.json)（字段可机检）；
- 上游不合格 → 下游**拒绝启动**（不静默降级）；环节候选全败 → 运行终止并记录全部判 0 理由。

### LLM 模型档案与角色路由（功能 016）

LLM 的**端点、凭证变量名、价目**全部写在形态配置的 `llm` 段，业务代码零厂商字面量
（静态断言 `tests/unit/test_no_vendor_literals.py` 守）：

```yaml
llm:
  profiles:
    deepseek-flash:
      base_url: https://api.deepseek.com      # 或 base_url_env: <变量名>
      api_key_env: OPENAI_API_KEY             # 凭证变量名按档案声明读取（不隐式采纳环境里的同名变量）
      legacy_env: true                        # 沿用旧变量名（报告会标注"建议改中立名"）
      prices: {prompt_per_1k: 0.0003, completion_per_1k: 0.0012}
      price_note: "峰时缓存未命中上限（保守高估）"   # 价目口径备注：随快照冻结、进账目报告
    local-qwen:
      base_url_env: LOCAL_LLM_BASE_URL
      api_key_env: LOCAL_LLM_API_KEY
      prices: {prompt_per_1k: 0.0, completion_per_1k: 0.0}
      zero_marginal: true                     # 零价目必须显式声明（自建：零边际成本仍非免费）
  roles:                                      # 固定四角色（按既有调用点盘点定稿，单层映射）
    generation: deepseek-flash                # 剧本三阶段生成
    judge: deepseek-flash                     # 四家 LLM judge 委员会
    copywriting: deepseek-flash               # 宣发文案
    dreaming_candidates: local-qwen           # 做梦层候选生成
  default_profile: deepseek-flash             # 单档案可省略（自动认定）；未映射角色回落它
```

- **换厂商 = 只改配置**：路由只在网关（`LLMGateway` 按 `role` 取档案 → 端点 + 价目），
  调用点只传 `role`；枚举外角色/缺项/多层映射**在配置解析期即报错**（不静默降级）；
- **凭证中立**（消除"环境里无关 `OPENAI_API_KEY` 被当成就绪"的假阳性）：
  `HttpBackend` 构造入参化、端点与密钥按档案声明注入（`from_profile` / `from_profiles`，
  多档案按档案 id 分派端点）；核查器 `uv run python ops/check_credentials.py` 的
  `llm_profiles` 块给出"变量名 + 档案 + 用途 + 端点（只记 host）"，未被子档案声明的环境变量
  记入 `ignored_environ`；`--probe-profiles` 才探档案端点（缺省零网络）；
- **冒烟按档案**：`uv run python ops/smoke_llm.py --profile deepseek-flash`
  （`--model` 为等价别名）；`--round` 改写 **`llm.roles`**（角色的唯一路由入口）而不再散落改模型名；
- **成本可分解**：`gateway.cost_breakdown()` → `{角色: {档案: {调用数, tokens, 金额}}}`；
  `gateway.cost_report()` 附**价目口径备注**与**"记账 ≠ 厂商账单"**声明；
- **价目为何不内置进代码**（本特性的核心纪律，防回潮）：价目一旦写死在代码里，历史节点的
  "当时价目"就不可复现——改一次价目，全部历史成本与审计复算都会漂移（宪章原则一：版本冻结）。
  因此档案与价目**只存在于配置**，并随 `config_snapshot["llm_profiles"]` 冻结进树；
  机检断言"改配置价目后历史节点成本与快照口径逐字段不变、新节点用新价目"
  （`tests/unit/test_llm_price_freeze.py`）。清单一致性由
  `tests/unit/test_upgrade_manifest_lock.py` 双向锁（配置 ⇄ `docs/pilot-upgrade-manifest.json`）。

### 真实 LLM 冒烟（DeepSeek 示例，只有 LLM 凭证时）

只拿到 LLM 凭证（OpenAI 兼容）时，可用 `ops/smoke_llm.py` 验证 LLM 腿能否跑通——
**其余适配器仍无凭证、C 路径与真实生成仍 `not_delivered`**：

```bash
# 在**你自己的 shell** 里导出（变量名须与配置档案声明一致；本仓任何文件都不含真实密钥）
export DEEPSEEK_API_KEY=...               # 你的 DeepSeek 密钥

# ① 网关级冒烟：一次调用，打印模型 / base host（脱敏）/ tokens / 按价目折算的成本 / 缓存命中
uv run python ops/smoke_llm.py --config configs/movie.yaml --profile deepseek-flash

# ② 最小规模的真实试水单轮（剧本线真实，平台适配器仍模拟）：会真的计费
uv run python ops/smoke_llm.py --config configs/shortdrama.yaml --round

# ③ 零真实调用的装配校验（同一最小档，LLM 走 mock；不需要凭证）
uv run python ops/smoke_llm.py --config configs/shortdrama.yaml --round --dry-run
```

- **退出码**：`0` 成功｜`1` 凭证缺失（并指向 `ops/check_credentials.py`）｜`2` 其它失败；
- **只切 LLM 后哪些环节变真实**：剧本线（生成 + judge）整体真实；分镜/视觉/剪辑的 judge 与
  宣发文案生成也走真实 LLM（共用网关）；**平台侧（渲染/生成/投放）仍是模拟**，账面金额零外部计费；
- **价目口径**：`screenplay.model_prices` 的 `deepseek-flash` / `deepseek-v4-pro` 按
  **峰时缓存未命中上限**折算（保守高估；真实账单因缓存命中与错峰只会更低），
  模型名/价目变动由运维按官方口径更新（详见
  [docs/二期升级路径-真实生成与投放.md](docs/二期升级路径-真实生成与投放.md) 第七节）；
- **成本量级**（估算，非保证）：最小档 `--round` 约 48 次 LLM 调用、prompt ≈ 39k tokens、
  completion 千 token 量级 → 按 `deepseek-flash` 折算 **$0.02 上下**，`deepseek-v4-pro` **$0.1 上下**。

### 短剧形态配置约束（切换/改配置前必读）

| 约束 | 原因 |
| --- | --- |
| `visual.simulated_gen.fps` 必须 = `visual.clip_spec.fps` | 模拟视频生成器编码档固定 8fps；不一致 → `rule.format_compliance` 全判 0 |
| 片段/渲染尺寸需**被 16 整除**且 9:16（现 144x256） | 否则 ffmpeg 会改尺寸（如 216→224），合规门禁逐字段对照失败 |
| 派生镜头数 ≤ **`2**(R·C)`** 且 **`2**C ≤ render.width`** | 分镜预演渲染器的索引块网格容量（功能 018 起由 `storyboard.render.index_grid` 声明，短剧现 `{rows: 1, cols: 4}` ⇒ 容量 16；缺项即拒绝启动） |
| `镜头数 × 单镜时长` 须落在 `editing.target_duration_s ± duration_tolerance_s` | EDL 时长合规门禁 |
| `promo` 单轮投放上限 = `exploration_per_round_usd × promo_pilot_ratio` ≥ 物料申请额之和 | 超限即拒投（不静默超投） |
| 转场为**出向**语义（同分区前一镜须带 dissolve） | `forbid_jump_cut_within_scene` |
| 生产档（120s 成片 / 16 镜）在机器高负载下可能撞 ffmpeg 编码抖动 | 单片段生成失败即如实 fail（不降级），恢复路径 = 断点续跑 |

## spec-kit 工作流

本仓库由 spec-kit 驱动：

- `specs/001-tree-evaluators/`：发现树与评估器框架（T001–T036 全部完成）
- `specs/002-replay-sandbox/`：回放模拟器与沙箱化策略执行（T101–T138 全部完成）
- `specs/003-promo-loop/`：宣发 Agent 全闭环（T201–T228 全部完成）
- `specs/004-visual-loop/`：视觉 Agent 闭环（T301–T332 全部完成）
- `specs/005-dreaming/`：做梦层与谱系报表（T401–T425 全部完成）
- `specs/015-pilot-shortdrama/`：短剧形态试水作品（形态配置 + 链式交接 + 可复现样片包）

## 一期里程碑全景

| 周 | 交付物 | 验收 | 状态 |
| --- | --- | --- | --- |
| 1~3 | core/tree + core/evaluators + 注册中心 | 覆盖率 ≥ 85% | ✅ 001 |
| 4~6 | core/replay + sandbox + 对抗测试套件 | 作弊全拦截；τ≥0.95 | ✅ 002 |
| 7~8 | agents/promo 全闭环（模拟投放，单轮 ≤ 预算 2%） | 首轮进化曲线，成本入账 | ✅ 003 |
| 9~10 | agents/visual 五评估器 + 闭环 | 回放打分与重算一致性验收 | ✅ 004 |
| 11~12 | dreaming 层 + 谱系报表 | 连续 5 轮 reward 曲线无塌缩 | ✅ 005 |

每个特性目录含 `spec.md`（用户故事与需求）、`plan.md` / `research.md` /
`data-model.md`（技术设计）、`contracts/`（接口契约）、`tasks.md`（任务分解）、
`quickstart.md`（端到端验证指南）。
