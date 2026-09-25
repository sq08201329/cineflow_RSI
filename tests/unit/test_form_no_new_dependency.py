"""零新增运行时依赖常驻用例（功能 021 A5 / T2197，FR-012 的机检承载）。

三条常驻断言：

1. **依赖集合 == 基线快照**（`tests/unit/fixtures/dependency_baseline.json`，由**机制落地前**
   导出）：`pyproject.toml` 的 `dependencies` / `optional-dependencies` 与 `uv.lock` 的包名集
   任一**新增 / 删除 / 重命名** ⇒ 红，并**逐条点名**差集（不得只报总数）；
2. **本特性新增模块的 import 面 ⊆ stdlib ∪ 既有依赖 ∪ 仓库内包**——逐个 AST 求 import 名，
   容器内**不得**出现第三方插件框架（`entry_points` / `stevedore` / `pluggy` 一类）；
3. `pyproject.toml` 内 `entry_points` **零命中**（不引入"安装元数据驱动的可用性"）。
"""

import ast
import json
import re
import sys
import tomllib
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
BASELINE_PATH = REPO_ROOT / "tests" / "unit" / "fixtures" / "dependency_baseline.json"

# 既有依赖的**分发包名 → import 名**（少数包名与模块名不同形；其余同名）
_DISTRIBUTION_TO_IMPORT = {
    "pyyaml": "yaml",
    "uuid-utils": "uuid_utils",
    "imageio-ffmpeg": "imageio_ffmpeg",
}
# 本特性新增/改动的模块（机制侧与接入侧的插件面；逐个断言 import 面）
FEATURE_MODULES: tuple[str, ...] = (
    "core/evaluators/plugin.py",
    "agents/visual/evaluators/plugins.py",
    "agents/dev/evaluators/plugins.py",
    "agents/screenplay/evaluators/plugins.py",
    "agents/storyboard/evaluators/plugins.py",
    "agents/sound/evaluators/plugins.py",
    "agents/editing/evaluators/plugins.py",
    "ops/form_guard.py",
    "ops/form_onboarding.py",
    "ops/form_plugin.py",
    "ops/demo_form_plugin.py",
)
# 第三方插件框架（出现即红：本特性的可用性只由配置声明决定）
FORBIDDEN_PLUGIN_FRAMEWORKS: tuple[str, ...] = (
    "stevedore",
    "pluggy",
    "importlib_metadata",
    "pkg_resources",
    "setuptools",
)


def _normalize(name: str) -> str:
    """分发包名规范化（去 extras / 版本区间；下划线等价连字符）。"""
    base = re.split(r"[<>=!~;\s]", name.split("[")[0].strip())[0]
    return base.lower().replace("_", "-")


def _current_dependencies() -> dict:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    optional = {
        extra: sorted({_normalize(item) for item in items})
        for extra, items in (project.get("optional-dependencies") or {}).items()
    }
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return {
        "pyproject_dependencies": sorted(
            {_normalize(item) for item in project.get("dependencies", [])}
        ),
        "pyproject_optional_dependencies": optional,
        "uv_lock_packages": sorted(
            {_normalize(package["name"]) for package in lock.get("package", [])}
        ),
    }


def _read_baseline() -> dict:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for key in ("pyproject_dependencies", "pyproject_optional_dependencies", "uv_lock_packages"):
        assert key in payload, f"基线快照缺字段 {key!r}（缺基线就无从对照）"
    return payload


def _imported_names(path: Path) -> set[str]:
    """AST 求该模块的顶层 import 名（`import a.b` ⇒ `a`；`from a.b import c` ⇒ `a`）。"""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _stdlib_names() -> frozenset[str]:
    return frozenset(sys.stdlib_module_names) | {"__future__"}


def _repo_packages() -> frozenset[str]:
    """仓库内顶层包/目录（`core` / `agents` / `ops` / `tests` / `web` / `dreaming` …）。"""
    return frozenset(
        path.name
        for path in REPO_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    ) | {"tests", "core", "agents", "ops", "web", "dreaming", "policies", "billing"}


class Test依赖集合与基线快照相等:
    def test_三处依赖集合逐条相等(self):
        baseline = _read_baseline()
        current = _current_dependencies()
        added: list[str] = []
        removed: list[str] = []
        for key in ("pyproject_dependencies", "uv_lock_packages"):
            added += [f"{key}:+{item}" for item in set(current[key]) - set(baseline[key])]
            removed += [f"{key}:-{item}" for item in set(baseline[key]) - set(current[key])]
        assert set(current["pyproject_optional_dependencies"]) == set(
            baseline["pyproject_optional_dependencies"]
        ), "optional-dependencies 的 extra 集合发生变化"
        for extra in baseline["pyproject_optional_dependencies"]:
            now = set(current["pyproject_optional_dependencies"][extra])
            was = set(baseline["pyproject_optional_dependencies"][extra])
            added += [f"{extra}:+{item}" for item in now - was]
            removed += [f"{extra}:-{item}" for item in was - now]
        assert added == [] and removed == [], (
            f"依赖集合与基线快照不一致（新增 {added}；删除 {removed}）"
            "——零新增运行时依赖是 FR-012 的红线，任何依赖变化都必须显式登记"
        )

    def test_基线快照非空(self):
        baseline = _read_baseline()
        assert baseline["pyproject_dependencies"], "基线依赖集为空（本断言会空跑）"
        assert baseline["uv_lock_packages"], "基线包名集为空（本断言会空跑）"

    def test_基线条目已规范化(self):
        for name in _read_baseline()["pyproject_dependencies"]:
            assert name == _normalize(name), f"基线条目未规范化：{name!r}"
            assert unicodedata.is_normalized("NFKC", name), f"基线条目未做 Unicode 规范化：{name!r}"


class Test新增模块的import面:
    def test_全部模块可解析且不含第三方插件框架(self):
        allowed = _stdlib_names() | _repo_packages()
        allowed |= {
            _DISTRIBUTION_TO_IMPORT.get(name, name)
            for name in _read_baseline()["pyproject_dependencies"]
        }
        offenders: list[str] = []
        for relative in FEATURE_MODULES:
            path = REPO_ROOT / relative
            assert path.is_file(), f"本特性新增模块不存在：{relative}"
            for name in _imported_names(path):
                if name in FORBIDDEN_PLUGIN_FRAMEWORKS:
                    offenders.append(f"{relative}: {name}（第三方插件框架）")
                elif name not in allowed:
                    offenders.append(f"{relative}: {name}（既非 stdlib、也非既有依赖）")
        assert offenders == [], f"import 面越界：{offenders}"

    def test_无entry_points驱动的可用性(self):
        text = PYPROJECT.read_text(encoding="utf-8")
        assert text.count("entry_points") == 0, (
            "pyproject.toml 出现 entry_points：插件可用性必须由配置声明决定，"
            "不得走安装元数据驱动的发现"
        )

    def test_插件目录与薄工厂都在断言面内(self):
        """接入侧的 `core/evaluators/plugins/**`（B 阶段新增面）若已存在也纳入同一断言。"""
        plugins_dir = REPO_ROOT / "core" / "evaluators" / "plugins"
        present = sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in plugins_dir.rglob("*.py")
            if "__pycache__" not in path.parts
        )
        allowed = _stdlib_names() | _repo_packages()
        allowed |= {
            _DISTRIBUTION_TO_IMPORT.get(name, name)
            for name in _read_baseline()["pyproject_dependencies"]
        }
        offenders = [
            f"{relative}: {name}"
            for relative in present
            for name in _imported_names(REPO_ROOT / relative)
            if name not in allowed
        ]
        assert offenders == [], f"cores/evaluators/plugins 的 import 面越界：{offenders}"
