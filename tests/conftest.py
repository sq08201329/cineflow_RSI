"""测试公共夹具。

- sqlite_engine：SQLite 内存库 + 建表 + 不可变触发器（单元测试专用）；
- tree_store：基于内存库的 TreeStore；
- artifact_store：临时目录 LocalArtifactStore（单元测试专用，契约限定 Local 仅测试用）。

桩评估器不在此定义（唯一定义来源为 T025 的 tests/stubs.py）。
"""

import time

import pytest
from sqlalchemy import create_engine


@pytest.fixture()
def sqlite_engine():
    """SQLite 内存引擎：建表并安装双方言触发器中的 SQLite 版本。"""
    from core.tree.db import create_schema  # 延迟导入：TDD 期间实现尚未落地

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return engine


@pytest.fixture()
def tree_store(sqlite_engine):
    from core.tree.store import create_tree_store

    return create_tree_store(sqlite_engine)


@pytest.fixture()
def artifact_store(tmp_path):
    from core.tree.artifacts import LocalArtifactStore

    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.fixture()
def make_tree():
    """DiscoveryTree 工厂：字段可被调用方覆盖。"""
    from core.tree.models import DiscoveryTree, new_id

    def _make(**overrides):
        root_id = overrides.pop("root_id", new_id())
        fields = {
            "tree_id": new_id(),
            "project_id": "proj-test",
            "agent_id": "agent-test",
            "policy_version": "a1b2c3d4e5f6",
            "root_id": root_id,
            "node_ids": [root_id],
            "config_snapshot": {"evaluator_weights": {"rule.x": "gate"}},
        }
        fields.update(overrides)
        return DiscoveryTree(**fields)

    return _make


@pytest.fixture()
def make_node():
    """TreeNode 工厂：默认构造一个已评估节点，字段可覆盖。"""
    from core.tree.models import CostRecord, NodeStatus, TreeNode, new_id

    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        fields = {
            "node_id": new_id(),
            "tree_id": "tree-placeholder",
            "parent_id": None,
            "depth": 0,
            "agent_id": "agent-test",
            "policy_version": "a1b2c3d4e5f6",
            "prompt": "",
            "observation_context": {},
            # 64 位十六进制 BLAKE3 占位
            "artifact_hash": "ab" * 32,
            "eval_breakdown": {"rule.x@1.0.0": {"score": 0.8}},
            "score": 0.8,
            "cost": CostRecord(llm_calls=1, wall_clock_seconds=0.5),
            "status": NodeStatus.EVALUATED,
            # 递增时间戳，保证 children() 的 created_at 升序可断言
            "created_at": time.time() + counter["n"] * 1e-6,
        }
        fields.update(overrides)
        return TreeNode(**fields)

    return _make


@pytest.fixture()
def build_historical_tree(tree_store, make_tree, make_node):
    """小树构建工厂（功能 002 回放夹具）。

    nodes_spec 元素：(parent_idx | None, gen_params, score | None, status, cost)。
    根节点 parent_idx=None；depth 自动递推；gen_params 记入 observation_context
    （回放精确匹配的依据）；观测白名单默认含 gen_params。
    返回 (tree, node_ids 按 spec 顺序)。
    """
    from core.tree.models import CostRecord, NodeStatus, new_id

    def _build(
        nodes_spec,
        *,
        project_id="proj-replay",
        agent_id="agent-replay",
        policy_version="a1b2c3d4e5f6",
        observation_fields=("gen_params",),
    ):
        tree = make_tree(
            project_id=project_id,
            agent_id=agent_id,
            policy_version=policy_version,
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": list(observation_fields),
            },
        )
        tree_store.create_tree(tree)

        node_ids: list[str] = []
        depths: list[int] = []
        for i, (parent_idx, gen_params, score, status, cost) in enumerate(nodes_spec):
            parent_id = None if parent_idx is None else node_ids[parent_idx]
            depth = 0 if parent_idx is None else depths[parent_idx] + 1
            node = make_node(
                node_id=tree.root_id if i == 0 else new_id(),
                tree_id=tree.tree_id,
                parent_id=parent_id,
                depth=depth,
                agent_id=agent_id,
                policy_version=policy_version,
                observation_context={"gen_params": gen_params},
                eval_breakdown={}
                if status is NodeStatus.FAILED
                else {"rule.x@1": {"score": score}},
                score=score,
                cost=cost or CostRecord(),
                status=status,
                created_at=float(i + 1),
            )
            tree_store.append_node(node)
            node_ids.append(node.node_id)
            depths.append(depth)
        return tree, node_ids

    return _build


@pytest.fixture()
def make_trajectory():
    """录制轨迹夹具生成器（回放轨迹报告/无偏性验收用）。"""
    from core.replay.trajectory import ReplayTrajectory, TrajectoryStatus
    from core.tree.models import CostRecord

    def _make(**overrides):
        fields = {
            "policy_version": "a1b2c3d4e5f6",
            "best_score_curve": [0.4, 0.5],
            "probe_count": 1,
            "effective_sequential_rounds": 1.0,
            "total_cost": CostRecord(),
            "final_node_id": "n1",
            "status": TrajectoryStatus.COMPLETED,
            "diagnostics": {},
        }
        fields.update(overrides)
        return ReplayTrajectory(**fields)

    return _make


@pytest.fixture()
def campaigns_engine():
    """运营表夹具：SQLite 内存库建 promo_campaigns（可变表，无 immutable 触发器）。"""
    from sqlalchemy import create_engine

    from agents.promo.db import create_campaigns_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_campaigns_schema(engine)
    return engine


@pytest.fixture()
def promo_config():
    """宣发形态配置夹具：直接读 configs/movie.yaml 的 promo 段（真实配置路径）。"""
    from pathlib import Path

    import yaml

    from agents.promo.config import PromoConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return PromoConfig.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))


@pytest.fixture()
def mock_gateway(promo_config):
    """Mock 网关夹具：确定性后端 + 形态价目表；测试可用 sleep 注入消除真实退避。"""
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway

    return LLMGateway(MockBackend(), price_book=promo_config.model_prices, sleep=lambda _: None)


@pytest.fixture()
def simulated_platform(promo_config):
    """确定性模拟平台夹具（决策 3：物料哈希种子，逐字节可复现）。"""
    from agents.promo.platform.simulated import SimulatedPlatform

    return SimulatedPlatform(promo_config.simulated_platform)
