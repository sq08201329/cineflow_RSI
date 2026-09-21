"""只读查询层单测（功能 013 / T1305，先于实现编写）。

覆盖六类查询（树清单/节点列表/节点详情/谱系/曲线/摘要）+ health 的字段 schema、空态、
分页边界、惰性 DB 连接与 DB 不可用路径，以及两条纪律断言：

- **零 import core/agents/dreaming**（AST 扫描 web/ 源码——宪章原则五新条款的物理证明）；
- **只读**（调用全部查询前后：文件化产物逐字节不变、树库行数与内容不变）。

字段语义与 001 落库口径一致（node_count = node_ids 长度、cost_usd = 成本记录的
generation_api_cost_usd、created_at 为 001 的 Float 时间戳）。
"""

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from web import queries
from web.queries import (
    WEB_DATA_DIR_KEYS,
    DatabaseUnavailableError,
    WebConfig,
    WebQueryError,
    get_evolution,
    get_health,
    get_lineage,
    get_node,
    get_summary,
    list_nodes,
    list_trees,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "movie.yaml"
BANNED_ROOTS = {"core", "agents", "dreaming"}
COST_FIELDS = (
    "llm_calls",
    "llm_tokens",
    "generation_api_calls",
    "generation_api_cost_usd",
    "human_review_minutes",
    "wall_clock_seconds",
)


@pytest.fixture(autouse=True)
def _isolated_engine_cache():
    """引擎缓存逐测试隔离（DSN 含临时目录路径，跨测试复用无意义且会留下连接）。"""
    queries.reset_engine_cache()
    yield
    queries.reset_engine_cache()


def _insert_tree(
    engine,
    *,
    tree_id: str,
    project_id: str,
    agent_id: str,
    policy_version: str,
    node_ids=(),
    config_snapshot=None,
) -> None:
    """直插一棵只含树记录（无节点）的树：边界与空态夹具（只读 SQL 侧的同构输入）。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO discovery_trees (tree_id, project_id, agent_id, policy_version,"
                " root_id, node_ids, config_snapshot) VALUES (:tree_id, :project_id, :agent_id,"
                " :policy_version, :root_id, :node_ids, :config_snapshot)"
            ),
            {
                "tree_id": tree_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "policy_version": policy_version,
                "root_id": f"{tree_id}-n0",
                "node_ids": json.dumps(list(node_ids)),
                "config_snapshot": json.dumps(config_snapshot or {"evaluator_weights": {}}),
            },
        )


def _config_payload() -> dict:
    """合法 web 配置映射（缺字段/非法值用例的基线；与 configs/movie.yaml 同构）。"""
    return {
        "web": {
            "host": "127.0.0.1",
            "port": 8080,
            "dsn_env": "CINEFLOW_WEB_DSN",
            "token": "",
            "page_size": 50,
            "data_dirs": {
                "policies": "policies/history",
                "dreaming": "dreaming/history",
                "calibration": "calibration",
                "pools": "replay/pools",
            },
            "export_dir": "web/dist",
        },
        "dreaming": {"collapse_window": 3, "collapse_threshold": 0.7},
    }


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


class Test配置解析:
    def test_真实配置段逐字段(self):
        config = WebConfig.from_yaml(CONFIG_PATH)
        assert config.host == "127.0.0.1"
        assert config.port == 8080
        assert config.dsn_env == "CINEFLOW_WEB_DSN"
        assert config.token == ""
        assert config.page_size == 50
        assert config.export_dir == "web/dist"
        assert config.data_dirs == {
            "policies": "policies/history",
            "dreaming": "dreaming/history",
            "calibration": "calibration",
            "pools": "replay/pools",
        }
        assert WEB_DATA_DIR_KEYS == ("policies", "dreaming", "calibration", "pools")

    def test_塌缩口径与_005_同源取自同一配置(self):
        """塌缩标注窗口/阈值取自同一配置文件的 dreaming 段（与 005 同一口径，不写死）。"""
        config = WebConfig.from_yaml(CONFIG_PATH)
        assert config.collapse_window == 3
        assert config.collapse_threshold == 0.7

    def test_数据目录解析(self, web_config):
        assert web_config.data_dir("dreaming").name == "dreaming"
        with pytest.raises(WebQueryError):
            web_config.data_dir("artifacts")

    def test_缺段即报错(self):
        with pytest.raises(WebQueryError, match="web"):
            WebConfig.from_dict({"dreaming": {"collapse_window": 3, "collapse_threshold": 0.7}})

    def test_缺_dreaming_塌缩口径即报错(self):
        payload = _config_payload()
        payload.pop("dreaming")
        with pytest.raises(WebQueryError, match="collapse_window"):
            WebConfig.from_dict(payload)

    @pytest.mark.parametrize("missing", ["host", "port", "dsn_env", "token", "page_size"])
    def test_缺字段即报错(self, missing):
        payload = _config_payload()
        payload["web"].pop(missing)
        with pytest.raises(WebQueryError, match=missing):
            WebConfig.from_dict(payload)

    @pytest.mark.parametrize(
        ("override", "match"),
        [
            ({"port": 70000}, "port"),
            ({"port": "8080"}, "port"),
            ({"page_size": 0}, "page_size"),
            ({"host": ""}, "host"),
            ({"dsn_env": ""}, "dsn_env"),
            ({"export_dir": ""}, "export_dir"),
            ({"token": None}, "token"),
        ],
    )
    def test_缺省之外的非法值即报错(self, override, match):
        payload = _config_payload()
        payload["web"].update(override)
        with pytest.raises(WebQueryError, match=match):
            WebConfig.from_dict(payload)

    @pytest.mark.parametrize(
        ("override", "match"),
        [
            ({"collapse_window": 0}, "collapse_window"),
            ({"collapse_window": "3"}, "collapse_window"),
            ({"collapse_threshold": 1.5}, "collapse_threshold"),
            ({"collapse_threshold": 0}, "collapse_threshold"),
        ],
    )
    def test_非法塌缩口径即报错(self, override, match):
        payload = _config_payload()
        payload["dreaming"].update(override)
        with pytest.raises(WebQueryError, match=match):
            WebConfig.from_dict(payload)

    @pytest.mark.parametrize(
        "data_dirs",
        [
            {"policies": "a", "dreaming": "b", "calibration": "c"},  # 缺 pools
            {"policies": "a", "dreaming": "b", "calibration": "c", "pools": "d", "extra": "e"},
            {"policies": "", "dreaming": "b", "calibration": "c", "pools": "d"},
            "not-a-mapping",
        ],
    )
    def test_数据目录集合与取值校验(self, data_dirs):
        payload = _config_payload()
        payload["web"]["data_dirs"] = data_dirs
        with pytest.raises(WebQueryError, match="data_dirs"):
            WebConfig.from_dict(payload)


# ---------------------------------------------------------------------------
# 树清单（GET /api/trees）
# ---------------------------------------------------------------------------


class Test树清单:
    def test_字段集与类型对齐_001_落库(self, web_config, web_fixture_trees):
        page = list_trees(web_config)
        assert page["total"] == 4
        assert page["page"] == 1
        assert page["page_size"] == web_config.page_size
        for item in page["items"]:
            assert set(item) == {
                "tree_id",
                "project_id",
                "agent_id",
                "policy_version",
                "node_count",
                "created_at",
                "form",
            }
            assert isinstance(item["tree_id"], str)
            assert isinstance(item["project_id"], str)
            assert isinstance(item["agent_id"], str)
            assert isinstance(item["policy_version"], str)
            assert isinstance(item["node_count"], int)
            assert isinstance(item["created_at"], float)
            assert item["form"] is None or isinstance(item["form"], str)

    def test_node_count_取自冻结的节点清单(self, web_config, web_fixture_trees):
        page = list_trees(web_config, page_size=web_config.page_size)
        counts = {item["tree_id"]: item["node_count"] for item in page["items"]}
        assert counts["tree-alpha-visual-champion"] == 3
        assert counts["tree-alpha-visual-child"] == 2
        assert counts["tree-beta-visual-champion"] == 1
        assert counts["tree-beta-storyboard-beta"] == 2

    def test_按更新时间倒序(self, web_config, web_fixture_trees):
        items = list_trees(web_config, page_size=4)["items"]
        assert [item["tree_id"] for item in items] == [
            "tree-beta-storyboard-beta",
            "tree-beta-visual-champion",
            "tree-alpha-visual-child",
            "tree-alpha-visual-champion",
        ]
        created = [item["created_at"] for item in items]
        assert created == sorted(created, reverse=True)

    def test_形态未标注为_None(self, web_config, web_fixture_trees):
        items = list_trees(web_config, page_size=4)["items"]
        forms = {item["tree_id"]: item["form"] for item in items}
        assert forms["tree-alpha-visual-champion"] == "visual"
        assert forms["tree-beta-visual-champion"] is None  # 未自报形态不推断（011 口径）

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"project_id": "proj-alpha"}, 2),
            ({"agent_id": "visual"}, 3),
            ({"agent_id": "storyboard"}, 1),
            ({"project_id": "proj-beta", "agent_id": "visual"}, 1),
        ],
    )
    def test_三维过滤各自生效(self, web_config, web_fixture_trees, filters, expected):
        assert list_trees(web_config, page_size=10, **filters)["total"] == expected

    def test_策略版本过滤横跨项目(self, web_config, web_fixture_trees, web_versions):
        page = list_trees(web_config, page_size=10, policy_version=web_versions["champion"])
        assert page["total"] == 2
        assert {item["project_id"] for item in page["items"]} == {"proj-alpha", "proj-beta"}

    def test_空态不报错(self, web_config, web_fixture_trees):
        page = list_trees(web_config, project_id="proj-does-not-exist")
        assert page == {"items": [], "page": 1, "page_size": web_config.page_size, "total": 0}

    def test_分页边界无重复无遗漏(self, web_config, web_fixture_trees):
        seen: list[str] = []
        for page in (1, 2, 3):
            payload = list_trees(web_config, page=page)
            assert payload["total"] == 4
            seen += [item["tree_id"] for item in payload["items"]]
        assert seen == [
            "tree-beta-storyboard-beta",
            "tree-beta-visual-champion",
            "tree-alpha-visual-child",
            "tree-alpha-visual-champion",
        ]
        assert len(set(seen)) == len(seen)  # 无重复
        assert list_trees(web_config, page=4)["items"] == []  # 越界页：空 items，total 仍如实

    def test_page_size_上限由配置封顶(self, web_config, web_fixture_trees):
        capped = list_trees(web_config, page_size=1000)
        assert capped["page_size"] == web_config.page_size == 50
        assert capped["total"] == 4  # 封顶后仍单页返回全部夹具树

    @pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"page_size": 0}])
    def test_非法分页参数即报错(self, web_config, web_fixture_trees, params):
        with pytest.raises(WebQueryError):
            list_trees(web_config, **params)


# ---------------------------------------------------------------------------
# 节点列表（GET /api/trees/{tree_id}/nodes）与节点详情（GET /api/nodes/{node_id}）
# ---------------------------------------------------------------------------


class Test节点列表:
    def test_字段集与类型对齐_001_落库(self, web_config, web_fixture_trees):
        page = list_nodes(web_config, "tree-alpha-visual-champion")
        assert page["total"] == 3
        for item in page["items"]:
            assert set(item) == {
                "node_id",
                "parent_id",
                "depth",
                "score",
                "cost_usd",
                "status",
                "created_at",
            }
            assert isinstance(item["depth"], int)
            assert isinstance(item["score"], float)
            assert isinstance(item["cost_usd"], float)
            assert item["status"] in {"evaluated", "failed"}
            assert isinstance(item["created_at"], float)

    def test_深度升序且父引用正确(self, web_config, web_fixture_trees):
        page = list_nodes(web_config, "tree-alpha-visual-champion")
        items = page["items"]
        assert [item["node_id"] for item in items] == [
            "tree-alpha-visual-champion-n0",
            "tree-alpha-visual-champion-n1",
            "tree-alpha-visual-champion-n2",
        ]
        assert [item["depth"] for item in items] == [0, 1, 1]
        assert items[0]["parent_id"] is None
        assert {item["parent_id"] for item in items[1:]} == {"tree-alpha-visual-champion-n0"}
        assert [item["score"] for item in items] == [0.3, 0.5, 0.7]
        assert [item["cost_usd"] for item in items] == pytest.approx([0.05, 0.1, 0.15])

    @pytest.mark.parametrize(("depth", "expected"), [(0, 1), (1, 2)])
    def test_深度过滤(self, web_config, web_fixture_trees, depth, expected):
        page = list_nodes(web_config, "tree-alpha-visual-champion", depth=depth)
        assert page["total"] == expected
        assert all(item["depth"] == depth for item in page["items"])

    def test_分页无重复无遗漏(self, web_config, web_fixture_trees):
        first = list_nodes(web_config, "tree-alpha-visual-champion", page=1, page_size=2)
        second = list_nodes(web_config, "tree-alpha-visual-champion", page=2, page_size=2)
        assert first["page_size"] == 2
        assert [item["node_id"] for item in first["items"]] == [
            "tree-alpha-visual-champion-n0",
            "tree-alpha-visual-champion-n1",
        ]
        assert [item["node_id"] for item in second["items"]] == ["tree-alpha-visual-champion-n2"]
        assert first["total"] == second["total"] == 3

    def test_不存在树返回_None(self, web_config, web_fixture_trees):
        assert list_nodes(web_config, "tree-does-not-exist") is None

    def test_空树返回空态(self, web_config, web_fixture_trees, web_tree_engine, web_versions):
        """零节点树（理论态）：items=[] 且 total=0（不报错不伪造）。"""
        _insert_tree(
            web_tree_engine,
            tree_id="tree-empty",
            project_id="proj-empty",
            agent_id="visual",
            policy_version=web_versions["champion"],
        )
        assert list_nodes(web_config, "tree-empty") == {
            "items": [],
            "page": 1,
            "page_size": web_config.page_size,
            "total": 0,
        }
        page = list_trees(web_config, page_size=10)
        empty = next(item for item in page["items"] if item["tree_id"] == "tree-empty")
        assert empty["node_count"] == 0
        assert empty["created_at"] is None  # 无根节点 → 时间戳缺失如实为空


class Test节点详情:
    def test_字段集齐全(self, web_config, web_fixture_trees):
        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        assert set(detail) == {
            "node_id",
            "tree_id",
            "parent_id",
            "depth",
            "prompt",
            "observation_keys",
            "artifact",
            "eval_breakdown",
            "score",
            "cost_usd",
            "cost",
            "status",
            "created_at",
        }
        assert detail["node_id"] == "tree-alpha-visual-champion-n1"
        assert detail["tree_id"] == "tree-alpha-visual-champion"
        assert detail["parent_id"] == "tree-alpha-visual-champion-n0"
        assert detail["depth"] == 1
        assert detail["score"] == 0.5
        assert detail["status"] == "evaluated"
        assert detail["created_at"] == 1001.0
        assert "夹具提示词 1" in detail["prompt"]

    def test_观测只给键不给值(self, web_config, web_fixture_trees):
        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        assert detail["observation_keys"] == [
            "clip_id",
            "gen_params",
            "material_kind",
            "material_tags",
        ]
        assert all(isinstance(key, str) for key in detail["observation_keys"])

    def test_工件引用为哈希与元信息键(self, web_config, web_fixture_trees):
        import blake3

        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        node_id = "tree-alpha-visual-champion-n1"
        assert detail["artifact"] == {
            "hash": blake3.blake3(node_id.encode()).hexdigest(),
            "metadata_keys": ["material_kind", "material_tags"],
        }
        plain = get_node(web_config, "tree-alpha-visual-champion-n0")
        assert plain["artifact"]["metadata_keys"] == []

    def test_eval_breakdown_携带评估器版本(self, web_config, web_fixture_trees):
        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        assert detail["eval_breakdown"] == [
            {"evaluator_key": "judge.cinematic@1.0.0", "score": 0.5, "diagnostics_keys": []},
            {
                "evaluator_key": "proxy.aesthetic@1.0.0",
                "score": 0.5,
                "diagnostics_keys": ["band"],
            },
        ]
        assert all("@" in entry["evaluator_key"] for entry in detail["eval_breakdown"])

    def test_成本明细与_cost_usd_口径(self, web_config, web_fixture_trees):
        detail = get_node(web_config, "tree-alpha-visual-champion-n1")
        assert set(detail["cost"]) == set(COST_FIELDS)
        assert detail["cost"]["llm_calls"] == 1
        assert detail["cost"]["llm_tokens"] == 200
        # cost_usd = 001 成本记录的 generation_api_cost_usd（与 tree_total 对账字段同口径）
        assert detail["cost"]["generation_api_cost_usd"] == 0.1
        assert detail["cost_usd"] == detail["cost"]["generation_api_cost_usd"]

    def test_不存在节点返回_None(self, web_config, web_fixture_trees):
        assert get_node(web_config, "node-does-not-exist") is None


# ---------------------------------------------------------------------------
# 谱系（GET /api/lineage/{policy_version}）
# ---------------------------------------------------------------------------


class Test谱系:
    def test_跨项目归属标注齐全(self, web_config, web_fixture_trees, web_lineage_files):
        payload = get_lineage(web_config, web_lineage_files["champion"])
        assert payload["policy_version"] == web_lineage_files["champion"]
        assert payload["agent_id"] == "visual"
        assert payload["trees"] == [
            {
                "tree_id": "tree-alpha-visual-champion",
                "project_id": "proj-alpha",
                "agent_id": "visual",
            },
            {
                "tree_id": "tree-beta-visual-champion",
                "project_id": "proj-beta",
                "agent_id": "visual",
            },
        ]
        assert payload["cross_project"] is True
        assert payload["parents"] == []

    def test_子版本跨项目与孤儿版本(self, web_config, web_fixture_trees, web_lineage_files):
        payload = get_lineage(web_config, web_lineage_files["champion"])
        children = {(row["version"], row["project_id"]) for row in payload["children"]}
        assert (web_lineage_files["child"], "proj-alpha") in children
        assert (web_lineage_files["child"], "proj-beta") in children
        # 有 meta 无树的版本：project_id 为 None（如实标注，不伪造归属）
        assert (web_lineage_files["orphan"], None) in children

    def test_父链逐级回指(self, web_config, web_fixture_trees, web_lineage_files):
        payload = get_lineage(web_config, web_lineage_files["child"])
        parents = {(row["version"], row["project_id"]) for row in payload["parents"]}
        assert parents == {
            (web_lineage_files["champion"], "proj-alpha"),
            (web_lineage_files["champion"], "proj-beta"),
        }
        assert {row["tree_id"] for row in payload["trees"]} == {
            "tree-alpha-visual-child",
            "tree-beta-storyboard-beta",
        }
        assert payload["children"] == []
        assert payload["cross_project"] is True

    def test_单项目版本跨项目字段为空列表(
        self, web_config, web_fixture_trees, web_lineage_files, web_tree_engine
    ):
        """单项目版本：cross_project=False，跨项目字段为空列表（非键缺失）。"""
        _insert_tree(
            web_tree_engine,
            tree_id="tree-single-project",
            project_id="proj-alpha",
            agent_id="visual",
            policy_version=web_lineage_files["orphan"],
        )
        payload = get_lineage(web_config, web_lineage_files["orphan"])
        assert payload["cross_project"] is False
        assert payload["trees"] == [
            {
                "tree_id": "tree-single-project",
                "project_id": "proj-alpha",
                "agent_id": "visual",
            }
        ]
        assert payload["children"] == []  # 无子版本 → 空列表（非键缺失）
        assert [row["version"] for row in payload["parents"]] == [
            web_lineage_files["champion"],
            web_lineage_files["champion"],
        ]  # 父链按项目各展开一行（跨项目归属标注）

    def test_孤儿版本_无树无子(self, web_config, web_fixture_trees, web_lineage_files):
        """有 meta 无树的版本：trees 为空列表、children 为空、cross_project=False。"""
        payload = get_lineage(web_config, web_lineage_files["orphan"])
        assert payload["trees"] == []
        assert payload["children"] == []
        assert payload["cross_project"] is False

    def test_不存在版本返回_None(self, web_config, web_fixture_trees, web_lineage_files):
        assert get_lineage(web_config, "version-does-not-exist") is None


# ---------------------------------------------------------------------------
# 进化曲线（GET /api/evolution/{agent_id}）与摘要（GET /api/summary）
# ---------------------------------------------------------------------------


class Test进化曲线:
    def test_逐轮序列与塌缩标注(self, web_config, web_fixture_rounds):
        payload = get_evolution(web_config, "visual")
        assert payload["agent_id"] == "visual"
        assert [row["round_id"] for row in payload["rounds"]] == [
            "dream-visual-1",
            "dream-visual-2",
            "dream-visual-3",
            "dream-visual-4",
        ]
        assert [row["round"] for row in payload["rounds"]] == [1, 2, 3, 4]
        assert [row["best_reward"] for row in payload["rounds"]] == pytest.approx(
            [0.575, 0.125, 0.1, 0.05]
        )
        assert [row["cost_usd"] for row in payload["rounds"]] == [0.4, 0.3, 0.2, 0.1]
        assert [row["collapse_flag"] for row in payload["rounds"]] == [False, True, True, True]
        assert payload["baseline_reward"] == pytest.approx(0.575)
        assert payload["collapse"] == {
            "collapsed": True,
            "start_round": 2,
            "threshold": 0.7,
            "window": 3,
        }

    def test_失败轮次不进曲线(self, web_config, web_fixture_rounds):
        payload = get_evolution(web_config, "visual")
        assert all("dream-visual-5" != row["round_id"] for row in payload["rounds"])

    def test_无轮次数据空态(self, web_config, web_data_dir):
        payload = get_evolution(web_config, "no-such-agent")
        assert payload["rounds"] == []
        assert payload["baseline_reward"] is None
        assert payload["collapse"]["collapsed"] is False
        assert payload["collapse"]["start_round"] is None
        assert payload["collapse"]["window"] == web_config.collapse_window

    def test_塌缩口径不写死(self, web_config, web_fixture_rounds):
        """窗口/阈值来自配置：放大阈值后同一序列不再判塌缩（口径可证伪）。"""
        widened = replace(web_config, collapse_threshold=0.05)
        payload = get_evolution(widened, "visual")
        assert payload["collapse"]["collapsed"] is False
        assert [row["collapse_flag"] for row in payload["rounds"]] == [False] * 4


class Test摘要:
    def test_信度与漂移面板(
        self, web_config, web_reliability_report, web_drift_report, web_fixture_trees
    ):
        web_drift_report()
        payload = get_summary(web_config)
        calibration = payload["calibration"]
        assert calibration["period"] == "2026-W39"
        assert calibration["target"] == 0.6
        assert calibration["meets"] is False  # judge 0.3 < 0.6 → 未达标（如实）
        agents = {entry["agent_id"]: entry["evaluators"] for entry in calibration["agents"]}
        judge = next(e for e in agents["visual"] if e["evaluator_key"].startswith("judge."))
        assert judge["metric"] == "kendall_tau"
        assert judge["value"] == 0.3
        assert judge["samples"] == 12
        assert judge["meets_target"] is False
        proxy = next(e for e in agents["visual"] if e["evaluator_key"].startswith("proxy."))
        assert proxy["metric"] == "pearson_r"
        assert proxy["meets_target"] is True

        drift = payload["drift"]
        assert drift["period"] == "2026-W39"
        assert drift["items"] == [
            {
                "evaluator_key": "judge.cinematic@1.0.0",
                "status": "suspect",
                "since": "2026-09-21T10:00:00+00:00",
            }
        ]
        assert drift["alerts"], "超阈漂移应产生告警（012 报表 alerts 投影）"
        assert drift["alerts"][0]["evaluator_key"] == "judge.cinematic@1.0.0"

    def test_缺失如实空态(self, web_config, web_data_dir):
        payload = get_summary(web_config)
        assert payload["calibration"] == {
            "period": None,
            "target": None,
            "meets": None,
            "agents": [],
            "alerts": [],
        }
        assert payload["drift"]["items"] == []
        assert payload["drift"]["alerts"] == []
        assert payload["drift"]["period"] is None

    def test_信度面板不重算口径(self, web_config, web_reliability_report, web_data_dir):
        """接口读数 = 010 报告文件逐字段（本查询不重算相关系数）。"""
        report = json.loads(
            (Path(web_data_dir["calibration"]) / "reports" / "2026-W39.json").read_text("utf-8")
        )
        payload = get_summary(web_config)["calibration"]
        assert payload["period"] == report["period"]
        assert payload["target"] == report["target"]
        assert payload["alerts"] == report["alerts"]
        assert [entry["agent_id"] for entry in payload["agents"]] == sorted(report["agents"])
        for entry in payload["agents"]:
            for evaluator in entry["evaluators"]:
                source = report["agents"][entry["agent_id"]][evaluator["evaluator_key"]]
                assert evaluator["samples"] == source["samples"]
                assert evaluator["meets_target"] == source["meets_target"]
                assert evaluator["value"] == source[evaluator["metric"]]


# ---------------------------------------------------------------------------
# health 与韧性
# ---------------------------------------------------------------------------


class Test健康与韧性:
    def test_全通(self, web_config, web_fixture_trees, web_data_dir):
        health = get_health(web_config)
        assert health == {"ok": True, "db": "up", "files": "ok", "detail": None}

    def test_DB_不可用如实报_down(self, web_config, monkeypatch, web_data_dir):
        monkeypatch.setenv(
            web_config.dsn_env, "postgresql+psycopg://cineflow:cineflow@127.0.0.1:1/none"
        )
        health = get_health(web_config)
        assert health["db"] == "down"
        assert health["ok"] is False
        assert health["files"] == "ok"
        assert health["detail"]

    def test_DSN_缺失如实报_down(self, web_config, monkeypatch, web_data_dir):
        monkeypatch.delenv(web_config.dsn_env, raising=False)
        health = get_health(web_config)
        assert health["db"] == "down"
        assert web_config.dsn_env in health["detail"]

    def test_DB_不可用时树接口报错而文件面板可用(self, web_config, monkeypatch, web_data_dir):
        monkeypatch.setenv(
            web_config.dsn_env, "postgresql+psycopg://cineflow:cineflow@127.0.0.1:1/none"
        )
        with pytest.raises(DatabaseUnavailableError):
            list_trees(web_config)
        assert get_evolution(web_config, "visual")["rounds"] == []
        assert get_summary(web_config)["calibration"]["agents"] == []

    def test_数据目录类型错误_文件面板报_error(self, web_config, web_data_dir):
        broken = dict(web_config.data_dirs)
        broken["dreaming"] = str(web_data_dir["policies"] / "not-a-dir.txt")
        (web_data_dir["policies"] / "not-a-dir.txt").write_text("x", encoding="utf-8")
        assert get_health(replace(web_config, data_dirs=broken))["files"] == "error"

    def test_文件损坏如实报错不静默(self, web_config, web_data_dir):
        reports = Path(web_data_dir["calibration"]) / "reports"
        (reports / "2026-W39.json").write_text("{ 不是 JSON", encoding="utf-8")
        with pytest.raises(queries.DataSourceError):
            get_summary(web_config)


class Test惰性连接:
    def test_文件面板不建连(self, web_config, web_data_dir, monkeypatch):
        """只读文件面板（曲线/摘要）不触碰 DB——DSN 缺失也照常可用。"""
        monkeypatch.delenv(web_config.dsn_env, raising=False)
        assert get_evolution(web_config, "visual")["rounds"] == []
        assert get_summary(web_config)["calibration"]["agents"] == []
        assert queries.engine_cache_size() == 0  # 惰性：未触碰 DB 即零连接

    def test_引擎按_DSN_复用(self, web_config, web_fixture_trees):
        list_trees(web_config)
        assert queries.engine_cache_size() == 1
        list_nodes(web_config, "tree-alpha-visual-champion")
        get_health(web_config)
        assert queries.engine_cache_size() == 1  # 同 DSN 复用同一引擎（不重复建连）


# ---------------------------------------------------------------------------
# 纪律：零 import 应用层模块 + 只读
# ---------------------------------------------------------------------------


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


class Test纪律:
    def test_web_源码零_import_应用层模块(self, web_source_files):
        """宪章原则五新条款的物理证明：web/ 不存在对 core/agents/dreaming 的代码依赖。"""
        assert web_source_files, "未找到 web/ 源文件"
        imported = {
            str(path): sorted(_imported_roots(path) & BANNED_ROOTS) for path in web_source_files
        }
        assert {path: roots for path, roots in imported.items() if roots} == {}

    def test_查询层只读(self, web_config, web_fixture_trees, web_data_dir, web_tree_engine):
        """全部查询调用前后：文件化产物逐字节不变 + 树库零变更（只读行为断言）。"""
        from sqlalchemy import text

        def fingerprint() -> dict:
            return {
                str(path.relative_to(web_data_dir["calibration"])): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
                for path in sorted(web_data_dir["calibration"].rglob("*"))
                if path.is_file()
            }

        def row_counts() -> dict:
            with web_tree_engine.connect() as conn:
                return {
                    table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                    for table in ("discovery_trees", "tree_nodes")
                }

        before_files, before_rows = fingerprint(), row_counts()
        list_trees(web_config)
        list_nodes(web_config, "tree-alpha-visual-champion")
        get_node(web_config, "tree-alpha-visual-champion-n1")
        get_lineage(web_config, "irrelevant-version")
        get_evolution(web_config, "visual")
        get_summary(web_config)
        get_health(web_config)
        assert fingerprint() == before_files
        assert row_counts() == before_rows
