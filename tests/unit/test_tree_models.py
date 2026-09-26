"""领域模型单测（US1 / T011）。

覆盖 data-model.md §1 的构造期校验：frozen 不可改、score/status 一致性、
depth 递推、非空字段、artifact_hash 格式、CostRecord 分量非负、DiscoveryTree 快照必填。
"""

from dataclasses import FrozenInstanceError

import pytest

from core.tree.errors import ValidationError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id


def _node_kwargs(**overrides):
    fields = {
        "node_id": new_id(),
        "tree_id": new_id(),
        "parent_id": None,
        "depth": 0,
        "agent_id": "agent-a",
        "policy_version": "a1b2c3d4e5f6",
        "prompt": "",
        "observation_context": {"round": 1},
        "artifact_hash": "ab" * 32,
        "eval_breakdown": {"rule.x@1.0.0": {"score": 0.8}},
        "score": 0.8,
        "cost": CostRecord(),
        "status": NodeStatus.EVALUATED,
        "created_at": 1700000000.0,
    }
    fields.update(overrides)
    return fields


class TestFrozen:
    def test_tree_node_不可变(self):
        node = TreeNode(**_node_kwargs())
        with pytest.raises(FrozenInstanceError):
            node.score = 0.1  # type: ignore[misc]

    def test_cost_record_不可变(self):
        cost = CostRecord()
        with pytest.raises(FrozenInstanceError):
            cost.llm_calls = 9  # type: ignore[misc]

    def test_discovery_tree_不可变(self):
        tree = DiscoveryTree(
            tree_id=new_id(),
            project_id="p",
            agent_id="a",
            policy_version="v",
            root_id=new_id(),
            node_ids=[],
            config_snapshot={"k": 1},
        )
        with pytest.raises(FrozenInstanceError):
            tree.project_id = "q"  # type: ignore[misc]


class TestScoreStatus一致性:
    def test_evaluated_缺_score_拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(score=None, status=NodeStatus.EVALUATED))

    @pytest.mark.parametrize("bad_score", [-0.01, 1.01, 2.0])
    def test_evaluated_score_越界拒构造(self, bad_score):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(score=bad_score, status=NodeStatus.EVALUATED))

    def test_failed_带_score_拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(score=0.5, status=NodeStatus.FAILED))

    def test_failed_score_none_合法(self):
        node = TreeNode(**_node_kwargs(score=None, status=NodeStatus.FAILED))
        assert node.score is None
        assert node.status is NodeStatus.FAILED

    def test_planned_可构造但_score_必须为_none(self):
        node = TreeNode(**_node_kwargs(score=None, status=NodeStatus.PLANNED))
        assert node.status is NodeStatus.PLANNED

    def test_planned_带_score_拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(score=0.5, status=NodeStatus.PLANNED))


class TestDepth递推:
    def test_根节点_depth_非零拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(parent_id=None, depth=1))

    def test_非根节点_depth_零拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(parent_id=new_id(), depth=0))

    def test_depth_负数拒构造(self):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(depth=-1))

    def test_child_of_递推_depth(self):
        parent = TreeNode(**_node_kwargs())
        kwargs = _node_kwargs()
        del kwargs["parent_id"], kwargs["depth"], kwargs["tree_id"]
        child = TreeNode.child_of(parent, **kwargs)
        assert child.parent_id == parent.node_id
        assert child.depth == parent.depth + 1
        assert child.tree_id == parent.tree_id

    def test_child_of_跨树字段由父节点接管(self):
        # tree_id/depth/parent_id 由 child_of 强制继承，显式传入即拒绝
        parent = TreeNode(**_node_kwargs())
        with pytest.raises(ValidationError):
            TreeNode.child_of(parent, **_node_kwargs(tree_id=new_id(), depth=1))

    def test_child_of_拒绝显式_parent_id(self):
        parent = TreeNode(**_node_kwargs())
        with pytest.raises(ValidationError):
            TreeNode.child_of(parent, **_node_kwargs(parent_id=new_id(), depth=1))


class Test字段校验:
    @pytest.mark.parametrize("field", ["node_id", "tree_id", "agent_id", "policy_version"])
    def test_必填字符串为空拒构造(self, field):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(**{field: ""}))

    @pytest.mark.parametrize("bad_hash", ["xyz", "ab" * 31, "zz" * 32, "AB" * 32])
    def test_artifact_hash_格式非法拒构造(self, bad_hash):
        with pytest.raises(ValidationError):
            TreeNode(**_node_kwargs(artifact_hash=bad_hash))

    def test_cost_分量负数拒构造(self):
        with pytest.raises(ValidationError):
            CostRecord(generation_api_cost_usd=-0.1)

    def test_cost_默认全零(self):
        cost = CostRecord()
        assert cost.llm_calls == 0
        assert cost.wall_clock_seconds == 0.0

    def test_new_id_生成_uuid7(self):
        import uuid

        parsed = uuid.UUID(new_id())
        assert parsed.version == 7


class TestLlmBreakdown:
    """llm_breakdown 扩展字段（功能 022 / plan D1~D2）：结构校验、asdict 往返、旧行读回兼容。"""

    def _entry(self, calls=2, prompt=40, completion=20, cost=0.01):
        return {
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cost_usd": cost,
        }

    def _breakdown(self, **entry_overrides):
        return {"screenwriter": {"p-cheap": self._entry(**entry_overrides)}}

    def test_默认为空合法(self):
        cost = CostRecord()
        assert cost.llm_breakdown == {}

    def test_分解Σ不超总量合法(self):
        cost = CostRecord(llm_calls=3, llm_tokens=100, llm_breakdown=self._breakdown())
        assert cost.llm_breakdown["screenwriter"]["p-cheap"]["calls"] == 2

    def test_Σ等于总量合法(self):
        # 无缓存命中时等号成立（plan D2）
        CostRecord(llm_calls=2, llm_tokens=60, llm_breakdown=self._breakdown())

    @pytest.mark.parametrize(
        "key,bad",
        [("calls", -1), ("prompt_tokens", -1), ("completion_tokens", -1), ("cost_usd", -0.01)],
    )
    def test_分量负值拒构造(self, key, bad):
        bd = self._breakdown()
        bd["screenwriter"]["p-cheap"][key] = bad
        with pytest.raises(ValidationError):
            CostRecord(llm_calls=3, llm_tokens=100, llm_breakdown=bd)

    def test_角色键为空拒构造(self):
        with pytest.raises(ValidationError):
            CostRecord(
                llm_calls=1,
                llm_tokens=1,
                llm_breakdown={"": {"p": self._entry(calls=1, prompt=1, completion=0)}},
            )

    def test_档案键为空拒构造(self):
        with pytest.raises(ValidationError):
            CostRecord(
                llm_calls=1,
                llm_tokens=1,
                llm_breakdown={"r": {"": self._entry(calls=1, prompt=1, completion=0)}},
            )

    def test_条目缺键拒构造(self):
        with pytest.raises(ValidationError):
            CostRecord(llm_calls=1, llm_tokens=1, llm_breakdown={"r": {"p": {"calls": 1}}})

    def test_calls_超和拒构造(self):
        with pytest.raises(ValidationError):
            CostRecord(llm_calls=1, llm_tokens=100, llm_breakdown=self._breakdown())

    def test_tokens_超和拒构造(self):
        with pytest.raises(ValidationError):
            # 分解 tokens 合计 40 + 20 = 60 > llm_tokens=59
            CostRecord(llm_calls=5, llm_tokens=59, llm_breakdown=self._breakdown())

    def test_cost_usd_不校验总额(self):
        # D2 口径局限如实登记：六字段无对应标量，cost_usd 总额不做超和校验
        CostRecord(llm_calls=2, llm_tokens=60, llm_breakdown=self._breakdown(cost=999.0))

    def test_asdict_往返(self):
        from dataclasses import asdict

        cost = CostRecord(llm_calls=2, llm_tokens=60, llm_breakdown=self._breakdown())
        assert CostRecord(**asdict(cost)) == cost

    def test_旧dict缺键读回_默认且不报错(self):
        # 历史行 JSON 无 llm_breakdown 键：CostRecord(**row.cost) 读回默认空映射
        old_row = {
            "llm_calls": 2,
            "llm_tokens": 60,
            "generation_api_calls": 1,
            "generation_api_cost_usd": 0.5,
            "human_review_minutes": 0.0,
            "wall_clock_seconds": 1.5,
        }
        cost = CostRecord(**old_row)
        assert cost.llm_breakdown == {}
        assert cost.llm_calls == 2


class TestDiscoveryTree:
    def _tree_kwargs(self, **overrides):
        fields = {
            "tree_id": new_id(),
            "project_id": "p",
            "agent_id": "a",
            "policy_version": "v",
            "root_id": new_id(),
            "node_ids": [],
            "config_snapshot": {"evaluator_weights": {}},
        }
        fields.update(overrides)
        return fields

    @pytest.mark.parametrize("field", ["tree_id", "project_id", "agent_id", "policy_version"])
    def test_必填字段为空拒构造(self, field):
        with pytest.raises(ValidationError):
            DiscoveryTree(**self._tree_kwargs(**{field: ""}))

    def test_config_snapshot_为空拒构造(self):
        with pytest.raises(ValidationError):
            DiscoveryTree(**self._tree_kwargs(config_snapshot={}))

    def test_合法构造(self):
        tree = DiscoveryTree(**self._tree_kwargs())
        assert tree.project_id == "p"
