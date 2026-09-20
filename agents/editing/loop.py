"""剪辑线上探索执行器（contracts/editing-loop.md C1~C3，US1 闭环本体）。

一轮探索：策略产 EDL 组合 → C1 四层执行前校验（违规 0 渲染 0 成本）→
预算门禁（申请前校验 + 事务内复核，≤ 语义含最小货币单位）→ 渲染
（昂贵动作仅此阶段，原则三）→ 成片内容寻址 → 评估器协议打分
（US1 桩注入，US2 接线真实五评估器）→ 合成得分（quantize 定点归一）→
节点一次性 INSERT 冻结。失败 job 成本照计入账 status=failed（原则二）。
幂等：tree_id/job_id 由 round_id 确定性派生 + 唯一键 (round_id, edl_hash)，
二次触发重建首轮 EditingRoundResult（0 重复渲染、0 重复扣费）。
素材可行性预检：镜头数不足最小可行数 → 执行前拒绝；素材总长 < 目标时长
下限 → FAILED 节点如实落盘注明（规格边界情况）。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.editing.config import EditingConfig
from agents.editing.db import edit_render_jobs
from agents.editing.edl import EditDecisionList, validate_edl
from agents.editing.evaluators import build_editing_evaluators
from agents.editing.evaluators.composite import (
    COMPOSITE_POLICY,
    composite_editing,
    evaluate_editing,
)
from agents.editing.platform.base import EditRenderAdapter, RenderError
from agents.editing.shots import SceneStructure, ShotLibrary
from core.evaluators.base import ArtifactRef, Evaluator
from core.evaluators.quantize import quantize_score
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError, ValidationError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

PLACEHOLDER_HASH = "00" * 32  # 未产出工件的拒绝节点占位哈希（无工件可引）


class EditingLoopError(Exception):
    """剪辑闭环错误（素材预检/轮次收口校验失败等）。"""


class EditingPolicy(Protocol):
    """剪辑策略协议（做梦层接入前的手工策略形态）：产出 EDL 组合。"""

    policy_version: str

    def plan(self, config: EditingConfig, inputs: dict) -> list: ...


@dataclass(frozen=True)
class EditingRoundResult:
    """轮次收口：jobs 汇总 + 成本对账 + 预检结论（C2）。"""

    round_id: str
    tree_id: str
    policy_version: str
    jobs: list[dict]  # [{"job_id","status","reason","edl_hash"}]
    spent_usd: float
    budget_cap_usd: float
    precheck: str = "ok"  # 素材预检不通过时注明原因（FAILED 节点同文落盘）
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"editing-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _job_id(round_id: str, index: int) -> str:
    return f"{round_id}-j{index}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_inputs(inputs: dict) -> tuple[ShotLibrary, SceneStructure]:
    """输入校验先于一切副作用（C1：镜头库与分区结构类型不对即拒绝）。"""
    library = inputs.get("shot_library")
    structure = inputs.get("scene_structure")
    if not isinstance(library, ShotLibrary):
        raise EditingLoopError("inputs 缺少 shot_library（ShotLibrary）")
    if not isinstance(structure, SceneStructure):
        raise EditingLoopError("inputs 缺少 scene_structure（SceneStructure）")
    return library, structure


def _material_precheck(library: ShotLibrary, config: EditingConfig) -> str | None:
    """素材可行性预检（C2 边界情况）。

    返回 None = 可行；否则返回注明原因：
    - 镜头数不足最小可行数（下限时长 / 单镜头上限 向上取整）→ 执行前拒绝；
    - 素材总长 < 目标时长下限 → FAILED 节点落盘注明（调用方处理）。
    """
    lower_bound_ms = int((config.target_duration_s - config.duration_tolerance_s) * 1000)
    max_shot_ms = int(config.shot_limits["max_shot_ms"])
    min_shot_count = -(-lower_bound_ms // max_shot_ms)  # 向上取整
    if len(library.shots) < min_shot_count:
        raise EditingLoopError(
            f"素材不足：镜头数 {len(library.shots)} < 最小可行数 {min_shot_count}"
            f"（目标下限 {lower_bound_ms}ms / max_shot_ms {max_shot_ms}）"
        )
    material_total_ms = sum(s.duration_ms for s in library.shots)
    if material_total_ms < lower_bound_ms:
        return (
            f"素材总长不足：素材 {material_total_ms}ms < 目标下限 {lower_bound_ms}ms"
            f"（target {config.target_duration_s}s − tolerance {config.duration_tolerance_s}s）"
        )
    return None


def run_editing_round(
    round_id: str,
    policy: EditingPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    adapter: EditRenderAdapter,
    engine: Engine,
    config: EditingConfig,
    inputs: dict,
    evaluators: list[Evaluator] | dict | None = None,
    gateway: LLMGateway | None = None,
) -> EditingRoundResult:
    """执行一轮剪辑线上探索（全流程幂等）。

    evaluators：None → 默认装配真实五评估器（build_editing_evaluators，需 gateway）；
    dict（{"gates","pacing","judge","all"}）→ 真实编排（gate 短路不跑 judge）；
    list → 评估器桩注入路径（测试/无偏性回放，面向评估器协议编程）。
    对账三方：树内成本 == 运营表扣减 + 网关增量（judge 计费，004 同口径）。
    """
    # 0) 输入与素材预检先于一切副作用（适配器 0 调用、0 成本、0 落库）
    library, structure = _validate_inputs(inputs)
    gateway_before = float(gateway.total_cost_usd) if gateway is not None else 0.0
    # 评估器装配：缺省按 evaluator_weights.editing 装配真实五评估器（T727 接线）；
    # 显式注入用于测试桩/无偏性回放（US1 取舍：面向评估器协议编程）
    if evaluators is None:
        if gateway is None:
            raise EditingLoopError("装配真实五评估器必须提供 LLM 网关（judge 计费路径）")
        evaluators = build_editing_evaluators(config, gateway)

    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    policy_version = getattr(policy, "policy_version", "unknown")
    cap = config.exploration_per_round_usd

    # 策略产 EDL 组合：规范化失败（形状非法）同样按"执行前拒绝"处理
    plans: list[dict] = []
    normalized: list[tuple[EditDecisionList | None, str | None]] = []
    for item in policy.plan(config, inputs):
        try:
            edl = item if isinstance(item, EditDecisionList) else EditDecisionList.from_dict(item)
        except ValidationError as exc:
            edl, error = None, f"EDL 形状非法：{exc}"
            plans.append(item if isinstance(item, dict) else {"raw": str(item)})
        else:
            error = None
            plans.append(edl.to_dict())
        normalized.append((edl, error))

    # 素材预检：镜头数不足直接拒绝（0 副作用）；总长不足走 FAILED 落盘路径
    precheck_reason = _material_precheck(library, config)

    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id="editing",
        agent_id="editing",
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=_config_snapshot(config, evaluators),
    )
    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回 ----
    try:
        store.create_tree(tree)
        store.append_node(_root_node(tree_id, root_id, round_id, plans, policy_version, library))
    except DuplicateError:
        return _reconstruct(round_id, store, engine, config)

    if precheck_reason is not None:
        _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=_job_id(round_id, 0),
            policy_version=policy_version,
            edl_dict=None,
            edl_hash=None,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(),
            reason=precheck_reason,
        )
        return EditingRoundResult(
            round_id=round_id,
            tree_id=tree_id,
            policy_version=policy_version,
            jobs=[],
            spent_usd=0.0,
            budget_cap_usd=cap,
            precheck=precheck_reason,
            cost_reconciliation=_reconcile(
                store,
                engine,
                tree_id,
                round_id,
                gateway_delta=_gateway_delta(gateway, gateway_before),
            ),
        )

    jobs: list[dict] = []
    for index, (edl, error) in enumerate(normalized):
        jobs.append(
            _run_job(
                index,
                edl,
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
                library=library,
                structure=structure,
            )
        )

    return EditingRoundResult(
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
    """轮次树显式冻结入口（池接线：渲染任务全终态才允许冻结入池，004/006 同构）。

    config_snapshot 在树创建时已写全——树 immutable 不允许后补，
    此函数只做终态校验 + 返回树对象。
    """
    tree_id = round_tree_id(round_id)
    try:
        store.get_node(_round_root_id(round_id))
    except Exception as exc:
        raise EditingLoopError(f"轮次树不存在：{tree_id}（round_id={round_id}）") from exc
    with engine.connect() as conn:
        pending = conn.execute(
            select(func.count())
            .select_from(edit_render_jobs)
            .where(
                edit_render_jobs.c.round_id == round_id,
                edit_render_jobs.c.status.in_(["pending", "rendered", "evaluated"]),
            )
        ).scalar()
    if pending:
        raise EditingLoopError(f"轮次 {round_id} 尚有 {pending} 个渲染任务未到终态，不得冻结入池")
    trees = store.trees_by(project_id="editing", agent_id="editing")
    matches = [t for t in trees if t.tree_id == tree_id]
    if not matches:
        raise EditingLoopError(f"轮次树不存在：{tree_id}")
    return matches[0]


def _config_snapshot(config: EditingConfig, evaluators: list[Evaluator] | dict) -> dict:
    """快照冻结：权重 + 评估器版本组合 + 观测白名单 + 剪辑口径配置。"""
    all_evaluators = evaluators["all"] if isinstance(evaluators, dict) else evaluators
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {e.spec.evaluator_id: e.spec.version for e in all_evaluators},
        "observation_fields": ["gen_params", "edl", "edl_hash", "job_id"],
        "composite_policy": COMPOSITE_POLICY,  # 合成归一口径进版本元信息（C9）
        "transition_rules": config.transition_rules,
        "shot_limits": config.shot_limits,
        "target_duration_s": config.target_duration_s,
        "duration_tolerance_s": config.duration_tolerance_s,
        "render": config.render,
    }


def _root_node(tree_id, root_id, round_id, plans, policy_version, library) -> TreeNode:
    """轮次锚点根节点（score=0.0 仅作结构起点；计划与输入摘要存档供幂等重建）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id="editing",
        policy_version=policy_version,
        prompt=(
            f"剪辑探索轮次 {round_id}：镜头库 {len(library.shots)} 镜头 / "
            f"音轨 {len(library.audio_tracks)} 条"
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
                select(func.sum(edit_render_jobs.c.actual_cost_usd)).where(
                    edit_render_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _append_edl_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    job_id: str,
    policy_version: str,
    edl_dict: dict | None,
    edl_hash: str | None,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    reason: str | None,
) -> str:
    """剪辑节点一次性完整 INSERT（落盘即冻结）；观测携带回放匹配键。"""
    observation = {"job_id": job_id}
    if edl_dict is not None:
        observation["edl"] = edl_dict
        # 回放匹配槽：002 规范化精确匹配固定读 observation_context["gen_params"]
        # （core/replay/matching.py GEN_PARAMS_KEY）——剪辑侧同一内容落双键，
        # 换取回放/做梦/盲评全链路对 editing 零特判（原则五，006 同款取舍）
        observation["gen_params"] = edl_dict
    if edl_hash is not None:
        observation["edl_hash"] = edl_hash
    if reason:
        observation["reject_reason"] = reason
    node = TreeNode(
        node_id=f"{job_id}-node",
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="editing",
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
    edl: EditDecisionList | None,
    normalize_error: str | None,
    edl_dict: dict,
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
    library,
    structure,
) -> dict:
    """单 EDL 流水线：C1 校验 → 预算门禁 → 渲染 → 内容寻址 → 评估 → 落盘。"""
    job_id = _job_id(round_id, index)

    # 1) C1 执行前四层校验（违规 0 渲染 0 成本，FR-002）
    if edl is None:
        reason = normalize_error
    else:
        try:
            validate_edl(edl, library, structure, config.transition_rules)
            reason = None
        except ValidationError as exc:
            reason = f"EDL 校验拒绝：{exc}"
    if reason is not None:
        _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=None,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {"job_id": job_id, "status": "rejected", "reason": reason, "edl_hash": None}

    edl_hash = edl.edl_hash()
    estimated = float(adapter.estimate(edl, library))

    # 2) 预算门禁前置校验（渲染申请前，≤ 语义含最小货币单位边界）
    spent_cents = round(_round_spent(engine, round_id) * 100)
    cap_cents = round(config.exploration_per_round_usd * 100)
    if spent_cents + round(estimated * 100) > cap_cents:
        reason = (
            f"预算门禁：已耗 ${spent_cents / 100:.2f} + 申请 ${estimated:.2f} "
            f"> 上限 ${cap_cents / 100:.2f}（拒绝）"
        )
        _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {"job_id": job_id, "status": "rejected", "reason": reason, "edl_hash": edl_hash}

    # 3) 渲染（失败 job：预估成本照常入账 status=failed，轮次继续——原则二）
    try:
        film = adapter.render(edl, library)
    except RenderError as exc:
        _insert_job(
            engine,
            round_id=round_id,
            job_id=job_id,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
            status="failed",
            estimated=estimated,
            actual=estimated,  # 失败照计预估成本（C2 场景 4）
            artifact_hash=None,
            error=str(exc),
        )
        _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
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
            "edl_hash": edl_hash,
        }

    # 4) 成片内容寻址
    artifact_hash = artifacts.put(film.mp4_bytes)

    # 5) 事务内复核+扣减落账（防并发双花）：实际扣费再次校验上限
    try:
        _insert_job_guarded(
            engine,
            round_id,
            job_id,
            edl_dict,
            edl_hash,
            film.actual_cost_usd,
            estimated,
            artifact_hash,
            config.exploration_per_round_usd,
        )
    except _BudgetExceeded as exc:
        _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=film.actual_cost_usd,
            ),
            reason=str(exc),
        )
        return {"job_id": job_id, "status": "rejected", "reason": str(exc), "edl_hash": edl_hash}

    # 6) 评估器协议打分（崩溃隔离：FAILED 成本入账轮次继续，同 004 SC-006）
    artifact_ref = ArtifactRef(artifact_hash=artifact_hash, metadata=film.metadata)
    ctx = {
        "edl": edl,
        "edl_dict": edl_dict,
        "metadata": film.metadata,
        "shot_library": library,
        "scene_structure": structure,
        "config": config,
    }
    try:
        if isinstance(evaluators, dict):
            # 真实五评估器编排（C9：gate 短路不跑 judge；judge 计费用量入节点成本）
            breakdown, score, judge_usage = evaluate_editing(
                evaluators, artifact_ref, ctx, config.evaluator_weights
            )
        else:
            # 评估器桩注入路径（US1 测试/无偏性回放）：逐评估器打分 + 正式合成
            breakdown = {}
            for evaluator in evaluators:
                result = evaluator.evaluate(artifact_ref, ctx)
                breakdown[evaluator.spec.key] = {
                    "score": result.score,
                    "diagnostics": result.diagnostics,
                }
            score = quantize_score(composite_editing(breakdown, config.evaluator_weights))
            judge_usage = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        node_id = _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=score,
            breakdown=breakdown,
            cost=CostRecord(
                llm_calls=judge_usage["llm_calls"],
                llm_tokens=judge_usage["llm_tokens"],
                generation_api_calls=1,
                generation_api_cost_usd=film.actual_cost_usd + judge_usage["cost_usd"],
            ),
            reason=None,
        )
        _mark_inserted(engine, job_id, node_id)
        return {"job_id": job_id, "status": "inserted", "reason": "", "edl_hash": edl_hash}
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离：FAILED 成本入账轮次继续
        node_id = _append_edl_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            edl_dict=edl_dict,
            edl_hash=edl_hash,
            artifact_hash=artifact_hash,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=film.actual_cost_usd,
            ),
            reason=f"评估器崩溃：{exc}",
        )
        _mark_inserted(engine, job_id, node_id)
        return {
            "job_id": job_id,
            "status": "failed",
            "reason": f"评估器崩溃：{exc}",
            "edl_hash": edl_hash,
        }


class _BudgetExceeded(Exception):
    """预算门禁内部信号（事务内复核失败）。"""


def _insert_job(
    engine,
    *,
    round_id: str,
    job_id: str,
    edl_dict: dict,
    edl_hash: str,
    status: str,
    estimated: float,
    actual: float,
    artifact_hash: str | None,
    error: str | None,
) -> None:

    with engine.begin() as conn:
        conn.execute(
            insert(edit_render_jobs).values(
                job_id=job_id,
                round_id=round_id,
                edl_json=json.dumps(edl_dict, sort_keys=True, ensure_ascii=False),
                edl_hash=edl_hash,
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
    edl_dict: dict,
    edl_hash: str,
    actual: float,
    estimated: float,
    artifact_hash: str,
    cap_usd: float,
) -> None:
    """事务内复核 spent + 实际扣费 ≤ 上限 后落运营表（防并发双花）。"""

    with engine.begin() as conn:
        spent = float(
            conn.execute(
                select(func.sum(edit_render_jobs.c.actual_cost_usd)).where(
                    edit_render_jobs.c.round_id == round_id
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
            insert(edit_render_jobs).values(
                job_id=job_id,
                round_id=round_id,
                edl_json=json.dumps(edl_dict, sort_keys=True, ensure_ascii=False),
                edl_hash=edl_hash,
                status="rendered",
                estimated_cost_usd=estimated,
                actual_cost_usd=actual,
                artifact_hash=artifact_hash,
                error=None,
                created_at=_now_iso(),
            )
        )


def _mark_inserted(engine, job_id: str, node_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            edit_render_jobs.update()
            .where(edit_render_jobs.c.job_id == job_id)
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
    round_id: str, store: TreeStore, engine: Engine, config: EditingConfig
) -> EditingRoundResult:
    """幂等重建：二次触发撞唯一约束后，从树与运营表还原首轮 EditingRoundResult。"""
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    plans = root.observation_context.get("plans", [])
    nodes = store.nodes_of(tree_id)
    node_by_job = {n.observation_context.get("job_id"): n for n in nodes if n.parent_id is not None}
    with engine.connect() as conn:
        rows = {
            row.job_id: row
            for row in conn.execute(
                select(edit_render_jobs).where(edit_render_jobs.c.round_id == round_id)
            )
        }
    jobs = []
    for index, _plan_item in enumerate(plans):
        job_id = _job_id(round_id, index)
        node = node_by_job.get(job_id)
        edl_hash = None if node is None else node.observation_context.get("edl_hash")
        if job_id in rows and rows[job_id].status == "inserted":
            status = (
                "failed" if node is not None and node.status is NodeStatus.FAILED else "inserted"
            )
            reason = "" if node is None else node.observation_context.get("reject_reason", "")
            jobs.append(
                {"job_id": job_id, "status": status, "reason": reason, "edl_hash": edl_hash}
            )
        elif node is not None:
            status = "failed" if node.status is NodeStatus.FAILED else "rejected"
            jobs.append(
                {
                    "job_id": job_id,
                    "status": status,
                    "reason": node.observation_context.get("reject_reason", ""),
                    "edl_hash": edl_hash,
                }
            )
        elif job_id in rows:
            jobs.append(
                {
                    "job_id": job_id,
                    "status": rows[job_id].status,
                    "reason": rows[job_id].error or "",
                    "edl_hash": rows[job_id].edl_hash,
                }
            )
    return EditingRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=config.exploration_per_round_usd,
        cost_reconciliation={},
    )
