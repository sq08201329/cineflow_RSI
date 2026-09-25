"""功能 021 / 契约 C5~C8：形态守卫（派生面 + 两层扫描 + 例外三条）的机检。

**为什么**：020 的守卫把 `agents/pilot` 显式排除
（`Test零形态分支静态断言.test_core_与_agents_无形态字面量` 里的 `if "pilot" not in path.parts`），
且形态名清单是写死的字面量表（三个字面量）⇒ **新增一份形态配置就会静默逃逸**。
本文件机检 021 的收敛结果：

1. **派生面**（`declared_forms` = id 面 / `form_literals` = 名称面，**分两个函数、不得混用**）：
   形态名由 `configs/*.yaml` 的 `form:` + `form_aliases` 派生，**零人工常量**；
2. **派生失败即报错**（缺 `form` / `form` 非字符串 / 缺 `form_aliases` / 取值或别名重复 /
   文件名 stem 与 `form` 不一致 / 无配置可派生）；
3. **两层扫描**（`literal_violations` / `branch_violations`）在**同一**扫描面
   （`core/` + `agents/`，**含 `agents/pilot`**）上违规数恒 0；
4. **符号名锚点**（`FormHit` 五字段、`symbol` 非空、模块级 `<module>`、机检**不依赖行号**）；
5. **例外三条**（E1 配置**文件路径**字面量 / E2 扫描面定义 / E3 docstring 中性描述）；
6. **词边界判定**（ASCII 名按词边界、中文名按子串）——短名不得误命中更长标识符；
7. **有牙齿**（三类合成反例**由派生值构造**）。

**本文件零人工形态常量**：一切形态名与形态名清单都由 `declared_forms()` / `form_literals()`
派生，合成反例在运行期用派生值拼出（契约 C5/C6 的"有牙齿自检"要求）。
"""

import ast
import re
from pathlib import Path

import pytest

from ops.form_guard import (
    FormConfigError,
    FormHit,
    branch_violations,
    classify_exception,
    declared_forms,
    form_branch_patterns,
    form_literals,
    iter_sources,
    literal_violations,
    name_pattern,
    violations_in,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
# 派生面（**唯二**输入）：id 面 = 登记面；名称面 = 两层扫描的唯一输入
FORM_NAMES = declared_forms(CONFIGS_DIR)
FORM_FACE = form_literals(CONFIGS_DIR)
assert len(FORM_NAMES) >= 2, "派生面失效：configs/*.yaml 至少两份（两形态）"

# 三处**委派点**（只换常量来源，循环体与断言体原位保留；副本数恒 1）与其形态名常量表
DELEGATION_SITES = {
    "tests/unit/test_form_switch.py": ("BANNED_LITERALS", "BANNED_PATTERNS"),
    "tests/unit/test_billing_core_purity.py": ("FORM_LITERALS", "FORM_PATTERNS"),
    "tests/unit/test_dev_core_degraded_purity.py": ("FORM_LITERALS", "FORM_PATTERNS"),
}
GUARD_FUNCTIONS = (
    "declared_forms",
    "form_literals",
    "form_branch_patterns",
    "name_pattern",
    "violations_in",
    "literal_violations",
    "branch_violations",
)
# 既有断言的符号名锚点（**不引行号**：020 的引用漂移教训）：(文件, 类, 用例, 失败信息关键片段)
ASSERTION_SITES = (
    (
        "tests/unit/test_form_switch.py",
        "Test零形态分支静态断言",
        "test_core_与_agents_无形态字面量",
        "不得出现形态字面量",
    ),
    (
        "tests/unit/test_form_switch.py",
        "Test零形态分支静态断言",
        "test_全仓_agents_与_core_无形态判断分支",
        "不得出现形态判断",
    ),
    (
        "tests/unit/test_form_switch.py",
        "Test零形态分支静态断言",
        "test_渠道解析不得出现形态字面量或形态判断",
        "不得出现形态字面量",
    ),
    (
        "tests/unit/test_billing_core_purity.py",
        "Test零形态与厂商字面量",
        "test_无形态字面量与形态分支",
        "不得出现形态字面量",
    ),
    (
        "tests/unit/test_dev_core_degraded_purity.py",
        "Test零形态与厂商字面量",
        "test_无形态字面量与形态分支",
        "不得出现形态字面量",
    ),
)
# "不在扫描面内、也不得为过断言改写"的既有字面量（E2：扫描面只有 core/ + agents/）
OUTSIDE_SCAN_FACE = (
    "ops/billing.py",
    "ops/ingest_metrics.py",
    "ops/demo_merged_pool.py",
    "ops/smoke_llm.py",
    "web/server.py",
    "web/export.py",
    "dreaming/deploy_hook.py",
)


def _write_configs(base: Path, spec: list[tuple[str, list[str]]]) -> Path:
    """写出一组临时形态配置（取值全部由派生面构造，本文件零人工形态常量）。"""
    configs = base / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    for form, aliases in spec:
        (configs / f"{form}.yaml").write_text(
            f"form: {form}\nform_aliases: [{', '.join(aliases)}]\n", encoding="utf-8"
        )
    return configs


def _write_source(base: Path, rel: str, text: str) -> Path:
    """写出一份合成源码（`rel` 相对 `base`，父目录自动创建）。"""
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _call_name(node: ast.Call) -> str | None:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)


def _method(tree: ast.Module, cls_name: str, func_name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == func_name:
                    return child
    return None


def _assigned_from_guard(path: Path) -> dict[str, str]:
    """收集 `X = <守卫函数调用>` 形态的赋值（X → 被调函数名）；类体内同样计入。"""
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        called = _call_name(value)
        if called not in GUARD_FUNCTIONS:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = called
    return found


def _container_enumeration_hits(path: Path, names: tuple[str, ...]) -> set[str]:
    """反向扫描（T2198 扩展口径的本地投影）：任意容器字面量 + 装饰器实参中的名称面字符串。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: set[str] = set()

    def _collect(elements: list[ast.expr]) -> None:
        for element in elements:
            if isinstance(element, ast.Constant) and element.value in names:
                offenders.add(f"{path.name}:{element.lineno}:{element.value!r}")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            _collect(list(node.elts))
        elif isinstance(node, ast.Dict):
            _collect([key for key in node.keys if key is not None] + list(node.values))
        for decorator in getattr(node, "decorator_list", ()):
            for child in ast.walk(decorator):
                if isinstance(child, ast.Constant) and child.value in names:
                    offenders.add(f"{path.name}:{child.lineno}:{child.value!r}")
    return offenders


class Test派生面:
    """C6：两条派生面（id 面 / 名称面）零人工常量、缺键即报错、文件名与取值一致。"""

    def test_id_面取值唯一且与配置文件名逐字一致(self):
        configs = sorted(CONFIGS_DIR.glob("*.yaml"))
        assert configs, "形态配置面非空（缺配置即报错，不得回落到人工常量兜底）"
        assert all(isinstance(form, str) and form.strip() for form in FORM_NAMES)
        assert len(set(FORM_NAMES)) == len(FORM_NAMES)
        assert len(FORM_NAMES) == len(configs)
        assert {path.stem for path in configs} == set(FORM_NAMES)  # stem == form 取值

    def test_名称面覆盖id面且元素两两唯一(self):
        assert set(FORM_NAMES) <= set(FORM_FACE)
        assert len(set(FORM_FACE)) == len(FORM_FACE)

    def test_别名只进名称面不进_id_面(self, tmp_path):
        alias = f"{FORM_NAMES[0]}-alias"
        # 临时布局覆盖**全部**已声明形态（新增形态自动纳入 ⇒ 本用例不依赖"恰好两份"）
        configs = _write_configs(
            tmp_path, [(FORM_NAMES[0], [alias]), *((name, []) for name in FORM_NAMES[1:])]
        )
        assert declared_forms(configs) == FORM_NAMES
        assert set(form_literals(configs)) == {*FORM_NAMES, alias}
        assert len(form_literals(configs)) == len(FORM_NAMES) + 1
        assert alias not in declared_forms(configs)

    def test_两个派生函数不是一个面(self):
        assert declared_forms is not form_literals
        # 分支模式是**形态无关的语法模式**（六条字面不变），不属形态名常量表
        assert form_branch_patterns() == (
            "form ==",
            "form==",
            "form !=",
            "form!=",
            "form is ",
            "form in ",
        )

    def test_缺_form_键即报错(self, tmp_path):
        configs = _write_configs(tmp_path, [(FORM_NAMES[0], [])])
        (configs / f"{FORM_NAMES[1]}.yaml").write_text("form_aliases: []\n", encoding="utf-8")
        with pytest.raises(FormConfigError):
            declared_forms(configs)
        with pytest.raises(FormConfigError):
            form_literals(configs)

    def test_form_非字符串或空即报错(self, tmp_path):
        configs = _write_configs(tmp_path, [(FORM_NAMES[0], [])])
        (configs / f"{FORM_NAMES[1]}.yaml").write_text(
            "form: []\nform_aliases: []\n", encoding="utf-8"
        )
        with pytest.raises(FormConfigError):
            declared_forms(configs)

    def test_缺_form_aliases_键即报错(self, tmp_path):
        configs = _write_configs(tmp_path, [(FORM_NAMES[0], [])])
        (configs / f"{FORM_NAMES[1]}.yaml").write_text(f"form: {FORM_NAMES[1]}\n", encoding="utf-8")
        with pytest.raises(FormConfigError):
            form_literals(configs)

    def test_取值或别名与他形态冲突即报错(self, tmp_path):
        # 两个文件声明同一个 form 取值（文件名与取值一致性同时被破坏）⇒ 报错
        configs = tmp_path / "configs"
        configs.mkdir(parents=True)
        for stem in FORM_NAMES:
            (configs / f"{stem}.yaml").write_text(
                f"form: {FORM_NAMES[1]}\nform_aliases: []\n", encoding="utf-8"
            )
        with pytest.raises(FormConfigError):
            declared_forms(configs)
        # 别名与他形态的 form 取值冲突（跨文件全部名称两两唯一）
        clash = _write_configs(
            tmp_path / "clash", [(FORM_NAMES[0], []), (FORM_NAMES[1], [FORM_NAMES[0]])]
        )
        with pytest.raises(FormConfigError):
            form_literals(clash)

    def test_文件名与_form_取值不一致即报错(self, tmp_path):
        configs = _write_configs(tmp_path, [(FORM_NAMES[0], [])])
        (configs / f"{FORM_NAMES[1]}.yaml").write_text(
            f"form: {FORM_NAMES[0]}-x\nform_aliases: []\n", encoding="utf-8"
        )
        with pytest.raises(FormConfigError):
            declared_forms(configs)

    def test_无配置可派生即报错_不得兜底人工清单(self, tmp_path):
        configs = tmp_path / "configs"
        configs.mkdir()
        with pytest.raises(FormConfigError):
            declared_forms(configs)
        with pytest.raises(FormConfigError):
            form_literals(configs)


class Test扫描面:
    """C5/C6：两层共用同一扫描面（`core/` + `agents/`，**含 `agents/pilot`**）。"""

    def test_两根下_py_全集无排除(self):
        for root in ("core", "agents"):
            expected = {
                path for path in (REPO_ROOT / root).rglob("*.py") if "__pycache__" not in path.parts
            }
            assert set(iter_sources(root)) == expected
        assert set(iter_sources()) == set(iter_sources("core")) | set(iter_sources("agents"))

    def test_agents_pilot_在面内(self):
        scanned = {path.relative_to(REPO_ROOT).as_posix() for path in iter_sources("agents")}
        assert "agents/pilot/backends.py" in scanned
        assert "agents/pilot/pilot.py" in scanned

    def test_非扫描面目录不在面内(self):
        scanned = {path.relative_to(REPO_ROOT).as_posix() for path in iter_sources()}
        assert scanned.isdisjoint(OUTSIDE_SCAN_FACE)

    def test_E2_是扫描面定义不是逐条豁免(self):
        hit = FormHit(
            path=OUTSIDE_SCAN_FACE[0],
            symbol="<module>",
            line=97,
            hit=FORM_FACE[0],
            layer="literal",
        )
        assert classify_exception(hit) == "E2"

    def test_面外既有默认值未被改写(self):
        # E2：`ops/`/`web/`/`dreaming/` 的既有配置路径默认值与演示形态值**不在扫描面内**，
        # 也**不得**为过断言改写成中性措辞（本特性零触碰）——相关行仍按派生名称面命中
        for rel in OUTSIDE_SCAN_FACE:
            lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
            assert any(
                any(name_pattern(name).search(line) for name in FORM_FACE) for line in lines
            ), rel


class Test符号名锚点:
    """C5：机检定位按符号名（`FormHit.symbol`），行号只作人读辅助。"""

    def test_行号漂移仍由同一符号名定位(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        before = _write_source(
            tmp_path, "core/before.py", f'def target():\n    value = 1\n    marker = "{name}"\n'
        )
        after = _write_source(
            tmp_path,
            "core/after.py",
            f'def target():\n    value = 1\n    value += 1\n    marker = "{name}"\n',
        )
        early = literal_violations(configs, sources=(before,))[0]
        late = literal_violations(configs, sources=(after,))[0]
        assert early.symbol == late.symbol == "target"
        assert early.line != late.line

    def test_模块级与五字段(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        module = _write_source(tmp_path, "core/module_level.py", f'MARKER = "{name}"\n')
        hit = literal_violations(configs, sources=(module,))[0]
        assert hit.symbol == "<module>"
        assert hit.layer == "literal"
        assert hit.hit == name
        assert set(FormHit.__dataclass_fields__) == {"path", "symbol", "line", "hit", "layer"}


class Test判定与例外:
    """C5：词边界是**判定**、例外是**放行**——两者不得互相顶替。"""

    def test_词边界_短名不误命中更长标识符(self):
        # 短名由派生值构造（如 B1 的短 id）：子串判定会命中 `field`/`yield`/`view` 一类常见标识符
        short = FORM_NAMES[0][-2:]
        assert name_pattern(short).search(f'FORM = "{short}"')
        assert name_pattern(short).search(f"configs/{short}.yaml")
        for identifier in (f"f{short}ld", f"y{short}ld", f"v{short}w", f"_{short}1"):
            assert not name_pattern(short).search(f"{identifier} = 1"), identifier

    def test_中文别名按子串判定(self):
        alias = f"{FORM_NAMES[0]}中文别名"
        assert name_pattern(alias).search(f"前缀{alias}后缀")

    def test_有牙齿_裸字面量写在_core_文件即判违规(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        target = _write_source(tmp_path, "core/injected.py", f'FORM = "{name}"\n')
        assert [hit.hit for hit in literal_violations(configs, sources=(target,))] == [name]

    def test_有牙齿_分支写在_agents_pilot_backends_即判违规(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        real = REPO_ROOT / "agents" / "pilot" / "backends.py"
        target = _write_source(
            tmp_path,
            "agents/pilot/backends.py",
            real.read_text(encoding="utf-8") + f'\nif form == "{name}":\n    pass\n',
        )
        # 补面举证：被注入的文件**就是**真实装配点（020 的盲区不再是盲区）
        assert target.read_text(encoding="utf-8") != real.read_text(encoding="utf-8")
        assert real in iter_sources("agents")
        assert [hit.hit for hit in branch_violations(configs, sources=(target,))] == ["form =="]
        assert [hit.hit for hit in literal_violations(configs, sources=(target,))] == [name]

    def test_有牙齿_docstring_取值绑定判违规_中性描述放行(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        binding = _write_source(
            tmp_path, "core/binding.py", f'def f():\n    """取值（{name} ⇒ 30 s）。"""\n'
        )
        assert [hit.hit for hit in literal_violations(configs, sources=(binding,))] == [name]
        neutral = _write_source(
            tmp_path,
            "core/neutral.py",
            f'def f():\n    """形态 {name} 的原值由配置承载，不在此绑定取值。"""\n',
        )
        candidates = literal_violations(configs, sources=(neutral,), include_exceptions=True)
        assert [classify_exception(hit) for hit in candidates] == ["E3"]
        assert literal_violations(configs, sources=(neutral,)) == ()

    def test_E3_硬条件_注释与含等号行不受保护(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        comment = _write_source(tmp_path, "core/comment.py", f"# 形态 {name} 的取值\nVALUE = 1\n")
        assert literal_violations(configs, sources=(comment,)) != ()
        bound = _write_source(
            tmp_path, "core/bound.py", f'def f():\n    """形态 {name}：30 s = 0.5 min。"""\n'
        )
        assert literal_violations(configs, sources=(bound,)) != ()

    def test_E1_只认配置文件路径字面量(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        target = _write_source(
            tmp_path,
            "core/evidence_like.py",
            f'SOURCE = "configs/{name}.yaml gate.require_unbiasedness=false"\n',
        )
        candidates = literal_violations(configs, sources=(target,), include_exceptions=True)
        assert [classify_exception(hit) for hit in candidates] == ["E1"]
        assert literal_violations(configs, sources=(target,)) == ()

    def test_E1_不被滥用(self, tmp_path):
        name = FORM_NAMES[0]
        configs = _write_configs(tmp_path, [(name, [])])
        label = _write_source(tmp_path, "core/label.py", f'FORM_LABEL = "{name}"\n')
        candidates = literal_violations(configs, sources=(label,), include_exceptions=True)
        assert [classify_exception(hit) for hit in candidates] == [None]
        assert [hit.hit for hit in literal_violations(configs, sources=(label,))] == [name]
        mapping = _write_source(
            tmp_path, "core/mapping.py", f'FORM_PATHS = {{"{name}": "configs/{name}.yaml"}}\n'
        )
        offenders = literal_violations(configs, sources=(mapping,))
        assert offenders and all(hit.hit == name for hit in offenders)


class Test真实扫描面:
    """C5~C8 的收敛结果：违规数 0、E1 恰好 1 处、E3 为 0 处、副本数 1。"""

    def test_违规零且例外恰好一处E1_零处E3(self):
        assert literal_violations(CONFIGS_DIR) == ()
        assert branch_violations(CONFIGS_DIR) == ()
        candidates = literal_violations(CONFIGS_DIR, include_exceptions=True)
        assert candidates, "扫描面有效（否则空跑假绿）"
        e1 = [hit for hit in candidates if classify_exception(hit) == "E1"]
        e3 = [hit for hit in candidates if classify_exception(hit) == "E3"]
        assert len(e1) == 1
        assert e1[0].path == "core/deployment/evidence.py"
        assert e1[0].symbol == "_unbiasedness_result"
        # E1 的既有行不得改写：该行仍带**派生**出的配置路径
        text = (REPO_ROOT / e1[0].path).read_text(encoding="utf-8")
        assert any(f"configs/{name}.yaml" in text for name in FORM_FACE)
        assert e3 == []

    def test_新增_E1_命中点会被计数变化捉住(self, tmp_path):
        real = REPO_ROOT / "core" / "deployment" / "evidence.py"
        source = real.read_text(encoding="utf-8")
        # 注入用的形态名取自**既有 E1 行里的配置路径字面量**（不写死形态名；派生面之外的取值
        # 不构成命中 ⇒ 写死形态名会让本用例依赖"派生面首个形态 = 该行形态"这一前提）
        matched = re.search(r"configs/([0-9A-Za-z_-]+)\.yaml", source)
        assert matched, "既有 E1 行的配置路径字面量缺失（本用例会空跑）"
        name = matched.group(1)
        configs = _write_configs(tmp_path, [(name, [])])
        target = tmp_path / "core" / "deployment" / "evidence.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            source + f'\nEXTRA = "configs/{name}.yaml deployment.gate.x=false"\n',
            encoding="utf-8",
        )
        candidates = literal_violations(configs, sources=(target,), include_exceptions=True)
        assert len([hit for hit in candidates if classify_exception(hit) == "E1"]) == 2

    def test_逐文件违规零_两层同一扫描面(self):
        for path in iter_sources():
            text = path.read_text(encoding="utf-8")
            for name in FORM_FACE:
                assert violations_in(text, name, path=str(path)) == ()


class Test委派与副本数:
    """C7：三处委派点只换常量来源；派生面定义点唯一（副本数恒 1）。"""

    def test_三处委派点的常量表改由守卫派生(self):
        for rel, targets in DELEGATION_SITES.items():
            found = _assigned_from_guard(REPO_ROOT / rel)
            expected = {
                "BANNED_LITERALS": "form_literals",
                "BANNED_PATTERNS": "form_branch_patterns",
                "FORM_LITERALS": "form_literals",
                "FORM_PATTERNS": "form_branch_patterns",
            }
            assert {name: found.get(name) for name in targets} == {
                name: expected[name] for name in targets
            }

    def test_三处委派点的用例体原位保留(self):
        for rel, cls_name, func_name, message in ASSERTION_SITES:
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
            func = _method(tree, cls_name, func_name)
            assert func is not None, f"{rel}::{cls_name}::{func_name} 被删除"
            asserts = [node for node in ast.walk(func) if isinstance(node, ast.Assert)]
            assert asserts, f"{rel}::{func_name} 的断言被删空"
            messages = [ast.unparse(node.msg) for node in asserts if node.msg is not None]
            assert any(message in text for text in messages), f"{rel}::{func_name} 失败信息形态被改"
            assert any(isinstance(node, ast.For) for node in ast.walk(func)), "循环体被删"
            # 委派证明：用例体内**调用**守卫函数，或**引用**由守卫派生的常量表（两者都算委派）
            called = {_call_name(node) for node in ast.walk(func) if isinstance(node, ast.Call)}
            referenced = {
                node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
                for node in ast.walk(func)
                if isinstance(node, (ast.Name, ast.Attribute))
            }
            derived = set(_assigned_from_guard(REPO_ROOT / rel))
            assert (called & set(GUARD_FUNCTIONS)) or (referenced & derived), (
                f"{rel}::{func_name} 未委派到守卫派生面（既没调用，也没引用派生常量表）"
            )

    def test_派生面定义点唯一(self):
        counts = {name: 0 for name in ("declared_forms", "form_literals", "form_branch_patterns")}
        for root in ("core", "agents", "ops", "tests", "web", "dreaming"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if node.name in counts:
                        counts[node.name] += 1
        assert counts == {"declared_forms": 1, "form_literals": 1, "form_branch_patterns": 1}

    def test_守卫与自身测试零人工形态常量(self):
        offenders: set[str] = set()
        for path in (REPO_ROOT / "ops" / "form_guard.py", Path(__file__)):
            offenders |= _container_enumeration_hits(path, FORM_FACE)
        assert offenders == set(), sorted(offenders)
