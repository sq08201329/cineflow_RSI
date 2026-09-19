"""输入摘要单测（T405，data-model §1.5）。

digest = 最近 K 轮做梦落盘报告摘要；读取自 dreaming/history/{agent_id}/；
哈希可复核；首轮空历史注明。
"""

import json

import blake3

from dreaming.digest import build_digest, digest_hash


def _write_round(history_root, agent_id, round_id, **overrides):
    payload = {
        "round_id": round_id,
        "agent_id": agent_id,
        "winner_version": "a1b2c3d4e5f6",
        "status": "completed",
        "candidates": [],
        "digest": {},
    }
    payload.update(overrides)
    directory = history_root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{round_id}.json").write_text(json.dumps(payload), encoding="utf-8")


class Test最近K轮:
    def test_按轮次序读最近K轮(self, tmp_path):
        for i in range(1, 6):
            _write_round(tmp_path, "promo", f"dream-promo-{i}", winner_version=f"v{i}")
        digest = build_digest("promo", tmp_path, recent_k=3)
        assert [r["round_id"] for r in digest["rounds"]] == [
            "dream-promo-3",
            "dream-promo-4",
            "dream-promo-5",
        ]
        assert digest["recent_k"] == 3

    def test_不足K轮全读(self, tmp_path):
        _write_round(tmp_path, "promo", "dream-promo-1")
        digest = build_digest("promo", tmp_path, recent_k=5)
        assert len(digest["rounds"]) == 1

    def test_摘要含胜者奖励与状态(self, tmp_path):
        _write_round(tmp_path, "promo", "dream-promo-1", winner_version="abc")
        digest = build_digest("promo", tmp_path, recent_k=5)
        assert digest["rounds"][0]["winner_version"] == "abc"
        assert digest["rounds"][0]["status"] == "completed"


class Test哈希可复核:
    def test_digest哈希确定性(self, tmp_path):
        _write_round(tmp_path, "promo", "dream-promo-1")
        d1 = build_digest("promo", tmp_path, recent_k=5)
        d2 = build_digest("promo", tmp_path, recent_k=5)
        assert digest_hash(d1) == digest_hash(d2)

    def test_哈希与内容联动(self, tmp_path):
        _write_round(tmp_path, "promo", "dream-promo-1")
        digest = build_digest("promo", tmp_path, recent_k=5)
        expected = blake3.blake3(
            json.dumps(digest, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        assert digest_hash(digest) == expected


class Test首轮空历史:
    def test_空目录注明无历史(self, tmp_path):
        digest = build_digest("promo", tmp_path, recent_k=5)
        assert digest["rounds"] == []
        assert "无历史" in digest["note"]

    def test_目录不存在也注明(self, tmp_path):
        digest = build_digest("ghost-agent", tmp_path, recent_k=5)
        assert digest["rounds"] == []
        assert "无历史" in digest["note"]
