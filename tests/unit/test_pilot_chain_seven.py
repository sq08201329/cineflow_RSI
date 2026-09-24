"""功能 018 阶段 2（契约 C1/C2/C4）：七环节链的**清单同步不变量**（漏一处即红）。

链首插入 `dev` 之后，"元组 / 阶段表 / `AgentConfigs` / 运行时装配 / 预检四处清单 / 产物
kind 登记 / 两形态声明"七处集中声明必须同批落地——本文件把这些声明之间的**一致性**钉成
断言（不靠人记得）：

1. 拓扑序恰为七元组 `dev → script → storyboard → visual → sound → editing → promo`；
2. `STAGE_CONFIG_SECTION` / `STAGE_TREE_PREFIX` 的键域 == 阶段元组、值域 == 配置字段名集，
   且树前缀与各 Agent loop 的 `round_tree_id` 实测一致（不得以 stage_id 直推）；
3. 阶段表 `output_kind` 的全部取值 ∈ `package._KIND_CONTENT_TYPE`（C4）；
4. 七环节的权重/加载器/档位声明齐备（含 `dev` 档，缺档即红）；
5. **模拟漏同步**（从阶段表删 `dev`）⇒ 一致性断言红（不是"少一环也能跑"）；
6. `dev` 拒绝路径（`artifact_hash is None`）⇒ 阶段 `failed`、其后环节全 `skipped`、零下游调用。

另加四条**常驻静态断言**（不放松）：编排层不新增落树路径、零形态分支、`agents/` 不 import
`ops/`、`core/` 零形态字面量。
"""

import ast
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.stages import (
    PILOT_STAGE_IDS,
    STAGE_CONFIG_SECTION,
    STAGE_TREE_PREFIX,
    AgentConfigs,
    build_runtime,
    build_stage_specs,
)
from core.orchestration.dag import build_dag
from core.orchestration.models import RunStatus, StageStatus

FORM = "shortdrama"
REPO_ROOT = Path(__file__).resolve().parents[2]
# 链首插入后的拓扑序（契约 C1 的字面量：本文件独立声明，不复用实现常量）
SEVEN = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")
# 环节 id → 该环节对应的 Agent loop 模块（树前缀实测的对照面）
_STAGE_LOOP_MODULES = {
    "dev": "agents.dev.loop",
    "script": "agents.screenplay.loop",
    "storyboard": "agents.storyboard.loop",
    "visual": "agents.visual.loop",
    "sound": "agents.sound.loop",
    "editing": "agents.editing.loop",
    "promo": "agents.promo.loop",
}


@pytest.fixture()
def runtime(pilot_form_config_path, pilot_dirs, tmp_path):
    return build_runtime(
        form=FORM,
        config_path=pilot_form_config_path(FORM),
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
    )


@pytest.fixture()
def tiers(tmp_path):
    """两形态 `budget.tiers` 键集（调用点声明的环节 id 必须逐一声明额度）。"""
    keys: set[str] = set()
    for form in ("movie", "shortdrama"):
        payload = yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text("utf-8"))
        keys |= set(payload["budget"]["tiers"])
    return keys


class Test清单同步不变量:
    def test_拓扑序恰为七元组(self, runtime):
        specs = build_stage_specs(runtime)
        assert tuple(spec.stage_id for spec in specs) == PILOT_STAGE_IDS == SEVEN
        dag = build_dag(specs)
        assert dag.topological_order() == list(SEVEN)
        assert dag.spec("dev").depends_on == ()  # 链首无依赖
        assert dag.spec("script").depends_on == ("dev",)  # 剧本阶段改依赖链首
        for previous, stage_id in zip(SEVEN, SEVEN[1:], strict=False):
            assert dag.spec(stage_id).depends_on == (previous,)

    def test_配置段与树前缀是单一映射声明(self, runtime):
        config_fields = {field.name for field in fields(AgentConfigs)}
        assert set(STAGE_CONFIG_SECTION) == set(PILOT_STAGE_IDS)
        assert set(STAGE_CONFIG_SECTION.values()) == config_fields
        assert STAGE_CONFIG_SECTION["script"] == "screenplay"  # 不得以 stage_id 直推
        assert set(STAGE_TREE_PREFIX) == set(PILOT_STAGE_IDS)
        # 树前缀与各 Agent loop 的实测前缀一致（`script` 实为 `screenplay-round-`）
        for stage_id, prefix in STAGE_TREE_PREFIX.items():
            module = __import__(_STAGE_LOOP_MODULES[stage_id], fromlist=["round_tree_id"])
            probe = "probe"
            expected = module.round_tree_id(probe).removesuffix(f"-round-{probe}")
            assert prefix == expected, f"{stage_id} 树前缀 {prefix!r} != 实测 {expected!r}"
            assert stages_module.round_tree_id_of(stage_id, probe) == module.round_tree_id(probe)

    def test_产物_kind_全部登记(self, runtime):
        for spec in build_stage_specs(runtime):
            assert spec.output_kind in package_module._KIND_CONTENT_TYPE, spec.stage_id

    def test_七环节权重加载器与档位齐备(self, pilot_form_config_path, tiers):
        from agents.pilot.pilot import config_completeness
        from core.evaluators.weights import load_evaluator_weights

        checked = config_completeness(pilot_form_config_path(FORM))
        # 每条链上环节的配置段名（`script → screenplay`）都必须有加载器与权重登记
        for stage_id in SEVEN:
            section = STAGE_CONFIG_SECTION[stage_id]
            assert section in checked, f"{stage_id}（{section} 段）未登记加载器"
            assert f"weights:{section}" in checked, f"{stage_id}（{section} 权重）未登记"
            assert load_evaluator_weights(pilot_form_config_path(FORM), section)
        # 调用点声明的环节 id 必须两形态均有档位声明（含 dev 档；缺档即拒绝启动）
        assert "dev" in tiers
        assert set(stages_module.chat_stage_ids()) <= tiers

    def test_模拟漏同步即红(self, runtime):
        """从阶段表删 `dev` ⇒ 一致性断言红（不是"少一环也能跑"）。"""
        specs = build_stage_specs(runtime)
        assert stages_module.declaration_mismatches(specs, runtime.configs) == []
        dropped = [spec for spec in specs if spec.stage_id != "dev"]
        mismatches = stages_module.declaration_mismatches(dropped, runtime.configs)
        assert mismatches, "阶段表删 dev 后未报不一致"
        assert any("dev" in item or "拓扑" in item for item in mismatches)

    def test_dev_拒绝路径下游零调用(self, runtime, monkeypatch):
        """`run_dev_round` 返回 `artifact_hash is None` ⇒ 阶段 failed、其后 skipped、零调用。"""
        from core.orchestration.executor import ExecutionContext
        from core.orchestration.executor import run as run_dag
        from core.orchestration.models import StageOutcome

        rejected = {
            "job_id": "dev-round-x-slate",
            "status": "rejected",
            "reason": "策略计划缺少 entries（执行前拒绝）",
            "artifact_hash": None,
            "cache_key": None,
            "response_hash": None,
        }

        class _DevResult:
            round_id = "run-1-dev"
            tree_id = "dev-round-run-1-dev"
            policy_version = "34525518074d"
            job = rejected
            spent_usd = 0.0
            cost_reconciliation: dict = {}

        monkeypatch.setattr(
            stages_module, "run_dev_round", lambda **kwargs: _DevResult(), raising=True
        )
        calls: list[str] = []

        def _stub(stage_id):
            def _entry(stage_input):
                calls.append(stage_id)
                return StageOutcome(products=(), cost_usd=0.0)

            return _entry

        specs = [
            spec
            if spec.stage_id == "dev"
            else stages_module.StageSpec(
                stage_id=spec.stage_id,
                entrypoint=_stub(spec.stage_id),
                depends_on=spec.depends_on,
                handoff=spec.handoff,
                title=spec.title,
                output_kind=spec.output_kind,
            )
            for spec in build_stage_specs(runtime)
        ]
        stages_module.bind_runtime(runtime)
        record = run_dag(
            build_dag(specs),
            ExecutionContext(
                run_id="run-1",
                form=FORM,
                config_fingerprint=runtime.config_fingerprint,
                input_fingerprint="ab" * 32,
                shared={
                    "runtime": runtime,
                    "run_id": "run-1",
                    "pilot_inputs": _pilot_inputs().to_dict(),
                },
            ),
            clock=lambda: "2026-01-01T00:00:00+00:00",
        )
        assert record.status is RunStatus.FAILED
        assert record.stage("dev").status is StageStatus.FAILED
        assert record.stage("dev").failure_reason  # 原因点名（不静默）
        for stage_id in SEVEN[1:]:
            assert record.stage(stage_id).status is StageStatus.SKIPPED
        assert calls == []  # 下游零调用（不带着空工件往下走）


def _pilot_inputs():
    from agents.pilot.pilot import PilotInputs

    return PilotInputs(
        topic="夜班记录",
        target_duration_min=1.0,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑",),
        audience="都市女性",
    )


def _spec_with(stage_id, **overrides):
    specs = build_stage_specs(None)
    spec = next(item for item in specs if item.stage_id == stage_id)
    fields_ = {field.name: getattr(spec, field.name) for field in fields(type(spec))}
    fields_.update(overrides)
    return stages_module.StageSpec(**fields_)


class Test拒绝语义:
    def test_dev_阶段登记产物_kind_为_slate(self):
        spec = next(spec for spec in build_stage_specs(None) if spec.stage_id == "dev")
        assert spec.output_kind == "slate"
        assert package_module._KIND_CONTENT_TYPE["slate"] == "json"

    def test_产物_kind_未登记即红(self, runtime):
        """值域对不上（未登记的产物类型）⇒ 一致性断言报错。"""
        specs = [_spec_with("script", output_kind="不存在的产物类型")]
        mismatches = stages_module.declaration_mismatches(specs, runtime.configs)
        assert any("产物" in item or "kind" in item for item in mismatches)


class Test常驻静态断言:
    def test_编排层不新增落树路径(self):
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        for banned in ("store.append_node(", "create_tree(", "store.create_tree("):
            assert banned not in source, banned

    def test_零形态分支(self):
        for path in [Path(stages_module.__file__), Path(package_module.__file__)]:
            source = path.read_text(encoding="utf-8")
            for banned in ("shortdrama", '"movie"', "form ==", "form is ", "form !="):
                assert banned not in source, f"{path}:{banned}"

    def test_agents_不反向依赖_ops(self):
        root = Path(stages_module.__file__).resolve().parents[1]  # agents/
        offenders = []
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(name == "ops" or name.startswith("ops.") for name in names):
                    offenders.append(str(path.relative_to(root.parent)))
        assert not offenders, offenders

    def test_core_无形态字面量(self):
        from agents.pilot.pilot import PilotInputs  # noqa: F401  （确保 core 扫描前模块已加载）

        for path in sorted((REPO_ROOT / "core").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            for banned in ("shortdrama", '"movie"', "'movie'", "form ==", "form is "):
                assert banned not in source, f"{path}:{banned}"
