"""剧本分阶段产出执行器（contracts/screenplay-loop.md C1~C3，降级模式闭环本体）。

一轮产出：输入预检（缺题材/目标时长非法 → 执行前拒绝，0 副作用）→ 人工策略产
**分阶段计划**（outline/scenes/script 各自的结构化标记）→ 计划执行前校验（缺标记/
形状非法 → 该阶段拒绝且 0 网关调用 0 成本）→ 逐阶段经网关生成（唯一昂贵动作，原则三：
缓存键 = 模型 + 提示词 + 采样参数，响应哈希落盘供回放核对；同输入跨轮次命中缓存即
零成本复现）→ 工件 = 计划标记 + 网关正文（内容寻址入对象存储）→ 评估器协议注入打分
（gate 短路 + 适用权重归一）→ quantize 定点归一 → 节点一次性 INSERT 冻结。
网关失败/工件构造失败/评估器崩溃一律按阶段隔离：成本照计、轮次继续（原则二）。

**分阶段输入脉络**：前一阶段产出的工件是后一阶段提示词与参数的输入（
`params.previous_artifact_hash` 如实记录上游工件哈希；上游未产出即 null，不伪造）。

**幂等**（C1 场景 2）：tree_id/job_id 由 round_id 确定性派生 + 运营表唯一键
(round_id, stage, params_hash)；二次触发撞树锚点重复约束 → 重建首轮
ScreenplayRoundResult（0 重复生成、0 重复扣费、0 重复节点/行）。

**配置即形态**（原则五）：节拍表/别名表/比例区间/目标时长/模型与价目/judge 段全部来自
ScreenplayConfig，冻结进轮次树 config_snapshot（历史节点不受此后配置变更影响）。

**评估器装配**（T923）：`evaluators=None` → 默认装配真实七评估器（
`build_screenplay_evaluators`，需网关；gate 短路不跑 judge）；dict → 真实七评估器编排
（`evaluate_screenplay`）；list → 评估器协议注入路径（US3 回放/无偏性重算复用）。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import blake3
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.screenplay.artifact import STAGES, ScriptArtifact
from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.db import screenplay_jobs
from agents.screenplay.evaluators import build_screenplay_evaluators
from agents.screenplay.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_screenplay,
    evaluate_screenplay,
)
from core.calibration.drift_gate import DriftGate, apply_gate
from core.evaluators.base import ArtifactRef, Evaluator
from core.evaluators.quantize import quantize_score
from core.llm_gateway.gateway import GatewayError, LLMGateway
from core.llm_gateway.profiles import (  # noqa: E402 - 功能 016 快照接线
    gateway_profile_snapshot,
    with_llm_profiles,
)
from core.llm_gateway.routing import Role  # 功能 016：调用角色（路由只在网关）
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError, ValidationError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

AGENT_ID = "screenplay"
PROJECT_ID = "screenplay"
TEMPERATURE = 0.0  # 生成走确定性档（缓存收敛非确定性，原则三）
# 单次生成的最大输出 token（三段共用，也是预估成本上界的输入）。
# 为什么这么大：真实 LLM 单轮实测——接入的**推理模型**（思维链与正文共享输出预算），`script` 阶段
# （最长产出：4 场景 × 12 行 JSON）在 1024 与 4096 预算下都出现
# `finish_reason='length'` + `reasoning_content` 非空 + `content=""`：预算被思维链吃光，
# 正文一个字都没输出（网关如实报"后端返回空 content"并给出形态，不静默产出空工件）。
# 预算必须覆盖"思维链 + 正文"，故按最坏情况给足（预估成本上界随之保守上抬，见 _estimate_cost）。
MAX_TOKENS = 16384
# 未产出工件的节点占位哈希（拒绝/失败节点无工件可引）
PLACEHOLDER_HASH = "00" * 32
# 执行前校验用占位正文（仅试构造工件，不落库、不调网关）
PLACEHOLDER_TEXT = "（待生成：仅供执行前校验）"
_MARKER_KEYS = ("beats", "scenes", "characters", "lines")

_STAGE_INSTRUCTIONS = {
    "outline": "写出三幕结构与关键节拍的大纲正文：开场画面、激励事件、第一幕转折、"
    "中点、灵魂黑夜、第二幕转折、高潮与结局走向。",
    "scenes": "在已确认大纲的基础上写出分场：逐场场景头（内景/外景 - 地点 - 时间）、"
    "出场角色与场次内容，保持剧内时间戳单调递进。",
    "script": "在已确认分场的基础上写出剧本：逐行台词与动作，标注关键行与情绪基调，"
    "对白行占比落在配置区间内。",
}


class ScreenplayLoopError(Exception):
    """剧本闭环错误（输入预检/计划预检/评估器装配等）。"""


class ScreenplayPolicy(Protocol):
    """剧本策略协议（降级模式：策略由人工编写，不自动进化）。

    `plan(inputs, config)` 产**分阶段计划**：`{stage: {beats, scenes, characters, lines}}`
    ——每个阶段的结构化标记与 ScriptArtifact 同 schema（节拍/场景头/角色表/行）；
    策略只定结构，阶段正文由网关生成（提示词入缓存键与参数哈希）。
    """

    policy_version: str

    def plan(self, inputs: dict, config: ScreenplayConfig) -> dict: ...


@dataclass(frozen=True)
class ScreenplayRoundResult:
    """轮次收口：分阶段 jobs 汇总 + 成本对账。"""

    round_id: str
    tree_id: str
    policy_version: str
    # [{"job_id","stage","status","reason","artifact_hash","cache_key","response_hash"}]
    jobs: list[dict]
    spent_usd: float
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"screenplay-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _job_id(round_id: str, stage: str) -> str:
    return f"{round_id}-{stage}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def stage_cache_key(model: str, prompt: str, temperature: float, max_tokens: int) -> str:
    """网关缓存键（与网关内部同构成：模型|提示词|温度|max_tokens）。

    同构成即"命中缓存 == 同一次调用的产出"：落盘后回放可核对（原则三）。
    """
    return blake3.blake3(f"{model}|{prompt}|{temperature}|{max_tokens}".encode()).hexdigest()


def stage_match_key(
    stage: str,
    *,
    policy_version: str,
    inputs: dict,
    config: ScreenplayConfig,
    markers,
) -> dict:
    """回放匹配键（002 规范化精确匹配槽）：**策略可复现的结构键**。

    只含"给定策略源码 + 输入 + 配置即可重算"的部分（stage / 策略版本 / 模型与采样档 /
    目标时长 / 结构计划摘要）——**不含生成产物摘要**（prompt 摘要与上游工件哈希依赖
    生成结果，回放不得触发生成，故不入匹配键；它们另存观测与运营表供审计，007 教训）。
    同结构同策略 → 同键（键序无关，002 `normalize_params` 口径）；未命中即 UNKNOWN。
    """
    return {
        "stage": stage,
        "policy_version": policy_version,
        "model": config.model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "target_duration_min": inputs["target_duration_min"],
        "plan_digest": blake3.blake3(_canonical(markers).encode()).hexdigest(),
    }


def _canonical(value) -> str:
    """规范化 JSON（键排序）：参数哈希与计划摘要的确定性底座。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _string_list(value, field_name: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ScreenplayLoopError(f"inputs.{field_name} 必须为字符串列表，实际为 {value!r}")
    for item in value:
        if not isinstance(item, str) or not item:
            raise ScreenplayLoopError(f"inputs.{field_name} 必须为非空字符串列表，实际为 {value!r}")
    return list(value)


def _validate_inputs(inputs: dict, config: ScreenplayConfig) -> dict:
    """输入预检先于一切副作用（缺题材/目标时长非法 → 执行前拒绝，0 网关 0 落库）。"""
    if not isinstance(inputs, dict):
        raise ScreenplayLoopError(f"inputs 必须为 dict，实际为 {inputs!r}")
    topic = inputs.get("topic")
    if not isinstance(topic, str) or not topic:
        raise ScreenplayLoopError("inputs 缺少 topic（题材，非空字符串）")
    minutes = inputs.get("target_duration_min", config.target_duration_min)
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        raise ScreenplayLoopError(
            f"inputs.target_duration_min 必须为正整数分钟，实际为 {minutes!r}"
        )
    return {
        "topic": topic,
        "target_duration_min": minutes,
        "constraints": _string_list(inputs.get("constraints", []), "constraints"),
        "characters": _string_list(inputs.get("characters", []), "characters"),
    }


def _stage_plan(plan, stage: str) -> tuple[dict | None, str | None]:
    """阶段计划执行前校验：缺阶段/缺标记/形状非法一律拒绝（0 网关调用 0 成本）。

    校验口径 = 用占位正文试构造同阶段工件（执行前校验即门禁的提前计算，008 同款纪律）；
    真工件随后用网关正文构造，构造失败按阶段失败处理（费用已发生，照计）。
    """
    if not isinstance(plan, dict):
        return None, f"策略计划缺少 {stage} 阶段（执行前拒绝，不调用网关）"
    missing = [key for key in _MARKER_KEYS if not plan.get(key)]
    if missing:
        return None, f"策略计划 {stage} 阶段缺结构化标记 {missing}（执行前拒绝）"
    markers = {key: plan[key] for key in _MARKER_KEYS}
    try:
        ScriptArtifact(stage=stage, text=PLACEHOLDER_TEXT, **markers)
    except ValidationError as exc:
        return None, f"策略计划 {stage} 阶段非法：{exc}（执行前拒绝）"
    return markers, None


def _stage_prompt(
    stage: str,
    *,
    policy_version: str,
    inputs: dict,
    config: ScreenplayConfig,
    markers: dict,
    previous: ScriptArtifact | None,
) -> str:
    """阶段提示词：输入摘要 + 结构计划 + 前一阶段工件（确定性，无时间戳/随机流）。

    确定性是缓存收敛的前提（同输入跨轮次命中缓存零成本复现，原则三）。
    """
    constraints = "；".join(inputs["constraints"]) if inputs["constraints"] else "无"
    cast = "；".join(inputs["characters"]) if inputs["characters"] else "无"
    previous_text = (
        previous.canonical_json()
        if previous is not None
        else "未产出（上游阶段失败或未运行，如实标注）"
    )
    rows = [
        f"【剧本生成 · {stage} 阶段】人工策略版本 {policy_version}",
        f"题材：{inputs['topic']}",
        f"题材约束：{constraints}",
        (
            f"目标时长：{inputs['target_duration_min']} 分钟"
            f"（页数口径 {inputs['target_duration_min']} 页 / 每页 {config.lines_per_page} 行；"
            f"容差 ±{config.page_tolerance} 页）"
        ),
        f"角色设定：{cast}",
        f"结构计划（节拍/场景/角色/行，与工件同 schema）：{_canonical(markers)}",
        f"前一阶段工件：{previous_text}",
        f"阶段要求：{_STAGE_INSTRUCTIONS[stage]}",
    ]
    return "\n".join(rows)


def _stage_params(
    stage: str,
    *,
    policy_version: str,
    inputs: dict,
    config: ScreenplayConfig,
    prompt: str,
    markers: dict,
    previous: ScriptArtifact | None,
) -> dict:
    """阶段运营参数（唯一键分量）：回放结构键 + 生成产物摘要（审计与缓存核对）。"""
    key = stage_match_key(
        stage,
        policy_version=policy_version,
        inputs=inputs,
        config=config,
        markers=markers,
    )
    return {
        **key,
        "prompt_digest": blake3.blake3(prompt.encode()).hexdigest(),
        # 分阶段输入脉络：上游工件哈希（未产出即 null，不伪造）
        "previous_artifact_hash": None if previous is None else previous.artifact_hash(),
    }


def _estimate_cost(prompt: str, price: dict, max_tokens: int) -> float:
    """预估成本上界（网关价目）：输入 token 估计 + 满额输出 token。

    输出按 max_tokens 满额估算是刻意保守——保证 actual ≤ estimated（schema CHECK），
    失败调用照计预估成本（原则二）。
    """
    prompt_tokens = max(1, len(prompt) // 2)
    return (
        prompt_tokens / 1000 * price["prompt_per_1k"]
        + max_tokens / 1000 * price["completion_per_1k"]
    )


def _score(
    evaluators, artifact_ref: ArtifactRef, context: dict, weights: dict
) -> tuple[dict, float, dict]:
    """打分：dict = 真实七评估器编排（C11）；list = 评估器协议注入路径（US3 回放复用）。

    返回 (breakdown, score, 评估器计费用量)；协议注入路径逐评估器汇总 `last_usage`
    （judge 类评估器暴露 llm_calls/llm_tokens/cost_usd），由调用方入节点成本。
    """
    if isinstance(evaluators, dict):
        return evaluate_screenplay(evaluators, artifact_ref, context, weights)
    usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
    breakdown: dict[str, dict] = {}
    for evaluator in evaluators:
        result = evaluator.evaluate(artifact_ref, context)
        breakdown[evaluator.spec.key] = {
            "score": result.score,
            "diagnostics": result.diagnostics,
        }
        last_usage = getattr(evaluator, "last_usage", None)
        if isinstance(last_usage, dict):
            usage["llm_calls"] += int(last_usage.get("llm_calls", 0))
            usage["llm_tokens"] += int(last_usage.get("llm_tokens", 0))
            usage["cost_usd"] += float(last_usage.get("cost_usd", 0.0))
    return breakdown, quantize_score(composite_screenplay(breakdown, weights)), usage


def _config_snapshot(config: ScreenplayConfig, evaluators) -> dict:
    """配置快照随树冻结（原则五）：此后配置变更不影响历史节点与得分。"""
    all_evaluators = evaluators["all"] if isinstance(evaluators, dict) else evaluators
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {
            evaluator.spec.evaluator_id: evaluator.spec.version for evaluator in all_evaluators
        },
        # 回放投影白名单（002）：只暴露策略侧可消费的观测槽——网关核对键
        # （cache_key/response_hash）留运营表与节点观测，不进沙箱投影（原则四）
        "observation_fields": ["gen_params", "stage", "job_id"],
        "composite_policy": COMPOSITE_POLICY,
        "target_duration_min": config.target_duration_min,
        "page_tolerance": config.page_tolerance,
        "lines_per_page": config.lines_per_page,
        "dialogue_action_ratio": config.dialogue_action_ratio,
        "beat_sheet": [dict(beat) for beat in config.beat_sheet],
        "character_aliases": {
            name: list(aliases) for name, aliases in config.character_aliases.items()
        },
        "upgrade_criteria": config.upgrade_criteria,
        "model": config.model,
        "judge": config.judge,
        "anchor_outlines": [anchor.to_dict() for anchor in config.anchor_outlines],
    }


def _root_node(
    tree_id: str,
    root_id: str,
    round_id: str,
    policy_version: str,
    inputs: dict,
    stages: list[dict],
) -> TreeNode:
    """轮次锚点根节点（结构起点；输入摘要与阶段计划结论存档供审计）。"""
    constraints = "；".join(inputs["constraints"]) if inputs["constraints"] else "无"
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id=AGENT_ID,
        policy_version=policy_version,
        prompt=(
            f"剧本产出轮次 {round_id}：题材 {inputs['topic']} | 约束 {constraints} | "
            f"目标时长 {inputs['target_duration_min']} 分钟"
        ),
        observation_context={"round_id": round_id, "inputs": inputs, "stages": stages},
        artifact_hash=PLACEHOLDER_HASH,
        eval_breakdown={},
        score=0.0,
        cost=CostRecord(),
        status=NodeStatus.EVALUATED,
        created_at=time.time(),
    )


def _append_stage_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    job_id: str,
    stage: str,
    policy_version: str,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    observation: dict,
    prompt: str = "",
) -> None:
    """阶段节点一次性完整 INSERT（落盘即冻结）；观测携带回放匹配槽与网关核对键。"""
    context = {"job_id": job_id, "stage": stage}
    context.update(observation)
    store.append_node(
        TreeNode(
            node_id=f"{job_id}-node",
            tree_id=tree_id,
            parent_id=root_id,
            depth=1,
            agent_id=AGENT_ID,
            policy_version=policy_version,
            prompt=prompt,
            observation_context=context,
            artifact_hash=artifact_hash,
            eval_breakdown=breakdown,
            score=score,
            cost=cost,
            status=status,
            created_at=time.time(),
        )
    )


def _insert_job(
    engine: Engine,
    *,
    job_id: str,
    round_id: str,
    stage: str,
    policy_version: str,
    params: dict,
    params_hash: str,
    cache_key: str | None,
    response_hash: str | None,
    status: str,
    estimated: float,
    actual: float | None,
    artifact_hash: str | None,
    error: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(screenplay_jobs).values(
                job_id=job_id,
                round_id=round_id,
                stage=stage,
                policy_version=policy_version,
                params_json=_canonical(params),
                params_hash=params_hash,
                cache_key=cache_key,
                response_hash=response_hash,
                status=status,
                estimated_cost_usd=estimated,
                actual_cost_usd=actual,
                artifact_hash=artifact_hash,
                error=error,
                created_at=_now_iso(),
            )
        )


def _mark_inserted(engine: Engine, job_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            screenplay_jobs.update()
            .where(screenplay_jobs.c.job_id == job_id)
            .values(status="inserted")
        )


def _round_spent(engine: Engine, round_id: str) -> float:
    with engine.connect() as conn:
        return float(
            conn.execute(
                select(func.sum(screenplay_jobs.c.actual_cost_usd)).where(
                    screenplay_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _reconcile(store: TreeStore, engine: Engine, tree_id: str, round_id: str, judge_cost: float):
    """对账：树内成本合计 == 运营表实际扣费合计 + 评估器（judge）计费增量。

    生成成本两侧同源（运营表即网关折算入账），judge 成本只进树内节点（网关侧账目），
    故以评估器用量增量为桥（004/007 同口径）。
    """
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    ledger_total = _round_spent(engine, round_id)
    return {
        "tree_total_usd": tree_total,
        "ledger_total_usd": ledger_total,
        "evaluator_cost_usd": judge_cost,
        "consistent": abs(tree_total - (ledger_total + judge_cost)) < 1e-9,
    }


def run_screenplay_round(
    round_id: str,
    policy: ScreenplayPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    engine: Engine,
    gateway: LLMGateway,
    config: ScreenplayConfig,
    inputs: dict,
    evaluators: list[Evaluator] | dict | None = None,
    drift_gate: DriftGate | None = None,
) -> ScreenplayRoundResult:
    """执行一轮剧本分阶段产出（全流程幂等）。

    drift_gate：漂移合成门禁（功能 012）——合成前按 judge 漂移状态降权/排除
    （None = 未接线，权重原样）；权重变化 → composite 版本哈希变化 → 自然升版。

    evaluators：None → 默认装配真实七评估器（需 gateway）；dict → 真实七评估器编排；
    list → 评估器协议注入路径（桩/回放重算）。
    阶段失败隔离：网关失败/工件构造失败/评估器崩溃都只影响该阶段（成本照计），
    后续阶段继续并按实际产出记录输入脉络。
    """
    normalized_inputs = _validate_inputs(inputs, config)  # 预检先于一切副作用
    if evaluators is None:
        if gateway is None:  # 装配真实七评估器必须提供网关（judge 计费路径）
            raise ScreenplayLoopError("装配真实七评估器必须提供 LLM 网关（judge 计费路径）")
        evaluators = build_screenplay_evaluators(config, gateway)
    elif not evaluators:
        raise ScreenplayLoopError("evaluators 不能为空列表（评估器协议注入需要至少一个评估器）")

    policy_version = getattr(policy, "policy_version", "unknown")
    raw_plan = policy.plan(normalized_inputs, config)
    stage_plans: dict[str, tuple[dict | None, str | None]] = {}
    stage_records: list[dict] = []
    for stage in STAGES:
        plan = raw_plan.get(stage) if isinstance(raw_plan, dict) else None
        markers, reason = _stage_plan(plan, stage)
        stage_plans[stage] = (markers, reason)
        stage_records.append(
            {
                "stage": stage,
                "plan_digest": None
                if markers is None
                else blake3.blake3(_canonical(markers).encode()).hexdigest(),
                "reject_reason": reason or "",
            }
        )

    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=with_llm_profiles(
            _config_snapshot(config, evaluators),
            gateway_profile_snapshot(gateway),  # 功能 016：档案与价目随快照冻结
        ),
    )
    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回（0 重复生成 0 重复扣费）----
    try:
        store.create_tree(tree)
        store.append_node(
            _root_node(tree_id, root_id, round_id, policy_version, normalized_inputs, stage_records)
        )
    except DuplicateError:
        return _reconstruct(round_id, store, engine)

    jobs: list[dict] = []
    judge_cost = 0.0
    previous: ScriptArtifact | None = None
    for stage in STAGES:
        markers, reject_reason = stage_plans[stage]
        job, produced, stage_judge_cost = _run_stage(
            stage,
            markers=markers,
            reject_reason=reject_reason,
            round_id=round_id,
            tree_id=tree_id,
            root_id=root_id,
            policy_version=policy_version,
            inputs=normalized_inputs,
            config=config,
            previous=previous,
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            evaluators=evaluators,
            drift_gate=drift_gate,
        )
        jobs.append(job)
        judge_cost += stage_judge_cost
        previous = produced  # 输入脉络：仅实际产出才成为下游输入（未产出即 null）

    return ScreenplayRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        cost_reconciliation=_reconcile(store, engine, tree_id, round_id, judge_cost),
    )


def _run_stage(
    stage: str,
    *,
    markers: dict | None,
    reject_reason: str | None,
    round_id: str,
    tree_id: str,
    root_id: str,
    policy_version: str,
    inputs: dict,
    config: ScreenplayConfig,
    previous: ScriptArtifact | None,
    store: TreeStore,
    artifacts: ArtifactStore,
    engine: Engine,
    gateway: LLMGateway,
    evaluators: list[Evaluator] | dict,
    drift_gate: DriftGate | None = None,
) -> tuple[dict, ScriptArtifact | None, float]:
    """单阶段流水线：计划预检 → 网关生成 → 工件内容寻址 → 评估 → 落盘。"""
    job_id = _job_id(round_id, stage)
    empty_job = {
        "job_id": job_id,
        "stage": stage,
        "status": "rejected",
        "reason": reject_reason,
        "artifact_hash": None,
        "cache_key": None,
        "response_hash": None,
    }
    if markers is None:
        # 执行前拒绝：0 网关调用 0 成本，拒绝原因如实落节点
        _append_stage_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            stage=stage,
            policy_version=policy_version,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            observation={"reject_reason": reject_reason},
        )
        return empty_job, None, 0.0

    prompt = _stage_prompt(
        stage,
        policy_version=policy_version,
        inputs=inputs,
        config=config,
        markers=markers,
        previous=previous,
    )
    params = _stage_params(
        stage,
        policy_version=policy_version,
        inputs=inputs,
        config=config,
        prompt=prompt,
        markers=markers,
        previous=previous,
    )
    params_hash = blake3.blake3(_canonical(params).encode()).hexdigest()
    # 功能 016（遗留 1 收敛）：估算与折算**同源**——都取网关本次调用生效的价目
    # （接档案时来自角色命中的档案价目；未接档案时来自 price_book），两价目不会脱钩
    call_model, price = gateway.prices_for(role=Role.GENERATION, model=config.model)
    cache_key = stage_cache_key(call_model, prompt, TEMPERATURE, MAX_TOKENS)  # 实际调用模型
    estimated = _estimate_cost(prompt, price, MAX_TOKENS)
    match_key = stage_match_key(
        stage,
        policy_version=policy_version,
        inputs=inputs,
        config=config,
        markers=markers,
    )
    observation = {
        # 回放匹配槽（002 固定读键）：仅策略可复现的结构键
        "gen_params": match_key,
        "params_hash": params_hash,
        "cache_key": cache_key,
        "prompt_digest": params["prompt_digest"],
        "previous_artifact_hash": params["previous_artifact_hash"],
    }

    # 1) 生成经网关（唯一昂贵动作，原则三）：失败 → 节点 FAILED + 预估成本照计
    try:
        generated = gateway.chat(
            prompt,
            model=config.model,  # 旧路径兼容；接档案后模型由角色路由决定
            role=Role.GENERATION,  # 功能 016：剧本生成角色
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except GatewayError as exc:
        return _fail_stage(
            f"网关失败：{exc}",
            stage=stage,
            job_id=job_id,
            round_id=round_id,
            tree_id=tree_id,
            root_id=root_id,
            policy_version=policy_version,
            params=params,
            params_hash=params_hash,
            cache_key=cache_key,
            response_hash=None,
            estimated=estimated,
            actual=estimated,  # 失败照计预估成本（原则二）
            artifact_hash=None,
            cost=CostRecord(llm_calls=1, generation_api_cost_usd=estimated),
            observation=observation,
            prompt=prompt,
            store=store,
            engine=engine,
        )

    response_hash = blake3.blake3(generated.text.encode()).hexdigest()
    observation["response_hash"] = response_hash
    actual = float(generated.cost_usd)

    # 2) 工件构造（网关正文 + 计划标记）：构造失败也照计已发生的费用
    try:
        artifact = ScriptArtifact(stage=stage, text=generated.text, **markers)
    except ValidationError as exc:
        return _fail_stage(
            f"工件构造失败：{exc}",
            stage=stage,
            job_id=job_id,
            round_id=round_id,
            tree_id=tree_id,
            root_id=root_id,
            policy_version=policy_version,
            params=params,
            params_hash=params_hash,
            cache_key=cache_key,
            response_hash=response_hash,
            estimated=estimated,
            actual=actual,
            artifact_hash=None,
            cost=CostRecord(llm_calls=1, generation_api_cost_usd=actual),
            observation=observation,
            prompt=prompt,
            store=store,
            engine=engine,
        )

    # 3) 工件内容寻址入对象存储（哈希即 key，PUT-if-absent 幂等）
    artifact_hash = artifacts.put(artifact.canonical_json().encode())
    observation["artifact_hash"] = artifact_hash
    _insert_job(
        engine,
        job_id=job_id,
        round_id=round_id,
        stage=stage,
        policy_version=policy_version,
        params=params,
        params_hash=params_hash,
        cache_key=cache_key,
        response_hash=response_hash,
        status="generated",
        estimated=estimated,
        actual=actual,
        artifact_hash=artifact_hash,
        error=None,
    )
    tokens = int(generated.usage["prompt_tokens"] + generated.usage["completion_tokens"])

    # 4) 评估器协议注入打分（崩溃隔离：FAILED 成本照计，轮次继续）
    artifact_ref = ArtifactRef(artifact_hash=artifact_hash, metadata={"stage": stage})
    context = {
        "artifact": artifact,
        "stage": stage,
        "inputs": inputs,
        "markers": markers,
        "config": config,
        "round_id": round_id,
        "previous_artifact": previous,
    }
    weights = apply_gate(config.evaluator_weights, drift_gate)  # 漂移门禁（合成前一处，012）
    try:
        breakdown, score, usage = _score(evaluators, artifact_ref, context, weights)
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离：FAILED 成本入账轮次继续
        _mark_inserted(engine, job_id)
        return _fail_stage(
            f"评估器崩溃：{exc}",
            stage=stage,
            job_id=job_id,
            round_id=round_id,
            tree_id=tree_id,
            root_id=root_id,
            policy_version=policy_version,
            params=params,
            params_hash=params_hash,
            cache_key=cache_key,
            response_hash=response_hash,
            estimated=estimated,
            actual=actual,
            artifact_hash=artifact_hash,
            cost=CostRecord(llm_calls=1, llm_tokens=tokens, generation_api_cost_usd=actual),
            observation=observation,
            prompt=prompt,
            store=store,
            engine=engine,
            recorded=True,
        )

    _append_stage_node(
        store,
        tree_id=tree_id,
        root_id=root_id,
        job_id=job_id,
        stage=stage,
        policy_version=policy_version,
        artifact_hash=artifact_hash,
        status=NodeStatus.EVALUATED,
        score=score,
        breakdown=breakdown,
        cost=CostRecord(
            llm_calls=1 + usage["llm_calls"],
            llm_tokens=tokens + usage["llm_tokens"],
            generation_api_cost_usd=actual + usage["cost_usd"],
        ),
        observation=observation,
        prompt=prompt,
    )
    _mark_inserted(engine, job_id)
    return (
        {
            "job_id": job_id,
            "stage": stage,
            "status": "inserted",
            "reason": "",
            "artifact_hash": artifact_hash,
            "cache_key": cache_key,
            "response_hash": response_hash,
        },
        artifact,
        usage["cost_usd"],
    )


def _fail_stage(
    reason: str,
    *,
    stage: str,
    job_id: str,
    round_id: str,
    tree_id: str,
    root_id: str,
    policy_version: str,
    params: dict,
    params_hash: str,
    cache_key: str,
    response_hash: str | None,
    estimated: float,
    actual: float,
    artifact_hash: str | None,
    cost: CostRecord,
    observation: dict,
    prompt: str,
    store: TreeStore,
    engine: Engine,
    recorded: bool = False,
) -> tuple[dict, None, float]:
    """阶段失败落盘：运营表 failed（成本照计）+ 节点 FAILED（原则二/三）。"""
    if not recorded:  # 运营表尚无本阶段行（生成失败/构造失败）→ 补 failed 行
        _insert_job(
            engine,
            job_id=job_id,
            round_id=round_id,
            stage=stage,
            policy_version=policy_version,
            params=params,
            params_hash=params_hash,
            cache_key=cache_key,
            response_hash=response_hash,
            status="failed",
            estimated=estimated,
            actual=actual,
            artifact_hash=artifact_hash,
            error=reason,
        )
    _append_stage_node(
        store,
        tree_id=tree_id,
        root_id=root_id,
        job_id=job_id,
        stage=stage,
        policy_version=policy_version,
        artifact_hash=artifact_hash if artifact_hash is not None else PLACEHOLDER_HASH,
        status=NodeStatus.FAILED,
        score=None,
        breakdown={},
        cost=cost,
        observation={**observation, "reject_reason": reason},
        prompt=prompt,
    )
    return (
        {
            "job_id": job_id,
            "stage": stage,
            "status": "failed",
            "reason": reason,
            "artifact_hash": artifact_hash,
            "cache_key": cache_key,
            "response_hash": response_hash,
        },
        None,
        0.0,
    )


def _reconstruct(round_id: str, store: TreeStore, engine: Engine) -> ScreenplayRoundResult:
    """幂等重建：二次触发撞树锚点后，从树与运营表还原首轮 ScreenplayRoundResult。

    jobs/成本合计逐项还原；**对账字段留空**——评估器计费增量不在重算范围内，
    宁可留空也不伪造"一致"（原则六：不编造证据）。
    """
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    nodes = {
        node.observation_context.get("stage"): node
        for node in store.nodes_of(tree_id)
        if node.parent_id is not None
    }
    with engine.connect() as conn:
        rows = {
            row.stage: row
            for row in conn.execute(
                select(screenplay_jobs).where(screenplay_jobs.c.round_id == round_id)
            )
        }
    jobs: list[dict] = []
    for stage in STAGES:
        node = nodes.get(stage)
        row = rows.get(stage)
        context = node.observation_context if node is not None else {}
        if row is not None:
            # 评估器崩溃：运营表已 inserted 但节点 FAILED —— 如实报 failed
            failed = node is not None and node.status is NodeStatus.FAILED
            status = "failed" if failed else row.status
            reason = row.error or context.get("reject_reason", "")
            artifact_hash = row.artifact_hash
            cache_key = row.cache_key
            response_hash = row.response_hash
        else:
            status = "rejected"
            reason = context.get("reject_reason", "")
            artifact_hash = cache_key = response_hash = None
        jobs.append(
            {
                "job_id": _job_id(round_id, stage),
                "stage": stage,
                "status": status,
                "reason": reason,
                "artifact_hash": artifact_hash,
                "cache_key": cache_key,
                "response_hash": response_hash,
            }
        )
    return ScreenplayRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        cost_reconciliation={},
    )
