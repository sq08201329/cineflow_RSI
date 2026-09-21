"""静态导出测试（功能 013 / T1318，先于实现编写；契约 C8/SC-005）。

验证要点：

1. **导出目录形态**：静态资产（index.html/board.html/static/*）+ `data/*.json` 预生成；
2. **同一查询层**：导出 JSON 与在线 API 响应**逐字段一致**（导出不得另建取数口径）；
3. **断服务可浏览**：用**纯静态文件服务**（标准库，无任何 API）挂载导出目录后，
   两视图仍能取数与渲染——含 node+DOM 桩对静态模式（`window.CINEFLOW_STATIC`）的真实执行；
4. **写白名单与只读**：导出只写导出目录（源目录逐字节不变）；节点上限截断如实标注；
   空态导出不报错。
"""

import functools
import http.server
import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from web import export as web_export

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).parent / "page_dom_smoke.js"

EXPECTED_DATA_FILES = {
    "facets.json",
    "trees.json",
    "nodes.json",
    "node_details.json",
    "lineage.json",
    "evolution.json",
    "costs.json",
    "summary.json",
    "manifest.json",
}


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """纯静态文件服务（无 API）：日志静音，供"断服务后可浏览"用例使用。"""

    def log_message(self, *args):  # noqa: D102 - 基类签名
        return


@contextmanager
def _static_server(directory: Path):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _read(export_dir: Path, relative: str) -> dict:
    return json.loads((export_dir / relative).read_text(encoding="utf-8"))


@pytest.fixture()
def exported(web_config, tmp_path, web_fixture_trees, web_fixture_rounds, web_lineage_files):
    """默认导出（临时目录，不污染仓库工作树）。"""
    dest = tmp_path / "web-dist"
    written = web_export.export_all(web_config, dest=dest, static_root=STATIC_ROOT)
    return dest, written


class Test导出目录形态:
    def test_静态资产与数据文件齐全(self, exported):
        dest, written = exported
        assert (dest / "index.html").is_file()
        assert (dest / "board.html").is_file()
        assert (dest / "static" / "app.js").is_file()
        assert (dest / "static" / "style.css").is_file()
        data_files = {path.name for path in (dest / "data").glob("*.json")}
        assert data_files == EXPECTED_DATA_FILES
        assert set(written) >= {
            "index.html",
            "board.html",
            "static/app.js",
            "static/style.css",
            "data/trees.json",
        }

    def test_导出页注入静态模式标记(self, exported):
        dest, _written = exported
        for page in ("index.html", "board.html"):
            text = (dest / page).read_text(encoding="utf-8")
            assert "window.CINEFLOW_STATIC = true" in text
            assert "static/app.js" in text

    def test_导出页仍不含写入口(self, exported):
        dest, _written = exported
        for page in ("index.html", "board.html"):
            text = (dest / page).read_text(encoding="utf-8").lower()
            assert "<form" not in text
            assert "method=" not in text

    def test_资产逐字节拷贝(self, exported):
        dest, _written = exported
        for name in ("app.js", "style.css"):
            assert (dest / "static" / name).read_bytes() == (STATIC_ROOT / name).read_bytes()

    def test_清单如实标注(self, exported):
        dest, _written = exported
        manifest = _read(dest, "data/manifest.json")
        assert manifest["counts"]["trees"] == 4
        assert manifest["counts"]["nodes"] == 8
        assert manifest["truncated"] == []
        assert manifest["max_nodes"] == web_export.DEFAULT_MAX_NODES
        assert manifest["db"] == "up"
        assert manifest["db_note"] is None
        assert "只读快照" in manifest["note"]


class Test导出与接口逐字段一致:
    """同一查询层的机检证据：导出文件 vs 在线接口响应逐字段比对。"""

    def test_树快照与接口一致(self, exported, web_live_server, web_fixture_trees, web_config):
        dest, _written = exported
        snapshot = _read(dest, "data/trees.json")
        live = web_live_server().json("GET", "/api/trees")
        assert snapshot["total"] == live[2]["total"]
        assert snapshot["items"] == live[2]["items"]

    def test_节点快照与接口一致(self, exported, web_live_server, web_fixture_trees):
        dest, _written = exported
        snapshot = _read(dest, "data/nodes.json")["trees"]
        server = web_live_server()
        for tree_id, entry in snapshot.items():
            _status, _headers, live = server.json("GET", f"/api/trees/{tree_id}/nodes")
            assert entry["items"] == live["items"]
            assert entry["total"] == live["total"]

    def test_节点详情快照与接口一致(self, exported, web_live_server, web_fixture_trees):
        dest, _written = exported
        snapshot = _read(dest, "data/node_details.json")["nodes"]
        assert snapshot, "快照应包含节点详情"
        server = web_live_server()
        for node_id, detail in snapshot.items():
            _status, _headers, live = server.json("GET", f"/api/nodes/{node_id}")
            assert detail == live

    def test_谱系快照与接口一致(self, exported, web_live_server, web_lineage_files):
        dest, _written = exported
        snapshot = _read(dest, "data/lineage.json")["versions"]
        assert set(snapshot) == set(web_lineage_files.values())
        server = web_live_server()
        for version, payload in snapshot.items():
            _status, _headers, live = server.json("GET", f"/api/lineage/{version}")
            assert payload == live

    def test_曲线快照与接口一致(self, exported, web_live_server, web_fixture_rounds):
        dest, _written = exported
        snapshot = _read(dest, "data/evolution.json")["agents"]
        assert "visual" in snapshot
        _status, _headers, live = web_live_server().json("GET", "/api/evolution/visual")
        assert snapshot["visual"] == live

    def test_成本与摘要快照与接口一致(self, exported, web_live_server, web_fixture_trees):
        dest, _written = exported
        server = web_live_server()
        assert _read(dest, "data/costs.json") == server.json("GET", "/api/costs")[2]
        assert _read(dest, "data/summary.json") == server.json("GET", "/api/summary")[2]
        assert _read(dest, "data/facets.json") == server.json("GET", "/api/facets")[2]

    def test_导出过程不改源数据(
        self,
        web_config,
        web_data_dir,
        web_tree_engine,
        web_fixture_trees,
        web_fixture_rounds,
        web_lineage_files,
        tmp_path,
    ):
        """导出的读路径只读：导出前后树库行数与文件化产物指纹零变更。"""
        from sqlalchemy import text

        def fingerprint() -> dict:
            snapshot = {}
            for key, root in web_data_dir.items():
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        snapshot[f"{key}/{path.relative_to(root)}"] = (
                            path.stat().st_size,
                            path.stat().st_mtime_ns,
                        )
            return snapshot

        with web_tree_engine.connect() as conn:
            rows_before = conn.execute(text("SELECT COUNT(*) FROM tree_nodes")).scalar()
        files_before = fingerprint()
        web_export.export_all(web_config, dest=tmp_path / "web-dist", static_root=STATIC_ROOT)
        with web_tree_engine.connect() as conn:
            rows_after = conn.execute(text("SELECT COUNT(*) FROM tree_nodes")).scalar()
        assert (rows_before, rows_after) == (8, 8)
        assert fingerprint() == files_before


class Test断服务后可浏览:
    def test_纯静态服务可服务导出目录(self, exported):
        dest, _written = exported
        with _static_server(dest) as base:
            import urllib.request

            for url in ("index.html", "board.html", "static/app.js", "data/trees.json"):
                with urllib.request.urlopen(f"{base}/{url}") as response:  # noqa: S310 - 本机回环
                    assert response.status == 200
                    assert response.read() == (dest / url).read_bytes()

    def test_导出引用与文件一一对应(self, exported):
        """静态数据的取数链完整：app.js 引用的每个 data/*.json 都在导出目录里。"""
        import re

        dest, _written = exported
        referenced = set(
            re.findall(
                r"[\"'`](data/[A-Za-z0-9_.-]+\.json)[\"'`]",
                (dest / "static" / "app.js").read_text(encoding="utf-8"),
            )
        )
        assert referenced, "离线快照引用清单不得为空"
        missing = {rel for rel in referenced if not (dest / rel).is_file()}
        assert missing == set(), f"导出目录缺离线快照：{sorted(missing)}"

    def test_静态模式两视图真实渲染(
        self,
        exported,
        web_config,
        web_fixture_trees,
        web_fixture_rounds,
        web_reliability_report,
        web_drift_report,
    ):
        """最强证据：无任何 API 服务，仅静态文件 + 静态模式标记 → 两视图仍取数并渲染。"""
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("node 不可用：静态模式 DOM 冒烟跳过")
        web_drift_report()
        dest, _written = exported
        # 漂移报表在导出 fixture 之后才写入 → 重新导出一次，保证快照含最新摘要
        web_export.export_all(web_config, dest=dest, static_root=STATIC_ROOT)
        with _static_server(dest) as base:
            for page, mode, extra in (
                ("index.html", "tree", []),
                ("board.html", "board", ["visual"]),
            ):
                result = subprocess.run(
                    [
                        node,
                        str(HARNESS),
                        str(dest / page),
                        str(dest / "static" / "app.js"),
                        base,
                        mode,
                        *extra,
                        "static",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                payload = json.loads(result.stdout)
                assert "harness_error" not in payload, payload
                assert payload["error"] == "", payload
                assert payload["mode_hint"].startswith("离线静态快照")
                if mode == "tree":
                    assert payload["tree_rows"] == 4
                    assert "judge.cinematic@1.0.0" in payload["node_detail"]
                    assert "proj-" in payload["lineage"]["trees"]
                else:
                    assert payload["reward_chart_children"] > 0
                    assert "suspect" in payload["drift_panel"]


class Test边界与只读:
    def test_节点上限截断如实标注(self, web_config, tmp_path, web_fixture_trees):
        dest = tmp_path / "web-dist-capped"
        written = web_export.export_all(web_config, dest=dest, static_root=STATIC_ROOT, max_nodes=3)
        assert written  # 导出仍完成
        manifest = _read(dest, "data/manifest.json")
        assert manifest["max_nodes"] == 3
        assert manifest["counts"]["nodes"] == 3
        assert manifest["truncated"], "被截断的树必须如实列出"
        assert set(manifest["truncated"]) <= set(web_fixture_trees)

    def test_DB_不可用时导出仍完成并如实标注(
        self, web_config, tmp_path, web_data_dir, monkeypatch, web_fixture_rounds
    ):
        """与只读服务同款韧性：DB 不可用 → 树快照为空但导出完成，manifest 如实标注 db=down。"""
        broken = replace(web_config, dsn_env="CINEFLOW_WEB_EXPORT_BROKEN_DSN")
        monkeypatch.setenv(
            "CINEFLOW_WEB_EXPORT_BROKEN_DSN",
            "postgresql+psycopg://cineflow:cineflow@127.0.0.1:1/none",
        )
        dest = tmp_path / "web-dist-no-db"
        written = web_export.export_all(broken, dest=dest, static_root=STATIC_ROOT)
        assert written  # 导出照常完成
        manifest = _read(dest, "data/manifest.json")
        assert manifest["db"] == "down"
        assert manifest["db_note"] and "只读库不可用" in manifest["db_note"]
        assert manifest["counts"]["trees"] == 0
        assert _read(dest, "data/trees.json")["items"] == []
        # 文件面板不受影响：曲线仍按文件枚举的分线导出（分线来自 dreaming/history 目录）
        assert "visual" in _read(dest, "data/evolution.json")["agents"]

    def test_空态导出(self, web_config, tmp_path, web_data_dir):
        dest = tmp_path / "web-dist-empty"
        web_export.export_all(web_config, dest=dest, static_root=STATIC_ROOT)
        assert _read(dest, "data/trees.json")["items"] == []
        assert _read(dest, "data/costs.json")["node_count"] == 0
        assert _read(dest, "data/summary.json")["calibration"]["agents"] == []
        assert _read(dest, "data/evolution.json")["agents"] == {}

    def test_导出目录越界拒绝(self, tmp_path):
        with pytest.raises(ValueError, match="导出目录"):
            web_export._target(tmp_path / "root", "../outside.json")

    def test_重复导出幂等覆盖(self, exported, web_config):
        dest, _written = exported
        first = (dest / "data" / "trees.json").read_text(encoding="utf-8")
        web_export.export_all(web_config, dest=dest, static_root=STATIC_ROOT)
        assert (dest / "data" / "trees.json").read_text(encoding="utf-8") == first
