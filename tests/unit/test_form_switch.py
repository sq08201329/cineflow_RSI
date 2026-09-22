"""功能 015 US1（T1512）：双形态同链运行 + 形态差异逐项归因 + 零形态分支静态断言。

三件事：
1. **同链双形态**：同一条链代码（形态无关的 `StageSpec`）在两套配置上均跑通，成本/明细
   差异**完全来自配置值**（进入运行记录的形态值也只作如实透传）；
2. **差异可归因**：每一处形态差异都能在 `configs/*.yaml` 里找到承载字段（权重与阈值、
   节奏基准曲线前段权重上调、外环日级、预算与并行度下调、竖屏 1~3 分钟规格）；
3. **零形态分支**（宪章原则五 / SC-002）：`core/` 与 `agents/` 源码不含 `shortdrama`
   字面量分支，也不含 `form ==` 一类的形态判断——形态切换是**换配置文件**，不是改代码。
"""

import json
from pathlib import Path

import pytest
import yaml

from core.orchestration.dag import build_dag
from core.orchestration.executor import ExecutionContext, run
from core.orchestration.models import (
    ProductRef,
    RunStatus,
    StageOutcome,
    StageSpec,
    fingerprint_of,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMS = ("movie", "shortdrama")

# 同一条链读取的三个预算字段（形态差异的真实来源；阶段代码不认识形态）
BUDGET_STAGES = (
    ("budget_visual", ("visual", "exploration_per_round_usd")),
    ("budget_sound", ("sound", "exploration_per_round_usd")),
    ("budget_editing", ("editing", "exploration_per_round_usd")),
)


def _config_value(config_path: Path, keys: tuple[str, ...]):
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cursor = payload
    for key in keys:
        cursor = cursor[key]
    return cursor


def _without_cadence(deployment: dict) -> dict:
    """deployment 段去掉**唯一**按形态声明的运营节奏键（014 抽检超期告警窗口）。

    用于证明"除该键外形态无关基建段逐字相同"——放宽的只是这一处声明过的差异，
    其余任何意外分叉仍会被下面的等值断言抓住。
    """
    payload = json.loads(json.dumps(deployment))
    payload["spot_check"].pop("pending_alert_days", None)
    return payload


class _Recorder:
    """形态无关的阶段入口：从 shared 里的配置路径读预算并如实入账（同一份代码）。"""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        self.calls = 0

    def __call__(self, stage_input):
        self.calls += 1
        value = float(_config_value(Path(stage_input.shared["config_path"]), self.keys))
        product = ProductRef(
            kind="stub", ref=f"{stage_input.stage_id}", content_hash=fingerprint_of(str(value))
        )
        return StageOutcome(
            products=(product,),
            cost_usd=value,
            detail={"budget_usd": value, "form_seen": stage_input.form},
        )


def _chain():
    """同一条链（形态无关）：三阶段线性依赖，入口按 shared 中的配置路径读预算。"""
    recorders = {}
    specs = []
    for index, (stage_id, keys) in enumerate(BUDGET_STAGES):
        recorders[stage_id] = _Recorder(keys)
        specs.append(
            StageSpec(
                stage_id=stage_id,
                entrypoint=recorders[stage_id],
                depends_on=() if index == 0 else (BUDGET_STAGES[index - 1][0],),
                output_kind="stub",
            )
        )
    return build_dag(specs), recorders


def _run_form(config_path: Path):
    dag, recorders = _chain()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    ctx = ExecutionContext(
        run_id=f"run-{payload['form']}",
        form=payload["form"],
        config_fingerprint=fingerprint_of(config_path.read_bytes()),
        input_fingerprint=fingerprint_of("pilot-materials"),
        shared={"config_path": str(config_path)},
    )
    return run(dag, ctx, clock=lambda: "2026-09-21T00:00:00+00:00"), recorders


class Test同链双形态:
    def test_两套配置同链运行均成功(self, pilot_form_config_path):
        for form in FORMS:
            record, recorders = _run_form(pilot_form_config_path(form))
            assert record.status is RunStatus.DONE
            assert record.form == form
            assert record.completed_stages == tuple(stage_id for stage_id, _ in BUDGET_STAGES)
            assert all(recorder.calls == 1 for recorder in recorders.values())

    def test_阶段成本逐项等于各自配置值(self, pilot_form_config_path):
        for form in FORMS:
            config_path = pilot_form_config_path(form)
            record, _ = _run_form(config_path)
            expected = [float(_config_value(config_path, keys)) for _, keys in BUDGET_STAGES]
            assert [state.cost_usd for state in record.stages] == expected

    def test_双形态差异来自配置而非代码分支(self, pilot_form_config_path):
        movie, _ = _run_form(pilot_form_config_path("movie"))
        shortdrama, _ = _run_form(pilot_form_config_path("shortdrama"))
        # 同一份阶段代码 + 同一拓扑序：只有配置值不同 → 账目不同（且短剧更省）
        assert [s.stage_id for s in movie.stages] == [s.stage_id for s in shortdrama.stages]
        assert shortdrama.total_cost_usd < movie.total_cost_usd
        # 形态值进入阶段输入与运行记录（只透传，不参与任何判断）
        for state in shortdrama.stages:
            assert state.detail["form_seen"] == "shortdrama"
        assert json.loads(shortdrama.dump_json())["form"] == "shortdrama"


class Test差异逐项可归因:
    """每一项形态差异都必须落在配置文件里（找不到承载字段即红）。

    对比面 = 仓库**真实的两套形态配置**（`configs/movie.yaml` 与 `configs/shortdrama.yaml`）：
    形态差异的归因必须以真配置为准，夹具精简副本只用于同链双形态的运行验证。
    """

    def _pair(self, form="shortdrama"):
        movie = REPO_ROOT / "configs" / "movie.yaml"
        short = REPO_ROOT / "configs" / f"{form}.yaml"
        return (
            yaml.safe_load(movie.read_text(encoding="utf-8")),
            yaml.safe_load(short.read_text(encoding="utf-8")),
        )

    def test_权重与阈值差异(self):
        from core.evaluators.weights import load_evaluator_weights

        movie_path = REPO_ROOT / "configs" / "movie.yaml"
        short_path = REPO_ROOT / "configs" / "shortdrama.yaml"
        for agent in ("screenplay", "storyboard", "visual", "sound", "editing", "promo"):
            movie_w = load_evaluator_weights(movie_path, agent)
            short_w = load_evaluator_weights(short_path, agent)
            assert movie_w != short_w, f"{agent} 形态权重必须有差异（差异在配置，不在代码）"
            assert set(movie_w) == set(short_w)  # 差异靠值不靠删分量

        from agents.sound.config import SoundConfig
        from agents.storyboard.config import StoryboardConfig

        assert (
            SoundConfig.from_yaml(short_path).av_sync_threshold_ms
            < SoundConfig.from_yaml(movie_path).av_sync_threshold_ms
        )  # 口型同步阈值收紧
        assert (
            StoryboardConfig.from_yaml(short_path).shot_grammar["max_same_size_run"]
            < StoryboardConfig.from_yaml(movie_path).shot_grammar["max_same_size_run"]
        )  # 同景别连续镜头上限收紧

    def test_节奏基准曲线前段权重上调(self):
        from agents.editing.config import EditingConfig

        movie = EditingConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        short = EditingConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        movie_head = movie.pacing_baseline["segments"][0]
        short_head = short.pacing_baseline["segments"][0]
        assert short_head["weight"] > movie_head["weight"]  # 前段权重上调
        weights = [segment["weight"] for segment in short.pacing_baseline["segments"]]
        assert weights[0] == max(weights)  # 前段（留存决定段）为最高权重
        assert short.pacing_baseline["segments"][0]["span"] == [0.0, 0.1]  # 前 10% 即前段
        # 分段更细更快：均镜头时长下调
        assert short_head["mean_ms"] < movie_head["mean_ms"]

    def test_外环日级(self):
        from core.calibration.config import CalibrationConfig

        movie = CalibrationConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        short = CalibrationConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        assert short.period_days == 1 and short.period_days < movie.period_days

    def test_预算与并行度下调(self):
        from agents.editing.config import EditingConfig
        from agents.promo.config import PromoConfig
        from agents.sound.config import SoundConfig
        from agents.storyboard.config import StoryboardConfig
        from agents.visual.config import VisualConfig
        from dreaming.config import DreamConfig

        movie_path = REPO_ROOT / "configs" / "movie.yaml"
        short_path = REPO_ROOT / "configs" / "shortdrama.yaml"
        pairs = (
            (VisualConfig, "exploration_per_round_usd"),
            (SoundConfig, "exploration_per_round_usd"),
            (EditingConfig, "exploration_per_round_usd"),
            (StoryboardConfig, "exploration_per_round_usd"),
            (PromoConfig, "exploration_per_round_usd"),
        )
        for config_class, field in pairs:
            assert getattr(config_class.from_yaml(short_path), field) < (
                getattr(config_class.from_yaml(movie_path), field)
            ), f"{config_class.__name__}.{field} 必须下调"
        movie_cfg, short_cfg = self._pair()
        assert short_cfg["replay"]["worker_count"] < movie_cfg["replay"]["worker_count"]  # 并行度
        assert (
            DreamConfig.from_yaml(short_path).candidates_per_round
            < DreamConfig.from_yaml(movie_path).candidates_per_round
        )  # 做梦候选预算

    def test_竖屏规格(self):
        from agents.editing.config import EditingConfig
        from agents.storyboard.config import StoryboardConfig
        from agents.visual.config import VisualConfig

        short_path = REPO_ROOT / "configs" / "shortdrama.yaml"
        movie_path = REPO_ROOT / "configs" / "movie.yaml"
        for config_class, attr in (
            (VisualConfig, "clip_spec"),
            (StoryboardConfig, "render"),
            (EditingConfig, "render"),
        ):
            short_spec = getattr(config_class.from_yaml(short_path), attr)
            movie_spec = getattr(config_class.from_yaml(movie_path), attr)
            assert short_spec["height"] > short_spec["width"], f"{config_class.__name__} 必须竖屏"
            assert short_spec["height"] != movie_spec["height"]
        _, short_cfg = self._pair()
        width, height = short_cfg["promo"]["material_spec"]["poster_size"].split("x")
        assert int(height) > int(width)  # 竖屏封面

    def test_时长一分到三分钟档(self):
        from agents.editing.config import EditingConfig
        from agents.screenplay.config import ScreenplayConfig

        short_path = REPO_ROOT / "configs" / "shortdrama.yaml"
        short = ScreenplayConfig.from_yaml(short_path)
        assert 1 <= short.target_duration_min <= 3
        editing = EditingConfig.from_yaml(short_path)
        assert 60 <= editing.target_duration_s <= 180
        lower = short.target_duration_min - short.page_tolerance
        upper = short.target_duration_min + short.page_tolerance
        assert (lower, upper) == (1, 3)  # 页数区间恰好落在 1~3 分钟档

    def test_全量差异都被配置文件承载(self):
        """逐键对比两套配置：差异集合非空，且每处差异都可用配置路径指认。"""
        movie, short = self._pair()
        differences = {key for key in set(movie) | set(short) if movie.get(key) != short.get(key)}
        assert differences == {
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
        # 形态无关基建段逐字相同（web / cost_regression 不因形态而变）；
        # deployment 段**唯一**按形态声明的键是 `spot_check.pending_alert_days`
        # （014 复核超期告警窗口：运营节奏即形态——短剧投放密集，复核窗口更短），
        # 其余逐字相同（该键的取值口径另由 test_deployment_config 的用例守住）
        for key in ("web", "cost_regression"):
            assert movie[key] == short[key]
        assert (
            movie["deployment"]["spot_check"]["pending_alert_days"]
            != short["deployment"]["spot_check"]["pending_alert_days"]
        )
        assert _without_cadence(movie["deployment"]) == _without_cadence(short["deployment"])


class Test零形态分支静态断言:
    """SC-002：形态分支零代码——`core/` 与 `agents/` 不含形态字面量分支或 form 判断。"""

    BANNED_LITERALS = ("shortdrama", '"movie"', "'movie'")
    BANNED_PATTERNS = ("form ==", "form==", "form !=", "form!=", "form is ", "form in ")

    def _sources(self, root: str):
        return sorted(
            path for path in (REPO_ROOT / root).rglob("*.py") if "__pycache__" not in path.parts
        )

    def test_core_与_agents_无形态字面量(self):
        scanned = self._sources("core") + [
            path for path in self._sources("agents") if "pilot" not in path.parts
        ]
        assert scanned, "未扫描到源码"
        for path in scanned:
            source = path.read_text(encoding="utf-8")
            for banned in self.BANNED_LITERALS:
                assert banned not in source, f"{path} 不得出现形态字面量：{banned}"

    def test_全仓_agents_与_core_无形态判断分支(self):
        # agents/pilot 的配置读取允许读形态值，但同样不得按形态分支
        for path in self._sources("core") + self._sources("agents"):
            source = path.read_text(encoding="utf-8")
            for banned in self.BANNED_PATTERNS:
                assert banned not in source, f"{path} 不得出现形态判断：{banned}"

    def test_形态切换只经配置文件(self):
        """形态以配置文件为唯一载体：两套配置存在即两个形态，代码侧无形态枚举/映射表。"""
        configs = sorted(path.name for path in (REPO_ROOT / "configs").glob("*.yaml"))
        assert configs == ["movie.yaml", "shortdrama.yaml"]


@pytest.mark.parametrize("form", FORMS)
def test_形态配置可被链读取(form, pilot_form_config_path):
    payload = yaml.safe_load(pilot_form_config_path(form).read_text(encoding="utf-8"))
    assert payload["form"] == form
