"""塌缩检测与进化曲线单测（US3 / T418，research 决策 5）。

正常序列不误报、注入塌缩 100% 告警并指明 start_round、
轮数 < window 不可能塌缩、连续两轮同版本 → plateau_note。
"""

import json

import pytest

from dreaming.lineage import build_curve, detect_collapse

WINDOW, THRESHOLD = 3, 0.7


class TestDetectCollapse:
    def test_正常序列不误报(self):
        result = detect_collapse([0.5, 0.52, 0.6, 0.55, 0.58], window=WINDOW, threshold=THRESHOLD)
        assert result.collapsed is False
        assert result.start_round is None

    def test_注入塌缩_告警并指明起始轮(self):
        """baseline=0.5，阈值 0.35：第 3 轮起连续 3 轮低于阈值 → start_round=3。"""
        rewards = [0.5, 0.4, 0.3, 0.3, 0.3, 0.6]
        result = detect_collapse(rewards, window=WINDOW, threshold=THRESHOLD)
        assert result.collapsed is True
        assert result.start_round == 3  # 1-based 轮次号

    def test_塌缩起点取最早连续段(self):
        rewards = [0.5, 0.3, 0.3, 0.3, 0.3]
        result = detect_collapse(rewards, window=WINDOW, threshold=THRESHOLD)
        assert result.start_round == 2

    def test_不足window不可能塌缩(self):
        result = detect_collapse([0.5, 0.1], window=WINDOW, threshold=THRESHOLD)
        assert result.collapsed is False

    def test_空序列不塌缩(self):
        assert detect_collapse([], window=WINDOW, threshold=THRESHOLD).collapsed is False

    def test_恰好window轮塌缩(self):
        result = detect_collapse([0.5, 0.2, 0.2, 0.2], window=WINDOW, threshold=THRESHOLD)
        assert result.collapsed is True and result.start_round == 2


def _write_round(history_root, agent_id, seq, winner, reward, status="completed"):
    payload = {
        "round_id": f"dream-{agent_id}-{seq}",
        "agent_id": agent_id,
        "winner_version": winner,
        "status": status,
        "digest": {},
        "candidates": [{"version": winner, "reward": {"reward": reward}} if winner else {}],
    }
    directory = history_root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"dream-{agent_id}-{seq}.json").write_text(json.dumps(payload))


class TestBuildCurve:
    def test_曲线与基线(self, tmp_path):
        for i, (winner, reward) in enumerate([("v1", 0.4), ("v2", 0.55), ("v3", 0.6)], start=1):
            _write_round(tmp_path, "agent-dream", i, winner, reward)
        curve = build_curve("agent-dream", tmp_path, WINDOW, THRESHOLD)
        assert [r["round"] for r in curve.rounds] == [1, 2, 3]
        assert curve.rounds[0]["winner_version"] == "v1"
        assert curve.baseline_reward == pytest.approx(0.4)
        assert curve.collapse["collapsed"] is False
        assert curve.plateau_note is None

    def test_注入塌缩序列_曲线告警(self, tmp_path):
        for i, reward in enumerate([0.5, 0.3, 0.3, 0.3, 0.31], start=1):
            _write_round(tmp_path, "agent-dream", i, f"v{i}", reward)
        curve = build_curve("agent-dream", tmp_path, WINDOW, THRESHOLD)
        assert curve.collapse["collapsed"] is True
        assert curve.collapse["start_round"] == 2
        assert curve.collapse["threshold"] == THRESHOLD
        assert curve.collapse["window"] == WINDOW

    def test_连续两轮同版本_plateau_note(self, tmp_path):
        for i, winner in enumerate(["v1", "v2", "v2", "v3"], start=1):
            _write_round(tmp_path, "agent-dream", i, winner, 0.5 + i * 0.01)
        curve = build_curve("agent-dream", tmp_path, WINDOW, THRESHOLD)
        assert curve.plateau_note is not None and "v2" in curve.plateau_note

    def test_空历史_空曲线不报错(self, tmp_path):
        curve = build_curve("ghost", tmp_path, WINDOW, THRESHOLD)
        assert curve.rounds == []
        assert curve.collapse["collapsed"] is False
        assert curve.baseline_reward is None

    def test_失败轮次跳过(self, tmp_path):
        _write_round(tmp_path, "agent-dream", 1, "v1", 0.5)
        _write_round(tmp_path, "agent-dream", 2, None, 0.0, status="failed_all_rejected")
        _write_round(tmp_path, "agent-dream", 3, "v2", 0.6)
        curve = build_curve("agent-dream", tmp_path, WINDOW, THRESHOLD)
        assert [r["round"] for r in curve.rounds] == [1, 3]

    def test_曲线可序列化(self, tmp_path):
        _write_round(tmp_path, "agent-dream", 1, "v1", 0.5)
        curve = build_curve("agent-dream", tmp_path, WINDOW, THRESHOLD)
        data = json.loads(curve.to_json())
        assert set(data) == {"agent_id", "rounds", "baseline_reward", "collapse", "plateau_note"}
