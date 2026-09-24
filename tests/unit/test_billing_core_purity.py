"""静态断言：`core/billing/` 与业务无关（功能 019 / 契约 C1；宪章原则五）。

**为什么**：`core/billing/` 承接的是"渠道计费纪律"本身（前置预算门禁、账单规范化导入、
逐项差异对账、校准先决、连续运行证据），要服务多个渠道（本特性唯实例化 LLM 渠道，媒体渠道
与生成侧渠道结转 G4/G2）。一旦里面出现渠道 id、环节 id、账单格式 id、厂商字面量或形态字面量，
"同一套机制、多渠道复用"即失效，且第二个渠道接入时会诱发按名分支（`channel_id == …`）。
本文件用 **AST + 文本双层机检** 把它钉死，编号即契约 C1 的四条断言：

1. 零形态字面量与形态判断分支（同 `tests/unit/test_form_switch.py:293-294` 口径）；
2. 零厂商词与**配置声明的档案 id / 端点 host**（同 `tests/unit/test_no_vendor_literals.py:39-52`
   的反向扫描法）；
3. 零**配置声明的渠道 id、环节 id 与账单格式 id**（反向扫描 `budget.channels` 键、
   `budget.tiers` 键、`budget.*.bill.format`）——白名单**仅**注册表内置通用格式键
   `csv_lines` / `json_lines`，且**白名单本身常驻断言**（新增一条即红）；
4. 零反向依赖：不得 `from agents.…` / `import agents`，也不得 `import dreaming`。

**扫描口径（如实登记）**：声明的 id 是**标识符/键**，故按标识符边界判定
（`(?<!\\w)id(?!\\w)`）而不是子串判定——本包必须出现 `deviation`（校准偏差口径）这类词，
子串判定会把 `dev` 误命中在 `deviation` 里（假阳性）。这不是放宽：写死仍一律命中，
`Test零配置声明字面量::test_扫描口径有牙齿` 用合成源码自检该判定确实会捉住写死形态。
"""

import ast
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "core" / "billing"
FORMS = ("movie", "shortdrama")

# 形态值字面量（同 tests/unit/test_form_switch.py 的 BANNED_LITERALS 口径）
FORM_LITERALS = ("shortdrama", '"movie"', "'movie'")
FORM_PATTERNS = ("form ==", "form==", "form !=", "form!=", "form is ", "form in ")
VENDOR_WORDS = ("deepseek", "openai", "qwen")
# 注册表内置通用格式键（C2）：**唯一**允许出现在 core/billing/ 内的格式字面量
FORMAT_WHITELIST = ("csv_lines", "json_lines")
FORBIDDEN_IMPORT_ROOTS = ("agents", "dreaming")
# 写死判定：与**字符串字面量**比较才是分支（参数与参数的相等判定是参数化纪律本身）
HARDCODED_ID_PATTERN = re.compile(r"\b(?:channel_id|tier_id|format_id)\s*(?:==|!=)\s*[\"']")


def _sources() -> list[pathlib.Path]:
    files = sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)
    assert files, "未找到 core/billing 源码（包落点变了？）"
    return files


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _id_pattern(literal: str) -> re.Pattern:
    """标识符边界判定：`dev` 命中 `dev`/`"dev"`，不命中 `deviation`/`dev_loop`。"""
    return re.compile(rf"(?<![0-9A-Za-z_]){re.escape(literal)}(?![0-9A-Za-z_])")


def _scan_ids(literals) -> list[str]:
    """反向扫描：`literals` 中任一取值以标识符形态出现在包内即计入违规点。"""
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for literal in literals:
            for match in _id_pattern(str(literal)).finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{_rel(path)}:{line} 出现 {literal!r}")
    return offenders


def _sections() -> list[dict]:
    return [
        yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))
        for form in FORMS
    ]


def _declared_channel_ids() -> list[str]:
    return sorted({str(key) for section in _sections() for key in section["budget"]["channels"]})


def _declared_tier_ids() -> list[str]:
    return sorted({str(key) for section in _sections() for key in section["budget"]["tiers"]})


def _declared_format_ids() -> list[str]:
    return sorted(
        {
            str(channel["bill"]["format"])
            for section in _sections()
            for channel in section["budget"]["channels"].values()
        }
    )


def _declared_profile_literals() -> list[str]:
    """两套形态配置里声明的档案 id 与端点 host（core 里出现即意味着路由写死）。"""
    literals: set[str] = set()
    for section in _sections():
        for profile_id, profile in section["llm"]["profiles"].items():
            literals.add(str(profile_id))
            if profile.get("base_url"):
                host = str(profile["base_url"]).rstrip("/")
                literals.add(host)
                literals.add(re.search(r"https?://([^/]+)", host).group(1))
    return sorted(literals)


class Test源码扫描集:
    def test_五模块被扫描(self):
        assert {path.name for path in _sources()} >= {
            "__init__.py",
            "budget.py",
            "bill.py",
            "reconcile.py",
            "calibration.py",
            "runlog.py",
        }


class Test零形态与厂商字面量:
    """C1 ① / ②：形态与厂商维度不得写死（渠道/价目/端点全来自配置）。"""

    def test_无形态字面量与形态分支(self):
        for path in _sources():
            source = path.read_text(encoding="utf-8")
            for banned in FORM_LITERALS:
                assert banned not in source, f"{_rel(path)} 不得出现形态字面量：{banned}"
            for pattern in FORM_PATTERNS:
                assert pattern not in source, f"{_rel(path)} 不得出现形态判断：{pattern}"

    def test_无配置声明的档案_id_与端点(self):
        literals = _declared_profile_literals()
        assert "deepseek-flash" in literals  # 扫描面有效（配置里确有该档案）
        assert _scan_ids(literals) == []

    def test_无厂商词(self):
        offenders = [
            f"{_rel(path)}:{line} 出现厂商词 {word!r}"
            for path in _sources()
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            for word in VENDOR_WORDS
            if word in text.lower()
        ]
        assert offenders == [], "core/billing 出现厂商词：\n" + "\n".join(offenders)


class Test零配置声明字面量:
    """C1 ③：渠道 id、环节 id、账单格式 id 全部作参数/配置值传入，包内零字面量。"""

    def test_无配置声明的渠道_id(self):
        declared = _declared_channel_ids()
        assert declared  # 扫描面有效（渠道 id 由配置声明；本特性只实例化一个渠道）
        offenders = _scan_ids(declared)
        assert offenders == [], "core/billing 出现渠道 id：\n" + "\n".join(offenders)

    def test_无配置声明的环节_id(self):
        declared = _declared_tier_ids()
        assert len(declared) == 8  # 环节 id 清单 = 8 处 .chat( 调用点声明的 stage= 取值
        offenders = _scan_ids(declared)
        assert offenders == [], "core/billing 出现环节 id：\n" + "\n".join(offenders)

    def test_账单格式白名单常驻(self):
        """白名单本身常驻断言：新增第三个内置格式键即红（须连同本文件一起改）。"""
        from core.billing.bill import builtin_format_ids

        assert FORMAT_WHITELIST == ("csv_lines", "json_lines")
        assert builtin_format_ids() == FORMAT_WHITELIST

    def test_无白名单外的格式_id(self):
        declared = _declared_format_ids()
        assert declared  # 扫描面有效
        offenders = _scan_ids([fid for fid in declared if fid not in FORMAT_WHITELIST])
        assert offenders == [], "core/billing 出现白名单外的格式 id：\n" + "\n".join(offenders)

    def test_零渠道与环节写死比较(self):
        offenders = [
            f"{_rel(path)}:{match.start()} {match.group(0)!r}"
            for path in _sources()
            for match in HARDCODED_ID_PATTERN.finditer(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], "core/billing 出现写死的 id 比较：\n" + "\n".join(offenders)

    def test_扫描口径有牙齿(self):
        """反向扫描的自检：合成的写死形态必须被判定命中（机检不是空跑全绿）。"""
        channel_id = _declared_channel_ids()[0]
        tier_id = _declared_tier_ids()[0]
        synthetic = (
            f'if channel_id == "{channel_id}":\n'
            f'    tier = "{tier_id}"\n'
            f"THRESHOLD = {channel_id.upper()}\n"
        )
        assert _id_pattern(channel_id).search(synthetic)
        assert _id_pattern(tier_id).search(synthetic)
        assert HARDCODED_ID_PATTERN.search(synthetic)
        # 反面：`deviation` 里的 `dev` 不得被误判为环节 id（假阳性会逼出无意义的改写）
        assert _id_pattern("dev").search('tier = "dev"')
        assert not _id_pattern("dev").search("deviation = (measured - expected) / expected")


class Test零反向依赖:
    """原则五：依赖单向（`agents → core`、`dreaming → core`），core 不得反向 import。"""

    def test_无_agents_与_dreaming_导入(self):
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    roots = {(node.module or "").split(".")[0]}
                else:
                    continue
                for root in sorted(roots & set(FORBIDDEN_IMPORT_ROOTS)):
                    offenders.append(f"{_rel(path)}:{node.lineno} 反向 import {root}")
        assert offenders == [], "core/billing 不得反向依赖：\n" + "\n".join(offenders)

    def test_源码文本无_agents_导入形态(self):
        offenders = [
            f"{_rel(path)}:{line}"
            for path in _sources()
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "from agents." in text or "import agents" in text
        ]
        assert offenders == [], f"core/billing 不得 import agents.*：{offenders}"


# ---------------------------------------------------------------------------
# C10 门禁注入两层断言（T1916；承接 T1906）
# ---------------------------------------------------------------------------

INJECTION_ROOTS = ("core", "agents", "ops")
GUARD_KWARG = "spend_guard"
# 真实渠道后端标记：出现即"本构造点走真实渠道"（按规则判定，不用文件白名单）
REAL_BACKEND_MARKERS = ("HttpBackend", "_llm_backend")
# 离线装配标记：同函数内构造模拟后端或测试桩
OFFLINE_BACKEND_MARKERS = ("MockBackend", "_CountingBackend", "StubBackend")
# ② 保证性清单常驻：真实渠道装配点（**两处**：链内唯一装配点 + 最小规模校准入口）
REAL_ASSEMBLY_POINTS = ("agents/pilot/backends.py", "ops/smoke_llm.py")
# ① 离线装配豁免清单常驻：显式 `spend_guard=None`（新增一处即红）
OFFLINE_ASSEMBLIES = (
    "ops/demo_dev_loop.py",
    "ops/demo_editing_loop.py",
    "ops/demo_promo_loop.py",
    "ops/demo_screenplay_loop.py",
    "ops/demo_storyboard_loop.py",
    "ops/demo_visual_loop.py",
    "ops/dev.py",
    "ops/screenplay.py",
)


def _function_node(tree: ast.Module, node: ast.Call):
    """构造点所在**函数**节点（找不到所属函数 → 整个模块，按"未定"从严归类）。"""
    for candidate in ast.walk(tree):
        if not isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if candidate.lineno <= node.lineno <= (candidate.end_lineno or candidate.lineno):
            return candidate
    return tree


def _scope_names(scope) -> set[str]:
    """作用域内**代码层面**引用的名字（注释与文档串不算引用——"引用后端"指真调用了它）。"""
    names: set[str] = set()
    for child in ast.walk(scope):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.ImportFrom):
            names.update(alias.name for alias in child.names)
        elif isinstance(child, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in child.names)
    return names


def _gateway_constructions() -> list[dict]:
    """普查 `LLMGateway(...)` 构造点（core/ agents/ ops/ 全域，含装配脚本与演示）。"""
    sites: list[dict] = []
    for root in INJECTION_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts or "/tests/" in path.as_posix():
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "LLMGateway":
                    continue
                guard = next((kw for kw in node.keywords if kw.arg == GUARD_KWARG), None)
                names = _scope_names(_function_node(tree, node))
                sites.append(
                    {
                        "path": path.relative_to(REPO_ROOT).as_posix(),
                        "line": node.lineno,
                        "has_guard_kwarg": guard is not None,
                        "guard_is_explicit_none": (
                            guard is not None
                            and isinstance(guard.value, ast.Constant)
                            and guard.value.value is None
                        ),
                        "real": bool(names & set(REAL_BACKEND_MARKERS)),
                    }
                )
    return sites


class Test门禁注入两层断言:
    """C10 的覆盖断言：**显式性 + 保证性**，豁免按规则判定（不用文件白名单）。

    阶段 3 结束时本类**故意红**在"真实两处尚未注入非 None 守卫"上——那是 TDD 的预期中间态，
    由 US1 的 T1928（`agents/pilot/backends.py` 与 `ops/smoke_llm.py` 注入 `FileLedger` +
    档位配置 + 渠道 id）转绿，泄漏门禁的洞不能靠改断言绕过。
    """

    def test_构造点普查面有效(self):
        sites = _gateway_constructions()
        assert len(sites) == 10, [(site["path"], site["line"]) for site in sites]
        assert sorted({site["path"] for site in sites}) == sorted(
            [*REAL_ASSEMBLY_POINTS, *OFFLINE_ASSEMBLIES]
        )

    def test_之一_显式性_任何构造必须显式传_spend_guard(self):
        """① 显式性（必要但不充分）：含显式 `None` 也算"传了"——本改造要的是"每处都想清楚"。"""
        offenders = [
            f"{site['path']}:{site['line']}"
            for site in _gateway_constructions()
            if not site["has_guard_kwarg"]
        ]
        assert offenders == [], (
            "以下 LLMGateway 构造未显式声明 spend_guard=（缺声明即红，含显式 None）：\n"
            + "\n".join(offenders)
        )

    def test_之二_保证性_真实渠道装配点必须传非_None_守卫(self):
        """② 保证性（真正的"必先过门禁"）：按规则判定，清单常驻。"""
        sites = _gateway_constructions()
        real = sorted({site["path"] for site in sites if site["real"]})
        assert real == sorted(REAL_ASSEMBLY_POINTS), (
            f"真实渠道装配点清单变化：{real}（新增即红——必须连同非 None 守卫一起改）"
        )
        offenders = [
            f"{site['path']}:{site['line']}"
            for site in sites
            if site["real"] and not (site["has_guard_kwarg"] and not site["guard_is_explicit_none"])
        ]
        assert offenders == [], (
            "真实渠道装配点必须传**非 None** 的 spend_guard=（真实调用必先过门禁）：\n"
            + "\n".join(offenders)
        )
        # core/ 与 agents/ 下不得出现 `spend_guard=None`（把 None 写在真实链上即是开洞）
        forbidden = [
            f"{site['path']}:{site['line']}"
            for site in sites
            if site["guard_is_explicit_none"] and site["path"].startswith(("core/", "agents/"))
        ]
        assert forbidden == [], f"core/ 与 agents/ 下不得出现 spend_guard=None：{forbidden}"

    def test_之二_离线装配清单常驻(self):
        sites = _gateway_constructions()
        offline = sorted({site["path"] for site in sites if not site["real"]})
        assert offline == sorted(OFFLINE_ASSEMBLIES), (
            f"离线装配点清单变化：{offline}（新增一处即红：必须显式声明 spend_guard=None）"
        )
        offenders = [
            f"{site['path']}:{site['line']}"
            for site in sites
            if not site["real"] and not (site["has_guard_kwarg"] and site["guard_is_explicit_none"])
        ]
        assert offenders == [], (
            "离线装配（模拟后端/测试桩/不可用路径）必须显式 spend_guard=None：\n"
            + "\n".join(offenders)
        )

    def test_之三_条件义务_不可用真实路径按规则判定(self):
        """③ 堵洞：`ops/screenplay.py` / `ops/dev.py` 的注入后端路径**当前不可用**
        （`--backend http` → `_backend()` 里裸构造 `HttpBackend()` 恒抛 `GatewayError`），
        故按①显式 `None` 登记；但一旦它们在本函数内变成可用真实装配（如
        `HttpBackend.from_profile(...)`），规则即把它们归入真实清单 ⇒ 本条与②同时红。
        """
        unusable = {"ops/screenplay.py", "ops/dev.py"}
        sites = _gateway_constructions()
        for site in sites:
            if site["path"] in unusable:
                assert not site["real"], (
                    f"{site['path']}:{site['line']} 已被改为真实装配路径——必须连同非 None 守卫改"
                )
                assert site["guard_is_explicit_none"]
        # 规则自检（有牙齿）：合成一个"可行真实装配"的构造点 ⇒ 必须被判为真实
        synthetic = """
def _backend(args):
    from core.llm_gateway.backends.http import HttpBackend
    return HttpBackend.from_profile(args.profile)

def main(args):
    gateway = LLMGateway(_backend(args), price_book={}, spend_guard=None)
"""
        tree = ast.parse(synthetic)
        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LLMGateway"
        )
        scope = _scope_names(_function_node(tree, call))
        assert not scope & set(REAL_BACKEND_MARKERS)  # 后端来自别处 ⇒ 今日分类为离线
        # 同一条规则：把真实后端就地内联 ⇒ 命中（此时 spend_guard=None 即红）
        inlined = synthetic.replace(
            "LLMGateway(_backend(args),", "LLMGateway(HttpBackend.from_profile(args.profile),"
        )
        tree = ast.parse(inlined)
        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LLMGateway"
        )
        assert _scope_names(_function_node(tree, call)) & set(REAL_BACKEND_MARKERS)
