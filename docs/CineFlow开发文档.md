# CineFlow 开发文档（工程实现版）
## 影视全 Agents 自我进化生产系统

> 版本：v0.1（对应技术方案 v1.0 的阶段一范围）
> 性质：可执行开发文档 —— 接口、数据模型、模块规格、验收标准
> 阶段一范围：L1 基建 + 视觉 Agent + 宣发 Agent 双闭环

---

## 0. 技术选型与仓库结构

### 0.1 技术栈

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.11+ | LLM 生态、评估模型生态 | uv 管理安装包 |
| 任务编排 | 自研轻量 DAG 执行器（不引入 Airflow） | 探索决策是动态的，静态 DAG 框架不匹配 |
| 存储 | PostgreSQL（元数据/谱系） + S3 兼容对象存储（工件/快照） | 节点 immutable 语义由存储层配合实现 |
| 内容寻址 | BLAKE3 哈希 | 工件去重 |
| 沙箱 | Docker（gVisor runtime） | 策略代码与生成工件隔离执行 |
| LLM 网关 | 自研薄层（统一计费、缓存、重试） | 成本计量是核心需求，不可依赖黑盒 SDK |
| 前端 | 二期再议，一期仅内部 CLI + JSON 报告 | — |

### 0.2 Monorepo 结构

```
cineflow/
├── core/                    # L1 基建，业务无关
│   ├── tree/                # 发现树：模型、存储、谱系
│   ├── replay/              # 回放模拟器引擎 + 前缀拦截
│   ├── sandbox/             # 容器化执行
│   ├── llm_gateway/         # LLM 统一入口（计费/缓存）
│   ├── cost/                # 成本计量
│   └── evaluators/          # 评估器注册中心 + 基类
├── agents/                  # L2 生产 Agent（每目录一个）
│   ├── visual/
│   └── promo/               # 宣发
├── dreaming/                # 策略开发 Agent + 奖励函数
├── policies/                # 探索策略基类 + 历史版本
├── configs/                 # 形态配置（movie/shortdrama/...）
├── tests/
│   ├── adversarial/         # 前缀泄露对抗测试（必过）
│   └── unbiasedness/        # 回放无偏性验收
└── ops/                     # 部署、迁移、数据回流脚本
```

依赖方向严格单向：`agents → core`，`dreaming → core`，禁止反向。

---

## 1. 核心数据模型（core/tree）

### 1.1 节点与树

```python
# core/tree/models.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class NodeStatus(str, Enum):
    PLANNED = "planned"        # 仅线上探索中存在；回放中不落此态
    EVALUATED = "evaluated"    # 终态：得分已确定，可回放
    FAILED = "failed"          # 评估器崩溃等，score=None，仍可回放（成本已发生）

@dataclass(frozen=True)        # frozen=True 是 immutable 约束的第一道防线
class CostRecord:
    llm_calls: int = 0
    llm_tokens: int = 0
    generation_api_calls: int = 0     # 视频/TTS/渲染等昂贵动作
    generation_api_cost_usd: float = 0.0
    human_review_minutes: float = 0.0
    wall_clock_seconds: float = 0.0

@dataclass(frozen=True)
class TreeNode:
    node_id: str                     # uuid7（时间有序）
    tree_id: str
    parent_id: str | None            # 根节点 None
    depth: int
    agent_id: str                    # "visual" / "promo" / ...
    policy_version: str              # 产生此节点的策略版本，谱系关键字段

    prompt: str                      # 本次尝试的提示词
    observation_context: dict        # 决策时可见的累积观测（回放重建依据）
    artifact_hash: str               # 工件内容寻址哈希 → 对象存储
    eval_breakdown: dict             # {"rule": ..., "proxy": ..., "judge": ...}
    score: float | None              # 合成得分；FAILED 为 None
    cost: CostRecord
    status: NodeStatus
    created_at: float

@dataclass(frozen=True)
class DiscoveryTree:
    tree_id: str
    project_id: str
    agent_id: str
    policy_version: str
    root_id: str
    node_ids: list[str]              # 节点本体按 node_id 存 KV，树只存结构
    config_snapshot: dict            # 评估器版本等全量配置快照
```

**存储层强制约束**（PostgreSQL 侧）：

- `tree_nodes` 表**禁用 UPDATE/DELETE**（触发器拒绝），只允许 INSERT —— immutable 的第二道防线；
- `eval_breakdown` 必须包含每个评估器的 `evaluator_id@version`，缺失即拒绝写入（第三道防线）；
- 工件写入对象存储的 key 即 `artifact_hash`，天然去重。

### 1.2 谱系查询接口

```python
class TreeStore(Protocol):
    def append_node(self, node: TreeNode) -> None: ...
    def get_node(self, node_id: str) -> TreeNode: ...
    def children(self, node_id: str) -> list[TreeNode]: ...
    def trees_by(self, *, project_id: str | None = None,
                 agent_id: str | None = None,
                 policy_version: str | None = None) -> list[DiscoveryTree]: ...
```

---

## 2. 评估器框架（core/evaluators）

### 2.1 基类与注册

```python
# core/evaluators/base.py
class EvaluatorKind(str, Enum):
    RULE = "rule"
    PROXY_MODEL = "proxy_model"
    JUDGE = "judge"
    HUMAN = "human"

@dataclass(frozen=True)
class EvalResult:
    score: float                     # 归一化到 [0, 1]
    diagnostics: dict                # 人可读的明细，进节点 eval_breakdown

class Evaluator(ABC):
    spec: EvaluatorSpec              # id / version / kind / deterministic / cost_per_call

    @abstractmethod
    def evaluate(self, artifact: ArtifactRef,
                 context: dict) -> EvalResult: ...

    # 成对比较型 judge 实现此接口；单点打分型不用
    def compare(self, a: ArtifactRef, b: ArtifactRef,
                context: dict) -> float:   # 返回 a 胜率 ∈ [0,1]
        raise NotImplementedError

# 注册中心：评估器以 "evaluator_id@version" 全局唯一
REGISTRY: dict[str, Evaluator] = {}

def register(ev: Evaluator) -> None:
    key = f"{ev.spec.evaluator_id}@{ev.spec.version}"
    assert ev.spec.deterministic or ev.spec.kind == EvaluatorKind.HUMAN, \
        "非确定性评估器禁止注册（human 锚点除外，仅用于校准）"
    assert key not in REGISTRY, "同 id+version 重复注册：任何变更必须升版本号"
    REGISTRY[key] = ev
```

### 2.2 合成评分

```python
# 权重来自形态配置 configs/movie.yaml，冻结进 DiscoveryTree.config_snapshot
def composite_score(breakdown: dict[str, EvalResult],
                    weights: dict[str, float]) -> float:
    # 硬规则为门禁：任一 rule 评估器 score == 0 → 总分直接为 0（不可行解）
    for key, res in breakdown.items():
        if key.startswith("rule.") and res.score == 0.0:
            return 0.0
    return sum(weights[k] * r.score for k, r in breakdown.items())
```

### 2.3 阶段一需实现的评估器清单

**视觉 Agent（agents/visual/evaluators/）**

| evaluator_id | kind | 实现要点 |
| --- | --- | --- |
| `rule.format_compliance` | rule | 分辨率/时长/帧率/编码合规，ffmpeg probe |
| `proxy.aesthetic` | proxy | 冻结版本美学评分模型（模型文件哈希写入 spec） |
| `proxy.identity_consistency` | proxy | 人脸/主体嵌入跨镜头余弦距离 |
| `proxy.flicker` | proxy | 帧间亮度直方图抖动 + 频域伪影检测 |
| `judge.cinematic` | judge | 3 提示词成对比较投票，Elo 转 [0,1] |

**宣发 Agent（agents/promo/evaluators/）**

| evaluator_id | kind | 实现要点 |
| --- | --- | --- |
| `rule.material_compliance` | rule | 平台物料规格（尺寸、时长、敏感词） |
| `proxy.ctr_history` | proxy | 历史投放数据的同类型 CTR 预测模型 |
| `human.platform_metrics` | human* | 完播率/CTR/转化——来自平台回流的**真实值**，视为确定性真值 |

\* 平台数据一旦回流即冻结为常数，可安全用于回放。

---

## 3. 回放模拟器引擎（core/replay）—— 正确性核心

### 3.1 语义定义

模拟器由一棵（或多棵合并的）发现树构建。策略在其中运行时：

- 状态 = 已揭示节点集合；
- 动作 = 选择父节点扩展（带生成参数：batch size、temperature 档）；
- 揭示 = 系统按"该父节点下、与该生成参数匹配的真实历史节点"返回其 `score/cost`——**数据来自历史，不做任何新生成**；
- 无匹配节点 = 该走法历史上从未发生 → 返回 `UNKNOWN`，策略不得获得得分（设计上逼迫策略只在经验覆盖区域内行动；覆盖不足靠扩大线上探索解决，不靠模拟器编造）。

### 3.2 前缀可观测拦截层

```python
# core/replay/simulator.py
class ReplaySimulator:
    def __init__(self, trees: list[DiscoveryTree], store: TreeStore):
        self._revealed: dict[str, TreeNode] = {}     # 已揭示
        self._latent: dict[str, list[TreeNode]] = {} # parent_id → 未揭示子节点
        self._clock = VirtualClock()

    def observed(self) -> dict[str, Observation]:
        """策略唯一的信息入口。只暴露已揭示节点，且只暴露
        observation_context 中允许的字段 —— 拦截层在此。"""
        return {nid: self._to_observation(n) for nid, n in self._revealed.items()}

    def probe(self, parent_id: str, gen_params: dict) -> ProbeResult:
        """揭示动作。校验 gen_params 匹配后移交节点，计入虚拟成本。"""
        self._clock.tick_decision()
        cands = [n for n in self._latent.get(parent_id, [])
                 if self._matches(n, gen_params)]
        if not cands:
            return ProbeResult(status="UNKNOWN")
        for n in cands:
            self._revealed[n.node_id] = n
        self._latent[parent_id] = [n for n in self._latent[parent_id]
                                   if n not in cands]
        self._clock.tick_execution(batch_size=len(cands))
        return ProbeResult(nodes=[self._to_observation(n) for n in cands])
```

**硬性规则**：策略代码拿到的只有 `observed()` 与 `probe()` 两个入口，整个 `_latent` 在沙箱内物理不可达（策略在独立容器进程跑，经 IPC 交互，而非同进程传对象——杜绝反射偷看）。

### 3.3 虚拟时钟与并行惩罚

```python
class VirtualClock:
    decision_rounds: int = 0
    effective_sequential_rounds: float = 0.0
    worker_count: int  # 来自形态配置

    def tick_execution(self, batch_size: int) -> None:
        # 一批 k 个 probe 消耗 ceil(k / W) 个有效串行轮
        self.effective_sequential_rounds += math.ceil(batch_size / self.worker_count)
```

### 3.4 无偏性验收（tests/unbiasedness/）

新评估器或新 Agent 上线前必须通过的测试：

1. 用策略 π 在线跑一次，记录真实轨迹 T_real 与总分；
2. 在由**其他树**组成的模拟器池中回放同版本 π；
3. 回放轨迹与 T_real 的得分序列 Kendall τ ≥ 0.95，否则拒绝上线。

### 3.5 对抗测试（tests/adversarial/，CI 必过）

- `CheatingPolicy.peek_latent()`：尝试遍历模拟器对象属性读未揭示节点 → 必须失败；
- `CheatingPolicy.timing_side_channel()`：用响应时间推断隐藏得分 → 断言响应时间加入固定抖动；
- `CheatingPolicy.hash_oracle()`：用 artifact_hash 反查对象存储 → 沙箱无对象存储凭证，必须 403。

---

## 4. 探索策略接口（policies/）

```python
# policies/base.py
class ExplorationPolicy(ABC):
    """唯一被进化的对象。代码即策略，版本即 git commit hash。"""

    @abstractmethod
    def solve(self, env: SimulatorEnv, budget: Budget) -> str:
        """返回最终选中的最优 node_id。
        env 仅提供 observed() / probe()；budget 限制 probe 总数。"""

@dataclass(frozen=True)
class Budget:
    max_probes: int
    max_generation_calls: int   # 线上探索时生效；回放中恒为 0
```

版本管理：每次做梦产出的策略代码存 `policies/history/{agent_id}/{version}.py`，version = 代码内容的 BLAKE3 前 12 位。谱系 = `(policy_version → tree_ids → child_policy_versions)` 全链路可查。

---

## 5. 做梦层（dreaming/）

### 5.1 策略开发 Agent 流程

```
输入：当前最优策略代码 + 模拟器池中最近 K 轮回放报告 + 评估器诊断摘要
  │
  ├─ 1. 由 LLM 生成 M 套候选策略代码（M 默认 128）
  ├─ 2. 静态检查：接口签名、import 白名单、禁止网络/文件 IO
  ├─ 3. 沙箱内逐套回放打分（全模拟器池）
  ├─ 4. reward = pareto_auc(score, probes) − λ · parallel_penalty
  └─ 5. 胜出策略 → 人工 approve（一期保留）→ 次日部署线上探索
```

### 5.2 奖励函数实现

```python
# dreaming/reward.py
def reward(trajectory: ReplayTrajectory, lam: float = 0.5) -> float:
    auc = pareto_auc(trajectory.best_score_curve, trajectory.probe_count)
    penalty = trajectory.effective_sequential_rounds / max(trajectory.probe_count, 1)
    return auc - lam * penalty
```

### 5.3 防过拟合机制

- 模拟器池按时间分 train/validation 树（最近一棵树永远只做 validation）；
- 候选策略若在 train 上第一但 validation 跌出前 20%，判过拟合，丢弃；
- 固定 ε=0.1：线上探索保留 10% 预算给随机策略，维持树多样性。

---

## 6. 宣发 Agent 的闭环特例（agents/promo）

宣发是一期闭环最干净的环节，其特殊流程：

1. **线上探索** = 真实小规模投放（预算上限由形态配置控制，默认单轮 ≤ 总预算 2%）；
2. 平台数据经 `ops/ingest_metrics.py` 回流 → 写入节点 `eval_breakdown["human.platform_metrics"]`，**写入即冻结**；
3. 回放与做梦与其余 Agent 完全同构——这正是一期先跑它的原因：用真实评估器验证整套基建，再向代理评估器环节推广。

---

## 7. 配置系统（configs/）

形态即配置。`configs/movie.yaml` 示例：

```yaml
form: movie
budget:
  exploration_per_round_usd: 500
  promo_pilot_ratio: 0.02
evaluator_weights:
  visual:
    rule.format_compliance: gate
    proxy.aesthetic: 0.25
    proxy.identity_consistency: 0.35
    proxy.flicker: 0.15
    judge.cinematic: 0.25
dreaming:
  candidates_per_round: 128
  lambda: 0.5
  epsilon_random: 0.1
replay:
  validation_split: latest_tree
  unbiasedness_tau_threshold: 0.95
```

短剧形态 = 另建 `shortdrama.yaml`，改权重、预算、评估器组合，零代码改动。

---

## 8. CI/CD 与质量门禁

| 门禁 | 内容 | 阻塞级别 |
| --- | --- | --- |
| 对抗测试 | §3.5 全部作弊策略必须被拦截 | 合并阻塞 |
| 无偏性测试 | §3.4，新评估器/Agent 上线前 | 发布阻塞 |
| immutable 审计 | 随机抽 100 个历史节点重算 score，与落盘值一致 | 每日定时，失败即告警 |
| 成本回归 | 相同回放任务成本突增 >20% 报警 | 每日定时 |

## 9. 一期里程碑（对应方案阶段一）

| 周 | 交付物 | 验收 |
| --- | --- | --- |
| 1~3 | core/tree + core/evaluators + 注册中心 | 单元测试覆盖 ≥85% |
| 4~6 | core/replay + sandbox + 对抗测试套件 | 作弊策略全拦截；无偏性 τ≥0.95 |
| 7~8 | agents/promo 全闭环（真实投放） | 首轮进化曲线产出，成本数据入账 |
| 9~10 | agents/visual 五个评估器 + 闭环 | 回放打分与真实重跑一致性验收 |
| 11~12 | dreaming 层 + 谱系报表 | 连续 5 轮进化 reward 曲线无塌缩 |

## 10. 已知技术债与二期事项

- 一期人工 approve 策略部署，二期评估自动化；
- 跨项目发现树合并回放（池化复用）二期实现；
- judge 漂移自动检测（对比历史锚点评分分布）二期实现；
- 前端可视化（发现树浏览器、进化曲线看板）二期。
