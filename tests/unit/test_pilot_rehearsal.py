"""功能 018 阶段 4 / US1（契约 C10）：排练档的**单点解析**与档位内部一致性机检。

排练档（FR-013/014）的机制目标是"**缩的是数字，不是链路**"：`status=declared` 时按
`pilot.rehearsal.scale` 覆盖四个体量键，链路拓扑 / 交接契约 / 门禁 / 评估器组合**一行不动**；
`unstandardized`（未标定）时不覆盖（形态原值在 force）并如实标注"未标定"——**不发明数字**；
`work_kind=real_work`（真实作品）同样不缩档，且标记可机检地**不为** `rehearsal`（排练产物
不得被标为真实作品）。

另覆盖：缺项即拒绝启动（缺 `status` / `work_kind` / `scale` 任一键）、两处时长口径不一致即
**拒绝启动并点名两处实测值**（SC-012①）、时长粒度到秒级浮点与浮点分钟（`0.5` = 30 秒档可
声明且可经运行级输入对齐）、以及档位内部一致性（秒 ↔ 分钟折算、页数区间、派生镜头数与
索引容量下界——派生镜头数只读 `agents/pilot/scale.py` 的唯一公式）。

**如实登记（包面边界）**：`work_kind` 与生效体量快照进入**样片包**（`manifest.work_kind` /
`state.volume`）的落点属**阶段 6 的 T1829**；本阶段（US1）的机检面是**预检报告的确定性段**
`pilot_volume`（`agents/pilot/pilot.py` 的 `effective_volume`）与运行期生效配置——两者与
T1829 读**同一份**快照，故"排练产物不得被标为真实作品"的口径只有一处取值来源。
"""

from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from agents.pilot import scale as scale_module
from agents.pilot import stages as stages_module
from agents.pilot.pilot import PilotConfig, PilotInputs, PrecheckError, precheck
from agents.pilot.stages import ScreenplayConfig, VisualConfig, build_runtime, build_stage_specs
from core.evaluators.weights import load_evaluator_weights

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMS = ("shortdrama", "movie")
# 链首插入后的拓扑序（独立声明，不复用实现常量）
SEVEN = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")
# 演示档（30 秒档；短剧形态原值为 120 秒/2 分钟，故这是"缩档"而非"等值"）
DEMO_SCALE = {
    "target_duration_s": 30.0,
    "script_target_minutes": 0.5,
    "script_tolerance_minutes": 1.0,
    "clip_duration_seconds": 7.5,
}
# 与形态原值逐键不同的档位（四键全动，用来证明"覆盖集恰为四个体量键"）
OTHER_SCALE = {
    "target_duration_s": 60.0,
    "script_target_minutes": 1.0,
    "script_tolerance_minutes": 2.0,
    "clip_duration_seconds": 5.0,
}


def _inputs(minutes: float = 0.5) -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=minutes,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
        genre_bounds=("悬疑", "夜戏"),
        audience="都市女性",
    )


def _real_config(tmp_path: Path, form: str) -> Path:
    """真实形态配置的派生副本：只把账本根落 tmp，形态取值**逐字保留**（口径变了即红）。"""
    source = (REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8")
    assert "root: billing" in source, "派生点存在（账本根不得落仓库）"
    target = tmp_path / "configs" / f"{form}-real.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.replace("root: billing", f"root: {tmp_path / 'billing'}"), encoding="utf-8"
    )
    return target


def _derived(base: Path, tmp_path: Path, *, name: str, mutate) -> Path:
    """派生副本：`mutate` 只改配置声明（YAML 语义合并，其余取值逐值不变）。"""
    payload = yaml.safe_load(base.read_text(encoding="utf-8"))
    mutate(payload)
    target = tmp_path / "configs" / f"{name}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _with_rehearsal(**rehearsal: Any):
    """把 `pilot.rehearsal` 段整体替换为给定声明（缺键由实现的缺项拒绝如实报错）。"""

    def _mutate(payload: dict) -> None:
        payload["pilot"]["rehearsal"] = rehearsal

    return _mutate


def _config_with(
    tmp_path: Path,
    form: str,
    *,
    name: str,
    status: str | None = "declared",
    work_kind: str | None = "rehearsal",
    scale: dict | None = None,
) -> Path:
    """派生排练档声明（`None` 即**不写该键**，用来机检缺项拒绝）。"""
    rehearsal: dict[str, Any] = {}
    if status is not None:
        rehearsal["status"] = status
    if work_kind is not None:
        rehearsal["work_kind"] = work_kind
    if scale is not None:
        rehearsal["scale"] = scale
    return _derived(
        _real_config(tmp_path, form), tmp_path, name=name, mutate=_with_rehearsal(**rehearsal)
    )


def _runtime(config_path: Path, tmp_path: Path, form: str):
    return build_runtime(
        form=form,
        config_path=config_path,
        data_dir=tmp_path / f"data-{form}",
        artifacts_root=tmp_path / f"artifacts-{form}",
    )


def _changed_paths(left: Any, right: Any, prefix: str = "") -> set[str]:
    """两个 `AgentConfigs` 的逐字段差异路径集（证明"只覆盖体量键"）。"""
    if is_dataclass(left) and is_dataclass(right):
        changed: set[str] = set()
        for field in fields(left):
            changed |= _changed_paths(
                getattr(left, field.name), getattr(right, field.name), f"{prefix}{field.name}."
            )
        return changed
    return set() if left == right else {prefix.rstrip(".")}


def _declarations(runtime) -> tuple:
    """阶段结构视图：拓扑/依赖/产物 kind/入口与交接函数的**声明形状**。"""
    return tuple(
        (spec.stage_id, spec.depends_on, spec.output_kind, spec.title, spec.handoff.__name__)
        for spec in build_stage_specs(runtime)
    )


class Test单点解析:
    def test_declared_按档位覆盖生效体量(self, tmp_path):
        form = "shortdrama"
        config = _config_with(tmp_path, form, name="declared", scale=DEMO_SCALE)
        runtime = _runtime(config, tmp_path, form)
        configs = runtime.configs
        assert float(configs.editing.target_duration_s) == pytest.approx(30.0)
        # 剧本侧的目标/容差是**整页口径**（页数门禁 `rule.page_minutes` 是整数页）：浮点分钟档
        # 向上折算到 ≥1 页（`apply_rehearsal_scale` 的分钟 → 整页折算口径）
        assert int(configs.screenplay.target_duration_min) == 1
        assert int(configs.screenplay.page_tolerance) == 1
        assert float(configs.visual.clip_spec["duration_seconds"]) == pytest.approx(7.5)

    def test_只覆盖四个体量键_链路与评估器组合不动(self, tmp_path):
        form = "shortdrama"
        plain = _config_with(tmp_path, form, name="plain", status="unstandardized", scale=None)
        full = _config_with(tmp_path, form, name="full", scale=OTHER_SCALE)
        plain_runtime = _runtime(plain, tmp_path, form)
        full_runtime = _runtime(full, tmp_path, form)
        # 覆盖集**恰为**四个体量键所在路径（多一处即红：链路/门禁/评估器组合一行不动）
        assert _changed_paths(plain_runtime.configs, full_runtime.configs) == {
            "screenplay.target_duration_min",
            "screenplay.page_tolerance",
            "visual.clip_spec",
            "editing.target_duration_s",
        }
        # 阶段结构（拓扑/依赖/产物 kind/入口/交接）逐项相同且恰为七环节
        assert _declarations(plain_runtime) == _declarations(full_runtime)
        assert tuple(spec.stage_id for spec in build_stage_specs(full_runtime)) == SEVEN
        # 评估器组合（权重与门禁声明）逐段相同：档位只换取值，不换评估器
        for section in stages_module.STAGE_CONFIG_SECTION.values():
            assert load_evaluator_weights(plain, section) == load_evaluator_weights(full, section)

    def test_unstandardized_不覆盖且如实标注未标定(self, tmp_path):
        form = "shortdrama"
        config = _config_with(tmp_path, form, name="unstd", status="unstandardized", scale=None)
        runtime = _runtime(config, tmp_path, form)
        raw = stages_module.EditingConfig.from_yaml(config)
        screenplay = ScreenplayConfig.from_yaml(config)
        # 形态原值在 force（不覆盖）
        assert float(runtime.configs.editing.target_duration_s) == pytest.approx(
            float(raw.target_duration_s)
        )
        assert int(runtime.configs.screenplay.target_duration_min) == screenplay.target_duration_min
        report = precheck(
            form=form, config_path=config, inputs=_inputs(2.0), data_dir=tmp_path / "pre"
        )
        volume = report["pilot_volume"]
        assert volume["rehearsal_status"] == "unstandardized"
        assert volume["scale_in_force"] is False
        assert volume["source"] == "form_original"
        assert "未标定" in volume["note"]
        # 未标定期间**不发明数字**：生效体量逐键等于形态原值
        assert volume["effective"]["target_duration_s"] == pytest.approx(
            float(raw.target_duration_s)
        )
        assert volume["effective"]["clip_duration_seconds"] == pytest.approx(
            float(VisualConfig.from_yaml(config).clip_spec["duration_seconds"])
        )

    def test_生效体量快照与运行期配置同源(self, tmp_path):
        form = "shortdrama"
        config = _config_with(tmp_path, form, name="snapshot", scale=DEMO_SCALE)
        runtime = _runtime(config, tmp_path, form)
        report = precheck(
            form=form, config_path=config, inputs=_inputs(0.5), data_dir=tmp_path / "pre"
        )
        volume = report["pilot_volume"]
        assert volume["work_kind"] == "rehearsal"
        assert volume["scale_in_force"] is True
        assert volume["source"] == "declared_scale"
        assert volume["effective"]["target_duration_s"] == pytest.approx(
            float(runtime.configs.editing.target_duration_s)
        )
        assert volume["effective"]["script_target_pages"] == int(
            runtime.configs.screenplay.target_duration_min
        )
        assert volume["effective"]["clip_duration_seconds"] == pytest.approx(
            float(runtime.configs.visual.clip_spec["duration_seconds"])
        )
        assert volume["effective"]["scene_count"] == runtime.pilot.scene_count
        assert volume["effective"]["lines_per_scene"] == runtime.pilot.lines_per_scene

    def test_real_work_不缩档且标记不为排练(self, tmp_path):
        form = "shortdrama"
        config = _config_with(
            tmp_path, form, name="real-work", work_kind="real_work", scale=DEMO_SCALE
        )
        runtime = _runtime(config, tmp_path, form)
        raw = stages_module.EditingConfig.from_yaml(config)
        # 真实作品用形态原值：档位声明照在，但**不生效**（不缩档）
        assert float(runtime.configs.editing.target_duration_s) == pytest.approx(
            float(raw.target_duration_s)
        )
        assert runtime.pilot.rehearsal_in_force is False
        report = precheck(
            form=form, config_path=config, inputs=_inputs(2.0), data_dir=tmp_path / "pre"
        )
        volume = report["pilot_volume"]
        assert volume["work_kind"] == "real_work"
        assert volume["work_kind"] != "rehearsal"  # 排练产物不得被标为真实作品（反之亦然）
        assert volume["scale_in_force"] is False
        assert volume["source"] == "form_original"


class Test缺项与拒绝语义:
    @pytest.mark.parametrize(
        ("status", "work_kind", "scale", "named"),
        [
            (None, "rehearsal", DEMO_SCALE, "rehearsal.status"),
            ("declared", None, DEMO_SCALE, "rehearsal.work_kind"),
            ("declared", "rehearsal", None, "rehearsal.scale"),
            (
                "declared",
                "rehearsal",
                {**DEMO_SCALE, "clip_duration_seconds": None},
                "clip_duration_seconds",
            ),
        ],
    )
    def test_排练档缺项即拒绝启动(self, tmp_path, status, work_kind, scale, named):
        form = "shortdrama"
        config = _config_with(
            tmp_path, form, name="missing", status=status, work_kind=work_kind, scale=scale
        )
        with pytest.raises(PrecheckError) as excinfo:
            PilotConfig.from_yaml(config)
        assert named in str(excinfo.value)
        with pytest.raises(PrecheckError):
            precheck(form=form, config_path=config, inputs=_inputs(0.5), data_dir=tmp_path / "pre")

    def test_两处时长不一致即拒绝启动并点名实测值(self, tmp_path):
        form = "movie"

        def _break(payload: dict) -> None:
            payload["editing"]["target_duration_s"] = 120  # 与 screenplay 90 分钟（5400 秒）矛盾

        config = _derived(
            _real_config(tmp_path, form), tmp_path, name="movie-mismatch", mutate=_break
        )
        with pytest.raises(PrecheckError) as excinfo:
            precheck(form=form, config_path=config, inputs=_inputs(0.5), data_dir=tmp_path / "pre")
        message = str(excinfo.value)
        assert "5400 s" in message  # 两处实测值逐字点名（不静默择一）
        assert "120 s" in message

    def test_运行级时长与生效档不一致即拒绝(self, tmp_path):
        form = "shortdrama"
        config = _config_with(tmp_path, form, name="run-level", scale=DEMO_SCALE)
        with pytest.raises(PrecheckError) as excinfo:
            precheck(form=form, config_path=config, inputs=_inputs(2.0), data_dir=tmp_path / "pre")
        message = str(excinfo.value)
        assert "120 s" in message  # 运行级 2.0 分钟 → 120 秒
        assert "30 s" in message  # 生效档 30 秒


class Test档位粒度:
    def test_30秒演示档可声明且可经浮点分钟对齐(self, tmp_path):
        form = "movie"  # 真实 movie 配置声明 30 秒排练档（0.5 分钟）
        config = _real_config(tmp_path, form)
        report = precheck(
            form=form, config_path=config, inputs=_inputs(0.5), data_dir=tmp_path / "pre"
        )
        assert report["pilot_volume"]["effective"]["target_duration_s"] == pytest.approx(30.0)
        assert report["pilot_volume"]["effective"]["script_target_minutes"] == pytest.approx(0.5)

    def test_浮点分钟指纹确定性(self):
        assert _inputs(0.5).to_dict()["target_duration_min"] == 0.5
        assert _inputs(2.0).to_dict()["target_duration_min"] == 2  # 整值写整值（不产生两个指纹）
        assert _inputs(2.0).fingerprint() == _inputs(2).fingerprint()
        assert _inputs(0.5).fingerprint() != _inputs(2.0).fingerprint()


class Test档位内部一致性:
    @pytest.mark.parametrize("form", FORMS)
    def test_生效体量在档位内自洽(self, tmp_path, form):
        config = _real_config(tmp_path, form)
        runtime = _runtime(config, tmp_path, form)
        pilot = runtime.pilot
        configs = runtime.configs
        screenplay = ScreenplayConfig.from_yaml(config)
        # ① 秒 ↔ 分钟折算：生效成片时长 == 生效剧本目标分钟 × 60（容差 1e-6，SC-012①）
        assert float(configs.editing.target_duration_s) == pytest.approx(
            pilot.effective_script_target_minutes(screenplay.target_duration_min) * 60.0,
            abs=1e-6,
        )
        # ② 页数区间（口径 `agents/screenplay/evaluators/page_minutes.py:55-66`）：
        #    页数 = 场景数 × 每场景行数 ÷ lines_per_page ∈ [目标 ± 容差]
        pages = pilot.scene_count * pilot.lines_per_scene / configs.screenplay.lines_per_page
        low = configs.screenplay.target_duration_min - configs.screenplay.page_tolerance
        high = configs.screenplay.target_duration_min + configs.screenplay.page_tolerance
        assert low - 1e-6 <= pages <= high + 1e-6
        # ③ 派生镜头数满足索引容量下界（公式只有一份：`agents/pilot/scale.py`）；量子上界同理
        derived = scale_module.derived_shot_count(
            scene_count=pilot.scene_count,
            target_duration_s=float(configs.editing.target_duration_s),
            clip_duration_seconds=float(configs.visual.clip_spec["duration_seconds"]),
        )
        grid = configs.storyboard.render["index_grid"]
        assert derived <= 2 ** (int(grid["rows"]) * int(grid["cols"]))
        assert 2 ** int(grid["cols"]) <= int(configs.storyboard.render["width"])
        assert 1 <= int(grid["rows"]) <= int(configs.storyboard.render["height"])


class Test零档位分支:
    def test_实现侧无档位与形态分支(self):
        """`code` 侧不得按档位/形态分支：取值只来自配置声明的**同一段解析**。"""
        banned = (
            "shortdrama",
            '"movie"',
            "'movie'",
            "form ==",
            "form is ",
            "form !=",
            "试水档",
            "长片档",
        )
        for path in sorted((REPO_ROOT / "agents" / "pilot").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for literal in banned:
                assert literal not in source, f"{path.name}:{literal}"
