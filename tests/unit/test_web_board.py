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

from web.queries import WebQueryError, get_costs, get_evolution, get_summary

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


# ---------------------------------------------------------------------------
# 功能 022 / T2218：get_costs 新增 group_by="role_profile"（plan D3 三态区分）
# ---------------------------------------------------------------------------

_ROLE = "screenwriter"
_PROFILE = "p-fast"


def _cost_json(*, llm_calls, llm_tokens, cost_usd, breakdown=None, with_key=True):
    """成本记录原始 JSON：with_key=False 复现历史行（llm_breakdown 键缺席）。"""
    record = {
        "llm_calls": llm_calls,
        "llm_tokens": llm_tokens,
        "generation_api_calls": llm_calls,
        "generation_api_cost_usd": cost_usd,
        "human_review_minutes": 0.0,
        "wall_clock_seconds": 0.5,
    }
    if with_key:
        record["llm_breakdown"] = breakdown if breakdown is not None else {}
    return record


def _breakdown_entry(calls, prompt, completion, cost):
    return {
        "calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_usd": cost,
    }


@pytest.fixture()
def cost_nodes(web_tree_engine):
    """022 成本分解夹具：占位树 + 逐节点成本 JSON 直写（可控 llm_breakdown 键在场性）。

    直写 SQL 而不经 TreeStore：新 CostRecord 序列化恒带 llm_breakdown 键，
    历史行缺键形态只能由原始 JSON 构造（D3 三态之「未标定」的复现路径）。
    """
    from sqlalchemy import insert

    from core.tree.db import discovery_trees, tree_nodes

    with web_tree_engine.begin() as conn:
        conn.execute(
            insert(discovery_trees).values(
                tree_id="tree-cost-022",
                project_id="proj-cost",
                agent_id="visual",
                policy_version="a1b2c3d4e5f6",
                root_id="cost-022-n0",
                node_ids=[],
                config_snapshot={},
            )
        )

    counter = {"n": 0}

    def _add(cost: dict, *, agent_id: str = "visual") -> str:
        counter["n"] += 1
        node_id = f"cost-022-n{counter['n']}"
        with web_tree_engine.begin() as conn:
            conn.execute(
                insert(tree_nodes).values(
                    node_id=node_id,
                    tree_id="tree-cost-022",
                    parent_id=None,
                    depth=0,
                    agent_id=agent_id,
                    policy_version="a1b2c3d4e5f6",
                    prompt="",
                    observation_context={},
                    artifact_hash="ab" * 32,
                    eval_breakdown={},
                    score=0.5,
                    status="evaluated",
                    cost=cost,
                    created_at=1000.0 + counter["n"],
                )
            )
        return node_id

    return _add


class Test角色档案成本分解:
    """功能 022 / T2218：`get_costs(group_by="role_profile")` 分组（plan D3/D5）。

    分组值直接取自原始 JSON 的 llm_breakdown 键（与网关 cost_breakdown() 同键）；
    「未标定」（历史行缺键）单列、不摊入任何分组，与「缓存命中（零计费）」不混标。
    """

    def test_分组值取分解同键且逐格对账(self, web_config, cost_nodes):
        cost_nodes(
            _cost_json(
                llm_calls=2,
                llm_tokens=150,
                cost_usd=0.05,
                breakdown={_ROLE: {_PROFILE: _breakdown_entry(2, 100, 50, 0.03)}},
            )
        )
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=50,
                cost_usd=0.02,
                breakdown={_ROLE: {_PROFILE: _breakdown_entry(1, 40, 10, 0.01)}},
            )
        )
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=30,
                cost_usd=0.02,
                breakdown={"judge": {"p-judge": _breakdown_entry(1, 20, 10, 0.02)}},
            )
        )
        payload = get_costs(web_config, group_by="role_profile")
        attributed = {
            (item["role"], item["profile_id"]): item
            for item in payload["items"]
            if item["state"] == "attributed"
        }
        assert set(attributed) == {(_ROLE, _PROFILE), ("judge", "p-judge")}
        entry = attributed[(_ROLE, _PROFILE)]
        assert entry["calls"] == 3
        assert entry["prompt_tokens"] == 140
        assert entry["completion_tokens"] == 60
        assert entry["cost_usd"] == pytest.approx(0.04)
        assert entry["node_count"] == 2
        assert attributed[("judge", "p-judge")]["node_count"] == 1
        assert payload["node_count"] == 3
        assert payload["total_cost_usd"] == pytest.approx(0.06)

    def test_历史行未标定单列不摊入任何分组(self, web_config, cost_nodes):
        cost_nodes(_cost_json(llm_calls=2, llm_tokens=120, cost_usd=0.05, with_key=False))
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=50,
                cost_usd=0.02,
                breakdown={_ROLE: {_PROFILE: _breakdown_entry(1, 40, 10, 0.02)}},
            )
        )
        payload = get_costs(web_config, group_by="role_profile")
        uncalibrated = [item for item in payload["items"] if item["state"] == "uncalibrated"]
        assert len(uncalibrated) == 1
        item = uncalibrated[0]
        assert item["role"] is None and item["profile_id"] is None
        assert item["node_count"] == 1
        assert item["calls"] == 2  # 计数取六字段 llm_calls 合计
        assert item["cost_usd"] is None  # 历史行无 LLM 专属金额标量，如实为 null
        attributed = [i for i in payload["items"] if i["state"] == "attributed"]
        assert sum(i["node_count"] for i in attributed) == 1

    def test_缓存命中零计费与未标定不混标(self, web_config, cost_nodes):
        # 键在场且空、llm_calls>0 → 缓存命中（零计费）；键缺席 → 未标定（历史行）
        cost_nodes(_cost_json(llm_calls=1, llm_tokens=80, cost_usd=0.0, breakdown={}))
        cost_nodes(_cost_json(llm_calls=1, llm_tokens=60, cost_usd=0.03, with_key=False))
        payload = get_costs(web_config, group_by="role_profile")
        states = {item["state"]: item for item in payload["items"]}
        assert set(states) == {"cache_hit_zero_cost", "uncalibrated"}
        hit = states["cache_hit_zero_cost"]
        assert hit["cost_usd"] == 0.0
        assert hit["calls"] == 1
        assert hit["node_count"] == 1
        assert states["uncalibrated"]["node_count"] == 1

    def test_未标定组计数与占比可断言取得(self, web_config, cost_nodes):
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=50,
                cost_usd=0.02,
                breakdown={_ROLE: {_PROFILE: _breakdown_entry(1, 40, 10, 0.02)}},
            )
        )
        cost_nodes(_cost_json(llm_calls=1, llm_tokens=80, cost_usd=0.0, breakdown={}))
        cost_nodes(_cost_json(llm_calls=3, llm_tokens=60, cost_usd=0.03, with_key=False))
        payload = get_costs(web_config, group_by="role_profile")
        uncalibrated = next(item for item in payload["items"] if item["state"] == "uncalibrated")
        assert payload["node_count"] == 3
        assert uncalibrated["node_count"] / payload["node_count"] == pytest.approx(1 / 3)

    def test_无_LLM_调用节点单列不入缓存命中(self, web_config, cost_nodes):
        # 键在场且空、llm_calls==0 → 无 LLM 腿（与缓存命中不混标，如实单列）
        cost_nodes(_cost_json(llm_calls=0, llm_tokens=0, cost_usd=0.0, breakdown={}))
        cost_nodes(_cost_json(llm_calls=1, llm_tokens=10, cost_usd=0.0, breakdown={}))
        payload = get_costs(web_config, group_by="role_profile")
        states = {item["state"]: item for item in payload["items"]}
        assert set(states) == {"no_llm", "cache_hit_zero_cost"}
        assert states["no_llm"]["calls"] == 0
        assert states["no_llm"]["node_count"] == 1

    def test_Agent_过滤仍生效(self, web_config, cost_nodes):
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=50,
                cost_usd=0.02,
                breakdown={_ROLE: {_PROFILE: _breakdown_entry(1, 40, 10, 0.02)}},
            )
        )
        cost_nodes(
            _cost_json(
                llm_calls=1,
                llm_tokens=30,
                cost_usd=0.01,
                breakdown={"judge": {"p-judge": _breakdown_entry(1, 20, 10, 0.01)}},
            ),
            agent_id="storyboard",
        )
        payload = get_costs(web_config, agent_id="storyboard", group_by="role_profile")
        assert payload["node_count"] == 1
        assert {(i["role"], i["profile_id"]) for i in payload["items"]} == {("judge", "p-judge")}

    def test_未知分组维度报错(self, web_config):
        with pytest.raises(WebQueryError, match="分组维度"):
            get_costs(web_config, group_by="bogus")


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
