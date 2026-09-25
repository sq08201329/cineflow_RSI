"""功能 021 A3（T2137）：五处登记点的**逐形态**完备性 + 登记完备三条件 + 不新造第六处。

覆盖 `specs/021-form-plugin-validation/contracts/form-registration.md` 的 **C9 / C10**：

- **逐处逐形态判定**：对 `declared_forms()` 的**每一个**取值，五处逐一判定"已登记 + 已委派"，
  缺项**点名是哪一处、缺哪个形态**（不得只报总数）；
- **登记完备三条件**（替代"恰好两份"，**禁止删除**；下界 ①/③ + 双向集合 ②）；
- **不新造第六处**：`REGISTRATION_SITES` 恰好五处 + 白名单之外的反向扫描集合**为空**；
- **委派证明**：每处形态集合表达式**不得**是字面量元组/列表（AST 判定），必须到达派生面；
- **id 面 / 名称面不混用**：登记比较一律用 `declared_forms()`，`form_literals()` 参与次数恒 0；
- **有牙齿自检**（故意越界取证，不得空跑）：临时第三形态配置 + 人工枚举 ⇒ 双向集合条件必红；
  两份临时配置 `form:` 取值相同 ⇒ 两两唯一条件必红；`configs/` 指向空目录/单份配置 ⇒ 下界必红；
  注入**函数体内元组**与**装饰器实参** ⇒ 反向扫描各自必红。

形态名**唯一**来自 `ops/form_guard.py` 的派生面（`configs/*.yaml` 的 `form:` + `form_aliases`），
登记面判定与反向扫描**唯一**实现在 `ops/form_onboarding.py`（本文件不重写判定、不另存形态清单）。
"""

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from ops.form_guard import FormConfigError, declared_forms, form_literals
from ops.form_onboarding import (
    FORM_SET_FACE,
    MIN_CONFIG_COUNT,
    PAIRWISE_REQUIRED_DIFFERENCE,
    PAIRWISE_UNCHANGED_SEGMENTS,
    REGISTRATION_SITES,
    EnumSite,
    RegistrationSite,
    form_enum_sites,
    pairwise_differences,
    pairwise_violations,
    registration_completeness,
    site_registration_status,
    sixth_site_scan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
FORM_SWITCH = REPO_ROOT / "tests" / "unit" / "test_form_switch.py"
# C10 的用例与实现面：引用名称面（`form_literals`）的次数必须为 0
ID_FACE_IMPLEMENTATION = ("registration_completeness", "registered_forms", "site_delegated")
# 形态无关基建段（逐对差异集里必须**逐字相同**）
UNCHANGED_SEGMENTS = PAIRWISE_UNCHANGED_SEGMENTS


# 注入用的临时形态名（只写进 `tmp_path` 的临时 `configs/`；**不进仓库配置**、不构成形态枚举）
_TEMP_FORM = "ad"
_TEMP_FORM_ALT = "animated"
# 临时三形态布局 = 派生面全部形态 + 一个临时形态（**不写形态集合字面量**）
_TEMP_THREE_FORMS = (*declared_forms(CONFIGS_DIR), _TEMP_FORM)
_TEMP_THREE_FORMS_ALT = (*declared_forms(CONFIGS_DIR), _TEMP_FORM_ALT)


def _write_config(directory: Path, stem: str, form: str, *, aliases: tuple[str, ...] = ()) -> Path:
    """临时形态配置（只含派生面必需的 `form` / `form_aliases` 两键）。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.yaml"
    path.write_text(
        yaml.safe_dump({"form": form, "form_aliases": list(aliases)}, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _synthetic_site(
    root: Path, source: str, *, symbol: str = "FORMS", name: str = "tmp_registration_site.py"
) -> RegistrationSite:
    """临时登记点（注入面）：把一份人工枚举写进临时仓库布局（`name` 区分同一次用例的多处注入）。"""
    target = root / "tests" / "unit" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return RegistrationSite(
        path=f"tests/unit/{name}",
        symbol=symbol,
        kind="form_set",
        anchor=symbol,
        delegate="declared_forms",
    )


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"未找到函数 {name}（该用例被删除了？）")


class Test五处登记点逐形态判定:
    """C9 的机检断言：逐处登记率 100% + 委派证明 100% + 缺项点名。"""

    def test_每处对派生面的每个形态都已登记且已委派(self):
        declared = declared_forms(CONFIGS_DIR)
        assert declared, "派生面为空 ⇒ 登记点判定空跑（配置缺失即报错，不得静默全绿）"
        statuses = registration_completeness().sites
        assert len(statuses) == len(REGISTRATION_SITES) == 5
        for status in statuses:
            for form in declared:
                assert form in status.registered, status.describe()
                assert form not in status.missing, status.describe()
            assert status.foreign == (), status.describe()
            assert status.delegated, f"{status.site.path}::{status.site.symbol} 未委派派生面"
            assert status.ok, status.describe()
            assert "已登记 + 已委派" in status.describe()

    def test_缺项点名是哪一处缺哪个形态(self, tmp_path):
        """③ 的注入面：**临时第三形态配置 + 一处人工枚举** ⇒ 双向集合条件逐处点名 `ad`。"""
        configs = tmp_path / "configs"
        for stem in _TEMP_THREE_FORMS:
            _write_config(configs, stem, stem)
        site = _synthetic_site(tmp_path, 'FORMS = ("movie", "shortdrama")\n', symbol="FORMS")
        status = site_registration_status(site, configs_dir=configs, repo_root=tmp_path)
        assert status.registered == declared_forms(CONFIGS_DIR)
        assert status.missing == (_TEMP_FORM,)  # 点名"缺哪个形态"
        assert status.delegated is False  # 委派证明失败（字面量元组）
        assert "tests/unit/tmp_registration_site.py" in status.describe()  # 点名"哪一处"
        assert "ad" in status.describe()
        assert not status.ok

    def test_委派证明是AST判定而非文本匹配(self):
        """形态集合表达式**不得**是字面量元组/列表；必须是到达派生面的调用。"""
        for site in REGISTRATION_SITES:
            if site.kind != "form_set":
                continue  # ④ 是按配置路径通用的 clause_list 面（委派判定另立）
            source = (REPO_ROOT / site.path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            assignments = {
                target.id: node.value
                for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            anchor = assignments.get(site.anchor)
            if anchor is None:
                # parametrize 实参面（②）：取该函数的装饰器第二个实参
                node = _function_def(tree, site.anchor)
                anchors = [
                    decorator.args[1]
                    for decorator in node.decorator_list
                    if isinstance(decorator, ast.Call)
                    and len(decorator.args) >= 2
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                    and {item.strip() for item in decorator.args[0].value.split(",")}
                    & {"form", "config_path"}
                ]
                assert anchors, f"{site.path} 的 parametrize 形态实参面缺失"
                anchor = anchors[0]
            literal_container = isinstance(anchor, ast.Tuple | ast.List | ast.Set) and all(
                isinstance(item, ast.Constant) for item in anchor.elts
            )
            assert not literal_container, (
                f"{site.path}::{site.anchor} 的形态集合表达式退回字面量"
                "（新形态会在该处静默逃逸）：" + ast.unparse(anchor)
            )

    def test_id面与名称面不混用(self):
        """登记完备是 id 面的事：C10 的实现面引用 `form_literals` 的次数恒 0。"""
        import ops.form_onboarding as module

        for name in ID_FACE_IMPLEMENTATION:
            source = inspect.getsource(getattr(module, name))
            assert "form_literals" not in source, f"{name} 不得引用名称面（中文别名会打破 ⊆ 比较）"
        # 登记面求值器只认 id 面（结构性守卫：名称面进登记比较即报错）
        faces = [item for item in module._REGISTRATION_FACES]
        assert faces == ["declared_forms"]
        # ①′（T2139）的用例同样不得引用名称面
        switch = _function_def(
            ast.parse(FORM_SWITCH.read_text(encoding="utf-8")), "test_形态切换只经配置文件"
        )
        assert "form_literals" not in ast.unparse(switch)

    def test_文件名stem必须等于form取值(self):
        """C10 的前提断言：形态 id 可零人工常量反查回**唯一**配置路径。"""
        for form in declared_forms(CONFIGS_DIR):
            assert (CONFIGS_DIR / f"{form}.yaml").is_file(), f"{form} 反查不到唯一配置路径"
            payload = yaml.safe_load((CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8"))
            assert payload["form"] == form

    def test_单份配置文件名与取值不一致即报错(self, tmp_path):
        configs = tmp_path / "configs"
        _write_config(configs, "movie", "shortdrama")
        with pytest.raises(FormConfigError, match="stem"):
            declared_forms(configs)


class Test登记完备三条件:
    """C10：三条件并列（两两唯一 ∧ 双向集合 ∧ 下界 ≥2），"恰好两份"**未被删除**。"""

    def test_三条并列条件全部成立(self):
        report = registration_completeness()
        assert report.unique  # ① 两两唯一（重复 / 缺键 ⇒ 派生即报错）
        assert report.registered_matches  # ② 双向相等（缺项 ⇒ 逐处点名）
        assert report.at_least_two  # ③ 下界保留
        assert report.violations() == ()
        assert report.config_count == len(declared_forms(CONFIGS_DIR))

    def test_下界恒为2不得提到3(self):
        assert MIN_CONFIG_COUNT == 2, "下界提到 3 会把机制可用与本次接入的形态数量耦合"

    def test_恰好两份断言未被删除且已升级为三条并列(self):
        """C10 的"禁止删除"机检：按**符号名**定位同一用例，函数体内含三条并列断言。"""
        tree = ast.parse(FORM_SWITCH.read_text(encoding="utf-8"))
        node = _function_def(tree, "test_形态切换只经配置文件")
        asserts = [item for item in ast.walk(node) if isinstance(item, ast.Assert)]
        assert len(asserts) >= 3, "该用例必须含三条并列断言（两两唯一 / 双向集合 / 下界）"
        body = ast.unparse(node)
        for field in ("unique", "registered_matches", "at_least_two"):
            assert field in body, f"三条并列断言缺 {field}（不得以删除/放宽换取通过）"

    def test_逐对形态差异集非空且不含形态无关段(self):
        """T2138/T2141 的"逐形态对"常驻断言（口径共用同一实现 ⇒ 两处不各写一份）。"""
        pairs = pairwise_differences()
        assert pairs, "派生面只有一个形态 ⇒ 逐对断言空跑（登记完备需 ≥2 份配置）"
        for pair in pairs:
            assert pairwise_violations(pair) == (), pairwise_violations(pair)
            for key in PAIRWISE_REQUIRED_DIFFERENCE:
                assert key in pair.differing
            assert pair.differing
            for key in UNCHANGED_SEGMENTS:
                assert key not in pair.differing


class Test不新造第六处:
    """C9.7：白名单恰好五处 + 反向扫描（扩展口径含函数体内与装饰器实参）。"""

    def test_白名单恰好五处且form_set面四处(self):
        assert len(REGISTRATION_SITES) == 5
        assert len(FORM_SET_FACE) == 4
        assert {site.kind for site in REGISTRATION_SITES} == {"form_set", "clause_list"}
        assert {site.path for site in REGISTRATION_SITES} == {
            "tests/unit/test_form_switch.py",
            "tests/unit/test_config_integrity.py",
            "tests/contract/test_pilot_contracts.py",
            "agents/pilot/pilot.py",
            "tests/conftest.py",
        }

    def test_白名单之外的反向扫描集合为空(self):
        """白名单之内由委派证明守住；白名单之外**必须为空**（新增一处即红，须显式登记）。"""
        leftover = sixth_site_scan()
        assert leftover == (), [site.describe() for site in leftover]

    def test_反向扫描口径覆盖函数体内与装饰器实参(self, tmp_path):
        """有牙齿自检：只看模块级常量会**空跑假绿**，故口径必须覆盖两处。"""
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "tmp_body.py").write_text(
            'def iteration():\n    for form in ("movie", "shortdrama"):\n        pass\n',
            encoding="utf-8",
        )
        (tmp_path / "tests" / "tmp_deco.py").write_text(
            "import pytest\n\n\n"
            '@pytest.mark.parametrize("form", ("movie", "shortdrama"))\n'
            "def test_thing(form):\n    assert form\n",
            encoding="utf-8",
        )
        found = form_enum_sites(configs_dir=CONFIGS_DIR, roots=(tmp_path / "tests",))
        kinds = {(site.path.rsplit("/", 1)[-1], site.kind) for site in found}
        assert ("tmp_body.py", "tuple") in kinds, found
        assert ("tmp_deco.py", "decorator_arg") in kinds, found
        for site in found:
            assert isinstance(site, EnumSite)
            assert site.names
            assert site.symbol


class Test有牙齿自检:
    """故意越界取证：注入即红（"今天绿"不算证据）。"""

    def test_两份配置form取值相同则两两唯一条件必红(self, tmp_path):
        configs = tmp_path / "configs"
        _write_config(configs, "movie", "movie")
        _write_config(configs, "shortdrama", "movie")  # 同名形态两份 ⇒ 派生即报错
        with pytest.raises(FormConfigError):
            report = registration_completeness(configs_dir=configs)
            assert report.unique  # 到不了这里：派生面在重复取值上即报错
        # 别名跨文件重复同样必红（名称面两两唯一）
        configs2 = tmp_path / "configs2"
        _write_config(configs2, "movie", "movie", aliases=("影",))
        _write_config(configs2, "shortdrama", "shortdrama", aliases=("影",))
        with pytest.raises(FormConfigError, match="重复"):
            declared_forms(configs2)

    def test_空目录或单份配置则下界条件必红(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FormConfigError):
            registration_completeness(configs_dir=empty)  # 零份配置 ⇒ 派生面报错
        single = tmp_path / "single"
        _write_config(single, "movie", "movie")
        report = registration_completeness(configs_dir=single)
        assert report.config_count == 1
        assert report.at_least_two is False
        assert any("下界" in issue for issue in report.violations())

    def test_人工枚举即双向集合条件必红(self, tmp_path):
        """**临时第三形态配置 + 一处人工枚举**的组合 ⇒ 逐处点名该形态。"""
        configs = tmp_path / "configs"
        for stem in _TEMP_THREE_FORMS:
            _write_config(configs, stem, stem)
        literal = _synthetic_site(tmp_path, 'FORMS = ("movie", "shortdrama")\n')
        delegated = _synthetic_site(
            tmp_path,
            "from ops.form_guard import declared_forms\n\n"
            'CONFIGS_DIR = "configs"\n'
            "FORMS = declared_forms(CONFIGS_DIR)\n",
            name="tmp_delegated_site.py",
        )
        literal_status = site_registration_status(literal, configs_dir=configs, repo_root=tmp_path)
        delegated_status = site_registration_status(
            delegated, configs_dir=configs, repo_root=tmp_path
        )
        assert literal_status.missing == (_TEMP_FORM,)
        assert literal_status.delegated is False
        assert delegated_status.registered == declared_forms(configs)
        assert delegated_status.delegated is True
        assert delegated_status.ok

    def test_形态集合表达式不可机检即报错(self, tmp_path):
        runtime_built = _synthetic_site(
            tmp_path, "FORMS = tuple(name for name in _some_runtime_table)\n"
        )
        status = site_registration_status(
            runtime_built, configs_dir=CONFIGS_DIR, repo_root=tmp_path
        )
        assert status.registered == ()
        assert not status.ok
        assert "不可机检" in status.describe()

    def test_名称面不得进登记比较(self, tmp_path):
        """把形态集合表达式写到名称面（含中文别名）⇒ 结构性报错，不得静默通过。"""
        site = _synthetic_site(
            tmp_path,
            "from ops.form_guard import form_literals\n\n"
            'CONFIGS_DIR = "configs"\n'
            "FORMS = form_literals(CONFIGS_DIR)\n",
        )
        status = site_registration_status(site, configs_dir=CONFIGS_DIR, repo_root=tmp_path)
        assert status.registered == ()
        assert "名称面" in status.describe()

    def test_别名不进登记面(self):
        """登记面 = id 面：名称面（含中文别名）与派生面不等 ⇒ 别名不得参与集合比较。"""
        literals = set(form_literals(CONFIGS_DIR))
        declared = set(declared_forms(CONFIGS_DIR))
        assert declared <= literals
        assert declared == set(registration_completeness().declared)
        # 名称面确实是**超集**（含别名）⇒ 若用它做登记比较会恒假（C10 反例 7）
        assert literals > declared or literals == declared

    def test_三形态布局下判定同样成立(self, tmp_path):
        """登记面判定不是"两形态特判"：形态名由派生面给出，任意数量都走同一判定。"""
        configs = tmp_path / "configs"
        for stem in _TEMP_THREE_FORMS_ALT:
            _write_config(configs, stem, stem)
        site = _synthetic_site(
            tmp_path,
            "from ops.form_guard import declared_forms\n\n"
            'CONFIGS_DIR = "configs"\n'
            "FORMS = declared_forms(CONFIGS_DIR)\n",
        )
        status = site_registration_status(site, configs_dir=configs, repo_root=tmp_path)
        assert status.registered == declared_forms(configs)
        assert status.ok
        report = registration_completeness(configs_dir=configs)
        assert report.unique and report.at_least_two and report.registered_matches
        # 五处登记点随**给定的**派生面走（真派生）；而人工枚举的那一处在同一布局下必红
        literal = _synthetic_site(
            tmp_path, 'FORMS = ("movie", "shortdrama")\n', name="tmp_literal_three.py"
        )
        literal_status = site_registration_status(literal, configs_dir=configs, repo_root=tmp_path)
        assert literal_status.missing == (_TEMP_FORM_ALT,)
        assert literal_status.foreign == ()
        assert literal_status.ok is False
