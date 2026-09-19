"""盲评清单生成单测（功能 010 US1 / T509，先于实现编写；契约 C1 场景 1~4）。

- top-k 按周期内节点 score 降序；样本不足取实际数量并在 round 注明；
- 序列化键白名单 = {node_id, artifact_hash, round_id}，递归断言无 score/eval_breakdown；
- 对 agent_id="promo" 调用报错（平台真值锚点不盲评，澄清决议）。
"""

import json
from datetime import datetime, timezone

import pytest

from core.calibration.models import CalibrationRound, RoundStatus
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError

# 周期窗口：2026-09-14 ~ 2026-09-20（含尾日）
PERIOD_START = "2026-09-14"
PERIOD_END = "2026-09-20"
_BASE_TS = datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp()

_BREAKDOWN = {
    "proxy.aesthetic@1.0.0": {"score": 0.7},
    "judge.cinematic@1.0.0": {"score": 0.6},
}


def _specs(scores):
    return [(s, dict(_BREAKDOWN)) for s in scores]


def _read_round_file(data_dir, round_: CalibrationRound) -> dict:
    path = data_dir / "rounds" / round_.agent_id / f"{round_.round_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_keys(payload):
    """递归收集 JSON 载荷中的全部键（零泄露机检用）。"""
    keys = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= _walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= _walk_keys(item)
    return keys


class TestTopK降序:
    def test_周期内12节点取5条降序(
        self, tree_store, build_calibration_tree, calibration_data_dir
    ):
        scores = [0.1, 0.9, 0.5, 0.3, 0.8, 0.2, 0.95, 0.4, 0.6, 0.7, 0.15, 0.85]
        _, node_ids = build_calibration_tree(
            _specs(scores), agent_id="visual", base_created_at=_BASE_TS
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert len(round_.node_ids) == 5
        # 期望顺序 = score 降序前五
        expected = [
            node_ids[scores.index(s)] for s in sorted(scores, reverse=True)[:5]
        ]
        assert list(round_.node_ids) == expected
        assert round_.status is RoundStatus.OPEN
        assert round_.note == ""

    def test_周期外节点不入清单(self, tree_store, build_calibration_tree, calibration_data_dir):
        # 周期内 2 个 + 周期外（2025 年）1 个高分节点
        build_calibration_tree(
            _specs([0.5, 0.6]), agent_id="visual", base_created_at=_BASE_TS
        )
        build_calibration_tree(
            _specs([0.99]),
            agent_id="visual",
            base_created_at=datetime(2025, 9, 15, tzinfo=timezone.utc).timestamp(),
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert len(round_.node_ids) == 2

    def test_其他_agent_节点不入清单(
        self, tree_store, build_calibration_tree, calibration_data_dir
    ):
        build_calibration_tree(_specs([0.5]), agent_id="visual", base_created_at=_BASE_TS)
        build_calibration_tree(_specs([0.99]), agent_id="agent-other", base_created_at=_BASE_TS)
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert len(round_.node_ids) == 1


class Test样本不足:
    def test_3节点取3条并注明(self, tree_store, build_calibration_tree, calibration_data_dir):
        _, node_ids = build_calibration_tree(
            _specs([0.3, 0.9, 0.6]), agent_id="visual", base_created_at=_BASE_TS
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert len(round_.node_ids) == 3
        assert "样本不足" in round_.note
        # 落盘文件同样注明
        payload = _read_round_file(calibration_data_dir, round_)
        assert "样本不足" in payload["note"]


class Test零泄露白名单:
    def test_清单条目键白名单(self, tree_store, build_calibration_tree, calibration_data_dir):
        build_calibration_tree(_specs([0.5, 0.6]), agent_id="visual", base_created_at=_BASE_TS)
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        payload = _read_round_file(calibration_data_dir, round_)
        for entry in payload["blind_list"]:
            assert set(entry) == {"node_id", "artifact_hash", "round_id"}

    def test_递归扫描无得分键(self, tree_store, build_calibration_tree, calibration_data_dir):
        build_calibration_tree(_specs([0.5, 0.6]), agent_id="visual", base_created_at=_BASE_TS)
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            top_k=5,
            data_dir=calibration_data_dir,
        )
        payload = _read_round_file(calibration_data_dir, round_)
        leaked = _walk_keys(payload["blind_list"]) & {"score", "eval_breakdown"}
        assert not leaked


class TestPromo不盲评:
    def test_对_promo_调用报错(self, tree_store, calibration_data_dir):
        with pytest.raises(ValidationError, match="promo"):
            build_blind_list(
                tree_store,
                agent_id="promo",
                period_start=PERIOD_START,
                period_end=PERIOD_END,
                top_k=5,
                data_dir=calibration_data_dir,
            )
