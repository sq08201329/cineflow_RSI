"""功能 018 阶段 5（US2 / 契约 C5~C7）：`dev` → 剧本的**字段级交接声明**。

本文件把"下游读取集 == `renames`(上游 − 丢弃) ∪ 运行级 ∪ 派生"这条守恒等式、**三类来源**
（承接含改名 / 运行级 / 派生）、**独立丢弃集**、**取数依据**（第三类登记项）与**两层断言**
钉成机检：

1. 声明形状——`DevScriptHandoff(mode, reads, renames, dropped, derived, sources)`：
   `set(reads) == SCRIPT_INPUT_READS`（合计且每键恰一类）；`dropped` 与 `reads` **并列**（不是
   第四类）；`sources == {entries, production_marks}` 既不进 `reads` 也不进 `dropped`；
2. 读取集**源码扫描式锁定**——两式 `inputs[...]` / `inputs.get(...)` 逐点收集，新增读取点
   未登记即红（双向绑定：声明了却没人读也红）；
3. **改名承接**——`topic ← genre` 登记在 `renames`（`genre` **不进**丢弃集）；漏登承接类键、
   或写成"丢 `genre` + 派生 `topic`"即红；
4. 取数入口——`production_marks` **恰好一条**且指向组内条目；缺失/越界（多条）/悬空/要点缺失
   四类拒绝并**点名**（不静默取第一条、不伪装成"选题为空"）；
5. 两条来源——有 `dev` ⇒ `dev_script_handoff`；无 `dev`（既有短剧链）⇒ `run_level_pilot_inputs`
   四键全运行级，生效 `mode` 随机读可见（**禁止静默择一**）；
6. 两侧同步——上游导出面增删字段或 `SCHEMA_VERSION` 升版而未同步本侧声明 ⇒ 断言红；
7. `dev` 失败 ⇒ 下游零调用、如实 `skipped`（不以上一轮残留产物顶替）；
8. 两层断言各自成立——同名字段子集层 `FieldParity.consistent()` 绿 ∧ 承接/运行级层 C6 ①~⑥ 绿；
   改名承接层**不复用** `consistent()`（把改名结果塞进同名声明的三式必然不成立）。
"""

from dataclasses import replace
from pathlib import Path

import pytest

from agents.dev import artifact as dev_artifact
from agents.dev import export_slate as export_slate_module
from agents.dev.artifact import TopicSlate, simulated_signal_sources
from agents.pilot import handoffs
from agents.pilot import stages as stages_module
from agents.pilot.pilot import PilotInputs, run_pilot
from agents.screenplay.loop import SCRIPT_INPUT_READS
from core.orchestration.models import RunStatus, StageInput, StageStatus

FORM = "shortdrama"
REPO_ROOT = Path(__file__).resolve().parents[2]
PILOT_INPUTS = {
    "topic": "夜班记录",
    "target_duration_min": 0.5,
    "characters": ["林静", "陈默"],
    "constraints": ["单场景为主"],
    "genre_bounds": ["悬疑", "夜戏"],
    "audience": "都市女性",
}


def _entry(
    direction_id: str, *, in_production: bool = False, genre: str = "医疗悬疑", **over
) -> dict:
    payload = {
        "direction_id": direction_id,
        "rationale": f"{direction_id} 的立项论证要点",
        "eval_components": {"proxy.box_office_prior": 0.8},
        "genre": genre,
        "constraints": ["夜戏", "低成本"],
        "characters": ["值班护士 林静"],
        "in_production": in_production,
    }
    payload.update(over)
    return payload


def _export(entries: list[dict], marks: tuple[str, ...] = ()) -> dict:
    """夹具立项组合工件 → 017 导出面（模拟数据源标注 + 组合级指向）。"""
    slate = TopicSlate(
        entries=entries,
        signal_sources=simulated_signal_sources({"baseline_usd_million": 10.0}),
        production_marks=marks,
    )
    return export_slate_module.export_slate(slate)


def _marked_export() -> dict:
    return _export([_entry("dir-a", in_production=True), _entry("dir-b")], ("dir-a",))


def _declaration(**over) -> handoffs.DevScriptHandoff:
    return replace(handoffs.dev_script_handoff_declaration(), **over)


class Test声明形状:
    def test_读取集三类且每键恰一类(self):
        declaration = handoffs.dev_script_handoff_declaration()
        assert set(declaration.reads) == set(SCRIPT_INPUT_READS)
        assert set(declaration.reads.values()) <= set(handoffs.READ_CLASSES)
        assert declaration.reads["target_duration_min"] == "运行级"
        assert declaration.violations() == []
        assert declaration.assert_consistent() is None

    def test_丢弃集与读取集并列且取数依据单列(self):
        declaration = handoffs.dev_script_handoff_declaration()
        # `dropped` 是独立集合（不是 reads 的第四类），取数依据既不进 reads 也不进 dropped
        assert "dropped" not in declaration.reads
        assert "dropped" not in set(declaration.reads.values())
        assert declaration.sources == handoffs.SELECTION_SOURCES
        assert not declaration.sources & set(declaration.reads)
        assert not declaration.sources & declaration.dropped
        assert declaration.dropped == handoffs.DEV_SCRIPT_DROPPED

    def test_未声明读取键即拒绝并点名(self):
        broken = _declaration(reads={**handoffs.DEV_SCRIPT_READS, "unexpected": "派生"})
        violations = broken.violations()
        assert any("unexpected" in item for item in violations)
        with pytest.raises(handoffs.HandoffError, match="unexpected"):
            broken.assert_consistent()

    def test_缺类或多类即红(self):
        dropped_key = _declaration(
            reads={k: v for k, v in handoffs.DEV_SCRIPT_READS.items() if k != "topic"}
        )
        assert any("topic" in item for item in dropped_key.violations())
        wrong_class = _declaration(reads={**handoffs.DEV_SCRIPT_READS, "characters": "第四类"})
        assert any("第四类" in item or "characters" in item for item in wrong_class.violations())


class Test读取集锁定:
    def test_两式扫描与声明双向绑定(self):
        assert handoffs.read_set_mismatches() == []
        assert set(handoffs.scanned_read_keys()) == set(SCRIPT_INPUT_READS)

    def test_新增读取点未登记即红(self, tmp_path):
        """在副本里注入第三个读取点 ⇒ 扫描立刻点名该键（不得靠人记得）。"""
        root = _copied_scan_domain(tmp_path)
        target = root / handoffs.READ_SCAN_FILES[0]
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n\ndef _injected(inputs):\n    return inputs.get('new_read')\n",
            encoding="utf-8",
        )
        mismatches = handoffs.read_set_mismatches(root)
        assert any("new_read" in item for item in mismatches)

    def test_读取点消失即红(self, tmp_path):
        """声明了却没人读 ⇒ 同样红（声明不得凭空多出读取键）。"""
        root = _copied_scan_domain(tmp_path)
        (root / handoffs.READ_SCAN_FILES[0]).write_text("", encoding="utf-8")
        mismatches = handoffs.read_set_mismatches(root)
        assert mismatches
        assert any("target_duration_min" in item for item in mismatches)


class Test改名承接与丢弃:
    def test_topic_是_genre_的改名承接(self):
        declaration = handoffs.dev_script_handoff_declaration()
        assert declaration.renames["topic"] == "genre"
        assert declaration.renames["constraints"] == "constraints"
        assert declaration.renames["characters"] == "characters"
        assert declaration.reads["topic"] == "承接"
        assert "genre" not in declaration.dropped  # 承接 ≠ 丢弃

    def test_承接类键漏登即红(self):
        broken = _declaration(renames={"constraints": "constraints"})
        violations = broken.violations()
        assert any("topic" in item for item in violations)

    def test_写成丢弃加派生即红(self):
        """`genre` 进丢弃集、`topic` 改成派生 ⇒ 守恒等式仍成立，但要点被"丢"了 ⇒ 必须红。"""
        broken = _declaration(
            reads={**handoffs.DEV_SCRIPT_READS, "topic": "派生"},
            renames={"constraints": "constraints", "characters": "characters"},
            dropped=handoffs.DEV_SCRIPT_DROPPED | {"genre"},
            derived=frozenset({"topic"}),
        )
        assert any("genre" in item for item in broken.violations())
        with pytest.raises(handoffs.HandoffError):
            broken.assert_consistent()

    def test_派生不得占用上游与承接目标键名(self):
        # 派生键占用**承接目标**键名（topic 仍在 renames 值集里）⇒ ④ 红
        taken_target = _declaration(
            reads={**handoffs.DEV_SCRIPT_READS, "topic": "派生"},
            derived=frozenset({"topic"}),
        )
        assert any("topic" in item for item in taken_target.violations())
        # 派生键占用**上游要点**键名（genre）⇒ ④ 红
        taken_upstream = _declaration(
            reads={**handoffs.DEV_SCRIPT_READS, "target_duration_min": "派生"},
            derived=frozenset({"genre"}),
        )
        assert any("genre" in item for item in taken_upstream.violations())


class Test取数入口:
    def test_合规组合取到被标记条目的要点(self):
        view = handoffs.dev_to_script_inputs(_marked_export(), PILOT_INPUTS)
        assert view.mode == handoffs.HANDOFF_MODE_DEV
        assert set(view.inputs) == set(SCRIPT_INPUT_READS)
        entry = _marked_export()["entries"][0]
        assert view.inputs["topic"] == entry["genre"]  # 改名承接
        assert view.inputs["constraints"] == entry["constraints"]
        assert view.inputs["characters"] == entry["characters"]
        assert view.inputs["target_duration_min"] == PILOT_INPUTS["target_duration_min"]
        assert view.selected_entry == "dir-a"
        # 逐字段可追溯（承接的上游字段名 / 运行级键名）
        assert view.trace["topic"]["source"] == "genre"
        assert view.trace["topic"]["entry"] == "dir-a"
        assert view.trace["target_duration_min"]["class"] == "运行级"

    def test_标记缺失即拒绝(self):
        with pytest.raises(handoffs.HandoffError, match="缺失"):
            handoffs.dev_to_script_inputs(_export([_entry("dir-a")]), PILOT_INPUTS)

    def test_标记越界即拒绝并点名(self):
        export = _export(
            [_entry("dir-a", in_production=True), _entry("dir-b", in_production=True)],
            ("dir-a", "dir-b"),
        )
        with pytest.raises(handoffs.HandoffError) as excinfo:
            handoffs.dev_to_script_inputs(export, PILOT_INPUTS)
        message = str(excinfo.value)
        assert "dir-a" in message and "dir-b" in message
        assert "恰好一条" in message

    def test_标记悬空即拒绝并点名(self):
        with pytest.raises(handoffs.HandoffError) as excinfo:
            handoffs.dev_to_script_inputs(_export([_entry("dir-a")], ("dir-ghost",)), PILOT_INPUTS)
        assert "dir-ghost" in str(excinfo.value)
        assert "悬空" in str(excinfo.value)

    def test_要点为空即拒绝并点名(self):
        """承接不解除要点校验：`genre` 空 ⇒ 拒绝（不许等到下游拿空 `topic`）。"""
        export = _export([_entry("dir-a", in_production=True, genre="")])
        with pytest.raises(handoffs.HandoffError) as excinfo:
            handoffs.dev_to_script_inputs(export, PILOT_INPUTS)
        assert "genre" in str(excinfo.value)

    def test_运行级输入缺项即拒绝(self):
        missing = {
            key: value for key, value in PILOT_INPUTS.items() if key != "target_duration_min"
        }
        with pytest.raises(handoffs.HandoffError, match="target_duration_min"):
            handoffs.dev_to_script_inputs(_marked_export(), missing)


class Test两条来源:
    def test_有无dev的单一判定(self):
        assert (
            handoffs.script_input_mode(("dev", "script", "storyboard")) == handoffs.HANDOFF_MODE_DEV
        )
        assert (
            handoffs.script_input_mode(("script", "storyboard")) == handoffs.HANDOFF_MODE_RUN_LEVEL
        )

    def test_无dev回落运行级四键全运行级(self):
        view = handoffs.run_level_script_inputs(PILOT_INPUTS)
        assert view.mode == handoffs.HANDOFF_MODE_RUN_LEVEL
        assert set(view.inputs) == set(SCRIPT_INPUT_READS)
        assert set(view.declaration.reads.values()) == {"运行级"}
        assert view.declaration.assert_consistent() is None
        assert view.inputs["topic"] == PILOT_INPUTS["topic"]  # 不改名（无上游）

    def test_入口按_handoff_input_择一不静默(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        """无 `dev` 上游（`handoff_input=None`）⇒ 运行级回落，生效 mode 落 `StageState.detail`。"""
        runtime = stages_module.build_runtime(
            form=FORM,
            config_path=pilot_demo_config_path,
            data_dir=pilot_dirs,
            artifacts_root=tmp_path / "artifacts",
        )
        stages_module.bind_runtime(runtime)
        outcome = stages_module._script_entry(
            StageInput(
                stage_id="script",
                form=FORM,
                upstream={},
                handoff_input=None,
                shared={"runtime": runtime, "run_id": "run-fallback", "pilot_inputs": PILOT_INPUTS},
            )
        )
        assert outcome.detail["input_source"] == handoffs.HANDOFF_MODE_RUN_LEVEL
        assert outcome.detail["input_trace"]["topic"]["class"] == "运行级"

    def test_预检报告声明生效来源(self, pilot_demo_config_path, pilot_dirs):
        from agents.pilot.pilot import precheck

        report = precheck(
            form=FORM,
            config_path=pilot_demo_config_path,
            inputs=_inputs(),
            data_dir=pilot_dirs,
        )
        assert report["script_input_source"]["mode"] == handoffs.HANDOFF_MODE_DEV
        assert report["script_input_source"]["source"] == "stage_table"


class Test端到端接线:
    def test_七环节运行中剧本取自交接且mode可见(self, pilot_demo_config_path, pilot_dirs, tmp_path):
        result = _run(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", "run-handoff")
        dev = result.record.stage("dev")
        script = result.record.stage("script")
        assert script.detail["input_source"] == handoffs.HANDOFF_MODE_DEV
        assert script.detail["selected_entry"] == dev.detail["production_marks"][0]
        assert script.detail["input_trace"]["topic"]["source"] == "genre"
        assert result.precheck_report["script_input_source"]["mode"] == handoffs.HANDOFF_MODE_DEV

    def test_dev失败下游零调用且如实skipped(
        self, pilot_demo_config_path, pilot_dirs, tmp_path, monkeypatch
    ):
        calls: list[str] = []

        class _Rejected:
            round_id = "run-fail-dev-dev"
            tree_id = "dev-round-run-fail-dev-dev"
            policy_version = "34525518074d"
            job = {
                "job_id": "run-fail-dev-dev-slate",
                "status": "rejected",
                "reason": "策略计划缺少 entries（执行前拒绝）",
                "artifact_hash": None,
                "cache_key": None,
                "response_hash": None,
            }
            spent_usd = 0.0
            cost_reconciliation: dict = {}

        monkeypatch.setattr(stages_module, "run_dev_round", lambda **kwargs: _Rejected())
        original = stages_module._script_entry

        def _spy(stage_input):
            calls.append(stage_input.stage_id)
            return original(stage_input)

        monkeypatch.setattr(stages_module, "_script_entry", _spy)
        result = _run(pilot_demo_config_path, pilot_dirs, tmp_path / "artifacts", "run-fail-dev")
        assert result.record.status is RunStatus.FAILED
        assert result.record.stage("dev").status is StageStatus.FAILED
        assert result.record.stage("script").status is StageStatus.SKIPPED
        assert calls == []  # 下游零调用（不静默择一、不用上一轮残留顶替）
        assert result.package_dir is None


class Test两侧同步:
    def test_导出面新增字段而未同步即红(self, monkeypatch):
        monkeypatch.setattr(
            export_slate_module,
            "EXPORT_ENTRY_FIELDS",
            (*export_slate_module.EXPORT_ENTRY_FIELDS, "added_field"),
        )
        with pytest.raises(handoffs.HandoffError, match="added_field"):
            handoffs.dev_script_handoff_declaration()

    def test_导出面删除字段而未同步即红(self, monkeypatch):
        """本侧丢弃集声明的字段若从导出面消失 ⇒ 两侧不同步（红）。"""
        remaining = tuple(
            field for field in export_slate_module.EXPORT_ENTRY_FIELDS if field != "rationale"
        )
        monkeypatch.setattr(export_slate_module, "EXPORT_ENTRY_FIELDS", remaining)
        with pytest.raises(handoffs.HandoffError, match="rationale"):
            handoffs.dev_script_handoff_declaration()

    def test_schema_升版而未同步即红(self, monkeypatch):
        monkeypatch.setattr(dev_artifact, "SCHEMA_VERSION", "1.1.0")
        with pytest.raises(handoffs.HandoffError, match="SCHEMA_VERSION"):
            handoffs.dev_script_handoff_declaration()

    def test_改名承接不动导出面(self):
        """`genre` 仍在 017 导出面上（改名发生在**本侧**承接层）⇒ 不要求任何字段增删。"""
        assert "genre" in export_slate_module.EXPORT_ENTRY_FIELDS
        handoffs.dev_script_handoff_declaration()  # 同步断言通过即"不触发对侧快照变更"


class Test两层断言:
    def test_同名字段子集层与承接层各自成立(self):
        # 同名字段子集层：`consistent()` 三式（**只对同名键集求值**），原样保留
        same_name = handoffs.FieldParity(
            label="上游 → 下游（同名子集）",
            upstream=frozenset({"genre", "constraints", "characters"}),
            downstream=frozenset({"constraints", "characters"}),
            dropped=frozenset({"genre"}),
        )
        assert same_name.consistent() is True
        # 承接 / 运行级层：**不复用** `consistent()`，另立 ①~⑥
        declaration = handoffs.dev_script_handoff_declaration()
        assert declaration.violations() == []
        assert declaration.assert_consistent() is None

    def test_承接层不可复用同名声明的三式(self):
        """把改名承接与运行级塞进同名字段子集层 ⇒ `consistent()` 必然不成立（故不可复用）。"""
        declaration = handoffs.dev_script_handoff_declaration()
        mixed = handoffs.FieldParity(
            label="错用：承接层套同名声明的三式",
            upstream=handoffs.TRANSFERABLE_FIELDS,
            downstream=frozenset(declaration.reads),
        )
        assert mixed.consistent() is False
        # 反之：声明本身（承接层）仍然自洽——两条要求同时可满足
        assert declaration.violations() == []


def _copied_scan_domain(tmp_path: Path) -> Path:
    """扫描域的副本（注入读取点用；只复制被扫描的两个文件）。"""
    for relative in handoffs.READ_SCAN_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=0.5,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


def _run(config_path, data_dir, artifacts_root, run_id: str):
    return run_pilot(
        form=FORM,
        config_path=config_path,
        inputs=_inputs(),
        data_dir=data_dir,
        artifacts_root=artifacts_root,
        run_id=run_id,
        clock=lambda: "2026-01-01T00:00:00+00:00",
    )
