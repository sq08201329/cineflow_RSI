"""形态接入登记点面 + 接入改动清单 + 机制侧总账（021 C9/C10/C12/C13 的机检实现）。

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
import json
import subprocess
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


# ---------------------------------------------------------------------------
# 接入改动清单（C12）：基线派生 / 类别判定 / 指纹 / append-only / 越界即红
# ---------------------------------------------------------------------------

# 退出码语义（与既有工具一致；常量符号沿用 `ops/transfer.py` 的先例）
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
# 清单的语义版本（C12 的字段权威表：当前取值 `1`；只增不改）
SCHEMA = 1
# 类别枚举（**英文，权威**）：判定一律走它，`counts` 的中文键只作报表人读面
CATEGORIES: tuple[str, ...] = ("config", "plugin", "test_doc", "out_of_scope")
# `counts` 的中文键名（权威；多/少键即红）
COUNTS_KEYS: tuple[str, ...] = ("配置", "插件", "测试与文档", "越界", "既有模块被修改")
# 类别 → counts 中文键（唯一映射；`越界` 单列）
_CATEGORY_COUNTS_KEY = {
    "config": "配置",
    "plugin": "插件",
    "test_doc": "测试与文档",
    "out_of_scope": "越界",
}
# 状态枚举（`git diff --name-status` 的取值；未跟踪新增文件恒为 `A`）
STATUS_CODES: tuple[str, ...] = ("A", "M", "D")
# 放行面（按**类别**判定，不按路径前缀）
_TEST_DOC_PREFIXES: tuple[str, ...] = ("tests/", "docs/", "specs/")
# 仓库根的**交付文档**（裁决 2026-09-26）：`README.md` 属"测试与文档"面、不是模块逻辑改动。
# **牙齿保留**：本放行面**只**含文档路径——`core/` / `agents/` / `ops/` / `web/` / `dreaming/` /
# `policies/` 的**既有文件修改**仍是 `out_of_scope`，新增 `ops/**` 亦一律越界（判据不动）。
_TEST_DOC_FILES: tuple[str, ...] = ("README.md",)
_PLUGIN_TARGET_PREFIX = "core/evaluators/plugins/"
_CONFIG_PREFIX = "configs/"
# `index.jsonl` 每行的字段集（**恰好七键**；C12 的字段权威表）
INDEX_KEYS: tuple[str, ...] = (
    "baseline_ref",
    "form",
    "config_path",
    "config_fingerprint",
    "change_count",
    "violations",
    "exit_code",
)
# 产物层固定字段（C11 三层标注之产物层；键名权威在 C14）
UNCALIBRATED_REASON = (
    "受众 / 指标口径 / 素材规格 / 预算档属业务侧输入，未给定 ⇒ 新形态按最小可行形态接入"
    "（未标定）；机制已就绪、真实节律与业务数字待运营给定后只改配置值"
)


class OnboardingError(ValueError):
    """清单机制的用法/配置错误（基线取错、基线不可解析、配置不可读、形态 id 不一致）。"""


@dataclass(frozen=True)
class ChangedFile:
    """一处改动（`path` 相对仓库根、`status` ∈ `STATUS_CODES`）。"""

    path: str
    status: str


def _git(args: list[str], repo_root: Path) -> str:
    # `core.quotePath=false`：git 默认对**非 ASCII 路径**输出 C 转义引号
    # （如 `"docs/\344\270\211..."`）⇒ 路径前缀判定会失配（中文路径被误判 `out_of_scope`）。
    # 关闭引号后拿到**原样 UTF-8 路径**，清单条目的 `path` 与真实路径逐字相同。
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise OnboardingError(f"git {' '.join(args)} 失败：{completed.stderr.strip()}")
    return completed.stdout


def rev_parse(ref: str, *, repo_root: Path = REPO_ROOT) -> str:
    """把 ref 解析为提交哈希（不可解析即报错——不猜、不静默兜底到 HEAD）。"""
    if not isinstance(ref, str) or not ref.strip():
        raise OnboardingError("ref 必须为非空字符串（基线必须显式给出，不默认取 HEAD）")
    return _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo_root).strip()


def _normalize_status(status: str) -> str:
    code = str(status).strip().upper()[:1]
    if code in ("R", "C"):  # 改名/拷贝 = 对既有文件的修改（既有实现口径）
        return "M"
    if code not in STATUS_CODES:
        raise OnboardingError(f"非法改动状态：{status!r}（取值域 {list(STATUS_CODES)}）")
    return code


def untracked_files(*, repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """仓库根的**未跟踪文件集合**（`git ls-files --others --exclude-standard`）。

    "--out 必须落临时目录"与"跑完仓库根零新增文件"的判据 = 该集合**前后逐条相等**。
    """
    return tuple(
        line.strip()
        for line in _git(["ls-files", "--others", "--exclude-standard"], repo_root).splitlines()
        if line.strip()
    )


def changed_files(baseline_ref: str, *, repo_root: Path = REPO_ROOT) -> tuple[ChangedFile, ...]:
    """由 git **派生**改动集合：`git diff --name-status <ref>` ∪ `git ls-files --others`。

    **不手工列举** ⇒ "自报漏项"在机制上不可能发生。未跟踪的新增文件（新形态配置与新插件在
    接入时通常是**未跟踪**文件——它们只出现在 `git ls-files --others` 一侧，`git diff` 看不见）
    **必须在内**——漏了它们，"新增"整类会消失、越界计数反而"看起来干净"（这是最常见的漏项模式）。
    """
    rev_parse(baseline_ref, repo_root=repo_root)
    found: dict[str, str] = {}
    for line in _git(["diff", "--name-status", baseline_ref], repo_root).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        found[parts[-1]] = _normalize_status(parts[0])
    for line in _git(["ls-files", "--others", "--exclude-standard"], repo_root).splitlines():
        path = line.strip()
        if path and path not in found:
            found[path] = "A"
    return tuple(ChangedFile(path=path, status=found[path]) for path in sorted(found))


def classify(path: str, status: str) -> str:
    """按**类别**判定（**不是**按路径前缀）：配置 / 插件 / 测试与文档 / 越界。

    `agents/<agent>/evaluators/` 前缀下，**新增**插件文件放行、**修改**该前缀下的既有文件越界
    ——前缀判定**无法区分这两件事**，故必须同时看 `status` 与类别。`ops/**` 的**任何**改动
    （新增或修改）都判 `out_of_scope`：一旦给 `ops/` 开放行面，本清单就再也证明不了
    "新形态接入 = 仅新增配置 + 插件"。

    **测试与文档面**（裁决 2026-09-26）= `tests/**` / `docs/**` / `specs/**` 的新增或修改
    **与仓库根 `README.md`**（交付文档，不是模块逻辑改动）；放行面**只**含文档路径——
    六目录（`core/` / `agents/` / `ops/` / `web/` / `dreaming/` / `policies/`）的既有文件修改、
    以及三类之外的新增文件（含新增 `ops/**`）**一律仍是 `out_of_scope`**。
    """
    code = _normalize_status(status)
    if code == "A" and path.startswith(_CONFIG_PREFIX) and path.endswith(".yaml"):
        return "config"
    if code == "A" and (
        path.startswith(_PLUGIN_TARGET_PREFIX)
        or (path.startswith("agents/") and "/evaluators/" in path)
    ):
        return "plugin"
    if path in _TEST_DOC_FILES or path.startswith(_TEST_DOC_PREFIXES):
        return "test_doc"
    return "out_of_scope"


def _reason(change: ChangedFile, category: str) -> str:
    if category == "config":
        return "新增形态配置（形态以配置文件为唯一载体）"
    if category == "plugin":
        return "新增插件（经 impl 声明才生效——目录不决定可用性、配置声明才决定）"
    if category == "test_doc":
        return f"{'新增' if change.status == 'A' else '修改/删除'}登记点同步与文档"
    if change.status in ("M", "D"):
        return f"{'修改' if change.status == 'M' else '删除'}既有模块 {change.path}（越界）"
    return f"三类放行面之外的新增文件 {change.path}（越界：新增 core 机制模块 / 新增 ops CLI 等）"


def config_fingerprint(config_path: str | Path) -> str:
    """该形态配置文件的 BLAKE3 十六进制摘要**前 12 位**（口径沿用 `fingerprint_of`）。"""
    from core.orchestration.models import fingerprint_of

    path = Path(config_path)
    if not path.is_file():
        raise OnboardingError(f"形态配置不可读：{path}")
    return fingerprint_of(path.read_bytes())[:12]


def _form_of(config_path: Path) -> str:
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OnboardingError(f"形态配置不可读：{config_path}（{exc}）") from exc
    if not isinstance(document, dict) or not isinstance(document.get("form"), str):
        raise OnboardingError(f"形态配置缺顶层 form 键：{config_path}")
    form = document["form"]
    if config_path.stem != form:
        raise OnboardingError(
            f"配置文件名 stem 必须逐字等于 form 取值（{config_path.stem!r} != {form!r}）"
            "——形态 id 与配置路径必须一一对应"
        )
    return form


def build_manifest(
    config_path: str | Path,
    baseline_ref: str,
    *,
    mechanism_ledger_ref: str | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """产出完整清单（逐条路径 + 类别 + 越界标记 + 计数 + 回溯字段）。

    **两个 ref 并排落产物头部且必须不相等**：`baseline_ref`（接入前）与
    `mechanism_ledger_ref`（机制落地后）。两者相等 ⇒ **基线取错**（机制侧改动会被算成接入越界，
    判据自相矛盾）⇒ 报错，不"取最接近的一个"兜底。
    """
    if not isinstance(baseline_ref, str) or not baseline_ref.strip():
        raise OnboardingError("baseline_ref 必须非空（缺 --baseline 即用法错误，不默认取 HEAD）")
    if not isinstance(mechanism_ledger_ref, str) or not mechanism_ledger_ref.strip():
        raise OnboardingError("mechanism_ledger_ref 必须非空（机制落地后的提交 ref）")
    path = Path(config_path)
    resolved_root = Path(repo_root)
    resolved_path = path if path.is_absolute() else resolved_root / path
    form = _form_of(resolved_path)
    baseline_hash = rev_parse(baseline_ref, repo_root=resolved_root)
    mechanism_hash = rev_parse(mechanism_ledger_ref, repo_root=resolved_root)
    if mechanism_hash == baseline_hash:
        raise OnboardingError(
            f"baseline_ref == mechanism_ledger_ref（{baseline_ref}）：基线取错，"
            "机制侧改动会被算成接入越界、判据自相矛盾"
        )
    changes = tuple(
        {
            "path": change.path,
            "status": change.status,
            "category": (category := classify(change.path, change.status)),
            "violation": category == "out_of_scope",
            "reason": _reason(change, category),
        }
        for change in changed_files(baseline_ref, repo_root=resolved_root)
    )
    counts = {key: 0 for key in COUNTS_KEYS}
    for change in changes:
        counts[_CATEGORY_COUNTS_KEY[change["category"]]] += 1
    counts["既有模块被修改"] = sum(
        1 for change in changes if change["violation"] and change["status"] in ("M", "D")
    )
    violations = tuple(change["path"] for change in changes if change["violation"])
    return {
        "schema": SCHEMA,
        "baseline_ref": baseline_ref,
        "mechanism_ledger_ref": mechanism_ledger_ref,
        "form": form,
        "config_path": resolved_path.relative_to(resolved_root).as_posix(),
        "config_fingerprint": config_fingerprint(resolved_path),
        "changes": list(changes),
        "counts": counts,
        "violations": list(violations),
        "mechanism_changes_included": False,
        "zero_code_onboarding": not violations,
        "exit_code": EXIT_FAILED if violations else EXIT_OK,
        "uncalibrated": True,
        "uncalibrated_reason": UNCALIBRATED_REASON,
    }


def write_manifest(manifest: dict, out_dir: str | Path) -> Path:
    """**append-only** 落盘：清单文件写后不回改（同内容重跑产生**新序号**），
    `index.jsonl` 追加一行。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    form = str(manifest["form"])
    prefix = f"onboarding-{form}-"
    seq = 1 + max(
        (
            int(path.stem[len(prefix) :])
            for path in out.glob(f"{prefix}*.json")
            if path.stem[len(prefix) :].isdigit()
        ),
        default=0,
    )
    target = out / f"{prefix}{seq:04d}.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    line = {
        "baseline_ref": manifest["baseline_ref"],
        "form": form,
        "config_path": manifest["config_path"],
        "config_fingerprint": manifest["config_fingerprint"],
        "change_count": len(manifest["changes"]),
        "violations": list(manifest["violations"]),
        "exit_code": manifest["exit_code"],
    }
    with (out / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return target


# ---------------------------------------------------------------------------
# 机制侧总账（C13）：FR-013 的六项 + `MECHANISM_LEDGER_PATHS`（路径并集，**不写死条数**）
# ---------------------------------------------------------------------------

_A1_FIXTURE_SYNC_FACE: tuple[str, ...] = (
    # A1 夹具同步面：**实测改动集**（`tests/**` 内内联配置字典夹具补 `evaluators` 段）。
    # 派生口径是"凡在 `tests/**` 内调用六个 `build_*_evaluators` 的测试文件"（由符号调用反查）——
    # 但**符号命中面 ⊋ 改动面**：实测命中 16 个文件里，只有下面 5 个（+ `tests/conftest.py`，归 ⑤）
    # 真的需要补声明段；其余 10 个从**真实形态配置**取 `evaluators` 段 ⇒ 未改动、**不登记**
    # （本表以 git 实测改动集为准，`NOT_LEDGER_ITEMS` 逐条登记剔除面）。
    "tests/unit/test_dev_composite.py",
    "tests/unit/test_editing_composite.py",
    "tests/unit/test_screenplay_cli.py",
    "tests/unit/test_screenplay_composite.py",
    "tests/unit/test_storyboard_composite.py",
)

MECHANISM_LEDGER: tuple[dict, ...] = (
    {
        "ordinal": "①",
        "step": "A1/A4",
        "summary": "配置驱动的插件声明与唯一装配点（声明面 + importlib 解析 + 通用参数通道 + "
        "既有装配面改委派 + 声明面承载 + 测试侧夹具/存根 + A1 夹具同步面 + cadence 收口的加载面）",
        "items": (
            *(
                {"path": path, "kind": "new"}
                for path in (
                    "agents/dev/config.py",
                    "agents/dev/evaluators/plugins.py",
                    "agents/editing/config.py",
                    "agents/editing/evaluators/plugins.py",
                    "agents/screenplay/config.py",
                    "agents/screenplay/evaluators/plugins.py",
                    "agents/sound/config.py",
                    "agents/sound/evaluators/plugins.py",
                    "agents/storyboard/config.py",
                    "agents/storyboard/evaluators/plugins.py",
                    "agents/visual/config.py",
                    "agents/visual/evaluators/plugins.py",
                    "core/evaluators/plugin.py",
                    "tests/contract/test_plugin_contracts.py",
                    "tests/plugin_fixtures.py",
                    "tests/plugin_stubs.py",
                    "tests/unit/fixtures/dependency_baseline.json",
                    "tests/unit/fixtures/evaluator_assembly_baseline.json",
                    "tests/unit/test_evaluator_plugin_assembly.py",
                    "tests/unit/test_form_no_new_dependency.py",
                )
            ),
            *(
                {"path": path, "kind": "modified"}
                for path in (
                    "agents/dev/evaluators/__init__.py",
                    "agents/editing/evaluators/__init__.py",
                    "agents/screenplay/evaluators/__init__.py",
                    "agents/sound/evaluators/__init__.py",
                    "agents/storyboard/evaluators/__init__.py",
                    "agents/visual/loop.py",
                    "configs/movie.yaml",
                    "configs/shortdrama.yaml",
                    "core/calibration/config.py",
                    "core/evaluators/errors.py",
                    *_A1_FIXTURE_SYNC_FACE,
                )
            ),
        ),
    },
    {
        "ordinal": "②",
        "step": "A2",
        "summary": "扫描面补面（字面量与判断分支两层均覆盖 core/ + agents/ **含 agents/pilot**、"
        "锚点改符号名）",
        "items": (
            {"path": "ops/form_guard.py", "kind": "new"},
            {"path": "tests/unit/test_form_guard.py", "kind": "new"},
            {"path": "tests/unit/test_billing_core_purity.py", "kind": "modified"},
            {"path": "tests/unit/test_dev_core_degraded_purity.py", "kind": "modified"},
            {"path": "tests/unit/test_form_switch.py", "kind": "modified"},
        ),
    },
    {
        "ordinal": "③",
        "step": "A2/A3",
        "summary": "形态名由 configs/*.yaml 派生 + 三副本收敛为单一实现（副本数 ⇒ 1）+ "
        "全仓同族「两形态枚举」副本（T2146/T2196 普查）逐处委派",
        "items": (
            {"path": "ops/form_guard.py", "kind": "new"},
            {"path": "ops/demo_shortdrama_feedback.py", "kind": "modified"},
            {"path": "ops/dev.py", "kind": "modified"},
            {"path": "ops/screenplay.py", "kind": "modified"},
            {"path": "tests/contract/test_billing_contracts.py", "kind": "modified"},
            {"path": "tests/contract/test_llm_profile_contracts.py", "kind": "modified"},
            {"path": "tests/contract/test_pilot_contracts.py", "kind": "modified"},
            {"path": "tests/contract/test_pilot_film_contracts.py", "kind": "modified"},
            {"path": "tests/contract/test_transfer_contracts.py", "kind": "modified"},
            {"path": "tests/unit/test_billing_channels.py", "kind": "modified"},
            {"path": "tests/unit/test_billing_config.py", "kind": "modified"},
            {"path": "tests/unit/test_billing_gateway_cells.py", "kind": "modified"},
            {"path": "tests/unit/test_billing_peak_windows.py", "kind": "modified"},
            {"path": "tests/unit/test_calibration_transfer.py", "kind": "modified"},
            {"path": "tests/unit/test_dev_policy_loader.py", "kind": "modified"},
            {"path": "tests/unit/test_no_vendor_literals.py", "kind": "modified"},
            {"path": "tests/unit/test_pilot_backend_selection.py", "kind": "modified"},
            {"path": "tests/unit/test_pilot_rehearsal.py", "kind": "modified"},
            {"path": "tests/unit/test_smoke_llm_profile.py", "kind": "modified"},
        ),
    },
    {
        "ordinal": "④",
        "step": "A3/A4",
        "summary": "agents/pilot/pilot.py 的裸形态词收敛 + 020 口径逐项机检"
        "（form_clause_completeness：七项 + 不适用显式声明 + 三层未标定标注）",
        "items": (
            {"path": "agents/pilot/pilot.py", "kind": "modified"},
            {"path": "tests/unit/test_form_clause_completeness.py", "kind": "new"},
        ),
    },
    {
        "ordinal": "⑤",
        "step": "A3",
        "summary": '"恰好两份"升级为**登记完备**口径（禁止删除）',
        "items": (
            {"path": "tests/conftest.py", "kind": "modified"},
            {"path": "tests/unit/test_config_integrity.py", "kind": "modified"},
            {"path": "tests/unit/test_form_registration.py", "kind": "new"},
            {"path": "tests/unit/test_form_switch.py", "kind": "modified"},
            {"path": "tests/unit/test_pilot_chain_seven.py", "kind": "modified"},
        ),
    },
    {
        "ordinal": "⑥",
        "step": "A5",
        "summary": "接入改动清单机检与 CLI/演示（A5 同批创建；演示**形态无关**）",
        "items": (
            {"path": "ops/demo_form_plugin.py", "kind": "new"},
            {"path": "ops/form_onboarding.py", "kind": "new"},
            {"path": "ops/form_plugin.py", "kind": "new"},
            {"path": "tests/contract/test_form_onboarding_contracts.py", "kind": "new"},
            {"path": "tests/unit/test_form_onboarding.py", "kind": "new"},
        ),
    },
)

# 路径并集（**条数由本常量给出、不写死**；判据 = 与 quickstart 的"机制侧总账"表**集合相等**）
MECHANISM_LEDGER_PATHS: tuple[str, ...] = tuple(
    sorted({item["path"] for entry in MECHANISM_LEDGER for item in entry["items"]})
)

# 排除面（机检在做集合比较前**必须逐条剔除**；否则"文档表与常量集合相等"会被行文引用污染）：
# ① **不存在**的路径：`tests/unit/test_visual_composite.py` 与
#    `tests/contract/test_{screenplay,storyboard,editing,sound}_contracts.py` 一族
#    （实测：visual 的装配调用点在 `tests/unit/test_visual_consistency.py`；
#    `tests/contract/` 下只有 `test_dev_contracts.py` 命中）——不为凑数保留不存在的路径；
# ② **符号命中但实测未改动**的夹具（契约 C13 的 15 行子表把"调用 `build_*_evaluators` 的文件面"
#    当成了"改动面"，实测**只有 5 个**真的需要补声明段）：下面 10 条为剔除面；
# ③ `tests/unit/test_calibration_config.py` 是"明确不动的既有文件"（cadence 越界新用例落在
#    `tests/unit/test_form_clause_completeness.py`）；
# ④ **注释/文档面**：`agents/pilot/{stages,run_report}.py` 与 `docs/三期立项书.md` 承载的是
#    019 构造点普查事实（13 → 14）的**更正**（注释与文档数字），无语义变更、不服务 FR-013 的
#    任一项；C13 明文"不新造第 7 项"且 `docs/**` 的文档变更**不重复登记** ⇒ 一律不入账；
# ⑤ 设计件（plan/research/quickstart/data-model/spec）与**裸文件名片段**是行文引用，不是条目。
NOT_LEDGER_ITEMS: tuple[str, ...] = (
    "agents/pilot/run_report.py",
    "agents/pilot/stages.py",
    "docs/三期立项书.md",
    "tests/contract/test_dev_contracts.py",
    "tests/contract/test_editing_contracts.py",
    "tests/contract/test_screenplay_contracts.py",
    "tests/contract/test_sound_contracts.py",
    "tests/contract/test_storyboard_contracts.py",
    "tests/unit/test_calibration_config.py",
    "tests/unit/test_dev_compare_adopt.py",
    "tests/unit/test_screenplay_compare_adopt.py",
    "tests/unit/test_sound_composite.py",
    "tests/unit/test_visual_composite.py",
    "tests/unit/test_visual_consistency.py",
    "tests/unbiasedness/test_dev_unbiased.py",
    "tests/unbiasedness/test_editing_unbiased.py",
    "tests/unbiasedness/test_screenplay_unbiased.py",
    "tests/unbiasedness/test_sound_unbiased.py",
    "tests/unbiasedness/test_storyboard_unbiased.py",
    "plan.md",
    "research.md",
    "quickstart.md",
    "data-model.md",
    "spec.md",
    "__init__.py",
)


def ledger_paths_of(entry: dict) -> tuple[str, ...]:
    """一项总账的路径（保序；重复路径只在**首次归属项**计数，此处按项内去重返回）。"""
    seen: dict[str, None] = {}
    for item in entry["items"]:
        seen.setdefault(item["path"], None)
    return tuple(seen)


def mechanism_ledger_of(path: str) -> tuple[dict, ...]:
    """该路径归属的总账项（可能多项：`ops/form_guard.py` 服务 ②③；无归属 ⇒ 空元组）。"""
    return tuple(
        entry for entry in MECHANISM_LEDGER if any(item["path"] == path for item in entry["items"])
    )
