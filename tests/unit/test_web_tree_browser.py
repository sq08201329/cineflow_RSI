"""树浏览器契约测试（功能 013 / T1313，先于实现编写；契约 C4/C5）。

覆盖（**经真实 HTTP 服务**，比直调查询函数更接近页面实际调用形态）：

- C4：三维过滤各自生效与可组合、分页边界无重复无遗漏（page_size 请求上限由配置封顶）、
  空态如实（items=[] + total=0，不报错）、节点列表（深度过滤/得分/成本/状态）、
  节点详情（prompt/观测键/工件哈希与元信息键/eval_breakdown 版本标注/成本）；
- 过滤控件选项（facets）：项目/Agent/策略版本/形态取自树库权威来源；
- C5：谱系链路跨项目归属标注、单项目版本跨项目字段为空列表（非缺失）、
  不存在版本 → 404 + 说明。
"""

import pytest

from web.queries import list_facets

AGENT = "visual"


class TestC4三维过滤:
    @pytest.mark.parametrize(
        ("query", "expected_total"),
        [
            ("", 4),
            ("project_id=proj-alpha", 2),
            ("agent_id=visual", 3),
            ("agent_id=storyboard", 1),
            ("project_id=proj-alpha&agent_id=visual", 2),
            ("project_id=proj-beta&agent_id=storyboard", 1),
            ("project_id=proj-alpha&agent_id=storyboard", 0),
        ],
    )
    def test_过滤各自生效且可组合(self, web_live_server, web_fixture_trees, query, expected_total):
        server = web_live_server()
        status, _headers, payload = server.json("GET", f"/api/trees?{query}")
        assert status == 200
        assert payload["total"] == expected_total

    def test_策略版本过滤横跨项目(
        self, web_live_server, web_fixture_trees, web_versions, web_lineage_files
    ):
        version = web_lineage_files["champion"]
        assert version == web_versions["champion"]
        server = web_live_server()
        status, _headers, payload = server.json("GET", f"/api/trees?policy_version={version}")
        assert status == 200
        assert {item["project_id"] for item in payload["items"]} == {"proj-alpha", "proj-beta"}
        assert all(item["policy_version"] == version for item in payload["items"])

    def test_树清单字段与形态标注(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/trees")
        assert status == 200
        forms = {item["tree_id"]: item["form"] for item in payload["items"]}
        assert forms["tree-alpha-visual-champion"] == "visual"
        assert forms["tree-beta-visual-champion"] is None  # 未自报形态 → null（不推断）
        counts = {item["tree_id"]: item["node_count"] for item in payload["items"]}
        assert counts["tree-alpha-visual-champion"] == 3

    def test_过滤选项来自树库(self, web_config, web_fixture_trees, web_lineage_files):
        facets = list_facets(web_config)
        assert facets["projects"] == ["proj-alpha", "proj-beta"]
        assert facets["agents"] == ["storyboard", "visual"]
        assert set(facets["policy_versions"]) == {
            web_lineage_files["champion"],
            web_lineage_files["child"],
        }
        assert facets["forms"] == ["storyboard", "visual"]  # 未标注形态不入选项

    def test_过滤选项路由(self, web_live_server, web_fixture_trees, web_lineage_files):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/facets")
        assert status == 200
        assert set(payload) == {"projects", "agents", "policy_versions", "forms"}
        assert payload["agents"] == ["storyboard", "visual"]


class TestC4分页边界:
    def test_逐页无重复无遗漏(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        seen: list[str] = []
        for page in (1, 2, 3):
            status, _headers, payload = server.json("GET", f"/api/trees?page={page}&page_size=2")
            assert status == 200
            assert payload["total"] == 4
            assert payload["page"] == page
            assert payload["page_size"] == 2
            seen += [item["tree_id"] for item in payload["items"]]
        assert len(seen) == 4
        assert len(set(seen)) == 4  # 无重复

    def test_page_size_请求上限由配置封顶(self, web_live_server, web_fixture_trees, web_config):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/trees?page_size=9999")
        assert status == 200
        assert payload["page_size"] == web_config.page_size

    def test_空态如实(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/trees?project_id=proj-none")
        assert status == 200
        assert payload["items"] == []
        assert payload["total"] == 0

    def test_越界页返回空页而非报错(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/trees?page=9")
        assert status == 200
        assert payload["items"] == []
        assert payload["total"] == 4

    @pytest.mark.parametrize("query", ["page=0", "page=-2", "page_size=0", "page=x"])
    def test_非法分页参数_400(self, web_live_server, web_fixture_trees, query):
        server = web_live_server()
        status, _headers, payload = server.json("GET", f"/api/trees?{query}")
        assert status == 400
        assert payload["error"] == "invalid_request"


class TestC4节点浏览:
    def test_节点列表字段齐全(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        status, _headers, payload = server.json(
            "GET", "/api/trees/tree-alpha-visual-champion/nodes"
        )
        assert status == 200
        assert payload["total"] == 3
        root, child = payload["items"][0], payload["items"][1]
        assert root["depth"] == 0 and root["parent_id"] is None
        assert child["depth"] == 1 and child["parent_id"] == root["node_id"]
        assert payload["items"][2]["status"] == "evaluated"
        assert [item["cost_usd"] for item in payload["items"]] == pytest.approx([0.05, 0.1, 0.15])

    @pytest.mark.parametrize(("depth", "expected"), [(0, 1), (1, 2)])
    def test_深度过滤(self, web_live_server, web_fixture_trees, depth, expected):
        server = web_live_server()
        status, _headers, payload = server.json(
            "GET", f"/api/trees/tree-alpha-visual-champion/nodes?depth={depth}"
        )
        assert status == 200
        assert payload["total"] == expected
        assert all(item["depth"] == depth for item in payload["items"])

    def test_节点分页无重复无遗漏(self, web_live_server, web_fixture_trees):
        server = web_live_server()
        collected: list[str] = []
        for page in (1, 2):
            _status, _headers, payload = server.json(
                "GET", f"/api/trees/tree-alpha-visual-champion/nodes?page={page}&page_size=2"
            )
            assert payload["total"] == 3
            collected += [item["node_id"] for item in payload["items"]]
        assert collected == [
            "tree-alpha-visual-champion-n0",
            "tree-alpha-visual-champion-n1",
            "tree-alpha-visual-champion-n2",
        ]

    def test_节点详情字段齐全(self, web_live_server, web_fixture_trees):
        import blake3

        server = web_live_server()
        node_id = "tree-alpha-visual-champion-n1"
        status, _headers, detail = server.json("GET", f"/api/nodes/{node_id}")
        assert status == 200
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
        assert "夹具提示词 1" in detail["prompt"]
        assert detail["observation_keys"] == [
            "clip_id",
            "gen_params",
            "material_kind",
            "material_tags",
        ]
        assert detail["artifact"]["hash"] == blake3.blake3(node_id.encode()).hexdigest()
        assert detail["artifact"]["metadata_keys"] == ["material_kind", "material_tags"]
        # eval_breakdown 分量携带评估器版本（evaluator_id@version，原则一）
        assert [entry["evaluator_key"] for entry in detail["eval_breakdown"]] == [
            "judge.cinematic@1.0.0",
            "proxy.aesthetic@1.0.0",
        ]
        assert all("@" in entry["evaluator_key"] for entry in detail["eval_breakdown"])
        assert detail["eval_breakdown"][1]["diagnostics_keys"] == ["band"]
        assert detail["cost"]["generation_api_cost_usd"] == detail["cost_usd"] == 0.1

    def test_空树节点列表为空态(
        self, web_live_server, web_config, web_tree_engine, web_versions, web_fixture_trees
    ):
        from sqlalchemy import text

        with web_tree_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO discovery_trees (tree_id, project_id, agent_id, policy_version,"
                    " root_id, node_ids, config_snapshot) VALUES ('tree-plain', 'proj-alpha',"
                    " 'visual', :version, 'tree-plain-n0', '[]', '{\"evaluator_weights\": {}}')"
                ),
                {"version": web_versions["champion"]},
            )
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/trees/tree-plain/nodes")
        assert status == 200
        assert payload == {"items": [], "page": 1, "page_size": web_config.page_size, "total": 0}

    @pytest.mark.parametrize("path", ["/api/trees/tree-none/nodes", "/api/nodes/node-none"])
    def test_不存在树与节点_404(self, web_live_server, web_fixture_trees, path):
        server = web_live_server()
        status, _headers, payload = server.json("GET", path)
        assert status == 404
        assert payload["error"] == "not_found"
        assert payload["detail"]


class TestC5谱系链路:
    def test_跨项目归属标注齐全(self, web_live_server, web_fixture_trees, web_lineage_files):
        server = web_live_server()
        status, _headers, payload = server.json(
            "GET", f"/api/lineage/{web_lineage_files['champion']}"
        )
        assert status == 200
        assert payload["agent_id"] == AGENT
        assert payload["cross_project"] is True
        assert {row["project_id"] for row in payload["trees"]} == {"proj-alpha", "proj-beta"}
        children = {(row["version"], row["project_id"]) for row in payload["children"]}
        assert (web_lineage_files["child"], "proj-alpha") in children
        assert (web_lineage_files["child"], "proj-beta") in children
        assert (web_lineage_files["orphan"], None) in children  # 有 meta 无树：归属如实为空

    def test_父链逐级回指(self, web_live_server, web_fixture_trees, web_lineage_files):
        server = web_live_server()
        status, _headers, payload = server.json("GET", f"/api/lineage/{web_lineage_files['child']}")
        assert status == 200
        assert [row["version"] for row in payload["parents"]] == [
            web_lineage_files["champion"]
        ] * 2  # 父版本按项目各展开一行
        assert {row["tree_id"] for row in payload["trees"]} == {
            "tree-alpha-visual-child",
            "tree-beta-storyboard-beta",
        }

    def test_单项目版本跨项目字段为空列表(
        self, web_live_server, web_fixture_trees, web_lineage_files
    ):
        server = web_live_server()
        status, _headers, payload = server.json(
            "GET", f"/api/lineage/{web_lineage_files['orphan']}"
        )
        assert status == 200
        assert payload["trees"] == []
        assert payload["children"] == []
        assert payload["cross_project"] is False
        assert payload["parents"] != []  # 父链仍在（与"跨项目字段为空"是两件事）

    def test_不存在版本_404(self, web_live_server, web_fixture_trees, web_lineage_files):
        server = web_live_server()
        status, _headers, payload = server.json("GET", "/api/lineage/version-none")
        assert status == 404
        assert payload["error"] == "not_found"
        assert "version-none" in payload["detail"]
