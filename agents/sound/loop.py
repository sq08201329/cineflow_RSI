"""声音线上探索执行器（contracts/sound-loop.md C1，US1 闭环本体）。

一轮探索：TimingSheet 执行前校验（非法输入 0 调用 0 成本）→ 策略产 SoundGenParams
组合 → 预算门禁（申请前校验 + 事务内复核，≤ 语义含最小货币单位）→ 按 gen_type
分派适配器生成（昂贵动作仅此阶段，原则三）→ 工件内容寻址 → 评估器协议打分
（US1 桩注入，US2 接线真实四评估器）→ 合成得分（quantize 定点归一）→
节点一次性 INSERT 冻结。失败 job 成本照计入账 status=failed（原则二）。
幂等：tree_id/job_id 由 round_id 确定性派生 + 唯一键 (round_id, params_hash)，
二次触发重建首轮 SoundRoundResult（0 重复生成、0 重复扣费）。
分账：轮次成本按 gen_type 分组合计（TTS/音效/音乐各自小计 + 总计）。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import blake3
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.sound.config import SoundConfig
from agents.sound.db import sound_gen_jobs
from agents.sound.platform.base import SoundGenAdapter, SoundGenError
from agents.sound.timing import TimingSheet
from core.evaluators.base import ArtifactRef, EvalResult, Evaluator
from core.evaluators.composite import composite_score_versioned
from core.evaluators.quantize import quantize_score
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

PLACEHOLDER_HASH = "00" * 32  # 未产出工件的拒投节点占位哈希（无工件可引）
_GEN_TYPES = ("tts", "sfx", "music")


class SoundLoopError(Exception):
    """声音闭环错误（轮次收口校验失败等）。"""


class SoundPolicy(Protocol):
    """声音策略协议（做梦层接入前的手工策略形态）：产出 gen_params 组合。"""

    policy_version: str

    def plan(self, config: SoundConfig, inputs: dict) -> list[dict]: ...


@dataclass(frozen=True)
class SoundRoundResult:
    """轮次收口：jobs 汇总 + 成本按类型分账（C1）。"""

    round_id: str
    tree_id: str
    policy_version: str
    jobs: list[dict]  # [{"job_id","gen_type","status","reason"}]
    spent_usd: float
    budget_cap_usd: float
    cost_by_type: dict = field(default_factory=dict)  # {tts,sfx,music,total}
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"sound-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _job_id(round_id: str, index: int) -> str:
    return f"{round_id}-j{index}"


def _params_hash(gen_params: dict) -> str:
    canonical = json.dumps(gen_params, sort_keys=True, ensure_ascii=False)
    return blake3.blake3(canonical.encode()).hexdigest()


def _params_json(gen_params: dict) -> str:
    return json.dumps(gen_params, sort_keys=True, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_inputs(inputs: dict) -> TimingSheet:
    """TimingSheet 执行前校验：非法输入在策略/适配器/落库之前拒绝（C1 场景 5）。"""
    sheet = inputs.get("timing_sheet")
    if isinstance(sheet, TimingSheet):
        return sheet
    if isinstance(sheet, dict):
        return TimingSheet(**sheet)  # 构造即校验，非法 → ValidationError
    raise SoundLoopError("inputs 缺少 timing_sheet（TimingSheet 或 dict）")


def run_sound_round(
    round_id: str,
    policy: SoundPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    adapters: dict[str, SoundGenAdapter],
    engine: Engine,
    config: SoundConfig,
    inputs: dict,
    evaluators: list[Evaluator],
) -> SoundRoundResult:
    """执行一轮声音线上探索（全流程幂等）。"""
    # 0) 输入校验先于一切副作用（适配器 0 调用、0 成本、0 落库）
    timing_sheet = _validate_inputs(inputs)

    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    policy_version = getattr(policy, "policy_version", "unknown")
    cap = config.exploration_per_round_usd

    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回 ----
    plans = policy.plan(config, inputs)
    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id="sound",
        agent_id="sound",
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=_config_snapshot(config, evaluators),
    )
    try:
        store.create_tree(tree)
        store.append_node(
            _root_node(tree_id, root_id, round_id, plans, policy_version, timing_sheet)
        )
    except DuplicateError:
        return _reconstruct(round_id, store, engine, config)

    jobs: list[dict] = []
    for index, plan_item in enumerate(plans):
        jobs.append(
            _run_job(
                index,
                plan_item,
                round_id,
                tree_id,
                root_id,
                store=store,
                artifacts=artifacts,
                adapters=adapters,
                engine=engine,
                config=config,
                evaluators=evaluators,
                timing_sheet=timing_sheet,
            )
        )

    return SoundRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=cap,
        cost_by_type=_cost_by_type(engine, round_id),
        cost_reconciliation=_reconcile(store, engine, tree_id, round_id),
    )


def _config_snapshot(config: SoundConfig, evaluators: list[Evaluator]) -> dict:
    """快照冻结：权重 + 评估器版本组合 + 观测白名单 + 评估口径配置。"""
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {e.spec.evaluator_id: e.spec.version for e in evaluators},
        "observation_fields": ["gen_params", "gen_type", "job_id"],
        "loudness": config.loudness,
        "av_sync_threshold_ms": config.av_sync_threshold_ms,
        "sample_rate": config.sample_rate,
    }


def _root_node(tree_id, root_id, round_id, plans, policy_version, timing_sheet) -> TreeNode:
    """轮次锚点根节点（score=0.0 仅作结构起点；计划与时序存档供幂等重建）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id="sound",
        policy_version=policy_version,
        prompt=f"声音探索轮次 {round_id}",
        observation_context={
            "round_id": round_id,
            "plans": plans,
            "timing_sheet": {
                "utterances": [asdict(u) for u in timing_sheet.utterances],
                "effects": [asdict(e) for e in timing_sheet.effects],
            },
        },
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
                select(func.sum(sound_gen_jobs.c.actual_cost_usd)).where(
                    sound_gen_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )


def _cost_by_type(engine: Engine, round_id: str) -> dict:
    """分账：按 gen_type 分组合计 + 总计（C1；失败 job 的入账成本同样计入）。"""
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                sound_gen_jobs.c.gen_type,
                func.sum(sound_gen_jobs.c.actual_cost_usd),
            )
            .where(sound_gen_jobs.c.round_id == round_id)
            .group_by(sound_gen_jobs.c.gen_type)
        ).all()
    by_type = {gen_type: 0.0 for gen_type in _GEN_TYPES}
    for gen_type, subtotal in rows:
        by_type[gen_type] = float(subtotal or 0.0)
    by_type["total"] = sum(by_type.values())
    return by_type


def _append_job_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    job_id: str,
    gen_type: str,
    gen_params: dict,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    reason: str | None,
) -> str:
    """声音节点一次性完整 INSERT（落盘即冻结）。"""
    observation = {"gen_params": gen_params, "gen_type": gen_type, "job_id": job_id}
    if reason:
        observation["reject_reason"] = reason
    node = TreeNode(
        node_id=f"{job_id}-node",
        tree_id=tree_id,
        parent_id=root_id,
        depth=1,
        agent_id="sound",
        policy_version="sound",
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
    plan_item: dict,
    round_id: str,
    tree_id: str,
    root_id: str,
    *,
    store,
    artifacts,
    adapters,
    engine,
    config,
    evaluators,
    timing_sheet,
) -> dict:
    """单任务流水线：预算门禁 → 生成 → 内容寻址 → 评估 → 合成 → 落盘。"""
    job_id = _job_id(round_id, index)
    gen_type = plan_item["gen_type"]
    gen_params = plan_item["gen_params"]
    adapter = adapters[gen_type]
    estimated = float(adapter.estimate(gen_params))

    # 1) 预算门禁前置校验（生成申请前，≤ 语义含最小货币单位边界）
    spent_cents = round(_round_spent(engine, round_id) * 100)
    cap_cents = round(config.exploration_per_round_usd * 100)
    if spent_cents + round(estimated * 100) > cap_cents:
        reason = (
            f"预算门禁：已耗 ${spent_cents / 100:.2f} + 申请 ${estimated:.2f} "
            f"> 上限 ${cap_cents / 100:.2f}（拒绝）"
        )
        _append_job_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            reason=reason,
        )
        return {"job_id": job_id, "gen_type": gen_type, "status": "rejected", "reason": reason}

    # 2) 生成（失败 job：预估成本照常入账 status=failed，轮次继续——原则二）
    try:
        produced = adapter.generate(gen_params)
    except SoundGenError as exc:
        _insert_job(
            engine,
            round_id=round_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            status="failed",
            estimated=estimated,
            actual=estimated,  # 失败照计预估成本（C1 场景 4）
            artifact_hash=None,
            error=str(exc),
        )
        _append_job_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(generation_api_calls=1, generation_api_cost_usd=estimated),
            reason=f"生成失败：{exc}",
        )
        return {
            "job_id": job_id,
            "gen_type": gen_type,
            "status": "failed",
            "reason": f"生成失败：{exc}",
        }

    # 3) 工件内容寻址
    artifact_hash = artifacts.put(produced.wav_bytes)

    # 4) 事务内复核+扣减落账（防并发双花）：实际扣费再次校验上限
    try:
        _insert_job_guarded(
            engine,
            round_id,
            job_id,
            gen_type,
            gen_params,
            produced.actual_cost_usd,
            estimated,
            artifact_hash,
            config.exploration_per_round_usd,
        )
    except _BudgetExceeded as exc:
        _append_job_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=produced.actual_cost_usd,
            ),
            reason=str(exc),
        )
        return {"job_id": job_id, "gen_type": gen_type, "status": "rejected", "reason": str(exc)}

    # 5) 评估器协议打分（崩溃隔离：FAILED 成本入账轮次继续，同 004 SC-006）
    artifact_ref = ArtifactRef(artifact_hash=artifact_hash, metadata=produced.metadata)
    ctx = {
        "gen_params": gen_params,
        "gen_type": gen_type,
        "metadata": produced.metadata,
        "timing_sheet": timing_sheet,
        "sample_rate": config.sample_rate,
    }
    try:
        breakdown = {}
        for evaluator in evaluators:
            result = evaluator.evaluate(artifact_ref, ctx)
            breakdown[evaluator.spec.key] = {
                "score": result.score,
                "diagnostics": result.diagnostics,
            }
        score = quantize_score(
            composite_score_versioned(
                {k: EvalResult(score=v["score"]) for k, v in breakdown.items()},
                _weights(config),
            )
        )
        node_id = _append_job_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            artifact_hash=artifact_hash,
            status=NodeStatus.EVALUATED,
            score=score,
            breakdown=breakdown,
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=produced.actual_cost_usd,
            ),
            reason=None,
        )
        _mark_inserted(engine, job_id, node_id)
        return {"job_id": job_id, "gen_type": gen_type, "status": "inserted", "reason": ""}
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离：FAILED 成本入账轮次继续
        node_id = _append_job_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            gen_type=gen_type,
            gen_params=gen_params,
            artifact_hash=artifact_hash,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(
                generation_api_calls=1,
                generation_api_cost_usd=produced.actual_cost_usd,
            ),
            reason=f"评估器崩溃：{exc}",
        )
        _mark_inserted(engine, job_id, node_id)
        return {
            "job_id": job_id,
            "gen_type": gen_type,
            "status": "failed",
            "reason": f"评估器崩溃：{exc}",
        }


def _weights(config: SoundConfig) -> dict:
    return {
        key: (0.0 if str(value).lower() == "gate" else float(value))
        for key, value in config.evaluator_weights.items()
    }


class _BudgetExceeded(Exception):
    """预算门禁内部信号（事务内复核失败）。"""


def _insert_job(
    engine,
    *,
    round_id: str,
    job_id: str,
    gen_type: str,
    gen_params: dict,
    status: str,
    estimated: float,
    actual: float,
    artifact_hash: str | None,
    error: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(sound_gen_jobs).values(
                job_id=job_id,
                round_id=round_id,
                gen_type=gen_type,
                params_json=_params_json(gen_params),
                params_hash=_params_hash(gen_params),
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
    gen_type: str,
    gen_params: dict,
    actual: float,
    estimated: float,
    artifact_hash: str,
    cap_usd: float,
) -> None:
    """事务内复核 spent + 实际扣费 ≤ 上限 后落运营表（防并发双花）。"""
    with engine.begin() as conn:
        spent = float(
            conn.execute(
                select(func.sum(sound_gen_jobs.c.actual_cost_usd)).where(
                    sound_gen_jobs.c.round_id == round_id
                )
            ).scalar()
            or 0.0
        )
        spent_cents = round(spent * 100)
        charge_cents = round(actual * 100)
        if spent_cents + charge_cents > round(cap_usd * 100):
            raise _BudgetExceeded(
                f"预算门禁：已耗 ${spent:.2f} + 本次 ${actual:.2f} "
                f"> 上限 ${cap_usd:.2f}（拒绝）"
            )
        conn.execute(
            insert(sound_gen_jobs).values(
                job_id=job_id,
                round_id=round_id,
                gen_type=gen_type,
                params_json=_params_json(gen_params),
                params_hash=_params_hash(gen_params),
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
            sound_gen_jobs.update()
            .where(sound_gen_jobs.c.job_id == job_id)
            .values(status="inserted")
        )


def _reconcile(store, engine, tree_id: str, round_id: str) -> dict:
    """两方对账：树内成本合计 == 运营表扣减合计（SC-003 口径，无网关侧）。"""
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    adapter_total = _round_spent(engine, round_id)
    return {
        "tree_total_usd": tree_total,
        "ledger_total_usd": adapter_total,
        "consistent": abs(tree_total - adapter_total) < 1e-9,
    }


def _reconstruct(
    round_id: str, store: TreeStore, engine: Engine, config: SoundConfig
) -> SoundRoundResult:
    """幂等重建：二次触发撞唯一约束后，从树与运营表还原首轮 SoundRoundResult。"""
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    plans = root.observation_context.get("plans", [])
    nodes = store.nodes_of(tree_id)
    node_by_job = {
        n.observation_context.get("job_id"): n for n in nodes if n.parent_id is not None
    }
    with engine.connect() as conn:
        rows = {
            row.job_id: row
            for row in conn.execute(
                select(sound_gen_jobs).where(sound_gen_jobs.c.round_id == round_id)
            )
        }
    jobs = []
    for index, plan_item in enumerate(plans):
        job_id = _job_id(round_id, index)
        gen_type = plan_item["gen_type"]
        node = node_by_job.get(job_id)
        if job_id in rows and rows[job_id].status == "inserted":
            status = "failed" if node is not None and node.status is NodeStatus.FAILED else "inserted"
            reason = "" if node is None else node.observation_context.get("reject_reason", "")
            jobs.append({"job_id": job_id, "gen_type": gen_type, "status": status, "reason": reason})
        elif node is not None:
            status = "failed" if node.status is NodeStatus.FAILED else "rejected"
            jobs.append(
                {
                    "job_id": job_id,
                    "gen_type": gen_type,
                    "status": status,
                    "reason": node.observation_context.get("reject_reason", ""),
                }
            )
        elif job_id in rows:
            jobs.append(
                {
                    "job_id": job_id,
                    "gen_type": gen_type,
                    "status": rows[job_id].status,
                    "reason": rows[job_id].error or "",
                }
            )
    return SoundRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        jobs=jobs,
        spent_usd=_round_spent(engine, round_id),
        budget_cap_usd=config.exploration_per_round_usd,
        cost_by_type=_cost_by_type(engine, round_id),
        cost_reconciliation={},
    )
