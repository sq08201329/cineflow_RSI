"""看板查询测试（功能 013 / T1317，先于实现编写；契约 C7）。

覆盖：

- **逐轮 reward 序列与 dreaming/history 逐字段一致**（读轮次报告的胜出候选，不重算）；
- **塌缩标注**（005 判定规则 + 配置驱动的窗口/阈值，随配置改变而改变）；
- **成本汇总与 CostRecord 聚合对账一致**：按 (Agent, 周期) 合计 `generation_api_cost_usd`，
  期望值在测试侧用 core 的 TreeStore 独立聚合（同一 CostRecord 口径）；
- **摘要徽标**：010 信度达标状态 + 012 漂移状态（normal/suspect/confirmed_drift）；
  缺失一律如实空态。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from web.queries import get_costs, get_evolution, get_summary

AGENT = "visual"
JUDGE_KEY = "judge.cinematic@1.0.0"
PERIOD = "2026-W39"


def _iso_period(created_at: float) -> str:
    """周期口径：节点时间戳（001 Float）→ ISO 周标签（%G-W%V，UTC），与 010/012 同形。"""
    return datetime.fromtimestamp(created_at, UTC).strftime("%G-W%V")


@pytest.fixture()
def expected_costs(web_tree_engine, web_fixture_trees):
    """core 侧独立聚合（对账权威口径）：(agent, ISO 周) → 成本合计 / 节点数。"""

    def _aggregate(agent_id=None):
        from core.tree.store import create_tree_store

        store = create_tree_store(web_tree_engine)
        buckets: dict[tuple[str, str], dict] = {}
        for node_ids in web_fixture_trees.values():
            for node_id in node_ids:
                node = store.get_node(node_id)
                if agent_id is not None and node.agent_id != agent_id:
                    continue
                key = (node.agent_id, _iso_period(node.created_at))
                bucket = buckets.setdefault(key, {"cost_usd": 0.0, "node_count": 0})
                bucket["cost_usd"] += node.cost.generation_api_cost_usd
                bucket["node_count"] += 1
        return buckets

    return _aggregate


class TestC7进化曲线:
    def test_逐轮_reward_与轮次报告逐字段一致(self, web_config, web_data_dir, web_fixture_rounds):
        payload = get_evolution(web_config, AGENT)
        for row in payload["rounds"]:
            report = (web_data_dir["dreaming"] / AGENT / f"{row['round_id']}.json").read_text(
                encoding="utf-8"
            )
            source = json.loads(report)
            winner = next(
                candidate
                for candidate in source["candidates"]
                if candidate["version"] == row["winner_version"]
            )
            assert row["best_reward"] == winner["reward"]["reward"]
            assert row["cost_usd"] == winner["trajectory"]["total_cost"]["generation_api_cost_usd"]
        assert [row["round"] for row in payload["rounds"]] == [1, 2, 3, 4]

    def test_塌缩标注存在且随配置改变(self, web_config, web_fixture_rounds):
        payload = get_evolution(web_config, AGENT)
        assert payload["collapse"]["collapsed"] is True
        assert payload["collapse"]["start_round"] == 2
        assert [row["collapse_flag"] for row in payload["rounds"]] == [False, True, True, True]
        # 口径可证伪：阈值放宽到 0.05 → 不再判塌缩（窗口/阈值来自配置）
        widened = get_evolution(replace(web_config, collapse_threshold=0.05), AGENT)
        assert widened["collapse"]["collapsed"] is False
        assert [row["collapse_flag"] for row in widened["rounds"]] == [False] * 4

    def test_失败轮不进曲线(self, web_config, web_fixture_rounds):
        payload = get_evolution(web_config, AGENT)
        assert all(row["round_id"] != "dream-visual-5" for row in payload["rounds"])

    def test_按_Agent_分线互不串线(self, web_config, web_data_dir, write_dream_rounds):
        write_dream_rounds(
            [{"round_id": "dream-storyboard-1", "curve": [0.1, 0.2], "cost_usd": 0.7}],
            agent_id="storyboard",
        )
        visual = get_evolution(web_config, AGENT)
        storyboard = get_evolution(web_config, "storyboard")
        assert visual["rounds"] == []
        assert [row["round_id"] for row in storyboard["rounds"]] == ["dream-storyboard-1"]

    def test_无轮次数据空态(self, web_config, web_data_dir):
        payload = get_evolution(web_config, "agent-none")
        assert payload["rounds"] == []
        assert payload["baseline_reward"] is None
        assert payload["collapse"]["collapsed"] is False


class TestC7成本汇总:
    def test_按_Agent_周期合计与_CostRecord_对账一致(
        self, web_config, web_fixture_trees, expected_costs
    ):
        payload = get_costs(web_config)
        expected = expected_costs()
        assert {(row["agent_id"], row["period"]) for row in payload["items"]} == set(expected)
        for row in payload["items"]:
            bucket = expected[(row["agent_id"], row["period"])]
            assert row["cost_usd"] == pytest.approx(bucket["cost_usd"])
            assert row["node_count"] == bucket["node_count"]

    def test_合计等于全节点成本之和(self, web_config, web_fixture_trees, expected_costs):
        payload = get_costs(web_config)
        expected = expected_costs()
        assert payload["total_usd"] == pytest.approx(
            sum(bucket["cost_usd"] for bucket in expected.values())
        )
        assert payload["node_count"] == sum(bucket["node_count"] for bucket in expected.values())
        assert payload["total_usd"] == pytest.approx(
            sum(row["cost_usd"] for row in payload["agents"])
        )

    def test_Agent_过滤与小计(self, web_config, web_fixture_trees, expected_costs):
        payload = get_costs(web_config, agent_id="storyboard")
        expected = {key: value for key, value in expected_costs().items() if key[0] == "storyboard"}
        assert {(row["agent_id"], row["period"]) for row in payload["items"]} == set(expected)
        assert {row["agent_id"] for row in payload["agents"]} == {"storyboard"}

    def test_周期口径为_ISO_周(self, web_config, web_fixture_trees):
        payload = get_costs(web_config)
        # 夹具节点时间戳 1000s / 1010s … → 1970-01-01（ISO 1970-W01）
        assert payload["periods"] == ["1970-W01"]

    def test_无节点空态(self, web_config, web_data_dir):
        payload = get_costs(web_config)
        assert payload == {
            "items": [],
            "agents": [],
            "periods": [],
            "total_usd": 0.0,
            "node_count": 0,
        }

    def test_成本汇总路由(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/costs")
        assert status == 200
        assert set(payload) == {"items", "agents", "periods", "total_usd", "node_count"}
        assert payload["node_count"] == 8  # 夹具树共 8 个节点

    def test_成本汇总按_Agent_参数(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/costs?agent_id=visual")
        assert status == 200
        assert {row["agent_id"] for row in payload["agents"]} == {AGENT}
        assert payload["node_count"] == 6


class TestC7摘要徽标:
    def test_信度达标状态与漂移徽标(self, web_config, web_reliability_report, web_drift_report):
        web_drift_report()
        payload = get_summary(web_config)
        calibration = payload["calibration"]
        assert calibration["period"] == PERIOD
        assert calibration["target"] == 0.6
        assert calibration["meets"] is False
        drift = payload["drift"]
        assert drift["period"] == PERIOD
        assert drift["items"] == [
            {"evaluator_key": JUDGE_KEY, "status": "suspect", "since": "2026-09-21T10:00:00+00:00"}
        ]
        assert drift["alerts"], "超阈漂移应产生告警"

    def test_确认漂移后的徽标(
        self,
        web_config,
        web_data_dir,
        web_reliability_report,
        web_drift_report,
        drift_config,
    ):
        """人工确认漂移（012 处置通路）→ 徽标变 confirmed_drift（终态如实呈现）。"""
        from core.calibration.drift_models import DriftAction, DriftConclusion
        from core.calibration.drift_report import build_report
        from core.calibration.drift_status import dispose

        base = web_data_dir["calibration"]
        web_drift_report()
        dispose(
            base,
            JUDGE_KEY,
            DriftConclusion.CONFIRMED_DRIFT,
            by="ops-user",
            reason="锚点分布确认漂移，停用该 judge",
            action=DriftAction.DEACTIVATE,
            at="2026-09-21T11:00:00+00:00",
        )
        build_report(PERIOD, drift_config, base)
        items = get_summary(web_config)["drift"]["items"]
        assert items == [
            {
                "evaluator_key": JUDGE_KEY,
                "status": "confirmed_drift",
                "since": "2026-09-21T11:00:00+00:00",
            }
        ]

    def test_全部缺失空态(self, web_config, web_data_dir):
        payload = get_summary(web_config)
        assert payload["calibration"]["agents"] == []
        assert payload["calibration"]["meets"] is None
        assert payload["calibration"]["period"] is None
        assert payload["drift"]["items"] == []
        assert payload["drift"]["alerts"] == []
        assert payload["drift"]["period"] is None

    def test_无漂移登记时徽标为_normal(
        self, web_config, web_data_dir, web_reliability_report, drift_config
    ):
        """有信度无漂移登记：报表 items 的状态为 normal（从未检出漂移，如实呈现）。"""
        from core.calibration.drift_report import build_report

        base = web_data_dir["calibration"]
        build_report(PERIOD, drift_config, base)
        drift = get_summary(web_config)["drift"]
        assert {item["status"] for item in drift["items"]} in (set(), {"normal"})
        assert drift["alerts"] == []
