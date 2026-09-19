"""输入摘要组装（data-model §1.5，T406）。

digest = 最近 K 轮做梦落盘报告摘要，读取自
`dreaming/history/{agent_id}/{round_id}.json`（只增不改，git 历史承担审计）；
首轮做梦目录为空 → digest 注明"无历史"；digest 哈希入 DreamRound 可复核。
"""

import json
from pathlib import Path

import blake3

DEFAULT_HISTORY_ROOT = Path("dreaming/history")


def digest_hash(digest: dict) -> str:
    """digest 内容哈希（规范化 JSON 的 BLAKE3，可复核）。"""
    canonical = json.dumps(digest, sort_keys=True, ensure_ascii=False).encode()
    return blake3.blake3(canonical).hexdigest()


def build_digest(
    agent_id: str, history_root: str | Path = DEFAULT_HISTORY_ROOT, *, recent_k: int
) -> dict:
    """组装输入摘要：最近 K 轮报告的轮次/胜者/状态/奖励。"""
    directory = Path(history_root) / agent_id
    rounds: list[dict] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            rounds.append(
                {
                    "round_id": payload["round_id"],
                    "winner_version": payload.get("winner_version"),
                    "status": payload.get("status"),
                    "candidate_count": len(payload.get("candidates", [])),
                }
            )
    rounds = rounds[-recent_k:] if recent_k > 0 else []
    return {
        "agent_id": agent_id,
        "recent_k": recent_k,
        "rounds": rounds,
        "note": "" if rounds else "首轮做梦：无历史（dreaming/history 为空）",
    }
