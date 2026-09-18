"""生成参数匹配与观测投影单测（US1 / T111）。

- 规范化 JSON 精确相等（决策 5）：键序无关、嵌套递归、缺字段即不匹配；
- 白名单投影不泄漏白名单外字段（不变量 1）；
- ProbeResult.unknown() 零信息零成本（FR-004）。
"""

import pytest

from core.replay.errors import ValidationError
from core.replay.matching import node_gen_params, normalize_params, params_match
from core.replay.observation import (
    ProbeResult,
    observation_whitelist,
    project_observation,
    sum_costs,
)
from core.tree.models import CostRecord


class Test规范化匹配:
    def test_键序无关(self):
        assert params_match({"a": 1, "b": 2}, {"b": 2, "a": 1})

    def test_嵌套字典键序无关(self):
        assert params_match({"x": {"p": 1, "q": [1, 2]}}, {"x": {"q": [1, 2], "p": 1}})

    def test_列表顺序敏感(self):
        assert not params_match({"x": [1, 2]}, {"x": [2, 1]})

    def test_缺字段即不匹配(self):
        """决策 5：不做缺省补齐——缺字段 = 不匹配（UNKNOWN）。"""
        assert not params_match({"temperature": 0.7}, {"temperature": 0.7, "batch_size": 1})

    def test_值不同不匹配(self):
        assert not params_match({"temperature": 0.7}, {"temperature": 0.70 + 1e-9})

    def test_unicode_与空白稳定(self):
        assert params_match({"提示": " 中文 "}, {"提示": " 中文 "})
        assert not params_match({"提示": "中文"}, {"提示": "中文 "})

    def test_规范化产出确定性字符串(self):
        assert normalize_params({"b": 2, "a": 1}) == '{"a":1,"b":2}'

    def test_非_dict_报_ValidationError(self):
        with pytest.raises(ValidationError):
            normalize_params([1, 2])

    def test_非_JSON_值语义报_ValidationError(self):
        with pytest.raises(ValidationError):
            normalize_params({"f": object()})


class Test节点生成参数提取:
    def test_从_observation_context_提取(self, make_node):
        node = make_node(observation_context={"gen_params": {"temperature": 0.3}})
        assert node_gen_params(node) == {"temperature": 0.3}

    def test_缺省为空_dict(self, make_node):
        assert node_gen_params(make_node(observation_context={})) == {}

    def test_非_dict_值视为空(self, make_node):
        assert node_gen_params(make_node(observation_context={"gen_params": "oops"})) == {}


class Test白名单投影:
    def test_仅白名单字段可见(self, make_node):
        node = make_node(
            observation_context={"gen_params": {"t": 1}, "secret": "不得泄露"},
        )
        obs = project_observation(node, ["gen_params"])
        assert obs.fields == {"gen_params": {"t": 1}}
        assert "secret" not in obs.fields

    def test_空白名单零泄露(self, make_node):
        node = make_node(observation_context={"gen_params": {"t": 1}})
        assert project_observation(node, []).fields == {}

    def test_投影携带得分与成本(self, make_node):
        node = make_node(score=0.7, cost=CostRecord(llm_calls=2))
        obs = project_observation(node, [])
        assert obs.node_id == node.node_id
        assert obs.depth == node.depth
        assert obs.score == 0.7
        assert obs.cost.llm_calls == 2

    def test_投影不携带可定位未揭示节点的字段(self, make_node):
        """Observation 无 parent_id / tree_id / artifact_hash / eval_breakdown。"""
        obs = project_observation(make_node(), ["gen_params"])
        for leaked in ("parent_id", "tree_id", "artifact_hash", "eval_breakdown"):
            assert not hasattr(obs, leaked)


class Test白名单读取:
    def test_从快照读取(self):
        assert observation_whitelist({"observation_fields": ["a", "b"]}) == ("a", "b")

    def test_缺省为空(self):
        assert observation_whitelist({}) == ()

    def test_类型非法视为空(self):
        assert observation_whitelist({"observation_fields": "not-a-list"}) == ()


class TestProbeResult与成本:
    def test_unknown_零信息零成本(self):
        result = ProbeResult.unknown()
        assert result.status == "unknown"
        assert result.nodes == []
        assert result.virtual_cost == CostRecord()

    def test_sum_costs_逐项相加(self):
        total = sum_costs(
            [CostRecord(llm_calls=1, llm_tokens=10), CostRecord(llm_calls=2, llm_tokens=20)]
        )
        assert total.llm_calls == 3
        assert total.llm_tokens == 30

    def test_sum_costs_空序列(self):
        assert sum_costs([]) == CostRecord()
