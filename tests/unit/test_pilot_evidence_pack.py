"""功能 018 阶段 6 / US3（契约 C11/C12）：样片包证据面——逐环节评估分量 + 逐环节真实/模拟标注。

覆盖：

1. `state.json` 逐环节**非空** `eval_breakdown`（键 = `evaluator_id@version`，形状与口径取自
   **树节点原文**——不改写、不归一化、不重算）；派生产物（分镜 `shotlist`）显式标
   `derived: true` + "无节点分量"（**不与缺项混同**）；抹掉任一环节的分量面 ⇒ **拒绝装配**；
2. `manifest.json` 的 `stages[].source`/`channel` + 顶层 `channels` 汇总（`llm` 的来源/
   `adapter_ref`/`profile_id` + 平台侧逐环节来源）+ `work_kind`；预检报告同步
   `stages: {stage_id: source}`；
3. **诚实约束**：全模拟 ⇒ 七项全 `simulated`、包内**无**"真实渠道全链路已跑通"一类表述、
   `SIMULATED_NOTE` 在位；把某项改标 `real` ⇒ 断言红（逐环节标注必须等于装配面声明）；
4. 交叉核对：包内 `llm.source` 与 019 运行记录的逐调用 `source` 一致；
5. 缺项即拒绝装配（拒绝语义不放宽）；包面仍是**五件套**（画像不入包）。
"""

import json
from pathlib import Path

import pytest
import yaml

from agents.pilot import backends as backends_module
from agents.pilot import package as package_module
from agents.pilot import stages as stages_module
from agents.pilot.package import PACKAGE_FILES, PackageError, load_manifest, verify_package
from agents.pilot.pilot import PilotInputs, precheck, run_pilot
from agents.pilot.stages import PILOT_STAGE_IDS
from core.billing.runlog import load_run

REPO_ROOT = Path(__file__).resolve().parents[2]
FORM = "shortdrama"
_LEGACY_PREFIX = "真实渠道"  # 未具备真实渠道期间的禁语（FR-011 / SC-004）
_CAPTURED: dict = {}


@pytest.fixture(scope="module")
def pilot_run(tmp_path_factory):
    """一次完整七环节运行（整包证据面共用；捕获 runtime 以便与树节点逐项对照）。"""
    tmp_path = tmp_path_factory.mktemp("evidence-pack")
    config_path = _demo_config(tmp_path)
    artifacts_root = tmp_path / "artifacts"
    original = stages_module.build_runtime

    def _capture(**kwargs):
        runtime = original(**kwargs)
        _CAPTURED["runtime"] = runtime
        return runtime

    stages_module.build_runtime = _capture
    try:
        result = run_pilot(
            form=FORM,
            config_path=config_path,
            inputs=_inputs(),
            data_dir=tmp_path / "pilot",
            artifacts_root=artifacts_root,
            run_id="run-evidence",
            clock=_fixed_clock(),
        )
    finally:
        stages_module.build_runtime = original
    assert result.record.status.value == "done"
    _CAPTURED.update(
        {
            "result": result,
            "config_path": config_path,
            "data_dir": tmp_path / "pilot",
            "artifacts_root": artifacts_root,
        }
    )
    return _CAPTURED


def _demo_config(tmp_path: Path) -> Path:
    """短剧演示档派生配置（只改排练档声明与账本根，同 `pilot_demo_config_path` 夹具口径）。"""
    text = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    assert "root: billing" in text
    text = text.replace("root: billing", f"root: {tmp_path / 'billing'}")
    for old, new in (
        ("target_duration_s: 120.0", "target_duration_s: 30.0"),
        ("script_target_minutes: 2.0", "script_target_minutes: 0.5"),
    ):
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "configs" / "shortdrama-demo.yaml"
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


def _fixed_clock():
    return lambda: "2026-01-01T00:00:00+00:00"


def _state(run) -> dict:
    return json.loads((run["result"].package_dir / "state.json").read_text(encoding="utf-8"))


def _pieces(run, *, state=None, manifest=None) -> package_module.PackagePieces:
    """包内五件的内存视图（用于复验/篡改用例）。"""
    package_dir = run["result"].package_dir

    def _load(name: str) -> dict:
        return json.loads((package_dir / name).read_text(encoding="utf-8"))

    return package_module.PackagePieces(
        manifest=_load("manifest.json") if manifest is None else manifest,
        reel=(package_dir / "reel.mp4").read_bytes(),
        products=_load("products.json"),
        cost=_load("cost.json"),
        state=_load("state.json") if state is None else state,
    )


class Test逐环节评估分量:
    def test_七环节分量非空且取自树节点原文(self, pilot_run):
        runtime = pilot_run["runtime"]
        state = _state(pilot_run)
        breakdown = state["eval_breakdown"]
        assert set(breakdown) == set(PILOT_STAGE_IDS)
        for stage_id in PILOT_STAGE_IDS:
            nodes = package_module.stage_nodes(pilot_run["result"].record, runtime, stage_id)
            assert nodes, stage_id
            expected = {node.node_id: node.eval_breakdown for node in nodes}
            assert breakdown[stage_id] == expected, stage_id  # 原文、不重算
            for node_id, fragments in breakdown[stage_id].items():
                assert fragments, f"{stage_id}:{node_id}"
                for key, value in fragments.items():
                    assert "@" in key, key  # 键 = evaluator_id@version
                    assert "score" in value

    def test_派生产物显式标注且不与缺项混同(self, pilot_run):
        state = _state(pilot_run)
        derived = state["derived_products"]
        # 分镜清单是另立内容寻址工件的**派生产物**：显式标 derived + "无节点分量"
        rows = derived["storyboard"]
        assert [row["kind"] for row in rows] == ["shotlist"]
        assert rows[0]["derived"] is True
        assert "无节点分量" in rows[0]["note"]
        # 其余产物都对应候选节点（故不在派生产物之列）
        runtime = pilot_run["runtime"]
        for stage_id in PILOT_STAGE_IDS:
            node_hashes = {
                node.artifact_hash
                for node in package_module.stage_nodes(
                    pilot_run["result"].record, runtime, stage_id
                )
            }
            derived_hashes = {row["content_hash"] for row in derived.get(stage_id, ())}
            for product in pilot_run["result"].record.stage(stage_id).products:
                if product.content_hash in derived_hashes:
                    assert product.content_hash not in node_hashes, f"{stage_id}/{product.kind}"
                else:
                    assert product.content_hash in node_hashes, f"{stage_id}/{product.kind}"

    def test_新字段无墙钟(self, pilot_run):
        payload = json.dumps(_state(pilot_run), ensure_ascii=False, sort_keys=True)
        for banned in ("created_at", "timestamp", "wall_clock", "started_at", "finished_at"):
            assert banned not in payload, banned

    def test_抹掉分量面即拒绝装配(self, pilot_run):
        state = _state(pilot_run)
        state["eval_breakdown"]["visual"] = {}
        with pytest.raises(PackageError, match="visual"):
            package_module._validate_pieces(_pieces(pilot_run, state=state))
        state["eval_breakdown"].pop("sound")
        with pytest.raises(PackageError, match="sound"):
            package_module._validate_pieces(_pieces(pilot_run, state=state))

    def test_分量面缺键即拒绝装配(self, pilot_run):
        state = _state(pilot_run)
        first = next(iter(state["eval_breakdown"]["dev"]))
        state["eval_breakdown"]["dev"][first] = {"not_an_evaluator_key": {"score": 1.0}}
        with pytest.raises(PackageError, match="evaluator_id@version"):
            package_module._validate_pieces(_pieces(pilot_run, state=state))


class Test逐环节标注:
    def test_逐环节标注与顶层汇总(self, pilot_run):
        manifest = load_manifest(pilot_run["result"].package_dir)
        channels = manifest["channels"]
        assert set(channels["stages"]) == set(PILOT_STAGE_IDS)
        assert set(channels["platform"]) == {"storyboard", "visual", "sound", "editing", "promo"}
        assert channels["llm"]["source"] == "simulated"
        assert channels["llm"]["adapter_ref"]
        assert channels["llm"]["profile_id"].startswith("llm_profiles@")
        assert "无交叉核对面" in channels["note"]  # 平台腿如实登记
        for row in manifest["stages"]:
            assert row["source"] in backends_module.BACKEND_SOURCES
            assert row["channel"]
            assert row["source"] == channels["stages"][row["stage_id"]]["source"]
        assert manifest["work_kind"] == "rehearsal"

    def test_预检报告同步逐环节标注(self, pilot_run):
        report = precheck(
            form=FORM,
            config_path=pilot_run["config_path"],
            inputs=_inputs(),
            data_dir=pilot_run["data_dir"],
        )
        stages = report["pilot_backend"]["stages"]
        assert set(stages) == set(PILOT_STAGE_IDS)
        assert set(stages.values()) == {"simulated"}
        assert report["pilot_backend"]["credentials_checked"] is False

    def test_全模拟无真实渠道表述且诚实标注在位(self, pilot_run):
        package_dir = pilot_run["result"].package_dir
        manifest = load_manifest(package_dir)
        assert {row["source"] for row in manifest["stages"]} == {"simulated"}
        assert package_module.SIMULATED_NOTE in manifest["note"]
        banned = f"{_LEGACY_PREFIX}全链路已跑通"
        for name in PACKAGE_FILES:
            if not name.endswith(".json"):
                continue
            text = (package_dir / name).read_text(encoding="utf-8")
            assert banned not in text, name
            assert "全链路已跑通" not in text, name

    def test_改标真实即红(self, pilot_run):
        """模拟被标为真实恒 0：逐环节标注必须等于装配面声明（改写即拒）。"""
        manifest = load_manifest(pilot_run["result"].package_dir)
        manifest["stages"][1]["source"] = "real"  # script 伪标真实
        with pytest.raises(PackageError, match="装配面声明"):
            package_module._validate_pieces(_pieces(pilot_run, manifest=manifest))

    def test_LLM_腿与_019_运行记录交叉核对(self, pilot_run):
        """包内 `llm.source` 与 019 运行记录的逐调用 `source` 一致。"""
        manifest = load_manifest(pilot_run["result"].package_dir)
        root = pilot_run["config_path"].parent.parent / "billing"
        files = sorted((root / "llm" / "runs").glob("*.json"))
        assert files  # 本次运行确有运行记录
        sources = set()
        for path in files:
            payload = load_run(path.stem, channel_id="llm", root=root)
            sources |= {entry["source"] for entry in payload["entries"]}
        assert sources == {manifest["channels"]["llm"]["source"]}
        assert (
            yaml.safe_load(pilot_run["config_path"].read_text("utf-8"))["pilot"]["llm_backend"]
            == "mock"
        )

    def test_非模拟后端分层标注可机读(self):
        """声明 `llm_backend: http` 而平台仍模拟 ⇒ llm 为 real、平台五环节为 simulated。

        装配期会因缺凭证拒绝（不假装验过凭证），故此处按**装配面声明的归一化**口径机检分层。
        """
        assert backends_module.normalize_source("http") == "real"
        assert backends_module.normalize_source("simulated") == "simulated"
        assert backends_module.normalize_source("mock") == "simulated"
        platform = ("storyboard", "visual", "sound", "editing", "promo")
        rows = backends_module.stage_channels(
            _StubBackends({"llm": "http", **{slot: "simulated" for slot in platform}})
        )
        assert rows["script"]["source"] == "real"
        assert rows["dev"]["source"] == "real"
        assert {rows[stage]["source"] for stage in platform} == {"simulated"}
        assert rows["script"]["channel"] == "llm_profiles@abcdef123456"  # 档案引用（LLM 槽位）

    def test_保留值_fallback_原样标注(self):
        """019 的保留值 `fallback` 出现即原样标注，**不得**折叠进 real/simulated。"""
        assert backends_module.normalize_source("fallback") == "fallback"
        assert "fallback" in backends_module.BACKEND_SOURCES
        with pytest.raises(backends_module.BackendAssemblyError):
            backends_module.normalize_source("未知后端")


class _StubBackends:
    """装配面声明的只读视图（分层标注机检用；不构造任何后端实现）。"""

    def __init__(self, resolved: dict) -> None:
        self.resolved = resolved
        self.gateway = _StubGateway()


class _StubGateway:
    def profile_snapshot(self):
        return type("_Snapshot", (), {"ref": "llm_profiles@abcdef123456"})()


class Test缺项即拒绝装配:
    def test_缺顶层汇总即拒绝(self, pilot_run):
        manifest = load_manifest(pilot_run["result"].package_dir)
        manifest.pop("channels")
        with pytest.raises(PackageError, match="channels"):
            package_module._validate_pieces(_pieces(pilot_run, manifest=manifest))

    def test_缺某环节标注即拒绝(self, pilot_run):
        manifest = load_manifest(pilot_run["result"].package_dir)
        manifest["stages"][3]["source"] = ""
        with pytest.raises(PackageError, match="source/channel"):
            package_module._validate_pieces(_pieces(pilot_run, manifest=manifest))

    def test_复验发现包内证据被改写(self, pilot_run, tmp_path):
        """改写包内标注/分量后再复验 ⇒ 拒绝（篡改面 100% 被拒，不产误导性清单）。"""
        tampered = tmp_path / "tampered-labels"
        tampered.mkdir(parents=True, exist_ok=True)
        for name in PACKAGE_FILES:
            (tampered / name).write_bytes((pilot_run["result"].package_dir / name).read_bytes())
        manifest = json.loads((tampered / "manifest.json").read_text(encoding="utf-8"))
        manifest["stages"][2]["source"] = "real"
        (tampered / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(PackageError):
            verify_package(tampered)

    def test_画像不入包(self, pilot_run):
        assert sorted(path.name for path in pilot_run["result"].package_dir.iterdir()) == sorted(
            PACKAGE_FILES
        )
        assert verify_package(pilot_run["result"].package_dir)["files"] == list(PACKAGE_FILES)
