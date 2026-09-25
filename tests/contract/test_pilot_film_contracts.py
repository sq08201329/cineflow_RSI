"""功能 018 阶段 6 / US3（契约 C1~C13 聚合）：七环节长片链的契约聚合断言。

本文件把 018 的十三项契约**聚合**成一处机检（细节用例分散在各特性的单元/契约文件里，
此处只做跨契约的"同一口径"复核与**对抗/篡改面常驻举证**）：

- **C1** 清单同步不变量（元组 == 表 == 配置段 == 权重 == 档位）、编排层不新增落树路径；
- **C2** `dev` 入口只用既有轮次入口、策略装载下沉 `agents/dev/`（装载即静态检查）；
- **C3** `dev` 档两形态声明、调用点档位齐备（计数仍 8）；
- **C4** 产物 kind 与内容类型双向一致；
- **C5~C7** 交接守恒、读取集锁定、`renames` 覆盖、取数依据登记、两侧同步；
- **C8/C9** 索引网格容量下界/量子上界、常量退役、升版义务的材料在位；
- **C10** 排练档单点解析 + 两处时长一致 + 缺项拒绝启动；
- **C11** 逐环节分量入包与缺项拒绝；**C12** 标注取值域与"模拟被标为真实恒 0"；
- **C13** 画像 `verdict`、三方口径、`LLMGateway(` 构造点仍 **13** 处（019 普查，E-03）。

**对抗/篡改面常驻**（本特性的对抗面在此聚合举证，不并入既有套件）：改写包内标注/分量后再
复验、把 `simulated` 改标 `real`、把墙钟塞进五件套、抹掉角色键让"缺项放行"——各 100% 被拒。
"""

import ast
import json
from pathlib import Path

import pytest
import yaml

from agents.pilot import handoffs, run_report
from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.package import PACKAGE_FILES, PackageError, load_manifest, verify_package
from agents.pilot.pilot import PilotInputs, PrecheckError, precheck, run_pilot
from agents.pilot.scale import derived_shot_count
from agents.pilot.stages import PILOT_STAGE_IDS, build_stage_specs
from core.billing.runlog import RUN_ENTRY_FIELDS
from core.orchestration.models import StageStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
FORM = "shortdrama"
SEVEN = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")
# 019 的构造点普查口径（core/ + agents/ + ops/，不含 tests）
GATEWAY_SCAN_ROOTS = ("core", "agents", "ops")
GATEWAY_CONSTRUCTION_COUNT = 13
_CHAT_CALL_SITE_COUNT = 8
_CAPTURED: dict = {}


def _demo_config(tmp_path: Path, *, name: str = "demo") -> Path:
    text = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    assert "root: billing" in text
    text = text.replace("root: billing", f"root: {tmp_path / 'billing'}")
    for old, new in (
        ("target_duration_s: 120.0", "target_duration_s: 30.0"),
        ("script_target_minutes: 2.0", "script_target_minutes: 0.5"),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "configs" / f"shortdrama-{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=0.5,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


@pytest.fixture(scope="module")
def run_context(tmp_path_factory):
    """一次完整七环节运行（C1~C13 的验收面共用）+ 捕获 runtime 供逐项复核。"""
    tmp_path = tmp_path_factory.mktemp("film-contracts")
    config_path = _demo_config(tmp_path)
    original = stages_module.build_runtime
    captured: dict = {}

    def _capture(**kwargs):
        runtime = original(**kwargs)
        captured["runtime"] = runtime
        return runtime

    stages_module.build_runtime = _capture
    try:
        result = run_pilot(
            form=FORM,
            config_path=config_path,
            inputs=_inputs(),
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
            run_id="run-film",
            clock=lambda: "2026-01-01T00:00:00+00:00",
        )
    finally:
        stages_module.build_runtime = original
    assert result.record.status.value == "done"
    return {
        "result": result,
        "runtime": captured["runtime"],
        "config_path": config_path,
        "tmp": tmp_path,
    }


def _package_json(run_context, name: str) -> dict:
    path = run_context["result"].package_dir / name
    return json.loads(path.read_text(encoding="utf-8"))


def _pieces(run_context, *, manifest=None, state=None) -> package_module.PackagePieces:
    package_dir = run_context["result"].package_dir

    def _load(name: str) -> dict:
        return json.loads((package_dir / name).read_text(encoding="utf-8"))

    return package_module.PackagePieces(
        manifest=_load("manifest.json") if manifest is None else manifest,
        reel=(package_dir / "reel.mp4").read_bytes(),
        products=_load("products.json"),
        cost=_load("cost.json"),
        state=_load("state.json") if state is None else state,
    )


class TestC1清单同步与静态断言:
    def test_七处声明一致(self, run_context):
        runtime = run_context["runtime"]
        assert (
            stages_module.declaration_mismatches(build_stage_specs(runtime), runtime.configs) == []
        )
        assert tuple(spec.stage_id for spec in build_stage_specs(None)) == PILOT_STAGE_IDS == SEVEN

    def test_编排层不新增落树路径(self):
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        for banned in ("store.append_node(", "create_tree(", "store.create_tree("):
            assert banned not in source, banned

    def test_零形态分支与_agents_不依赖_ops(self):
        offenders = []
        for root in ("core", "agents"):
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                for banned in ("shortdrama", '"movie"', "'movie'", "form ==", "form is "):
                    assert banned not in source, f"{path}:{banned}"
                tree = ast.parse(source, filename=str(path))
                for node in ast.walk(tree):
                    names: list[str] = []
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        names = [node.module or ""]
                    if root == "agents" and any(
                        name == "ops" or name.startswith("ops.") for name in names
                    ):
                        offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []


class TestC2_C3链首与预算:
    def test_dev_入口只用既有轮次入口(self):
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        assert "run_dev_round(" in source
        entry = source.split("def _dev_entry", 1)[1].split("\ndef ", 1)[0]
        assert "run_dev_round(" in entry
        assert "agents/dev/policy_loader.py" in source  # 策略装载下沉（装载即静态检查）

    def test_调用点档位齐备且计数仍_8(self):
        tiers = set()
        for form in ("movie", "shortdrama"):
            payload = yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text("utf-8"))
            # 020（C11）：档位在**渠道内** —— 取各渠道 `channels.<id>.tiers` 的键并集
            for channel in payload["budget"]["channels"].values():
                tiers |= set(channel["tiers"])
        sites = stages_module.chat_call_sites()
        assert len(sites) == _CHAT_CALL_SITE_COUNT
        assert set(stages_module.chat_stage_ids()) <= tiers
        assert "dev" in tiers

    def test_缺_dev_档即拒绝启动(self, run_context, tmp_path):
        payload = yaml.safe_load(run_context["config_path"].read_text("utf-8"))
        llm = next(
            key
            for key, spec in payload["budget"]["channels"].items()
            if spec["adapter"] == "pilot_llm"
        )
        payload["budget"]["channels"][llm]["tiers"].pop("dev")
        broken = tmp_path / "configs" / "no-dev-tier.yaml"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        with pytest.raises(PrecheckError, match="dev"):
            precheck(form=FORM, config_path=broken, inputs=_inputs(), data_dir=tmp_path / "p")


class TestC4产物_kind:
    def test_kind_登记双向一致(self):
        expected = {
            "slate": "json",
            "script": "json",
            "shotlist": "json",
            "material": "json",
            "animatic": "video",
            "clip": "video",
            "reel": "video",
            "audio": "audio",
        }
        assert package_module._KIND_CONTENT_TYPE == expected
        for spec in build_stage_specs(None):
            assert spec.output_kind in expected


class TestC5到C7交接:
    def test_守恒等式与读取集锁定(self):
        declaration = handoffs.dev_script_handoff_declaration()
        assert declaration.violations() == []
        assert handoffs.read_set_mismatches() == []
        assert declaration.renames["topic"] == "genre"
        assert "genre" not in declaration.dropped
        assert declaration.sources == handoffs.SELECTION_SOURCES

    def test_剧本输入显式声明来源(self, run_context):
        record = run_context["result"].record
        assert record.stage("script").detail["input_source"] == handoffs.HANDOFF_MODE_DEV
        assert (
            record.stage("script").detail["selected_entry"]
            == (record.stage("dev").detail["production_marks"][0])
        )
        report = precheck(
            form=FORM,
            config_path=run_context["config_path"],
            inputs=_inputs(),
            data_dir=run_context["tmp"] / "pilot",
        )
        assert report["script_input_source"]["mode"] == handoffs.HANDOFF_MODE_DEV

    def test_漏同步与要点缺失即拒绝(self, run_context):
        from agents.dev import export_slate as export_slate_module
        from agents.dev.artifact import TopicSlate, simulated_signal_sources

        slate = TopicSlate(
            entries=[
                {
                    "direction_id": "dir-a",
                    "rationale": "论证",
                    "eval_components": {},
                    "genre": "医疗悬疑",
                    "constraints": ["夜戏"],
                    "characters": ["林静"],
                    "in_production": True,
                }
            ],
            signal_sources=simulated_signal_sources({"baseline_usd_million": 10.0}),
            production_marks=("dir-a",),
        )
        export = export_slate_module.export_slate(slate)
        assert (
            handoffs.dev_to_script_inputs(export, _inputs().to_dict()).inputs["topic"] == "医疗悬疑"
        )
        with pytest.raises(handoffs.HandoffError, match="悬空"):
            handoffs.dev_to_script_inputs(
                {**export, "production_marks": ["dir-ghost"]}, _inputs().to_dict()
            )
        broken = {
            **export,
            "entries": [{**export["entries"][0], "genre": ""}],
        }
        with pytest.raises(handoffs.HandoffError, match="genre"):
            handoffs.dev_to_script_inputs(broken, _inputs().to_dict())


class TestC8_C9网格与升版:
    def test_两形态容量上下界(self):
        for form in ("movie", "shortdrama"):
            payload = yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text("utf-8"))
            grid = payload["storyboard"]["render"]["index_grid"]
            shots = derived_shot_count(
                scene_count=payload["pilot"]["scene_count"],
                target_duration_s=float(payload["editing"]["target_duration_s"]),
                clip_duration_seconds=float(payload["visual"]["clip_spec"]["duration_seconds"]),
            )
            assert shots <= 2 ** (grid["rows"] * grid["cols"]), form  # 容量下界
            assert 2 ** grid["cols"] <= payload["storyboard"]["render"]["width"], form  # 量子上界
            assert 1 <= grid["rows"] <= payload["storyboard"]["render"]["height"]

    def test_码内常量退役且升版材料在位(self):
        source = (REPO_ROOT / "agents/storyboard/board_render.py").read_text(encoding="utf-8")
        assert "INDEX_BITS" not in source and "_INDEX_ROWS" not in source
        assert "index_grid" in source
        alignment = (REPO_ROOT / "agents/storyboard/evaluators/alignment.py").read_text(
            encoding="utf-8"
        )
        assert "frame_function_hash" in alignment  # 网格参数经实现哈希进版本（原则一）
        # 无迁移/无回填路径（旧节点与旧工件不改写）
        assert not list((REPO_ROOT / "ops/migrations").glob("*grid*"))


class TestC10排练档:
    def test_体量键唯一解析者与两处时长一致(self, run_context):
        runtime = run_context["runtime"]
        report = precheck(
            form=FORM,
            config_path=run_context["config_path"],
            inputs=_inputs(),
            data_dir=run_context["tmp"] / "pilot",
        )
        volume = report["pilot_volume"]["effective"]
        assert volume["target_duration_s"] == pytest.approx(
            volume["script_target_minutes"] * 60.0, abs=1e-6
        )
        assert volume["scene_count"] == runtime.pilot.scene_count
        assert volume["lines_per_scene"] == runtime.pilot.lines_per_scene

    def test_缺体量键与时长矛盾即拒绝启动(self, run_context, tmp_path):
        payload = yaml.safe_load(run_context["config_path"].read_text("utf-8"))
        payload["pilot"].pop("scene_count")
        missing = tmp_path / "configs" / "no-scene.yaml"
        missing.parent.mkdir(parents=True, exist_ok=True)
        missing.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        with pytest.raises(PrecheckError, match="scene_count"):
            precheck(form=FORM, config_path=missing, inputs=_inputs(), data_dir=tmp_path / "p")
        payload = yaml.safe_load(run_context["config_path"].read_text("utf-8"))
        payload["editing"]["target_duration_s"] = 60
        mismatched = tmp_path / "configs" / "bad-duration.yaml"
        mismatched.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        with pytest.raises(PrecheckError, match="120 s"):
            precheck(form=FORM, config_path=mismatched, inputs=_inputs(), data_dir=tmp_path / "p")


class TestC11到C13证据面:
    def test_分量与标注入包(self, run_context):
        state = _package_json(run_context, "state.json")
        gateway = _package_json(run_context, "cost.json")["gateway"]
        manifest = load_manifest(run_context["result"].package_dir)
        assert set(state["eval_breakdown"]) == set(SEVEN)
        assert all(state["eval_breakdown"][stage] for stage in SEVEN)
        assert {row["source"] for row in manifest["stages"]} == {"simulated"}
        assert gateway["reconciled"] is True
        assert set(gateway["by_stage"]) == set(SEVEN)
        assert tuple(RUN_ENTRY_FIELDS) == (
            "at",
            "stage",
            "source",
            "adapter_ref",
            "profile_id",
            "result",
            "cost_source",
            "fallback_reason",
        )

    def test_画像结论与三方口径(self, run_context):
        record = run_context["result"].record
        profile = run_report.build_profile(record, run_context["runtime"], clock_mode="fixed")
        assert profile["verdict"] == "not_evaluable"
        assert run_report.FIXED_CLOCK_NOTE in profile["verdict_reason"]
        assert profile["volume"]["shot_count"] == record.stage("storyboard").detail["shot_count"]
        assert profile["volume"] == _package_json(run_context, "state.json")["volume"]

    def test_构造点普查仍_13_处(self):
        sites: list[str] = []
        for root in GATEWAY_SCAN_ROOTS:
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                        if name == "LLMGateway":
                            sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert len(sites) == GATEWAY_CONSTRUCTION_COUNT, sites


class Test对抗与篡改面:
    def test_改标真实即拒(self, run_context):
        manifest = load_manifest(run_context["result"].package_dir)
        manifest["stages"][4]["source"] = "real"  # sound 伪标真实
        with pytest.raises(PackageError, match="装配面声明"):
            package_module._validate_pieces(_pieces(run_context, manifest=manifest))

    def test_抹掉分量即拒(self, run_context):
        state = _package_json(run_context, "state.json")
        state["eval_breakdown"].pop("promo")
        with pytest.raises(PackageError, match="promo"):
            package_module._validate_pieces(_pieces(run_context, state=state))

    def test_墙钟塞进五件套即拒(self, run_context):
        state = _package_json(run_context, "state.json")
        first_stage = next(iter(state["eval_breakdown"]["dev"]))
        key = next(iter(state["eval_breakdown"]["dev"][first_stage]))
        state["eval_breakdown"]["dev"][first_stage][key]["created_at"] = 1.0
        with pytest.raises(PackageError, match="墙钟"):
            package_module._validate_pieces(_pieces(run_context, state=state))

    def test_删掉角色键让缺项放行即拒(self, run_context, tmp_path):
        """上游要点被抹掉（`characters` 为空）⇒ 交接侧拒绝（承接不解除要点校验）。"""
        from agents.dev import export_slate as export_slate_module

        package_dir = run_context["result"].package_dir
        slate_hash = run_context["result"].record.stage("dev").detail["artifact_hash"]
        slate = json.loads(run_context["runtime"].artifacts.get(slate_hash))
        entry = dict(slate["entries"][0])
        entry["characters"] = []
        slate["entries"] = [entry]
        export = export_slate_module.export_slate(
            __import__("agents.dev.artifact", fromlist=["TopicSlate"]).TopicSlate.from_dict(slate)
        )
        with pytest.raises(handoffs.HandoffError, match="characters"):
            handoffs.dev_to_script_inputs(export, _inputs().to_dict())
        assert package_dir.is_dir()  # 包不受影响（拒绝发生在交接侧）

    def test_复验发现篡改(self, run_context, tmp_path):
        """包落盘后被改写 ⇒ 复验拒绝（`verify_package` 走同一套证据面校验）。

        **登记边界（如实）**：体量字段（`state.volume`）的篡改不改变五件齐备性，也不被复验
        重算（复验不新造测量）——它的真相源是运行记录与工件（复装配即发现），故此处只断言
        "与运行记录对照可辨"，不假装复验能独立发现。
        """
        tampered = tmp_path / "tampered"
        tampered.mkdir(parents=True, exist_ok=True)
        for name in PACKAGE_FILES:
            (tampered / name).write_bytes((run_context["result"].package_dir / name).read_bytes())
        manifest = json.loads((tampered / "manifest.json").read_text(encoding="utf-8"))
        manifest["stages"][5]["source"] = "real"
        (tampered / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(PackageError):
            verify_package(tampered)
        # 体量篡改：与运行记录对照可辨（复验不重算体量，故在此如实登记边界）
        state_path = tampered / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["volume"]["shot_count"] = 999999
        state_path.write_text(json.dumps(state), encoding="utf-8")
        record = run_context["result"].record
        assert state["volume"]["shot_count"] != record.stage("storyboard").detail["shot_count"]

    def test_环节失败即整轮失败零半包(self, run_context, tmp_path, monkeypatch):
        def _boom(stage_input):
            del stage_input
            from core.orchestration.errors import StageFailedError

            raise StageFailedError("宣发环节注入失败")

        monkeypatch.setattr(stages_module, "_promo_entry", _boom)
        result = run_pilot(
            form=FORM,
            config_path=run_context["config_path"],
            inputs=_inputs(),
            data_dir=tmp_path / "fail" / "pilot",
            artifacts_root=tmp_path / "fail" / "artifacts",
            run_id="run-fail",
            clock=lambda: "2026-01-01T00:00:00+00:00",
        )
        assert result.record.status.value == "failed"
        assert result.record.stage("promo").status is StageStatus.FAILED
        assert result.package_dir is None
        assert not (tmp_path / "fail" / "pilot" / "packages" / "run-fail").exists()
