# AGENTS.md — CineFlow 工程约定

本仓对**所有在此工作的 Agent** 的约定。优先级：用户指令 > 本文件 > 技能模板。
本文件是**唯一权威**：不要在别处再抄一份同类约定。

## 0. 先读什么

- 项目定位与最新交付状态：`README.md`、`docs/三期交付总览.md`（一/二期：`docs/一期交付总览.md`、`docs/二期交付总览.md`）
- 最高工程约定（**宪章 v2.1.0**）：`.specify/memory/constitution.md`
- 某特性的依据与施工图：`specs/<NNN>-<短名>/`（`spec.md` / `plan.md` / `tasks.md`）
- 立项依据与运营侧前提：`docs/三期立项书.md`

## 1. 特性流程

`/skill:speckit-specify` → `clarify` → `plan` → `tasks` → `analyze` → `implement`（收口可用 `converge`）。
产物落 `specs/<NNN>-<短名>/`。

## 2. 精简执行约定（本仓默认）

目的：把上下文预算花在**证据**上，而不是文档体量与回报长度上。

### 2.1 设计件范围

- 默认只写三件：`spec.md`、`plan.md`（**决策与契约并成 plan 的小节**）、`tasks.md`。
- `research.md` / `data-model.md` / `contracts/*.md` / `quickstart.md` **仅在**下列情况才单列：
  (a) 存在需要推演、且会被反复引用的决策；(b) 契约面复杂到必须单独冻结（多方并行实现同一接口）。否则并进 `plan.md`。
- 经验上限：单特性设计件总量 **≤800 行**。超过就是过度设计，先砍再写。

### 2.2 委派

- **一次一个子代理**（不铺并行），除非文件面确实互不重叠且都无外部依赖。
- brief **≤300 字**：给**任务号 + 红线 + 回报格式**，让子代理自己去读 `tasks.md` / 契约。**不要**在 brief 里复述任务书。
- **回报限长 ≤300 字**，固定四段：
  ① 新增/修改文件（只列名字）② **一条**关键命令原文 + 输出关键行 ③ 通过 / 失败 ④ 未决项。
  **禁止**：表格、逐条复述任务书、大段 diff、把"我读了 X"当证据。

### 2.3 父代理职责

- 只做三件：**设计裁决**、**复核门禁硬证据**、**提交**。
- 复核对象是**硬证据**：逐字节不变、既有文件零改动、门禁命令与计数、清单 `violations`。**不逐条复查文档措辞**。
- 慢门禁**由父代理在宿主上跑**，子代理不得跑（见 §3）。

## 3. 门禁纪律

- 收口必须跑齐并贴实测数字：
  - 覆盖率 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov=web --cov-report=term-missing --cov-fail-under=85`（**≥85%**）
  - 契约两条腿：`uv run pytest tests/contract` 与 `CINEFLOW_CONTRACT_STUB=1 uv run pytest tests/contract -q`
  - 集成 `uv run pytest tests/integration -m integration`、对抗 `uv run pytest tests/adversarial -m adversarial`、
    无偏性 `uv run pytest tests/unbiasedness -m unbiasedness`、`uv run ruff check . && uv run ruff format --check .`
- **门禁一律串行**：对抗套件**不具备并行安全性**——与全量单测（含起容器的用例）或集成并跑时，其 autouse
  "无孤儿容器"断言会因并发的 `cineflow-sandbox-*` 容器**假红**。
- **断言里不得有"运行日"隐含依赖**：时间一律经**显式时钟**注入（否则写出当天绿、次日必红）。
- 既有断言**零删除、零放宽**；只能"按扩展更新"（加键、加参数、加用例）。改断言去迁就实现属返工。
- **提交分批**：机制改动**先单独提交** → 以该提交为基线取"接入/改动清单"证据 → **再**提交接入件。
  反序会让取证不可复现（接入件进了基线树 ⇒ 清单只能得到空集）。

## 4. 诚实与红线

- 不发明数字、平台名、凭证、业务口径；未给定 ⇒ 如实标"未标定"，并写明**机检后果**与**未给定期间的默认行为**。
- 模拟件**不得**冒充真实：`RUN_SOURCES` 取值域、"模拟被标为真实次数恒 0" 是硬机检。
- 既有评估器实现文件**零改动**（文件字节进 `implementation_version` 哈希）；历史产物**零回改**。
- 偏离、失败、未验证项**必须如实登记**（写进 `tasks.md` 的批次登记或交付总览），**不得**用未跑的结果充证据。
- 子代理一律**不提交**；`git add`/`commit` 由父代理做。
