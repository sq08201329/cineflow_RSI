"""门槛判定与证据快照（功能 014 US1 / T1412，契约 C2/C3 + FR-002/FR-003）。

判定优先级（契约 C2，数值越小越优先）：
`forbidden_agent`（009 名单）> `insufficient_evidence`（前置失败或要件 missing）>
`blocked`（逐要件不满足）> `eligible`（三要件同时满足）。

- **缺证据即拦截**（宁可拦截，不推测）：前置缺失/未通过、任要件 missing、以及缺 judge 的
  `not_applicable`（`allow_without_judge=false` 时）一律非 eligible；
- **禁止名单优先级最高**：即使三要件齐备也判 forbidden_agent（009 的
  `dreaming.no_auto_evolve_agents`，升级须另立决议）；
- `eligible` 是唯一放行判据（`GateVerdict.allow`）：其余判定一律不部署；
- **证据快照**（C3）：每次判定落 `deployment/evidence/{agent}/{candidate}.{ts}.json`，
  指纹只含证据取值/要件状态/判定/理由（不含时间）→ 同证据同判定**幂等**（返回既有快照，
  逐字节不变）；证据变化 → 新快照，旧快照保留（只增不改，原则二）。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import blake3

from core.deployment.config import DeploymentConfig
from core.deployment.errors import DeploymentEvidenceError
from core.deployment.models import (
    REQUIREMENTS,
    EvidenceBundle,
    EvidenceSnapshot,
    GateDecision,
    GateVerdict,
    RequirementState,
)

EVIDENCE_DIR = "evidence"


def evidence_dir(data_dir: str | Path, agent_id: str) -> Path:
    """证据快照目录：deployment/evidence/{agent}/。"""
    return Path(data_dir) / EVIDENCE_DIR / agent_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp(at: str | None) -> str:
    """时刻 → 快照文件名时间戳（秒级 UTC；非法时间即报错，不静默用当前时间）。"""
    moment = at or _now()
    try:
        parsed = datetime.fromisoformat(moment)
    except (TypeError, ValueError) as exc:
        raise DeploymentEvidenceError(f"判定时刻必须为 ISO 字符串，实际为 {moment!r}") from exc
    return parsed.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def fingerprint_of(bundle: EvidenceBundle, verdict: GateVerdict) -> str:
    """判定指纹：证据取值 + 要件状态 + 判定 + 理由（**不含时间**，故同判定可幂等）。"""
    payload = {
        "agent_id": bundle.agent_id,
        "candidate_version": bundle.candidate_version,
        "deployed_version": bundle.deployed_version,
        "prerequisite": bundle.unbiasedness.to_dict(),
        "requirements": [item.to_dict() for item in bundle.requirements],
        "decision": verdict.decision.value,
        "reason": verdict.reason,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return blake3.blake3(canonical.encode("utf-8")).hexdigest()


def _requirement_summary(item) -> str:
    return f"{item.name}（{item.state.value}）：{item.reason}"


def judge(bundle: EvidenceBundle, cfg: DeploymentConfig) -> tuple[GateDecision, str]:
    """判定核心（纯函数）：按优先级给出判定与理由（理由必须可归因到具体要件）。"""
    if not isinstance(bundle, EvidenceBundle):
        raise DeploymentEvidenceError(
            f"bundle 必须为 EvidenceBundle，实际为 {type(bundle).__name__}"
        )

    if cfg.is_forbidden(bundle.agent_id):
        return GateDecision.FORBIDDEN_AGENT, (
            "禁止自动进化名单（009 dreaming.no_auto_evolve_agents）："
            f"{bundle.agent_id} 一律判定不满足（优先级最高，即使三要件齐备；升级须另立决议）"
        )

    prerequisite = bundle.unbiasedness
    if prerequisite.state is not RequirementState.SATISFIED:
        return GateDecision.INSUFFICIENT_EVIDENCE, (
            "证据不足：前置无偏性未确立"
            f"（{prerequisite.state.value}）——{prerequisite.reason}（宁可拦截，不推测）"
        )

    missing = [item for item in bundle.requirements if item.state is RequirementState.MISSING]
    if missing:
        return GateDecision.INSUFFICIENT_EVIDENCE, (
            "证据不足：" + "；".join(_requirement_summary(item) for item in missing)
        )

    blocked = []
    for item in bundle.requirements:
        if item.state is RequirementState.UNSATISFIED:
            blocked.append(_requirement_summary(item))
        elif item.state is RequirementState.NOT_APPLICABLE and not cfg.gate.allow_without_judge:
            blocked.append(
                f"{item.name}（not_applicable）：{item.reason}"
                "；deployment.gate.allow_without_judge=false（默认保守：不适用不放宽其他要件）"
            )
    if blocked:
        return GateDecision.BLOCKED, "要件不满足：" + "；".join(blocked)

    notes = [
        f"{item.name} not_applicable（deployment.gate.allow_without_judge=true 显式放开）"
        for item in bundle.requirements
        if item.state is RequirementState.NOT_APPLICABLE
    ]
    reason = "前置无偏性通过，三要件同时满足"
    if notes:
        reason += "；" + "；".join(notes)
    return GateDecision.ELIGIBLE, reason + "：允许自动接班"


def gate(bundle: EvidenceBundle, cfg: DeploymentConfig, *, at: str | None = None) -> GateVerdict:
    """门槛判定（契约 C2）：纯函数，不落盘（落盘见 `record_snapshot`）。"""
    if not isinstance(cfg, DeploymentConfig):
        raise DeploymentEvidenceError(f"cfg 必须为 DeploymentConfig，实际为 {type(cfg).__name__}")
    decision, reason = judge(bundle, cfg)
    return GateVerdict(
        decision=decision,
        agent_id=bundle.agent_id,
        candidate_version=bundle.candidate_version,
        prerequisite=bundle.unbiasedness,
        requirements=tuple(bundle.requirement(name) for name in REQUIREMENTS),
        reason=reason,
        decided_at=at or _now(),
        allow_without_judge=cfg.gate.allow_without_judge,
    )


def load_snapshots(data_dir: str | Path, agent_id: str, candidate_version: str) -> list[dict]:
    """读取某候选的全部证据快照（按文件名升序 = 判定时间序）；无目录 → 空列表。"""
    directory = evidence_dir(data_dir, agent_id)
    if not directory.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob(f"{candidate_version}.*.json"))
    ]


def record_snapshot(
    bundle: EvidenceBundle,
    verdict: GateVerdict,
    data_dir: str | Path,
    *,
    at: str | None = None,
) -> Path:
    """证据快照落盘（契约 C3，幂等）：同指纹已存在 → 返回既有文件（零字节变化）。"""
    if not isinstance(verdict, GateVerdict):
        raise DeploymentEvidenceError(
            f"verdict 必须为 GateVerdict，实际为 {type(verdict).__name__}"
        )
    fingerprint = fingerprint_of(bundle, verdict)
    directory = evidence_dir(data_dir, bundle.agent_id)
    if directory.is_dir():
        for path in sorted(directory.glob(f"{bundle.candidate_version}.*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("fingerprint") == fingerprint:
                return path

    moment = at or _now()
    stamp = _stamp(moment)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{bundle.candidate_version}.{stamp}.json"
    suffix = 1
    while path.exists():  # 同秒内的不同证据：换文件名，绝不覆盖既有快照
        path = directory / f"{bundle.candidate_version}.{stamp}-{suffix}.json"
        suffix += 1
    snapshot = EvidenceSnapshot(
        snapshot_id=f"snap-{bundle.agent_id}-{bundle.candidate_version}-{stamp}",
        agent_id=bundle.agent_id,
        candidate_version=bundle.candidate_version,
        fingerprint=fingerprint,
        bundle=bundle,
        verdict=verdict,
        created_at=moment,
    )
    path.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def gate_and_record(
    bundle: EvidenceBundle,
    cfg: DeploymentConfig,
    data_dir: str | Path,
    *,
    at: str | None = None,
) -> tuple[GateVerdict, Path]:
    """判定 + 落快照（唯一入口用）：返回（判定，快照路径）。"""
    verdict = gate(bundle, cfg, at=at)
    return verdict, record_snapshot(bundle, verdict, data_dir, at=at)
