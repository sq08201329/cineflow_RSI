"""宣发线上探索执行器（contracts/promo-loop.md，US1 闭环本体）。

一轮探索：策略出物料简报 → 网关生成（计费）→ 合规门禁 → 预算门禁
（事务内校验+扣减，≤ 语义含最小货币单位边界）→ 适配器投放 → 轮次结果。
幂等：tree_id/root_id/material_id 均由 round_id 确定性派生，二次触发
撞唯一约束后直接重建首轮 RoundResult（0 重复投放、0 元重复扣费）。
落树两段式：拒投/失败节点轮次内直接落盘；投放成功的节点待指标回流后
由 `agents/promo/ingest.py` 一次性完整 INSERT（research 决策 1）。
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.promo.config import PromoConfig, PromoConfigError
from agents.promo.db import promo_campaigns
from agents.promo.evaluators.compliance import MaterialComplianceEvaluator
from agents.promo.evaluators.ctr import CtrHistoryEvaluator
from agents.promo.material import generate_material
from agents.promo.platform.base import (
    CampaignStatus,
    PlatformError,
    PromoMaterial,
    RateLimitedError,
    UnavailableError,
)
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

ADAPTER_MAX_RETRIES = 3  # 适配器瞬时错误退避上限（网关失败不再重试——双层重试禁止叠加）
STATUS_POLL_MAX = 5  # delivered 轮询上限


class PromoLoopError(Exception):
    """宣发闭环错误（冻结校验失败等）。"""


class PromoPolicy(Protocol):
    """宣发策略协议（做梦层接入前的手工策略形态）：产出物料生成简报列表。"""

    policy_version: str

    def plan_materials(self, config: PromoConfig) -> list[dict]: ...


@dataclass(frozen=True)
class RoundResult:
    """轮次结果（JSON 报告形态见契约 §3）。"""

    round_id: str
    tree_id: str
    policy_version: str
    materials: list[dict]  # [{"material_id","status","reason"}]
    spent_usd: float
    budget_cap_usd: float
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"promo-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _material_id(round_id: str, index: int) -> str:
    return f"{round_id}-m{index}"


def _cost_record(cost: dict, spent_usd: float = 0.0) -> CostRecord:
    """成本映射：网关折算 + 投放花费同入 generation_api_cost_usd（对账口径一致）。"""
    return CostRecord(
        llm_calls=cost.get("llm_calls", 0),
        llm_tokens=cost.get("llm_tokens", 0),
        generation_api_calls=1 if spent_usd > 0 else 0,
        generation_api_cost_usd=cost.get("gateway_usd", 0.0) + spent_usd,
        wall_clock_seconds=cost.get("wall_clock_seconds", 0.0),
    )


def _eval_fragment(evaluator, artifact_ref, context) -> dict:
    result = evaluator.evaluate(artifact_ref, context)
    return {"score": result.score, "diagnostics": result.diagnostics}


def run_round(
    round_id: str,
    policy: PromoPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    adapter,
    gateway: LLMGateway,
    config: PromoConfig,
    *,
    engine: Engine,
    ctr_history: list[dict] | None = None,
    sleep=time.sleep,
) -> RoundResult:
    """执行一轮线上探索（全流程幂等）。"""
    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    policy_version = getattr(policy, "policy_version", "unknown")
    cap = config.budget_cap_usd

    compliance = MaterialComplianceEvaluator(config)
    ctr = CtrHistoryEvaluator(
        # 只用已冻结的历史回流数据（FR-012）：默认从冻结树的 human 明细汇聚
        ctr_history if ctr_history is not None else collect_ctr_history(store),
        ctr_prior=config.ctr_prior,
        ctr_cap=config.ctr_cap,
    )

    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回 ----
    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id="promo",
        agent_id="promo",
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=_config_snapshot(config, compliance, ctr),
    )
    try:
        store.create_tree(tree)
        briefs = policy.plan_materials(config)
        store.append_node(_root_node(tree_id, root_id, round_id, briefs, policy_version))
    except DuplicateError:
        return _reconstruct(round_id, store, engine, config)

    gateway_before = gateway.total_cost_usd

    materials: list[dict] = []
    for index, brief in enumerate(briefs):
        materials.append(
            _run_material(
                index,
                brief,
                round_id,
                tree_id,
                root_id,
                store=store,
                artifacts=artifacts,
                adapter=adapter,
                gateway=gateway,
                config=config,
                engine=engine,
                compliance=compliance,
                ctr=ctr,
                sleep=sleep,
            )
        )

    spent = _round_spent(engine, round_id)
    result = RoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        materials=materials,
        spent_usd=spent,
        budget_cap_usd=cap,
        cost_reconciliation=_reconcile(
            store,
            engine,
            tree_id,
            round_id,
            gateway_delta=gateway.total_cost_usd - gateway_before,
        ),
    )
    return result


def _config_snapshot(config: PromoConfig, compliance, ctr) -> dict:
    """快照冻结：评估器版本组合 + 权重 + 物料规格/敏感词/先验随树冻结
    （宪章原则一：评估器版本组合冻结进树，回放/审计可复现）。"""
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {
            "rule.material_compliance": compliance.spec.version,
            "proxy.ctr_history": ctr.spec.version,
            "human.platform_metrics": "1.0.0",
        },
        "observation_fields": ["gen_params", "material_id"],
        "promo": {
            "material_spec": config.material_spec,
            "sensitive_words": config.sensitive_words,
            "ctr_prior": config.ctr_prior,
            "metric_weights": config.metric_weights,
        },
    }


def _root_node(
    tree_id: str, root_id: str, round_id: str, briefs: list[dict], policy_version: str
) -> TreeNode:
    """轮次锚点根节点（score=0.0 仅作结构起点；简报存于观测上下文供幂等重建）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id="promo",
        policy_version=policy_version,
        prompt=f"宣发探索轮次 {round_id}",
        observation_context={"round_id": round_id, "briefs": briefs},
        artifact_hash="ab" * 32,
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
                select(func.sum(promo_campaigns.c.spent_usd)).where(
                    promo_campaigns.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _append_child(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    material: PromoMaterial,
    gen_params: dict,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    reason: str | None,
) -> str:
    """拒投/失败节点轮次内直接落盘（投放成功节点由回流管道一次性落盘）。"""
    observation = {
        "gen_params": gen_params,
        "material_id": material.material_id,
        "material_kind": material.kind,
        "material_tags": material.tags,
        "material_platform": material.platform,
    }
    if reason:
        observation["reject_reason"] = reason
    node = TreeNode(
        node_id=f"{material.material_id}-node",
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="promo",
        policy_version="promo",
        prompt="",
        observation_context=observation,
        artifact_hash=material.artifact_hash,
        eval_breakdown=breakdown,
        score=score,
        cost=cost,
        status=status,
        created_at=time.time(),
    )
    store.append_node(node)
    return node.node_id


def _run_material(
    index: int,
    brief: dict,
    round_id: str,
    tree_id: str,
    root_id: str,
    *,
    store,
    artifacts,
    adapter,
    gateway,
    config,
    engine,
    compliance,
    ctr,
    sleep,
) -> dict:
    """单物料流水线：生成 → 合规门禁 → 预算门禁 → 投放。"""
    material_id = _material_id(round_id, index)
    gen_params = brief.get("gen_params", {})

    # 1) 生成（网关计费；网关失败不再重试——网关内部已退避 3 次）
    try:
        material, gen_cost = generate_material(
            brief, material_id, gateway, artifacts, model=config.default_model
        )
    except Exception as exc:
        placeholder = PromoMaterial(
            material_id=material_id,
            kind=brief.get("kind", "copy"),
            content={},
            artifact_hash="ab" * 32,
            platform=brief.get("platform", "simulated"),
            tags=list(brief.get("tags", [])),
        )
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=placeholder,
            gen_params=gen_params,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(),
            reason=f"生成失败：{exc}",
        )
        return {"material_id": material_id, "status": "failed", "reason": f"生成失败：{exc}"}

    from core.evaluators.base import ArtifactRef

    artifact_ref = ArtifactRef(artifact_hash=material.artifact_hash)
    ctx = {
        "material": {
            "content": material.content,
            "kind": material.kind,
            "platform": material.platform,
            "tags": material.tags,
        }
    }

    # 2) 合规门禁（缺配置拒投不放行；拦截成本照常入账）
    try:
        compliance_fragment = _eval_fragment(compliance, artifact_ref, ctx)
    except PromoConfigError as exc:
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=material,
            gen_params=gen_params,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=_cost_record(gen_cost),
            reason=f"缺配置拒投：{exc}",
        )
        return {"material_id": material_id, "status": "rejected", "reason": f"缺配置拒投：{exc}"}

    ctr_fragment = _eval_fragment(ctr, artifact_ref, ctx)
    breakdown = {
        f"{compliance.spec.key}": compliance_fragment,
        f"{ctr.spec.key}": ctr_fragment,
    }

    if compliance_fragment["score"] == 0.0:
        reason = "合规门禁拦截：" + "; ".join(compliance_fragment["diagnostics"]["violations"])
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=material,
            gen_params=gen_params,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown=breakdown,
            cost=_cost_record(gen_cost),
            reason=reason,
        )
        return {"material_id": material_id, "status": "rejected", "reason": reason}

    # 3) 预算门禁前置校验（投放申请前，≤ 语义含最小货币单位边界）
    requested = float(brief["budget_usd"])
    spent_cents = round(_round_spent(engine, round_id) * 100)
    cap_cents = round(config.budget_cap_usd * 100)
    if spent_cents + round(requested * 100) > cap_cents:
        reason = (
            f"预算门禁：已耗 ${spent_cents / 100:.2f} + 申请 ${requested:.2f} "
            f"> 上限 ${cap_cents / 100:.2f}（拒投）"
        )
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=material,
            gen_params=gen_params,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown=breakdown,
            cost=_cost_record(gen_cost),
            reason=reason,
        )
        return {"material_id": material_id, "status": "rejected", "reason": reason}

    # 4) 投放（适配器瞬时错误退避重试，上限 3 次；其余错误直接失败）
    try:
        campaign = _create_campaign_with_retry(adapter, material, requested, round_id, sleep)
    except PlatformError as exc:
        _record_campaign(
            engine,
            round_id=round_id,
            material_id=material_id,
            status="failed",
            spent_usd=0.0,
            external_id=None,
            metrics={"cost": {**gen_cost, "total_usd": gen_cost["gateway_usd"]}},
        )
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=material,
            gen_params=gen_params,
            status=NodeStatus.FAILED,
            score=None,
            breakdown=breakdown,
            cost=_cost_record(gen_cost),
            reason=f"投放失败：{exc}",
        )
        return {"material_id": material_id, "status": "failed", "reason": f"投放失败：{exc}"}

    # 5) 事务内复核+扣减落账（防并发双花）：实际扣费再次校验上限
    try:
        _insert_campaign_guarded(
            engine,
            round_id,
            material,
            campaign,
            config.budget_cap_usd,
            gen_cost,
            gen_params,
            breakdown,
        )
    except _BudgetExceeded as exc:
        adapter.pause(campaign.external_id)  # 拒投即暂停，避免继续消耗
        _append_child(
            store,
            tree_id=tree_id,
            root_id=root_id,
            material=material,
            gen_params=gen_params,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown=breakdown,
            cost=_cost_record(gen_cost),
            reason=str(exc),
        )
        return {"material_id": material_id, "status": "rejected", "reason": str(exc)}

    # 4) 推进至 delivered（指标回流由 agents/promo/ingest.py 完成一次性落盘）
    status = campaign.status
    for _ in range(STATUS_POLL_MAX):
        status = adapter.get_status(campaign.external_id)
        if status is CampaignStatus.DELIVERED:
            break
    if status is not CampaignStatus.DELIVERED:
        return {"material_id": material_id, "status": "failed", "reason": "投放未送达"}
    return {"material_id": material_id, "status": "delivered", "reason": ""}


class _BudgetExceeded(Exception):
    """预算门禁内部信号（事务内校验失败）。"""


def _create_campaign_with_retry(adapter, material, budget_usd, round_id, sleep):
    """适配器瞬时错误（限流/不可用）指数退避重试，上限 3 次；其余错误直接失败。"""
    key = f"{round_id}:{material.material_id}"
    last: Exception | None = None
    for attempt in range(ADAPTER_MAX_RETRIES + 1):
        try:
            return adapter.create_campaign(material, budget_usd, idempotency_key=key)
        except (RateLimitedError, UnavailableError) as exc:
            last = exc
            if attempt < ADAPTER_MAX_RETRIES:
                sleep(0.5 * (2**attempt))
    raise last  # type: ignore[misc]


def _insert_campaign_guarded(
    engine,
    round_id,
    material: PromoMaterial,
    campaign,
    cap_usd: float,
    gen_cost: dict,
    gen_params: dict,
    breakdown: dict,
) -> None:
    """事务内校验 spent + 实际扣费 ≤ 上限 后落运营表（防并发双花；≤ 语义）。"""
    with engine.begin() as conn:
        spent = float(
            conn.execute(
                select(func.sum(promo_campaigns.c.spent_usd)).where(
                    promo_campaigns.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )
        # 最小货币单位边界：以分为单位比较，浮点误差不得放行超支
        spent_cents = round(spent * 100)
        charge_cents = round(campaign.spent_usd * 100)
        cap_cents = round(cap_usd * 100)
        if spent_cents + charge_cents > cap_cents:
            raise _BudgetExceeded(
                f"预算门禁：已耗 ${spent:.2f} + 本次 ${campaign.spent_usd:.2f} "
                f"> 上限 ${cap_usd:.2f}（拒投）"
            )
        now = time.time()
        conn.execute(
            insert(promo_campaigns).values(
                campaign_id=campaign.campaign_id,
                round_id=round_id,
                material_id=material.material_id,
                node_id=None,
                status="delivered",
                spent_usd=campaign.spent_usd,
                external_id=campaign.external_id,
                metrics={
                    "gen_params": gen_params,
                    "material": {
                        "content": material.content,
                        "kind": material.kind,
                        "platform": material.platform,
                        "tags": material.tags,
                        "artifact_hash": material.artifact_hash,
                    },
                    "cost": {
                        **gen_cost,
                        "spent_usd": campaign.spent_usd,
                        "total_usd": gen_cost["gateway_usd"] + campaign.spent_usd,
                    },
                    "eval_fragments": breakdown,
                },
                created_at=now,
                updated_at=now,
            )
        )


def _record_campaign(engine, *, round_id, material_id, status, spent_usd, external_id, metrics):
    now = time.time()
    with engine.begin() as conn:
        conn.execute(
            insert(promo_campaigns).values(
                campaign_id=f"{round_id}-{material_id}-failed",
                round_id=round_id,
                material_id=material_id,
                node_id=None,
                status=status,
                spent_usd=spent_usd,
                external_id=external_id,
                metrics=metrics,
                created_at=now,
                updated_at=now,
            )
        )


def _reconcile(store, engine, tree_id, round_id, *, gateway_delta: float) -> dict:
    """三方对账（SC-003）：树内合计 + 待回流运营表成本 == 网关 + 适配器账目。"""
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                promo_campaigns.c.spent_usd,
                promo_campaigns.c.metrics,
                promo_campaigns.c.status,
            ).where(promo_campaigns.c.round_id == round_id)
        ).all()
    platform_total = sum(row.spent_usd for row in rows)
    # 待回流：仅 delivered 运营行记录的总成本（生成+投放）尚未进树；
    # failed 行的成本已随 FAILED 节点落树，不得重复计入
    pending = sum(
        (row.metrics or {}).get("cost", {}).get("total_usd", 0.0)
        for row in rows
        if row.status == "delivered" and (row.metrics or {}).get("cost")
    )
    ledger_total = gateway_delta + platform_total
    consistent = abs(tree_total + pending - ledger_total) < 1e-9
    return {
        "tree_total_usd": tree_total,
        "pending_campaigns_usd": pending,
        "ledger_total_usd": ledger_total,
        "consistent": consistent,
    }


def _reconstruct(
    round_id: str, store: TreeStore, engine: Engine, config: PromoConfig
) -> RoundResult:
    """幂等重建：二次触发撞唯一约束后，从树与运营表还原首轮 RoundResult。"""
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    briefs = root.observation_context.get("briefs", [])
    nodes = store.nodes_of(tree_id)
    node_by_material = {
        n.observation_context.get("material_id"): n for n in nodes if n.parent_id is not None
    }
    with engine.connect() as conn:
        rows = {
            row.material_id: row
            for row in conn.execute(
                select(promo_campaigns).where(promo_campaigns.c.round_id == round_id)
            )
        }
    materials = []
    for index, _ in enumerate(briefs):
        material_id = _material_id(round_id, index)
        if material_id in rows and rows[material_id].status in ("delivered", "ingested"):
            materials.append({"material_id": material_id, "status": "delivered", "reason": ""})
        elif material_id in node_by_material:
            node = node_by_material[material_id]
            status = "failed" if node.status is NodeStatus.FAILED else "rejected"
            materials.append(
                {
                    "material_id": material_id,
                    "status": status,
                    "reason": node.observation_context.get("reject_reason", ""),
                }
            )
        elif material_id in rows:
            materials.append(
                {"material_id": material_id, "status": rows[material_id].status, "reason": ""}
            )
    return RoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        materials=materials,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=config.budget_cap_usd,
        cost_reconciliation={},
    )


def collect_ctr_history(store: TreeStore, agent_id: str = "promo") -> list[dict]:
    """从已冻结树的 human.platform_metrics 明细汇聚 CTR 历史（FR-012）。

    只读已落盘（即已冻结）节点；返回 CTR 评估器的历史记录形态
    [{platform, kind, tags, impressions, clicks}]。
    """
    records: list[dict] = []
    for tree in store.trees_by(agent_id=agent_id):
        for node in store.nodes_of(tree.tree_id):
            if node.status is not NodeStatus.EVALUATED:
                continue
            fragment = next(
                (
                    v
                    for k, v in node.eval_breakdown.items()
                    if k.startswith("human.platform_metrics@")
                ),
                None,
            )
            if fragment is None:
                continue
            diagnostics = fragment["diagnostics"]
            ctx = node.observation_context
            records.append(
                {
                    "platform": ctx.get("material_platform", ""),
                    "kind": ctx.get("material_kind", ""),
                    "tags": list(ctx.get("material_tags", [])),
                    "impressions": int(diagnostics["impressions"]),
                    "clicks": int(diagnostics["clicks"]),
                }
            )
    return records


def freeze_round_tree(round_id: str, store: TreeStore, engine: Engine) -> DiscoveryTree:
    """轮次树显式冻结入口（契约边界：调用方在轮次完成后触发，非自行冻结）。

    校验：全部运营记录已到终态（ingested/failed）——delivered 未回流
    不得冻结入池；通过后返回树对象供 002 SimulatorPool 入池。
    """
    tree_id = round_tree_id(round_id)
    store.get_node(_round_root_id(round_id))  # 不存在 → NotFoundError
    with engine.connect() as conn:
        pending = conn.execute(
            select(func.count())
            .select_from(promo_campaigns)
            .where(
                promo_campaigns.c.round_id == round_id,
                promo_campaigns.c.status.in_(["created", "delivering", "delivered"]),
            )
        ).scalar()
    if pending:
        raise PromoLoopError(f"轮次 {round_id} 尚有 {pending} 条投放未回流完成，不得冻结入池")
    trees = store.trees_by(project_id="promo", agent_id="promo")
    matches = [t for t in trees if t.tree_id == tree_id]
    if not matches:
        raise PromoLoopError(f"轮次树不存在：{tree_id}")
    return matches[0]
