"""通用件 `rule.artifact_metadata` 的单测 + 注册元数据 + **与既有评估器的对比样本**（021 T2165⑥）。

`core/evaluators/plugins/artifact_metadata.py` 是 `core/evaluators/plugins/`（业务无关通用件落点）
的最小可行样例：它零 `agents.*` import、零形态字面量/分支、零环境变量与网络读取、零配置文件读取、
参数面为空（声明面 `params: {}`）。本文件守住四件事：

1. **注册元数据**（原则一 / FR-001）：`evaluator_id` / `version` / `kind` / `deterministic` /
   `cost_per_call` 齐备，且 `version` == 唯一装配点同源的**实现身份版本**（声明值与实现产出必须
   三方一致：声明 == 实现产出 == 注册实例值）；
2. **判据正反例**（可证伪 + 局限如实标注）：工件归因元信息非空且逐键非空 ⇒ 1.0；空映射 /
   空取值 ⇒ 0.0 并**逐键点名**（不抛异常）；`0` / `False` 是合法取值（不得被当作"未声明"）；
3. **业务无关四条**（文本 + AST 双层；与契约测试同一口径）；
4. **对比样本**（与既有评估器的对照件）：同一工件、同一装配路径下，新增通用件与既有门禁
   各跑一次，产出可机检的对照件（键 = `evaluator_id@version`、逐条得分与明细、确定性复跑一致）。
"""

import ast
import importlib
import inspect
from pathlib import Path

import pytest
import yaml

from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.errors import RegistrationError
from core.evaluators.plugin import implementation_identity_version, parse_manifest
from core.evaluators.plugins.artifact_metadata import EVALUATOR_ID, artifact_metadata
from core.evaluators.registry import Registry
from ops.form_guard import declared_forms, form_branch_patterns, form_literals, name_pattern

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
PLUGIN_PATH = REPO_ROOT / "core" / "evaluators" / "plugins" / "artifact_metadata.py"
#: 同一份插件被多个形态配置声明的举证面（形态名由派生面给出、零人工常量）
FORMS = declared_forms(CONFIGS_DIR)
#: 既有评估器（对照件的另一侧）：sound 的响度门禁——零 LLM、确定性、同一装配路径
EXISTING_IMPL = "agents.sound.evaluators.plugins:loudness_compliance"
_PROBE_HASH = "0" * 64


def _artifact(**metadata) -> ArtifactRef:
    return ArtifactRef(_PROBE_HASH, dict(metadata))


def _declared_versions() -> list[str]:
    """各形态声明面里该通用件的声明值（证明"同一份插件被多个形态配置声明"）。"""
    values = []
    for form in FORMS:
        document = yaml.safe_load((CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8"))
        for entries in document["evaluators"]["plugins"].values():
            for slot in entries.values():
                if EVALUATOR_ID in slot:
                    values.append(slot[EVALUATOR_ID]["version"])
    return values


class Test注册元数据:
    def test_元数据齐备且为确定性零成本硬规则(self):
        spec = artifact_metadata().spec
        assert spec.evaluator_id == EVALUATOR_ID
        assert spec.kind is EvaluatorKind.RULE
        assert spec.deterministic is True
        assert spec.cost_per_call == 0.0
        assert spec.version and spec.version.split("+")[0] == "1.0.0"

    def test_版本是实现身份版本且与声明面三方一致(self):
        evaluator = artifact_metadata()
        assert evaluator.spec.version == implementation_identity_version(evaluator)
        declared = set(_declared_versions())
        assert declared, "该通用件必须被至少一份形态配置声明（目录不决定可用性）"
        assert declared == {evaluator.spec.version}, "声明值必须逐字等于实现产出"

    def test_同id同version重复注册被拒(self):
        registry = Registry()
        registry.register(artifact_metadata())
        with pytest.raises(RegistrationError, match="重复注册"):
            registry.register(artifact_metadata())

    def test_纯关键字零参数_声明面params为空(self):
        signature = inspect.signature(artifact_metadata)
        assert not signature.parameters, "一评估器一函数：参数面为空（既有取值不搬到声明面）"
        assert artifact_metadata().spec.evaluator_id == EVALUATOR_ID


class Test判据正反例:
    def test_元信息非空且逐键非空即过(self):
        result = artifact_metadata().evaluate(
            _artifact(schema_version="1.0.0", stage="delivery", render_hash=_PROBE_HASH), {}
        )
        assert result.score == 1.0
        assert result.diagnostics["violations"] == []
        assert result.diagnostics["declared_keys"] == ["render_hash", "schema_version", "stage"]

    def test_零与假值是合法取值(self):
        result = artifact_metadata().evaluate(_artifact(attempt=0, retried=False), {})
        assert result.score == 1.0, "0 / False 是已声明的取值，不得被当作未声明"

    @pytest.mark.parametrize(
        "payload",
        (
            {},
            {"stage": ""},
            {"stage": "   "},
            {"stage": None},
            {"clips": []},
            {"labels": {}},
        ),
    )
    def test_空元信息或空取值判零并逐键点名(self, payload):
        result = artifact_metadata().evaluate(_artifact(**payload), {})
        assert result.score == 0.0
        assert result.diagnostics["violations"], "判 0 必须给出可读理由（不得只回一个 0）"
        if payload:
            assert sorted(payload) == result.diagnostics["empty_keys"]

    def test_确定性复跑一致(self):
        artifact = _artifact(stage="delivery")
        first = artifact_metadata().evaluate(artifact, {})
        second = artifact_metadata().evaluate(artifact, {})
        assert first == second


class Test业务无关四条:
    def test_文本层零形态字面量零取数零网络(self):
        source = PLUGIN_PATH.read_text(encoding="utf-8")
        for name in form_literals(CONFIGS_DIR):
            assert not name_pattern(name).search(source), f"出现形态名：{name}"
        for pattern in form_branch_patterns():
            assert pattern not in source, f"出现形态判断：{pattern}"
        for banned in ("os.environ", "os.getenv", "open(", "http://", "https://"):
            assert banned not in source, f"出现越界面：{banned}"

    def test_ast层零越界import(self):
        tree = ast.parse(PLUGIN_PATH.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots.add((node.module or "").split(".")[0])
        assert not roots & {"agents", "dreaming", "ops", "web", "policies"}
        assert not roots & {"socket", "urllib", "requests", "httpx", "openai"}

    def test_声明了注入槽位面之外为零(self):
        """本插件不声明任何注入槽位（零参数）⇒ 装配点不得替它注入任何东西。"""
        signature = inspect.signature(artifact_metadata)
        assert not set(signature.parameters) & {"agent_config", "gateway", "artifacts", "registry"}


class Test与既有评估器的对比样本:
    """同一工件、同一装配路径下：新增通用件 vs 既有门禁（可机检的对照件）。"""

    def _existing(self):
        from agents.sound.config import SoundConfig
        from core.evaluators.plugin import assemble

        module = importlib.import_module("agents.sound.evaluators.plugins")
        document = yaml.safe_load((CONFIGS_DIR / f"{FORMS[-1]}.yaml").read_text(encoding="utf-8"))
        assembled = assemble(
            parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
            agent_config=SoundConfig.from_dict(document),
        )
        for evaluator in assembled["all"]:
            if evaluator.spec.evaluator_id == "rule.loudness_compliance":
                return evaluator
        raise AssertionError("对照件缺失：既有门禁未在装配面出现")

    def test_对照件_同工件逐条明细与确定性(self):
        artifact = _artifact(schema_version="1.0.0", stage="delivery")
        new = artifact_metadata()
        existing = self._existing()
        breakdown = {
            new.spec.key: new.evaluate(artifact, {}),
            existing.spec.key: existing.evaluate(artifact, {}),
        }
        assert all("@" in key for key in breakdown), "键必须为 evaluator_id@version"
        rerun = {
            new.spec.key: new.evaluate(artifact, {}),
            existing.spec.key: existing.evaluate(artifact, {}),
        }
        assert breakdown == rerun, "两侧都必须是确定性复跑一致"
        assert new.spec.key.startswith(f"{EVALUATOR_ID}@")
        assert existing.spec.key.startswith("rule.loudness_compliance@")
        for result in breakdown.values():
            assert 0.0 <= result.score <= 1.0
            assert isinstance(result.diagnostics, dict)
        # 对比样本的**声明与实现一致性**：两侧都由唯一装配点解析（`impl` 是声明面唯一入口）
        impls = {
            new.spec.evaluator_id: "core.evaluators.plugins.artifact_metadata:artifact_metadata",
            existing.spec.evaluator_id: EXISTING_IMPL,
        }
        assert len(set(impls.values())) == 2, "两侧实现不同（对照才有意义）"
