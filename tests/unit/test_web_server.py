"""只读 HTTP 服务单测（功能 013 / T1307，先于实现编写）。

覆盖：路由表登记（仅 GET）、token 校验（401/200）、静态资产服务、路径穿越拒绝、
DB 不可用时的韧性（服务照常启动 + 树接口 503 + 文件面板可用 + health 报 db: down）、
JSON 响应形态与错误映射（404/400/405/503）。

真实 socket 起服务（ThreadingHTTPServer，端口 0 = 临时端口），经 `http.client` 发请求——
比直接调处理函数更接近部署形态。
"""

import http.client
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from web import server as web_server

REPO_ROOT = Path(__file__).resolve().parents[2]


class _LiveServer:
    """把服务起在临时端口上，返回 (host, port) 与关闭句柄。"""

    def __init__(self, config, static_root):
        self.httpd = web_server.create_server(config, static_root=static_root)
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, *, headers=None, body=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            conn.close()

    def json(self, method: str, path: str, **kwargs):
        status, headers, payload = self.request(method, path, **kwargs)
        parsed = json.loads(payload.decode("utf-8")) if payload else None
        return status, headers, parsed

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def static_root(tmp_path):
    """静态资产根（测试用临时目录）：两页面 + 脚本 + 样式 + 一个越界诱饵文件。"""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>树浏览器</title>", encoding="utf-8")
    (root / "board.html").write_text("<!doctype html><title>进化看板</title>", encoding="utf-8")
    (root / "app.js").write_text("// 夹具脚本\n", encoding="utf-8")
    (root / "style.css").write_text("body { margin: 0 }\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不应被服务", encoding="utf-8")
    return root


@pytest.fixture()
def live_server(web_config, static_root):
    server = _LiveServer(web_config, static_root)
    yield server
    server.close()


class Test路由表:
    def test_全部路由仅_GET(self):
        methods = {route.method for route in web_server.ROUTES}
        assert methods == {"GET"}
        assert web_server.WRITE_METHODS == ("POST", "PUT", "DELETE", "PATCH")

    def test_路由表覆盖全部数据面板(self):
        patterns = [route.pattern for route in web_server.ROUTES]
        assert any("trees" in pattern for pattern in patterns)
        assert any("nodes" in pattern for pattern in patterns)
        assert any("lineage" in pattern for pattern in patterns)
        assert any("evolution" in pattern for pattern in patterns)
        assert any("summary" in pattern for pattern in patterns)
        assert any("health" in pattern for pattern in patterns)


class Test只读数据接口:
    def test_健康检查(self, live_server):
        status, _headers, payload = live_server.json("GET", "/api/health")
        assert status == 200
        assert payload["db"] == "up"
        assert payload["files"] == "ok"

    def test_树清单(self, live_server, web_fixture_trees):
        status, _headers, payload = live_server.json("GET", "/api/trees")
        assert status == 200
        assert payload["total"] == 4
        assert len(payload["items"]) == 4

    def test_树清单过滤与分页参数(self, live_server, web_fixture_trees):
        _status, _headers, payload = live_server.json(
            "GET", "/api/trees?project_id=proj-alpha&page=1&page_size=1"
        )
        assert payload["total"] == 2
        assert payload["page_size"] == 1
        assert len(payload["items"]) == 1

    def test_节点列表与详情(self, live_server, web_fixture_trees):
        status, _headers, payload = live_server.json(
            "GET", "/api/trees/tree-alpha-visual-champion/nodes"
        )
        assert status == 200
        assert [item["node_id"] for item in payload["items"]][0].endswith("-n0")
        status, _headers, detail = live_server.json(
            "GET", "/api/nodes/tree-alpha-visual-champion-n1"
        )
        assert status == 200
        assert detail["eval_breakdown"][0]["evaluator_key"].endswith("@1.0.0")

    def test_谱系与曲线与摘要(self, live_server, web_fixture_trees, web_fixture_rounds):
        status, _headers, lineage = live_server.json(
            "GET", "/api/lineage/version-does-not-exist"
        )
        assert status == 404
        status, _headers, evolution = live_server.json("GET", "/api/evolution/visual")
        assert status == 200
        assert len(evolution["rounds"]) == 4
        status, _headers, summary = live_server.json("GET", "/api/summary")
        assert status == 200
        assert set(summary) == {"calibration", "drift"}

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/api/trees/tree-does-not-exist/nodes", 404),
            ("/api/nodes/node-does-not-exist", 404),
            ("/api/lineage/version-does-not-exist", 404),
        ],
    )
    def test_不存在资源_404(self, live_server, web_fixture_trees, path, expected):
        status, _headers, payload = live_server.json("GET", path)
        assert status == expected
        assert payload["error"]

    @pytest.mark.parametrize("query", ["page=0", "page_size=0", "depth=-1"])
    def test_非法参数_400(self, live_server, web_fixture_trees, query):
        if query.startswith("page"):
            base = "/api/trees"
        else:
            base = "/api/trees/tree-alpha-visual-champion/nodes"
        status, _headers, payload = live_server.json("GET", f"{base}?{query}")
        assert status == 400
        assert "必须为" in payload["detail"]

    def test_未注册路径_404(self, live_server):
        status, _headers, payload = live_server.json("GET", "/api/nope")
        assert status == 404
        assert payload["error"] == "not_found"


class Test写请求拒绝:
    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_写动词一律拒绝(self, live_server, web_fixture_trees, method):
        status, _headers, payload = live_server.json(method, "/api/trees")
        assert status == 405
        assert payload["error"] == "method_not_allowed"
        assert method in payload["detail"]

    def test_写请求零副作用(self, live_server, web_fixture_trees, web_tree_engine):
        from sqlalchemy import text

        with web_tree_engine.connect() as conn:
            before = conn.execute(text("SELECT COUNT(*) FROM discovery_trees")).scalar()
        live_server.request("POST", "/api/trees", body=b"{}")
        live_server.request("DELETE", "/api/nodes/tree-alpha-visual-champion-n0")
        with web_tree_engine.connect() as conn:
            after = conn.execute(text("SELECT COUNT(*) FROM discovery_trees")).scalar()
        assert (before, after) == (4, 4)

    def test_静态路径写请求_405(self, live_server):
        status, _headers, _payload = live_server.request("PUT", "/static/app.js")
        assert status == 405


class TestToken校验:
    def test_未配置_token_仅本机语义(self, live_server):
        status, _headers, _payload = live_server.request("GET", "/api/health")
        assert status == 200

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            ("/api/health", {}),
            ("/api/health?token=wrong", {}),
            ("/api/health", {"Authorization": "Bearer wrong"}),
        ],
    )
    def test_配置_token_后缺token或错token_401(
        self, web_config, static_root, web_fixture_trees, path, headers
    ):
        server = _LiveServer(replace(web_config, token="s3cret"), static_root)
        try:
            status, _headers, payload = server.json("GET", path, headers=headers)
            assert status == 401
            assert payload["error"] == "unauthorized"
        finally:
            server.close()

    def test_配置_token_后带token_200(self, web_config, static_root, web_fixture_trees):
        server = _LiveServer(replace(web_config, token="s3cret"), static_root)
        try:
            for path, headers in (
                ("/api/health?token=s3cret", {}),
                ("/api/health", {"Authorization": "Bearer s3cret"}),
            ):
                status, _headers, payload = server.json("GET", path, headers=headers)
                assert status == 200
                assert payload["db"] == "up"
        finally:
            server.close()

    def test_静态资产不受_token_约束(self, web_config, static_root):
        """静态资产只含页面代码（无权威数据）：数据面全部在 /api/* 之后。"""
        server = _LiveServer(replace(web_config, token="s3cret"), static_root)
        try:
            status, _headers, payload = server.request("GET", "/static/app.js")
            assert status == 200
            assert payload.decode("utf-8") == "// 夹具脚本\n"
        finally:
            server.close()


class Test静态资产服务与路径穿越:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/", "<!doctype html><title>树浏览器</title>"),
            ("/index.html", "<!doctype html><title>树浏览器</title>"),
            ("/board", "<!doctype html><title>进化看板</title>"),
            ("/static/app.js", "// 夹具脚本\n"),
            ("/static/style.css", "body { margin: 0 }\n"),
        ],
    )
    def test_资产可服务(self, live_server, path, expected):
        status, headers, payload = live_server.request("GET", path)
        assert status == 200
        assert payload.decode("utf-8") == expected
        assert headers["Content-Type"].startswith(("text/html", "text/javascript", "text/css"))

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../secret.txt",
            "/static/%2e%2e%2fsecret.txt",
            "/static/..%2fsecret.txt",
            "/static//../secret.txt",
            "/..%2Fsecret.txt",
        ],
    )
    def test_路径穿越拒绝(self, live_server, path):
        status, _headers, payload = live_server.request("GET", path)
        assert status in (403, 404)
        assert "不应被服务" not in payload.decode("utf-8", errors="replace")

    def test_缺失资产_404(self, live_server):
        status, _headers, payload = live_server.request("GET", "/static/missing.js")
        assert status == 404
        assert b"not_found" in payload

    def test_目录请求_404(self, live_server):
        status, _headers, _payload = live_server.request("GET", "/static/")
        assert status == 404


class TestDB不可用韧性:
    def test_服务照常启动_树接口_503_文件面板可用(
        self, web_config, static_root, web_data_dir, monkeypatch
    ):
        broken = replace(web_config, dsn_env="CINEFLOW_WEB_BROKEN_DSN")
        monkeypatch.setenv(
            "CINEFLOW_WEB_BROKEN_DSN",
            "postgresql+psycopg://cineflow:cineflow@127.0.0.1:1/none",
        )
        server = _LiveServer(broken, static_root)
        try:
            status, _headers, payload = server.json("GET", "/api/trees")
            assert status == 503
            assert payload["error"] == "database_unavailable"
            assert payload["detail"]

            status, _headers, health = server.json("GET", "/api/health")
            assert status == 200
            assert health["db"] == "down"
            assert health["files"] == "ok"

            status, _headers, evolution = server.json("GET", "/api/evolution/visual")
            assert status == 200
            assert evolution["rounds"] == []

            status, _headers, summary = server.json("GET", "/api/summary")
            assert status == 200
            assert summary["calibration"]["agents"] == []

            status, _headers, _payload = server.request("GET", "/static/app.js")
            assert status == 200
        finally:
            server.close()

    def test_DSN_未配置_树接口_503(self, web_config, static_root, monkeypatch, web_data_dir):
        monkeypatch.delenv(web_config.dsn_env, raising=False)
        server = _LiveServer(web_config, static_root)
        try:
            status, _headers, payload = server.json("GET", "/api/trees")
            assert status == 503
            assert web_config.dsn_env in payload["detail"]
        finally:
            server.close()


class Test绑定与配置:
    def test_默认_bind_回环地址(self, web_config):
        assert web_config.host == "127.0.0.1"

    def test_端口取配置值(self, web_config, static_root):
        httpd = web_server.create_server(replace(web_config, port=0), static_root=static_root)
        try:
            assert httpd.server_address[0] == "127.0.0.1"
            assert httpd.server_address[1] > 0
        finally:
            httpd.server_close()
        assert web_config.port == 8080  # 真实配置端口未被就地改写

    def test_server_main_可调用(self):
        assert callable(web_server.main)

    def test_默认静态根为包内_static(self):
        assert web_server.default_static_root() == REPO_ROOT / "web" / "static"
