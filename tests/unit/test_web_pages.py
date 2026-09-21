"""页面冒烟测试（功能 013 / T1314，先于页面实现编写；契约 C6/C9）。

三层验证：

1. **静态断言**：两页面与静态资产可服务；页面的 fetch 路径**全部登记在路由表内**
   （`web/server.py` 的 ROUTES 是唯一登记处）；控件标记存在；无外部库引用；
   **页面不含任何写入口**（无表单、无写动词、fetch 不带 method）；
2. **离线数据面引用**：app.js 引用的 `data/*.json` 与 `web/export.py` 的导出清单一致；
3. **DOM 冒烟（node + 极简 DOM 桩，见 page_dom_smoke.js）**：真实执行 app.js——
   起真实服务、点树行、选节点、看谱系/看板，断言渲染出真实数据（node 缺失时跳过）。

浏览器级渲染（视觉/交互细节）不在自动化范围：本测试覆盖"数据能取到、视图能渲染"，
样式与观感需人工打开页面查看（见 README 用法）。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from web import server as web_server

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).parent / "page_dom_smoke.js"
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node 不可用：页面 DOM 冒烟跳过")

# 页面必须存在的控件/面板标记（契约 C6/C9）
INDEX_MARKERS = (
    "tree-browser-root",
    "tree-filters",
    "filter-project",
    "filter-agent",
    "filter-version",
    "tree-table",
    "tree-table-body",
    "node-table",
    "node-table-body",
    "node-detail",
    "node-detail-body",
    "lineage-view",
    "lineage-parents",
    "lineage-trees",
    "lineage-children",
    "empty-state",
)
BOARD_MARKERS = (
    "board-root",
    "agent-picker",
    "reward-chart",
    "cost-chart",
    "calibration-panel",
    "drift-panel",
    "empty-state",
)


def _page_text(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def _app_js() -> str:
    return (STATIC_ROOT / "app.js").read_text(encoding="utf-8")


def _api_paths(source: str) -> set[str]:
    """从 JS 源码提取 `/api/...` 字面量；模板占位 `${...}` 归一为 `x`（路由机检用）。"""
    extracted = set()
    for literal in re.findall(r"[`\"'](/api/[^`\"']*)[`\"']", source):
        extracted.add(re.sub(r"\$\{[^}]*\}", "x", literal))
    return extracted


def _static_data_files(source: str) -> set[str]:
    return set(re.findall(r"[`\"'](data/[A-Za-z0-9_.-]+\.json)[`\"']", source))


def _route_matches(path: str) -> bool:
    return any(re.match(route.pattern, path) for route in web_server.ROUTES)


@pytest.fixture()
def real_static_server(web_live_server):
    """服务真实静态资产（web/static 本身）——页面内容断言用（默认夹具是临时资产桩）。"""
    return web_live_server(static_root=STATIC_ROOT)


class Test页面可服务:
    @pytest.mark.parametrize(
        ("url", "marker"),
        [
            ("/", "<!doctype html>"),
            ("/index.html", "树浏览器"),
            ("/board", "看板"),
            ("/static/app.js", "CineFlow 只读前端"),
            ("/static/style.css", "--panel"),
        ],
    )
    def test_静态资产可服务(self, real_static_server, url, marker):
        status, _headers, payload = real_static_server.request("GET", url)
        assert status == 200
        assert marker in payload.decode("utf-8")

    def test_两页面路径映射同一资产(self, real_static_server):
        for page, route in (("/", "/index.html"), ("/board", "/board.html")):
            _status, _headers, direct = real_static_server.request("GET", page)
            _status, _headers, mapped = real_static_server.request(
                "GET", f"/static/{route.lstrip('/')}"
            )
            assert direct == mapped


class Test页面静态断言:
    @pytest.mark.parametrize(
        ("source_name", "markers"),
        [("index.html", INDEX_MARKERS), ("board.html", BOARD_MARKERS)],
    )
    def test_控件标记存在(self, source_name, markers):
        source = _page_text(source_name)
        for marker in markers:
            assert f'id="{marker}"' in source, f"{source_name} 缺标记 {marker}"

    @pytest.mark.parametrize("source_name", ["index.html", "board.html"])
    def test_无外部库引用(self, source_name):
        source = _page_text(source_name)
        assert "http://" not in source.replace("http://www.w3.org", "")  # 仅允许 SVG 命名空间
        assert "https://" not in source
        assert "cdn" not in source.lower()
        assert "unpkg" not in source.lower()
        # 只允许引用本目录静态资产（相对路径 static/ 或 data/）
        for src in re.findall(r'(?:src|href)="([^"]+)"', source):
            assert src.startswith(("static/", "data/")), f"{source_name} 引用了非本目录资产 {src}"

    @pytest.mark.parametrize("source_name", ["index.html", "board.html"])
    def test_页面不含写入口(self, source_name):
        """只读纪律落到页面：无表单、无写动词、无提交按钮（录入/审批/部署永远走 CLI）。"""
        source = _page_text(source_name).lower()
        assert "<form" not in source
        assert "method=" not in source
        for verb in ("post", "put", "delete", "patch"):
            assert f'"{verb}"' not in source
        assert 'type="submit"' not in source

    def test_js_不含写请求(self):
        source = _app_js()
        assert "method:" not in source  # fetch 不设 method（默认 GET）
        for verb in ("POST", "PUT", "DELETE", "PATCH"):
            assert verb not in source, f"app.js 出现写动词 {verb}"

    def test_js_fetch_路径全部登记在路由表内(self):
        paths = _api_paths(_app_js())
        assert paths, "未从 app.js 提取到任何接口路径"
        unregistered = {path for path in paths if not _route_matches(path)}
        assert unregistered == set(), f"页面调用了未登记路径：{sorted(unregistered)}"

    def test_路由表的每条路由都被页面用到(self):
        """反向机检：路由表里没有"页面没接"的游离数据面接口（避免文档与实现漂移）。"""
        registered = {
            "/api/health",
            "/api/summary",
            "/api/facets",
            "/api/costs",
            "/api/trees",
            "/api/trees/x/nodes",
            "/api/nodes/x",
            "/api/lineage/x",
            "/api/evolution/x",
        }
        used = _api_paths(_app_js())
        assert registered - used == {"/api/health"}  # health 仅诊断用（服务/演示直接调用）

    def test_js_只引用相对静态资产(self):
        source = _app_js().replace("http://www.w3.org/2000/svg", "")  # SVG 命名空间非网络引用
        assert "http://" not in source and "https://" not in source
        files = _static_data_files(source)
        assert files == {
            "data/facets.json",
            "data/trees.json",
            "data/nodes.json",
            "data/node_details.json",
            "data/lineage.json",
            "data/evolution.json",
            "data/costs.json",
            "data/summary.json",
        }

    def test_看板图表为手绘_SVG(self):
        source = _app_js()
        assert "createElementNS" in source and "http://www.w3.org/2000/svg" in source
        assert "<svg" in _page_text("board.html")

    def test_token_从查询串读取并附加(self):
        source = _app_js()
        assert 'TOKEN_PARAM = "token"' in source
        assert "location.search" in source
        assert "withToken" in source


def _run_page_smoke(server, page: str, mode: str, agent: str | None = None) -> dict:
    """用 node + DOM 桩真实执行页面 JS（断言渲染出真实数据）；agent 可指定看板分线。"""
    base_url = f"http://{server.host}:{server.port}"
    command = [
        NODE,
        str(HARNESS),
        str(STATIC_ROOT / page),
        str(STATIC_ROOT / "app.js"),
        base_url,
        mode,
    ]
    if agent:
        command.append(agent)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.stdout, f"node 无输出：stderr={result.stderr[-500:]}"
    payload = json.loads(result.stdout)
    # 排障用：node 侧看门狗/报错痕迹随 payload 一起带出（失败时打印）
    payload["stderr_tail"] = result.stderr[-4000:]
    assert "harness_error" not in payload, json.dumps(payload, ensure_ascii=False, indent=2)
    assert payload["error"] == "", f"页面报错：{payload['error']}"
    return payload


@requires_node
class Test看板页面冒烟:
    def test_曲线成本摘要面板渲染真实数据(
        self,
        web_live_server,
        web_fixture_trees,
        web_fixture_rounds,
        web_reliability_report,
        web_drift_report,
    ):
        web_drift_report()
        server = web_live_server()
        payload = _run_page_smoke(server, "board.html", "board", agent="visual")
        assert payload["agent_options"] >= 2  # storyboard + visual
        assert payload["reward_chart_children"] > 0  # 手绘 SVG 真的画出来了
        assert payload["cost_chart_children"] > 0
        assert "塌缩标注" in payload["collapse_note"]
        assert "visual 合计" in payload["cost_note"]
        assert "suspect" in payload["drift_panel"]
        assert "未达标" in payload["calibration_panel"]

    def test_缺失数据空态如实(self, web_live_server, web_data_dir):
        """无轮次/无成本/无信度/无漂移：看板呈现空态（不报错不伪造）。"""
        server = web_live_server()
        payload = _run_page_smoke(server, "board.html", "board")
        assert payload["error"] == ""
        assert "空态" in payload["empty_state"]
        assert "空态" in payload["calibration_panel"]
        assert "空态" in payload["drift_panel"]


@requires_node
class Test树浏览器页面冒烟:
    def test_真实取数并渲染(self, web_live_server, web_fixture_trees, web_lineage_files):
        server = web_live_server()
        payload = _run_page_smoke(server, "index.html", "tree")
        assert payload["tree_rows"] == 4
        assert payload["node_rows"] >= 1
        assert "共 4 棵" in payload["tree_page_info"]
        # 节点详情：评估器版本标注 + 工件哈希 + 成本明细都渲染出来
        assert "judge.cinematic@1.0.0" in payload["node_detail"]
        assert "工件引用" in payload["node_detail"]
        assert "成本明细" in payload["node_detail"]
        # 谱系链路（跨项目归属标注）
        assert payload["lineage"]["label"].startswith("（")
        assert "proj-" in payload["lineage"]["trees"]
        assert payload["lineage"]["cross"]

    def test_空态如实呈现(self, web_live_server, web_fixture_trees):
        """无树项目：页面呈现空态（不报错、不伪造）——通过过滤到不存在项目验证。"""
        server = web_live_server()
        payload = _run_page_smoke(server, "index.html", "tree")
        assert payload["empty_state"] == ""  # 有数据时空态不显示
        assert payload["tree_rows"] == 4
