"""插件契约测试（021 A1 / T2110，先于实现编写）：C1~C4 的可执行面。

七条常驻机检：① 配置声明集 = 可用插件全集（六 Agent × 两形态逐字等于 `evaluator_weights`
键集）；② `assemble` 是 `impl` 的**唯一解析点**（全仓 `importlib.import_module` 的插件解析
命中点集合 == {`core/evaluators/plugin.py`}）；③ 纯关键字调用（AST：装配点对 `impl` 的调用
零位置实参）；④ 同 id 同 version 在同一注册中心重复注册被拒、非确定性插件注册被拒；
⑤ 必需元数据缺失即报错；⑥ `version` 不被声明覆盖（三方相等 + `EvaluatorSpec` 冻结未被反射
改写）；⑦ 插件业务无关（文本 + AST 双层）。
"""

import ast
import dataclasses
import importlib
from pathlib import Path

import pytest
import yaml

from agents.sound.config import SoundConfig
from core.evaluators.base import EvaluatorKind, EvaluatorSpec
from core.evaluators.errors import RegistrationError, ValidationError
from core.evaluators.plugin import assemble, parse_manifest
from core.evaluators.registry import Registry
from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
# 形态 id 面（021 T2146）：由 `configs/*.yaml` 的 `form:` 派生 ⇒ 新增形态自动进入遍历面
FORMS = declared_forms(REPO_ROOT / "configs")
PLUGIN_AGENTS = ("screenplay", "storyboard", "visual", "sound", "editing", "dev")
PLUGIN_FILES = tuple(f"agents/{agent}/evaluators/plugins.py" for agent in PLUGIN_AGENTS)
# 插件解析面：唯一装配点（其余 `importlib.import_module` 调用点是把 CLI 模块取回来转发，
# 不接触声明面——由 `test_第二解析路径出现即红` 的 `impl` 面断言另守一遍）
RESOLUTION_POINTS = {
    "core/evaluators/plugin.py",
    "ops/demo_billing.py",
    "ops/demo_shortdrama_feedback.py",
}


def _document(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _declared_ids(form: str, agent: str) -> set[str]:
    return {
        evaluator_id
        for entries in _document(form)["evaluators"]["plugins"][agent].values()
        for evaluator_id in entries
    }


class Test声明集等于可用插件全集:
    @pytest.mark.parametrize("form", FORMS)
    @pytest.mark.parametrize("agent", PLUGIN_AGENTS)
    def test_声明集逐字等于权重键集(self, form, agent):
        weights = _document(form)["evaluator_weights"][agent]
        assert _declared_ids(form, agent) == set(weights), f"{form}/{agent}"

    def test_两形态声明面结构逐字相同(self):
        """槽位 / `evaluator_id` / `impl` / `params` 逐字相同；`version` 因形态取值进版本哈希
        而逐形态声明（下一条断言：各形态声明值 == 该形态实现产出值 ⇒ 不存在"谎报版本"）。"""
        movie = _document("movie")["evaluators"]["plugins"]
        short = _document("shortdrama")["evaluators"]["plugins"]
        assert set(movie) == set(short)
        for agent in PLUGIN_AGENTS:
            assert set(movie[agent]) == set(short[agent]), f"{agent} 槽位集合"
            for slot in movie[agent]:
                assert set(movie[agent][slot]) == set(short[agent][slot]), f"{agent}/{slot}"
                for evaluator_id in movie[agent][slot]:
                    assert (
                        movie[agent][slot][evaluator_id]["impl"]
                        == short[agent][slot][evaluator_id]["impl"]
                    )
                    assert (
                        movie[agent][slot][evaluator_id]["params"]
                        == short[agent][slot][evaluator_id]["params"]
                    )

    @pytest.mark.parametrize("form", FORMS)
    @pytest.mark.parametrize("agent", PLUGIN_AGENTS)
    def test_声明version等于实现产出(self, form, agent, tmp_path):
        """三方相等（C4）：声明值 == 实现产出值 == 装配后实例的 `spec.version`。"""
        from core.tree.artifacts import LocalArtifactStore

        plugin_module = importlib.import_module(f"agents.{agent}.evaluators.plugins")
        config = _agent_config(form, agent)
        manifest = parse_manifest(
            config.plugin_declarations, plugin_module.AGENT, slots=plugin_module.SLOT_LAYOUT
        )
        registry = Registry()
        assemble(
            manifest,
            agent_config=config,
            gateway=_StubGateway(),
            artifacts=LocalArtifactStore(tmp_path / "artifacts"),
            registry=registry,
        )
        declared = {
            declaration.evaluator_id: declaration.version for declaration in manifest.declarations
        }
        registered = registry.list_all()
        assert len(registered) == len(declared), f"{form}/{agent} 注册数 != 声明数"
        for spec in registered:
            assert declared[spec.evaluator_id] == spec.version, (
                f"{form}/{agent}/{spec.evaluator_id}"
            )

    def test_声明version不得覆盖实现(self):
        """声明面只**校验**实现身份版本，不反射改写实现：装配点零 `dataclasses.replace` /
        零 `object.__setattr__` / 零 `spec.version =` 赋值（版本由 `EvaluatorSpec` 重新构造后
        按**实现身份口径**钉住）；`EvaluatorSpec` 冻结未被反射改写。"""
        source = (REPO_ROOT / "core" / "evaluators" / "plugin.py").read_text(encoding="utf-8")
        assert "dataclasses.replace" not in source and "replace(spec" not in source
        assert "spec.version =" not in source and "spec.key =" not in source
        assert "object.__setattr__" not in source
        spec = EvaluatorSpec(
            evaluator_id="rule.x", version="1.0.0+abc", kind=EvaluatorKind.RULE, deterministic=True
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.version = "1.0.0+other"


class Test唯一解析点与纯关键字:
    # 生产侧扫描面（`tests/**` 不构成生产解析路径；测试侧的版本钉住助手另有其文档）
    PRODUCTION_ROOTS = ("core", "agents", "ops", "web", "dreaming")

    def _import_module_sites(self) -> set[str]:
        sites = set()
        for root in self.PRODUCTION_ROOTS:
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "import_module"
                    ):
                        sites.add(path.relative_to(REPO_ROOT).as_posix())
        return sites

    def test_assemble是impl的唯一解析点(self):
        assert self._import_module_sites() == RESOLUTION_POINTS
        # 非装配点的解析调用点不得接触声明面（不读 `impl`）
        for relative in sorted(RESOLUTION_POINTS - {"core/evaluators/plugin.py"}):
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert "impl" not in source, f"{relative} 出现声明面词汇"

    def test_第二解析路径出现即红(self):
        """除唯一装配点外，没有任何模块解析 `module:attr` 声明。"""
        offenders = []
        for root in self.PRODUCTION_ROOTS:
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative == "core/evaluators/plugin.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                        if node.slice.value == "impl":
                            offenders.append(f"{relative}:{node.lineno}")
        assert offenders == []

    def test_装配点对impl的调用零位置实参(self):
        tree = ast.parse((REPO_ROOT / "core" / "evaluators" / "plugin.py").read_text("utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "target"
        ]
        assert calls, "装配点的 `impl` 调用点必须存在（否则本断言空跑）"
        for call in calls:
            assert call.args == [], f"第 {call.lineno} 行出现位置实参（必须纯关键字调用）"


class Test注册与元数据:
    def test_同id同version重复注册被拒(self, tmp_path):
        from core.tree.artifacts import LocalArtifactStore

        config = SoundConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        plugin_module = importlib.import_module("agents.sound.evaluators.plugins")
        manifest = parse_manifest(
            config.plugin_declarations, plugin_module.AGENT, slots=plugin_module.SLOT_LAYOUT
        )
        registry = Registry()
        assemble(
            manifest,
            agent_config=config,
            artifacts=LocalArtifactStore(tmp_path / "artifacts"),
            registry=registry,
        )
        assert len(registry.list_all()) == len(config.evaluator_weights)
        with pytest.raises(RegistrationError, match="重复注册"):
            assemble(
                manifest,
                agent_config=config,
                artifacts=LocalArtifactStore(tmp_path / "artifacts"),
                registry=registry,
            )

    def test_非确定性插件注册被拒(self):
        from core.evaluators.base import ArtifactRef, EvalResult, Evaluator

        class _NonDeterministic(Evaluator):
            def __init__(self) -> None:
                self.spec = EvaluatorSpec(
                    evaluator_id="rule.nondeterministic",
                    version="1.0.0+x",
                    kind=EvaluatorKind.RULE,
                    deterministic=False,
                )

            def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
                return EvalResult(score=1.0)

        with pytest.raises(RegistrationError, match="非确定性"):
            Registry().register(_NonDeterministic())

    def test_必需元数据缺失即报错(self):
        base = {"kind": EvaluatorKind.RULE, "deterministic": True}
        for overrides in (
            {"evaluator_id": "", "version": "1.0.0"},
            {"evaluator_id": "rule.x", "version": ""},
            {"evaluator_id": "rule.x", "version": "1.0.0", "cost_per_call": -1},
            {"evaluator_id": "rule.x", "version": "1.0.0", "kind": "unknown-kind"},
        ):
            with pytest.raises(ValidationError):
                EvaluatorSpec(**{**base, **overrides})


class Test插件业务无关:
    """C3 的四条硬性属性：文本层 + AST 层双层（AST 层复用 017 先例的 import 扫描法）。"""

    FORM_LITERALS = ("shortdrama", '"movie"', "'movie'")
    FORM_PATTERNS = ("form ==", "form==", "form !=", "form!=", "form is ", "form in ")
    FORBIDDEN_IMPORTS = (
        "socket",
        "urllib",
        "requests",
        "httpx",
        "openai",
        "anthropic",
        "deepseek",
    )

    def _files(self) -> list[Path]:
        core_plugins = REPO_ROOT / "core" / "evaluators" / "plugins"
        files = [REPO_ROOT / relative for relative in PLUGIN_FILES]
        if core_plugins.is_dir():
            files.extend(sorted(core_plugins.rglob("*.py")))
        assert len(files) >= len(PLUGIN_FILES)
        return files

    def test_文本层零形态字面量零取数零网络(self):
        for path in self._files():
            source = path.read_text(encoding="utf-8")
            for banned in self.FORM_LITERALS:
                assert banned not in source, f"{path} 出现形态字面量：{banned}"
            for pattern in self.FORM_PATTERNS:
                assert pattern not in source, f"{path} 出现形态判断：{pattern}"
            assert "os.environ" not in source and "os.getenv" not in source
            assert "open(" not in source
            assert "http://" not in source and "https://" not in source

    def test_ast层零越界import(self):
        offenders: list[str] = []
        for path in self._files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    roots = {(node.module or "").split(".")[0]}
                else:
                    continue
                for root in sorted(roots & set(self.FORBIDDEN_IMPORTS)):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} import {root}")
        assert offenders == []

    def test_装配点零业务词汇且不import_agents(self):
        source = (REPO_ROOT / "core" / "evaluators" / "plugin.py").read_text(encoding="utf-8")
        for agent in PLUGIN_AGENTS:
            assert agent not in source, f"core 唯一装配点出现 Agent 名：{agent}"
        for banned in self.FORM_LITERALS + ("animated",):
            assert banned not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = (
                    node.module
                    if isinstance(node, ast.ImportFrom)
                    else " ".join(alias.name for alias in node.names)
                )
                assert not (module or "").startswith(("agents", "dreaming", "ops", "web")), module

    def test_插件文件零孤立(self):
        """模块级公开函数必须至少被一份 `configs/*.yaml` 的 `impl` 引用（目录不决定可用性）。"""
        referenced = {
            leaf["impl"].split(":", 1)[1]
            for form in FORMS
            for entries in _document(form)["evaluators"]["plugins"].values()
            for slot in entries.values()
            for leaf in slot.values()
        }
        orphans = []
        for path in self._files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    if node.name not in referenced:
                        orphans.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
        assert orphans == []


class _StubGateway:
    total_cost_usd = 0.0


def _agent_config(form: str, agent: str):
    module_name, class_name = {
        "screenplay": ("agents.screenplay.config", "ScreenplayConfig"),
        "storyboard": ("agents.storyboard.config", "StoryboardConfig"),
        "visual": ("agents.visual.config", "VisualConfig"),
        "sound": ("agents.sound.config", "SoundConfig"),
        "editing": ("agents.editing.config", "EditingConfig"),
        "dev": ("agents.dev.config", "DevConfig"),
    }[agent]
    config_class = getattr(importlib.import_module(module_name), class_name)
    return config_class.from_dict(_document(form))


class Test同步版本口径:
    """T2160：`sync-versions` 的校验口径与"不允许覆盖实现"机检（门禁只跑 `--check`）。"""

    def _tampered_config(self, tmp_path, *, suffix: str) -> Path:
        """把 movie.yaml 的**第一条** `version` 改掉（其余字节逐字保留）→ 临时配置。"""
        text = (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8")
        first = next(
            leaf["version"]
            for entries in _document("movie")["evaluators"]["plugins"].values()
            for slot in entries.values()
            for leaf in slot.values()
        )
        tampered = f"{first}{suffix}"
        assert text.count(first) == 1, "版本声明不唯一（配置口径变了即红）"
        target = tmp_path / "movie.yaml"
        target.write_text(text.replace(first, tampered), encoding="utf-8")
        return target

    def _run(self, *args: str) -> tuple[int, dict]:
        import contextlib
        import io
        import json as _json

        from ops import form_plugin

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = form_plugin.main(list(args))
        return code, _json.loads(stream.getvalue())

    def test_check报差集不回写且退出非零(self, tmp_path):
        target = self._tampered_config(tmp_path, suffix="-x")
        before = target.read_bytes()
        code, payload = self._run("sync-versions", "--check", "--config", str(target))
        assert code == 1
        assert payload["mode"] == "check" and payload["diffs"]
        assert target.read_bytes() == before, "`--check` 不得回写配置（门禁禁止改写权威配置换绿灯）"
        diff = payload["diffs"][0]
        assert diff["declared"] != diff["actual"]
        assert diff["section_path"][:2] == ["evaluators", "plugins"]

    def test_write只改version叶子键且check随即归零(self, tmp_path):
        target = self._tampered_config(tmp_path, suffix="-x")
        before = target.read_text(encoding="utf-8").splitlines(keepends=True)
        code, payload = self._run("sync-versions", "--write", "--config", str(target))
        assert code in (0, 1) and payload["diffs"]
        after = target.read_text(encoding="utf-8").splitlines(keepends=True)
        assert len(before) == len(after)
        changed = [
            index for index, (old, new) in enumerate(zip(before, after, strict=True)) if old != new
        ]
        assert len(changed) == len(payload["diffs"]), f"被改动的行数 {changed} 与差集数不等"
        for index in changed:
            assert "version" in before[index] and "version" in after[index]
        code_again, payload_again = self._run("sync-versions", "--check", "--config", str(target))
        assert code_again == 0 and payload_again["diffs"] == []

    def test_不回写声明值进实现且不归一化版本(self, tmp_path):
        """三条禁止：不改 `spec`、不反射改写、不"归一化"版本字符串。"""
        source = (REPO_ROOT / "ops" / "form_plugin.py").read_text(encoding="utf-8")
        for forbidden in (
            "dataclasses.replace",
            "object.__setattr__",
            "setattr(",
            "spec.version =",
            "spec.key =",
        ):
            assert forbidden not in source, f"sync 面出现 {forbidden!r}（不得覆盖实现）"
        # 只留 `+` 前的基础版本 ⇒ 装配期必须**拒绝**（不得把它归一化到实现身份版本）
        text = (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8")
        first = next(
            leaf["version"]
            for entries in _document("movie")["evaluators"]["plugins"].values()
            for slot in entries.values()
            for leaf in slot.values()
        )
        base_only = first.split("+")[0]
        target = tmp_path / "base.yaml"
        target.write_text(text.replace(first, base_only), encoding="utf-8")
        code, payload = self._run("sync-versions", "--check", "--config", str(target))
        assert code == 1 and payload["diffs"]
        diff = payload["diffs"][0]
        assert diff["declared"] == base_only and diff["actual"] != base_only

    def test_判据是装配期一致性校验(self, tmp_path):
        """`--check` 的差集来自**装配期三方一致性校验**（不是 sync 自身的产物）。"""
        target = self._tmp_ok(tmp_path)
        code, payload = self._run("sync-versions", "--check", "--config", str(target))
        assert code == 0 and payload["diffs"] == []
        spec_source = (REPO_ROOT / "core" / "evaluators" / "base.py").read_text(encoding="utf-8")
        assert "frozen=True" in spec_source
        spec = EvaluatorSpec(
            evaluator_id="rule.x", version="1.0.0+abc", kind=EvaluatorKind.RULE, deterministic=True
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.version = "1.0.0+other"
        assert spec.key == "rule.x@1.0.0+abc"

    def _tmp_ok(self, tmp_path) -> Path:
        target = tmp_path / "movie-ok.yaml"
        target.write_text(
            (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        return target

    def test_check是默认模式(self, tmp_path):
        target = self._tampered_config(tmp_path, suffix="-x")
        code, payload = self._run("sync-versions", "--config", str(target))
        assert code == 1 and payload["mode"] == "check"

    def test_只读纪律_零git命令(self):
        source = (REPO_ROOT / "ops" / "form_plugin.py").read_text(encoding="utf-8")
        for token in ("git add", "git commit", "git reset", "git checkout"):
            assert token not in source
