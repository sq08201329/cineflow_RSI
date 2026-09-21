"""视觉线上探索执行器（contracts/visual-loop.md，US1 闭环本体）。

一轮探索：策略产出 gen_params 组合 → 预算门禁（申请前校验 + 事务内复核，
≤ 语义含最小货币单位边界）→ 适配器生成（昂贵动作仅此阶段，原则三）→
工件内容寻址 → ffprobe 探测 → 五评估器（合规 0 分短路不跑 judge，
省 LLM 成本；单评估器崩溃该片段 FAILED 轮次继续）→ 合成得分（quantize
定点归一）→ 一次性完整节点 INSERT 冻结。
幂等：tree_id/clip_id 由 round_id 确定性派生 + 运营表唯一键
(round_id, params_hash)，二次触发重建首轮结果（0 重复生成、0 重复扣费）。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

import blake3
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.visual.clip import produce_clip
from agents.visual.config import VisualConfig
from agents.visual.db import visual_gen_jobs
from agents.visual.evaluators.aesthetic import AestheticEvaluator
from agents.visual.evaluators.cinematic import CinematicJudgeEvaluator
from agents.visual.evaluators.flicker import FlickerEvaluator
from agents.visual.evaluators.format_compliance import FormatComplianceEvaluator
from agents.visual.evaluators.identity import IdentityConsistencyEvaluator
from agents.visual.frames import sample_frames
from agents.visual.platform.base import (
    VideoGenError,
)
from agents.visual.platform.simulated import encode_mp4, render_frames
from core.calibration.drift_gate import DriftGate, apply_gate
from core.evaluators.base import ArtifactRef, EvalResult
from core.evaluators.composite import composite_score_versioned
from core.evaluators.quantize import quantize_score
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

ADAPTER_MAX_RETRIES = 3  # 适配器瞬时错误退避上限（网关失败不再重试）
PLACEHOLDER_HASH = "00" * 32  # 未产出工件的拒投节点占位哈希（无工件可引）


class VisualLoopError(Exception):
    """视觉闭环错误（冻结校验失败等，US2 接线）。"""


class VisualPolicy(Protocol):
    """视觉策略协议（做梦层接入前的手工策略形态）：产出 gen_params 组合。"""

    policy_version: str

    def plan_clips(self, config: VisualConfig) -> list[dict]: ...


@dataclass(frozen=True)
class RoundResult:
    """轮次结果（JSON 报告形态见契约 §3）。"""

    round_id: str
    tree_id: str
    policy_version: str
    clips: list[dict]  # [{"clip_id","status","reason"}]
    spent_usd: float
    budget_cap_usd: float
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"visual-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _clip_id(round_id: str, index: int) -> str:
    return f"{round_id}-c{index}"


def _params_hash(gen_params: dict) -> str:
    canonical = json.dumps(gen_params, sort_keys=True, ensure_ascii=False)
    return blake3.blake3(canonical.encode()).hexdigest()


def _judge_anchor_hashes(config: VisualConfig, artifacts: ArtifactStore) -> list[str]:
    """锚点集（决策 5）：configs 固定生成参数集经确定性模拟生成器产出锚点工件。

    参数哈希与工件哈希双双进入 judge 版本号；真实环境切换为固定素材的
    工件哈希清单，代码路径不变。
    """
    hashes = []
    for anchor_params in config.judge["anchor_gen_params"]:
        frames = render_frames(anchor_params, config.simulated_gen)
        hashes.append(artifacts.put(encode_mp4(frames, fps=config.clip_spec["fps"])))
    return hashes


def run_round(
    round_id: str,
    policy: VisualPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    adapter,
    gateway: LLMGateway,
    engine: Engine,
    config: VisualConfig,
    drift_gate: DriftGate | None = None,
) -> RoundResult:
    """执行一轮视觉线上探索（全流程幂等）。

    drift_gate：漂移合成门禁（功能 012）——合成前按 judge 漂移状态降权/排除
    （None = 未接线，权重原样）；权重变化 → composite 版本哈希变化 → 自然升版。
    """
    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    policy_version = getattr(policy, "policy_version", "unknown")
    cap = config.exploration_per_round_usd

    # 评估器装配（版本组合随树快照冻结）
    evaluators = build_evaluators(config, gateway, artifacts)
    compliance = evaluators["compliance"]
    proxies = evaluators["proxies"]
    judge = evaluators["judge"]

    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回 ----
    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id="visual",
        agent_id="visual",
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=_config_snapshot(config, compliance, proxies, judge),
    )
    try:
        store.create_tree(tree)
        clips_briefs = policy.plan_clips(config)
        store.append_node(_root_node(tree_id, root_id, round_id, clips_briefs, policy_version))
    except DuplicateError:
        return _reconstruct(round_id, store, engine, config)

    gateway_before = gateway.total_cost_usd
    clips: list[dict] = []
    for index, brief in enumerate(clips_briefs):
        clips.append(
            _run_clip(
                index,
                brief,
                round_id,
                tree_id,
                root_id,
                store=store,
                artifacts=artifacts,
                adapter=adapter,
                gateway=gateway,
                engine=engine,
                config=config,
                compliance=compliance,
                proxies=proxies,
                judge=judge,
                drift_gate=drift_gate,
            )
        )

    spent = _round_spent(engine, round_id)
    return RoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        clips=clips,
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


def build_evaluators(config: VisualConfig, gateway: LLMGateway, artifacts: ArtifactStore) -> dict:
    """五评估器装配（run_round / consistency / demo 共用的唯一装配点）。

    返回 {"compliance", "proxies": [...], "judge", "all": [...]}。
    """
    compliance = FormatComplianceEvaluator(config.clip_spec)
    proxies = [
        AestheticEvaluator(config.frame_sampling),
        IdentityConsistencyEvaluator(config.frame_sampling),
        FlickerEvaluator(config.frame_sampling),
    ]
    judge = CinematicJudgeEvaluator(
        gateway,
        model=_judge_model(config),
        prompts=list(config.judge["prompts"]),
        anchor_hashes=_judge_anchor_hashes(config, artifacts),
        sampling_spec=config.frame_sampling,
    )
    return {
        "compliance": compliance,
        "proxies": proxies,
        "judge": judge,
        "all": [compliance, *proxies, judge],
    }


def freeze_round_tree(round_id: str, store: TreeStore, engine: Engine) -> DiscoveryTree:
    """轮次树显式冻结入口（契约：GenJob 全终态才允许冻结入池）。

    config_snapshot 在树创建时已写全（五评估器版本组合 + 权重 + 观测
    白名单）——树 immutable 不允许后补，此函数只做终态校验 + 返回树对象。
    """
    tree_id = round_tree_id(round_id)
    store.get_node(_round_root_id(round_id))  # 不存在 → NotFoundError
    with engine.connect() as conn:
        pending = conn.execute(
            select(func.count())
            .select_from(visual_gen_jobs)
            .where(
                visual_gen_jobs.c.round_id == round_id,
                visual_gen_jobs.c.status.in_(["submitted", "generating", "completed"]),
            )
        ).scalar()
    if pending:
        raise VisualLoopError(f"轮次 {round_id} 尚有 {pending} 个生成任务未到终态，不得冻结入池")
    trees = store.trees_by(project_id="visual", agent_id="visual")
    matches = [t for t in trees if t.tree_id == tree_id]
    if not matches:
        raise VisualLoopError(f"轮次树不存在：{tree_id}")
    return matches[0]


def _judge_model(config: VisualConfig) -> str:
    """judge 经网关调用的模型（价目表必须覆盖；缺价目网关即报错）。"""
    return config.judge.get("model", "mock-copy-v1")


def _config_snapshot(config: VisualConfig, compliance, proxies, judge) -> dict:
    """快照冻结：五评估器版本组合 + 权重 + 观测白名单 + 采样规则。"""
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {
            "rule.format_compliance": compliance.spec.version,
            "proxy.aesthetic": proxies[0].spec.version,
            "proxy.identity_consistency": proxies[1].spec.version,
            "proxy.flicker": proxies[2].spec.version,
            "judge.cinematic": judge.spec.version,
        },
        "observation_fields": ["gen_params", "clip_id"],
        "clip_spec": config.clip_spec,
        "frame_sampling": config.frame_sampling,
    }


def _root_node(tree_id, root_id, round_id, clips_briefs, policy_version) -> TreeNode:
    """轮次锚点根节点（score=0.0 仅作结构起点；简报存档供幂等重建）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id="visual",
        policy_version=policy_version,
        prompt=f"视觉探索轮次 {round_id}",
        observation_context={"round_id": round_id, "clips": clips_briefs},
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
                select(func.sum(visual_gen_jobs.c.cost_usd)).where(
                    visual_gen_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _append_clip_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    clip_id: str,
    gen_params: dict,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    reason: str | None,
) -> str:
    """片段节点一次性完整 INSERT（落盘即冻结）。"""
    observation = {"gen_params": gen_params, "clip_id": clip_id}
    if reason:
        observation["reject_reason"] = reason
    node = TreeNode(
        node_id=f"{clip_id}-node",
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="visual",
        policy_version="visual",
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


def _run_clip(
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
    engine,
    config,
    compliance,
    proxies,
    judge,
    drift_gate=None,
) -> dict:
    """单片段流水线：预算门禁 → 生成 → 探测 → 五评估器 → 合成 → 落盘。"""
    clip_id = _clip_id(round_id, index)
    gen_params = brief.get("gen_params", {})
    estimated = float(config.simulated_gen["estimated_cost_usd"])

    # 1) 预算门禁前置校验（生成申请前，≤ 语义含最小货币单位边界）
    spent_cents = round(_round_spent(engine, round_id) * 100)
    cap_cents = round(config.exploration_per_round_usd * 100)
    if spent_cents + round(estimated * 100) > cap_cents:
        reason = (
            f"预算门禁：已耗 ${spent_cents / 100:.2f} + 申请 ${estimated:.2f} "
            f"> 上限 ${cap_cents / 100:.2f}（拒绝）"
        )
        _append_clip_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            clip_id=clip_id,
            gen_params=gen_params,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {"clip_id": clip_id, "status": "rejected", "reason": reason}

    # 2) 生成（适配器瞬时错误退避重试，上限 3 次；网关失败不重试）
    try:
        production = produce_clip(gen_params, clip_id, adapter, artifacts)
    except VideoGenError as exc:
        _record_job(
            engine,
            round_id=round_id,
            clip_id=clip_id,
            params_hash=_params_hash(gen_params),
            status="failed",
            cost_usd=0.0,
            external_id=None,
        )
        _append_clip_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            clip_id=clip_id,
            gen_params=gen_params,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(),
            reason=f"生成失败：{exc}",
        )
        return {"clip_id": clip_id, "status": "failed", "reason": f"生成失败：{exc}"}

    # 3) 事务内复核+扣减落账（防并发双花）：实际扣费再次校验上限
    try:
        _insert_job_guarded(
            engine, round_id, clip_id, gen_params, production, config.exploration_per_round_usd
        )
    except _BudgetExceeded as exc:
        _append_clip_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            clip_id=clip_id,
            gen_params=gen_params,
            artifact_hash=production.artifact_hash,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=production.actual_cost_usd,
                wall_clock_seconds=production.wall_clock_seconds,
            ),
            reason=str(exc),
        )
        return {"clip_id": clip_id, "status": "rejected", "reason": str(exc)}

    # 4) 五评估器（合规门禁短路不跑 judge；单评估器崩溃 → FAILED 轮次继续）
    artifact_ref = ArtifactRef(artifact_hash=production.artifact_hash)
    try:
        samples = sample_frames(
            _artifact_path(artifacts, production.artifact_hash, production.artifact_bytes),
            config.frame_sampling,
        )
        ctx = {"probe_meta": production.probe_meta, "samples": samples, "gen_params": gen_params}
        compliance_result = compliance.evaluate(artifact_ref, ctx)
        breakdown = {compliance.spec.key: _fragment(compliance_result)}

        judge_cost = {"llm_calls": 0, "llm_tokens": 0, "cost_usd": 0.0}
        if compliance_result.score == 0.0:
            # 门禁短路：合成 0 且不进入 judge（省 LLM 成本）
            for evaluator in proxies:
                breakdown[evaluator.spec.key] = _fragment(evaluator.evaluate(artifact_ref, ctx))
            score = 0.0
        else:
            for evaluator in [*proxies, judge]:
                breakdown[evaluator.spec.key] = _fragment(evaluator.evaluate(artifact_ref, ctx))
            judge_cost = judge.last_usage
            weights = apply_gate(_weights(config), drift_gate)  # 漂移门禁（合成前一处，012）
            score = quantize_score(
                composite_score_versioned(
                    {k: EvalResult(score=v["score"]) for k, v in breakdown.items()},
                    weights,
                )
            )
        cost = CostRecord(
            llm_calls=judge_cost["llm_calls"],
            llm_tokens=judge_cost["llm_tokens"],
            generation_api_calls=1,
            generation_api_cost_usd=production.actual_cost_usd + judge_cost["cost_usd"],
            wall_clock_seconds=production.wall_clock_seconds,
        )
        node_id = _append_clip_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            clip_id=clip_id,
            gen_params=gen_params,
            artifact_hash=production.artifact_hash,
            status=NodeStatus.EVALUATED,
            score=score,
            breakdown=breakdown,
            cost=cost,
            reason=None,
        )
        _mark_ingested(engine, round_id, clip_id, node_id)
        return {"clip_id": clip_id, "status": "ingested", "reason": ""}
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离（SC-006）：FAILED 成本入账轮次继续
        judge_usage = judge.last_usage
        cost = CostRecord(
            llm_calls=judge_usage["llm_calls"],
            llm_tokens=judge_usage["llm_tokens"],
            generation_api_calls=1,
            generation_api_cost_usd=production.actual_cost_usd + judge_usage["cost_usd"],
            wall_clock_seconds=production.wall_clock_seconds,
        )
        node_id = _append_clip_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            clip_id=clip_id,
            gen_params=gen_params,
            artifact_hash=production.artifact_hash,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=cost,
            reason=f"评估器崩溃：{exc}",
        )
        _mark_ingested(engine, round_id, clip_id, node_id)
        return {"clip_id": clip_id, "status": "failed", "reason": f"评估器崩溃：{exc}"}


def _fragment(result) -> dict:
    return {"score": result.score, "diagnostics": result.diagnostics}


def _weights(config: VisualConfig) -> dict:
    return {
        key: (0.0 if str(value).lower() == "gate" else float(value))
        for key, value in config.evaluator_weights.items()
    }


def _artifact_path(artifacts, artifact_hash: str, fallback_bytes: bytes) -> str:
    """采样需要文件路径：LocalArtifactStore 直接取路径，否则写临时文件。"""
    import tempfile

    root = getattr(artifacts, "_root", None)
    if root is not None:
        return str(root / artifact_hash)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(fallback_bytes)
        return tmp.name


class _BudgetExceeded(Exception):
    """预算门禁内部信号（事务内复核失败）。"""


def _insert_job_guarded(engine, round_id, clip_id, gen_params, production, cap_usd) -> None:
    """事务内复核 spent + 实际扣费 ≤ 上限 后落运营表（防并发双花）。"""
    with engine.begin() as conn:
        spent = float(
            conn.execute(
                select(func.sum(visual_gen_jobs.c.cost_usd)).where(
                    visual_gen_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )
        spent_cents = round(spent * 100)
        charge_cents = round(production.actual_cost_usd * 100)
        if spent_cents + charge_cents > round(cap_usd * 100):
            raise _BudgetExceeded(
                f"预算门禁：已耗 ${spent:.2f} + 本次 ${production.actual_cost_usd:.2f} "
                f"> 上限 ${cap_usd:.2f}（拒绝）"
            )
        now = time.time()
        conn.execute(
            insert(visual_gen_jobs).values(
                job_id=clip_id,
                round_id=round_id,
                params_hash=_params_hash(gen_params),
                node_id=None,
                status="completed",
                cost_usd=production.actual_cost_usd,
                external_id=clip_id,
                created_at=now,
                updated_at=now,
            )
        )


def _record_job(engine, *, round_id, clip_id, params_hash, status, cost_usd, external_id):
    now = time.time()
    with engine.begin() as conn:
        conn.execute(
            insert(visual_gen_jobs).values(
                job_id=clip_id,
                round_id=round_id,
                params_hash=params_hash,
                node_id=None,
                status=status,
                cost_usd=cost_usd,
                external_id=external_id,
                created_at=now,
                updated_at=now,
            )
        )


def _mark_ingested(engine, round_id, clip_id, node_id) -> None:
    with engine.begin() as conn:
        conn.execute(
            visual_gen_jobs.update()
            .where(visual_gen_jobs.c.job_id == clip_id)
            .values(status="ingested", node_id=node_id, updated_at=time.time())
        )


def _reconcile(store, engine, tree_id, round_id, *, gateway_delta: float) -> dict:
    """三方对账：树内成本合计 == 运营表扣减 + 网关账目（SC-003）。"""
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    adapter_total = _round_spent(engine, round_id)
    ledger_total = adapter_total + gateway_delta
    return {
        "tree_total_usd": tree_total,
        "ledger_total_usd": ledger_total,
        "consistent": abs(tree_total - ledger_total) < 1e-9,
    }


def _reconstruct(
    round_id: str, store: TreeStore, engine: Engine, config: VisualConfig
) -> RoundResult:
    """幂等重建：二次触发撞唯一约束后，从树与运营表还原首轮 RoundResult。"""
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    briefs = root.observation_context.get("clips", [])
    nodes = store.nodes_of(tree_id)
    node_by_clip = {
        n.observation_context.get("clip_id"): n for n in nodes if n.parent_id is not None
    }
    with engine.connect() as conn:
        rows = {
            row.job_id: row
            for row in conn.execute(
                select(visual_gen_jobs).where(visual_gen_jobs.c.round_id == round_id)
            )
        }
    clips = []
    for index, _ in enumerate(briefs):
        clip_id = _clip_id(round_id, index)
        if clip_id in rows and rows[clip_id].status == "ingested":
            node = node_by_clip.get(clip_id)
            status = (
                "failed" if node is not None and node.status is NodeStatus.FAILED else "ingested"
            )
            reason = "" if node is None else node.observation_context.get("reject_reason", "")
            clips.append({"clip_id": clip_id, "status": status, "reason": reason})
        elif clip_id in node_by_clip:
            node = node_by_clip[clip_id]
            status = "failed" if node.status is NodeStatus.FAILED else "rejected"
            clips.append(
                {
                    "clip_id": clip_id,
                    "status": status,
                    "reason": node.observation_context.get("reject_reason", ""),
                }
            )
        elif clip_id in rows:
            clips.append({"clip_id": clip_id, "status": rows[clip_id].status, "reason": ""})
    return RoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        clips=clips,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=config.exploration_per_round_usd,
        cost_reconciliation={},
    )
