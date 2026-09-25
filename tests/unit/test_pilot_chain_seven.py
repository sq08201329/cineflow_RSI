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

阶段 4（US1 / T1819）在本文件补**七环节端到端**：全模拟后端 + 固定时钟跑一轮 ⇒ 七环节全
`done`、五件套齐备且过 `verify_package`、同输入同配置独立工件根逐字节一致、任一环失败不产
半包、断点续跑零重跑（指纹不一致即拒绝）、`dev` 产物 `slate` 内容寻址可达且策略版本 == 部署
指针、`cost.json` 的 `by_stage` 键集覆盖七环节。**如实登记（F-08）**：包面**新增字段**
（`source`/`channels`/`work_kind`/`eval_breakdown`/`volume`）及其子集逐字节一致属阶段 6 的
产物面（T1829），本阶段只要求五件套本体逐字节一致。

另加四条**常驻静态断言**（不放松）：编排层不新增落树路径、零形态分支、`agents/` 不 import
`ops/`、`core/` 零形态字面量。
"""

import ast
import json
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.pilot import PilotInputs, resume_pilot, run_pilot
from agents.pilot.stages import (
    PILOT_STAGE_IDS,
    STAGE_CONFIG_SECTION,
    STAGE_TREE_PREFIX,
    AgentConfigs,
    build_runtime,
    build_stage_specs,
)
from core.orchestration.dag import build_dag
from core.orchestration.errors import OrchestrationError, StageFailedError
from core.orchestration.models import RunStatus, StageStatus

FORM = "shortdrama"
FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
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
        # 020（C11）：档位在**渠道内** —— 取各渠道 `channels.<id>.tiers` 的键并集
        for channel in payload["budget"]["channels"].values():
            keys |= set(channel["tiers"])
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


def _demo_inputs() -> PilotInputs:
    """演示档输入（0.5 分钟 = 30 秒，与夹具配置的排练档同口径）。"""
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=0.5,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


def _fixed_clock():
    """确定性时钟（全部时间戳同值：两次运行逐字节一致的对照口径）。"""
    return lambda: FIXED_TIMESTAMP


def _run_once(config_path, data_dir, artifacts_root, run_id: str):
    return run_pilot(
        form=FORM,
        config_path=config_path,
        inputs=_demo_inputs(),
        data_dir=data_dir,
        artifacts_root=artifacts_root,
        run_id=run_id,
        clock=_fixed_clock(),
    )


def _spec_with(stage_id, **overrides):
    specs = build_stage_specs(None)
    spec = next(item for item in specs if item.stage_id == stage_id)
    fields_ = {field.name: getattr(spec, field.name) for field in fields(type(spec))}
    fields_.update(overrides)
    return stages_module.StageSpec(**fields_)


class Test七环节端到端:
    """US1（T1819）：全模拟后端 + 固定时钟跑一轮——七环节全 `done`、五件套过校验、可复现。

    **如实登记（F-08）**：包面**新增字段**（`source`/`channels`/`work_kind`/`eval_breakdown`/
    `volume`）与其子集逐字节一致属阶段 6 的产物面（T1829）——本文件只要求**五件套本体**逐字节
    一致（015 口径不变）。
    """

    def test_七环节全_done_且五件套过校验(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        result = _run_once(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", "run-seven")
        assert result.record.status is RunStatus.DONE
        assert tuple(state.stage_id for state in result.record.stages) == SEVEN
        assert tuple(state.stage_id for state in result.record.stages) == PILOT_STAGE_IDS
        assert all(state.status is StageStatus.DONE for state in result.record.stages)
        assert result.record.completed_stages == SEVEN
        verified = package_module.verify_package(result.package_dir)
        assert verified["files"] == list(package_module.PACKAGE_FILES)
        assert verified["reconciled"] is True
        # 五件套本体齐备（第六件不存在：画像与报告落报告侧，不进包）
        assert sorted(path.name for path in result.package_dir.iterdir()) == sorted(
            package_module.PACKAGE_FILES
        )

    def test_同输入同配置独立工件根逐字节一致(self, pilot_demo_config_path, tmp_path):
        first = _run_once(
            pilot_demo_config_path,
            tmp_path / "a" / "pilot",
            tmp_path / "a" / "artifacts",
            "run-cmp",
        )
        second = _run_once(
            pilot_demo_config_path,
            tmp_path / "b" / "pilot",
            tmp_path / "b" / "artifacts",
            "run-cmp",
        )
        for name in package_module.PACKAGE_FILES:
            assert (first.package_dir / name).read_bytes() == (
                second.package_dir / name
            ).read_bytes(), name

    def test_cost_json_by_stage_覆盖七环节(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        """逐环节成本入账（运行记录 ⨯ 各 Agent 落盘账目）——第三方腿（网关记账）属阶段 6。"""
        result = _run_once(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", "run-cost")
        cost = json.loads((result.package_dir / "cost.json").read_text(encoding="utf-8"))
        assert set(cost["by_stage"]) == set(PILOT_STAGE_IDS)
        assert {line["stage_id"] for line in cost["lines"]} == set(PILOT_STAGE_IDS)
        for line in cost["lines"]:
            assert abs(float(line["recorded_usd"]) - float(line["ledger_usd"])) <= 1e-9
        assert cost["reconciled"] is True
        assert abs(float(cost["total_usd"]) - sum(cost["by_stage"].values())) <= 1e-9
        assert abs(float(cost["total_usd"]) - float(result.record.total_cost_usd)) <= 1e-9

    def test_任一环失败即整轮失败且不产半包(
        self, pilot_demo_config_path, pilot_dirs, tmp_path, monkeypatch
    ):
        def _boom(stage_input):
            del stage_input
            raise StageFailedError("视觉环节注入失败（下半链零调用）")

        monkeypatch.setattr(stages_module, "_visual_entry", _boom)
        result = _run_once(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", "run-fail")
        assert result.record.status is RunStatus.FAILED
        assert result.record.failure_stage == "visual"
        assert result.record.stage("visual").failure_reason
        assert result.record.stage("visual").status is StageStatus.FAILED
        for stage_id in ("sound", "editing", "promo"):
            assert result.record.stage(stage_id).status is StageStatus.SKIPPED
        assert result.package_dir is None  # 不装配（零半包）
        assert not (pilot_dirs / "packages" / "run-fail").exists()

    def test_断点续跑零重跑且指纹不一致即拒绝(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        run_id = "run-resume"
        artifacts_root = tmp_path / "artifacts"
        first = _run_once(pilot_demo_config_path, pilot_dirs, artifacts_root, run_id)
        resumed = resume_pilot(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_demo_inputs(),
            data_dir=pilot_dirs,
            artifacts_root=artifacts_root,
            run_id=run_id,
            clock=_fixed_clock(),
        )
        assert resumed.record == first.record  # 幂等：零重跑、零重复落盘
        assert all(state.attempts == 1 for state in resumed.record.stages)
        # 输入指纹不一致即拒绝（不把旧记录当"幂等成功"返回）
        with pytest.raises(OrchestrationError):
            resume_pilot(
                form=FORM,
                config_path=pilot_demo_config_path,
                inputs=PilotInputs(
                    topic="换一个题材",
                    target_duration_min=0.5,
                    characters=("林静", "陈默"),
                    constraints=(),
                    genre_bounds=("悬疑",),
                    audience="都市女性",
                ),
                data_dir=pilot_dirs,
                artifacts_root=artifacts_root,
                run_id=run_id,
                clock=_fixed_clock(),
            )

    def test_dev_产物_slate_可寻址且策略版本等于部署指针(
        self, pilot_demo_config_path, pilot_dirs, tmp_path
    ):
        artifacts_root = tmp_path / "artifacts"
        result = _run_once(pilot_demo_config_path, pilot_dirs, artifacts_root, "run-slate")
        state = result.record.stage("dev")
        product = next(item for item in state.products if item.kind == "slate")
        assert product.content_hash == state.detail["artifact_hash"]  # 内容寻址（put 返回即哈希）
        assert len(product.content_hash) == 64
        assert product.ref == product.content_hash
        runtime = build_runtime(
            form=FORM,
            config_path=pilot_demo_config_path,
            data_dir=pilot_dirs,
            artifacts_root=artifacts_root,
        )
        slate = json.loads(runtime.artifacts.get(product.content_hash))
        assert slate["entries"]  # 立项组合非空（可寻址的内容确是立项产物）
        pointer = yaml.safe_load(pilot_demo_config_path.read_text(encoding="utf-8"))["deployment"][
            "dev"
        ]["current_policy_version"]
        assert state.detail["policy_version"] == pointer  # 版本取部署指针（不回落"最新"）
        assert state.detail["entry_count"] == len(slate["entries"])

    def test_逐环节评估分量的取数路径可用(
        self, pilot_demo_config_path, pilot_dirs, tmp_path, monkeypatch
    ):
        """七环节轮次树节点逐环节携带非空 `eval_breakdown`（键 = `evaluator_id@version`）。

        **取数路径**（阶段 6 的 T1829 依此入包，本阶段只机检"路径可用"，不动包面）：
        `STAGE_TREE_PREFIX[stage_id] + "-round-" + f"{run_id}-{stage_id}"` →
        `runtime.store.nodes_of(tree_id)`（`depth >= 1`）→ 节点原文的 `eval_breakdown`。
        **不得**以 stage_id 直推树前缀（`script` 实为 `screenplay-round-`）。
        """
        captured: dict = {}
        original = stages_module.build_runtime

        def _capture(**kwargs):
            runtime = original(**kwargs)
            captured["runtime"] = runtime
            return runtime

        monkeypatch.setattr(stages_module, "build_runtime", _capture)
        run_id = "run-breakdown"
        _run_once(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", run_id)
        runtime = captured["runtime"]
        for stage_id in PILOT_STAGE_IDS:
            tree_id = stages_module.round_tree_id_of(stage_id, f"{run_id}-{stage_id}")
            nodes = [node for node in runtime.store.nodes_of(tree_id) if node.depth >= 1]
            assert nodes, f"{stage_id} 轮次树无候选节点（{tree_id}）"
            for node in nodes:
                assert node.eval_breakdown, f"{stage_id}:{node.node_id} 分量面为空"
                for key, value in node.eval_breakdown.items():
                    assert "@" in key, f"{stage_id} 分量键不是 evaluator_id@version：{key}"
                    assert "score" in value
        assert stages_module.round_tree_id_of("script", "r").startswith("screenplay-round-")


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
