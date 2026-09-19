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


@pytest.fixture()
def visual_config():
    """视觉形态配置夹具：直接读 configs/movie.yaml 的 visual 段。"""
    from pathlib import Path

    from agents.visual.config import VisualConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return VisualConfig.from_yaml(path)


@pytest.fixture()
def gen_jobs_engine():
    """视觉运营表夹具：SQLite 内存库建 visual_gen_jobs（可变表）。"""
    from sqlalchemy import create_engine

    from agents.visual.db import create_gen_jobs_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_gen_jobs_schema(engine)
    return engine


@pytest.fixture()
def make_clip_file():
    """程序化小片段工厂：320x240@8fps 共 16 帧（渐变 + 移动色块），写 mp4。

    测试基础设施自包含实现（不依赖 SimulatedVideoGen）；seed 控制图案参数。
    """
    import imageio.v3 as iio
    import numpy as np

    def _make(path, seed: int = 7):
        rng = np.random.default_rng(seed)
        brightness = 60 + int(seed % 100)
        speed = 2 + seed % 5
        frames = np.zeros((16, 240, 320, 3), dtype=np.uint8)
        ys, xs = np.mgrid[0:240, 0:320]
        for t in range(16):
            frame = np.clip(brightness + (xs * 0.2 + t * 3) % 120, 0, 255)
            block = (
                (xs >= (t * speed) % 280) & (xs < (t * speed) % 280 + 40) & (ys >= 100) & (ys < 140)
            )
            frame = np.where(block, 220, frame)
            noise = rng.integers(0, 3, size=frame.shape) if seed % 2 else 0
            frames[t] = np.stack([np.clip(frame + noise, 0, 255)] * 3, axis=-1).astype(np.uint8)
        iio.imwrite(path, frames, fps=8, codec="libx264")
        return path

    return _make


@pytest.fixture()
def clip_file(tmp_path, make_clip_file):
    """单个程序化片段文件（默认 seed=7）。"""
    return make_clip_file(tmp_path / "clip.mp4")


@pytest.fixture()
def dream_config():
    """做梦形态配置夹具：直接读 configs/movie.yaml 的 dreaming 段。"""
    from dreaming.config import DreamConfig

    return DreamConfig.from_yaml(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    )


@pytest.fixture()
def champion_source():
    """冠军策略源码工厂（静态检查必过的合法策略）。"""

    def _make(grid=((0.3,), (0.7,)), max_rounds=6):
        grid_literal = ", ".join(f'{{"temperature": {t}}}' for (t,) in grid)
        return f'''
class Policy:
    """冠军策略（做梦轮次的父版本）。"""

    def solve(self, env, budget):
        grid = [{grid_literal}]
        best_id = None
        probes = 0
        for round_no in range({max_rounds}):
            observations = env.observed()
            scored = sorted(
                ((o.score, o.node_id) for o in observations.values() if o.score is not None),
                key=lambda item: (-item[0], item[1]),
            )
            if scored:
                best_id = scored[0][1]
            if best_id is None or probes >= budget.max_probes:
                break
            env.probe(best_id, grid[round_no % len(grid)])
            probes += 1
        return best_id or ""
'''

    return _make


@pytest.fixture()
def multi_tree_pool(tree_store, build_historical_tree):
    """多时间树池工厂：N 棵结构不同分的树（uuid7 tree_id 时间有序，后者更晚）。

    返回 (trees, store)；每棵树 root + 两个 gen_params 档子节点（得分逐树递增，
    便于断言回放曲线差异与 validation 分树）。
    """
    from core.tree.models import CostRecord, NodeStatus

    def _make(n: int = 3):
        trees = []
        for t in range(n):
            tree, _ = build_historical_tree(
                [
                    (None, {}, 0.3 + t * 0.05, NodeStatus.EVALUATED, CostRecord()),
                    (
                        0,
                        {"temperature": 0.3},
                        0.5 + t * 0.05,
                        NodeStatus.EVALUATED,
                        CostRecord(llm_calls=1),
                    ),
                    (
                        0,
                        {"temperature": 0.7},
                        0.6 + t * 0.05,
                        NodeStatus.EVALUATED,
                        CostRecord(llm_calls=1),
                    ),
                ],
                project_id="dream-pool",
                agent_id="agent-dream",
                policy_version="a1b2c3d4e5f6",
            )
            trees.append(tree)
        return trees, tree_store

    return _make
