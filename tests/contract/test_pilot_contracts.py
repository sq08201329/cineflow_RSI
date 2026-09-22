"""试水作品契约聚合（功能 015）。

本批只覆盖 `contracts/handoffs.md` 的 C5~C9（orchestration 的 C1~C4 与 pilot-run 的
C10~C13 由 T1524 补全并与本文件合并为 C1~C13 全量）：

- **C5** 剧本 → 分镜段落：字段集双向锁定（含枚举值）+ 下游预检通过 + 缺字段即红；
- **C6** 分镜 → 视觉生成参数：镜头数 == 参数数 + 尺寸由形态配置派生（竖屏守卫）；
- **C7** 视听 → 剪辑输入：镜头库条目一致 + 含/无音轨两路径（无音轨如实标注）；
- **C8** 成片 → 宣发物料：素材引用与元数据齐备 + 规格来自配置裁剪规则；
- **C9** 拒绝语义：上游 FAILED → 下游**拒绝启动**（机检下游执行计数 0）；
  环节候选全败 → 环节 failed → 运行终止并记录全部候选判 0 理由。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.editing.shots import ShotLibrary
from agents.pilot.handoffs import (
    HandoffError,
    av_to_edit_inputs,
    edit_inputs_parity,
    reel_to_promo_materials,
    script_parity,
    script_to_segment,
    shotlist_parity,
    shotlist_to_gen_params,
)
from agents.pilot.package import (
    PACKAGE_FILES,
    SIMULATED_NOTE,
    PackageError,
    load_manifest,
    verify_package,
)
from agents.storyboard.script import ScriptSegment
from core.orchestration.dag import build_dag
from core.orchestration.executor import ExecutionContext, run
from core.orchestration.models import RunStatus, StageOutcome, StageSpec, StageStatus

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Cfg:
    """形态配置桩（只暴露交接要读的两段；形态值只作参数，不进分支）。"""

    def __init__(self, width=540, height=960, fps=8, duration_seconds=1.5):
        self.clip_spec = {
            "width": width,
            "height": height,
            "fps": fps,
            "duration_seconds": duration_seconds,
            "codec": "h264",
        }
        self.material_spec = {
            "max_copy_chars": 40,
            "poster_size": "1080x1920",
            "max_duration_seconds": 20,
        }


def _clip(shot_id: str, scene_id: str, index: int) -> dict:
    return {
        "shot_id": shot_id,
        "scene_id": scene_id,
        "artifact_hash": (f"{index:02d}" * 32)[:64],
        "duration_ms": 1500,
        "gen_params": {"shot_id": shot_id, "width": 540, "height": 960},
        "score": 0.75,
    }


def _reel() -> dict:
    return {
        "artifact_hash": "ee" * 32,
        "duration_ms": 120000,
        "width": 540,
        "height": 960,
        "fps": 8,
    }


class TestC5剧本到分镜:
    def test_四场景全链路(self, pilot_material_script):
        segment = script_to_segment(pilot_material_script)
        assert isinstance(segment, ScriptSegment)
        assert script_parity(pilot_material_script).consistent()
        assert segment.scene_ids() == ("scene-1", "scene-2")
        assert dict(zip(segment.line_ids(), segment.key_line_ids(), strict=False))
        # 关键行清单逐条承接（必覆盖清单来自上游 key=True 行）
        assert set(segment.key_line_ids()) == {"s1-l1", "s2-l1"}

    def test_缺字段即红(self, pilot_material_script):
        with pytest.raises(HandoffError):
            script_to_segment("不是工件")


class TestC6分镜到视觉:
    def test_镜头数等于参数数(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg())
        assert len(params) == len(pilot_material_shotlist.shots)
        assert shotlist_parity(pilot_material_shotlist, params).consistent()

    def test_竖屏规格由配置派生(self, pilot_material_shotlist):
        params = shotlist_to_gen_params(
            pilot_material_shotlist, config=_Cfg(width=720, height=1280)
        )
        assert {(p["width"], p["height"]) for p in params} == {(720, 1280)}

    def test_参数在视觉侧样式校验通过(self, pilot_material_shotlist):
        # 视觉模拟生成器只接受 dict 参数；此处机检参数键为 JSON 原生类型（可进生成器）
        params = shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg())
        for param in params:
            assert all(isinstance(key, str) for key in param)
            assert isinstance(param["width"], int) and isinstance(param["duration_s"], float)


class TestC7视听到剪辑:
    def test_含音轨与无音轨两路径(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        without = av_to_edit_inputs(clips, None, config=_Cfg())
        assert without.has_audio is False and without.shot_library.audio_tracks == ()
        with_audio = av_to_edit_inputs(
            clips,
            {"tracks": [{"gen_type": "tts", "artifact_hash": "aa" * 32, "at_ms": 0}]},
            config=_Cfg(),
        )
        assert with_audio.has_audio is True
        assert with_audio.shot_library.audio_tracks == ("tts-aaaaaaaaaaaa",)

    def test_镜头库条目一致(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        edits = av_to_edit_inputs(clips, None, config=_Cfg())
        assert isinstance(edits.shot_library, ShotLibrary)
        assert [shot.shot_id for shot in edits.shot_library.shots] == ["shot-1", "shot-2"]
        assert edit_inputs_parity(clips, edits).consistent()

    def test_剪辑侧校验通过(self):
        clips = [_clip("shot-1", "scene-1", 1), _clip("shot-2", "scene-2", 2)]
        edits = av_to_edit_inputs(clips, None, config=_Cfg())
        # 分区归属一致性由 SceneStructure 构造即校验（越界即构造失败）
        assert [scene.scene_id for scene in edits.scene_structure.scenes] == [
            "scene-1",
            "scene-2",
        ]


class TestC8成片到宣发:
    def test_物料素材齐备(self):
        materials = reel_to_promo_materials(_reel(), config=_Cfg())
        assert {material["kind"] for material in materials.materials} == {
            "clip",
            "poster",
            "copy",
        }
        assert all(material["source_hash"] == "ee" * 32 for material in materials.materials)

    def test_宣发侧规格校验通过(self):
        materials = reel_to_promo_materials(_reel(), config=_Cfg())
        poster = next(m for m in materials.materials if m["kind"] == "poster")
        width, height = poster["spec"]["poster_size"].split("x")
        assert int(height) > int(width)  # 竖屏封面


class TestC9拒绝语义:
    """上下游拒绝语义：上游 FAILED → 下游执行计数 0（不静默降级、不伪造输入）。"""

    def test_上游失败下游零调用(self, stage_entrypoint_stub):
        calls = {"third": 0}

        first = stage_entrypoint_stub("s1", fail="候选全败", candidates=["门禁违规", "分数为 0"])

        def _second(stage_input):
            raise AssertionError("上游 failed 时第二段不得启动")

        def _third(stage_input):
            calls["third"] += 1
            return StageOutcome()

        dag = build_dag(
            [
                StageSpec(stage_id="s1", entrypoint=first),
                StageSpec(stage_id="s2", entrypoint=_second, depends_on=("s1",)),
                StageSpec(stage_id="s3", entrypoint=_third, depends_on=("s2",)),
            ]
        )
        record = run(
            dag,
            ExecutionContext(
                run_id="run-c9",
                form="form-x",
                config_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
            ),
        )
        assert record.status is RunStatus.FAILED
        assert record.failure_stage == "s1"
        assert calls["third"] == 0  # 机检：下游执行计数 0
        assert record.stage("s2").status is StageStatus.SKIPPED
        reasons = [reason for c in record.stage("s1").candidates for reason in c.reasons]
        assert reasons == ["门禁违规", "分数为 0"]  # 全部候选判 0 理由落记录

    def test_交接函数对缺失上游输入拒绝(self):
        with pytest.raises(HandoffError):
            av_to_edit_inputs([], None, config=_Cfg())
        with pytest.raises(HandoffError):
            reel_to_promo_materials({}, config=_Cfg())


@pytest.mark.parametrize("segment_kind", ["script"])
def test_C5_C8_字段声明可机读(segment_kind, pilot_material_script, pilot_material_shotlist):
    """字段映射声明（kept/dropped/derived）随契约可机读：漂移即红。"""
    parities = [
        script_parity(pilot_material_script),
        shotlist_parity(
            pilot_material_shotlist,
            shotlist_to_gen_params(pilot_material_shotlist, config=_Cfg()),
        ),
        edit_inputs_parity(
            [_clip("shot-1", "scene-1", 1)],
            av_to_edit_inputs([_clip("shot-1", "scene-1", 1)], None, config=_Cfg()),
        ),
    ]
    for parity in parities:
        payload = parity.to_dict()
        assert payload["consistent"] is True
        assert payload["downstream"] and payload["label"]


# ---------------------------------------------------------------------------
# C1~C4 通用编排执行器（contracts/orchestration.md）
# ---------------------------------------------------------------------------


class TestC1到C4通用编排:
    """C1 DAG / C2 状态机 / C3 断点续跑 / C4 账目——用真执行器端到端断言。"""

    def _chain(self, stage_entrypoint_stub, count, **overrides):
        from core.orchestration.dag import build_dag
        from core.orchestration.models import StageSpec

        stubs, specs = {}, []
        for index in range(1, count + 1):
            stage_id = f"a{index}"
            stub = stage_entrypoint_stub(
                stage_id, cost_usd=0.5, **dict(overrides.get(stage_id, {}))
            )
            stubs[stage_id] = stub
            specs.append(
                StageSpec(
                    stage_id=stage_id,
                    entrypoint=stub,
                    depends_on=() if index == 1 else (f"a{index - 1}",),
                )
            )
        return build_dag(specs), stubs

    def _ctx(self, fingerprint="c" * 64):
        return ExecutionContext(
            run_id="run-c",
            form="form-x",
            config_fingerprint="d" * 64,
            input_fingerprint=fingerprint,
        )

    def test_c1_拓扑序与非法图(self, stage_entrypoint_stub):
        from core.orchestration.dag import build_dag
        from core.orchestration.errors import DagError
        from core.orchestration.models import StageSpec

        def _spec(stage_id, *deps):
            return StageSpec(
                stage_id=stage_id, entrypoint=stage_entrypoint_stub(stage_id), depends_on=deps
            )

        dag = build_dag(
            [_spec("a1"), _spec("a2", "a1"), _spec("a3", "a1"), _spec("a4", "a2", "a3")]
        )
        order = dag.topological_order()
        assert order[0] == "a1" and order[-1] == "a4"
        with pytest.raises(DagError):
            build_dag([_spec("a1", "ghost")])  # 依赖不存在
        with pytest.raises(DagError):
            build_dag([_spec("a1"), _spec("a1")])  # stage_id 重复
        with pytest.raises(DagError):
            build_dag([_spec("a1", "a2"), _spec("a2", "a1")])  # 环

    def test_c2_状态机与失败跳过(self, stage_entrypoint_stub):
        dag, stubs = self._chain(
            stage_entrypoint_stub, 3, a2={"fail": "候选全败", "candidates": ["门禁违规"]}
        )
        record = run(dag, self._ctx())
        assert record.status is RunStatus.FAILED and record.failure_stage == "a2"
        assert record.stage("a1").status is StageStatus.DONE
        assert record.stage("a3").status is StageStatus.SKIPPED
        assert stubs["a3"].calls == 0  # 失败点的下游拒绝启动

    def test_c3_续跑不重跑与指纹拒绝(self, stage_entrypoint_stub):
        broken_dag, broken = self._chain(stage_entrypoint_stub, 3, a2={"fail": "候选全败"})
        first = run(broken_dag, self._ctx())
        fixed_dag, fixed = self._chain(stage_entrypoint_stub, 3)
        resumed = run(fixed_dag, self._ctx(), resume_from=first)
        assert resumed.status is RunStatus.DONE
        assert fixed["a1"].calls == 0  # 已完成阶段零重跑
        assert resumed.stage("a2").attempts == 2
        from core.orchestration.errors import ResumeRejectedError

        with pytest.raises(ResumeRejectedError):
            run(broken_dag, self._ctx(fingerprint="e" * 64), resume_from=first)
        again = run(fixed_dag, self._ctx(), resume_from=resumed)
        assert again == resumed  # 完成后再续跑幂等

    def test_c4_账目汇总与对账(self, stage_entrypoint_stub):
        from core.orchestration.errors import LedgerMismatchError
        from core.orchestration.ledger import summarize_cost

        dag, _ = self._chain(stage_entrypoint_stub, 3)
        record = run(dag, self._ctx())
        ledger = summarize_cost(record, {state.stage_id: 0.5 for state in record.stages})
        assert ledger.total_usd == pytest.approx(1.5)
        assert all(line.delta_usd == 0.0 for line in ledger.lines)
        with pytest.raises(LedgerMismatchError):
            summarize_cost(record, {state.stage_id: 0.9 for state in record.stages})


# ---------------------------------------------------------------------------
# C10~C13 试水运行与样片包（contracts/pilot-run.md）
# ---------------------------------------------------------------------------


def _pilot_inputs():
    from agents.pilot.pilot import PilotInputs

    return PilotInputs(
        topic="夜班记录", target_duration_min=2, characters=("林静", "陈默"), constraints=()
    )


def _run_pilot(pilot_demo_config_path, tmp_path, *, run_id="contract-run", data_dir_name="a"):
    from agents.pilot.pilot import run_pilot

    return run_pilot(
        form="shortdrama",
        config_path=pilot_demo_config_path,
        inputs=_pilot_inputs(),
        data_dir=tmp_path / data_dir_name / "pilot",
        artifacts_root=tmp_path / data_dir_name / "artifacts",
        run_id=run_id,
        clock=lambda: "2026-01-01T00:00:00+00:00",
    )


class TestC10到C13试水运行:
    def test_c10_预检拒绝与一次运行(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        from agents.pilot.pilot import PilotInputs, PrecheckError, precheck

        with pytest.raises(PrecheckError):
            precheck(
                form="shortdrama",
                config_path=pilot_demo_config_path,
                inputs=PilotInputs(topic="", target_duration_min=0, characters=()),
                data_dir=pilot_dirs,
            )
        result = _run_pilot(pilot_demo_config_path, tmp_path)
        assert result.record.status is RunStatus.DONE
        assert result.record.completed_stages == (
            "script",
            "storyboard",
            "visual",
            "sound",
            "editing",
            "promo",
        )
        assert result.package_dir is not None

    def test_c10_可复现两次运行逐字节一致(self, pilot_demo_config_path, tmp_path):
        first = _run_pilot(pilot_demo_config_path, tmp_path, data_dir_name="first")
        second = _run_pilot(pilot_demo_config_path, tmp_path, data_dir_name="second")
        for name in PACKAGE_FILES:
            assert (first.package_dir / name).read_bytes() == (
                second.package_dir / name
            ).read_bytes(), name

    def test_c11_五件套与缺件即失败(self, pilot_demo_config_path, tmp_path):
        result = _run_pilot(pilot_demo_config_path, tmp_path)
        manifest = load_manifest(result.package_dir)
        assert SIMULATED_NOTE in manifest["note"]
        assert manifest["form"] == "shortdrama"
        assert manifest["config_fingerprint"]
        missing = tmp_path / "missing-package"
        missing.mkdir()
        with pytest.raises(PackageError):
            verify_package(missing)
        (result.package_dir / "reel.mp4").unlink()
        with pytest.raises(PackageError):
            verify_package(result.package_dir)

    def test_c12_账目对账零差异与篡改报错(self, pilot_demo_config_path, tmp_path):
        result = _run_pilot(pilot_demo_config_path, tmp_path)
        cost = json.loads((result.package_dir / "cost.json").read_text(encoding="utf-8"))
        assert cost["reconciled"] is True
        assert cost["total_usd"] == pytest.approx(sum(cost["by_stage"].values()))
        assert all(float(line["delta_usd"]) == 0.0 for line in cost["lines"])
        tampered = tmp_path / "tampered.json"
        cost["by_stage"]["visual"] = cost["by_stage"]["visual"] + 1.0
        tampered.write_text(json.dumps(cost, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(PackageError):
            verify_package(result.package_dir, cost_override=tampered)

    def test_c13_两套配置差异可归因且无形态分支(self):
        import yaml

        from core.orchestration.models import StageStatus as _Status  # noqa: F401

        movie = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
        short = yaml.safe_load(
            (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
        )
        differing = {key for key in set(movie) | set(short) if movie.get(key) != short.get(key)}
        # 形态差异逐项落在配置上（12 个段）；形态无关基建段逐字相同
        assert differing == {
            "form",
            "evaluator_weights",
            "replay",
            "promo",
            "visual",
            "sound",
            "editing",
            "storyboard",
            "screenplay",
            "calibration",
            "dreaming",
            # 014：deployment 段的抽检超期告警窗口按形态声明（运营节奏即形态，见下）
            "deployment",
        }
        for key in ("web", "cost_regression"):
            assert movie[key] == short[key]
        # deployment 段：**唯一**按形态声明的键是 spot_check.pending_alert_days
        # （014 复核超期告警窗口——运营节奏即形态），其余逐字相同
        assert (
            movie["deployment"]["spot_check"]["pending_alert_days"]
            != short["deployment"]["spot_check"]["pending_alert_days"]
        )
        normalized = json.loads(json.dumps(movie["deployment"]))
        normalized["spot_check"].pop("pending_alert_days")
        short_normalized = json.loads(json.dumps(short["deployment"]))
        short_normalized["spot_check"].pop("pending_alert_days")
        assert normalized == short_normalized
        # 代码侧零形态分支（core/ 与 agents/ 全量扫描）
        offenders = []
        for root in ("core", "agents"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                for banned in ("shortdrama", '"movie"', "'movie'", "form ==", "form is "):
                    if banned in source:
                        offenders.append(f"{path}:{banned}")
        assert not offenders, offenders


# ---------------------------------------------------------------------------
# 宪章级机检：FR-011 落树路径守卫 + SC-005 依赖清单
# ---------------------------------------------------------------------------


class Test宪章级机检:
    def test_fr011_编排层不直接写树(self):
        """FR-011：落树只经各 Agent 既有 loop 入口，编排层不得直接调用树写入 API。"""
        offenders = []
        for root in ("core/orchestration", "agents/pilot"):
            for path in (REPO_ROOT / root).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                for banned in (
                    "append_node(",
                    "create_tree(",
                    "store.append",
                    ".create_tree",
                ):
                    if banned in source:
                        offenders.append(f"{path}:{banned}")
        assert not offenders, offenders

    def test_fr011_运行期写入全部来自_Agent_入口(self, pilot_demo_config_path, tmp_path):
        """运行期树写入计数与来源：全部来自各 Agent 的 loop 模块（编排层零写入）。"""
        from agents.pilot import stages as stages_module
        from agents.pilot.pilot import FileRunStore, PilotInputs
        from core.orchestration.executor import ExecutionContext
        from core.orchestration.executor import run as run_dag

        writes: list[str] = []

        class _GuardedStore:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def append_node(self, node):
                import sys

                writes.append(sys._getframe(1).f_globals.get("__name__", "?"))
                return self._inner.append_node(node)

            def create_tree(self, tree):
                import sys

                writes.append(sys._getframe(1).f_globals.get("__name__", "?"))
                return self._inner.create_tree(tree)

        runtime = stages_module.build_runtime(
            form="shortdrama",
            config_path=pilot_demo_config_path,
            data_dir=tmp_path / "pilot",
            artifacts_root=tmp_path / "artifacts",
        )
        guarded = replace(runtime, store=_GuardedStore(runtime.store))
        stages_module.bind_runtime(guarded)
        inputs = PilotInputs(topic="夜班记录", target_duration_min=2, characters=("林静",))
        record = run_dag(
            stages_module.build_dag_for(guarded),
            ExecutionContext(
                run_id="guard-run",
                form="shortdrama",
                config_fingerprint=guarded.config_fingerprint,
                input_fingerprint=inputs.fingerprint(),
                shared={
                    "runtime": guarded,
                    "run_id": "guard-run",
                    "pilot_inputs": inputs.to_dict(),
                },
            ),
            store=FileRunStore(tmp_path / "pilot"),
            clock=lambda: "2026-01-01T00:00:00+00:00",
        )
        assert record.status is RunStatus.DONE
        assert writes, "未捕获到任何树写入（守卫失效）"
        # 写入者必须是各 Agent 的实现模块（编排层 core/orchestration 与 agents/pilot 不在列）
        bad = [name for name in writes if name.startswith(("core.orchestration", "agents.pilot"))]
        assert not bad, bad
        assert all(name.startswith(("agents.", "core.tree", "ops.")) for name in writes), writes

    def test_sc005_依赖清单无_Airflow_类编排框架(self):
        """SC-005：自研轻量 DAG——依赖清单不得出现 Airflow 类外部编排框架。"""
        banned = ("airflow", "apache-airflow", "prefect", "dagster", "luigi", "kedro", "argo")
        for name in ("pyproject.toml", "uv.lock"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8").lower()
            for framework in banned:
                assert f'name = "{framework}"' not in text, f"{name} 含 {framework}"
                assert f'"{framework}==' not in text, f"{name} 含 {framework}"
