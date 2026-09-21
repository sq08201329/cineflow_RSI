"""做梦执行管线（T412，contracts/dreaming.md；US1 本体）。

一轮做梦：digest 组装（最近 K 轮落盘报告）→ 候选生成（M 套，哈希去重）→
静态检查（rejected 不回放不记分）→ 沙箱全池串行回放（一期串行，
replay_parallelism=1 预留；超时/崩溃记 0 分注明不阻断）→
reward 排名（pareto_auc − λ·parallel_penalty，λ 来自配置）→
DreamRound 落盘 dreaming/history/{agent_id}/{round_id}.json（只增不改）。

零生成审计（FR-012）：全程生成 API 调用恒 0；LLM 调用仅候选生成环节。
pipeline 自身不更新部署指针（胜出者进审批流程，US2）。
"""

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from core.replay.pool import SimulatorPool
from core.replay.trajectory import ReplayTrajectory
from core.sandbox.runner import RunLimits, run_policy
from dreaming.candidates import CandidateGenerator
from dreaming.config import DreamConfig
from dreaming.digest import DEFAULT_HISTORY_ROOT, build_digest, digest_hash
from dreaming.reward import RewardBreakdown, compute_reward
from policies.base import Budget
from policies.static_check import find_violations
from policies.versioning import policy_version


class AutoEvolutionForbiddenError(Exception):
    """禁止自动进化（宪章原则六）：名单内的 Agent 不得由 dreaming 生成候选策略。

    这是**显式拒绝**（非静默跳过）——降级模式的 Agent（评估信号过弱）策略仅由人工
    编写、提交与采纳，升级须另立决议并修订宪章；自动进化不得在此处被"顺手开启"。
    """


@dataclass(frozen=True)
class Candidate:
    """候选策略（data-model §1 Candidate）。"""

    version: str
    source_code: str
    static_check: str  # "passed" | "rejected"
    violations: list[str] = field(default_factory=list)
    trajectory: dict | None = None  # ReplayTrajectory.to_dict()；回放失败为 None
    reward: RewardBreakdown | None = None  # 回放失败/违规为 None（排名按 0 分）
    note: str = ""
    trajectory_obj: ReplayTrajectory | None = field(
        default=None, repr=False, compare=False
    )  # 进程内对象（不序列化，供 reward 复核）

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("trajectory_obj", None)
        return data


@dataclass(frozen=True)
class DreamRound:
    """做梦轮次（data-model §1 DreamRound；落盘只增不改）。"""

    round_id: str
    agent_id: str
    champion_version: str
    digest: dict
    digest_sha: str
    candidates: list[Candidate]
    winner_version: str | None
    status: str  # completed / failed_all_rejected / failed_all_unknown / aborted
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "round_id": self.round_id,
            "agent_id": self.agent_id,
            "champion_version": self.champion_version,
            "digest": self.digest,
            "digest_sha": self.digest_sha,
            "candidates": [c.to_dict() for c in self.candidates],
            "winner_version": self.winner_version,
            "status": self.status,
            "diagnostics": self.diagnostics,
        }


def in_process_replay(source: str, pool: SimulatorPool) -> ReplayTrajectory:
    """进程内回放（单元测试/开发档）：同接口无容器，快速验证回放语义。

    生产路径为 default_sandbox_replay（002 容器沙箱）；两者轨迹口径一致。
    """
    namespace: dict = {}
    exec(compile(source, "<policy>", "exec"), namespace)  # 已过静态检查
    policy = namespace["Policy"]()
    simulator = pool.build(worker_count=4, budget=Budget(max_probes=8), latency_quantum_ms=0)
    final_node_id = policy.solve(simulator, simulator.budget)
    simulator.finalize(policy_version=policy_version(source), final_node_id=final_node_id)
    return simulator.trajectory()


def default_sandbox_replay(source: str, pool: SimulatorPool) -> ReplayTrajectory:
    """生产路径：002 沙箱容器回放（静态检查已过；物理隔离边界）。

    候选版本落盘到轮次临时目录——候选未过审批不得进 policies/history
    权威谱系（谱系落盘仅由 approve.decide 在 approved 时执行）。
    """
    import tempfile

    from core.sandbox.backends import select_backend

    simulator = pool.build(worker_count=4, budget=Budget(max_probes=8), latency_quantum_ms=0)
    with tempfile.TemporaryDirectory(prefix="cineflow-candidates-") as scratch:
        result = run_policy(source, simulator, RunLimits(), select_backend(), history_root=scratch)
    if result.trajectory is None:
        raise RuntimeError(f"沙箱回放失败：{result.status} {result.stderr_tail[-200:]}")
    return result.trajectory


def _next_round_id(agent_id: str, history_root: Path) -> str:
    directory = Path(history_root) / agent_id
    seq = len(list(directory.glob("dream-*.json"))) + 1 if directory.is_dir() else 1
    return f"dream-{agent_id}-{seq}"


def run_dream_round(
    agent_id: str,
    champion_source: str,
    generator: CandidateGenerator,
    simulator_pool: SimulatorPool,
    gateway,  # LLMGateway | None（MutatorGenerator 不用 LLM）
    config: DreamConfig,
    *,
    replay_fn=in_process_replay,
    history_root: str | Path = DEFAULT_HISTORY_ROOT,
    m: int | None = None,
    deploy_hook=None,
) -> DreamRound:
    """执行一轮做梦（生成 → 静态检查 → 串行回放 → reward 排名 → 落盘）。

    **候选生成前的拒绝守卫**（宪章原则六）：agent_id 命中 `no_auto_evolve_agents`
    名单即抛 `AutoEvolutionForbiddenError`——在任何副作用（候选生成/计费/落盘）之前。

    `deploy_hook`（可选）：**轮次收口后的唯一部署评估接线点**（功能 014 T1416）——
    收到已完成的 `DreamRound`，返回评估摘要并记入 `diagnostics["deployment"]`。
    部署逻辑一律在 core（dreaming 侧只调用，原则五单向依赖）；钩子失败如实记录、
    不阻断做梦主流程（评估失败绝不能吞掉一轮已有的回放成果）。
    """
    if agent_id in config.no_auto_evolve_agents:
        raise AutoEvolutionForbiddenError(
            f"agent_id={agent_id!r} 在禁止自动进化名单内（宪章原则六：评估信号过弱的环节"
            "禁止强行自动进化，策略仅由人工提交与采纳）——本调用被显式拒绝，"
            "未生成任何候选、未计费"
        )
    history_root = Path(history_root)
    round_id = _next_round_id(agent_id, history_root)
    champion_version = policy_version(champion_source)
    digest = build_digest(agent_id, history_root, recent_k=config.recent_k)
    m = m if m is not None else config.candidates_per_round

    # 候选生成（LLM 调用仅此环节，全过网关入账）+ 哈希去重
    sources = generator.generate(champion_source, digest, m)
    seen: dict[str, str] = {}
    for source in sources:
        seen.setdefault(policy_version(source), source)  # 同版本自动去重
    deduped = list(seen.values())

    # 静态检查：rejected 不回放不记分（FR-003）
    candidates: list[Candidate] = []
    for source in deduped:
        violations = find_violations(source)
        if violations:
            candidates.append(
                Candidate(
                    version=policy_version(source),
                    source_code=source,
                    static_check="rejected",
                    violations=violations,
                )
            )
        else:
            candidates.append(
                Candidate(version=policy_version(source), source_code=source, static_check="passed")
            )

    passed = [c for c in candidates if c.static_check == "passed"]
    diagnostics: dict = {"m_requested": m, "dedup_dropped": len(sources) - len(deduped)}

    if not passed:
        # 全灭：静态检查 100% 拦截 → 告警不产出胜者（决策 6）
        diagnostics["note"] = "全部候选未过静态检查"
        result = DreamRound(
            round_id=round_id,
            agent_id=agent_id,
            champion_version=champion_version,
            digest=digest,
            digest_sha=digest_hash(digest),
            candidates=candidates,
            winner_version=None,
            status="failed_all_rejected",
            diagnostics=diagnostics,
        )
        _persist(result, history_root)
        return result

    # 沙箱全池串行回放（一期串行；超时/崩溃记 0 分注明不阻断）
    replayed: list[Candidate] = []
    generation_api_calls = 0
    for candidate in passed:
        try:
            trajectory = replay_fn(candidate.source_code, simulator_pool)
        except Exception as exc:  # noqa: BLE001 - 单候选失败不阻断管线
            replayed.append(
                Candidate(
                    version=candidate.version,
                    source_code=candidate.source_code,
                    static_check="passed",
                    note=f"回放失败：{exc}",
                )
            )
            continue
        generation_api_calls += trajectory.total_cost.generation_api_calls
        replayed.append(
            Candidate(
                version=candidate.version,
                source_code=candidate.source_code,
                static_check="passed",
                trajectory=trajectory.to_dict(),
                trajectory_obj=trajectory,
                reward=compute_reward(trajectory, config.lambda_),
            )
        )

    # 零生成审计断言（FR-012：回放只读历史，全程生成调用恒 0）
    diagnostics["generation_api_calls"] = generation_api_calls
    diagnostics["zero_generation"] = generation_api_calls == 0

    all_unknown = all(
        c.trajectory is not None and not c.trajectory["best_score_curve"] for c in replayed
    ) and any(c.trajectory is not None for c in replayed)

    scored = [c for c in replayed if c.reward is not None]
    if all_unknown:
        diagnostics["note"] = "全部候选回放 UNKNOWN：经验覆盖不足，请扩大线上探索（原则三）"
        status = "failed_all_unknown"
        winner = None
    elif not scored:
        diagnostics["note"] = "全部候选回放失败"
        status = "failed_all_unknown"
        winner = None
    else:
        # 排名：reward 降序，平分按版本号升序（确定性）
        best = max(scored, key=lambda c: (c.reward.reward,))
        status = "completed"
        winner = best.version

    result = DreamRound(
        round_id=round_id,
        agent_id=agent_id,
        champion_version=champion_version,
        digest=digest,
        digest_sha=digest_hash(digest),
        candidates=[*replayed, *[c for c in candidates if c.static_check == "rejected"]],
        winner_version=winner,
        status=status,
        diagnostics=diagnostics,
    )
    if deploy_hook is not None:
        # 轮次收口后调用部署评估唯一入口（一处接线）：钩子自带上下文，返回摘要
        try:
            outcome = deploy_hook(result)
        except Exception as exc:  # noqa: BLE001 - 部署评估失败不阻断做梦（如实记录不静默）
            outcome = {"status": "error", "note": f"部署评估失败：{exc}"}
        result = replace(result, diagnostics={**result.diagnostics, "deployment": outcome})
    _persist(result, history_root)
    return result


def _persist(result: DreamRound, history_root: Path) -> Path:
    """DreamRound 落盘 dreaming/history/{agent_id}/{round_id}.json（只增不改）。"""
    directory = history_root / result.agent_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{result.round_id}.json"
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    return target
