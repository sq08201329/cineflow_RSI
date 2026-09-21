"""功能 015 US1（T1511）：短剧形态配置的**加载器完整性**机检（零代码切换的真实检验）。

`configs/shortdrama.yaml` 必须通过**全部**加载器：六个 Agent 的 `*Config.from_yaml`、
replay.pooling / dreaming / calibration / drift / deployment / web 段解析、以及六个
Agent 的 `evaluator_weights` 读取。缺项即红——若某个加载器非要代码分支才能适配，
本文件就会暴露它（FR-001：缺失关键项启动前报错，不静默回退）。

同时机检"缺项即红"的**反面**：临时删掉必需段后加载器必须报错（证明本机检有牙齿，
不是空跑全绿）。
"""

import importlib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"
MOVIE = REPO_ROOT / "configs" / "movie.yaml"

# (机检名, 模块, 配置类)：短剧配置必须逐个通过
CONFIG_CLASSES = (
    ("screenplay", "agents.screenplay.config", "ScreenplayConfig"),
    ("storyboard", "agents.storyboard.config", "StoryboardConfig"),
    ("visual", "agents.visual.config", "VisualConfig"),
    ("sound", "agents.sound.config", "SoundConfig"),
    ("editing", "agents.editing.config", "EditingConfig"),
    ("promo", "agents.promo.config", "PromoConfig"),
    ("pooling", "core.replay.pooling_models", "PoolingConfig"),
    ("dreaming", "dreaming.config", "DreamConfig"),
    ("calibration", "core.calibration.config", "CalibrationConfig"),
    ("drift", "core.calibration.drift_config", "DriftConfig"),
    ("deployment", "core.deployment.config", "DeploymentConfig"),
    ("web", "web.queries", "WebConfig"),
)

# 形态权重读取覆盖的 Agent（evaluator_weights 段）
WEIGHT_AGENTS = ("screenplay", "storyboard", "visual", "sound", "editing", "promo")

# "缺项即红"样例：(机检名, 待删除的配置路径)
REQUIRED_PATHS = (
    ("screenplay", ("screenplay", "beat_sheet")),
    ("screenplay", ("screenplay", "dialogue_action_ratio")),
    ("storyboard", ("storyboard", "shot_grammar")),
    ("storyboard", ("storyboard", "render")),
    ("visual", ("visual", "clip_spec")),
    ("visual", ("visual", "judge")),
    ("sound", ("sound", "prices")),
    ("sound", ("sound", "loudness")),
    ("editing", ("editing", "pacing_baseline")),
    ("editing", ("editing", "transition_rules")),
    ("promo", ("promo", "model_prices")),
    ("pooling", ("replay", "pooling")),
    ("dreaming", ("dreaming",)),
    ("calibration", ("calibration", "self_pairing_exclusions")),
    ("drift", ("calibration", "drift")),
    ("deployment", ("deployment", "gate")),
    ("web", ("web", "data_dirs")),
)


def _load_config(name: str, path: Path):
    """按机检名调用对应配置类加载器（延迟导入：避免与既有测试的导入顺序耦合）。"""
    module_name, class_name = next(
        (module, cls) for key, module, cls in CONFIG_CLASSES if key == name
    )
    config_class = getattr(importlib.import_module(module_name), class_name)
    return config_class.from_yaml(path)


def _load_weights(agent: str, path: Path):
    """按 Agent 名读取 evaluator_weights（权重加载器是独立入口）。"""
    from core.evaluators.weights import load_evaluator_weights

    return load_evaluator_weights(path, agent)


class Test形态标识与段完整性:
    def test_形态标识为短剧(self):
        payload = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
        assert payload["form"] == "shortdrama"

    def test_与电影配置段集合一致(self):
        """形态差异靠**值**表达，不靠删段：两套配置的顶层段集合一致。"""
        shortdrama = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
        movie = yaml.safe_load(MOVIE.read_text(encoding="utf-8"))
        assert set(shortdrama) == set(movie)

    def test_权重段覆盖全部_Agent(self):
        payload = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
        assert set(payload["evaluator_weights"]) == set(WEIGHT_AGENTS)


@pytest.mark.parametrize("name", [key for key, _, _ in CONFIG_CLASSES])
class Test全部配置类加载器:
    def test_短剧配置可加载(self, name):
        loaded = _load_config(name, SHORTDRAMA)
        assert loaded is not None

    def test_电影配置同样可加载(self, name):
        # 对照面：两套配置都过同一加载器（同链双形态的前提）
        assert _load_config(name, MOVIE) is not None


@pytest.mark.parametrize("agent", WEIGHT_AGENTS)
def test_权重读取覆盖全部_Agent(agent):
    weights = _load_weights(agent, SHORTDRAMA)
    assert weights, f"{agent} 权重不得为空"
    # 每个 Agent 至少一条 hard gate（形态差异不削弱门禁）
    raw = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))["evaluator_weights"][agent]
    assert "gate" in list(raw.values()), f"{agent} 必须保留硬规则门禁"


@pytest.mark.parametrize("name,keys", REQUIRED_PATHS)
def test_缺项即红(name, keys, tmp_path):
    """删掉必需段后加载器必须报错（不静默回退）——本机检的牙齿。"""
    payload = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
    cursor = payload
    for key in keys[:-1]:
        cursor = cursor[key]
    del cursor[keys[-1]]
    broken = tmp_path / "shortdrama.yaml"
    broken.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # 加载器各自定义错误类型（不共享基类），此处只机检"确实拒绝了"（缺项不静默回退）
    with pytest.raises(Exception):  # noqa: B017 - 各族加载器错误类型不一，统一断言"有拒绝"
        _load_config(name, broken)


def test_短剧配置无遗留占位():
    """配置不得含 TODO/占位值（形态配置是运行期唯一形态载体，必须完整可跑）。"""
    text = SHORTDRAMA.read_text(encoding="utf-8")
    for banned in ("TODO", "FIXME", "PLACEHOLDER", "xxx"):
        assert banned not in text
