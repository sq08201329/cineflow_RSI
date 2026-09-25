"""形态接入登记点面（021 C9/C10 的机检实现；本任务只落登记点面，清单机制另行追加）。

**不新造第六处登记点**（FR-007 / FR-012）：`REGISTRATION_SITES` 是**常驻白名单**，恰好五处——
`tests/unit/test_form_switch.py`（①）、`tests/unit/test_config_integrity.py`（②）、
`tests/contract/test_pilot_contracts.py`（③）、`agents/pilot/pilot.py`（④）、
`tests/conftest.py`（⑤）。四处 `kind == "form_set"`（①③⑤ 与 ② 的配置参数化面：该处的形态集合
取值必须**双向等于**派生面）、一处 `kind == "clause_list"`（④：按**配置路径**通用，逐形态跑 020
口径机检）。新增第六处（哪怕只是再加一个测试文件的形态元组）⇒ 反向扫描变红，须显式登记。

**派生面只有一份**：形态 id 一律取自 `ops/form_guard.py` 的 `declared_forms(configs_dir)`
（**id 面**）。本模块**不复制**派生规则，也**不允许**登记点用 `form_literals()`（名称面 = id 面 ∪
中文别名）——登记完备是 id 面的事（`contracts/form-registration.md` C10），名称面只进字面量扫描。
故本模块的登记面求值器只认 `declared_forms`，遇到 `form_literals` 即报错（结构性防面混用）。

**委派证明**（"新形态静默逃逸"的根因守卫）：每处 `form_set` 的形态集合表达式**不得**是字面量
元组/列表，求值必须**到达派生面**（`declared_forms(...)` 调用；经模块级 `Name` 跳转也算）。

**反向扫描**（T2145 的"不新造第六处"机检，**口径与 T2198 的扩展面同源**）：扫 `tests/**`、
`ops/**`、`core/**`、`agents/**` 的**任意** `ast.Tuple`/`List`/`Set`/`Dict` 字面量——**含函数体内
与 `@pytest.mark.parametrize(...)` 装饰器实参**（只看模块级常量会**空跑假绿**）——命中条件 =
容器内出现**两个以上**名称面条目（= "形态清单被枚举/写死"：形态集合常量、形态→配置路径映射）。
白名单之外的命中即"第六处"，须显式登记；`sixth_site_scan()` 返回它们。

**登记完备三条件**（替代"恰好两份"断言，**禁止删除**；C10）：① `form:` 取值两两唯一
（由派生面保证：重复 / 缺键 / 文件名 stem 与取值不一致 ⇒ 派生即报错）；② 四处 `form_set`
登记点的形态集合与该形态**双向相等**（`registered_forms(site) == declared_forms()`）；③ 配置数
**≥ `MIN_CONFIG_COUNT`**（= 2，**不得**提到 3——那会把"机制可用"与"本次接入了几个形态"耦合）。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import yaml

from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = REPO_ROOT / "configs"
# 登记完备三条件的下界（**保留 2**：机制可用不依赖本次接入的形态数量）
MIN_CONFIG_COUNT = 2
# 反向扫描面：与 T2198 的扩展口径同源（含 tests/ 与 ops/；只看 core/ + agents/ 会漏掉登记点本身）
ENUM_SCAN_ROOTS: tuple[str, ...] = ("tests", "ops", "core", "agents")
# 反向扫描的命中阈值：容器内出现**两个以上**名称面条目 = "形态清单被枚举/写死"
ENUM_MIN_NAMES = 2
# 逐对形态差异集的常驻不变量（口径与契约 C9 一致；两处用例共用同一实现、不各写一份）
PAIRWISE_REQUIRED_DIFFERENCE = ("form",)
PAIRWISE_UNCHANGED_SEGMENTS = ("web", "cost_regression")


class FormRegistrationError(ValueError):
    """登记点判定失败（锚点缺失 / 表达式不可机检 / 用名称面做登记比较 / 派生符号缺失）。"""


@dataclass(frozen=True)
class RegistrationSite:
    """一处登记点（**符号名锚点**；行号只作人读辅助，机检不引行号）。"""

    path: str  # 相对仓库根
    symbol: str  # 该登记点的符号名（人读锚点）
    kind: str  # "form_set" | "clause_list"
    anchor: str  # 形态集合表达式的锚点（模块级常量名，或函数名 ⇒ 取其 parametrize 实参）
    delegate: str  # 该处委派到的派生面（人读；判定在 registered_forms()/delegated）


REGISTRATION_SITES: tuple[RegistrationSite, ...] = (
    RegistrationSite(
        path="tests/unit/test_form_switch.py",
        symbol="FORMS",
        kind="form_set",
        anchor="FORMS",
        delegate="declared_forms",
    ),
    RegistrationSite(
        path="tests/unit/test_config_integrity.py",
        symbol="test_缺项即红",
        kind="form_set",
        anchor="test_缺项即红",
        delegate="declared_forms",
    ),
    RegistrationSite(
        path="tests/contract/test_pilot_contracts.py",
        symbol="test_c13_两套配置差异可归因且无形态分支",
        kind="form_set",
        anchor="FORMS",
        delegate="declared_forms",
    ),
    RegistrationSite(
        path="agents/pilot/pilot.py",
        symbol="config_completeness",
        kind="clause_list",
        anchor="form_clause_completeness",
        delegate="form_clause_completeness",
    ),
    RegistrationSite(
        path="tests/conftest.py",
        symbol="PILOT_FORMS",
        kind="form_set",
        anchor="PILOT_FORMS",
        delegate="declared_forms",
    ),
)

# 白名单的 form_set 面（①③⑤ 与 ② 的配置参数化面）：反向扫描以它为"已登记"判据
FORM_SET_FACE: tuple[str, ...] = tuple(
    site.path for site in REGISTRATION_SITES if site.kind == "form_set"
)

# 求值器只认**一个**派生面：登记完备是 id 面的事（名称面进登记比较 ⇒ 中文别名打破 `⊆`）
_REGISTRATION_FACES = {"declared_forms": declared_forms}
# 透明包装（`tuple(sorted(...))` 一类）：不影响派生面判定
_TRANSPARENT_CALLS = frozenset({"tuple", "list", "set", "frozenset", "sorted", "iter"})
# parametrize 的形态参数名（② 的配置参数化面按参数名识别）
_FORM_PARAM_NAMES = frozenset({"form", "config_path"})


# ---------------------------------------------------------------------------
# 锚点与登记面求值（AST；"调用而非字面量"的委派证明在此产生）
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    if not path.is_file():
        raise FormRegistrationError(f"登记点文件不存在：{path}（锚点失效即报错，不静默跳过）")
    return ast.parse(path.read_text(encoding="utf-8"))


def _module_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """模块级 `Name = 表达式`（登记点的形态集合常量在此可被反查到）。"""
    assignments: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assignments[node.target.id] = node.value
    return assignments


def _parametrize_argument(decorator: ast.expr) -> ast.expr | None:
    """`@pytest.mark.parametrize("<形态参数名>", <表达式>)` 的第二个实参（形态集合参数化面）。"""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == "parametrize"):
        return None
    if len(decorator.args) < 2:
        return None
    first = decorator.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    names = {item.strip() for item in first.value.split(",")}
    return decorator.args[1] if names & _FORM_PARAM_NAMES else None


def anchor_expression(site: RegistrationSite, *, repo_root: Path = REPO_ROOT) -> ast.expr:
    """该登记点的形态集合表达式（模块级常量优先，否则取函数的 parametrize 实参）。"""
    tree = _parse(repo_root / site.path)
    assignments = _module_assignments(tree)
    if site.anchor in assignments:
        return assignments[site.anchor]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.anchor:
            for decorator in node.decorator_list:
                found = _parametrize_argument(decorator)
                if found is not None:
                    return found
    raise FormRegistrationError(
        f"{site.path} 缺登记锚点 {site.anchor!r}（形态集合常量或 parametrize 实参），"
        "该处登记点已失效"
    )


def _evaluate_expression(
    node: ast.expr,
    assignments: dict[str, ast.expr],
    configs_dir: Path,
    *,
    seen: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """形态集合表达式的受限求值：只认字面量、模块级 `Name` 跳转与派生面调用。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        out: list[str] = []
        for item in node.elts:
            out.extend(_evaluate_expression(item, assignments, configs_dir, seen=seen))
        return tuple(dict.fromkeys(out))
    if isinstance(node, ast.Dict):
        out = []
        for key, value in zip(node.keys, node.values, strict=True):
            for item in (key, value):
                if item is not None:
                    out.extend(_evaluate_expression(item, assignments, configs_dir, seen=seen))
        return tuple(dict.fromkeys(out))
    if isinstance(node, ast.Name):
        if node.id in seen:
            raise FormRegistrationError(f"形态集合常量自引用：{node.id}")
        if node.id not in assignments:
            raise FormRegistrationError(
                f"形态集合表达式引用了非模块级常量 {node.id!r}：无法机检（不得用运行期拼接）"
            )
        return _evaluate_expression(
            assignments[node.id], assignments, configs_dir, seen=seen | {node.id}
        )
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _REGISTRATION_FACES:
            # 派生面调用：参数（配置目录表达式）不在此求值——登记比较一律用同一 id 面
            return _REGISTRATION_FACES[name](Path(configs_dir))
        if name in _TRANSPARENT_CALLS and node.args:
            return _evaluate_expression(node.args[0], assignments, configs_dir, seen=seen)
        raise FormRegistrationError(
            f"形态集合表达式不是派生面调用（实测 {ast.unparse(node)}）："
            f"可用的派生面只有 {sorted(_REGISTRATION_FACES)}——名称面（含中文别名）不得进登记比较"
        )
    if (
        isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp)
        and len(node.generators) == 1
    ):
        generator = node.generators[0]
        if generator.ifs:
            raise FormRegistrationError(
                f"形态集合不得用条件收窄（{ast.unparse(node)}）：登记点的集合必须是整个迭代域"
            )
        if not isinstance(generator.target, ast.Name):
            raise FormRegistrationError(f"不支持的推导式目标：{ast.unparse(generator.target)}")
        # 推导式的**迭代域**即该处的形态集合（元素表达式怎样拼装路径都不影响登记面）
        return _evaluate_expression(generator.iter, assignments, configs_dir, seen=seen)
    raise FormRegistrationError(
        f"形态集合表达式不可机检（实测 {ast.unparse(node)}）：登记点必须直接引用派生面"
    )


def registered_forms(
    site: RegistrationSite, *, configs_dir: Path = CONFIGS_DIR, repo_root: Path = REPO_ROOT
) -> tuple[str, ...]:
    """该登记点的**形态取值集合**（id 面；表达式到达派生面才返回，否则报错）。"""
    if site.kind != "form_set":
        raise FormRegistrationError(
            f"{site.path}::{site.symbol} 是 clause_list 登记点（按配置路径通用，不持有形态集合）"
        )
    assignments = _module_assignments(_parse(repo_root / site.path))
    return _evaluate_expression(
        anchor_expression(site, repo_root=repo_root), assignments, Path(configs_dir)
    )


def _derives_from_face(node: ast.expr, assignments: dict[str, ast.expr]) -> bool:
    """委派证明：形态集合表达式**到达派生面**（字面量元组 ⇒ False）。"""
    if isinstance(node, ast.Name):
        return node.id in assignments and _derives_from_face(assignments[node.id], assignments)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in _REGISTRATION_FACES:
            return True
        if name in _TRANSPARENT_CALLS and node.args:
            return _derives_from_face(node.args[0], assignments)
        return False
    if (
        isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp)
        and len(node.generators) == 1
    ):
        return _derives_from_face(node.generators[0].iter, assignments)
    return False


def site_delegated(site: RegistrationSite, *, repo_root: Path = REPO_ROOT) -> bool:
    """该处是否**已委派**（AST 委派证明）：形态集合表达式到达派生面。

    `clause_list`（④）的判定 = "同模块的收口函数存在、且被 `config_completeness` 调用"
    （按配置路径通用 ⇒ 该处对新形态无需改代码即生效）。
    """
    if site.kind == "form_set":
        assignments = _module_assignments(_parse(repo_root / site.path))
        return _derives_from_face(anchor_expression(site, repo_root=repo_root), assignments)
    tree = _parse(repo_root / site.path)
    defined = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == site.anchor
        for node in ast.walk(tree)
    )
    if not defined:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == site.symbol:
            return any(
                isinstance(call.func, ast.Name) and call.func.id == site.anchor
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            )
    return False


def _clause_forms(configs_dir: Path) -> tuple[str, ...]:
    """④ 的"已登记形态"：逐形态跑 020 口径机检，通过者即在该处已登记（按配置路径通用）。

    收口函数按符号名**显式导入**（不是"第二解析路径"——它不解析 `module:attr` 声明面）；
    符号未就位即视为该处未登记（TDD 序里这就是红灯方向）。
    """
    try:
        from agents.pilot.pilot import form_clause_completeness
    except ImportError:
        return ()
    registered: list[str] = []
    for form in declared_forms(configs_dir):
        try:
            form_clause_completeness(configs_dir / f"{form}.yaml")
        except Exception:  # noqa: BLE001 - 未通过即"该形态在该处未登记"，由状态逐处点名
            continue
        registered.append(form)
    return tuple(registered)


@dataclass(frozen=True)
class SiteStatus:
    """一处登记点的逐形态判定结果（缺项**点名是哪一处、缺哪个形态**）。"""

    site: RegistrationSite
    declared: tuple[str, ...]
    registered: tuple[str, ...]
    delegated: bool
    detail: str = ""

    @property
    def missing(self) -> tuple[str, ...]:
        """该处**缺**的形态（派生面有、登记面无 ⇒ 新形态在这一处静默逃逸）。"""
        return tuple(form for form in self.declared if form not in self.registered)

    @property
    def foreign(self) -> tuple[str, ...]:
        """该处多出的形态（登记面有、派生面无 ⇒ 挡住了"登记了没有配置的形态"）。"""
        return tuple(form for form in self.registered if form not in self.declared)

    @property
    def ok(self) -> bool:
        return self.delegated and not self.missing and not self.foreign

    def describe(self) -> str:
        if self.ok:
            return f"{self.site.path}::{self.site.symbol} 已登记 + 已委派（{self.site.kind}）"
        reasons = []
        if self.missing:
            reasons.append(f"缺形态 {list(self.missing)}")
        if self.foreign:
            reasons.append(f"多出未声明形态 {list(self.foreign)}")
        if not self.delegated:
            reasons.append("形态集合表达式未到达派生面（委派证明失败）")
        if self.detail:
            reasons.append(self.detail)
        return f"{self.site.path}::{self.site.symbol} 未登记：{'；'.join(reasons)}"


def site_registration_status(
    site: RegistrationSite, *, configs_dir: Path = CONFIGS_DIR, repo_root: Path = REPO_ROOT
) -> SiteStatus:
    """逐处判定"已登记 + 已委派"（缺项点名**哪一处、哪个形态**，不得只报总数）。"""
    declared = declared_forms(configs_dir)
    detail = ""
    try:
        delegated = site_delegated(site, repo_root=repo_root)
    except FormRegistrationError as exc:
        delegated = False
        detail = f"锚点失效：{exc}"
    if site.kind == "form_set":
        try:
            registered = registered_forms(site, configs_dir=configs_dir, repo_root=repo_root)
        except FormRegistrationError as exc:
            registered = ()
            detail = f"形态集合不可机检：{exc}"
    else:
        registered = _clause_forms(configs_dir)
        if not registered:
            detail = detail or (
                "form_clause_completeness 未就位或逐形态机检未通过（020 口径声明缺失即拒绝启动）"
            )
    return SiteStatus(
        site=site,
        declared=declared,
        registered=registered,
        delegated=delegated,
        detail=detail,
    )


def registration_status(
    *, configs_dir: Path = CONFIGS_DIR, repo_root: Path = REPO_ROOT
) -> tuple[SiteStatus, ...]:
    """五处登记点**逐一**判定（第六处的判定面是反向扫描，见 `sixth_site_scan`）。"""
    return tuple(
        site_registration_status(site, configs_dir=configs_dir, repo_root=repo_root)
        for site in REGISTRATION_SITES
    )


# ---------------------------------------------------------------------------
# 登记完备三条件（替代"恰好两份"；**禁止删除**原断言）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationCompleteness:
    """登记完备证据（三条件并列，各自的机检形式见 `contracts/form-registration.md` C10）。"""

    declared: tuple[str, ...]  # id 面（`configs/*.yaml` 的 `form:` 取值，两两唯一）
    sites: tuple[SiteStatus, ...]

    @property
    def config_count(self) -> int:
        return len(self.declared)

    @property
    def unique(self) -> bool:
        """① 两两唯一：`form:` 取值唯一数 == 配置文件数（重复 / 缺键 ⇒ 派生即报错）。"""
        return len(set(self.declared)) == len(self.declared)

    @property
    def at_least_two(self) -> bool:
        """③ 下界保留：配置数 ≥ `MIN_CONFIG_COUNT`（**不**提高到 3）。"""
        return self.config_count >= MIN_CONFIG_COUNT

    @property
    def registered_matches(self) -> bool:
        """② 四处 form_set 登记点的形态集合与该形态**双向相等**（缺项即红）。"""
        return bool(self.declared) and all(
            status.ok and status.registered == self.declared
            for status in self.sites
            if status.site.kind == "form_set"
        )

    def violations(self) -> tuple[str, ...]:
        """逐条点名（不删除、不放宽；每处缺项独立成条）。"""
        issues: list[str] = []
        if not self.unique:
            issues.append("① 条件：`configs/*.yaml` 的 `form:` 取值必须两两唯一")
        if not self.at_least_two:
            issues.append(
                f"③ 条件：配置数 {self.config_count} < 下界 {MIN_CONFIG_COUNT}"
                "（下界保留、不得提高——机制可用不与本次接入数量耦合）"
            )
        for status in self.sites:
            if not status.ok:
                issues.append(f"② 条件：{status.describe()}")
        return tuple(issues)


def registration_completeness(
    *, configs_dir: Path = CONFIGS_DIR, repo_root: Path = REPO_ROOT
) -> RegistrationCompleteness:
    """登记完备三条件的证据（形态集合比较**一律**用 id 面；名称面不参与）。"""
    return RegistrationCompleteness(
        declared=declared_forms(configs_dir),
        sites=registration_status(configs_dir=configs_dir, repo_root=repo_root),
    )


# ---------------------------------------------------------------------------
# 逐对形态差异集（① 与 ③ 的"逐形态对"常驻断言共用**同一实现**，不各写一份）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormPairDifference:
    """一对形态的顶层差异集（键集比较；差异必须落在配置里）。"""

    left: str
    right: str
    differing: frozenset[str]


def _document(configs_dir: Path, form: str) -> dict:
    payload = yaml.safe_load((configs_dir / f"{form}.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FormRegistrationError(f"{configs_dir / f'{form}.yaml'} 顶层不是映射")
    return payload


def pairwise_differences(*, configs_dir: Path = CONFIGS_DIR) -> tuple[FormPairDifference, ...]:
    """**每一对**形态的顶层差异集（新形态自动进入其遍历面 ⇒ 静默逃逸消除）。"""
    forms = declared_forms(configs_dir)
    documents = {form: _document(configs_dir, form) for form in forms}
    return tuple(
        FormPairDifference(
            left=left,
            right=right,
            differing=frozenset(
                key
                for key in set(documents[left]) | set(documents[right])
                if documents[left].get(key) != documents[right].get(key)
            ),
        )
        for left, right in combinations(forms, 2)
    )


def pairwise_violations(pair: FormPairDifference) -> tuple[str, ...]:
    """逐对断言的口径（非空 ∧ 必含 `form` ∧ 必不含形态无关基建段），违反即逐条点名。"""
    issues: list[str] = []
    if not pair.differing:
        issues.append(f"{pair.left} × {pair.right} 的顶层差异集为空（形态差异必须落在配置里）")
    for key in PAIRWISE_REQUIRED_DIFFERENCE:
        if key not in pair.differing:
            issues.append(f"{pair.left} × {pair.right} 的差异集缺 {key!r}")
    for key in PAIRWISE_UNCHANGED_SEGMENTS:
        if key in pair.differing:
            issues.append(f"{pair.left} × {pair.right} 的差异集不得含形态无关段 {key!r}")
    return tuple(issues)


# ---------------------------------------------------------------------------
# 反向扫描：形态清单被枚举/写死（白名单之外 ⇒ 第六处）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnumSite:
    """一处"形态清单被枚举/写死"的代码点。`symbol` 是机检锚点，行号只作人读辅助。"""

    path: str
    symbol: str
    line: int
    kind: str  # "tuple" | "list" | "set" | "dict" | "decorator_arg"
    names: tuple[str, ...]

    def describe(self) -> str:
        return f"{self.path}:{self.line}::{self.symbol}（{self.kind}）{list(self.names)}"


def _symbol_for_line(tree: ast.Module, line: int) -> str:
    best: ast.AST | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= line <= end and (best is None or node.lineno >= best.lineno):
            best = node
    return best.name if best is not None else "<module>"


def _decorator_containers(tree: ast.Module) -> set[int]:
    """落在 `@pytest.mark.parametrize(...)` 实参里的容器行（**只看模块级常量会空跑假绿**）。"""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            if _parametrize_argument(decorator) is None:
                continue
            for call in ast.walk(decorator):
                if isinstance(call, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                    lines.add(call.lineno)
    return lines


def _container_names(node: ast.expr, names: frozenset[str]) -> tuple[str, ...]:
    found: list[str] = []
    items: list[ast.expr | None] = []
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        items = list(node.elts)
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            items.extend([key, value])
    for item in items:
        if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in names:
            found.append(item.value)
    return tuple(dict.fromkeys(found))


def form_enum_sites(
    *, configs_dir: Path = CONFIGS_DIR, roots: tuple[str, ...] = ENUM_SCAN_ROOTS
) -> tuple[EnumSite, ...]:
    """全仓"形态清单被枚举/写死"的代码点（任意容器字面量 + 装饰器实参，**含函数体内**）。"""
    from ops.form_guard import form_literals

    names = frozenset(form_literals(configs_dir))
    found: list[EnumSite] = []
    for root in roots:
        base = Path(root)
        if not base.is_absolute():
            base = REPO_ROOT / base
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            decorated = _decorator_containers(tree)
            try:
                display = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                display = path.as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                    continue
                hits = _container_names(node, names)
                if len(hits) < ENUM_MIN_NAMES:
                    continue
                kind = "decorator_arg" if node.lineno in decorated else type(node).__name__.lower()
                found.append(
                    EnumSite(
                        path=display,
                        symbol=_symbol_for_line(tree, node.lineno),
                        line=node.lineno,
                        kind=kind,
                        names=hits,
                    )
                )
    return tuple(found)


def sixth_site_scan(
    *, configs_dir: Path = CONFIGS_DIR, roots: tuple[str, ...] = ENUM_SCAN_ROOTS
) -> tuple[EnumSite, ...]:
    """**白名单之外**的形态枚举点（= "第六处"）。

    集合口径：`FORM_SET_FACE`（白名单的 form_set 面）之内的枚举由各处**委派证明**单独守住
    （`site_delegated`），白名单之外的枚举**必须为空**——新增一处即红，须显式登记。
    """
    return tuple(
        site
        for site in form_enum_sites(configs_dir=configs_dir, roots=roots)
        if site.path not in FORM_SET_FACE
    )
