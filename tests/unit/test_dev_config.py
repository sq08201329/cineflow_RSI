"""DevConfig 配置测试（功能 017 / T1718）。

契约 C10 / FR-012：`dev` 段**全量声明**——组合条目数区间、进入生产标记数区间、组合约束
（方向重复率上限）、回放对比最小可比对树数、模拟数据源参数、升级判据阈值（四项），
另加生成模型与输出预算（生成是唯一昂贵动作，输出预算决定实际产出与成本上界）；
**缺任一项即报错**（不允许静默取码内默认——那会让"形态可配置"变成空话）。
两形态（movie / shortdrama）取值确有差异，且差异只经配置表达（零形态分支，原则五）；
权重读自 `evaluator_weights.dev`（两 gate + 两 proxy），缺失即报错。
"""

import copy
from pathlib import Path

import pytest
import yaml

from agents.dev.config import THRESHOLD_KEYS, DevConfig, DevConfigError
from core.evaluators.weights import load_evaluator_weights

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIE = REPO_ROOT / "configs" / "movie.yaml"
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"

# 缺项即报错矩阵：dev 段每一项都是形态参数（缺项不静默回退）
REQUIRED_PATHS = (
    ("dev", "slate"),
    ("dev", "slate", "min"),
    ("dev", "slate", "max"),
    ("dev", "production_marks"),
    ("dev", "production_marks", "min"),
    ("dev", "production_marks", "max"),
    ("dev", "combination"),
    ("dev", "combination", "max_direction_repeat_rate"),
    ("dev", "min_comparable_trees"),
    ("dev", "signals"),
    ("dev", "signals", "baseline_usd_million"),
    ("dev", "signals", "sensitivity"),
    ("dev", "signals", "buzz_baseline"),
    ("dev", "upgrade_criteria", "correlation_target"),
    ("dev", "upgrade_criteria", "min_samples"),
    ("dev", "upgrade_criteria", "drift_band"),
    ("dev", "upgrade_criteria", "gate_violation_max"),
    ("dev", "model"),
    ("dev", "model_prices"),
    ("dev", "max_tokens"),
    ("evaluator_weights",),
)


def _delete(payload: dict, path: tuple[str, ...]) -> dict:
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    del cursor[path[-1]]
    return payload


def _set(payload: dict, path: tuple[str, ...], value) -> dict:
    cursor = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    return payload


class Test真实配置解析:
    def test_两形态均可加载(self):
        for path in (MOVIE, SHORTDRAMA):
            config = DevConfig.from_yaml(path)
            assert config.slate_entries[0] <= config.slate_entries[1]
            assert config.production_marks[0] <= config.production_marks[1]
            assert config.min_comparable_trees >= 1

    def test_电影段取值(self, dev_config_fragment):
        config = DevConfig.from_dict(dev_config_fragment())
        assert config.slate_entries == (3, 6)
        assert config.production_marks == (1, 1)
        assert config.max_direction_repeat_rate == 0.34
        assert config.min_comparable_trees == 3
        assert set(config.signals) >= {"baseline_usd_million", "sensitivity", "buzz_baseline"}
        assert set(config.upgrade_criteria) == set(THRESHOLD_KEYS)
        assert config.max_tokens >= 1
        assert config.model in config.model_prices

    def test_快照冻结(self, dev_config_fragment):
        """配置冻结（随轮次树 config_snapshot 落盘：此后配置变更不影响历史节点）。"""
        config = DevConfig.from_dict(dev_config_fragment())
        with pytest.raises(Exception):  # noqa: B017 - frozen dataclass 抛 FrozenInstanceError
            config.min_comparable_trees = 9
        assert copy.deepcopy(config.evaluator_weights) == config.evaluator_weights

    def test_非_mapping_与缺_dev_段即报错(self, dev_config_fragment):
        with pytest.raises(DevConfigError, match="dict"):
            DevConfig.from_dict(["not-a-mapping"])
        with pytest.raises(DevConfigError, match="dev 段"):
            DevConfig.from_dict({"form": "movie", "evaluator_weights": {"dev": {"rule.x": "gate"}}})


@pytest.mark.parametrize("path", REQUIRED_PATHS, ids=lambda path: ".".join(path))
def test_缺项即报错(dev_config_fragment, path):
    """任一项缺失即装配期报错（不取码内默认：形态参数是配置的唯一事实源）。"""
    with pytest.raises(DevConfigError):
        DevConfig.from_dict(_delete(dev_config_fragment(), path))


class Test取值越界即报错:
    @pytest.mark.parametrize(
        ("path", "value"),
        [
            (("dev", "slate"), {"min": 5, "max": 3}),  # 区间倒置
            (("dev", "slate"), {"min": 0, "max": 3}),  # 条目数下界必须 ≥ 1
            (("dev", "slate"), {"min": 3}),  # 缺 max
            (("dev", "production_marks"), {"min": -1, "max": 1}),
            (("dev", "combination"), {"max_direction_repeat_rate": 1.5}),  # ∉ [0,1]
            (("dev", "combination"), {"max_direction_repeat_rate": -0.1}),
            (("dev", "min_comparable_trees"), 0),  # 前置门槛必须 ≥ 1
            (("dev", "signals"), {"baseline_usd_million": -1.0, "sensitivity": 1.0}),
            (("dev", "signals"), {"baseline_usd_million": 40.0, "sensitivity": 1e9}),
            (("dev", "upgrade_criteria"), {"min_samples": 0}),  # 最小样本量 ≥ 1
            (("dev", "model"), "ghost-model"),  # 模型不在价目表内
            (("dev", "max_tokens"), 0),  # 输出预算 ≥ 1
            (("dev", "model_prices"), {}),  # 空价目表
            (("evaluator_weights",), {}),
            (("evaluator_weights",), {"dev": {}}),
        ],
    )
    def test_非法取值即报错(self, dev_config_fragment, path, value):
        payload = dev_config_fragment()
        with pytest.raises(DevConfigError):
            DevConfig.from_dict(_set(payload, path, value))

    def test_判据阈值非数值即报错(self, dev_config_fragment):
        payload = _set(dev_config_fragment(), ("dev", "upgrade_criteria", "min_samples"), "12")
        with pytest.raises(DevConfigError):
            DevConfig.from_dict(payload)


class Test两形态取值差异:
    def test_段取值确有差异(self):
        """形态差异由配置承载（原则五）：差异靠值，不靠删段。"""
        movie = DevConfig.from_yaml(MOVIE)
        short = DevConfig.from_yaml(SHORTDRAMA)
        assert movie.slate_entries != short.slate_entries
        assert movie.production_marks != short.production_marks
        assert movie.max_direction_repeat_rate != short.max_direction_repeat_rate
        assert movie.signals != short.signals
        assert movie.upgrade_criteria != short.upgrade_criteria
        assert set(movie.signals) == set(short.signals)
        assert set(movie.upgrade_criteria) == set(short.upgrade_criteria)

    def test_权重来自_evaluator_weights_dev(self):
        """权重读自专题段（缺失即报错），两形态取值不同且分量键集一致。"""
        movie = DevConfig.from_yaml(MOVIE)
        short = DevConfig.from_yaml(SHORTDRAMA)
        assert set(movie.evaluator_weights) == set(load_evaluator_weights(MOVIE, "dev"))
        # 门禁分量以 "gate" 字面量声明（权重读取侧折算为 0.0：硬规则不进加权求和）
        gates = {key for key, value in movie.evaluator_weights.items() if value == "gate"}
        assert gates == {"rule.slate_structure", "rule.slate_combination"}
        assert all(load_evaluator_weights(MOVIE, "dev")[key] == 0.0 for key in gates)
        assert set(movie.evaluator_weights) == set(short.evaluator_weights)
        assert movie.evaluator_weights != short.evaluator_weights
        assert sorted(key for key in movie.evaluator_weights if key.startswith("proxy.")) == [
            "proxy.buzz_heat",
            "proxy.genre_regression",
        ]

    def test_顶层段集合一致(self):
        movie = yaml.safe_load(MOVIE.read_text(encoding="utf-8"))
        short = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
        assert set(movie["dev"]) == set(short["dev"])
