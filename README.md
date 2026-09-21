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
采样/提示词/锚点哈希）；合规 0 分短路不跑 judge（省 LLM 成本）；judge 调用
全经网关计费；一致性验收为发布阻塞（一致率 100% + τ 分档门禁）。

真实生成平台接入是凭证配置的运维动作：`VISUAL_GEN_BASE_URL` /
`VISUAL_GEN_API_KEY`（缺凭证不假装生成，原则六）。

## 声音闭环（功能 006）

```bash
# 单元测试（执行器/TimingSheet/音频合成/配置/四评估器/合成/回放/做梦接入）
uv run pytest tests/unit -k sound

# 生成适配器契约套件（三类型 × 双实现同构；真实骨架无凭证跳过）
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

# 渲染适配器契约套件（双实现同构；真实骨架无凭证跳过）
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
EDL 摘要成对比较，版本号 = 提示词+锚点集+摘要函数三段哈希；gate 短路不跑
judge，quantize 6 位定点归一）；渲染编码固定单线程确定性档（同 EDL 逐字节
复现，004 x264 flake 根因同源消除并已回移 004）；EDL 非法执行前拒绝
（0 渲染 0 成本）；无偏性 τ≥0.95 为发布阻塞（实测 τ=1.0）；做梦层
agent_id 泛化零改动接入（champion 策略 `policies/history/editing/` +
meta.json 谱系）。

真实渲染服务接入是凭证配置的运维动作：`EDIT_RENDER_BASE_URL` /
`EDIT_RENDER_API_KEY` 环境变量（缺凭证不假装渲染，原则六）。

## 分镜闭环（功能 008）

```bash
# 单元测试（剧本/ShotList 三层校验/分镜卡渲染/配置/摘要/五评估器/合成/执行器/回放/schema 快照）
uv run pytest tests/unit -k "storyboard or shotlist"

# 预演渲染适配器契约套件（双实现同构；真实骨架无凭证跳过）
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
ShotList 摘要成对比较，版本号 = 提示词+锚点集+摘要函数三段哈希；gate 短路不跑
judge，quantize 6 位定点归一）；ShotList 三层执行前校验（引用行存在/场景承接含关键
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
- **升级判据持续积累**：`calibration/upgrade-events/{周期}.json` 不可变快照（阈值快照 +
  judge 信度/漂移/门禁违规分布/人评锚点数 + **系统结论 meets|below** + 人推翻留痕，
  推翻不改写系统结论字段）；**升级为自动进化不在本特性范围**，须另立决议并修订宪章。

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

## spec-kit 工作流

本仓库由 spec-kit 驱动：

- `specs/001-tree-evaluators/`：发现树与评估器框架（T001–T036 全部完成）
- `specs/002-replay-sandbox/`：回放模拟器与沙箱化策略执行（T101–T138 全部完成）
- `specs/003-promo-loop/`：宣发 Agent 全闭环（T201–T228 全部完成）
- `specs/004-visual-loop/`：视觉 Agent 闭环（T301–T332 全部完成）
- `specs/005-dreaming/`：做梦层与谱系报表（T401–T425 全部完成）

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
