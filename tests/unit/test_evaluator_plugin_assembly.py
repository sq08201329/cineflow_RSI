"""插件声明面与唯一装配点单测（021 A1 / T2109 + T2111 + T2121 + T2122，先于实现编写）。

覆盖 `specs/021-form-plugin-validation/contracts/plugin-config.md` 的 C1/C2/C3 机检断言：

1. **改前装配序列对照（T2111，先于 T2120 落地）**：
   `tests/unit/fixtures/evaluator_assembly_baseline.json` 是**改造前**由硬编码装配导出、后由委派
   实现逐字复现的基线（六个 Agent × 两形态，含槽位与顺序、含既有实现文件的字节哈希）——
   它就是"既有两形态装配逐字节不变"的取证；
2. **声明面形状**（C1）：叶子键恰三键、`impl` 的 `module:attr`、`params` 映射、槽位取值域、
   缺段即报错（零兜底）；
3. **装配期规则**（C2）：`impl` 解析与 callable、参数严格性双向、三项一致性、注入槽位 opt-in、
   集合与权重键集一一对应、保序、注册口径；
4. **既有评估器实现文件零改动 / 既有参数不搬迁**（T2121/T2122）。
"""

import ast
import copy
import dataclasses
import inspect
import json
import re
from pathlib import Path

import blake3
import pytest
import yaml

from core.evaluators.errors import (
    PluginAssemblyError,
    PluginDeclarationError,
    RegistrationError,
)
from core.evaluators.plugin import INJECTION_SLOTS, assemble, parse_manifest
from ops.form_guard import declared_forms
from tests.plugin_stubs import stub_version

REPO_ROOT = Path(__file__).resolve().parents[2]
# 形态 id 面（021 T2146）：由 `configs/*.yaml` 的 `form:` 派生 ⇒ 新增形态自动进入遍历面
FORMS = declared_forms(REPO_ROOT / "configs")
BASELINE = json.loads(
    (REPO_ROOT / "tests" / "unit" / "fixtures" / "evaluator_assembly_baseline.json").read_text(
        encoding="utf-8"
    )
)

AGENTS = ("screenplay", "storyboard", "visual", "sound", "editing", "dev")


def _declared_pairs(form: str, agent: str) -> list[list[str]]:
    """声明面给出的 `[[slot, evaluator_id@version], ...]`（`configs/*.yaml` 声明顺序）。"""
    return [
        [slot, f"{evaluator_id}@{leaf['version']}"]
        for slot, entries in _declarations(form, agent).items()
        for evaluator_id, leaf in entries.items()
    ]


def _declared_all(form: str, agent: str) -> list[str]:
    """声明面给出的 `all` 序列（= 各槽位声明顺序拼接，未声明槽位不参与）。"""
    return [key for _, key in _declared_pairs(form, agent)]


SYMBOLS = {
    "screenplay": ("agents.screenplay.evaluators", "build_screenplay_evaluators"),
    "storyboard": ("agents.storyboard.evaluators", "build_storyboard_evaluators"),
    "visual": ("agents.visual.loop", "build_evaluators"),
    "sound": ("agents.sound.evaluators", "build_sound_evaluators"),
    "editing": ("agents.editing.evaluators", "build_editing_evaluators"),
    "dev": ("agents.dev.evaluators", "build_dev_evaluators"),
}
CONFIG_CLASSES = {
    "screenplay": ("agents.screenplay.config", "ScreenplayConfig"),
    "storyboard": ("agents.storyboard.config", "StoryboardConfig"),
    "visual": ("agents.visual.config", "VisualConfig"),
    "sound": ("agents.sound.config", "SoundConfig"),
    "editing": ("agents.editing.config", "EditingConfig"),
    "dev": ("agents.dev.config", "DevConfig"),
}


def _plugins_module(agent: str):
    import importlib

    return importlib.import_module(f"agents.{agent}.evaluators.plugins")


def _layout(agent: str) -> tuple[str, ...]:
    return _plugins_module(agent).SLOT_LAYOUT


def _document(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _agent_config(form: str, agent: str):
    import importlib

    module_name, class_name = CONFIG_CLASSES[agent]
    config_class = getattr(importlib.import_module(module_name), class_name)
    return config_class.from_dict(_document(form))


class _StubGateway:
    total_cost_usd = 0.0


def _build(agent: str, config, gateway=None, artifacts=None):
    import importlib

    module = importlib.import_module(SYMBOLS[agent][0])
    builder = getattr(module, SYMBOLS[agent][1])
    if agent == "visual":
        return builder(config, gateway, artifacts)
    if agent in ("screenplay", "storyboard", "editing"):
        return builder(config, gateway)
    return builder(config)


@pytest.fixture()
def build_env(tmp_path):
    from core.tree.artifacts import LocalArtifactStore

    return _StubGateway(), LocalArtifactStore(tmp_path / "artifacts")


def _slots_of(agent: str, value) -> list[list[str]]:
    if isinstance(value, list):
        return [["all", item.spec.key] for item in value]
    pairs = []
    for slot in _layout(agent):
        if slot not in value:  # 未声明的槽位不产出键（返回形状如实反映已声明集合）
            continue
        items = value[slot]
        items = items if isinstance(items, list) else [items]
        pairs.extend([slot, item.spec.key] for item in items)
    return pairs


class Test改前装配序列对照:
    """T2111：改造前后装配序列逐字相同（既有 `eval_breakdown` 与得分不受影响）。"""

    @pytest.mark.parametrize("form", FORMS)
    def test_六Agent装配序列与基线逐字相同(self, form, build_env):
        """基线覆盖的两个既有形态逐字等于**改前基线**；基线之后接入的形态（无"改前"可言）
        逐字等于**其自己的声明面**——两者都是真断言，无 xfail、无放宽。"""
        gateway, artifacts = build_env
        for agent in AGENTS:
            config = _agent_config(form, agent)
            value = _build(agent, config, gateway, artifacts)
            expected_slots = (
                BASELINE[form][agent]["slots"] if form in BASELINE else _declared_pairs(form, agent)
            )
            assert _slots_of(agent, value) == expected_slots, f"{form}/{agent}"
            all_keys = [
                item.spec.key for item in (value if isinstance(value, list) else value["all"])
            ]
            expected_all = (
                BASELINE[form][agent]["all"] if form in BASELINE else _declared_all(form, agent)
            )
            assert all_keys == expected_all, f"{form}/{agent}"

    @pytest.mark.parametrize("form", FORMS)
    def test_all键等于按槽位布局顺序拼接(self, form, build_env):
        """`all` = 按 `SLOT_LAYOUT` 顺序拼接**已装配槽位**；返回键集 == 声明面已声明槽位集
        （**未声明 ⇒ 不产出该键**，且 `all` 如实只含已声明集合）。"""
        gateway, artifacts = build_env
        for agent in AGENTS:
            if _layout(agent) == ("all",):
                continue
            value = _build(agent, _agent_config(form, agent), gateway, artifacts)
            declared = set(_declarations(form, agent))
            assert set(value) - {"all"} == declared, f"{form}/{agent} 返回键集 != 声明槽位集"
            expected = [item for slot in _layout(agent) for item in _as_list(value.get(slot, []))]
            assert value["all"] == expected, f"{form}/{agent}"

    def test_sound返回扁平列表且下标顺序等于声明顺序(self, build_env):
        gateway, artifacts = build_env
        for form in FORMS:
            value = _build("sound", _agent_config(form, "sound"), gateway, artifacts)
            assert isinstance(value, list)
            declared = [
                f"{evaluator_id}@{leaf['version']}"
                for entries in _declarations(form, "sound").values()
                for evaluator_id, leaf in entries.items()
            ]
            assert [item.spec.key for item in value] == declared

    def test_既有评估器实现文件字节未变(self):
        """T2121：范围 = **单评估器实现模块**（版本号把它们各自的字节并入哈希 ⇒ 改一字节即改
        `eval_breakdown`）。排除三类：`__init__.py`（装配入口）、`plugins.py`（新增薄工厂，
        属机制改动面）、`composite.py`（**编排件**：不产出 `spec`、字节不进任何版本哈希，且
        它正是"槽位未声明 ⇒ 跳过并如实标注"这一语义的消费点，021 最小可行形态收口所需）。"""

        recorded = BASELINE["implementation_files"]
        assert recorded, "基线必须记录实现文件哈希（否则本断言空跑）"
        changed = sorted(
            relative
            for relative, digest in recorded.items()
            if blake3.blake3((REPO_ROOT / relative).read_bytes()).hexdigest() != digest
        )
        assert changed == [], f"既有评估器实现文件被改动（违反 FR-013）：{changed}"


def _declarations(form: str, agent: str) -> dict:
    return _document(form)["evaluators"]["plugins"][agent]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _manifest(document, agent: str = "stubagent", *, slots=("gates",)):
    return parse_manifest({"evaluators": {"plugins": {agent: document}}}, agent, slots=slots)


def _stub_leaf(impl: str, *, version: str = "1.0.0+stub00000000", **params) -> dict:
    return {"impl": impl, "version": version, "params": params}


def _assemble_stub(
    impl: str,
    *,
    evaluator_id: str,
    slot: str = "gates",
    slots=None,
    version: str | None = None,
    params=None,
    weights=None,
    agent_config=None,
    **provided,
):
    """单条声明的装配：`evaluator_id` 与权重键按用例显式给出（合成反例的定位面）。

    声明的 `version` 缺省 = 存根模块的**实现身份版本**（与唯一装配点同口径，形态/参数无关）；
    需要构造"声明与实现不一致"的反例时显式传入别的值。
    """
    leaf = {
        "impl": impl,
        "version": version or stub_version(evaluator_id),
        "params": dict(params or {}),
    }
    layout = tuple(slots) if slots is not None else (slot,)
    document = {slot: {evaluator_id: leaf}}
    manifest = parse_manifest(document, "stubagent", slots=layout)
    return assemble(
        manifest,
        agent_config=agent_config
        or _stub_agent_config(weights if weights is not None else {evaluator_id: 1.0}),
        **provided,
    )


def _stub_agent_config(weights: dict):
    from tests.plugin_stubs import StubAgentConfig

    return StubAgentConfig(evaluator_weights=weights)


class Test声明面形状:
    """C1/C3：声明段的形状、叶子键与缺段即报错。"""

    def test_叶子键缺任一即报错(self):
        for missing in ("impl", "version", "params"):
            leaf = _stub_leaf("tests.plugin_stubs:declared_only")
            leaf.pop(missing)
            with pytest.raises(PluginDeclarationError, match=missing):
                _manifest({"gates": {"rule.x": leaf}}, "stubagent")

    def test_叶子键出现第四键即报错(self):
        leaf = _stub_leaf("tests.plugin_stubs:declared_only")
        leaf["extra"] = 1
        with pytest.raises(PluginDeclarationError, match="叶子键"):
            _manifest({"gates": {"rule.x": leaf}}, "stubagent")

    def test_impl冒号数不为1即报错(self):
        for bad in ("tests.plugin_stubs", "a:b:c", 3, ""):
            leaf = _stub_leaf("tests.plugin_stubs:declared_only")
            leaf["impl"] = bad
            with pytest.raises(PluginDeclarationError, match="impl"):
                _manifest({"gates": {"rule.x": leaf}}, "stubagent")

    def test_version非字符串或空串即报错(self):
        for bad in (1.0, "", None):
            leaf = _stub_leaf("tests.plugin_stubs:declared_only", version=bad)
            with pytest.raises(PluginDeclarationError, match="version"):
                _manifest({"gates": {"rule.x": leaf}}, "stubagent")

    def test_params非映射即报错(self):
        leaf = _stub_leaf("tests.plugin_stubs:declared_only")
        leaf["params"] = [1]
        with pytest.raises(PluginDeclarationError, match="params"):
            _manifest({"gates": {"rule.x": leaf}}, "stubagent")

    def test_表外槽位即报错(self):
        """对 dict 返回的 Agent 声明派生键 `all` ⇒ 槽位非法。"""
        leaf = _stub_leaf("tests.plugin_stubs:declared_only")
        with pytest.raises(PluginDeclarationError, match="槽位非法"):
            parse_manifest(
                {"all": {"rule.declared_only": leaf}}, "screenplay", slots=("gates", "proxies")
            )

    def test_缺evaluators段即报错_零兜底(self):
        for form in FORMS:
            for agent in AGENTS:
                document = _document(form)
                document.pop("evaluators")
                config = _config_from_document(agent, document)
                assert config.plugin_declarations is None
                with pytest.raises(PluginDeclarationError, match="evaluators.plugins"):
                    _build(agent, config, _StubGateway(), _stub_artifacts(tmp_path=None))

    def test_缺plugins或缺本Agent子键同样为None(self):
        for drop_plugins in (True, False):
            broken = copy.deepcopy(_document("movie"))
            if drop_plugins:
                broken["evaluators"].pop("plugins")
            else:
                broken["evaluators"]["plugins"].pop("screenplay")
            assert _config_from_document("screenplay", broken).plugin_declarations is None

    def test_签名逐字(self):
        parse_params = inspect.signature(parse_manifest).parameters
        assert tuple(parse_params) == ("document", "agent", "slots")
        assert parse_params["slots"].kind is inspect.Parameter.KEYWORD_ONLY
        assemble_params = inspect.signature(assemble).parameters
        assert tuple(assemble_params) == (
            "manifest",
            "agent_config",
            "gateway",
            "artifacts",
            "registry",
        )
        for name in ("agent_config", "gateway", "artifacts", "registry"):
            assert assemble_params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert assemble_params["agent_config"].default is inspect.Parameter.empty
        for name in ("gateway", "artifacts", "registry"):
            assert assemble_params[name].default is None
        assert INJECTION_SLOTS == ("agent_config", "gateway", "artifacts", "registry")


def _config_from_document(agent: str, document: dict):
    import importlib

    module_name, class_name = CONFIG_CLASSES[agent]
    config_class = getattr(importlib.import_module(module_name), class_name)
    return config_class.from_dict(document)


def _stub_artifacts(*, tmp_path):
    import tempfile

    from core.tree.artifacts import LocalArtifactStore

    return LocalArtifactStore(Path(tempfile.mkdtemp()) if tmp_path is None else tmp_path)


class Test装配期规则:
    """C2：解析、参数严格性、三项一致性、注入 opt-in、注册。"""

    def test_impl不可解析或非callable即报错(self):
        cases = (
            ("tests.plugin_stubs:no_such_attr", "不可解析", "rule.no_such_attr"),
            ("tests.no_such_module:declared_only", "不可解析", "rule.declared_only"),
            ("tests.plugin_stubs:NOT_CALLABLE", "可调用", "rule.declared_only"),
        )
        for impl, pattern, evaluator_id in cases:
            with pytest.raises(PluginAssemblyError, match=pattern):
                _assemble_stub(impl, evaluator_id=evaluator_id)

    def test_产出非评估器即报错(self):
        with pytest.raises(PluginAssemblyError, match="未产出评估器实例"):
            _assemble_stub("tests.plugin_stubs:not_an_evaluator", evaluator_id="rule.any")

    def test_evaluator_id不一致即报错(self):
        with pytest.raises(PluginAssemblyError, match="evaluator_id 不一致") as exc:
            _assemble_stub("tests.plugin_stubs:mismatched_id", evaluator_id="rule.mismatched_id")
        assert "rule.actually_other" in str(exc.value)

    def test_前缀与kind不一致即报错(self):
        with pytest.raises(PluginAssemblyError, match="kind 不一致"):
            _assemble_stub("tests.plugin_stubs:wrong_kind", evaluator_id="rule.wrong_kind")

    def test_version不一致即报错且给出两侧实测值(self):
        with pytest.raises(
            PluginAssemblyError, match="声明的 version 与实现的 version 不一致"
        ) as exc:
            _assemble_stub(
                "tests.plugin_stubs:wrong_version",
                evaluator_id="rule.wrong_version",
                version="1.0.0+stub00000000",  # 故意与实现身份不一致的声明值
            )
        message = str(exc.value)
        assert "1.0.0+stub00000000" in message  # 声明侧
        assert stub_version("rule.wrong_version") in message  # 实现身份侧
        assert "1.0.0+other1234567" in message  # 实现自报侧（口径可事后指认）

    def test_参数严格性双向反例(self):
        with pytest.raises(PluginAssemblyError, match="参数严格性不符") as exc:
            _assemble_stub(
                "tests.plugin_stubs:missing_declaration",
                evaluator_id="rule.missing_declaration",
            )
        assert "threshold" in str(exc.value)
        with pytest.raises(PluginAssemblyError, match="参数严格性不符") as exc:
            _assemble_stub(
                "tests.plugin_stubs:defaulted_parameter",
                evaluator_id="rule.defaulted_parameter",
                params={"threshold": 1},
            )
        assert "threshold" in str(exc.value)

    def test_可变参数即报错(self):
        with pytest.raises(PluginAssemblyError, match="可变参数"):
            _assemble_stub("tests.plugin_stubs:variadic", evaluator_id="rule.variadic")

    def test_注入槽位越界即报错(self):
        with pytest.raises(PluginAssemblyError, match="config_path") as exc:
            _assemble_stub(
                "tests.plugin_stubs:out_of_bounds_slot",
                evaluator_id="rule.out_of_bounds_slot",
            )
        assert "注入槽位" in str(exc.value)
        assert "gateway" in str(exc.value) and "artifacts" in str(exc.value)

    def test_params通道正例(self):
        assembled = _assemble_stub(
            "tests.plugin_stubs:takes_params",
            evaluator_id="rule.takes_params",
            params={"threshold": 1, "label": "x"},
        )
        assert [ev.spec.evaluator_id for ev in assembled["gates"]] == ["rule.takes_params"]

    def test_提供槽位取值为None即报错(self):
        with pytest.raises(PluginAssemblyError, match="未提供"):
            _assemble_stub(
                "tests.plugin_stubs:declares_gateway",
                evaluator_id="rule.declares_gateway",
                gateway=None,
            )

    def test_集合与权重键集两向不匹配均拒绝(self):
        for pattern, weights in (
            ("多余权重键", {"rule.mismatched_id": 1.0, "proxy.ghost": 0.5}),
            ("缺权重键", {"proxy.ghost_b": 0.5}),
        ):
            with pytest.raises(PluginAssemblyError, match=pattern):
                _assemble_stub(
                    "tests.plugin_stubs:mismatched_id",
                    evaluator_id="rule.mismatched_id",
                    weights=weights,
                )

    def test_保序_槽位内序列等于声明序列(self):
        from tests.plugin_stubs import stub_version

        document = {
            "gates": {
                "rule.declared_only": {
                    "impl": "tests.plugin_stubs:declared_only",
                    "version": stub_version("rule.declared_only"),
                    "params": {},
                },
            },
            "proxies": {
                "proxy.declared_only": {
                    "impl": "tests.plugin_stubs:declared_only_proxy",
                    "version": stub_version("proxy.declared_only"),
                    "params": {},
                },
            },
        }
        manifest = parse_manifest(document, "stubagent", slots=("gates", "proxies"))
        assembled = assemble(
            manifest,
            agent_config=_stub_agent_config(
                {"rule.declared_only": 1.0, "proxy.declared_only": 0.5}
            ),
        )
        assert [ev.spec.evaluator_id for ev in assembled["gates"]] == ["rule.declared_only"]
        assert [ev.spec.evaluator_id for ev in assembled["proxies"]] == ["proxy.declared_only"]
        assert [ev.spec.evaluator_id for ev in assembled["all"]] == [
            "rule.declared_only",
            "proxy.declared_only",
        ]

    def test_仅在提供registry时注册且重复键被拒(self):
        from core.evaluators.registry import Registry

        registry = Registry()
        _assemble_stub(
            "tests.plugin_stubs:declared_only",
            evaluator_id="rule.declared_only",
            registry=registry,
        )
        assert len(registry.list_all()) == 1
        with pytest.raises(RegistrationError):
            _assemble_stub(
                "tests.plugin_stubs:declared_only",
                evaluator_id="rule.declared_only",
                registry=registry,
            )

    def test_非确定性插件注册被拒(self):
        from core.evaluators.registry import Registry

        with pytest.raises(RegistrationError, match="非确定性"):
            _assemble_stub(
                "tests.plugin_stubs:nondeterministic",
                evaluator_id="rule.nondeterministic",
                registry=Registry(),
            )


class Test目录不决定可用性:
    """C3：声明才生效；缺声明即缺项 ⇒ 装配期报错。"""

    def test_目录里存在但未声明即不可用(self, build_env):
        gateway, artifacts = build_env
        document = _document("movie")
        dropped = copy.deepcopy(document)
        dropped["evaluators"]["plugins"]["editing"]["pacing"].pop("proxy.pacing_curve")
        config = _config_from_document("editing", dropped)
        # 实现文件仍在（孤立插件），但未声明 ⇒ 装配集合缺项 ⇒ 报错
        assert (REPO_ROOT / "agents" / "editing" / "evaluators" / "plugins.py").is_file()
        with pytest.raises(PluginAssemblyError, match="缺权重键"):
            _build("editing", config, gateway, artifacts)

    def test_实现里零缺段回落的兜底字样(self):
        pattern = re.compile(r"回落到硬编码|缺段.*兜底")
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in list((REPO_ROOT / "core" / "evaluators").rglob("*.py"))
            + list((REPO_ROOT / "agents").rglob("*/evaluators/*.py"))
            if "__pycache__" not in path.parts and pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []


class Test既有参数不搬迁:
    """T2122：单一事实源——`params` 全为 `{}`，既有取值仍经 `agent_config` 读取。"""

    @pytest.mark.parametrize("form", FORMS)
    def test_两形态声明面params全为空(self, form):
        for agent in AGENTS:
            for slot, entries in _declarations(form, agent).items():
                for evaluator_id, leaf in entries.items():
                    assert leaf["params"] == {}, f"{form}/{agent}/{slot}/{evaluator_id}"

    def test_params键名不得与既有Config字段同义(self):
        """双事实源检测：`params` 的键不得是既有 `<Agent>Config` 的任何字段名。"""
        offenders = []
        for form in FORMS:
            for agent in AGENTS:
                import importlib

                module_name, class_name = CONFIG_CLASSES[agent]
                fields = {
                    field.name
                    for field in dataclasses.fields(
                        getattr(importlib.import_module(module_name), class_name)
                    )
                }
                for slot, entries in _declarations(form, agent).items():
                    for evaluator_id, leaf in entries.items():
                        clash = sorted(set(leaf["params"]) & fields)
                        if clash:
                            offenders.append(f"{form}/{agent}/{slot}/{evaluator_id}: {clash}")
        assert offenders == []

    @pytest.mark.parametrize("form", FORMS)
    def test_薄工厂从agent_config槽位读既有取值(self, form):
        """声明面点名的每个薄工厂：纯关键字签名、经 `agent_config` 读既有参数、不自行取数。

        **点名面 = 该 Agent 的绑定薄工厂**（`impl` 指向本 Agent 的 `plugins` 模块）：业务无关的
        通用件落 `core/evaluators/plugins/`（C3 裁决 2），其同名机检在
        `tests/contract/test_plugin_contracts.py`（业务无关四条 + "插件文件零孤立"）里承担，
        故此处不把它误当作本 Agent 的薄工厂。**逐形态逐 Agent 的"本模块薄工厂必须被点名"
        断言保留**（不因通用件而放宽）。
        """
        for agent in AGENTS:
            source = (REPO_ROOT / "agents" / agent / "evaluators" / "plugins.py").read_text(
                encoding="utf-8"
            )
            assert "os.environ" not in source and "os.getenv" not in source
            assert "open(" not in source and "Path(" not in source
            by_name = {
                node.name: node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)
            }
            own_module = f"agents.{agent}.evaluators.plugins"
            referenced = {
                leaf["impl"].split(":")[1]
                for entries in _declarations(form, agent).values()
                for leaf in entries.values()
                if leaf["impl"].split(":")[0] == own_module
            }
            assert referenced, f"{agent} 的声明面必须点名本 Agent 的薄工厂"
            for name in sorted(referenced):
                node = by_name[name]
                names = {arg.arg for arg in node.args.kwonlyargs}
                body = ast.get_source_segment(source, node)
                assert names, f"{agent}.{name} 必须为纯关键字签名"
                assert all(arg is None for arg in node.args.args), f"{agent}.{name} 不得有位置参数"
                assert "agent_config" in names, f"{agent}.{name} 必须经 agent_config 读既有参数"
                assert "agent_config" in body

    def test_既有dataclass读路径仍是唯一来源(self):
        """先例 `tests/unit/test_form_switch.py` 的 dataclass 读路径一字不改且仍可用。"""
        from agents.sound.config import SoundConfig

        sound = SoundConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        movie = SoundConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert sound.av_sync_threshold_ms < movie.av_sync_threshold_ms
        assembled = _build("sound", sound)
        assert (
            assembled[1].spec.version
            == BASELINE["shortdrama"]["sound"]["slots"][1][1].split("@")[1]
        )
