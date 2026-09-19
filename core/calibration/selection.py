"""top-k 盲评清单生成（功能 010 US1，契约 C1）。

- 按周期内节点 score 降序取 top-k；样本不足取实际数量并在 round 注明；
- 零泄露：清单条目序列化键白名单 = {node_id, artifact_hash, round_id}，
  score / eval_breakdown 永不出现在清单中（FR-002，契约测试机检）；
- 轮次落盘 calibration/rounds/{agent_id}/{round_id}.json（状态 open）；
- promo 不盲评：其锚点为 platform_truth 回流，调用即 ValidationError（澄清决议）。
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.calibration.models import CalibrationRound
from core.evaluators.errors import ValidationError
from core.tree.models import new_id
from core.tree.store import TreeStore

# 盲评清单条目序列化键白名单（防锚定：人评看不到自动得分）
BLIND_LIST_KEYS = frozenset({"node_id", "artifact_hash", "round_id"})

# 仅采用人评盲评锚点的 Agent 之外的黑名单（promo 锚点走平台真值回流）
_NO_BLIND_AGENTS = frozenset({"promo"})


def _period_window(period_start: str, period_end: str) -> tuple[float, float]:
    """ISO 日期（YYYY-MM-DD，含首尾）→ created_at 秒级窗口 [start, end)。"""
    try:
        start = datetime.fromisoformat(period_start).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(period_end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    except ValueError as exc:
        raise ValidationError(f"周期必须为 ISO 日期（YYYY-MM-DD）：{exc}") from exc
    return start.timestamp(), end.timestamp()


def round_path(data_dir: str | Path, agent_id: str, round_id: str) -> Path:
    """轮次落盘路径：rounds/{agent_id}/{round_id}.json。"""
    return Path(data_dir) / "rounds" / agent_id / f"{round_id}.json"


def save_round(
    data_dir: str | Path, round_: CalibrationRound, blind_list: list[dict]
) -> Path:
    """轮次 + 盲评清单落盘（条目经键白名单过滤，零泄露的最后防线）。"""
    path = round_path(data_dir, round_.agent_id, round_.round_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        {key: entry[key] for key in sorted(BLIND_LIST_KEYS)} for entry in blind_list
    ]
    payload = {
        "round_id": round_.round_id,
        "agent_id": round_.agent_id,
        "period_start": round_.period_start,
        "period_end": round_.period_end,
        "top_k": round_.top_k,
        "status": round_.status.value,
        "note": round_.note,
        "blind_list": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_round(data_dir: str | Path, round_id: str) -> tuple[CalibrationRound, list[dict]]:
    """按 round_id 检索 rounds/*/ 并还原轮次与清单；不存在 → ValidationError。"""
    rounds_dir = Path(data_dir) / "rounds"
    matches = sorted(rounds_dir.glob(f"*/{round_id}.json")) if rounds_dir.is_dir() else []
    if not matches:
        raise ValidationError(f"校准轮次不存在：{round_id}")
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    round_ = CalibrationRound(
        round_id=payload["round_id"],
        agent_id=payload["agent_id"],
        period_start=payload["period_start"],
        period_end=payload["period_end"],
        top_k=payload["top_k"],
        node_ids=tuple(entry["node_id"] for entry in payload["blind_list"]),
        status=payload["status"],
        note=payload.get("note", ""),
    )
    return round_, payload["blind_list"]


def build_blind_list(
    store: TreeStore,
    *,
    agent_id: str,
    period_start: str,
    period_end: str,
    top_k: int,
    data_dir: str | Path,
    round_id: str | None = None,
) -> CalibrationRound:
    """生成 top-k 盲评清单并落盘轮次（状态 open）。

    按周期内得分节点 score 降序取 top-k（平分按 node_id 字典序保证确定性）；
    样本不足取实际数量并在 round.note 注明。
    """
    if agent_id in _NO_BLIND_AGENTS:
        raise ValidationError(f"{agent_id} 的锚点为平台真值回流，不参与盲评")
    if top_k < 1:
        raise ValidationError(f"top_k 必须为 ≥ 1 的整数，实际为 {top_k!r}")
    start_ts, end_ts = _period_window(period_start, period_end)

    candidates = []
    for tree in store.trees_by(agent_id=agent_id):
        for node in store.nodes_of(tree.tree_id):
            if node.score is None or not start_ts <= node.created_at < end_ts:
                continue
            candidates.append(node)
    candidates.sort(key=lambda n: (-n.score, n.node_id))
    selected = candidates[:top_k]

    round_ = CalibrationRound(
        round_id=round_id or new_id(),
        agent_id=agent_id,
        period_start=period_start,
        period_end=period_end,
        top_k=top_k,
        node_ids=tuple(node.node_id for node in selected),
        note=f"样本不足：周期内仅 {len(selected)} 个得分节点" if len(selected) < top_k else "",
    )
    blind_list = [
        {"node_id": node.node_id, "artifact_hash": node.artifact_hash, "round_id": round_.round_id}
        for node in selected
    ]
    save_round(data_dir, round_, blind_list)
    return round_
