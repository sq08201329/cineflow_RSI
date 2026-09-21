# 数据模型：短剧形态试水作品（015-pilot-shortdrama）

> 无新 DB 表——编排产物文件化（`pilot/runs/`、`pilot/packages/`，git 版本化惯例）；
> 各环节落树沿用既有路径（本特性不新增落树路径）。frozen dataclass 为第一道工序。

## 领域模型

- **FormConfig（形态配置）**：一套形态的完整参数集——`configs/{movie,shortdrama}.yaml`；
  字段覆盖全部加载器所需段（evaluator_weights 各 Agent、各 Agent 段、replay/pooling/
  dreaming/calibration/drift/deployment/web）+ 形态标识
- **StageSpec（环节定义）**：stage_id / 名称（script/storyboard/visual/sound/editing/promo）/
  依赖（上游 stage_id 列表）/ 执行入口（业务侧函数引用）/ 输入契约（handoff 函数引用）/
  输出工件类型
- **StageState**：stage_id / status（pending/running/done/failed/skipped）/ 输入指纹 /
  产物引用（工件哈希 + 节点/树引用）/ 候选与判 0 理由（failed 时）/ 成本 / 起止时间
- **RunRecord（试水运行记录）**：run_id / form / config_fingerprint / input_fingerprint /
  stages: [StageState] / 状态（running/done/failed）/ 失败点与原因 / 起止时间
- **HandoffContract（交接契约）**：from_stage / to_stage / 映射函数引用 / 上游导出字段集 /
  下游输入字段集（双向快照断言的数据面）
- **PilotPackage（样片包）**：run_id / 目录 / manifest（**"模拟生成"标注**、形态、
  配置指纹、产物清单、阶段状态）/ 成片引用 / 账目 / 状态快照
- **PilotCostLedger**：按 stage 与形态的成本汇总 + 与各 Agent 落盘成本的对账结果
- **UpgradePath（升级路径）**：B/C 的凭证环境变量名 / 适配器实现类 / 切换命令 /
  预算口径（结构化清单 + 文档）

## 文件 schema

```text
pilot/
├── runs/{run_id}.json           # RunRecord（阶段状态、指纹、产物引用、失败原因）
└── packages/{run_id}/
    ├── manifest.json            # "模拟生成"标注 + 形态 + 配置指纹 + 产物清单 + 阶段状态
    ├── reel.mp4                 # 成片（拷贝或引用）
    ├── products.json            # 各环节关键产物引用与哈希
    ├── cost.json                # 账目与对账结果
    └── state.json               # 各环节评分构成 / 坍缩状态 / 漂移摘要
```

## 状态机

- RunRecord：`running → done`（全部阶段 done）| `failed`（某阶段 failed，其后阶段 skipped）
- StageState：`pending → running → done | failed`；续跑时 `failed → running`（输入指纹一致）
- 阶段依赖：script → storyboard → visual → sound → editing → promo（结构上 visual 与
  sound 可并行；本特性按串行执行，依赖声明支持并行——留扩展）

## 配置（`configs/shortdrama.yaml`）

- `form: shortdrama` + 全部加载器所需段；形态差异（相对 movie）：评估器权重与阈值、
  节奏基准曲线（短剧体量，前段权重上调）、外环频率（日级）、预算与并行度（下调）、
  时长与规格（竖屏、1~3 分钟）、单页行数/时长换算参数
