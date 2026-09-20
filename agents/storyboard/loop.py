"""分镜线上探索执行器（contracts/storyboard-loop.md C1~C3，闭环本体）。

一轮探索：剧本预检（剧本不足/情绪取值非法 → 执行前拒绝，0 副作用）→ 策略产
ShotList 组合 → C1 三层执行前校验（违规 0 渲染 0 成本）→ 预算门禁（申请前校验 +
事务内复核，≤ 语义含最小货币单位）→ 预演渲染（昂贵动作仅此阶段，原则三）→
animatic 内容寻址 → 真实五评估器编排打分（gate 短路不跑 judge；显式注入路径供
US3 回放/无偏性复用）→ 合成得分（quantize 定点归一）→ 节点一次性 INSERT 冻结。
失败 job 成本照计入账 status=failed（原则二）。幂等：tree_id/job_id 由 round_id
确定性派生 + 唯一键 (round_id, shotlist_hash)，二次触发重建首轮
StoryboardRoundResult（0 重复渲染、0 重复扣费）。

观测落**双键**（`shotlist` + `gen_params`）——002 规范化精确匹配固定读
observation_context["gen_params"]（core/replay/matching.GEN_PARAMS_KEY），
分镜侧同一内容落双键换取回放/做梦/盲评全链路对 storyboard 零特判（007 教训复用）。
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.db import storyboard_render_jobs
from agents.storyboard.evaluators import build_storyboard_evaluators
from agents.storyboard.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_storyboard,
    evaluate_storyboard,
)
from agents.storyboard.platform.base import RenderError, StoryboardRenderAdapter
from agents.storyboard.script import ScriptSegment, validate_script
from agents.storyboard.shotlist import ShotList, validate_shotlist
from core.evaluators.base import ArtifactRef, Evaluator
from core.evaluators.quantize import quantize_score
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError, ValidationError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

PLACEHOLDER_HASH = "00" * 32  # 未产出工件的拒绝节点占位哈希（无工件可引）


class StoryboardLoopError(Exception):
    """分镜闭环错误（剧本预检/轮次收口校验失败等）。"""


class StoryboardPolicy(Protocol):
    """分镜策略协议（做梦层接入前的手工策略形态）：产出 ShotList 组合。"""

    policy_version: str

    def plan(self, config: StoryboardConfig, inputs: dict) -> list: ...


@dataclass(frozen=True)
class StoryboardRoundResult:
    """轮次收口：jobs 汇总 + 成本对账 + 预检结论（C2）。"""

    round_id: str
    tree_id: str
    policy_version: str
    jobs: list[dict]  # [{"job_id","status","reason","shotlist_hash"}]
    spent_usd: float
    budget_cap_usd: float
    precheck: str = "ok"  # 剧本不足为硬预检（执行前拒绝不建树）；本字段保留同构形态
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"storyboard-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _job_id(round_id: str, index: int) -> str:
    return f"{round_id}-j{index}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_inputs(inputs: dict, config: StoryboardConfig) -> ScriptSegment:
    """剧本预检先于一切副作用（C2 场景 5：剧本不足 → 执行前拒绝并注明）。"""
    script = inputs.get("script")
    if not isinstance(script, ScriptSegment):
        raise StoryboardLoopError("inputs 缺少 script（ScriptSegment）")
    try:
        validate_script(script, emotion_vectors=config.emotion_vectors)
    except ValidationError as exc:
        raise StoryboardLoopError(f"剧本预检拒绝：{exc}") from exc
    return script


def run_storyboard_round(
    round_id: str,
    policy: StoryboardPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    adapter: StoryboardRenderAdapter,
    engine: Engine,
    config: StoryboardConfig,
    inputs: dict,
    evaluators: list[Evaluator] | None = None,
    gateway: LLMGateway | None = None,
) -> StoryboardRoundResult:
    """执行一轮分镜线上探索（全流程幂等）。

    evaluators：None → 默认装配真实五评估器（build_storyboard_evaluators，需 gateway，
    gate 短路不跑 judge）；dict（{"gates","alignment","judge","all"}）→ 真实编排；
    list → 评估器桩注入路径（US3 无偏性回放重算，面向评估器协议编程）。
    对账三方：树内成本 == 运营表扣减 + 网关增量（judge 计费，004/007 同口径）。
    """
    # 0) 剧本预检先于一切副作用（适配器 0 调用、0 成本、0 落库）
    script = _validate_inputs(inputs, config)
    gateway_before = float(gateway.total_cost_usd) if gateway is not None else 0.0
    if evaluators is None:
        if gateway is None:
            raise StoryboardLoopError("装配真实五评估器必须提供 LLM 网关（judge 计费路径）")
        evaluators = build_storyboard_evaluators(config, gateway)

    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    policy_version = getattr(policy, "policy_version", "unknown")
    cap = config.exploration_per_round_usd

    # 策略产 ShotList 组合：规范化失败（形状非法）同样按"执行前拒绝"处理
    plans: list[dict] = []
    normalized: list[tuple[ShotList | None, str | None]] = []
    for item in policy.plan(config, inputs):
        try:
            shotlist = item if isinstance(item, ShotList) else ShotList.from_dict(item)
        except ValidationError as exc:
            shotlist, error = None, f"ShotList 形状非法：{exc}"
            plans.append(item if isinstance(item, dict) else {"raw": str(item)})
        else:
            error = None
            plans.append(shotlist.to_dict())
        normalized.append((shotlist, error))

    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id="storyboard",
        agent_id="storyboard",
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=_config_snapshot(config, evaluators),
    )
    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回 ----
    try:
        store.create_tree(tree)
        store.append_node(_root_node(tree_id, root_id, round_id, plans, policy_version, script))
    except DuplicateError:
        return _reconstruct(round_id, store, engine, config)

    jobs: list[dict] = []
    for index, (shotlist, error) in enumerate(normalized):
        jobs.append(
            _run_job(
                index,
                shotlist,
                error,
                plans[index],
                round_id,
                tree_id,
                root_id,
                policy_version=policy_version,
                store=store,
                artifacts=artifacts,
                adapter=adapter,
                engine=engine,
                config=config,
                evaluators=evaluators,
                script=script,
            )
        )

    return StoryboardRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=cap,
        cost_reconciliation=_reconcile(
            store, engine, tree_id, round_id, gateway_delta=_gateway_delta(gateway, gateway_before)
        ),
    )


def _gateway_delta(gateway: LLMGateway | None, before: float) -> float:
    """本轮网关计费增量（judge 成本侧；无网关/桩路径恒 0）。"""
    return float(gateway.total_cost_usd) - before if gateway is not None else 0.0


def freeze_round_tree(round_id: str, store: TreeStore, engine: Engine) -> DiscoveryTree:
    """轮次树显式冻结入口（池接线：渲染任务全终态才允许冻结入池，006/007 同构）。

    config_snapshot 在树创建时已写全——树 immutable 不允许后补，
    此函数只做终态校验 + 返回树对象。
    """
    tree_id = round_tree_id(round_id)
    try:
        store.get_node(_round_root_id(round_id))
    except Exception as exc:
        raise StoryboardLoopError(f"轮次树不存在：{tree_id}（round_id={round_id}）") from exc
    with engine.connect() as conn:
        pending = conn.execute(
            select(func.count())
            .select_from(storyboard_render_jobs)
            .where(
                storyboard_render_jobs.c.round_id == round_id,
                storyboard_render_jobs.c.status.in_(["pending", "rendered", "evaluated"]),
            )
        ).scalar()
    if pending:
        raise StoryboardLoopError(f"轮次 {round_id} 尚有 {pending} 个预演任务未终态，不得冻结入池")
    trees = store.trees_by(project_id="storyboard", agent_id="storyboard")
    matches = [t for t in trees if t.tree_id == tree_id]
    if not matches:
        raise StoryboardLoopError(f"轮次树不存在：{tree_id}")
    return matches[0]


def _config_snapshot(config: StoryboardConfig, evaluators: list[Evaluator] | dict) -> dict:
    """快照冻结：权重 + 评估器版本组合 + 观测白名单 + 分镜口径配置。"""
    all_evaluators = evaluators["all"] if isinstance(evaluators, dict) else evaluators
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {e.spec.evaluator_id: e.spec.version for e in all_evaluators},
        "observation_fields": ["gen_params", "shotlist", "shotlist_hash", "job_id"],
        "composite_policy": COMPOSITE_POLICY,  # 合成归一口径进版本元信息（C9）
        "shot_grammar": config.shot_grammar,
        "axis_rules": config.axis_rules,
        "alignment": config.alignment,
        "emotion_vectors": config.emotion_vectors,
        "render": config.render,
        "anchor_shotlists": [anchor.to_dict() for anchor in config.anchor_shotlists],
    }


def _root_node(
    tree_id: str,
    root_id: str,
    round_id: str,
    plans: list[dict],
    policy_version: str,
    script: ScriptSegment,
) -> TreeNode:
    """轮次锚点根节点（score=0.0 仅作结构起点；计划与输入摘要存档供幂等重建）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id="storyboard",
        policy_version=policy_version,
        prompt=(
            f"分镜探索轮次 {round_id}：剧本 {len(script.scenes)} 场景 / "
            f"{len(script.line_ids())} 行 / 必覆盖 {len(script.key_line_ids())} 条"
        ),
        observation_context={"round_id": round_id, "plans": plans},
        artifact_hash=PLACEHOLDER_HASH,
        eval_breakdown={},
        score=0.0,
        cost=CostRecord(),
        status=NodeStatus.EVALUATED,
        created_at=time.time(),
    )


def _round_spent(engine: Engine, round_id: str) -> float:
    with engine.connect() as conn:
        return float(
            conn.execute(
                select(func.sum(storyboard_render_jobs.c.actual_cost_usd)).where(
                    storyboard_render_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _append_board_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    job_id: str,
    policy_version: str,
    shotlist_dict: dict | None,
    shotlist_hash: str | None,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    reason: str | None,
) -> str:
    """分镜节点一次性完整 INSERT（落盘即冻结）；观测携带回放匹配键双键。"""
    observation = {"job_id": job_id}
    if shotlist_dict is not None:
        observation["shotlist"] = shotlist_dict
        # 回放匹配槽：002 规范化精确匹配固定读 observation_context["gen_params"]
        # （core/replay/matching.py GEN_PARAMS_KEY）——分镜侧同一内容落双键，
        # 换取回放/做梦/盲评全链路对 storyboard 零特判（原则五，007 教训复用）
        observation["gen_params"] = shotlist_dict
    if shotlist_hash is not None:
        observation["shotlist_hash"] = shotlist_hash
    if reason:
        observation["reject_reason"] = reason
    node = TreeNode(
        node_id=f"{job_id}-node",
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="storyboard",
        policy_version=policy_version,
        prompt="",
        observation_context=observation,
        artifact_hash=artifact_hash,
        eval_breakdown=breakdown,
        score=score,
        cost=cost,
        status=status,
        created_at=time.time(),
    )
    store.append_node(node)
    return node.node_id


def _run_job(
    index: int,
    shotlist: ShotList | None,
    normalize_error: str | None,
    shotlist_dict: dict,
    round_id: str,
    tree_id: str,
    root_id: str,
    *,
    policy_version: str,
    store,
    artifacts,
    adapter,
    engine,
    config,
    evaluators,
    script,
) -> dict:
    """单 ShotList 流水线：C1 校验 → 预算门禁 → 渲染 → 内容寻址 → 评估 → 落盘。"""
    job_id = _job_id(round_id, index)

    # 1) C1 执行前三层校验（违规 0 渲染 0 成本，FR-002）
    if shotlist is None:
        reason = normalize_error
    else:
        try:
            validate_shotlist(shotlist, script, config.shot_grammar)
            reason = None
        except ValidationError as exc:
            reason = f"ShotList 校验拒绝：{exc}"
    if reason is not None:
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=None,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {"job_id": job_id, "status": "rejected", "reason": reason, "shotlist_hash": None}

    shotlist_hash = shotlist.shotlist_hash()
    estimated = float(adapter.estimate(shotlist, config))

    # 2) 预算门禁前置校验（渲染申请前，≤ 语义含最小货币单位边界）
    spent_cents = round(_round_spent(engine, round_id) * 100)
    cap_cents = round(config.exploration_per_round_usd * 100)
    if spent_cents + round(estimated * 100) > cap_cents:
        reason = (
            f"预算门禁：已耗 ${spent_cents / 100:.2f} + 申请 ${estimated:.2f} "
            f"> 上限 ${cap_cents / 100:.2f}（拒绝）"
        )
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=shotlist_hash,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {
            "job_id": job_id,
            "status": "rejected",
            "reason": reason,
            "shotlist_hash": shotlist_hash,
        }

    # 3) 预演渲染（失败 job：预估成本照常入账 status=failed，轮次继续——原则二）
    try:
        animatic = adapter.render(shotlist, script, config)
    except RenderError as exc:
        _insert_job(
            engine,
            round_id=round_id,
            job_id=job_id,
            shotlist_json=shotlist.canonical_json(),
            shotlist_hash=shotlist_hash,
            status="failed",
            estimated=estimated,
            actual=estimated,  # 失败照计预估成本（C2 场景 4）
            artifact_hash=None,
            error=str(exc),
        )
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=shotlist_hash,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(generation_api_calls=1, generation_api_cost_usd=estimated),
            reason=f"渲染失败：{exc}",
        )
        return {
            "job_id": job_id,
            "status": "failed",
            "reason": f"渲染失败：{exc}",
            "shotlist_hash": shotlist_hash,
        }

    # 4) animatic 内容寻址
    artifact_hash = artifacts.put(animatic.mp4_bytes)

    # 5) 事务内复核+扣减落账（防并发双花）：实际扣费再次校验上限
    try:
        _insert_job_guarded(
            engine,
            round_id,
            job_id,
            shotlist.canonical_json(),
            shotlist_hash,
            animatic.actual_cost_usd,
            estimated,
            artifact_hash,
            config.exploration_per_round_usd,
        )
    except _BudgetExceeded as exc:
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=shotlist_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=animatic.actual_cost_usd,
            ),
            reason=str(exc),
        )
        return {
            "job_id": job_id,
            "status": "rejected",
            "reason": str(exc),
            "shotlist_hash": shotlist_hash,
        }

    # 6) 评估器打分（崩溃隔离：FAILED 成本入账轮次继续，004 SC-006 口径）
    artifact_ref = ArtifactRef(artifact_hash=artifact_hash, metadata=animatic.metadata)
    ctx = {
        "shotlist": shotlist,
        "shotlist_dict": shotlist_dict,
        "script": script,
        "metadata": animatic.metadata,
        "config": config,
    }
    try:
        if isinstance(evaluators, dict):
            # 真实五评估器编排（C9：gate 短路不跑 judge；judge 计费用量入节点成本）
            breakdown, score, judge_usage = evaluate_storyboard(
                evaluators, artifact_ref, ctx, config.evaluator_weights
            )
        else:
            # 评估器协议注入路径（US3 无偏性回放重算）：逐评估器打分 + 正式合成口径
            breakdown = {}
            for evaluator in evaluators:
                result = evaluator.evaluate(artifact_ref, ctx)
                breakdown[evaluator.spec.key] = {
                    "score": result.score,
                    "diagnostics": result.diagnostics,
                }
            score = quantize_score(composite_storyboard(breakdown, config.evaluator_weights))
            judge_usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=shotlist_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=score,
            breakdown=breakdown,
            cost=CostRecord(
                llm_calls=judge_usage["llm_calls"],
                llm_tokens=judge_usage["llm_tokens"],
                generation_api_calls=1,
                generation_api_cost_usd=animatic.actual_cost_usd + judge_usage["cost_usd"],
            ),
            reason=None,
        )
        _mark_inserted(engine, job_id)
        return {
            "job_id": job_id,
            "status": "inserted",
            "reason": "",
            "shotlist_hash": shotlist_hash,
        }
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离：FAILED 成本入账轮次继续
        _append_board_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            shotlist_dict=shotlist_dict,
            shotlist_hash=shotlist_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=animatic.actual_cost_usd,
            ),
            reason=f"评估器崩溃：{exc}",
        )
        _mark_inserted(engine, job_id)
        return {
            "job_id": job_id,
            "status": "failed",
            "reason": f"评估器崩溃：{exc}",
            "shotlist_hash": shotlist_hash,
        }


class _BudgetExceeded(Exception):
    """预算门禁内部信号（事务内复核失败）。"""


def _insert_job(
    engine,
    *,
    round_id: str,
    job_id: str,
    shotlist_json: str,
    shotlist_hash: str,
    status: str,
    estimated: float,
    actual: float,
    artifact_hash: str | None,
    error: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(storyboard_render_jobs).values(
                job_id=job_id,
                round_id=round_id,
                shotlist_json=shotlist_json,
                shotlist_hash=shotlist_hash,
                status=status,
                estimated_cost_usd=estimated,
                actual_cost_usd=actual,
                artifact_hash=artifact_hash,
                error=error,
                created_at=_now_iso(),
            )
        )


def _insert_job_guarded(
    engine,
    round_id: str,
    job_id: str,
    shotlist_json: str,
    shotlist_hash: str,
    actual: float,
    estimated: float,
    artifact_hash: str,
    cap_usd: float,
) -> None:
    """事务内复核 spent + 实际扣费 ≤ 上限 后落运营表（防并发双花）。"""
    with engine.begin() as conn:
        spent = float(
            conn.execute(
                select(func.sum(storyboard_render_jobs.c.actual_cost_usd)).where(
                    storyboard_render_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )
        spent_cents = round(spent * 100)
        charge_cents = round(actual * 100)
        if spent_cents + charge_cents > round(cap_usd * 100):
            raise _BudgetExceeded(
                f"预算门禁：已耗 ${spent:.2f} + 本次 ${actual:.2f} > 上限 ${cap_usd:.2f}（拒绝）"
            )
        conn.execute(
            insert(storyboard_render_jobs).values(
                job_id=job_id,
                round_id=round_id,
                shotlist_json=shotlist_json,
                shotlist_hash=shotlist_hash,
                status="rendered",
                estimated_cost_usd=estimated,
                actual_cost_usd=actual,
                artifact_hash=artifact_hash,
                error=None,
                created_at=_now_iso(),
            )
        )


def _mark_inserted(engine, job_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            storyboard_render_jobs.update()
            .where(storyboard_render_jobs.c.job_id == job_id)
            .values(status="inserted")
        )


def _reconcile(store, engine, tree_id: str, round_id: str, *, gateway_delta: float = 0.0) -> dict:
    """三方对账：树内成本合计 == 运营表扣减合计 + 网关增量（judge 计费侧，004 同口径）。"""
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    ledger_total = _round_spent(engine, round_id)
    return {
        "tree_total_usd": tree_total,
        "ledger_total_usd": ledger_total,
        "gateway_delta_usd": gateway_delta,
        "consistent": abs(tree_total - (ledger_total + gateway_delta)) < 1e-9,
    }


def _reconstruct(
    round_id: str, store: TreeStore, engine: Engine, config: StoryboardConfig
) -> StoryboardRoundResult:
    """幂等重建：二次触发撞唯一约束后，从树与运营表还原首轮 StoryboardRoundResult。"""
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    plans = root.observation_context.get("plans", [])
    nodes = store.nodes_of(tree_id)
    node_by_job = {n.observation_context.get("job_id"): n for n in nodes if n.parent_id is not None}
    with engine.connect() as conn:
        rows = {
            row.job_id: row
            for row in conn.execute(
                select(storyboard_render_jobs).where(storyboard_render_jobs.c.round_id == round_id)
            )
        }
    jobs = []
    for index, _plan_item in enumerate(plans):
        job_id = _job_id(round_id, index)
        node = node_by_job.get(job_id)
        shotlist_hash = None if node is None else node.observation_context.get("shotlist_hash")
        if job_id in rows and rows[job_id].status == "inserted":
            failed = node is not None and node.status is NodeStatus.FAILED
            status = "failed" if failed else "inserted"
            reason = "" if node is None else node.observation_context.get("reject_reason", "")
            jobs.append(
                {
                    "job_id": job_id,
                    "status": status,
                    "reason": reason,
                    "shotlist_hash": shotlist_hash,
                }
            )
        elif node is not None:
            status = "failed" if node.status is NodeStatus.FAILED else "rejected"
            jobs.append(
                {
                    "job_id": job_id,
                    "status": status,
                    "reason": node.observation_context.get("reject_reason", ""),
                    "shotlist_hash": shotlist_hash,
                }
            )
        elif job_id in rows:
            jobs.append(
                {
                    "job_id": job_id,
                    "status": rows[job_id].status,
                    "reason": rows[job_id].error or "",
                    "shotlist_hash": rows[job_id].shotlist_hash,
                }
            )
    return StoryboardRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=config.exploration_per_round_usd,
        cost_reconciliation={},
    )
