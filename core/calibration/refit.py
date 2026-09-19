"""权重再拟合提案与生效门禁（功能 010 US3，契约 C7/C8；research 决策 5/6/7）。

拟合（本模块第一段）：约束岭回归（numpy 实现，零新增依赖）——
min Σ(anchor_i − Σ_e w_e·s_ei)² + λ_ridge·‖w − w_current‖²，s.t. w_e ≥ 0，Σw = 1，
投影梯度 + 单纯形投影（Duchi 等 2008）；fixed_keys（gate 硬规则）权重冻结为零，
自由键收缩到 Σ = 1 − Σfixed。

门禁（本模块第二段）：
- maybe_propose：|mean_shift| > bias_threshold 且样本达标 → pending 提案；
  负相关禁止（转人工裁决）；首轮无历史台账记基线不判超阈（has_history=False）；
- confirm 逻辑事务：① composite 评估器注册新版本
  （version = "{base}+w{权重 BLAKE3 前 12}"，calibration 填台账最新快照）
  → ② configs evaluator_weights.{agent_id} 段定点改写（保注释，gate 行不动）
  → ③ 提案 confirmed（确认人/时间落盘）；中途失败 → 提案 failed，配置不切换；
- shelve 零变更；based_version ≠ 当前部署版本（由 yaml 现权重哈希重算）→ 拒绝。
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import blake3
import numpy as np

from core.calibration.ledger import read_latest
from core.calibration.models import BiasRecord, PairingRecord, ProposalStatus, WeightProposal
from core.evaluators.base import EvalResult, Evaluator, EvaluatorKind, EvaluatorSpec
from core.evaluators.composite import composite_score_versioned
from core.evaluators.errors import ValidationError
from core.evaluators.registry import Registry
from core.evaluators.weights import load_evaluator_weights
from core.tree.models import new_id
from core.yaml_edit import replace_section_entries

_COMPOSITE_BASE_VERSION = "1.0.0"
_FIT_MAX_ITER = 5000
_FIT_TOL = 1e-12


def _project_simplex(v: np.ndarray) -> np.ndarray:
    """投影到概率单纯形 {w ≥ 0, Σw = 1}（Duchi 等 2008，O(m log m)）。"""
    u = np.sort(v)[::-1]
    cumsum = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > cumsum - 1)[0][-1]
    theta = (cumsum[rho] - 1.0) / (rho + 1)
    return np.maximum(v - theta, 0.0)


def fit_weights(
    samples: list[tuple[float, dict[str, float]]],
    current_weights: dict[str, float],
    ridge_lambda: float,
    *,
    fixed_keys: frozenset[str] = frozenset(),
) -> dict[str, float]:
    """约束岭回归拟合候选权重。

    samples：[(anchor_score, {裸键: 分量得分})]，缺分量的样本由调用方剔除；
    fixed_keys 的权重冻结（gate 硬规则不入拟合）；零样本退化返回现权重。
    """
    keys = list(current_weights)
    w0 = np.array([float(current_weights[k]) for k in keys])
    if not samples:
        return {k: float(w) for k, w in zip(keys, w0, strict=True)}

    free_idx = [i for i, k in enumerate(keys) if k not in fixed_keys]
    free_keys = [keys[i] for i in free_idx]
    # 设计矩阵只取自由键（样本分量只覆盖自由键；fixed 维权重恒定，不进线性项）
    Xf = np.array([[float(s[k]) for k in free_keys] for _, s in samples])
    y = np.array([float(a) for a, _ in samples])

    # 仅在自由变量上优化；fixed 维保持 w0（gate = 0.0）
    w0f = w0[free_idx]
    fixed_sum = float(w0.sum() - w0f.sum())
    free_budget = 1.0 - fixed_sum  # 自由键单纯形预算：Σw_free = 1 − Σw_fixed

    w = w0f.copy()
    # 步长 = 1/L，L 为目标函数梯度的 Lipschitz 常数
    lipschitz = 2.0 * (float(np.linalg.norm(Xf, 2) ** 2) + ridge_lambda)
    step = 1.0 / max(lipschitz, 1e-12)
    for _ in range(_FIT_MAX_ITER):
        gradient = 2.0 * Xf.T @ (Xf @ w - y) + 2.0 * ridge_lambda * (w - w0f)
        w_next = _project_simplex(w - step * gradient) * free_budget
        if np.linalg.norm(w_next - w) < _FIT_TOL:
            w = w_next
            break
        w = w_next

    w_full = w0.copy()
    w_full[free_idx] = w
    result = {k: max(0.0, float(w_full[i])) for i, k in enumerate(keys)}
    # 数值尾数归一：自由键和精确为 1 − fixed_sum
    free_total = sum(result[k] for k in keys if k not in fixed_keys)
    if free_total > 0:
        scale = free_budget / free_total
        for k in keys:
            if k not in fixed_keys:
                result[k] *= scale
    return result


def composite_version(weights: dict[str, float]) -> str:
    """composite 评估器版本：{base}+w{权重 BLAKE3 前 12 位}（同权重同版本天然成立）。"""
    payload = json.dumps(weights, sort_keys=True)
    return f"{_COMPOSITE_BASE_VERSION}+w{blake3.blake3(payload.encode()).hexdigest()[:12]}"


class CompositeWeightsEvaluator(Evaluator):
    """composite 权重评估器：版本号即权重哈希元信息（宪章原则一）。

    evaluate 从 context["breakdown"] 取逐评估器分量（evaluator_id@version 键），
    按冻结权重合成总分；calibration 字段填台账最新快照（决策 7）。
    """

    def __init__(self, agent_id: str, weights: dict[str, float], calibration: dict) -> None:
        self._weights = dict(weights)
        self.spec = EvaluatorSpec(
            evaluator_id=f"composite.{agent_id}",
            version=composite_version(weights),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
            calibration=calibration,
        )

    def evaluate(self, artifact, context: dict) -> EvalResult:
        breakdown = {
            key: EvalResult(score=float(value["score"] if isinstance(value, dict) else value))
            for key, value in context["breakdown"].items()
        }
        return EvalResult(
            score=composite_score_versioned(breakdown, self._weights),
            diagnostics={"weights": dict(self._weights)},
        )


# ---------- 提案持久化（calibration/proposals/{proposal_id}.json） ----------


def proposal_path(data_dir, proposal_id: str) -> Path:
    return Path(data_dir) / "proposals" / f"{proposal_id}.json"


def _proposal_to_dict(proposal: WeightProposal) -> dict:
    return {
        "proposal_id": proposal.proposal_id,
        "agent_id": proposal.agent_id,
        "based_version": proposal.based_version,
        "bias_evidence": [asdict(record) for record in proposal.bias_evidence],
        "current_weights": dict(proposal.current_weights),
        "candidate_weights": dict(proposal.candidate_weights),
        "fit_objective": proposal.fit_objective,
        "ridge_lambda": proposal.ridge_lambda,
        "status": proposal.status.value,
        "confirmed_by": proposal.confirmed_by,
        "confirmed_at": proposal.confirmed_at,
    }


def save_proposal(data_dir, proposal: WeightProposal) -> Path:
    path = proposal_path(data_dir, proposal.proposal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_proposal_to_dict(proposal), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_proposal(data_dir, proposal_id: str) -> WeightProposal:
    path = proposal_path(data_dir, proposal_id)
    if not path.is_file():
        raise ValidationError(f"提案不存在：{proposal_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WeightProposal(
        proposal_id=payload["proposal_id"],
        agent_id=payload["agent_id"],
        based_version=payload["based_version"],
        bias_evidence=tuple(BiasRecord(**record) for record in payload["bias_evidence"]),
        current_weights=payload["current_weights"],
        candidate_weights=payload["candidate_weights"],
        fit_objective=payload["fit_objective"],
        ridge_lambda=payload["ridge_lambda"],
        status=payload["status"],
        confirmed_by=payload.get("confirmed_by"),
        confirmed_at=payload.get("confirmed_at"),
    )


# ---------- 提案生成（C7） ----------


def _samples_from_pairs(
    pairs: list[PairingRecord], free_keys: list[str]
) -> list[tuple[float, dict[str, float]]]:
    """配对记录 → 拟合样本：缺分量/被剔除的锚点行不入样（口径同配对契约）。"""
    anchor_scores: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}
    for pair in pairs:
        anchor_scores[pair.anchor_id] = pair.anchor_score
        if pair.excluded or pair.auto_score is None:
            continue
        bare_key = pair.evaluator_key.split("@")[0]
        components.setdefault(pair.anchor_id, {})[bare_key] = pair.auto_score
    return [
        (anchor_scores[anchor_id], comps)
        for anchor_id, comps in components.items()
        if all(key in comps for key in free_keys)
    ]


def maybe_propose(
    *,
    agent_id: str,
    bias_records: list[BiasRecord],
    pairs: list[PairingRecord],
    current_weights: dict[str, float],
    cfg,
    has_history: bool,
    data_dir,
    fixed_keys: frozenset[str] = frozenset(),
) -> WeightProposal | None:
    """超阈生成 pending 提案；未超阈/负相关/首轮基线 → None。

    has_history=False 表示首轮（无历史台账）：本轮偏差记基线，不判超阈。
    负相关（pearson_r 或 kendall_tau < 0）禁止生成提案，转人工裁决（原则六）。
    """
    if not has_history:
        return None
    for record in bias_records:
        for value in (record.pearson_r, record.kendall_tau):
            if value is not None and value < 0:
                return None  # 负相关告警由报告承担，此处禁止提案
    triggered = [
        record
        for record in bias_records
        if record.samples >= cfg.min_samples
        and record.mean_shift is not None
        and abs(record.mean_shift) > cfg.bias_threshold
    ]
    if not triggered:
        return None

    free_keys = [key for key in current_weights if key not in fixed_keys]
    samples = _samples_from_pairs(pairs, free_keys)
    candidate = fit_weights(samples, current_weights, cfg.ridge_lambda, fixed_keys=fixed_keys)
    proposal = WeightProposal(
        proposal_id=new_id(),
        agent_id=agent_id,
        based_version=composite_version(current_weights),
        bias_evidence=tuple(triggered),
        current_weights=dict(current_weights),
        candidate_weights=candidate,
        fit_objective=(
            "min Σ(anchor_i − Σ_e w_e·s_ei)² + "
            f"{cfg.ridge_lambda}·‖w − w_current‖²，s.t. w_e ≥ 0，Σw = 1"
        ),
        ridge_lambda=cfg.ridge_lambda,
    )
    save_proposal(data_dir, proposal)
    return proposal


# ---------- 确认 / 搁置（C8；人工仅两键，无编辑路径） ----------


def gate_keys_of(config_path) -> set[str]:
    """从 yaml 原文识别 gate 键（值为字符串 'gate' 的行不入定点改写/拟合）。"""
    import yaml

    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    section = data.get("evaluator_weights", {})
    return {
        key
        for weights in section.values()
        if isinstance(weights, dict)
        for key, value in weights.items()
        if isinstance(value, str) and value.strip().lower() == "gate"
    }


def confirm_proposal(
    data_dir,
    config_path,
    *,
    proposal_id: str,
    by: str,
    registry: Registry | None = None,
    at: str | None = None,
) -> str:
    """确认生效（逻辑事务）：注册新版本 → yaml 定点改写 → 提案 confirmed。

    based_version ≠ 当前部署版本（yaml 现权重哈希重算）→ ValidationError（过期，
    须重新提案）；中途失败 → 提案 failed 落盘，配置不切换（注册对象不可删，
    回滚 = 部署指针不切换 + 提案标记失败，research 决策 6）。
    """
    proposal = load_proposal(data_dir, proposal_id)
    if proposal.status is not ProposalStatus.PENDING:
        raise ValidationError(f"提案 {proposal_id} 状态为 {proposal.status}，仅 pending 可确认")

    current_weights = load_evaluator_weights(config_path, proposal.agent_id)
    if composite_version(current_weights) != proposal.based_version:
        raise ValidationError(
            f"基于版本已过期：提案 based_version={proposal.based_version}，"
            f"当前部署版本={composite_version(current_weights)}；须重新提案"
        )

    confirmed_at = at or datetime.now(UTC).isoformat()
    try:
        # ① composite 新版本注册（calibration 填台账最新快照）
        if registry is not None:
            ledger_latest = {
                key.split("@")[0]: snapshot
                for key in proposal.candidate_weights
                if (
                    snapshot := read_latest(
                        data_dir, proposal.agent_id, key.split("@")[0]
                    )
                )
                is not None
            }
            registry.register(
                CompositeWeightsEvaluator(
                    proposal.agent_id, proposal.candidate_weights,
                    {"ledger_latest": ledger_latest},
                )
            )
        # ② configs 定点改写（gate 行不动，注释与其他段原样保留）
        gate_keys = gate_keys_of(config_path)
        updates = {
            key: value
            for key, value in proposal.candidate_weights.items()
            if key not in gate_keys
        }
        path = Path(config_path)
        path.write_text(
            replace_section_entries(
                path.read_text(encoding="utf-8"),
                ("evaluator_weights", proposal.agent_id),
                updates,
            ),
            encoding="utf-8",
        )
    except Exception:
        # 生效中途失败：提案 → failed（已落盘），配置不切换
        save_proposal(data_dir, proposal.fail())
        raise

    # ③ 提案 confirmed（确认人/时间落盘）
    confirmed = proposal.confirm(by=by, at=confirmed_at)
    save_proposal(data_dir, confirmed)
    return composite_version(confirmed.candidate_weights)


def shelve_proposal(data_dir, proposal_id: str, *, by: str) -> WeightProposal:
    """人工搁置：提案 → shelved，注册中心与配置零变更。"""
    proposal = load_proposal(data_dir, proposal_id)
    shelved = proposal.shelve()
    save_proposal(data_dir, shelved)
    return shelved
