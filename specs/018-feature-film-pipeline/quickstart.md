# Quickstart：电影长片全链路编排（018-feature-film-pipeline）

## 验证命令（与 `.github/workflows/ci.yml` 逐字一致）

```bash
uv sync
uv run ruff check .                              # ci.yml unit job
uv run ruff format --check .                     # ci.yml unit job
uv run pytest tests/unit -k pilot                # 本特性单元面：七环节阶段表/预检清单/包证据面/排练档与场景数
uv run pytest tests/unit -k storyboard           # 位宽参数化：编码域下界/上界、逐镜往返、版本含生效位宽
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
uv run python ops/pilot.py run     --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 90 --characters 林静,陈默 --constraints "单人视角" \
    --data-dir pilot --run-id film-run                                   # 七环节串链（排练档按配置生效）
uv run python ops/pilot.py run --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 90 --characters 林静,陈默 --data-dir pilot --run-id film-fixed \
    --fixed-clock                                                        # 确定性档（可复现对照）
uv run python ops/pilot.py resume  --form movie --config configs/movie.yaml \
    --topic "夜班记录" --minutes 90 --characters 林静,陈默 --data-dir pilot --run-id film-run
uv run python ops/pilot.py inspect --data-dir pilot --run-id film-run --package   # 包内证据面（含来源标注与分量）
uv run python ops/pilot.py perf    --data-dir pilot --run-id film-run --config configs/movie.yaml \
    --clock system                                                       # 性能画像 → pilot/profiles/{run_id}.json
                                                 # 退出码：0 达标 / 1 未达标或不可评价 / 2 用法错误
uv run python ops/dev.py produce --round r1 --policy <版本>            # 017 侧仍可用（策略装载单一实现）
uv run python ops/demo_pilot.py                                          # 离线演示（零真实花费、零外部网络）
```

`--fixed-clock` 与 `perf --clock` 是**时钟口径的显式声明**：固定时钟的运行**不得**产出性能达标结论
（画像只标注"耗时为确定性常量，不构成性能证据"）；`perf` 另有退化时间戳交叉核验（C13）。
`movie`/`shortdrama` 两形态均须声明本特性新增的全部配置键（缺项即拒绝启动，不取码内默认）。

## 端到端场景（demo 流程，离线）

1. **配置完整性（七环节）**：`pilot` 段加载器（场景数/排练档/性能阈值）+ 七环节权重 + `dev` 段 +
   `storyboard.render.index_bits` 下界校验（`2**bits >= 该形态镜头数`，movie 60 镜 ⇒ ≥ 6）——
   逐项删键即拒绝启动（零成本零落树）
2. **七环节串链（排练档）**：`dev → script → storyboard → visual → sound → editing → promo`
   按 DAG 依次执行，链路拓扑与交接契约**一行不动**，只有体量按配置声明的排练档缩档；
   跑完产出样片包五件套 + 过 `verify_package`；把 `dev` 从阶段表移除（模拟漏同步）即红
3. **`dev → script` 字段级交接**：标记恰好一条 ⇒ 交出剧本输入视图，四键（`topic` /
   `target_duration_min` / `constraints` / `characters`）逐个标类（交接 / 运行级 / 派生）、可追溯；
   注入悬空标记 / 多条标记 / 要点为空 ⇒ **下游拒绝启动**并点名（不静默取第一条、不伪装成"选题为空"）
4. **包内两处新增（015 遗留缺口）**：① 逐环节**评估分量**（`evaluator_id@version` 与分量值，
   取自树节点 `eval_breakdown`，缺环节即拒绝装配）；② 逐环节**真实/模拟标注** + `channels` 汇总 +
   `work_kind`（本次为排练）——全模拟链路下七项全 `simulated`
5. **可复现与画像**：同输入同配置、独立工件根两次（一次固定时钟、一次系统时钟）⇒ 新增字段子集逐字节
   一致、五件套仍逐字节一致；性能画像固定在报告侧（墙钟只在 `pilot/profiles/`），固定时钟 ⇒ 不给达标
   结论，阈值未标定 ⇒ 只标"未标定"、不发明数字
6. **拒绝语义**：缺 `pilot.scene_count` → 启动拒绝（不取码内默认 4）；缺 `budget.tiers.dev` → 预检拒绝；
   任一环节失败 → 整轮失败、其后 `skipped`、**不装配样片包**
7. **movie 对照（零代码切换）**：同一套七环节链代码换一份形态配置跑通——60 镜在加宽后的位宽内
   100% 可编码（旧 4 位上限 16 的时代结束），旧位宽下已落盘工件与节点**逐字节不变**

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

## 里程碑验收（立项书周 8~11 / SC-001，`docs/三期立项书.md:52`/`:163`）

七环节全链路跑通并产出**可复现样片包**（五件套齐备 + 校验通过 + 同输入重跑逐字节一致率 100%）
+ **全链路成本账目**（七环节 100% 入账含 `FAILED`、三方对账零差异、`cost.json` 覆盖七环节）
+ **各环评估分量**逐环入包且无空项 + **长片体量的性能与预算门禁达标**按声明档位与阈值机检判定
（未标定项如实标注，不发明数字）。长片周期风险（`docs/三期立项书.md:222`）由排练档缓解。

## 验收映射

| 契约 | 验证命令 | 成功标准 |
| --- | --- | --- |
| C1~C4 链首插入 + 清单同步不变量 + `dev` 入口/产出 + 档位与预检 + kind 登记 | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`；demo 步①②⑥ | SC-002/006/007 |
| C5~C7 `dev → script` 字段级交接（读取集合计声明 + 丢弃/派生 + 两侧同步） | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`（含悬空/越界/多条注入用例）；demo 步③ | SC-003/011 |
| C8~C10 位宽配置键与上下界 + 原则一版本规则 + 排练档与场景数 | `pytest tests/unit -k storyboard`（编码域/往返/版本含位宽）+ `pytest tests/contract -k pilot`；demo 步①⑦ | SC-010/011 |
| C11~C13 评估分量入包 + 分层标注 + 性能/预算证据面与可复现 | `pytest tests/unit -k pilot` + `pytest tests/contract -k pilot`；`ops/pilot.py perf`；demo 步④⑤⑦ | SC-001/004/005/007/008 |
| SC-001 里程碑（四条关键验收） | demo 步②③④⑤（离线机检）；真实运行属运营动作 | SC-001 |
| SC-008 可复现一致率 100% | `pytest tests/contract -k pilot`（跨时钟新增字段子集 + 五件套逐字节） | SC-008 |
| SC-009 覆盖率 ≥85%（含 web）+ 对抗/无偏性不放松 + 零形态分支静态断言 | `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`；`uv run pytest tests/adversarial -m adversarial`；`uv run pytest tests/unbiasedness -m unbiasedness` | SC-009 |
| SC-006 超预算调用恒 0 + `dev` 档缺声明拒绝率 100% | `pytest tests/contract -k pilot` + `-k billing`（019 口径不放宽） | SC-006 |
| SC-004 分层标注可机读率 100% / 模拟被标为真实恒 0 | `pytest tests/unit -k pilot`（包内标注与 `channels` 断言）；demo 步④ | SC-004 |

## 验证记录（待实跑回填；字段标签镜像 `specs/017-dev-agent-degraded/quickstart.md`）

- `uv sync`：**待回填**
- `uv run ruff check .` + `uv run ruff format --check .`：**待回填**
- `uv run pytest tests/unit -k pilot` / `-k storyboard` / `-k dev`：**待回填**
- `uv run pytest tests/contract -k pilot` / 全量 `tests/contract` / `CINEFLOW_CONTRACT_STUB=1`：**待回填**
- `uv run pytest tests/integration/test_http_real_stub.py -q`：**待回填**
- `docker compose -f ops/dev.compose.yml up -d --wait postgres minio` + `uv run alembic -c ops/alembic.ini upgrade head`：**待回填**（本特性无新迁移；`dev_jobs` 迁移 0010 为 017 既有）
- `uv run pytest tests/integration -m integration`（真实 PG + MinIO）：**待回填**
- `uv run pytest tests/unbiasedness -m unbiasedness` / `tests/adversarial -m adversarial`：**待回填**（门禁不放松）
- 全量 `uv run pytest tests/unit --cov=… --cov-fail-under=85`：**待回填**（覆盖率数字待测）
- `uv run python ops/pilot.py run …`（七环节串链）/ `resume` / `inspect --package`：**待回填**（运行记录七环节状态、包内字段）
- `uv run python ops/pilot.py perf --clock system`：**待回填**（画像 verdict 与"未标定"标注）
- `uv run python ops/demo_pilot.py`：**待回填**（七步全 ok、退出码 0、用时）
- 清单同步复核（C1 的七处声明点 + 三处一致性断言）：**待回填**（逐处实值）
- 位宽与版本复核（C8/C9）：**待回填**（movie 60 镜编码 100%、两种位宽 ⇒ 两个版本号、旧节点逐字节不变）
- 交接复核（C5~C7）：**待回填**（读取集 4 键逐键标类、悬空标记拒绝启动率 100%）
- 诚实边界复核（C12/FR-011）：**待回填**（全模拟下七环节全 `simulated`、包内无"真实渠道全链路"表述）

### 环境复核（待回填）

- 本机口径与 `ci.yml` 逐字一致的实跑结果：**待回填**
- 真实生成/投放凭证与平台名（运营侧）、排练档数字（运营侧）、"真实渠道全链路已跑通"结论：
  **均待运营给定**，本特性只交付机制与离线复现。
