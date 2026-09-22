# Quickstart：短剧形态试水作品（015-pilot-shortdrama）

## 验证命令

```bash
uv sync

# 通用执行器 + 交接 + 试水（单元）
uv run pytest tests/unit -k "orchestration or pilot or handoff"

# 契约 C1~C13（通用执行器 / 四段交接 / 试水运行与样片包 / 宪章级机检）
uv run pytest tests/contract -k pilot

# 形态配置完整性（全部加载器缺项即红）
uv run pytest tests/unit -k "config_integrity"

# 形态零代码切换（权重/阈值/曲线/预算/规格差异可归因 + core/agents 静态扫描零分支）
uv run pytest tests/unit -k "form_switch"

# 端到端六步演示（配置完整性 → 短剧运行出样片包 → 可复现对照 → movie 对照 → 断点续跑 → 拒绝语义）
uv run python ops/demo_pilot.py

# CLI：预检（零成本零落树）/ 运行 / 查看 / 续跑
uv run python ops/pilot.py precheck --form shortdrama --config configs/shortdrama.yaml \
    --topic 夜班记录 --minutes 2 --characters 林静,陈默 --data-dir /tmp/pilot-demo
uv run python ops/pilot.py run --form shortdrama --config configs/shortdrama.yaml \
    --topic 夜班记录 --minutes 2 --characters 林静,陈默 --data-dir /tmp/pilot-demo \
    --run-id demo-run --fixed-clock
uv run python ops/pilot.py inspect --data-dir /tmp/pilot-demo --run-id demo-run --package
uv run python ops/pilot.py resume --form shortdrama --config configs/shortdrama.yaml \
    --topic 夜班记录 --minutes 2 --characters 林静,陈默 --data-dir /tmp/pilot-demo --run-id demo-run

# 覆盖率门禁（与 ci.yml 逐字一致：core + agents + dreaming + web ≥ 85%）
uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web \
    --cov-report=term-missing --cov-fail-under=85

# 本地验证纪律（与 ci.yml 逐字一致）
uv run ruff check .
uv run ruff format --check .
```

> 提示：`ops/pilot.py run` 直接跑 `configs/shortdrama.yaml`（生产档：120s 成片 / 16 镜）
> 在本机高负载下可能撞 ffmpeg 单片段编码抖动而走到阶段失败（如实 fail，不降级）；
> 演示与 CI 预算内的对照请用 `ops/demo_pilot.py`（内部对两套配置做**等值派生**：成片 30s / 剧本 2 页）。

## 端到端场景（demo 流程）

1. **配置完整性**：shortdrama.yaml 过全部加载器（缺项即红）
2. **一次试水运行**：六阶段按 DAG 执行 → 样片包（成片 + 产物引用 + 清单 + 账目 + 快照）
3. **可复现**：同输入同配置两次运行逐字节一致
4. **形态切换零代码**：movie 与 shortdrama 同链各跑一轮，差异全部可归因配置；静态扫描零分支
5. **断点续跑**：中途失败 → 修复续跑（已完成阶段不重跑）；输入指纹变更 → 拒绝续跑
6. **拒绝语义**：上游不合格 → 下游不启动；环节候选全判 0 → 运行终止并记录全部理由

## 验证记录（2026-09-22 实测，命令与 ci.yml 逐字一致）

| 命令 | 实测结果 |
| --- | --- |
| `uv run pytest tests/unit -k "orchestration or pilot or handoff"` | **140 passed** |
| `uv run pytest tests/contract -k pilot` | **25 passed**（C1~C13 全量：orchestration 4 + handoffs 13 + pilot-run 4 + 宪章级 3 + 字段声明 1） |
| `uv run pytest tests/unit -k "config_integrity"` | **51 passed**（含 17 组"缺项即红"反向机检） |
| `uv run pytest tests/unit -k "form_switch"` | **15 passed**（权重/阈值/曲线/外环/预算/竖屏/时长逐项归因 + core/agents 零形态分支） |
| `uv run python ops/demo_pilot.py` | **退出码 0**，六步全 ok，耗时 **35.17s**；连跑两次输出逐字节一致（除临时路径） |
| `uv run python ops/pilot.py precheck/run/inspect/resume` | 预检 exit 0（18 个加载器）；run exit 0（六阶段 done，总账 $3.97）；inspect exit 0（五件套齐备 + 「模拟生成」标注）；resume exit 0（幂等，attempts 全 1） |
| 覆盖率门禁（`--cov=core --cov=agents --cov=dreaming --cov=web --cov-fail-under=85`） | **TOTAL 92.82% ≥ 85%**（13404 语句 / 962 未覆盖；门禁通过） |
| 全量 `uv run pytest tests/unit` | **2786 passed / 1 failed**——唯一失败为既有视觉用例 `test_visual_consistency.py::Test全一致放行::test_重算逐字节一致_pass`（imageio-ffmpeg `FrameDecodeError`/`Could not load meta information`），**单独复跑通过**（见"实测发现"） |
| `uv run ruff check .` + `uv run ruff format --check .` | **双绿 454 文件** |

### 里程碑验收（立项书周 11~12 / SC-001）

自包含样片包产出 ✅ + 逐字节可复现 ✅ + 四段交接双向断言通过 ✅ + 成本对账零差异 ✅ +
形态切换零代码（静态扫描零分支）✅；覆盖率 **92.82% ≥ 85%** 不降 ✅。

> 样片为**模拟生成**（全模拟链路）：可用于技术验证与评审，不得对外作为真实作品发布。

### 实测发现（如实记录）

1. **ffmpeg 编码抖动（环境级，非本特性语义问题）**：本机高负载（并行跑多个 pytest 或
   16 连片段编码）时偶发 `imageio-ffmpeg` 报 `OSError: Could not load meta information`
   / `FrameDecodeError`。表现为：既有 `test_visual_consistency` 的某条用例失败，
   或试水运行的视觉阶段**如实** failed（单片段编码失败 → 阶段失败，不静默降级）。
   **处置**：不为让测试变绿而放宽语义；demo 与夹具用"试水档等值派生"（成片 30s / 4 镜）
   压低编码次数；失败后的恢复路径 = 断点续跑（`ops/pilot.py resume`）。隔离复跑均通过。
2. **promo 单轮投放上限口径**：上限 = `promo.exploration_per_round_usd × promo_promo_pilot_ratio`
   （短剧 120 × 0.02 = $2.4）；物料申请额之和须 ≤ 上限，否则拒投（不静默超投）。
   短剧配置由 60 上调到 120 才装得下 3 件物料（仍远低于电影 500）。
3. **宣发两段式落树**：投递成功节点待指标回流才冻结（`agents/promo/ingest.py`，CLI 薄封装
   在 `ops/ingest_metrics.py`）；编排层复用该既有入口，不另造回流逻辑。
4. **续跑校验顺序**：续跑**先**校验指纹/阶段集合，**再**对已完成记录走幂等返回——
   否则"输入已变但原运行 DONE"会被误当幂等成功（已在 015 修复并加回归断言）。
5. **配置硬约束**（改配置前必读，已写入 README 与 `configs/shortdrama.yaml` 注释）：
   模拟生成器编码档固定 8fps（`clip_spec.fps` 必须一致）；尺寸需被 16 整除且 9:16；
   分镜渲染器镜头索引上限 16；镜头数 × 单镜时长须落在 EDL 时长容差内；
   转场为出向语义（同分区前一镜须带 dissolve）。
