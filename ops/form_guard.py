"""形态守卫：`core/` 与 `agents/` 的**零形态字面量 / 零形态判断分支**（功能 021；契约 C5~C8）。

**形态名清单不是人工常量**：由 `configs/*.yaml` 的顶层 `form:` 与 `form_aliases` **派生**
（新增一份形态配置即**自动**纳入禁令面 ⇒ 新形态名无法静默逃逸）；派生失败（无配置 / 缺键 /
类型错 / 重名 / 文件名 stem 与 `form` 取值不一致）即**报错**，不静默跳过、不"取第一份"、
不保留"默认形态清单"兜底（"配置缺失即静默全绿"是最坏的假绿）。

**两条派生面不得混用**（契约 C6）：

- `declared_forms(configs_dir)`：**id 面**（登记面）——各配置的 `form:` 取值，两两唯一；
- `form_literals(configs_dir)`：**名称面**（两层扫描的**唯一**输入）——id 面 ∪ 全部 `form_aliases`。

**扫描面（定义，不是逐条豁免）**：`iter_sources()` = `core/**/*.py` + `agents/**/*.py`
（排除 `__pycache__`，**不排除 `agents/pilot`**——021 补去 020 的盲区）。`tests/`、`ops/`、
`web/`、`dreaming/` **不在扫描面内**（E2 = 扫描面定义本身，不是逐条放行）。

**判定规则与例外的分工（不得互相顶替）**：ASCII 名按**词边界** `(?<![0-9A-Za-z_])名(?![0-9A-Za-z_])`
（短 id 用子串判定会误命中 `read`/`load`/`head` 一类常见标识符），中文别名按**子串**（中文不承担
标识符角色）；命中即候选，再由**例外三条**放行——E1 配置**文件路径**字面量、E2 扫描面定义、
E3 docstring 的**中性描述**（该行不含 `=`/`⇒`/`→` 与分支模式）。**例外只有这三条**：文件白名单、
目录豁免、`# noqa` 式逐条放行一律不存在；假阳性的唯一处置是**中性化措辞**。

**本模块业务无关**：不 import 任何 `core.*` / `agents.*`，源码内**零形态名常量**（合成反例由调用方
用**派生值**构造），只依赖 stdlib（`ast`/`bisect`/`functools`/`re`/`pathlib`）与既有依赖 `yaml`。
"""

import ast
import bisect
import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
# 扫描面（**定义**）：见模块 docstring。新增根必须同步契约 C5 的"扫描面定义"表。
DEFAULT_SCAN_ROOTS: tuple[str, ...] = ("core", "agents")
# 判断分支层：形态无关的**语法模式**（六条字面不变；契约 C6）
BRANCH_PATTERNS: tuple[str, ...] = (
    "form ==",
    "form==",
    "form !=",
    "form!=",
    "form is ",
    "form in ",
)
# 配置文件路径的写法（E1 谓词认的"路径片段"）
CONFIG_PATH_TOKEN = re.compile(r"configs/[\w./-]*\.yaml")
# E3 的硬条件：命中行出现任一即视为"绑定取值"（不是中性描述）
BINDING_MARKERS: tuple[str, ...] = ("=", "⇒", "→")
MODULE_SYMBOL = "<module>"
_TEXT_PLACEHOLDER = "<text>"


class FormConfigError(ValueError):
    """形态配置派生失败（无配置 / 缺键 / 类型错 / 取值或别名重名 / 文件名与取值不一致）。"""


class FormScanError(ValueError):
    """扫描根不存在或不是目录——不做"空扫描全绿"的假绿。"""


@dataclass(frozen=True)
class FormHit:
    """一处命中。`symbol` 是**机检锚点**（行号只作人读辅助）。"""

    path: str  # 相对仓库根（posix）；仓外用绝对路径
    symbol: str  # 所属 FunctionDef/ClassDef 名；模块级 ⇒ "<module>"
    line: int  # 仅作人读辅助（020 的引用漂移教训：不得作为机检锚点）
    hit: str  # 命中的形态名或分支模式
    layer: str  # "literal" | "branch"


@dataclass(frozen=True)
class _Context:
    text: str
    tree: ast.Module
    line_starts: tuple[int, ...]
    docstring_lines: frozenset[int]
    string_segments: tuple[tuple[int, int, str], ...]  # (起行, 止行, 源码片段)


# ---------------------------------------------------------------------------
# 形态配置派生面（id 面 / 名称面；缺键即报错）
# ---------------------------------------------------------------------------


def _config_files(configs_dir: Path) -> tuple[Path, ...]:
    files = tuple(sorted(Path(configs_dir).glob("*.yaml")))
    if not files:
        raise FormConfigError(
            f"未找到形态配置：{configs_dir}/*.yaml —— 缺配置即报错，"
            "不得回落到「默认形态清单」兜底（那会让配置缺失静默全绿）"
        )
    return files


def _declared(configs_dir: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """逐份配置的（形态 id, 其余名称表）；四条派生失败各自报错。"""
    declared: list[tuple[str, tuple[str, ...]]] = []
    owners: dict[str, str] = {}
    for path in _config_files(configs_dir):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise FormConfigError(f"{path} 不是合法 YAML：{exc}") from exc
        if not isinstance(payload, dict):
            raise FormConfigError(f"{path} 顶层不是映射（形态配置格式错误）")
        if "form" not in payload:
            raise FormConfigError(f"{path} 缺顶层 form 键（缺键即报错，不静默跳过该文件）")
        form = payload["form"]
        if not isinstance(form, str) or not form.strip():
            raise FormConfigError(f"{path} 的 form 取值必须是非空字符串：{form!r}")
        if path.stem != form:
            raise FormConfigError(
                f"{path} 的文件名 stem 必须逐字等于 form 取值（{path.stem!r} != {form!r}）"
                "——这是「形态 id → 配置路径」零人工常量反查的前提"
            )
        if "form_aliases" not in payload:
            raise FormConfigError(f"{path} 缺顶层 form_aliases 键（允许显式空列表 []，缺键即报错）")
        aliases = payload["form_aliases"]
        if not isinstance(aliases, (list, tuple)):
            raise FormConfigError(f"{path} 的 form_aliases 必须是列表（可为 []）：{aliases!r}")
        names = [form]
        for item in aliases:
            if not isinstance(item, str) or not item.strip():
                raise FormConfigError(f"{path} 的 form_aliases 项必须是非空字符串：{item!r}")
            names.append(item)
        for name in names:
            owner = owners.get(name)
            if owner is not None:
                raise FormConfigError(
                    f"名称 {name!r} 在 {owner} 与 {path} 重复（跨文件全部名称两两唯一）"
                )
            owners[name] = str(path)
        declared.append((form, tuple(names[1:])))
    return tuple(declared)


def declared_forms(configs_dir: Path) -> tuple[str, ...]:
    """**id 面**（登记面）：全部 `configs/*.yaml` 的 `form:` 取值，两两唯一、非空字符串。"""
    return tuple(form for form, _ in _declared(configs_dir))


def form_literals(configs_dir: Path) -> tuple[str, ...]:
    """**名称面**（两层扫描的唯一输入）：id 面 ∪ 全部 `form_aliases` 项（去重后）。"""
    return tuple(name for form, aliases in _declared(configs_dir) for name in (form, *aliases))


def form_branch_patterns() -> tuple[str, ...]:
    """判断分支层的模式集（形态无关的语法模式，六条字面不变）。"""
    return BRANCH_PATTERNS


def name_pattern(name: str) -> re.Pattern[str]:
    """单个名字的**判定**模式：ASCII 名按词边界，中文名（别名）按子串。"""
    if name.isascii():
        return re.compile(rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])")
    return re.compile(re.escape(name))


# ---------------------------------------------------------------------------
# 扫描面与命中定位（按符号名锚定，不引行号）
# ---------------------------------------------------------------------------


def iter_sources(roots: tuple[str, ...] | str | Path = DEFAULT_SCAN_ROOTS) -> tuple[Path, ...]:
    """扫描面：`roots` 下全部 `.py`（排除 `__pycache__`，零其他排除）。"""
    if isinstance(roots, (str, Path)):
        roots = (str(roots),)
    files: list[Path] = []
    for root in roots:
        base = Path(root)
        if not base.is_absolute():
            base = REPO_ROOT / base
        if not base.is_dir():
            raise FormScanError(f"扫描根不存在或不是目录：{base}")
        files.extend(sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts))
    return tuple(files)


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def _display(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return candidate.as_posix()


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    index = text.find("\n")
    while index != -1:
        starts.append(index + 1)
        index = text.find("\n", index + 1)
    return tuple(starts)


def _symbol_for_line(tree: ast.Module, line: int) -> str:
    """所属 `FunctionDef`/`ClassDef` 名（最内层）；模块级 ⇒ `<module>`。"""
    best: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= line <= end and (best is None or node.lineno >= best.lineno):
            best = node
    return best.name if best is not None else MODULE_SYMBOL


@functools.lru_cache(maxsize=256)
def _context(text: str) -> _Context:
    tree = ast.parse(text)
    docstring_lines: set[int] = set()
    segments: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body:
                first = body[0]
                value = getattr(first, "value", None)
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    docstring_lines.update(
                        range(first.lineno, (first.end_lineno or first.lineno) + 1)
                    )
        target: ast.Constant | ast.JoinedStr | None = None
        segment = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            target, segment = node, node.value
        elif isinstance(node, ast.JoinedStr):
            target = node
            segment = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        if target is not None:
            segments.append((target.lineno, target.end_lineno or target.lineno, segment))
    return _Context(
        text=text,
        tree=tree,
        line_starts=_line_starts(text),
        docstring_lines=frozenset(docstring_lines),
        string_segments=tuple(segments),
    )


def _hits_in_text(text: str, needle: str, *, path: str, layer: str) -> tuple[FormHit, ...]:
    context = _context(text)
    pattern = name_pattern(needle) if layer == "literal" else re.compile(re.escape(needle))
    hits: list[FormHit] = []
    for match in pattern.finditer(text):
        line = bisect.bisect_right(context.line_starts, match.start())
        hits.append(
            FormHit(
                path=path,
                symbol=_symbol_for_line(context.tree, line),
                line=line,
                hit=needle,
                layer=layer,
            )
        )
    return tuple(hits)


# ---------------------------------------------------------------------------
# 例外三条（E1 谓词 / E2 = 扫描面定义 / E3 谓词）
# ---------------------------------------------------------------------------


def _in_scan_face(path: str) -> bool:
    if path == _TEXT_PLACEHOLDER:
        return True  # 合成文本（无文件落点）：不判 E2
    candidate = Path(path)
    if not candidate.is_absolute():
        return bool(candidate.parts) and candidate.parts[0] in DEFAULT_SCAN_ROOTS
    try:
        relative = candidate.relative_to(REPO_ROOT)
    except ValueError:
        return True  # 仓外路径（合成夹具树）：不判 E2，走 E1/E3
    return bool(relative.parts) and relative.parts[0] in DEFAULT_SCAN_ROOTS


def _is_config_path_literal(name: str, line_text: str, context: _Context, line: int) -> bool:
    """E1 谓词：命中所在**字符串字面量**同时含 `configs/` 与 `.yaml`，且名字**仅**作路径片段。"""
    if not any(
        "configs/" in segment and ".yaml" in segment
        for start, end, segment in context.string_segments
        if start <= line <= end
    ):
        return False
    tokens = [match.span() for match in CONFIG_PATH_TOKEN.finditer(line_text)]
    if not tokens:
        return False
    matches = list(name_pattern(name).finditer(line_text))
    if not matches:
        return False
    return all(
        any(start <= match.start() and match.end() <= end for start, end in tokens)
        for match in matches
    )


def _classify(hit: FormHit, context: _Context) -> str | None:
    if not _in_scan_face(hit.path):
        return "E2"  # 扫描面定义的可机检投影（面内命中永不返回 E2）
    lines = context.text.splitlines()
    line_text = lines[hit.line - 1] if 0 < hit.line <= len(lines) else ""
    if _is_config_path_literal(hit.hit, line_text, context, hit.line):
        return "E1"
    if (
        hit.line in context.docstring_lines
        and not any(marker in line_text for marker in BINDING_MARKERS)
        and not any(pattern in line_text for pattern in BRANCH_PATTERNS)
    ):
        return "E3"
    return None


def classify_exception(hit: FormHit) -> str | None:
    """例外代号（`"E1"` / `"E2"` / `"E3"` / `None` = 违规）。三条之外一律不存在。"""
    if not _in_scan_face(hit.path):
        return "E2"
    path = _resolve(hit.path)
    if not path.is_file():
        raise FormScanError(f"命中落点不存在，无法判定例外：{path}")
    return _classify(hit, _context(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 两层扫描（判定的唯一实现；三处委派点共用）
# ---------------------------------------------------------------------------


def violations_in(
    text: str, name: str, *, path: str = _TEXT_PLACEHOLDER, layer: str = "literal"
) -> tuple[FormHit, ...]:
    """单份文本 × 单个名字的**违规**命中（判定 → 例外放行，两步走同一实现）。"""
    context = _context(text)
    return tuple(
        hit
        for hit in _hits_in_text(text, name, path=path, layer=layer)
        if _classify(hit, context) is None
    )


def literal_violations(
    configs_dir: Path,
    *,
    sources: tuple[str | Path, ...] | None = None,
    include_exceptions: bool = False,
) -> tuple[FormHit, ...]:
    """字面量层：`sources`（默认 = 扫描面）内含派生名称面任一名字的违规点。

    `include_exceptions=True` 返回**候选**命中（含被例外放行的），供"E1 恰好一处 / E3 零处"取证。
    """
    names = form_literals(configs_dir)
    files = iter_sources() if sources is None else tuple(_resolve(item) for item in sources)
    offenders: list[FormHit] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        context = _context(text)
        display = _display(path)
        for name in names:
            for hit in _hits_in_text(text, name, path=display, layer="literal"):
                if include_exceptions or _classify(hit, context) is None:
                    offenders.append(hit)
    return tuple(offenders)


def branch_violations(
    configs_dir: Path,
    *,
    sources: tuple[str | Path, ...] | None = None,
    include_exceptions: bool = False,
) -> tuple[FormHit, ...]:
    """判断分支层：`sources`（默认 = 同一扫描面）内出现六条形态无关语法模式的违规点。

    `configs_dir` 与字面量层同形（调用面统一）：分支层形态无关，但派生面失效同样**报错**。
    """
    declared_forms(configs_dir)
    files = iter_sources() if sources is None else tuple(_resolve(item) for item in sources)
    offenders: list[FormHit] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        context = _context(text)
        display = _display(path)
        for pattern in form_branch_patterns():
            for hit in _hits_in_text(text, pattern, path=display, layer="branch"):
                if include_exceptions or _classify(hit, context) is None:
                    offenders.append(hit)
    return tuple(offenders)
