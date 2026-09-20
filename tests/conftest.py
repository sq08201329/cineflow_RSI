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


@pytest.fixture()
def build_calibration_tree(tree_store, make_tree, make_node):
    """多评估器夹具树工厂（功能 010 校准）：节点 eval_breakdown 带多分量。

    nodes_spec 元素：(score, breakdown)；breakdown 形如
    {"proxy.aesthetic@1.0.0": {"score": 0.8}, "judge.cinematic@1.0.0": {"score": 0.7}}。
    根节点 parent 为 None，其余挂在根下；created_at = base_created_at + 序号
    （周期过滤测试用 base_created_at 落入目标窗口）。
    返回 (tree, node_ids 按 spec 顺序)。
    """
    from core.tree.models import CostRecord, NodeStatus, new_id

    def _build(
        nodes_spec, *, agent_id="agent-calib", policy_version="a1b2c3d4e5f6", base_created_at=0.0
    ):
        tree = make_tree(agent_id=agent_id, policy_version=policy_version)
        tree_store.create_tree(tree)

        node_ids: list[str] = []
        for i, (score, breakdown) in enumerate(nodes_spec):
            node = make_node(
                node_id=tree.root_id if i == 0 else new_id(),
                tree_id=tree.tree_id,
                parent_id=None if i == 0 else tree.root_id,
                depth=0 if i == 0 else 1,
                agent_id=agent_id,
                policy_version=policy_version,
                eval_breakdown=breakdown,
                score=score,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=base_created_at + float(i + 1),
            )
            tree_store.append_node(node)
            node_ids.append(node.node_id)
        return tree, node_ids

    return _build


@pytest.fixture()
def make_anchor_entry():
    """人评录入条目工厂（功能 010）：{node_id, score, reviewer}，字段可覆盖。"""
    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        fields = {
            "node_id": "node-placeholder",
            "score": 0.8,
            "reviewer": f"reviewer-{counter['n']}",
        }
        fields.update(overrides)
        return fields

    return _make


@pytest.fixture()
def make_platform_backfill(campaigns_engine):
    """promo 回流数据工厂（功能 010）：ingested 运营记录 + 指标快照落库。

    metrics 对齐 ops/ingest_metrics.py 的真实落盘结构：
    {"platform_metrics": MetricSnapshot asdict, "material": {...}}；
    渠道标识取 material.platform，字段可覆盖。
    """
    from sqlalchemy import insert

    from agents.promo.db import promo_campaigns

    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        snapshot = {
            "ctr": 0.05,
            "completion_rate": 0.6,
            "conversions": 12,
            "impressions": 1000,
            "clicks": 50,
            "platform_timestamp": 1000.0 + counter["n"],
            "data_version": "v1",
        }
        snapshot.update(overrides.pop("snapshot", {}))
        material = {
            "platform": "douyin",
            "artifact_hash": f"{counter['n']:064x}",
            "kind": "poster",
            "tags": [],
        }
        material.update(overrides.pop("material", {}))
        fields = {
            "campaign_id": f"camp-{counter['n']}",
            "round_id": "round-calib",
            "material_id": f"mat-{counter['n']}",
            "node_id": f"node-{counter['n']:012d}",
            "status": "ingested",
            "spent_usd": 1.0,
            "external_id": f"ext-{counter['n']}",
            "metrics": {"platform_metrics": snapshot, "material": material},
            "created_at": 1000.0,
            "updated_at": 1000.0 + counter["n"],
        }
        fields.update(overrides)
        with campaigns_engine.begin() as conn:
            conn.execute(insert(promo_campaigns).values(**fields))
        return fields

    return _make


@pytest.fixture()
def calibration_data_dir(tmp_path):
    """calibration/ 临时数据目录夹具：rounds/ledger/reports/proposals 四层落盘结构。"""
    base = tmp_path / "calibration"
    for sub in ("rounds", "ledger", "reports", "proposals"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def anchors_engine():
    """锚点表夹具：SQLite 内存库建 calibration_anchors + INSERT-only 触发器。"""
    from sqlalchemy import create_engine

    from core.calibration.db import create_anchor_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_anchor_schema(engine)
    return engine


@pytest.fixture()
def make_timing_sheet():
    """TimingSheet 工厂（功能 006）：variant 取 valid（默认）/overlap/out_of_bounds。

    - valid：两条不重叠台词 + 两个音效事件；
    - overlap：第二条台词与第一条时间区间重叠（执行前必须拒绝）；
    - out_of_bounds：负时间戳（执行前必须拒绝）。
    字段可被调用方整体覆盖（utterances/effects）。
    """
    from agents.sound.timing import TimingSheet

    def _make(variant: str = "valid", **overrides):
        utterances = [
            {"text": "你终于来了。", "start_ms": 0, "end_ms": 1000},
            {"text": "我等了很久。", "start_ms": 1200, "end_ms": 2000},
        ]
        effects = [
            {"kind": "door_slam", "at_ms": 1100},
            {"kind": "footsteps", "at_ms": 2100},
        ]
        if variant == "overlap":
            utterances[1]["start_ms"] = 800  # 与第一条 [0,1000) 重叠
        elif variant == "out_of_bounds":
            effects[0]["at_ms"] = -50  # 负时间戳越界
        fields = {"utterances": utterances, "effects": effects}
        fields.update(overrides)
        return TimingSheet(**fields)

    return _make


@pytest.fixture()
def make_sound_gen_params():
    """声学属性可控的模拟生成参数工厂（功能 006）。

    loudness_gain_db / event_times_ms / cer_injected / emotion_vector 全部显式可控
    （评估器夹具注入用）；seed 决定波形细节（确定性）；gen_type ∈ {tts, sfx, music}。
    """
    counter = {"n": 0}

    def _make(**overrides):
        counter["n"] += 1
        fields = {
            "gen_type": "tts",
            "seed": 7 + counter["n"],
            "duration_s": 2.0,
            "loudness_gain_db": 0.0,  # 注入响度增益（rule.loudness_compliance 夹具）
            "event_times_ms": [0.0, 1000.0],  # 注入语音/音效事件时间（rule.av_sync 夹具）
            "cer_injected": 0.0,  # 注入错字率 ∈ [0,1]（proxy.asr_transcript 夹具）
            "emotion_vector": [0.5, 0.5],  # 注入情绪向量（proxy.emotion_music_match 夹具）
        }
        fields.update(overrides)
        return fields

    return _make


@pytest.fixture()
def sound_jobs_engine():
    """声音运营表夹具：SQLite 内存库建 sound_gen_jobs（可变表，无 immutable 触发器）。"""
    from sqlalchemy import create_engine

    from agents.sound.db import create_gen_jobs_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_gen_jobs_schema(engine)
    return engine


@pytest.fixture()
def sound_data_dir(tmp_path):
    """声音临时数据目录夹具：artifacts（wav 工件）/rounds（轮次收口落盘）两层结构。"""
    base = tmp_path / "sound"
    for sub in ("artifacts", "rounds"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def sound_config():
    """声音形态配置夹具：直接读 configs/movie.yaml 的 sound 段（真实配置路径）。"""
    from pathlib import Path

    from agents.sound.config import SoundConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return SoundConfig.from_yaml(path)


# ---------------------------------------------------------------------------
# 功能 007（剪辑闭环）夹具：镜头库/场景分区/EDL（合法 + 五类非法变体）/
# 素材帧/音轨/运营表/临时目录/形态配置
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_shot_library():
    """镜头库工厂（功能 007）：6 镜头 3 分区归属 + 1 个未分区镜头（跨分区变体用）。

    分区归属：scene-a = {shot-1, shot-2}，scene-b = {shot-3, shot-4}，
    scene-c = {shot-5, shot-6}；shot-orphan 在库但不归属 SceneStructure 任何分区
    （EDL 跨分区非法变体的素材）。音轨集合两条（bgm-01/voice-01）。
    字段可被调用方覆盖（shots/audio_tracks）。
    """
    from agents.editing.shots import ShotEntry, ShotLibrary

    def _make(**overrides):
        durations = {
            "shot-1": 4000,
            "shot-2": 4000,
            "shot-3": 5000,
            "shot-4": 4000,
            "shot-5": 6000,
            "shot-6": 4000,
            "shot-orphan": 4000,  # ≥ 合法 EDL 变体的 out_ms，保证跨分区变体精确落在第③层
        }
        scenes = {
            "shot-1": "scene-a",
            "shot-2": "scene-a",
            "shot-3": "scene-b",
            "shot-4": "scene-b",
            "shot-5": "scene-c",
            "shot-6": "scene-c",
            "shot-orphan": "scene-x",
        }
        shots = [
            ShotEntry(
                shot_id=sid,
                artifact_hash=f"{i + 1:064x}",
                duration_ms=durations[sid],
                scene_id=scenes[sid],
                metadata={"source": "fixture"},
            )
            for i, sid in enumerate(durations)
        ]
        fields = {"shots": shots, "audio_tracks": ("bgm-01", "voice-01")}
        fields.update(overrides)
        return ShotLibrary(**fields)

    return _make


@pytest.fixture()
def make_scene_structure(make_shot_library):
    """SceneStructure 工厂（功能 007）：与默认镜头库一致的有序分区结构。

    variant=valid（默认）/duplicate（shot_ids 跨分区重复）/unknown（引用不存在镜头）/
    mismatched（shot 归属与 ShotEntry.scene_id 不一致）。
    """
    from agents.editing.shots import Scene, SceneStructure

    def _make(variant: str = "valid", library=None, **overrides):
        library = library if library is not None else make_shot_library()
        scenes = [
            Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-2")),
            Scene(scene_id="scene-b", shot_ids=("shot-3", "shot-4")),
            Scene(scene_id="scene-c", shot_ids=("shot-5", "shot-6")),
        ]
        if variant == "duplicate":
            scenes[1] = Scene(scene_id="scene-b", shot_ids=("shot-2", "shot-3"))
        elif variant == "unknown":
            scenes[0] = Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-999"))
        elif variant == "mismatched":
            scenes[0] = Scene(scene_id="scene-a", shot_ids=("shot-1", "shot-3"))
        fields = {"scenes": scenes, "shot_library": library}
        fields.update(overrides)
        return SceneStructure(**fields)

    return _make


@pytest.fixture()
def make_edl():
    """EDL 工厂（功能 007）：合法 + 五类非法变体（执行前四层校验各拒绝一类）。

    - valid（默认）：5 镜头 3 分区有序，同区衔接用叠化（forbid_jump_cut_within_scene
      约束下合法），跨区用 cut；带一条音轨；
    - unknown_ref：引用不存在镜头（第①层拒绝）；
    - out_of_bounds：出点越界（out_ms > 镜头时长，第②层拒绝）；
    - cross_partition：引用未分区镜头 shot-orphan（第③层拒绝）；
    - scene_disorder：场景顺序回退（scene-b → scene-a，第③层拒绝）；
    - illegal_transition：转场类型不在规则库 allowed（第④层拒绝）。
    """
    from agents.editing.edl import EditDecisionList

    _VALID_CLIPS = [
        {
            "shot_id": "shot-1",
            "in_ms": 500,
            "out_ms": 3500,
            "transition": {"type": "dissolve", "duration_ms": 750},  # 帧网格对齐（8fps×750ms=6 帧）
        },  # 同区衔接须叠化
        {
            "shot_id": "shot-2",
            "in_ms": 0,
            "out_ms": 3000,
            "transition": {"type": "cut", "duration_ms": 0},
        },  # 跨区 cut 合法
        {
            "shot_id": "shot-3",
            "in_ms": 0,
            "out_ms": 4000,
            "transition": {"type": "dissolve", "duration_ms": 500},
        },
        {
            "shot_id": "shot-4",
            "in_ms": 200,
            "out_ms": 3200,
            "transition": {"type": "cut", "duration_ms": 0},
        },
        {
            "shot_id": "shot-5",
            "in_ms": 0,
            "out_ms": 5000,
            "transition": {"type": "cut", "duration_ms": 0},
        },
    ]

    def _make(variant: str = "valid", **overrides):
        import copy

        clips = copy.deepcopy(_VALID_CLIPS)
        if variant == "unknown_ref":
            clips[0] = {**clips[0], "shot_id": "shot-999"}
        elif variant == "out_of_bounds":
            clips[1] = {**clips[1], "out_ms": 4500}  # shot-2 时长 4000ms
        elif variant == "cross_partition":
            clips[0] = {**clips[0], "shot_id": "shot-orphan"}
        elif variant == "scene_disorder":
            clips[0], clips[1] = (
                {**clips[2], "transition": {"type": "cut", "duration_ms": 0}},
                clips[0],
            )  # scene-b 镜头提到 scene-a 之前
        elif variant == "illegal_transition":
            clips[0] = {**clips[0], "transition": {"type": "wipe", "duration_ms": 500}}
        fields = {
            "clips": clips,
            "audio": [{"track_ref": "bgm-01", "at_ms": 0, "gain": 0.8}],
        }
        fields.update(overrides)
        return EditDecisionList(**fields)

    return _make


@pytest.fixture()
def make_shot_frames(make_shot_library):
    """素材帧夹具（功能 007）：shot_id → numpy 帧序列（确定性程序化渐变帧）。

    帧数 = 镜头时长 × fps / 1000（整除口径）；帧内容以 shot_id 哈希为种子，
    同镜头重算逐字节一致。小尺寸（64x48）控制单测渲染耗时。
    """
    import blake3
    import numpy as np

    def _make(library=None, *, fps: int = 8, width: int = 64, height: int = 48):
        library = library if library is not None else make_shot_library()
        ys, xs = np.mgrid[0:height, 0:width]
        frames_by_shot = {}
        for shot in library.shots:
            count = shot.duration_ms * fps // 1000
            seed = int(blake3.blake3(shot.shot_id.encode()).hexdigest()[:16], 16)
            brightness = 40 + seed % 120
            frames = np.zeros((count, height, width, 3), dtype=np.uint8)
            for t in range(count):
                gray = np.clip(brightness + (xs * 0.3 + t * 2) % 100, 0, 255)
                frames[t] = np.stack([gray, np.clip(gray + 10, 0, 255), gray], axis=-1).astype(
                    np.uint8
                )
            frames_by_shot[shot.shot_id] = frames
        return frames_by_shot

    return _make


@pytest.fixture()
def make_audio_tracks():
    """音轨采样夹具（功能 007）：track_ref → int16 单声道采样序列（确定性）。"""
    import numpy as np

    def _make(*, sample_rate: int = 16000, duration_s: float = 2.0):
        n = int(round(duration_s * sample_rate))
        t = np.arange(n, dtype=np.float64) / sample_rate
        return {
            "bgm-01": (0.3 * np.sin(2.0 * np.pi * 220.0 * t) * 32767.0).astype(np.int16),
            "voice-01": (0.2 * np.sin(2.0 * np.pi * 440.0 * t) * 32767.0).astype(np.int16),
        }

    return _make


@pytest.fixture()
def editing_jobs_engine():
    """剪辑运营表夹具：SQLite 内存库建 edit_render_jobs（可变表，无 immutable 触发器）。"""
    from sqlalchemy import create_engine

    from agents.editing.db import create_render_jobs_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_render_jobs_schema(engine)
    return engine


@pytest.fixture()
def editing_data_dir(tmp_path):
    """剪辑临时数据目录夹具：artifacts（mp4 工件）/rounds（轮次收口落盘）两层结构。"""
    base = tmp_path / "editing"
    for sub in ("artifacts", "rounds"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def editing_config():
    """剪辑形态配置夹具：直接读 configs/movie.yaml 的 editing 段（真实配置路径）。"""
    from pathlib import Path

    from agents.editing.config import EditingConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return EditingConfig.from_yaml(path)


# ---------------------------------------------------------------------------
# 功能 008（分镜闭环）夹具：剧本段落（3 场景 9 行含关键行/情绪/轴向基准）/
# ShotList（合法 + 四类非法变体）/素材帧/运营表/临时目录/形态配置
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_script_segment():
    """剧本段落工厂（功能 008）：3 场景 × 3 行，含关键行标注 + 情绪 + axis_base。

    - valid（默认）：scene-1/scene-2 轴向基准 A、scene-3 基准 B（场景切换重置轴线）；
      关键行 3 条（s1-l2 动作关键、s2-l1 台词关键、s3-l3 收尾动作关键）；
      s1-l3 无情绪标注（情绪缺失"不适用"路径的夹具）；
    - duplicate_line：跨场景重复行 id（行 id 唯一性违规）；
    - duplicate_scene：scene_id 重复（场景有序性违规）；
    - bad_emotion：情绪取值不在向量表内；
    - empty_scenes：空场景列表（剧本不足预检路径）。
    字段可被调用方整体覆盖（scenes）。
    """
    from agents.storyboard.script import ScriptSegment

    _SCENES = [
        {
            "scene_id": "scene-1",
            "axis_base": "A",
            "lines": [
                {
                    "line_id": "s1-l1",
                    "kind": "dialogue",
                    "text": "夜里三点，走廊的灯一闪一闪。",
                    "key": False,
                    "emotion": "tense",
                },
                {
                    "line_id": "s1-l2",
                    "kind": "action",
                    "text": "她握紧门把手，缓缓推开。",
                    "key": True,
                    "emotion": "tense",
                },
                {
                    "line_id": "s1-l3",
                    "kind": "dialogue",
                    "text": "有人在吗？",
                    "key": False,
                    "emotion": None,
                },
            ],
        },
        {
            "scene_id": "scene-2",
            "axis_base": "A",
            "lines": [
                {
                    "line_id": "s2-l1",
                    "kind": "dialogue",
                    "text": "我不该回来的。",
                    "key": True,
                    "emotion": "sorrow",
                },
                {
                    "line_id": "s2-l2",
                    "kind": "action",
                    "text": "窗外的雨敲打着玻璃。",
                    "key": False,
                    "emotion": "sorrow",
                },
                {
                    "line_id": "s2-l3",
                    "kind": "dialogue",
                    "text": "但你回来了。",
                    "key": False,
                    "emotion": "calm",
                },
            ],
        },
        {
            "scene_id": "scene-3",
            "axis_base": "B",
            "lines": [
                {
                    "line_id": "s3-l1",
                    "kind": "action",
                    "text": "两人隔着长长的走廊对视。",
                    "key": False,
                    "emotion": "awe",
                },
                {
                    "line_id": "s3-l2",
                    "kind": "dialogue",
                    "text": "这一次，我不会再走。",
                    "key": False,
                    "emotion": "joyful",
                },
                {
                    "line_id": "s3-l3",
                    "kind": "action",
                    "text": "镜头缓缓拉远，灯光暗下。",
                    "key": True,
                    "emotion": "sorrow",
                },
            ],
        },
    ]

    def _make(variant: str = "valid", **overrides):
        import copy

        scenes = copy.deepcopy(_SCENES)
        if variant == "duplicate_line":
            scenes[1]["lines"][0]["line_id"] = "s1-l1"  # 跨场景重复行 id
        elif variant == "duplicate_scene":
            scenes[1]["scene_id"] = "scene-1"  # 场景重复（顺序性违规）
        elif variant == "bad_emotion":
            scenes[0]["lines"][0]["emotion"] = "melancholic"  # 不在情绪向量表
        elif variant == "empty_scenes":
            scenes = []
        fields = {"scenes": scenes}
        fields.update(overrides)
        return ScriptSegment(**fields)

    return _make


@pytest.fixture()
def script_segment(make_script_segment):
    """默认合法剧本段落（3 场景 9 行）。"""
    return make_script_segment()


@pytest.fixture()
def make_shotlist(make_script_segment):
    """ShotList 工厂（功能 008）：合法 + 四类非法变体（执行前三层校验各拒绝一类）。

    - valid（默认）：3 场景 9 镜（每场景 3 镜），承接行与剧本一致、key 行逐条承接、
      档位全部在规则库枚举内、场景内同侧（轴线基准）、**景别序列满足镜头语法门禁**
      （相邻跳跃 ≤ max_size_jump=2、无同景别连续）——US2 五评估器全过路径的夹具；
    - unknown_line：承接剧本不存在的行 s1-l9（第①层拒绝）；
    - scene_uncovered：scene-2 全部镜头移除（第②层场景承接拒绝）；
    - key_line_uncovered：移除承接关键行 s2-l1 的镜头（scene-2 仍有镜头，第②层拒绝）；
    - size_out_of_range：景别档位 extreme_wide 不在规则库枚举（第③层拒绝）。
    """
    from agents.storyboard.shotlist import ShotList

    _VALID_SHOTS = [
        {
            "shot_id": "shot-01",
            "scene_id": "scene-1",
            "covers": ["s1-l1"],
            "shot_size": "close_up",
            "camera": "eye_level",
            "side": "A",
            "movement": "static",
            "est_duration_ms": 1000,
            "alternatives": 2,
        },
        {
            "shot_id": "shot-02",
            "scene_id": "scene-1",
            "covers": ["s1-l2"],  # 关键行
            "shot_size": "medium",
            "camera": "over_shoulder",
            "side": "A",
            "movement": "dolly",
            "est_duration_ms": 1250,
            "alternatives": 3,
        },
        {
            "shot_id": "shot-03",
            "scene_id": "scene-1",
            "covers": ["s1-l3"],
            "shot_size": "full",
            "camera": "side",
            "side": "A",
            "movement": "pan",
            "est_duration_ms": 1000,
            "alternatives": 1,
        },
        {
            "shot_id": "shot-04",
            "scene_id": "scene-2",
            "covers": ["s2-l1"],  # 关键行
            "shot_size": "close_up",
            "camera": "low_angle",
            "side": "A",
            "movement": "static",
            "est_duration_ms": 1000,
            "alternatives": 2,
        },
        {
            "shot_id": "shot-05",
            "scene_id": "scene-2",
            "covers": ["s2-l2"],
            "shot_size": "medium",
            "camera": "high_angle",
            "side": "A",
            "movement": "tilt",
            "est_duration_ms": 1250,
            "alternatives": 2,
        },
        {
            "shot_id": "shot-06",
            "scene_id": "scene-2",
            "covers": ["s2-l3"],
            "shot_size": "wide",
            "camera": "eye_level",
            "side": "A",
            "movement": "handheld",
            "est_duration_ms": 1500,
            "alternatives": 1,
        },
        {
            "shot_id": "shot-07",
            "scene_id": "scene-3",
            "covers": ["s3-l1"],
            "shot_size": "medium",
            "camera": "high_angle",
            "side": "B",
            "movement": "static",
            "est_duration_ms": 1000,
            "alternatives": 2,
        },
        {
            "shot_id": "shot-08",
            "scene_id": "scene-3",
            "covers": ["s3-l2"],
            "shot_size": "close_up",
            "camera": "eye_level",
            "side": "B",
            "movement": "dolly",
            "est_duration_ms": 1500,
            "alternatives": 3,
        },
        {
            "shot_id": "shot-09",
            "scene_id": "scene-3",
            "covers": ["s3-l3"],  # 关键行
            "shot_size": "full",
            "camera": "low_angle",
            "side": "B",
            "movement": "static",
            "est_duration_ms": 750,
            "alternatives": 1,
        },
    ]

    def _make(variant: str = "valid", **overrides):
        import copy

        shots = copy.deepcopy(_VALID_SHOTS)
        if variant == "unknown_line":
            shots[1] = {**shots[1], "covers": ["s1-l9"]}
        elif variant == "scene_uncovered":
            shots = [s for s in shots if s["scene_id"] != "scene-2"]
        elif variant == "key_line_uncovered":
            shots = [s for s in shots if s["shot_id"] != "shot-04"]  # scene-2 仍有镜头
        elif variant == "size_out_of_range":
            shots[4] = {**shots[4], "shot_size": "extreme_wide"}
        fields = {"shots": shots}
        fields.update(overrides)
        return ShotList(**fields)

    return _make


@pytest.fixture()
def make_storyboard_frames():
    """素材帧夹具（功能 008）：确定性小尺寸 RGB 帧序列（编码/拼接辅助测试用）。

    帧内容由 seed 派生（渐变 + 移动色块），同 seed 重算逐字节一致；
    小尺寸默认 64x48 控制单测编码耗时。
    """

    def _make(*, count: int = 4, width: int = 64, height: int = 48, seed: int = 7):
        import numpy as np

        ys, xs = np.mgrid[0:height, 0:width]
        brightness = 40 + seed % 120
        frames = np.zeros((count, height, width, 3), dtype=np.uint8)
        for t in range(count):
            gray = np.clip(brightness + (xs * 0.25 + t * 3) % 110, 0, 255)
            block = (xs >= (t * 5) % max(1, width - 10)) & (xs < (t * 5) % max(1, width - 10) + 10)
            gray = np.where(block & (ys >= height // 3) & (ys < height * 2 // 3), 220, gray)
            frames[t] = np.stack([gray, np.clip(gray + 10, 0, 255), gray], axis=-1).astype(np.uint8)
        return frames

    return _make


@pytest.fixture()
def storyboard_jobs_engine():
    """分镜运营表夹具：SQLite 内存库建 storyboard_render_jobs（可变表，无触发器）。"""
    from sqlalchemy import create_engine

    from agents.storyboard.db import create_render_jobs_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_render_jobs_schema(engine)
    return engine


@pytest.fixture()
def storyboard_data_dir(tmp_path):
    """分镜临时数据目录夹具：artifacts（animatic mp4 工件）/rounds（轮次收口）两层结构。"""
    base = tmp_path / "storyboard"
    for sub in ("artifacts", "rounds"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def storyboard_config():
    """分镜形态配置夹具：直接读 configs/movie.yaml 的 storyboard 段（真实配置路径）。"""
    from pathlib import Path

    from agents.storyboard.config import StoryboardConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return StoryboardConfig.from_yaml(path)


# ---------------------------------------------------------------------------
# 功能 009（剧本 Agent 降级模式）夹具：ScriptArtifact 工厂（三段 outline/scenes/script
# + 结构化标记：节拍清单/场景头/角色表含别名/行）+ 七类缺陷变体（节拍缺失/页数越界/
# 幽灵角色/地点不一致/比例失衡/同名异写/时间线矛盾）+ 人工策略源码 + 临时库/目录/配置
# ---------------------------------------------------------------------------

# 节拍表（与 configs/movie.yaml 的 screenplay.beat_sheet 同源；required 才必需，
# theme_stated 为可选节拍——合法工件可不含，约束见 tests/unit/test_screenplay_config.py）
_SCREENPLAY_REQUIRED_BEATS = [
    ("opening_image", "act1"),
    ("inciting_incident", "act1"),
    ("act1_turn", "act1"),
    ("midpoint", "act2"),
    ("dark_night", "act2"),
    ("act2_turn", "act2"),
    ("climax", "act3"),
    ("resolution", "act3"),
]
_SCREENPLAY_OPTIONAL_BEATS = [("theme_stated", "act1")]

# 角色表（含别名；归一化口径 = name ∪ aliases，proxy.entity_consistency 依据）
_SCREENPLAY_CHARACTERS = [
    {"name": "林静", "aliases": ["阿静"]},
    {"name": "陈默", "aliases": ["默哥"]},
    {"name": "周医生", "aliases": []},
]

# 场景头（heading = "{内景|外景} - {地点} - {时间描述}"，地点段与 location 字段机检一致）
_SCREENPLAY_SCENES = [
    {
        "scene_id": "scene-1",
        "prefix": "内景",
        "location": "病房",
        "time_desc": "夜",
        "time_marker": 0,  # 剧内时间戳（分钟，非降序 —— proxy.timeline_conflict 口径）
        "characters": ["林静", "陈默", "周医生"],
        "axis_base": "A",
    },
    {
        "scene_id": "scene-2",
        "prefix": "内景",
        "location": "走廊",
        "time_desc": "夜",
        "time_marker": 30,
        "characters": ["林静", "周医生"],
        "axis_base": "A",
    },
    {
        "scene_id": "scene-3",
        "prefix": "外景",
        "location": "天台",
        "time_desc": "清晨",
        "time_marker": 75,
        "characters": ["陈默", "林静"],
        "axis_base": "B",  # 与 008 ScriptScene.axis_base 同域（A|B）
    },
]

# 每场景 3 行基准（对白 6 / 动作 3 → 对白行占比 0.667 ∈ 配置区间 [0.4, 0.8]；
# 关键行 3 条 = 导出 008 必覆盖清单；行情绪取 008 情绪向量表同域取值，s2-l3 无标注）
_SCREENPLAY_LINES = [
    {  # scene-1
        "dialogue": {"character": "林静", "text": "你醒了。", "key": False, "emotion": "tense"},
        "action": {
            "character": "陈默",
            "text": "陈默伸手去够床头的病历本，输液管绷紧。",
            "key": True,
            "emotion": "tense",
        },
        "closing": {
            "character": "周医生",
            "text": "别乱动，伤口还没长好。",
            "key": False,
            "emotion": "calm",
        },
    },
    {  # scene-2
        "dialogue": {
            "character": "林静",
            "text": "他到底瞒了我多久？",
            "key": False,
            "emotion": "sorrow",
        },
        "action": {
            "character": None,  # 群体动作：动作行允许无归属主体
            "text": "走廊尽头的灯一盏一盏亮起来。",
            "key": True,
            "emotion": "tense",
        },
        "closing": {
            "character": "周医生",
            "text": "三个月。他让我们都不要说。",
            "key": False,
            "emotion": None,  # 情绪无标注（不适用路径，导出后 008 侧同样可缺）
        },
    },
    {  # scene-3
        "dialogue": {
            "character": "陈默",
            "text": "我本想等手术结束再告诉你。",
            "key": False,
            "emotion": "sorrow",
        },
        "action": {
            "character": None,
            "text": "风把两人的外套吹得鼓起。",
            "key": False,
            "emotion": "awe",
        },
        "closing": {
            "character": "林静",
            "text": "以后不用一个人扛。",
            "key": True,
            "emotion": "joyful",
        },
    },
]
_SCREENPLAY_FILLER_EMOTIONS = ("calm", "tense", "sorrow", "joyful", "awe")


@pytest.fixture()
def make_script_artifact():
    """ScriptArtifact 工厂（功能 009 / T902）：三段工件 + 结构化标记 + 七类缺陷变体。

    结构：beats（节拍清单，默认含全部 required 节拍）+ scenes（场景头，含 location/
    time_marker/出场角色/axis_base）+ characters（角色表含 aliases）+ lines（行含归属
    角色/关键行标注/情绪）。默认 3 场景 × 3 行 = 9 行（对白 6 / 动作 3 → 占比 0.667）。

    - stage：outline / scenes / script（三段共用同一结构化标记，落树时各自独立节点）；
    - lines_per_scene：每场景行数（默认 3；超出基准行的部分按模板补齐，行 id 连续）；
    - pages + lines_per_page：按页数生成长形态工件（总行数 = pages × lines_per_page，
      需能被场景数整除；用于页数门禁的界内/界外对照）；
    - 变体（缺陷各只注入一类，其余保持合法）：
      * missing_beat：缺关键节拍 climax（required 缺失，rule.beat_structure 判 0）；
      * page_out_of_range：行数 × 3（页数约为基准 3 倍，越出目标 ± 容差）；
      * ghost_character：scene-2 出场角色与 s2-l3 归属角色改为未登记"赵护士"；
      * location_mismatch：scene-2 场景头地点段"病房"与 location 字段"走廊"不一致；
      * ratio_imbalance：全部行改为对白（对白行占比 1.0 > 上限 0.8）；
      * entity_variant：s1-l1 归属角色改为"小静"（未登记、与登记名"林静"同源的异写）；
      * timeline_conflict：scene-3 时间戳 75 → 5（相对 scene-2 的 30 回退，时间线矛盾）。
    字段可被调用方整体覆盖（beats/scenes/characters/lines/text/stage/schema_version）。
    """
    from agents.screenplay.artifact import ScriptArtifact

    def _make(
        variant: str = "valid",
        *,
        stage: str = "outline",
        lines_per_scene: int = 3,
        pages: int | None = None,
        lines_per_page: int = 45,
        **overrides,
    ):
        import copy

        scenes_spec = copy.deepcopy(_SCREENPLAY_SCENES)
        if pages is not None:
            total = pages * lines_per_page
            if total % len(scenes_spec):
                raise ValueError("pages × lines_per_page 必须能被场景数整除（夹具约束）")
            lines_per_scene = total // len(scenes_spec)
        if variant == "page_out_of_range":
            lines_per_scene *= 3

        beats = [
            {
                "beat_id": beat_id,
                "act": act,
                "required": True,
                "description": f"节拍 {beat_id} 的结构要求（夹具）",
            }
            for beat_id, act in _SCREENPLAY_REQUIRED_BEATS
        ]
        if variant == "missing_beat":
            beats = [beat for beat in beats if beat["beat_id"] != "climax"]

        if variant == "ghost_character":
            scenes_spec[1]["characters"] = [*scenes_spec[1]["characters"], "赵护士"]

        scenes, lines = [], []
        for index, scene in enumerate(scenes_spec):
            heading_location = scene["location"]
            if variant == "location_mismatch" and scene["scene_id"] == "scene-2":
                heading_location = "病房"  # 场景头地点段与 location 字段不一致
            scenes.append(
                {
                    "scene_id": scene["scene_id"],
                    "heading": f"{scene['prefix']} - {heading_location} - {scene['time_desc']}",
                    "location": scene["location"],
                    "time_marker": scene["time_marker"],
                    "characters": list(scene["characters"]),
                    "axis_base": scene["axis_base"],
                }
            )
            if variant == "timeline_conflict" and scene["scene_id"] == "scene-3":
                scenes[-1]["time_marker"] = 5  # 相对 scene-2（30）回退 → 时间线矛盾

            base = _SCREENPLAY_LINES[index]
            scene_lines = [
                {"kind": "dialogue", **base["dialogue"]},
                {"kind": "action", **base["action"]},
                {"kind": "dialogue", **base["closing"]},
            ]
            for extra in range(3, lines_per_scene):
                name = scene["characters"][extra % len(scene["characters"])]
                is_dialogue = extra % 3 != 0
                scene_lines.append(
                    {
                        "kind": "dialogue" if is_dialogue else "action",
                        "character": name,
                        "text": (
                            f"（续）{name}把话说完：这一夜还没结束。"
                            if is_dialogue
                            else f"（续）{name}的动作接续：灯光晃了晃。"
                        ),
                        "key": False,
                        "emotion": _SCREENPLAY_FILLER_EMOTIONS[
                            extra % len(_SCREENPLAY_FILLER_EMOTIONS)
                        ],
                    }
                )
            for offset, line in enumerate(scene_lines, start=1):
                line_id = f"s{index + 1}-l{offset}"
                if variant == "ghost_character" and line_id == "s2-l3":
                    line["character"] = "赵护士"  # 幽灵角色出现在行归属（未登记）
                if variant == "entity_variant" and line_id == "s1-l1":
                    line["character"] = "小静"  # 同名异写：未登记但与"林静"同源的写法
                if variant == "ratio_imbalance":
                    line["kind"] = "dialogue"  # 比例失衡：对白行占比 1.0
                lines.append({"line_id": line_id, "scene_id": scene["scene_id"], **line})

        payload = {
            "schema_version": "1.0.0",
            "stage": stage,
            "text": (
                f"（{stage} 阶段文本）病房的夜与天台的清晨之间，"
                "陈默藏了三个月的手术通知，林静必须决定要不要拆穿。"
            ),
            "beats": beats,
            "scenes": scenes,
            "characters": copy.deepcopy(_SCREENPLAY_CHARACTERS),
            "lines": lines,
        }
        payload.update(overrides)
        return ScriptArtifact.from_dict(payload)

    return _make


@pytest.fixture()
def script_artifact(make_script_artifact):
    """默认合法剧本工件（outline 阶段，3 场景 9 行）。"""
    return make_script_artifact()


@pytest.fixture()
def make_script_artifacts(make_script_artifact):
    """三段工件工厂：{"outline": ..., "scenes": ..., "script": ...}（同一结构化标记）。"""
    counter = {"n": 0}

    def _make(variant: str = "valid", **overrides):
        counter["n"] += 1
        return {
            stage: make_script_artifact(
                variant,
                stage=stage,
                text=f"{stage} 阶段文本（第 {counter['n']} 轮）",
                **overrides,
            )
            for stage in ("outline", "scenes", "script")
        }

    return _make


@pytest.fixture()
def screenplay_policy_source():
    """人工剧本策略源码工厂（功能 009 / T914 定案接口 + C13 提交通道）：合法策略 + 违规变体。

    策略接口（T914 定案）：`plan(inputs, config)` 产**分阶段计划**
    `{stage: {beats, scenes, characters, lines}}`——节拍取自配置节拍表的 required 项
    （策略不重复声明门禁口径），场景/角色/行由人编写的结构探索工艺给出；三阶段
    逐级细化（每场行数随阶段递增）。

    - valid（默认）：纯计算（无白名单外 import）+ Policy 类 + plan(inputs, config)；
    - forbidden_import：`import socket`（白名单外 import，静态检查拒绝）；
    - forbidden_call：`open(...)` 文件 IO（危险内建，静态检查拒绝）；
    - bad_signature：plan 缺 config 参数（接口签名检查拒绝，T929 提交通道）。

    静态检查口径（002）：策略源码不得有白名单外 import、危险内建、私有/dunder 属性访问
    ——故策略只暴露公开方法（stage_markers 而非 _markers）。
    """

    def _make(variant: str = "valid"):
        header = ""
        if variant == "forbidden_import":
            header = "import socket\n\n"
        body_io = (
            '        with open("outline.json", "w") as handle:\n'
            "            handle.write(str(plans))\n"
            if variant == "forbidden_call"
            else ""
        )
        signature = (
            "    def plan(self, inputs):\n"
            if variant == "bad_signature"
            else "    def plan(self, inputs, config):\n"
        )
        return f'''{header}class Policy:
    """人工剧本策略（降级模式：策略由人编写，不自动进化）。"""

    # 场景网格：场景头（内景/外景 - 地点 - 时间）+ 剧内时间戳 + 出场角色 + 轴向基准
    SCENES = (
        {{
            "scene_id": "scene-1",
            "prefix": "内景",
            "location": "病房",
            "time_desc": "夜",
            "time_marker": 0,
            "cast": ("林静", "陈默", "周医生"),
            "axis_base": "A",
        }},
        {{
            "scene_id": "scene-2",
            "prefix": "内景",
            "location": "走廊",
            "time_desc": "夜",
            "time_marker": 30,
            "cast": ("林静", "周医生"),
            "axis_base": "A",
        }},
        {{
            "scene_id": "scene-3",
            "prefix": "外景",
            "location": "天台",
            "time_desc": "清晨",
            "time_marker": 75,
            "cast": ("陈默", "林静"),
            "axis_base": "B",
        }},
    )
    CHARACTERS = (
        {{"name": "林静", "aliases": ["阿静"]}},
        {{"name": "陈默", "aliases": ["默哥"]}},
        {{"name": "周医生", "aliases": []}},
    )
    EMOTIONS = ("calm", "tense", "sorrow", "joyful", "awe")
    # 阶段细化深度：每场行数（大纲 → 分场 → 剧本逐级细化）
    LINES_PER_SCENE = {{"outline": 2, "scenes": 2, "script": 3}}

{signature}        beats = [
            {{
                "beat_id": beat["beat_id"],
                "act": beat["act"],
                "required": beat["required"],
                "description": beat["description"],
            }}
            for beat in config.beat_sheet
            if beat["required"]
        ]
        return {{
            stage: self.stage_markers(stage, inputs, beats)
            for stage in self.LINES_PER_SCENE
        }}

    def stage_markers(self, stage, inputs, beats):
        scenes = []
        lines = []
        for index, scene in enumerate(self.SCENES):
            scenes.append(
                {{
                    "scene_id": scene["scene_id"],
                    "heading": " - ".join(
                        (scene["prefix"], scene["location"], scene["time_desc"])
                    ),
                    "location": scene["location"],
                    "time_marker": scene["time_marker"],
                    "characters": list(scene["cast"]),
                    "axis_base": scene["axis_base"],
                }}
            )
            for offset in range(self.LINES_PER_SCENE[stage]):
                name = scene["cast"][offset % len(scene["cast"])]
                is_dialogue = offset % 2 == 0
                lines.append(
                    {{
                        "line_id": "s" + str(index + 1) + "-l" + str(offset + 1),
                        "scene_id": scene["scene_id"],
                        "kind": "dialogue" if is_dialogue else "action",
                        "text": (
                            name + "把话说完（" + stage + " 阶段）。"
                            if is_dialogue
                            else name + "在" + scene["location"] + "留下一个动作。"
                        ),
                        "character": name,
                        "key": offset == 0,
                        "emotion": self.EMOTIONS[(index + offset) % len(self.EMOTIONS)],
                    }}
                )
{body_io}        return {{
            "beats": beats,
            "scenes": scenes,
            "characters": [dict(character) for character in self.CHARACTERS],
            "lines": lines,
        }}
'''

    return _make


@pytest.fixture()
def screenplay_jobs_engine():
    """剧本运营表夹具：SQLite 内存库建 screenplay_jobs（可变表，无 immutable 触发器）。"""
    from sqlalchemy import create_engine

    from agents.screenplay.db import create_jobs_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_jobs_schema(engine)
    return engine


@pytest.fixture()
def screenplay_data_dir(tmp_path):
    """剧本临时数据目录夹具：artifacts（内容寻址工件）/rounds（轮次收口落盘）两层结构。"""
    base = tmp_path / "screenplay"
    for sub in ("artifacts", "rounds"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def screenplay_config():
    """剧本形态配置夹具：直接读 configs/movie.yaml 的 screenplay 段（真实配置路径）。"""
    from pathlib import Path

    from agents.screenplay.config import ScreenplayConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return ScreenplayConfig.from_yaml(path)
