"""契约：同源分层校验（C2 场景 3 + 分层口径，功能 013 / T1310）。

分层（2026-09-21 澄清决议）：

- **有报告产物的面板逐字段一致**（机检）——进化曲线 ← dreaming/history 轮次报告
  （以 005 的 `build_curve` 为权威读数）、信度 ← 010 报告、漂移 ← 012 报表、
  **谱系** ← 005 谱系报表（`build_lineage`；谱系面板的比对正是对冲 web/ 侧重写汇聚
  逻辑的口径漂移）；
- **树/节点接口以 DB 为权威源**：字段集与类型与 001 落库 schema 一致（无第二口径可比）。

比对实现为 `web/parity.py`（纯函数，零 import 应用层模块），差异报告为空即同源成立。
"""

import json

import pytest
from sqlalchemy import text

from web import parity
from web.queries import (
    get_evolution,
    get_lineage,
    get_node,
    get_summary,
    list_nodes,
    list_trees,
)

# ---------------------------------------------------------------------------
# 面板 → 报告文件路径映射
# ---------------------------------------------------------------------------


class Test报告路径映射:
    def test_映射到既有产物文件(self, web_config, web_fixture_rounds):
        dreaming = web_config.data_dir("dreaming") / "visual"
        assert parity.panel_report_path("evolution", web_config, agent_id="visual") == dreaming
        assert (
            parity.panel_report_path("calibration", web_config, period="2026-W39")
            == web_config.data_dir("calibration") / "reports" / "2026-W39.json"
        )
        assert (
            parity.panel_report_path("drift", web_config, period="2026-W39")
            == web_config.data_dir("calibration") / "drift" / "reports" / "2026-W39.json"
        )
        assert parity.panel_report_path("lineage", web_config) == web_config.data_dir("policies")

    def test_未知面板即报错(self, web_config):
        with pytest.raises(ValueError, match="面板"):
            parity.panel_report_path("unknown-panel", web_config)


# ---------------------------------------------------------------------------
# 进化曲线面板：接口 vs 005 曲线
# ---------------------------------------------------------------------------


class Test进化曲线同源:
    def _curve_report(self, web_config, web_data_dir):
        """005 权威读数（dreaming.lineage.build_curve，测试侧直接调用）。"""
        from dreaming.lineage import build_curve

        return build_curve(
            "visual",
            web_data_dir["dreaming"],
            collapse_window=web_config.collapse_window,
            collapse_threshold=web_config.collapse_threshold,
        ).to_dict()

    def test_逐字段一致(self, web_config, web_data_dir, web_fixture_rounds):
        response = get_evolution(web_config, "visual")
        report = self._curve_report(web_config, web_data_dir)
        assert parity.compare_evolution(response, report) == []

    def test_差异可检出(self, web_config, web_data_dir, web_fixture_rounds):
        """对冲"比对永不报错"：篡改响应即产出差异。"""
        response = get_evolution(web_config, "visual")
        report = self._curve_report(web_config, web_data_dir)
        tampered = json.loads(json.dumps(response))
        tampered["rounds"][0]["best_reward"] = 0.999
        tampered["collapse"]["window"] = 99
        diffs = parity.compare_evolution(tampered, report)
        assert any("best_reward" in diff for diff in diffs)
        assert any("window" in diff for diff in diffs)

    def test_成本逐字段一致(self, web_config, web_data_dir, web_fixture_rounds):
        """成本与轮次报告的胜出候选轨迹同口径（generation_api_cost_usd）。"""
        response = get_evolution(web_config, "visual")
        for row in response["rounds"]:
            payload = json.loads(
                (web_data_dir["dreaming"] / "visual" / f"{row['round_id']}.json").read_text("utf-8")
            )
            winner = next(c for c in payload["candidates"] if c["version"] == row["winner_version"])
            assert row["cost_usd"] == winner["trajectory"]["total_cost"]["generation_api_cost_usd"]
            assert row["best_reward"] == winner["reward"]["reward"]

    def test_塌缩标注与报告一致(self, web_config, web_data_dir, web_fixture_rounds):
        response = get_evolution(web_config, "visual")
        report = self._curve_report(web_config, web_data_dir)
        collapse = report["collapse"]
        assert response["collapse"] == collapse
        expected_flags = [
            collapse["collapsed"]
            and collapse["start_round"] <= index + 1 < collapse["start_round"] + collapse["window"]
            for index, row in enumerate(response["rounds"])
            if row["best_reward"] is not None
        ]
        assert [row["collapse_flag"] for row in response["rounds"]] == expected_flags


# ---------------------------------------------------------------------------
# 信度面板：接口 vs 010 报告
# ---------------------------------------------------------------------------


class Test信度同源:
    def test_逐字段一致(self, web_config, web_data_dir, web_reliability_report):
        response = get_summary(web_config)["calibration"]
        report = json.loads(
            (web_config.data_dir("calibration") / "reports" / "2026-W39.json").read_text("utf-8")
        )
        assert parity.compare_calibration(response, report) == []

    def test_差异可检出(self, web_config, web_reliability_report):
        response = get_summary(web_config)["calibration"]
        report = json.loads(
            (web_config.data_dir("calibration") / "reports" / "2026-W39.json").read_text("utf-8")
        )
        tampered = json.loads(json.dumps(response))
        tampered["agents"][0]["evaluators"][0]["value"] = 0.99
        assert parity.compare_calibration(tampered, report)


# ---------------------------------------------------------------------------
# 漂移面板：接口 vs 012 报表
# ---------------------------------------------------------------------------


class Test漂移同源:
    def test_逐字段一致(self, web_config, web_drift_report, web_reliability_report):
        web_drift_report()
        response = get_summary(web_config)["drift"]
        report = json.loads(
            (web_config.data_dir("calibration") / "drift" / "reports" / "2026-W39.json").read_text(
                "utf-8"
            )
        )
        assert parity.compare_drift(response, report) == []

    def test_差异可检出(self, web_config, web_drift_report):
        web_drift_report()
        response = get_summary(web_config)["drift"]
        report = json.loads(
            (web_config.data_dir("calibration") / "drift" / "reports" / "2026-W39.json").read_text(
                "utf-8"
            )
        )
        tampered = json.loads(json.dumps(response))
        tampered["items"][0]["status"] = "normal"
        assert parity.compare_drift(tampered, report)


# ---------------------------------------------------------------------------
# 谱系面板：接口 vs 005 谱系报表（对冲 web 侧重写汇聚逻辑的口径漂移）
# ---------------------------------------------------------------------------


class Test谱系同源:
    def _lineage_report(self, web_data_dir, engine, agent_id="visual"):
        from core.tree.store import create_tree_store
        from dreaming.lineage import build_lineage

        return build_lineage(
            agent_id, create_tree_store(engine), web_data_dir["policies"]
        ).to_dict()

    def test_冠军版本逐字段一致(
        self, web_config, web_data_dir, web_fixture_trees, web_lineage_files, web_tree_engine
    ):
        version = web_lineage_files["champion"]
        response = get_lineage(web_config, version)
        report = self._lineage_report(web_data_dir, web_tree_engine)
        assert parity.compare_lineage(response, report, agent_id="visual") == []

    def test_子代版本逐字段一致(
        self, web_config, web_data_dir, web_fixture_trees, web_lineage_files, web_tree_engine
    ):
        version = web_lineage_files["child"]
        response = get_lineage(web_config, version)
        report = self._lineage_report(web_data_dir, web_tree_engine)
        assert parity.compare_lineage(response, report, agent_id="visual") == []

    def test_孤儿版本一致(
        self, web_config, web_data_dir, web_fixture_trees, web_lineage_files, web_tree_engine
    ):
        version = web_lineage_files["orphan"]
        response = get_lineage(web_config, version)
        report = self._lineage_report(web_data_dir, web_tree_engine)
        assert parity.compare_lineage(response, report, agent_id="visual") == []

    def test_差异可检出(
        self, web_config, web_data_dir, web_fixture_trees, web_lineage_files, web_tree_engine
    ):
        version = web_lineage_files["child"]
        response = get_lineage(web_config, version)
        report = self._lineage_report(web_data_dir, web_tree_engine)
        tampered = json.loads(json.dumps(response))
        tampered["parents"] = []  # 丢父链
        assert parity.compare_lineage(tampered, report, agent_id="visual")
        champion = get_lineage(web_config, web_lineage_files["champion"])
        champion["trees"] = champion["trees"][:1]  # 跨项目产出树丢一棵
        assert parity.compare_lineage(champion, report, agent_id="visual")


# ---------------------------------------------------------------------------
# 树/节点接口：字段集与类型与 001 落库 schema 一致（无第二口径）
# ---------------------------------------------------------------------------


class Test树节点字段对齐_001:
    @staticmethod
    def _columns(engine, table: str) -> set[str]:
        """落库列名（sqlite_master 口径；PG 上的同义断言见 T1311 的 information_schema）。"""
        with engine.connect() as conn:
            rows = list(conn.execute(text(f"PRAGMA table_info({table})")).mappings())
        return {row["name"] for row in rows}

    def test_树清单字段对应_001_列(self, web_config, web_fixture_trees, web_tree_engine):
        columns = self._columns(web_tree_engine, "discovery_trees")
        item = list_trees(web_config)["items"][0]
        assert {"tree_id", "project_id", "agent_id", "policy_version"} <= columns
        assert {
            "tree_id",
            "project_id",
            "agent_id",
            "policy_version",
            "node_count",  # 派生自 node_ids 长度
            "created_at",  # 派生自根节点 created_at（001 无树级时间列）
            "form",  # 取自 config_snapshot["form"]
        } == set(item)

    def test_节点列表字段对应_001_列(self, web_config, web_fixture_trees, web_tree_engine):
        columns = self._columns(web_tree_engine, "tree_nodes")
        item = list_nodes(web_config, "tree-alpha-visual-champion")["items"][1]
        # 列名映射：cost(jsonb) → cost_usd（取 generation_api_cost_usd 投影）
        assert {"node_id", "parent_id", "depth", "score", "status", "created_at", "cost"} <= columns
        assert set(item) == {
            "node_id",
            "parent_id",
            "depth",
            "score",
            "cost_usd",
            "status",
            "created_at",
        }

    def test_节点详情字段类型与_001_一致(self, web_config, web_fixture_trees, web_tree_engine):
        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        with web_tree_engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT tree_id, parent_id, depth, prompt, observation_context,"
                        " artifact_hash, eval_breakdown, score, cost, status, created_at"
                        " FROM tree_nodes WHERE node_id = :node_id"
                    ),
                    {"node_id": "tree-alpha-visual-champion-n1"},
                )
                .mappings()
                .one()
            )
        assert detail["tree_id"] == row["tree_id"]
        assert detail["parent_id"] == row["parent_id"]
        assert detail["depth"] == row["depth"]
        assert detail["prompt"] == row["prompt"]
        assert detail["score"] == row["score"]
        assert detail["status"] == row["status"]
        assert detail["created_at"] == row["created_at"]
        assert detail["artifact"]["hash"] == row["artifact_hash"]
        assert detail["observation_keys"] == sorted(json.loads(row["observation_context"]))
        assert {entry["evaluator_key"] for entry in detail["eval_breakdown"]} == set(
            json.loads(row["eval_breakdown"])
        )
        assert detail["cost"] == json.loads(row["cost"])

    def test_树清单_created_at_取根节点时间戳(self, web_config, web_fixture_trees, web_tree_engine):
        item = next(
            entry
            for entry in list_trees(web_config)["items"]
            if entry["tree_id"] == "tree-alpha-visual-champion"
        )
        with web_tree_engine.connect() as conn:
            root_created = conn.execute(
                text(
                    "SELECT n.created_at FROM tree_nodes AS n JOIN discovery_trees AS t"
                    " ON t.root_id = n.node_id WHERE t.tree_id = :tree_id"
                ),
                {"tree_id": "tree-alpha-visual-champion"},
            ).scalar()
        assert item["created_at"] == root_created

    def test_node_count_等于冻结节点清单长度(self, web_config, web_fixture_trees, web_tree_engine):
        with web_tree_engine.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    text("SELECT tree_id, node_ids FROM discovery_trees")
                ).mappings()
            ]
        counts = {row["tree_id"]: len(json.loads(row["node_ids"])) for row in rows}
        for item in list_trees(web_config)["items"]:
            assert item["node_count"] == counts[item["tree_id"]]
