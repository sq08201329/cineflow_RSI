"""契约：只读门禁三重机检（C1~C3，功能 013 / T1309）。

三重机检 = ① 路由表断言（无写动词）② 角色权限（迁移 0009 的 SQL 纪律 + T1311 真实 PG）
③ 源码静态断言（无写调用、无写 SQL）。本文件承载 ① 与 ③，以及写请求被拒时的
**DB 零变更**、token 401/200、DB 不可用韧性、路径穿越拒绝。

静态断言的白名单例外：**只有 `web/export.py` 允许写盘**，且必须满足"写入函数带 `dest`
参数 + 目标表达式引用 `dest`（导出根目录）"；其余模块出现任何写调用即红。扫描器本身
有自检用例（合成违规源码必须被检出），避免"永远为绿的机检"。
"""

import ast
import re
import tokenize
from dataclasses import replace
from pathlib import Path

import pytest

from web import server as web_server

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT_MODULE = "export.py"

# 写 SQL 模式（大写 SQL 关键字，带结构上下文——HTTP 动词如 "DELETE" 不误报）
_WRITE_SQL_PATTERNS = (
    r"\bINSERT\s+INTO\b",
    r"\bDELETE\s+FROM\b",
    r"\bUPDATE\s+\S+\s+SET\b",
    r"\bDROP\s+(?:TABLE|SCHEMA|DATABASE|ROLE|FUNCTION|TRIGGER|INDEX)\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+(?:TABLE|SCHEMA|DATABASE|ROLE)\b",
    r"\bCREATE\s+(?:TABLE|SCHEMA|DATABASE|ROLE|FUNCTION|TRIGGER|INDEX)\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
)
_WRITE_SQL_RE = re.compile("|".join(_WRITE_SQL_PATTERNS))

# 写调用：写模式 open + 常见写盘方法/函数
_WRITE_METHODS = {
    "write_text",
    "write_bytes",
    "mkdir",
    "touch",
    "unlink",
    "rename",
    "replace",
    "rmdir",
}
_WRITE_FUNCTIONS = {
    "remove",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "copy",
    "copy2",
    "copyfile",
    "copytree",
    "move",
    "makedirs",
    "dump",
}
_WRITE_OPEN_MODES = set("wax+")
_EXPORT_ROOT_PARAM = "dest"


# ---------------------------------------------------------------------------
# 扫描器（静态断言的可审计实现 + 自检）
# ---------------------------------------------------------------------------


def _code_without_comments(path: Path) -> str:
    """源码文本（注释剔除）：静态断言的输入——注释不参与，代码与字符串字面量参与。"""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    cuts: dict[int, int] = {}
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                cuts[token.start[0]] = token.start[1]
    kept = []
    for index, line in enumerate(lines, start=1):
        if index in cuts:
            line = line[: cuts[index]] + ("\n" if line.endswith("\n") else "")
        kept.append(line)
    return "".join(kept)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _docstring_ids(tree: ast.Module) -> set[int]:
    """模块/类/函数 docstring 节点 id：文档说明不参与 SQL 字面量断言（散文不是 SQL）。"""
    ids: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, holders) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def _write_calls(tree: ast.Module) -> list[tuple[ast.Call, FunctionNode | None]]:
    """全部写盘调用（含写模式 open），附带所属函数。"""
    calls: list[tuple[ast.Call, ast.Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _WRITE_METHODS:
            calls.append((node, None))
        elif name == "open":
            if _is_write_mode(node):
                calls.append((node, None))
        elif name in _WRITE_FUNCTIONS:
            calls.append((node, None))
    attached = []
    for call, _ in calls:
        attached.append((call, _enclosing_function(tree, call)))
    return attached


def _is_write_mode(call: ast.Call) -> bool:
    modes = []
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        modes.append(call.args[1].value)
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            modes.append(keyword.value.value)
    if not modes:
        return False  # 缺省 "r"：只读打开
    return any(isinstance(mode, str) and set(mode) & _WRITE_OPEN_MODES for mode in modes)


def _enclosing_function(tree: ast.Module, target: ast.AST):
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(node):
            if child is target:
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


def _is_docstring_statement(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _bound_names(tree: ast.Module) -> set[str]:
    """模块内被绑定的名字（参数/赋值/循环/推导式/导入别名/函数类名）。

    用于区分"模块引用"与"恰好同名的局部变量"（例如把 `agents` 当局部列表用）——
    机检只拦真正的模块级依赖，不误报局部命名。
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _targets_export_root(target: ast.AST, function: FunctionNode) -> bool:
    """写目标是否落在导出根下：目标表达式引用 `dest`，或其局部变量由 `dest` 派生。

    （导出模块的写法约定：`target = dest / rel` 再 `target.write_text(...)`——
    静态断言跟随一层局部别名，运行时包含性由 web/export.py 的 `_target()` 兜底。）
    """
    rooted = {_EXPORT_ROOT_PARAM}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            if _EXPORT_ROOT_PARAM not in ast.dump(node.value):
                continue
            for assign_target in node.targets:
                if isinstance(assign_target, ast.Name):
                    rooted.add(assign_target.id)
    names = {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}
    return bool(names & rooted)


def _write_offenders(path: Path) -> list[str]:
    """某模块的写调用违规清单：非导出模块一律违规；导出模块要求写在 `dest` 参数函数内。"""
    tree = _tree(path)
    offenders = []
    for call, function in _write_calls(tree):
        location = f"{path.name}:{call.lineno}"
        if path.name != EXPORT_MODULE:
            offenders.append(f"{location} 只读模块内出现写调用")
            continue
        if function is None or _EXPORT_ROOT_PARAM not in {
            arg.arg for arg in function.args.args + function.args.kwonlyargs
        }:
            offenders.append(f"{location} 导出写调用不在带 {_EXPORT_ROOT_PARAM!r} 参数的函数内")
            continue
        target = call.func.value if isinstance(call.func, ast.Attribute) else call
        if not _targets_export_root(target, function):
            offenders.append(
                f"{location} 导出写调用目标未引用 {_EXPORT_ROOT_PARAM!r}（导出根目录）"
            )
    return offenders


def _sql_literal_offenders(path: Path) -> list[str]:
    """非 SELECT 的 SQL 字面量违规清单：含 SELECT 的语句字面量必须以 SELECT 开头。"""
    tree = _tree(path)
    docstrings = _docstring_ids(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if "SELECT" in node.value and not node.value.lstrip().upper().startswith("SELECT"):
            offenders.append(f"{path.name}:{node.lineno} 非 SELECT 开头的 SQL 字面量")
    return offenders


class Test扫描器自检:
    """机检必须有牙：合成违规源码必须被检出（否则"全绿"无意义）。"""

    def test_检出只读模块内的写调用(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text(
            "def f(p):\n    with open(p, 'w') as handle:\n        handle.write('x')\n",
            encoding="utf-8",
        )
        assert _write_offenders(path)

    def test_检出只读模块内的路径写方法(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text("from pathlib import Path\n\nPath('a').write_text('x')\n", encoding="utf-8")
        assert _write_offenders(path)

    def test_只读打开不误报(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text(
            "def f(p):\n    with open(p, 'r') as handle:\n        return handle.read()\n",
            encoding="utf-8",
        )
        assert _write_offenders(path) == []

    def test_导出模块内带_dest_参数的写调用放行(self, tmp_path):
        path = tmp_path / EXPORT_MODULE
        path.write_text(
            "def dump(dest, name):\n    target = dest / name\n    target.write_text('{}')\n",
            encoding="utf-8",
        )
        assert _write_offenders(path) == []

    def test_导出模块内非_dest_目标的写调用仍违规(self, tmp_path):
        path = tmp_path / EXPORT_MODULE
        path.write_text(
            "def dump(dest, name):\n    open('/tmp/elsewhere', 'w').close()\n",
            encoding="utf-8",
        )
        assert _write_offenders(path)

    def test_检出写_SQL(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text("SQL = 'INSERT INTO tree_nodes VALUES (1)'\n", encoding="utf-8")
        assert _WRITE_SQL_RE.search(_code_without_comments(path))

    def test_注释里的写_SQL_不误报(self, tmp_path):
        """注释是文档：不参与机检（代码与字符串字面量才参与）。"""
        path = tmp_path / "queries.py"
        path.write_text("SQL = 'SELECT 1'  # 禁止 INSERT INTO 之类\n", encoding="utf-8")
        assert _WRITE_SQL_RE.search(_code_without_comments(path)) is None

    def test_检出非_select_字面量(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text("SQL = 'WITHDRAW FROM t'\n", encoding="utf-8")
        assert _sql_literal_offenders(path) == []  # 无 SELECT 即不参与（非 SQL 语句）
        path.write_text("SQL = 'WHERE a = 1 SELECT'\n", encoding="utf-8")
        assert _sql_literal_offenders(path)

    def test_文档字符串不参与_SQL_字面量断言(self, tmp_path):
        path = tmp_path / "queries.py"
        path.write_text('"""本层只发只读查询（SQL 只有 SELECT）。"""\n', encoding="utf-8")
        assert _sql_literal_offenders(path) == []


# ---------------------------------------------------------------------------
# C1 路由表机检
# ---------------------------------------------------------------------------


class TestC1路由表:
    def test_全部路由仅_GET(self):
        assert {route.method for route in web_server.ROUTES} == {"GET"}
        assert web_server.WRITE_METHODS == ("POST", "PUT", "DELETE", "PATCH")

    def test_路由表覆盖写动词处理器(self):
        """服务必须显式处理四个写动词（各自走拒绝路径），否则会退化为静默 501。"""
        source = (REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")
        assert "import re" in source
        for method in web_server.WRITE_METHODS:
            assert f"def do_{method}(self)" in source

    def test_写动词处理器只做拒绝(self):
        """机检：do_POST/do_PUT/do_DELETE/do_PATCH 的函数体只调用一次拒绝处理器。"""
        tree = _tree(REPO_ROOT / "web" / "server.py")
        handlers = {f"do_{method}": method for method in web_server.WRITE_METHODS}
        seen = {}
        for node in ast.walk(tree):
            name = getattr(node, "name", "")
            if name in handlers and isinstance(node, ast.FunctionDef):
                seen[name] = node
        assert set(seen) == set(handlers)
        for name, node in seen.items():
            body = [
                statement
                for statement in node.body
                if not (
                    isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
                )
            ]
            assert len(body) == 1, f"{name} 不得含拒绝之外的逻辑"
            call = body[0]
            assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
            assert call.value.func.attr == "_write_method"  # type: ignore[attr-defined]

    def test_写请求_405_或_404_且_DB_零变更(
        self, web_live_server, web_fixture_trees, web_tree_engine
    ):
        from sqlalchemy import text

        server = web_live_server()
        with web_tree_engine.connect() as conn:
            before = {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in ("discovery_trees", "tree_nodes")
            }
        statuses = {}
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            for path in ("/api/trees", "/api/nodes/tree-alpha-visual-champion-n0", "/api/nope"):
                status, _headers, _payload = server.request(method, path)
                statuses[(method, path)] = status
        with web_tree_engine.connect() as conn:
            after = {
                table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                for table in ("discovery_trees", "tree_nodes")
            }
        assert set(statuses.values()) <= {404, 405}
        assert statuses[("POST", "/api/trees")] == 405  # 命中只读路由的写请求
        assert statuses[("POST", "/api/nope")] == 404  # 未注册路径
        assert before == after == {"discovery_trees": 4, "tree_nodes": 8}

    def test_写请求不改动文件化产物(self, web_live_server, web_data_dir, web_fixture_rounds):
        def fingerprint() -> dict:
            return {
                str(path.relative_to(web_data_dir["dreaming"])): (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                )
                for path in sorted(web_data_dir["dreaming"].rglob("*"))
                if path.is_file()
            }

        server = web_live_server()
        before = fingerprint()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            server.request(method, "/api/summary", body=b"{}")
        assert fingerprint() == before


# ---------------------------------------------------------------------------
# C2 静态断言（三重机检之三）
# ---------------------------------------------------------------------------


class TestC2静态断言:
    def test_web_源码无写_SQL(self, web_source_files):
        offenders = [
            f"{path.name}"
            for path in web_source_files
            if _WRITE_SQL_RE.search(_code_without_comments(path))
        ]
        assert offenders == [], f"只读目录内出现写 SQL 关键字：{offenders}"

    def test_web_源码_SQL_字面量只有_select(self, web_source_files):
        offenders = [
            violation for path in web_source_files for violation in _sql_literal_offenders(path)
        ]
        assert offenders == []

    def test_web_源码无写调用_导出模块白名单(self, web_source_files):
        offenders = [violation for path in web_source_files for violation in _write_offenders(path)]
        assert offenders == [], f"只读目录内出现写调用：{offenders}"

    def test_导出模块的写必须落在导出目录(self):
        """导出白名单的运行时兜底：`_target` 拒绝越出导出根的相对路径。"""
        from web import export

        outside = ["./../outside.txt", "data/../../etc/passwd"]
        for relative in outside:
            with pytest.raises(ValueError, match="导出目录"):
                export._target("/tmp/web-export-root", relative)
        inside = export._target("/tmp/web-export-root", "data/summary.json")
        assert str(inside).startswith("/tmp/web-export-root")

    def test_web_源码不动态导入与不引用应用层模块(self, web_source_files):
        """物理独立的延伸机检（AST 级）：无动态导入、无应用层模块引用（局部同名不误报）。"""
        banned = {"importlib", "core", "agents", "dreaming"}
        offenders = []
        for path in web_source_files:
            tree = _tree(path)
            module_refs = banned - _bound_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name.split(".")[0] for alias in node.names}
                    if names & banned:
                        offenders.append(f"{path.name}: 导入 {sorted(names)}")
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").split(".")[0] in banned:
                        offenders.append(f"{path.name}: 导入 {node.module}")
                elif isinstance(node, ast.Attribute):
                    root = node
                    while isinstance(root, ast.Attribute):
                        root = root.value
                    if isinstance(root, ast.Name) and root.id in module_refs:
                        offenders.append(f"{path.name}: 引用模块属性 {root.id}.{node.attr}")
                elif isinstance(node, ast.Name) and node.id in module_refs:
                    offenders.append(f"{path.name}: 引用模块名 {node.id}")
        assert offenders == []

    def test_查询层不含业务规则实现(self, web_source_files):
        """只读层不重算口径：不出现评估器/合成/回放/漂移检测的实现符号。"""
        banned = (
            "composite_score",
            "compute_reward",
            "pareto_auc",
            "detect_drift",
            "quantile_shift",
            "weighted_sum",
        )
        offenders = []
        for path in web_source_files:
            source = _code_without_comments(path)
            offenders += [f"{path.name}: {symbol}" for symbol in banned if symbol in source]
        assert offenders == []


# ---------------------------------------------------------------------------
# C3 部署形态
# ---------------------------------------------------------------------------


class TestC3部署形态:
    def test_默认_bind_回环(self, web_config):
        assert web_config.host == "127.0.0.1"

    @pytest.mark.parametrize(
        ("path", "expected"),
        [("/api/health", 200), ("/api/trees", 200), ("/api/summary", 200)],
    )
    def test_token_缺省时本机可读(self, web_live_server, web_fixture_trees, path, expected):
        server = web_live_server()
        status, _headers, _payload = server.json("GET", path)
        assert status == expected

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            ("/api/trees", {}),
            ("/api/trees?token=nope", {}),
            ("/api/summary", {"Authorization": "Bearer nope"}),
        ],
    )
    def test_token_配置后缺token或错token_401(
        self, web_config, web_fixture_trees, path, headers, web_live_server
    ):
        server = web_live_server(replace(web_config, token="s3cret"))
        status, _headers, payload = server.json("GET", path, headers=headers)
        assert status == 401
        assert payload["error"] == "unauthorized"

    @pytest.mark.parametrize(
        ("path", "headers"),
        [
            ("/api/trees?token=s3cret", {}),
            ("/api/summary", {"Authorization": "Bearer s3cret"}),
        ],
    )
    def test_token_配置后带token_200(
        self, web_config, web_fixture_trees, path, headers, web_live_server
    ):
        server = web_live_server(replace(web_config, token="s3cret"))
        status, _headers, _payload = server.json("GET", path, headers=headers)
        assert status == 200

    def test_DB_不可用_服务照常_树_503_文件面板可用(
        self, web_config, web_data_dir, monkeypatch, web_live_server, web_fixture_rounds
    ):
        broken = replace(web_config, dsn_env="CINEFLOW_WEB_BROKEN_DSN")
        monkeypatch.setenv(
            "CINEFLOW_WEB_BROKEN_DSN", "postgresql+psycopg://cineflow:cineflow@127.0.0.1:1/none"
        )
        server = web_live_server(broken)
        status, _headers, payload = server.json("GET", "/api/trees")
        assert status == 503
        assert payload["error"] == "database_unavailable"
        status, _headers, health = server.json("GET", "/api/health")
        assert (status, health["db"], health["files"]) == (200, "down", "ok")
        status, _headers, evolution = server.json("GET", "/api/evolution/visual")
        assert status == 200
        assert len(evolution["rounds"]) == 4  # 文件化面板不受 DB 影响

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../secret.txt",
            "/static/%2e%2e%2fsecret.txt",
            "/static/..%2fsecret.txt",
            "/static//../secret.txt",
            "/static/....//secret.txt",
        ],
    )
    def test_路径穿越拒绝(self, web_live_server, path):
        server = web_live_server()
        status, _headers, payload = server.request("GET", path)
        assert status in (403, 404)
        assert "不应被服务" not in payload.decode("utf-8", errors="replace")

    def test_静态资产正常可服务(self, web_live_server):
        server = web_live_server()
        status, _headers, payload = server.request("GET", "/static/app.js")
        assert (status, payload.decode("utf-8")) == (200, "// 夹具脚本\n")
