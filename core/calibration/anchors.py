"""人评锚点录入通道（功能 010 US1，契约 C2）。

- 逐条校验：score ∈ [0,1]、reviewer 非空、node_id 在该轮盲评清单内（防录错节点）；
- 轮次门禁：round 存在且状态 ∈ {open, intake}（closed 拒绝录入）；
- 同 (node_id, reviewer, round_id) 唯一冲突 → 该条拒绝并计数，整批不中断（FR-003 幂等）；
- 录入后轮次 open → intake；锚点写入即冻结（存储层触发器，原则一/二）。

insert_anchor 是锚点写入的唯一接口：平台真值适配（agents/promo/anchors.py）
与人评录入共用同一条 INSERT 路径与幂等语义。
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Connection, insert
from sqlalchemy.exc import IntegrityError

from core.calibration.db import calibration_anchors
from core.calibration.models import AnchorScore, RoundStatus
from core.calibration.selection import load_round, save_round
from core.evaluators.errors import ValidationError
from core.tree.models import new_id


def insert_anchor(conn: Connection, anchor: AnchorScore) -> bool:
    """写入一条锚点；同键冲突（唯一约束）→ 返回 False（幂等拒绝），不中断事务。"""
    try:
        with conn.begin_nested():
            conn.execute(
                insert(calibration_anchors).values(
                    anchor_id=anchor.anchor_id,
                    node_id=anchor.node_id,
                    artifact_hash=anchor.artifact_hash,
                    agent_id=anchor.agent_id,
                    source=anchor.source.value,
                    score=anchor.score,
                    reviewer=anchor.reviewer,
                    round_id=anchor.round_id,
                    created_at=anchor.created_at,
                )
            )
    except IntegrityError:
        return False
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def intake_anchors(
    conn: Connection,
    round_id: str,
    entries: list[dict],
    *,
    data_dir: str | Path,
    rejections: list | None = None,
) -> int:
    """人评录入：校验 → INSERT（source=human_blind）→ 轮次转 intake。

    返回入库条数；rejections（可选）逐条收集 {"entry", "reason"} 供 CLI 汇总。
    单条非法/重复不中断整批；轮次缺失或已 closed 则整批拒绝（ValidationError）。
    """
    round_, blind_list = load_round(data_dir, round_id)  # 不存在 → ValidationError
    if round_.status not in (RoundStatus.OPEN, RoundStatus.INTAKE):
        raise ValidationError(f"轮次 {round_id} 状态为 {round_.status}，禁止录入")

    artifact_by_node = {entry["node_id"]: entry["artifact_hash"] for entry in blind_list}

    def _reject(entry: dict, reason: str) -> None:
        if rejections is not None:
            rejections.append({"entry": entry, "reason": reason})

    accepted = 0
    for entry in entries:
        node_id = entry.get("node_id")
        score = entry.get("score")
        reviewer = entry.get("reviewer")
        if node_id not in artifact_by_node:
            _reject(entry, f"node_id 不在该轮盲评清单内：{node_id!r}")
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            _reject(entry, f"score 必须 ∈ [0,1]，实际为 {score!r}")
            continue
        if not isinstance(reviewer, str) or not reviewer:
            _reject(entry, f"reviewer 必须为非空字符串，实际为 {reviewer!r}")
            continue
        anchor = AnchorScore(
            anchor_id=new_id(),
            node_id=node_id,
            artifact_hash=artifact_by_node[node_id],
            agent_id=round_.agent_id,
            source="human_blind",
            score=float(score),
            reviewer=reviewer,
            round_id=round_id,
            created_at=_now_iso(),
        )
        if insert_anchor(conn, anchor):
            accepted += 1
        else:
            _reject(entry, f"同键重复（幂等拒绝）：({node_id}, {reviewer}, {round_id})")

    # 首轮录入后轮次 open → intake（清单不变，仅状态迁移落盘）
    if round_.status is RoundStatus.OPEN and (accepted or rejections):
        save_round(data_dir, round_.transition(RoundStatus.INTAKE), blind_list)
    return accepted
