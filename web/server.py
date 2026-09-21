"""只读 HTTP 服务（标准库 `http.server`，默认 bind 127.0.0.1）。

**只读门禁**（宪章原则五新条款）：`ROUTES` 是全部路由的唯一登记处，每一项都必须是 GET
（`WRITE_METHODS` 列出被拒绝的写动词）；写请求一律 405（命中 GET 路由的路径）或 404，
零副作用——路由表与"写请求被拒"由 tests/contract/test_web_readonly.py 机检。

- **访问控制**：配置 `web.token` 非空时 `/api/*` 需 `Authorization: Bearer <token>` 或
  `?token=`；静态资产是页面代码、**不含任何权威数据**，故不受 token 约束（浏览器的
  `<script src>` 请求无法携带 header/query，若一并强制 token 页面即无法加载）；
- **路径穿越**：静态资产解析后必须仍在静态根内（越界 403；`Path.resolve` 后比较）；
- **韧性**：DB 不可用时服务照常启动——树相关接口 503 + 明确说明，文件化面板
  （曲线/摘要）与静态资产照常可用，`/api/health` 如实报 `db: down`。

查询取数一律经 `web/queries.py`（本模块不含任何 SQL 与文件读取）。
"""

import json
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from web import queries
from web.queries import (
    DatabaseUnavailableError,
    DataSourceError,
    WebConfig,
    WebQueryError,
)

WRITE_METHODS = ("POST", "PUT", "DELETE", "PATCH")

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


@dataclass(frozen=True)
class Route:
    """只读路由：pattern 为正则（含命名组），handler 为 `_Handler` 上的方法名。"""

    method: str
    pattern: str
    handler: str


# 全部路由的唯一登记处：method 恒为 GET（机检断言）
ROUTES: tuple[Route, ...] = (
    Route("GET", r"^/$", "index_page"),
    Route("GET", r"^/index\.html$", "index_page"),
    Route("GET", r"^/board$", "board_page"),
    Route("GET", r"^/board\.html$", "board_page"),
    Route("GET", r"^/static/(?P<rel>.+)$", "static_asset"),
    Route("GET", r"^/api/health$", "api_health"),
    Route("GET", r"^/api/summary$", "api_summary"),
    Route("GET", r"^/api/trees$", "api_trees"),
    Route("GET", r"^/api/trees/(?P<tree_id>[^/]+)/nodes$", "api_tree_nodes"),
    Route("GET", r"^/api/nodes/(?P<node_id>[^/]+)$", "api_node_detail"),
    Route("GET", r"^/api/lineage/(?P<policy_version>[^/]+)$", "api_lineage"),
    Route("GET", r"^/api/evolution/(?P<agent_id>[^/]+)$", "api_evolution"),
)


def default_static_root() -> Path:
    """包内静态资产目录（页面/脚本/样式）。"""
    return Path(__file__).resolve().parent / "static"


def create_server(
    config: WebConfig, *, static_root: str | Path | None = None
) -> ThreadingHTTPServer:
    """建只读服务（未启动）：host/port 取自配置；static_root 缺省为包内 static 目录。"""
    root = Path(static_root) if static_root is not None else default_static_root()

    class Handler(WebRequestHandler):
        pass

    Handler.config = config
    Handler.static_root = root
    return ThreadingHTTPServer((config.host, config.port), Handler)


class WebRequestHandler(BaseHTTPRequestHandler):
    """只读请求处理：路由分派 + JSON 响应 + 统一错误映射（写请求不会走到业务层）。"""

    server_version = "CineFlowWeb/0.1"
    protocol_version = "HTTP/1.1"
    config: WebConfig
    static_root: Path

    # ------------------------------------------------------------------
    # 响应工具
    # ------------------------------------------------------------------

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send_bytes(status, body, _JSON_CONTENT_TYPE)

    def _error(self, status: int, error: str, detail: str = "") -> None:
        self._send_json(status, {"error": error, "detail": detail})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        sys.stderr.write(f"[web] {self.address_string()} {format % args}\n")

    # ------------------------------------------------------------------
    # 请求分派（仅 GET）
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - 基类约定
        import re

        parts = urlsplit(self.path)
        path = parts.path
        if not self._authorized(path, parts.query):
            self._error(401, "unauthorized", "配置了 token：请带 Authorization: Bearer 或 ?token=")
            return
        for route in ROUTES:
            matched = re.match(route.pattern, path)
            if matched is None:
                continue
            try:
                getattr(self, route.handler)(**matched.groupdict(), query=parse_qs(parts.query))
            except WebQueryError as exc:
                self._query_error(exc)
            return
        self._error(404, "not_found", f"未注册的只读路径：{path}")

    def _write_method(self, method: str) -> None:
        """写请求：命中只读 GET 路由 → 405；其余 → 404（无写端点可探测）。"""
        import re

        path = urlsplit(self.path).path
        if any(re.match(route.pattern, path) for route in ROUTES):
            self._error(405, "method_not_allowed", f"{method} 不在只读路由表内（仅 GET）")
            return
        self._error(404, "not_found", f"未注册的只读路径：{path}")

    def do_POST(self) -> None:  # noqa: N802 - 基类约定
        self._write_method("POST")

    def do_PUT(self) -> None:  # noqa: N802 - 基类约定
        self._write_method("PUT")

    def do_DELETE(self) -> None:  # noqa: N802 - 基类约定
        self._write_method("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802 - 基类约定
        self._write_method("PATCH")

    def _query_error(self, exc: WebQueryError) -> None:
        if isinstance(exc, DatabaseUnavailableError):
            self._error(503, "database_unavailable", str(exc))
        elif isinstance(exc, DataSourceError):
            self._error(500, "data_source_error", str(exc))
        else:
            self._error(400, "invalid_request", str(exc))

    # ------------------------------------------------------------------
    # 访问控制
    # ------------------------------------------------------------------

    def _authorized(self, path: str, query: str) -> bool:
        """token 校验：只作用于数据面 `/api/*`（静态资产只含页面代码，不含权威数据）。"""
        token = self.config.token
        if not token or not path.startswith("/api/"):
            return True
        header = self.headers.get("Authorization", "")
        if header == f"Bearer {token}":
            return True
        return parse_qs(query).get("token", [""])[0] == token

    # ------------------------------------------------------------------
    # 静态资产
    # ------------------------------------------------------------------

    def _asset(self, relative: str) -> Path | None:
        """静态资产解析：占位化后必须仍在静态根内（越界即拒绝；不存在返回 None）。"""
        candidate_rel = unquote(relative).lstrip("/")
        if not candidate_rel:
            return None
        root = self.static_root.resolve()
        candidate = (root / candidate_rel).resolve()
        if candidate != root and root not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None

    def _serve_asset(self, relative: str) -> None:
        target = self._asset(relative)
        if target is None:
            self._error(404, "not_found", f"静态资产不存在或越界：{relative}")
            return
        self._send_bytes(
            200,
            target.read_bytes(),
            _CONTENT_TYPES.get(target.suffix, "application/octet-stream"),
        )

    def static_asset(self, *, rel: str, query: dict) -> None:
        self._serve_asset(rel)

    def index_page(self, *, query: dict) -> None:
        """树浏览器页面（US2 交付页面资产；缺失时如实 404，不伪造页面）。"""
        self._serve_asset("index.html")

    def board_page(self, *, query: dict) -> None:
        """进化看板页面（US3 交付页面资产；缺失时如实 404）。"""
        self._serve_asset("board.html")

    # ------------------------------------------------------------------
    # 数据接口（全部经 web/queries.py，本模块零 SQL）
    # ------------------------------------------------------------------

    @staticmethod
    def _int_param(query: dict, name: str, default: int | None) -> int | None:
        raw = query.get(name, [None])[0]
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise WebQueryError(f"{name} 必须为整数，实际为 {raw!r}") from exc

    @staticmethod
    def _str_param(query: dict, name: str) -> str | None:
        value = query.get(name, [None])[0]
        return value or None

    def api_health(self, *, query: dict) -> None:
        self._send_json(200, queries.get_health(self.config))

    def api_summary(self, *, query: dict) -> None:
        self._send_json(200, queries.get_summary(self.config))

    def api_trees(self, *, query: dict) -> None:
        payload = queries.list_trees(
            self.config,
            project_id=self._str_param(query, "project_id"),
            agent_id=self._str_param(query, "agent_id"),
            policy_version=self._str_param(query, "policy_version"),
            page=self._int_param(query, "page", 1),
            page_size=self._int_param(query, "page_size", None),
        )
        self._send_json(200, payload)

    def api_tree_nodes(self, *, tree_id: str, query: dict) -> None:
        payload = queries.list_nodes(
            self.config,
            tree_id,
            depth=self._int_param(query, "depth", None),
            page=self._int_param(query, "page", 1),
            page_size=self._int_param(query, "page_size", None),
        )
        if payload is None:
            self._error(404, "not_found", f"树不存在：{tree_id}")
            return
        self._send_json(200, payload)

    def api_node_detail(self, *, node_id: str, query: dict) -> None:
        payload = queries.get_node(self.config, node_id)
        if payload is None:
            self._error(404, "not_found", f"节点不存在：{node_id}")
            return
        self._send_json(200, payload)

    def api_lineage(self, *, policy_version: str, query: dict) -> None:
        payload = queries.get_lineage(self.config, policy_version)
        if payload is None:
            self._error(404, "not_found", f"策略版本不存在：{policy_version}")
            return
        self._send_json(200, payload)

    def api_evolution(self, *, agent_id: str, query: dict) -> None:
        self._send_json(200, queries.get_evolution(self.config, agent_id))


def serve(config: WebConfig, *, static_root: str | Path | None = None) -> None:
    """阻塞式起服务（demo/CLI 用）；Ctrl-C 停止。"""
    httpd = create_server(config, static_root=static_root)
    host, port = httpd.server_address[0], httpd.server_address[1]
    sys.stderr.write(f"[web] 只读服务已启动：http://{host}:{port}/（仅 GET）\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断路径
        sys.stderr.write("[web] 收到中断，停止服务\n")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    """CLI：`python -m web.server [--config configs/movie.yaml]`。"""
    import argparse

    parser = argparse.ArgumentParser(description="CineFlow 只读前端服务（仅 GET 路由）")
    parser.add_argument("--config", default="configs/movie.yaml", help="形态配置文件路径")
    args = parser.parse_args(argv)
    try:
        config = WebConfig.from_yaml(args.config)
    except WebQueryError as exc:
        sys.stderr.write(f"[web] 配置错误：{exc}\n")
        return 2
    serve(config)
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
