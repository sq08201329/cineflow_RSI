"""形态配置权重读取单测（US3 / T027）。

- 从 configs/movie.yaml 读取 evaluator_weights；gate 语义转为 0.0 权重
  （门禁由 composite_score 的 rule. 前缀检查承担）；
- 缺键/非法值报错（WeightConfigError）；
- 读出的快照可冻结进 DiscoveryTree.config_snapshot（US3 验收场景 3 的配合面）。
"""

from pathlib import Path

import pytest
import yaml
from core.evaluators.weights import load_evaluator_weights

from core.evaluators.errors import WeightConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"


class Test读取形态配置:
    def test_读取_movie_yaml_visual_权重(self):
        weights = load_evaluator_weights(MOVIE_YAML, "visual")
        assert weights["rule.format_compliance"] == 0.0  # gate → 0.0（仅门禁，不参与求和）
        assert weights["proxy.aesthetic"] == pytest.approx(0.25)
        assert weights["proxy.identity_consistency"] == pytest.approx(0.35)
        assert weights["proxy.flicker"] == pytest.approx(0.15)
        assert weights["judge.cinematic"] == pytest.approx(0.25)

    def test_非_gate_权重总和为一(self):
        """Σweights = 1 由配置作者保证；movie.yaml 示例应满足（门禁权重除外）。"""
        weights = load_evaluator_weights(MOVIE_YAML, "visual")
        assert sum(weights.values()) == pytest.approx(1.0)


class Test缺键与非法值:
    def _write(self, tmp_path: Path, data: dict) -> Path:
        path = tmp_path / "form.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True))
        return path

    def test_缺_evaluator_weights_节报错(self, tmp_path):
        path = self._write(tmp_path, {"form": "x"})
        with pytest.raises(WeightConfigError, match="evaluator_weights"):
            load_evaluator_weights(path, "any")

    def test_缺_agent_键报错(self, tmp_path):
        path = self._write(tmp_path, {"evaluator_weights": {"other": {"proxy.a": 1.0}}})
        with pytest.raises(WeightConfigError, match="any"):
            load_evaluator_weights(path, "any")

    def test_权重值非法类型报错(self, tmp_path):
        path = self._write(tmp_path, {"evaluator_weights": {"a": {"proxy.a": "high"}}})
        with pytest.raises(WeightConfigError):
            load_evaluator_weights(path, "a")

    def test_权重值负数报错(self, tmp_path):
        path = self._write(tmp_path, {"evaluator_weights": {"a": {"proxy.a": -0.5}}})
        with pytest.raises(WeightConfigError):
            load_evaluator_weights(path, "a")

    def test_配置文件不存在报错(self, tmp_path):
        with pytest.raises(WeightConfigError):
            load_evaluator_weights(tmp_path / "ghost.yaml", "a")

    def test_gate_大小写与空白容错(self, tmp_path):
        path = self._write(tmp_path, {"evaluator_weights": {"a": {"rule.g": " GATE "}}})
        assert load_evaluator_weights(path, "a") == {"rule.g": 0.0}


class Test快照冻结配合:
    def test_读出权重可冻结进_config_snapshot(self, tree_store, make_tree):
        """weights 读入快照后落盘即冻结，读回逐字节一致（US3 验收场景 3）。"""
        weights = load_evaluator_weights(MOVIE_YAML, "visual")
        tree = make_tree(config_snapshot={"evaluator_weights": weights})
        tree_store.create_tree(tree)
        got = tree_store.trees_by(project_id=tree.project_id, agent_id=tree.agent_id)[0]
        assert got.config_snapshot["evaluator_weights"] == weights
