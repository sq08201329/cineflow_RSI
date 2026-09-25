# Quickstart：电影长片全链路编排（018-feature-film-pipeline）

## 验证命令（门禁套件与 `.github/workflows/ci.yml` 逐字一致；另有本特性的单元/契约子集与 CLI 实跑）

```bash
uv sync
uv run ruff check .                              # ci.yml unit job
uv run ruff format --check .                     # ci.yml unit job
uv run pytest tests/unit -k pilot                # 本特性单元面：七环节阶段表/预检清单/包证据面/排练档与场景数
uv run pytest tests/unit -k storyboard           # 索引块网格容量：容量下界/量子上界、逐镜往返、版本含生效网格参数
uv run pytest tests/unit -k dev                  # 017 回归面不降（链首插入不得破坏既有轮次语义）
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/contract -k pilot            # 契约面：C1~C13
uv run pytest tests/contract                     # ci.yml contract job（含段差异集与零形态分支全量扫描）
CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q   # ci.yml：真实分支对着本地 stub 实跑
uv run pytest tests/integration/test_http_real_stub.py -q  # ci.yml：真实适配器 stub 集成（本地 loopback）
uv run pytest tests/unbiasedness -m unbiasedness           # ci.yml：无偏性门禁不放松（SC-009）
uv run pytest tests/adversarial -m adversarial             # ci.yml：对抗门禁不放松（SC-009）
docker compose -f ops/dev.compose.yml up -d --wait postgres minio           # 仅 tests/integration 全量需要
uv run alembic -c ops/alembic.ini upgrade head                             # ci.yml integration job（环境注入 DSN）
uv run pytest tests/integration -m integration                             # ci.yml integration job
# 七环节串链（排练档按配置生效；`--minutes` 取生效档值 = 0.5 分钟 = 30 秒）
uv run python ops/pilot.py run     --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id film-run

# 确定性档（固定时钟；可复现对照）
uv run python ops/pilot.py run     --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id film-fixed \
    --fixed-clock

# 断点续跑（指纹一致才继续）与启动前预检（零成本零落树）——两者同取运行级输入映射
uv run python ops/pilot.py resume  --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot --run-id film-run
uv run python ops/pilot.py precheck --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 0.5 --characters 林静,陈默 --constraints "单人视角" \
    --genre-bounds 悬疑,夜戏 --audience 都市女性 --data-dir pilot

# 包内证据面（逐环节来源标注 + 评估分量）与性能画像（→ pilot/profiles/{run_id}.json）
# `perf` 退出码：0 达标 / 1 未达标或不可评价 / 2 用法错误
uv run python ops/pilot.py inspect --data-dir pilot --run-id film-run --package
uv run python ops/pilot.py perf    --form movie --config configs/movie.yaml \
    --data-dir pilot --run-id film-run --clock system

uv run python ops/dev.py produce --round r1 --policy <版本>            # 017 侧仍可用（策略装载单一实现）
uv run python ops/demo_pilot.py                                          # 离线演示（零真实花费、零外部网络）
```

`--minutes` 取**生效档值**（浮点分钟）：它必须等于**生效**成片时长 ÷ 60（排练档下 = 排练档
`target_duration_s` ÷ 60，示例 `0.5`；形态原值档下 movie 为 `90`）——与预检的"两处时长一致"
不变量（`editing.target_duration_s == screenplay.target_duration_min × 60`，容差 `1e-6`）**同批定稿**，
不一致即拒绝启动并点名两处实测值。上例中的 `0.5` 为**排练档声明的演示值**（数字属运营侧输入，以配置为准）。

`--genre-bounds` / `--audience` 是链首立项环节（`dev`）的运行级输入显式映射，缺项即**预检拒绝**
（`run` / `resume` / `precheck` 同口径）。`perf` 的 `--form` 为必填（同 `run`），`--clock` **必填、无默认**。

`--fixed-clock` 与 `perf --clock` 是**时钟口径的显式声明**：固定时钟的运行**不得**产出性能达标结论
（画像只标注"耗时为确定性常量，不构成性能证据"）；`perf` 另有退化时间戳交叉核验（C13）。
`movie`/`shortdrama` 两形态均须声明本特性新增的全部配置键（缺项即拒绝启动，不取码内默认）。

> **契约编号口径（F-06）**：本文件引用的 **C1~C13 是 018 自己的契约编号**
> （`specs/018-feature-film-pipeline/contracts/`），与 `tests/contract/test_pilot_contracts.py` 文件内部
> 的 015 编号（该文件内亦称 C13）**同名不同物**。

## 端到端场景（demo 流程，离线）

1. **配置完整性（七环节）**：`pilot` 段加载器（场景数/每场景行数/排练档/性能阈值）+ 七环节权重 + `dev` 段 +
   `storyboard.render.index_grid` 容量校验（`2**(R·C) >= 该形态派生镜头数` 且 `2**C <= render.width`；
   movie 原值 2700 镜 ⇒ 容量 ≥ 12 位、如 `rows: 2, cols: 8`）——
   逐项删键即拒绝启动（零成本零落树）
2. **七环节串链（排练档）**：`dev → script → storyboard → visual → sound → editing → promo`
   按 DAG 依次执行，链路拓扑与交接契约**一行不动**，只有体量按配置声明的排练档缩档；
   跑完产出样片包五件套 + 过 `verify_package`；把 `dev` 从阶段表移除（模拟漏同步）即红
3. **`dev → script` 字段级交接**：标记恰好一条 ⇒ 交出剧本输入视图，四键（`topic` /
   `target_duration_min` / `constraints` / `characters`）逐个标类（**承接含改名** / 运行级 / 派生）、可追溯；
   `dropped` 是与 `reads` 并列的独立集、取数依据（`entries`/`production_marks`）单列；
   注入悬空标记 / 多条标记 / 要点为空 ⇒ **下游拒绝启动**并点名（不静默取第一条、不伪装成"选题为空"）
4. **包内两处新增（015 遗留缺口）**：① 逐环节**评估分量**（`evaluator_id@version` 与分量值，
   取自树节点 `eval_breakdown`，缺环节即拒绝装配）；② 逐环节**真实/模拟标注** + `channels` 汇总 +
   `work_kind`（本次为排练）——全模拟链路下七项全 `simulated`
5. **可复现与画像**：同输入同配置、独立工件根两次（一次固定时钟、一次系统时钟）⇒ 新增字段子集逐字节
   一致、五件套仍逐字节一致；性能画像固定在报告侧（墙钟只在 `pilot/profiles/`），固定时钟 ⇒ 不给达标
   结论，阈值未标定 ⇒ 只标"未标定"、不发明数字
6. **拒绝语义**：缺 `pilot.scene_count` → 启动拒绝（不取码内默认 4）；缺 `budget.tiers.dev` → 预检拒绝；
   两处时长不一致（如 `target_duration_min: 90` 配 `target_duration_s: 120`）→ 拒绝启动并点名实测值；
   任一环节失败 → 整轮失败、其后 `skipped`、**不装配样片包**
7. **movie 对照（零代码切换）**：同一套七环节链代码换一份形态配置跑通——2700 镜在网格容量内
   100% 可编码（旧"帧顶 2 行 × 4 列 = 16"的时代结束；要更少镜头就改 `clip_spec.duration_seconds`，
   **不是**放宽容量校验），旧网格下已落盘工件与节点**逐字节不变**

## 诚实边界（本特性最核心的工程对象，不得谎报）

- **"真实渠道" = LLM 腿真实 + 平台侧按配置（默认模拟）**：今天真实可用的只有 LLM 腿（019 已交付
  前置预算门禁、账单对账与运行记录）。平台五环节（分镜/视觉/声音/剪辑/宣发）的真实适配器**代码在位
  但未交付使用**——`docs/pilot-upgrade-manifest.json` 的 B 路径（视频/音频生成，14 个凭证环境变量）
  与 C 路径（真实投放，2 个）状态均为 `not_delivered`。
- **禁止产出"真实渠道全链路已跑通"这类结论**：只有七环节 `source` 全为 `real` 时才可产出该表述；
  全模拟（默认）与部分真实（如 `llm: http` 而平台模拟）都必须如实分层标注，且保留"模拟生成"标注
  与预检报告的 `credentials_checked: false`（不假装验过凭证）。模拟被标为真实的次数恒须为 0。
- **真实生成的账号、凭证、平台名与预算档属运营侧前置输入**：本特性不发明凭证名、平台名与额度数字；
  未具备期间不重写适配器协议、不新开凭证面。
- **排练档的数字属运营侧输入**：表达机制已裁决（配置声明 + `work_kind` + 未标定如实登记），
  **待给定的是数字**；未给定期间按"未标定"登记、不发明数字，也不把排练产物标为真实作品。
- **性能画像的边界**：墙钟耗时只进报告侧、不进逐字节比对的五件套；固定时钟运行不构成性能证据；
  「专用基准环境下的绝对标定」属三期 WS3 第 3 项，**不在本特性**。
- **单次运行无法自证用过系统时钟**：时钟口径由入口显式声明 + 退化时间戳交叉核验两层承担；
  本特性不改 `RunRecord` 字段（015 冻结工件），残余风险如实登记为边界。
- **测试与演示全部走模拟后端 + 夹具**，零真实花费、零外部网络；真实运行属运营动作。

## 里程碑验收（立项书周 8~11 / SC-001，`docs/三期立项书.md:52`/`:211`）

七环节全链路跑通并产出**可复现样片包**（五件套齐备 + 校验通过 + 同输入重跑逐字节一致率 100%）
+ **全链路成本账目**（七环节 100% 入账含 `FAILED`、三方对账零差异、`cost.json` 覆盖七环节）
+ **各环评估分量**逐环入包且无空项 + **长片体量的性能与预算门禁达标**按声明档位与阈值机检判定
（未标定项如实标注，不发明数字）。长片周期风险（`docs/三期立项书.md:270`）由排练档缓解。

## 验收映射

| 契约 | 验证命令 | 成功标准 |
| --- | --- | --- |
| C1~C4 链首插入 + 清单同步不变量 + `dev` 入口/产出 + 档位与预检 + kind 登记 | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`；demo 步①②⑥ | SC-002/006/007 |
| C5~C7 `dev → script` 字段级交接（`reads` 三类合计声明 + `dropped` 独立集 + 取数依据 + 两侧同步） | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`（含悬空/越界/多条注入用例）；demo 步②（链内交接生效）与步⑥（拒绝语义） | SC-003/011 |
| C8~C10 索引块网格容量与上下界 + 原则一版本规则 + 排练档/场景数/派生镜头数持有者 | `pytest tests/unit -k storyboard`（容量/往返/版本含网格取值）+ `pytest tests/contract -k pilot`；demo 步①⑦ | SC-010/011/012 |
| C11~C13 评估分量入包 + 分层标注 + 性能/预算证据面与可复现 | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`；`ops/pilot.py perf`；demo 步②③⑦ | SC-001/004/005/007/008 |
| SC-001 里程碑（四条关键验收） | demo 步②③⑦（离线机检）+ `ops/pilot.py perf`；真实运行属运营动作 | SC-001 |
| SC-008 可复现一致率 100% | `pytest tests/contract -k pilot`（跨时钟新增字段子集 + 五件套逐字节） | SC-008 |
| SC-009 覆盖率 ≥85%（含 web）+ 对抗/无偏性不放松 + 零形态分支静态断言 | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`；`uv run pytest tests/adversarial -m adversarial`；`uv run pytest tests/unbiasedness -m unbiasedness` | SC-009 |
| SC-006 超预算调用恒 0 + `dev` 档缺声明拒绝率 100% | `pytest tests/contract -k pilot` + `-k billing`（019 口径不放宽） | SC-006 |
| SC-004 分层标注可机读率 100% / 模拟被标为真实恒 0 | `pytest tests/unit -k pilot`（包内标注与 `channels` 断言）；demo 步②（清单标注 + 装配期校验） | SC-004 |

## 验证记录（2026-09-24/25 实跑回填；字段标签镜像 `specs/017-dev-agent-degraded/quickstart.md`）

- `uv sync`：**Resolved 35 packages in 3ms / Checked 30 packages in 1ms** ✓（依赖与 `uv.lock` 一致，无变更）
- `uv run ruff check .` + `uv run ruff format --check .`：**双绿** ✓（`All checks passed!`；`564 files already formatted`）
- `uv run pytest tests/unit -k pilot` / `-k storyboard` / `-k dev`：**未单独复跑**——三条子集命令的面**已被
  全量 unit 套件覆盖**（见下 3821 过、覆盖率 92.58%，含 `tests/unit/test_pilot_*.py` 与 017 回归面）；
  按"由全量套件覆盖，未单独复跑"登记，不另立数字
- `uv run pytest tests/contract -k pilot` / 全量 `tests/contract` / `CINEFLOW_CONTRACT_STUB=1`：全量
  **408 过 + 56 skip** ✓（3 分 08 秒；skip = 真实实现无凭证按用例跳过，CI 口径同）；stub 口径 **464 过** ✓
  （3 分 25 秒）；`-k pilot` **未单独复跑**——其面（C1~C13 + 篡改面）由全量运行内的
  `tests/contract/test_pilot_film_contracts.py`（23 例）覆盖
- `uv run pytest tests/integration/test_http_real_stub.py -q`：**未单独复跑**——该文件的 54 项**在集成全量
  运行内执行**（见下 `96 过 / 54 deselected`）
- `docker compose -f ops/dev.compose.yml up -d --wait postgres minio` + `uv run alembic -c ops/alembic.ini upgrade head`：
  postgres / minio **均 healthy** ✓、`minio-init` 建桶成功；`upgrade head` 真实执行至 **0010_dev_jobs** ✓
  （本特性**无新迁移**；`dev_jobs` 迁移 0010 为 017 既有）
- `uv run pytest tests/integration -m integration`（真实 PG + MinIO）：**96 过 + 54 deselected** ✓
  （17 分 44 秒）。**如实登记一次中断**：首次门禁运行时 compose 栈已停（`compose ps` 为空），该次集成
  **静默退化为 `24 过 / 72 skip`**；重起栈并重跑后为 **96 过**，故以 96 为准，并把"栈掉线会静默退化为
  跳过"记进环境复核
- `uv run pytest tests/unbiasedness -m unbiasedness` / `tests/adversarial -m adversarial`：**40 过** ✓（15 秒）/
  **6 过** ✓（27 秒；本机 Docker 加固容器后端，CI 权威档为 gVisor）；门禁不放松
- 全量 `uv run pytest tests/unit --cov=… --cov-fail-under=85`：**3821 过 0 失败，覆盖率 92.58% ≥ 85%** ✓
  （29 分 59 秒；TOTAL 18012 语句 / 1337 未覆盖）
- `uv run python ops/pilot.py run …`（七环节串链，`--minutes` 取**生效档值**、浮点分钟，含
  `--genre-bounds` / `--audience`）/ `resume` / `precheck` / `inspect --package`：实测口径为
  `--form shortdrama --config configs/shortdrama.yaml --data-dir … --topic 测试 --minutes 2.0
  --characters 林一 --genre-bounds 悬疑 --audience 都市女性`（**`precheck` 退出码 0** ✓；`loaders`
  实测 **22 项**；`pilot_volume` 显示 `work_kind=rehearsal`、`source=declared_scale`、
  `rehearsal_status=declared`，生效体量 `{scene_count: 4, lines_per_scene: 12,
  script_target_minutes: 2.0, target_duration_s: 120.0, clip_duration_seconds: 7.5}`；
  **`run --run-id r18` 退出码 0、状态 `done`** ✓，运行记录阶段序列 =
  `['dev','script','storyboard','visual','sound','editing','promo']`；**`inspect --run-id r18 --package`
  退出码 0** ✓，七环节齐备且含 `files` / `form` / `input_fingerprint` / `config_fingerprint` /
  `failure_*`）；`resume` **未单独复跑**——断点续跑幂等面由 `ops/demo_pilot.py` 步⑤ 与全量套件覆盖
- `uv run python ops/pilot.py perf --form … --clock system`：**退出码 1、按设计** ✓（画像 verdict 与体量指标
  实测为 `performance.status=unstandardized` ⇒ 判定 `not_evaluable`，"未标定：只出台账与体量，不产出
  达标结论"，并输出体量明细 `shot_count=16` / `page_count=1.066667` / `reel_duration_s=115.2` /
  `target_duration_s=120.0`）；同一 `run_id` 换 `--clock fixed` ⇒ **退出码 1 `rejected`** ✓，理由
  `性能画像已存在且时钟口径不同（'system' != 'fixed'）：报告侧 append-only`——**不是失败**，而是
  "时钟口径钉死 + 报告侧不可改写"的按设计拒绝（退出码语义仍为 0 达标 / 1 未达标或不可评价 / 2 用法错误）
- `uv run python ops/demo_pilot.py`：**退出码 0，七步全 ok** ✓（用时 76.71 s）——步序 `1_配置完整性` /
  `2_七环节串链出包` / `3_可复现对照` / `4_movie对照零代码切换` / `5_断点续跑幂等` / `6_拒绝语义` /
  `7_排练档标注`，逐项 `ok=true`（步② 七环节出包、步③ 逐字节一致、步⑥ 拒绝语义、步⑦ 排练档标注均在其中）
- 清单同步复核（C1：七处集中声明点 + 三处一致性断言；含五处既有登记点的逐处实值——加载器元组 /
  `CONFIG_CLASSES` 与 `REQUIRED_PATHS` / 形态差异键集两处 / `_MINIMAL_MOVIE_CONFIG` 与书面备忘）：**实测全绿** ✓
  （七处集中声明点为 `PILOT_STAGE_IDS` / `build_stage_specs` / `AgentConfigs` / `build_runtime` /
  `pilot.py` 四处字面量清单 / `config_completeness` / `package.py` product kind；三处一致性断言与五处既有
  登记点（实测口径名：差异集 / `CONFIG_CLASSES`+`REQUIRED_PATHS` / `test_pilot_contracts` 段差异集 /
  `config_completeness` / `_MINIMAL_MOVIE_CONFIG` 判定）逐处全绿；其中 `loaders` 实测 **22 项**，
  验证了"计数随实现派生"的口径）
- 索引网格与版本复核（C8/C9）：**实测全绿** ✓（movie 原值 2700 镜逐序号编码 100%、shortdrama 16 镜；
  容量下界与量子上下界均按**实测数字**拒绝越界——如 `2**C > width` 用例拒绝启动；改网格参数 ⇒ 对齐代理
  版本号随之变化（两种网格取值 ⇒ 两个版本号，断言版本 ≠ 冻结字面量）、历史节点与工件**逐字节不变**
  （无迁移/无改写路径））
- 时长口径复核（C10/SC-012①）：**实测全绿** ✓（`target_duration_min × 60 == target_duration_s` 成立，
  movie `5400` s 系本特性修 bug；注入不一致配置 ⇒ **拒绝启动**且错误信息含**两处实测值**，由测试断言）
- 交接复核（C5~C7）：**实测全绿** ✓——由 `tests/unit/test_pilot_dev_script_handoff.py`（29 例）覆盖
  （`reads` 4 键逐键标类、`renames` 覆盖承接类键、`dropped` 与 `reads` 互斥、取数依据登记完备、
  悬空标记拒绝启动率 100%）；**未单独复跑** `-k pilot` 子集，结论即上列全量 unit 数字
- 诚实边界复核（C12/FR-011）：**实测全绿** ✓——由 `tests/contract/test_pilot_film_contracts.py`（23 例，
  聚合 C1~C13 + 篡改面）覆盖（含全模拟下七环节全 `simulated`、包内无"真实渠道全链路"表述这一类断言）；
  **未单独复跑**，结论即上列全量 contract 数字

### 环境复核（2026-09-24/25）

- 本机口径与 `ci.yml` **逐字实跑** ✓：本次门禁按 `ci.yml` 逐条执行；**与 CI 的唯一差异**是对抗套件本机走
  Docker 加固容器后端（CI 为 gVisor/runsc），断言面不受影响。另登记一坑：**compose 栈掉线会让集成静默
  退化为跳过**（本次亲身遇到：栈停后同一命令退化为 `24 过 / 72 skip`，重起栈重跑后为 `96 过`）——
  故集成结论须同时看 `compose ps` 与 passed 数
- 真实生成/投放凭证与平台名（运营侧）、排练档数字（运营侧）、"真实渠道全链路已跑通"结论：
  **均待运营给定**，本特性只交付机制与离线复现。
