"""成本回归每日告警核心逻辑单测（宪章门禁：相同回放任务成本突增 >20% → 告警）。

覆盖 ops/cost_regression.py 的纯函数：round_replay_cost_usd / load_cost_series /
check_agent_series / run_check / CostRegressionConfig。
"相同回放任务"口径：同一 agent_id 的做梦回放轮次序列（DreamRound 落盘 JSON），
成本 = 该轮全部成功回放轨迹 generation_api_cost_usd 合计（与三方对账同一 USD 口径）。
"""

import json

import pytest

from ops.cost_regression import (
    CostRegressionConfig,
    CostRegressionConfigError,
    check_agent_series,
    load_cost_series,
    main,
    round_replay_cost_usd,
    run_check,
)


def _candidate(cost_usd: float | None) -> dict:
    """构造候选条目：cost_usd 为 None 表示回放失败（trajectory=None）。"""
    if cost_usd is None:
        return {"version": "v-failed", "static_check": "passed", "trajectory": None}
    return {
        "version": f"v-{cost_usd}",
        "static_check": "passed",
        "trajectory": {"total_cost": {"generation_api_cost_usd": cost_usd}},
    }


def _round_payload(round_id: str, agent_id: str, costs: list[float]) -> dict:
    """构造 DreamRound 落盘 JSON 形态（只保留成本回归所需字段）。"""
    return {
        "round_id": round_id,
        "agent_id": agent_id,
        "status": "completed",
        "candidates": [_candidate(c) for c in costs],
    }


def _write_round(history_root, agent_id: str, seq: int, costs: list[float]) -> None:
    """把一轮做梦报告落盘到临时 history 目录（模拟 dreaming/history 形态）。"""
    directory = history_root / agent_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = _round_payload(f"dream-{agent_id}-{seq}", agent_id, costs)
    (directory / f"dream-{agent_id}-{seq}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class TestRoundReplayCost:
    def test_合计全部成功轨迹成本(self):
        payload = _round_payload("dream-a-1", "a", [1.0, 2.5])
        assert round_replay_cost_usd(payload) == pytest.approx(3.5)

    def test_回放失败候选不计入(self):
        payload = _round_payload("dream-a-1", "a", [1.0])
        payload["candidates"].append(_candidate(None))
        assert round_replay_cost_usd(payload) == pytest.approx(1.0)

    def test_无成功轨迹返回None(self):
        payload = _round_payload("dream-a-1", "a", [])
        payload["candidates"].append(_candidate(None))
        assert round_replay_cost_usd(payload) is None


class TestLoadCostSeries:
    def test_按轮次序号升序且跳过无效轮次(self, tmp_path):
        root = tmp_path / "history"
        _write_round(root, "a", 2, [2.0])
        _write_round(root, "a", 10, [10.0])  # 两位数序号：验证按数值而非字典序排序
        _write_round(root, "a", 1, [1.0])
        _write_round(root, "a", 3, [])  # 无成功轨迹：跳过
        series = load_cost_series(root, "a")
        assert series == [("dream-a-1", 1.0), ("dream-a-2", 2.0), ("dream-a-10", 10.0)]


class TestCheckAgentSeries:
    def test_增幅19不告警(self):
        series = [("dream-a-1", 100.0), ("dream-a-2", 119.0)]
        result = check_agent_series("a", series, threshold=0.2)
        assert result["alert"] is False
        assert result["increase_ratio"] == pytest.approx(0.19)

    def test_增幅21告警(self):
        series = [("dream-a-1", 100.0), ("dream-a-2", 121.0)]
        result = check_agent_series("a", series, threshold=0.2)
        assert result["alert"] is True
        assert result["increase_ratio"] == pytest.approx(0.21)
        assert result["baseline_round"] == "dream-a-1"
        assert result["latest_round"] == "dream-a-2"

    def test_恰好阈值不告警(self):
        """门禁口径为"突增 > 20%"：严格大于才告警，等于阈值放行。"""
        series = [("dream-a-1", 100.0), ("dream-a-2", 120.0)]
        assert check_agent_series("a", series, threshold=0.2)["alert"] is False

    def test_首轮无历史基线不告警(self):
        result = check_agent_series("a", [("dream-a-1", 100.0)], threshold=0.2)
        assert result["alert"] is False
        assert result["increase_ratio"] is None
        assert "基线" in result["note"]

    def test_基线成本为零跳过(self):
        """基线为 0 时增幅无定义：跳过而非误报。"""
        result = check_agent_series("a", [("dream-a-1", 0.0), ("dream-a-2", 5.0)], threshold=0.2)
        assert result["alert"] is False
        assert result["increase_ratio"] is None

    def test_比较对象为最近两轮(self):
        """基线 = 前一轮（非滑动窗口）：三轮序列只取最后两轮比较。"""
        series = [("dream-a-1", 50.0), ("dream-a-2", 100.0), ("dream-a-3", 110.0)]
        result = check_agent_series("a", series, threshold=0.2)
        assert result["alert"] is False
        assert result["baseline_round"] == "dream-a-2"


class TestRunCheck:
    def test_多任务标识各自独立判定(self, tmp_path):
        """agent-x 超阈告警、agent-y 正常：互不影响，告警只挂超阈者。"""
        root = tmp_path / "history"
        _write_round(root, "agent-x", 1, [100.0])
        _write_round(root, "agent-x", 2, [121.0])  # +21% → 告警
        _write_round(root, "agent-y", 1, [100.0])
        _write_round(root, "agent-y", 2, [119.0])  # +19% → 放行

        report = run_check(root, threshold=0.2)
        assert report["ok"] is False
        assert len(report["alerts"]) == 1
        assert report["alerts"][0]["agent_id"] == "agent-x"
        agents = {a["agent_id"]: a for a in report["agents"]}
        assert agents["agent-x"]["alert"] is True
        assert agents["agent-y"]["alert"] is False

    def test_全部正常时ok且无告警(self, tmp_path):
        root = tmp_path / "history"
        _write_round(root, "agent-y", 1, [100.0])
        _write_round(root, "agent-y", 2, [110.0])
        report = run_check(root, threshold=0.2)
        assert report["ok"] is True
        assert report["alerts"] == []

    def test_空历史目录放行(self, tmp_path):
        """尚无做梦落盘数据时不误报（CI 新环境形态）。"""
        report = run_check(tmp_path / "history", threshold=0.2)
        assert report["ok"] is True
        assert report["agents"] == []


class TestCostRegressionConfig:
    def test_读取阈值与落盘根目录(self):
        config = CostRegressionConfig.from_dict(
            {"cost_regression": {"threshold": 0.25, "history_root": "data/history"}}
        )
        assert config.threshold == pytest.approx(0.25)
        assert config.history_root == "data/history"

    def test_缺配置段报错(self):
        with pytest.raises(CostRegressionConfigError, match="cost_regression"):
            CostRegressionConfig.from_dict({})

    def test_阈值非正报错(self):
        with pytest.raises(CostRegressionConfigError, match="threshold"):
            CostRegressionConfig.from_dict({"cost_regression": {"threshold": 0.0}})

    def test_真实movie配置可加载(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        config = CostRegressionConfig.from_yaml(repo_root / "configs" / "movie.yaml")
        assert config.threshold == pytest.approx(0.2)
        assert config.history_root == "dreaming/history"


class TestMain:
    def test_无告警退出码0(self, tmp_path, capsys):
        root = tmp_path / "history"
        _write_round(root, "a", 1, [100.0])
        _write_round(root, "a", 2, [110.0])
        code = main(["--history-root", str(root), "--threshold", "0.2"])
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is True

    def test_有告警退出码1且打印告警明细(self, tmp_path, capsys):
        root = tmp_path / "history"
        _write_round(root, "a", 1, [100.0])
        _write_round(root, "a", 2, [121.0])
        code = main(["--history-root", str(root), "--threshold", "0.2"])
        assert code == 1
        report = json.loads(capsys.readouterr().out)
        assert report["alerts"][0]["agent_id"] == "a"
