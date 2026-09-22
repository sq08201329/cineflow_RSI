"""测试公共夹具。

- sqlite_engine：SQLite 内存库 + 建表 + 不可变触发器（单元测试专用）；
- tree_store：基于内存库的 TreeStore；
- artifact_store：临时目录 LocalArtifactStore（单元测试专用，契约限定 Local 仅测试用）。

桩评估器不在此定义（唯一定义来源为 T025 的 tests/stubs.py）。
"""

import http.client
import json
import threading
import time
from pathlib import Path

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

    可选扩展（功能 011 跨项目池化夹具；全部缺省时与既有行为逐字节一致）：
    - evaluator_versions：写入 config_snapshot["evaluator_versions"]（版本分组键的输入）；
    - form：写入 config_snapshot["form"]（形态标注；None = 不标注，按池形态归入）；
    - config_extra：额外并入 config_snapshot 的键（形态配置微调变体）；
    - root_created_at：根节点时间戳基准（节点时间戳 = 基准 + 序号 × 1e-6），
      供"同 created_at 多棵"的时间重叠变体构造。
    """
    from core.tree.models import CostRecord, NodeStatus, new_id

    def _build(
        nodes_spec,
        *,
        project_id="proj-replay",
        agent_id="agent-replay",
        policy_version="a1b2c3d4e5f6",
        observation_fields=("gen_params",),
        evaluator_versions=None,
        form=None,
        config_extra=None,
        root_created_at=None,
    ):
        snapshot = {
            "evaluator_weights": {"rule.x": 0.0},
            "observation_fields": list(observation_fields),
        }
        if evaluator_versions is not None:
            snapshot["evaluator_versions"] = dict(evaluator_versions)
        if form is not None:
            snapshot["form"] = form
        if config_extra:
            snapshot.update(config_extra)
        tree = make_tree(
            project_id=project_id,
            agent_id=agent_id,
            policy_version=policy_version,
            config_snapshot=snapshot,
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
                created_at=float(i + 1) if root_created_at is None else root_created_at + i * 1e-6,
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


# ---------------------------------------------------------------------------
# 跨项目池化夹具（功能 011 / T1002）：多项目树工厂、版本集变体、得分冲突变体、
# 时间重叠变体、池化临时目录与配置。既有夹具行为不变（扩展均为可选参数）。
# ---------------------------------------------------------------------------


@pytest.fixture()
def pool_evaluator_versions():
    """池化默认评估器版本集（同版本集 = 同语义最小单位：版本分组键的输入）。"""
    return {"rule.x": "1.0.0", "proxy.y": "1.0.0"}


@pytest.fixture()
def make_pool_tree(tree_store, build_historical_tree, pool_evaluator_versions):
    """跨项目池化单树工厂：root + 每个结构键一个子节点（gen_params = 结构键）。

    参数（全部关键字）：
    - project_id：项目归属（跨项目池的追溯字段）；
    - structure_keys：结构键元组（子节点 gen_params；回放匹配键）；
    - scores：逐结构键得分（与 structure_keys 等长）；
    - evaluator_versions：评估器版本集（写入 config_snapshot；缺省用默认版本集）；
    - form：形态标注（写入 config_snapshot["form"]；None = 不标注）；
    - created_at：根节点时间戳基准（同值即时间重叠变体）；
    - config_extra：config_snapshot 额外键（形态配置微调变体）。
    返回 (tree, node_ids)；树已落盘（冻结）于 tree_store。
    """
    from core.tree.models import CostRecord, NodeStatus

    def _make(
        *,
        project_id="project-a",
        agent_id="agent-pool",
        structure_keys=({"temperature": 0.3}, {"temperature": 0.7}),
        scores=(0.6, 0.7),
        evaluator_versions=None,
        form=None,
        created_at=None,
        policy_version="a1b2c3d4e5f6",
        config_extra=None,
    ):
        if len(scores) != len(structure_keys):
            raise ValueError("structure_keys 与 scores 必须等长")
        spec = [(None, {}, 0.0, NodeStatus.EVALUATED, CostRecord())]
        spec += [
            (0, params, score, NodeStatus.EVALUATED, CostRecord(llm_calls=1))
            for params, score in zip(structure_keys, scores, strict=True)
        ]
        return build_historical_tree(
            spec,
            project_id=project_id,
            agent_id=agent_id,
            policy_version=policy_version,
            evaluator_versions=(
                pool_evaluator_versions if evaluator_versions is None else evaluator_versions
            ),
            form=form,
            config_extra=config_extra,
            root_created_at=created_at,
        )

    return _make


@pytest.fixture()
def pool_multi_project_trees(make_pool_tree):
    """默认多项目树集合：项目 A/B 各 2 棵同 Agent 同形态同版本集树（共 4 棵）。

    结构键逐棵不同（树序号入结构键），得分逐棵递增（0.5 ~ 0.8）；
    C1 场景 1（合并归属可追溯）与场景 4（同输入两次构建可重现）的输入。
    """
    trees = []
    for p_index, project_id in enumerate(("project-a", "project-b")):
        for t in range(2):
            tag = f"{project_id}-{t}"
            tree, _ = make_pool_tree(
                project_id=project_id,
                structure_keys=(
                    {"temperature": 0.3, "tree": tag},
                    {"temperature": 0.7, "tree": tag},
                ),
                scores=(0.5 + 0.1 * (p_index * 2 + t), 0.6 + 0.1 * (p_index * 2 + t)),
                created_at=1000.0 + p_index * 10 + t,
            )
            trees.append(tree)
    return trees


@pytest.fixture()
def pool_version_split_trees(make_pool_tree, pool_evaluator_versions):
    """版本集变体：项目 A/B 各有 1 棵旧版本集 + 1 棵新版本集树（共 4 棵 → 两个版本组）。

    新版本集把 rule.x 抬到 2.0.0——跨版本不混池（C1 场景 2）。
    """
    new_versions = {**pool_evaluator_versions, "rule.x": "2.0.0"}
    trees = []
    for p_index, project_id in enumerate(("project-a", "project-b")):
        for versions, tag in ((pool_evaluator_versions, "v1"), (new_versions, "v2")):
            tree, _ = make_pool_tree(
                project_id=project_id,
                structure_keys=({"temperature": 0.5, "version": tag},),
                scores=(0.6 + 0.1 * p_index,),
                evaluator_versions=versions,
                created_at=1000.0 + p_index * 10 + (0.0 if tag == "v1" else 1.0),
            )
            trees.append(tree)
    return trees


@pytest.fixture()
def pool_conflict_trees(make_pool_tree):
    """得分冲突变体（澄清 Q1）：A/B 同结构键同版本集但得分不同（0.6 vs 0.8）。

    每项目另有 1 棵独立结构键树（共 4 棵 ≥ min_trees=3）——冲突树是
    "可复现假设在该树上不成立"的诊断源（UNKNOWN + ScoreConflict）。
    """
    conflict_key = {"temperature": 0.5, "shared": True}
    trees = []
    for p_index, (project_id, conflict_score) in enumerate(
        (("project-a", 0.6), ("project-b", 0.8))
    ):
        for slot, (keys, scores) in enumerate(
            (
                ((conflict_key,), (conflict_score,)),
                (({"temperature": 0.3, "unique": project_id},), (0.7,)),
            )
        ):
            tree, _ = make_pool_tree(
                project_id=project_id,
                structure_keys=keys,
                scores=scores,
                created_at=1000.0 + p_index * 10 + slot,
            )
            trees.append(tree)
    return trees


@pytest.fixture()
def pool_overlapping_time_trees(make_pool_tree):
    """时间重叠变体：A/B 各 2 棵同 created_at 树（共 4 棵同刻）。

    同时间戳多棵 → 池构建按 (created_at, project_id) 字典序稳定排序（可重现，边界情况）。
    """
    trees = []
    for project_id in ("project-a", "project-b"):
        for t in range(2):
            tree, _ = make_pool_tree(
                project_id=project_id,
                structure_keys=({"temperature": 0.3 + 0.1 * t},),
                scores=(0.6 + 0.05 * t,),
                created_at=2000.0,
            )
            trees.append(tree)
    return trees


@pytest.fixture()
def pooling_data_dir(tmp_path):
    """池化快照临时目录：replay/pools 的数据目录等价物（不污染仓库工作树）。"""
    path = tmp_path / "replay" / "pools"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def pooling_config(pooling_data_dir):
    """池化配置夹具：默认档（min_trees=3 / 稀释阈值 0.7 / 跨形态与做梦开关关闭）+ 临时池目录。"""
    from core.replay.pooling_models import PoolingConfig

    return PoolingConfig(pools_dir=str(pooling_data_dir))


# ---------------------------------------------------------------------------
# 功能 012（judge 漂移自动检测）夹具：010 分布快照序列工厂（真实 schema 同源写入器）
# + 基线/稳定/三形态漂移/样本不足/缺口周期序列 + 010 台账与信度报告（含 judge 条目）
# + drift 临时数据目录。全部为新增夹具，既有夹具行为不变。
# ---------------------------------------------------------------------------

# 基线型窄峰分布的形状参数：围绕 0.5、σ≈0.08（25 个确定性样本，落在 bucket 3~6）
_DRIFT_Z_GRID = (
    -1.6,
    -1.3,
    -1.1,
    -0.9,
    -0.8,
    -0.7,
    -0.6,
    -0.5,
    -0.4,
    -0.3,
    -0.2,
    -0.1,
    0.0,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
    1.1,
    1.3,
    1.6,
)


def _drift_scores(period_index: int, *, spread: float = 0.1, shift: float = 0.0) -> list[float]:
    """确定性打分序列：中心 0.5、σ≈spread、整体平移 shift，微抖动不跨分桶边界。

    同 period_index 重算逐字节一致；抖动幅度 0.002（不影响判定，仅让各周期分桶
    直方图略有差异，避免"逐周期完全相同"的平凡稳定序列）。
    """
    return [
        round(0.5 + shift + spread * z + 0.002 * ((i * 7 + period_index * 3) % 5 - 2), 6)
        for i, z in enumerate(_DRIFT_Z_GRID)
    ]


@pytest.fixture()
def drift_period_labels():
    """漂移检测默认周期序列：6 个周期（前 5 个为基线窗口，末个为当前周期）。"""
    return ("2026-W34", "2026-W35", "2026-W36", "2026-W37", "2026-W38", "2026-W39")


@pytest.fixture()
def drift_score_sequences(drift_period_labels):
    """命名分布序列（period → 锚点得分列表），供快照工厂落盘。

    - stable：6 周期同一窄峰分布（稳定序列，判 normal 不误报）；
    - mean_shift：前 5 周期基线 + 当前周期整体 +0.2（均值平移漂移）；
    - variance_widen：前 5 周期基线 + 当前周期 σ×1.8（方差展宽漂移）；
    - bimodal：前 5 周期基线 + 当前周期双峰（0.15/0.85 两簇，双峰化漂移）；
    - insufficient_current：前 5 周期基线 + 当前周期 2 样本（当前周期样本不足）；
    - insufficient_baseline：基线仅 1 周期 × 2 样本 + 当前周期充足（基线窗口样本不足）；
    - gap：基线缺 2026-W36 一个周期（序列缺口，不插值）+ 当前周期稳定；
    - first_period：仅当前周期一条（首周期无基线）。
    """
    periods = tuple(drift_period_labels)
    baseline = {p: _drift_scores(i) for i, p in enumerate(periods[:-1])}
    current = periods[-1]
    bimodal = [0.15] * 13 + [0.85] * 12
    return {
        "stable": {p: _drift_scores(i) for i, p in enumerate(periods)},
        "mean_shift": {**baseline, current: _drift_scores(5, shift=0.2)},
        "variance_widen": {**baseline, current: _drift_scores(5, spread=0.18)},
        "bimodal": {**baseline, current: list(bimodal)},
        "insufficient_current": {**baseline, current: [0.45, 0.55]},
        "insufficient_baseline": {periods[0]: [0.45, 0.55], current: _drift_scores(5)},
        "gap": {
            p: _drift_scores(i)
            for i, p in enumerate(periods[:-1])
            if p != "2026-W36"  # 缺口周期：既不落盘也不插值
        }
        | {current: _drift_scores(5)},
        "first_period": {current: _drift_scores(5)},
    }


@pytest.fixture()
def drift_data_dir(tmp_path):
    """漂移临时数据目录夹具：010 产物目录（snapshots/ledger/reports）+ drift 四层子目录。"""
    base = tmp_path / "calibration"
    for sub in ("snapshots", "ledger", "reports"):
        (base / sub).mkdir(parents=True)
    for sub in ("metrics", "status", "dispositions", "reports"):
        (base / "drift" / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def drift_config():
    """漂移检测配置夹具：直接读 configs/movie.yaml 的 calibration.drift 段（真实配置路径）。"""
    from pathlib import Path

    from core.calibration.drift_config import DriftConfig

    path = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
    return DriftConfig.from_yaml(path)


@pytest.fixture()
def write_drift_snapshots(drift_data_dir):
    """分布快照序列落盘工厂（功能 012）：与 010 同源写入器，schema 逐字段一致。

    参数：sequence = {period: [锚点得分]}；agent_id / evaluator_key（evaluator_id@版本）
    / data_dir 可覆盖。返回落盘路径列表（按 sequence 顺序展开）。
    """

    def _write(
        sequence,
        *,
        agent_id: str = "visual",
        evaluator_key: str = "judge.cinematic@1.0.0",
        data_dir=None,
    ):
        from core.calibration.ledger import write_anchor_snapshots
        from core.calibration.models import PairingRecord

        base = drift_data_dir if data_dir is None else data_dir
        paths = []
        for period, scores in sequence.items():
            pairs = [
                PairingRecord(
                    anchor_id=f"{period}-a{i}",
                    evaluator_key=evaluator_key,
                    anchor_score=float(score),
                    auto_score=0.5,
                )
                for i, score in enumerate(scores)
            ]
            paths += write_anchor_snapshots(base, agent_id, period, pairs)
        return paths

    return _write


@pytest.fixture()
def drift_sequence_writer(drift_score_sequences, write_drift_snapshots):
    """形态序列落盘工厂（功能 012）：按命名形态落盘（可限定周期子集）。

    variant ∈ drift_score_sequences 的键（stable/mean_shift/variance_widen/bimodal/
    insufficient_*/gap/first_period）；periods 给定时只落这些周期（补写单周期用）。
    """

    def _write(
        variant: str,
        *,
        agent_id: str = "visual",
        evaluator_key: str = "judge.cinematic@1.0.0",
        periods=None,
        data_dir=None,
    ):
        sequence = drift_score_sequences[variant]
        if periods is not None:
            sequence = {period: sequence[period] for period in periods}
        return write_drift_snapshots(
            sequence, agent_id=agent_id, evaluator_key=evaluator_key, data_dir=data_dir
        )

    return _write


@pytest.fixture()
def write_calibration_ledger(drift_data_dir):
    """010 台账写入工厂（功能 012 用）：per 评估器 per 周期追加 BiasRecord 行。

    entries 元素：{evaluator_key, period, samples, kendall_tau? | pearson_r?, note?}；
    judge 条目走 kendall_tau、连续条目走 pearson_r（与 010 close_round 同口径）。
    """

    def _write(entries, *, agent_id: str = "visual", data_dir=None):
        from core.calibration.ledger import append_ledger
        from core.calibration.models import BiasRecord

        base = drift_data_dir if data_dir is None else data_dir
        records = [
            BiasRecord(
                evaluator_key=entry["evaluator_key"],
                period=entry["period"],
                samples=entry["samples"],
                mean_shift=entry.get("mean_shift"),
                pearson_r=entry.get("pearson_r"),
                kendall_tau=entry.get("kendall_tau"),
                note=entry.get("note", ""),
            )
            for entry in entries
        ]
        append_ledger(base, agent_id, records)
        return records

    return _write


@pytest.fixture()
def calibration_reliability_report(drift_data_dir, write_calibration_ledger):
    """010 信度报告夹具（含 judge 条目）：写台账后调 build_report 落盘 reports/{period}.json。

    默认 judge.cinematic@1.0.0 的 kendall_tau 低于信度目标（0.3 < 0.6，信度下降信号），
    proxy.aesthetic@1.0.0 达标（0.8）——双信号联动测试的两侧对照。
    """

    def _make(
        period: str = "2026-W39",
        *,
        agent_id: str = "visual",
        target: float = 0.6,
        judge_tau: float = 0.3,
        proxy_r: float = 0.8,
        samples: int = 12,
        data_dir=None,
    ):
        from core.calibration.report import build_report

        base = drift_data_dir if data_dir is None else data_dir
        write_calibration_ledger(
            [
                {
                    "evaluator_key": "judge.cinematic@1.0.0",
                    "period": period,
                    "samples": samples,
                    "kendall_tau": judge_tau,
                },
                {
                    "evaluator_key": "proxy.aesthetic@1.0.0",
                    "period": period,
                    "samples": samples,
                    "pearson_r": proxy_r,
                },
            ],
            agent_id=agent_id,
            data_dir=base,
        )
        return build_report(base, period, target=target)

    return _make


# ---------------------------------------------------------------------------
# 功能 013（前端可视化）夹具：web 临时数据目录与配置、夹具树（2 项目 × 2 Agent ×
# 2 策略版本）、做梦轮次报告文件、010 信度报告、012 漂移状态与报表、谱系 meta。
# 全部为新增夹具，既有夹具行为不变；web 侧取数走文件版 SQLite（真连接，覆盖惰性建连
# 与 DB 不可用路径）。
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

# web 夹具策略源码：版本号 = 内容哈希（与 001/005 的版本纪律一致——夹具不臆造版本字面量）
WEB_POLICY_SOURCES = {
    "champion": (
        "class Policy:\n"
        '    """web 夹具冠军策略（谱系根：无父版本）。"""\n'
        "\n"
        "    def solve(self, env, budget):\n"
        '        return ""\n'
    ),
    "child": (
        "class Policy:\n"
        '    """web 夹具子代策略（父版本 = 冠军）。"""\n'
        "\n"
        "    def solve(self, env, budget):\n"
        '        return ""\n'
    ),
    "orphan": (
        "class Policy:\n"
        '    """web 夹具无树版本（有 meta 无树：谱系呈现版本自身）。"""\n'
        "\n"
        "    def solve(self, env, budget):\n"
        '        return ""\n'
    ),
}

# web 夹具树规格：(tree_id, 项目, Agent, 形态标注, 逐节点得分)
# 覆盖三维过滤（2 项目 × 2 Agent × 2 版本）、跨项目归属（冠军版本横跨 proj-alpha/proj-beta）、
# 未标注形态（form=None）与分页（节点总数 > page_size）
WEB_TREE_SPECS = (
    ("tree-alpha-visual-champion", "proj-alpha", "visual", "visual", (0.3, 0.5, 0.7)),
    ("tree-alpha-visual-child", "proj-alpha", "visual", "visual", (0.4, 0.9)),
    ("tree-beta-visual-champion", "proj-beta", "visual", None, (0.6,)),
    ("tree-beta-storyboard-beta", "proj-beta", "storyboard", "storyboard", (0.2, 0.35)),
)


@pytest.fixture()
def web_versions():
    """web 夹具策略版本（内容哈希）：champion / child / orphan。"""
    from policies.versioning import policy_version

    return {name: policy_version(source) for name, source in WEB_POLICY_SOURCES.items()}


@pytest.fixture()
def web_tree_engine(tmp_path):
    """web 查询层夹具库：文件版 SQLite + 001 schema（独立于 in-memory 夹具，跨连接共享）。"""
    from core.tree.db import create_schema

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tree.db'}")
    create_schema(engine)
    return engine


@pytest.fixture()
def web_tree_dsn(web_tree_engine):
    """只读角色的 DSN 等价物（单测走 SQLite 文件；真实 PG 权限断言见 T1311）。"""
    return str(web_tree_engine.url)


@pytest.fixture()
def web_fixture_trees(web_tree_engine, web_versions):
    """web 夹具树集合（落盘即冻结）：{tree_id: [node_id, ...]}。

    每棵树 = 根节点 + 逐节点子节点（挂在根下，depth 1）；created_at 按 (树序号, 节点序号)
    单调递增（树清单倒序与节点分页的确定性依据）。observation_context 携带 gen_params 与
    工件元信息键（champion 树中间节点带 material_* 键，覆盖工件元信息投影）。
    """
    import blake3

    from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
    from core.tree.store import create_tree_store

    store = create_tree_store(web_tree_engine)
    trees: dict[str, list[str]] = {}
    for tree_index, (tree_id, project_id, agent_id, form, scores) in enumerate(WEB_TREE_SPECS):
        version = web_versions["champion" if "champion" in tree_id else "child"]
        snapshot: dict = {"evaluator_weights": {"proxy.aesthetic": 1.0, "judge.cinematic": 1.0}}
        if form is not None:
            snapshot["form"] = form
        root_id = f"{tree_id}-n0"
        node_ids = [f"{tree_id}-n{index}" for index in range(len(scores))]
        store.create_tree(
            DiscoveryTree(
                tree_id=tree_id,
                project_id=project_id,
                agent_id=agent_id,
                policy_version=version,
                root_id=root_id,
                node_ids=node_ids,
                config_snapshot=snapshot,
            )
        )
        for index, score in enumerate(scores):
            observation = {
                "gen_params": {"temperature": 0.2 + 0.1 * index, "tree": tree_id},
                "clip_id": f"{tree_id}-clip-{index}",
            }
            if tree_id == "tree-alpha-visual-champion" and index == 1:
                observation |= {"material_kind": "poster", "material_tags": ["fixture"]}
            node_id = node_ids[index]
            store.append_node(
                TreeNode(
                    node_id=node_id,
                    tree_id=tree_id,
                    parent_id=None if index == 0 else root_id,
                    depth=0 if index == 0 else 1,
                    agent_id=agent_id,
                    policy_version=version,
                    prompt=f"夹具提示词 {index}：探索 {agent_id} 的参数组合",
                    observation_context=observation,
                    artifact_hash=blake3.blake3(node_id.encode()).hexdigest(),
                    eval_breakdown={
                        "proxy.aesthetic@1.0.0": {
                            "score": score,
                            "diagnostics": {"band": "high" if score > 0.5 else "low"},
                        },
                        "judge.cinematic@1.0.0": {"score": score, "diagnostics": {}},
                    },
                    score=score,
                    cost=CostRecord(
                        llm_calls=1,
                        llm_tokens=100 * (index + 1),
                        generation_api_cost_usd=0.05 * (index + 1),
                        wall_clock_seconds=0.25 * (index + 1),
                    ),
                    status=NodeStatus.EVALUATED,
                    created_at=1000.0 + tree_index * 10 + index,
                )
            )
        trees[tree_id] = node_ids
    return trees


@pytest.fixture()
def web_data_dir(tmp_path):
    """web 文件化产物临时根：policies / dreaming / calibration / pools 四类 data_dirs。

    calibration 下按 010 产物（snapshots/ledger/reports）与 012 产物
    （drift/{metrics,status,dispositions,reports}）分层创建，与既有夹具同构。
    """
    base = tmp_path / "web-data"
    dirs = {
        "policies": base / "policies",
        "dreaming": base / "dreaming",
        "calibration": base / "calibration",
        "pools": base / "replay" / "pools",
    }
    for path in dirs.values():
        path.mkdir(parents=True)
    for sub in ("snapshots", "ledger", "reports"):
        (dirs["calibration"] / sub).mkdir()
    for sub in ("metrics", "status", "dispositions", "reports"):
        (dirs["calibration"] / "drift" / sub).mkdir(parents=True)
    return dirs


@pytest.fixture()
def web_config(monkeypatch, tmp_path, web_data_dir, web_tree_dsn):
    """web 配置夹具：以真实 configs/movie.yaml 的 web 段为基准，数据目录/DSN 指向临时夹具。

    page_size 保持真实配置值（50）；分页边界用请求参数 page/page_size 构造，
    上限封顶由 requests 侧的 page_size=1000 机检。dsn_env 指向临时环境变量。
    """
    from dataclasses import replace

    from web.queries import WebConfig

    base = WebConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
    config = replace(
        base,
        dsn_env="CINEFLOW_WEB_TEST_DSN",
        data_dirs={key: str(value) for key, value in web_data_dir.items()},
        export_dir=str(tmp_path / "web-dist"),
    )
    monkeypatch.setenv(config.dsn_env, web_tree_dsn)
    return config


@pytest.fixture()
def web_source_files():
    """web/ 下全部 Python 源文件（静态断言输入）。"""
    return sorted((REPO_ROOT / "web").rglob("*.py"))


@pytest.fixture()
def web_static_root(tmp_path):
    """静态资产根夹具：两页面 + 脚本 + 样式 + 越界诱饵（静态资产/路径穿越用例）。"""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>树浏览器</title>", encoding="utf-8")
    (root / "board.html").write_text("<!doctype html><title>进化看板</title>", encoding="utf-8")
    (root / "app.js").write_text("// 夹具脚本\n", encoding="utf-8")
    (root / "style.css").write_text("body { margin: 0 }\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不应被服务", encoding="utf-8")
    return root


class _LiveServer:
    """真实 socket 上的只读服务（**临时端口**）：比直调处理函数更接近部署形态。

    端口强制取 0（临时端口）而非配置值：测试不得与本地正在运行的服务（默认 8080）
    抢占端口——"配置端口生效"由 tests/unit/test_web_server.py 的专项用例单独断言。
    """

    def __init__(self, config, static_root):
        from dataclasses import replace

        from web import server as web_server

        self.httpd = web_server.create_server(replace(config, port=0), static_root=static_root)
        self.host = self.httpd.server_address[0]
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, *, headers=None, body=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            conn.close()

    def json(self, method: str, path: str, **kwargs):
        status, headers, payload = self.request(method, path, **kwargs)
        parsed = json.loads(payload.decode("utf-8")) if payload else None
        return status, headers, parsed

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture()
def web_live_server(web_config, web_static_root):
    """活服务工厂：`start(config=None)` 起在临时端口；测试结束统一 shutdown。"""
    servers: list[_LiveServer] = []

    def _start(config=None, static_root=None):
        server = _LiveServer(
            web_config if config is None else config,
            web_static_root if static_root is None else static_root,
        )
        servers.append(server)
        return server

    yield _start
    for server in servers:
        server.close()


@pytest.fixture()
def write_dream_rounds(web_data_dir):
    """做梦轮次报告工厂：按 dreaming.pipeline 的 DreamRound schema 落盘（与 005 同源）。

    specs 元素：{"round_id", "curve": [逐轮得分], "cost_usd", "winner": bool, "status"}；
    reward 由 005 权威口径 compute_reward 从轨迹推导（不写死字面量）；winner 为 None/False
    的轮次落盘失败态（无胜出候选，曲线侧跳过）。
    """

    def _write(specs, *, agent_id: str = "visual", history_root=None):
        from core.replay.trajectory import ReplayTrajectory, TrajectoryStatus
        from core.tree.models import CostRecord
        from dreaming.pipeline import Candidate, DreamRound
        from dreaming.reward import compute_reward

        root = Path(web_data_dir["dreaming"]) if history_root is None else Path(history_root)
        directory = root / agent_id
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, spec in enumerate(specs, start=1):
            round_id = spec.get("round_id", f"dream-{agent_id}-{index}")
            status = spec.get("status", "completed")
            curve = list(spec.get("curve", [0.2, 0.4]))
            cost_usd = float(spec.get("cost_usd", 0.0))
            candidates: list = []
            winner = None
            if status == "completed" and spec.get("winner", True):
                version = spec.get("version", f"ver-{agent_id}-{index}")
                trajectory = ReplayTrajectory(
                    policy_version=version,
                    best_score_curve=curve,
                    probe_count=int(spec.get("probe_count", 4)),
                    effective_sequential_rounds=float(spec.get("sequential_rounds", 1.0)),
                    total_cost=CostRecord(generation_api_calls=1, generation_api_cost_usd=cost_usd),
                    final_node_id=f"{round_id}-final",
                    status=TrajectoryStatus.COMPLETED,
                    diagnostics={},
                )
                candidates.append(
                    Candidate(
                        version=version,
                        source_code=f"# {round_id} 候选（夹具）\n",
                        static_check="passed",
                        trajectory=trajectory.to_dict(),
                        reward=compute_reward(trajectory, 0.5),
                    )
                )
                winner = version
            payload = DreamRound(
                round_id=round_id,
                agent_id=agent_id,
                champion_version=spec.get("champion_version", "ver-champion"),
                digest={"agent_id": agent_id, "recent_k": 5, "rounds": [], "note": ""},
                digest_sha="0" * 64,
                candidates=candidates,
                winner_version=winner,
                status=status,
                diagnostics={},
            ).to_dict()
            target = directory / f"{round_id}.json"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            paths.append(target)
        return paths

    return _write


@pytest.fixture()
def web_fixture_rounds(web_data_dir, write_dream_rounds):
    """默认做梦轮次序列（visual）：4 轮完成（第 2 轮起奖励低于基线 ×0.7 连续 3 轮 → 塌缩）
    + 1 轮失败（曲线上跳过，不干扰塌缩序号的 1-based 位置）。
    """
    specs = [
        {"round_id": "dream-visual-1", "curve": [0.6, 0.8], "cost_usd": 0.4},
        {"round_id": "dream-visual-2", "curve": [0.2, 0.3], "cost_usd": 0.3},
        {"round_id": "dream-visual-3", "curve": [0.2, 0.25], "cost_usd": 0.2},
        {"round_id": "dream-visual-4", "curve": [0.15, 0.2], "cost_usd": 0.1},
        {"round_id": "dream-visual-5", "status": "failed_all_rejected", "winner": None},
    ]
    return write_dream_rounds(specs, agent_id="visual")


@pytest.fixture()
def web_lineage_files(web_data_dir, web_versions):
    """谱系 meta 夹具：冠军（根，含人工审批）→ 子代（树口径版本）+ 孤儿版本（有 meta 无树）。"""
    from dreaming.lineage import write_meta
    from policies.versioning import record_policy

    history_root = Path(web_data_dir["policies"])
    for source in WEB_POLICY_SOURCES.values():
        record_policy(source, "visual", history_root=history_root)
    champion = web_versions["champion"]
    child = web_versions["child"]
    orphan = web_versions["orphan"]
    write_meta(
        history_root,
        "visual",
        {
            "version": champion,
            "parent_version": None,
            "created_round": "manual-seed",
            "reward": {"pareto_auc": 0.3, "parallel_penalty": 0.25, "lambda": 0.5, "reward": 0.05},
            "source": "manual",
            "approval": {
                "approver": "sunqi",
                "at": "2026-09-21T00:00:00+08:00",
                "decision": "approved",
                "reason": "web 夹具首版部署（谱系根）",
            },
        },
    )
    write_meta(
        history_root,
        "visual",
        {
            "version": child,
            "parent_version": champion,
            "created_round": "dream-visual-2",
            "reward": {"pareto_auc": 0.25, "parallel_penalty": 0.25, "lambda": 0.5, "reward": 0.0},
            "source": "dreaming",
        },
    )
    write_meta(
        history_root,
        "visual",
        {
            "version": orphan,
            "parent_version": champion,
            "created_round": "dream-visual-3",
            "reward": {"pareto_auc": 0.1, "parallel_penalty": 0.25, "lambda": 0.5, "reward": -0.15},
            "source": "dreaming",
        },
    )
    return {"champion": champion, "child": child, "orphan": orphan}


@pytest.fixture()
def web_reliability_report(web_data_dir, calibration_reliability_report):
    """010 信度报告夹具（写入 web 的 calibration 目录）：judge 0.3 < 0.6 未达标、proxy 达标。"""
    return calibration_reliability_report(
        period="2026-W39", data_dir=web_data_dir["calibration"], target=0.6
    )


@pytest.fixture()
def web_drift_report(web_data_dir, drift_config, drift_sequence_writer):
    """012 漂移报表夹具（写入 web 的 calibration 目录）：检测 → 超阈登记 suspect → 落盘报表。

    返回 `build_report` 的报表对象；报表文件位于 `calibration/drift/reports/{period}.json`，
    状态登记位于 `calibration/drift/status/`（web 摘要面板的两类只读来源）。
    """

    def _make(
        *,
        agent_id: str = "visual",
        evaluator_key: str = "judge.cinematic@1.0.0",
        period: str = "2026-W39",
        variant: str = "mean_shift",
        periods=("2026-W38", "2026-W39"),
    ):
        from core.calibration.drift_metrics import detect_drift
        from core.calibration.drift_report import build_report
        from core.calibration.drift_status import register_suspect

        base = web_data_dir["calibration"]
        drift_sequence_writer(
            variant, agent_id=agent_id, evaluator_key=evaluator_key, data_dir=base
        )
        metrics = None
        for label in periods:
            metrics = detect_drift(agent_id, evaluator_key, label, drift_config, base)
        if metrics is not None and metrics.verdict.value == "drift":
            register_suspect(base, evaluator_key, metrics, at="2026-09-21T10:00:00+00:00")
        return build_report(period, drift_config, base)

    return _make


# ---------------------------------------------------------------------------
# 功能 014（策略部署评估自动化）夹具：deployment 临时数据目录（契约六子目录）、
# 部署形态配置（真实 configs/movie.yaml）、候选与证据组合矩阵（全满足/单要件不满足/
# 证据缺失/禁止名单）、池化回放对比结果（011 口径）、漂移状态（012 verdict）、
# 指针与部署留痕（configs 副本 + deploys 留痕）。全部为新增夹具，既有夹具行为不变。
# ---------------------------------------------------------------------------


@pytest.fixture()
def deployment_data_dir(tmp_path):
    """deployment 数据目录夹具：契约六子目录

    （mode/evidence/shadow/deploys/spot_checks/rollbacks）。
    """
    base = tmp_path / "deployment"
    for sub in ("mode", "evidence", "shadow", "deploys", "spot_checks", "rollbacks"):
        (base / sub).mkdir(parents=True)
    return base


@pytest.fixture()
def deployment_config():
    """部署形态配置夹具：直接读 configs/movie.yaml 的 deployment 段（真实配置路径）。"""
    from core.deployment.config import DeploymentConfig

    return DeploymentConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")


@pytest.fixture()
def pooled_replay_comparison():
    """011 池化回放对比结果夹具工厂：候选 vs 现部署同池 reward + 来源引用。

    构造走 `core.deployment.evidence.reward_compare_payload`——生产者（池化回放对比）
    与消费方同口径，夹具不另造 schema。
    """

    def _make(candidate=0.62, deployed=0.55, *, source="replay/pools/pool-visual-2026W39.json"):
        from core.deployment.evidence import reward_compare_payload

        return reward_compare_payload(candidate, deployed, source=source)

    return _make


@pytest.fixture()
def deployment_drift_registry(tmp_path):
    """012 漂移状态夹具工厂：normal/suspect/confirmed_drift/false_alarm → DriftRegistry。

    真实登记写入（`register_suspect` + `dispose`，非伪造映射），状态来自 system/人工
    两条既有通路；normal 即"无任何登记"（012 口径：无登记 = normal = 允许）。
    """

    def _make(
        state="normal",
        *,
        evaluator_key="judge.cinematic@1.0.0",
        agent_id="visual",
        data_dir=None,
    ):
        from core.calibration.drift_models import (
            DriftAction,
            DriftConclusion,
            DriftMetrics,
            DriftVerdict,
        )
        from core.calibration.drift_status import DriftRegistry, dispose, register_suspect

        base = tmp_path / f"deployment-drift-{state}" if data_dir is None else data_dir
        if state == "normal":
            return DriftRegistry.load(base)
        if state not in ("suspect", "confirmed_drift", "false_alarm"):
            raise ValueError(f"未知漂移状态夹具档位：{state!r}")
        metrics = DriftMetrics(
            evaluator_key=evaluator_key,
            agent_id=agent_id,
            period="2026-W39",
            verdict=DriftVerdict.DRIFT,
            samples=24,
            detector_version="drift_detector@1.0.0+014d3f10a2b7",
            psi=0.31,
            quantile_shifts={"p25": 0.2, "p50": 0.2, "p75": 0.2, "p90": 0.2},
            baseline_ref="2026-W33..2026-W38",
            thresholds={"psi": 0.2, "quantile": 0.1, "min_samples": 3, "window": 5},
            note="PSI 0.3100 > 0.2（014 夹具）",
        )
        register_suspect(base, evaluator_key, metrics, at="2026-09-21T10:00:00+00:00")
        if state == "suspect":
            return DriftRegistry.load(base)
        if state == "confirmed_drift":
            dispose(
                base,
                evaluator_key,
                DriftConclusion.CONFIRMED_DRIFT,
                by="ops",
                reason="人工确认漂移（014 夹具）",
                action=DriftAction.DEACTIVATE,
                at="2026-09-21T11:00:00+00:00",
            )
        else:
            dispose(
                base,
                evaluator_key,
                DriftConclusion.FALSE_ALARM,
                by="ops",
                reason="人工判为误报（014 夹具）",
                action=DriftAction.RESTORE,
                at="2026-09-21T11:00:00+00:00",
            )
        return DriftRegistry.load(base)

    return _make


@pytest.fixture()
def deployment_evidence_matrix(deployment_drift_registry, pooled_replay_comparison):
    """候选与证据组合矩阵夹具（US1 判定矩阵的唯一构造来源）。

    每档 = `{"kwargs": collect_evidence 关键字参数, "expected_decision": 期望判定,
    "expected_states": 逐要件状态，可选}`；判定矩阵的"双向断言"（满足即放行、不满足即
    拦截）在同一夹具上取数，避免测试各自拼证据导致口径分叉。
    """
    ok_registry = deployment_drift_registry("normal")
    suspect_registry = deployment_drift_registry("suspect")
    judge_keys = ("judge.cinematic@1.0.0",)
    pass_attestation = {"verdict": "pass", "tau": 0.82, "threshold": 0.6}
    fail_attestation = {"verdict": "fail", "tau": 0.2, "threshold": 0.6}
    wider_validation = {
        "cand-001": 0.1,
        "sibling-a": 0.9,
        "sibling-b": 0.8,
        "sibling-c": 0.7,
        "sibling-d": 0.6,
    }
    base = {
        "agent_id": "visual",
        "candidate_version": "cand-001",
        "deployed_version": "dep-000",
    }
    return {
        "all_satisfied": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "eligible",
            "expected_states": {
                "unbiasedness": "satisfied",
                "reward_compare": "satisfied",
                "validation_rank": "satisfied",
                "drift_verdict": "satisfied",
            },
        },
        "reward_unsatisfied": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.55, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "blocked",
            "expected_states": {"reward_compare": "unsatisfied"},
        },
        "validation_unsatisfied": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": wider_validation,
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "blocked",
            "expected_states": {"validation_rank": "unsatisfied"},
        },
        "drift_unsatisfied": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": suspect_registry,
            },
            "expected_decision": "blocked",
            "expected_states": {"drift_verdict": "unsatisfied"},
        },
        "unbiasedness_missing": {
            "kwargs": {
                **base,
                "unbiasedness": None,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "insufficient_evidence",
            "expected_states": {"unbiasedness": "missing"},
        },
        "unbiasedness_failed": {
            "kwargs": {
                **base,
                "unbiasedness": fail_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "insufficient_evidence",
            "expected_states": {"unbiasedness": "unsatisfied"},
        },
        "missing_reward": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": None,
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "insufficient_evidence",
            "expected_states": {"reward_compare": "missing"},
        },
        "missing_validation": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": None,
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "insufficient_evidence",
            "expected_states": {"validation_rank": "missing"},
        },
        "missing_drift": {
            "kwargs": {
                **base,
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": None,
            },
            "expected_decision": "insufficient_evidence",
            "expected_states": {"drift_verdict": "missing"},
        },
        "no_judge": {
            "kwargs": {
                **base,
                "agent_id": "promo",
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": (),
                "drift_registry": None,
            },
            "expected_decision": "blocked",
            "expected_states": {"drift_verdict": "not_applicable"},
        },
        "forbidden_agent": {
            "kwargs": {
                **base,
                "agent_id": "screenplay",
                "unbiasedness": pass_attestation,
                "reward_compare": pooled_replay_comparison(0.62, 0.55),
                "validation_rewards": {"cand-001": 0.8, "dep-000": 0.5},
                "judge_keys": judge_keys,
                "drift_registry": ok_registry,
            },
            "expected_decision": "forbidden_agent",
            "expected_states": {},
        },
    }


@pytest.fixture()
def deployment_pointer_files(tmp_path, deployment_data_dir):
    """指针与部署留痕夹具：configs 副本（可定点改写）+ deployment 数据目录。

    返回 dict：config = 仓库配置的临时副本（定点改写不触仓库工作树）、
    data_dir = deployment 数据目录、agent_id = screenplay、
    current_version = 副本内的当前部署指针值。
    """

    def _make(agent_id="screenplay", current_version=None):
        import yaml

        from core.yaml_edit import upsert_section_entries

        source = (REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8")
        config = tmp_path / f"movie-{agent_id}.yaml"
        config.write_text(source, encoding="utf-8")
        payload = yaml.safe_load(source)
        pointer = (payload.get("deployment") or {}).get(agent_id, {}).get("current_policy_version")
        if pointer is None:
            # 该 Agent 尚无部署指针（如 visual）：用定点改写补一条（与部署路径同款写法）
            pointer = current_version or "dep-000"
            config.write_text(
                upsert_section_entries(
                    source, ("deployment", agent_id), {"current_policy_version": pointer}
                ),
                encoding="utf-8",
            )
        return {
            "config": config,
            "data_dir": deployment_data_dir,
            "agent_id": agent_id,
            "current_version": pointer,
        }

    return _make


@pytest.fixture()
def write_deploy_events(deployment_data_dir):
    """部署事件留痕工厂：deploys/{ts}-{agent}.json（只增不改，已存在即拒绝覆盖）。"""

    def _write(events, *, data_dir=None):
        from datetime import UTC, datetime

        base = deployment_data_dir if data_dir is None else data_dir
        target_dir = base / "deploys"
        target_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, event in enumerate(events):
            stamp = event.get("deployed_at") or datetime.now(UTC).isoformat()
            token = stamp.replace(":", "").replace("-", "").replace("+00:00", "Z")
            path = target_dir / f"{token}-{event['agent_id']}-{index}.json"
            path.write_text(
                json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    return _write


@pytest.fixture()
def deployment_history_root(tmp_path):
    """策略工件目录夹具：policies/history/{agent}/{version}.py（回滚目标工件存在性检查用）。

    返回工厂 `_make(agent_id, *versions)`：写入策略源码工件（版本号由内容哈希决定，
    但此处按调用者给定的版本名落盘即可——工件存在性检查只认路径）。
    """

    def _make(agent_id, *versions):
        root = tmp_path / "policies" / "history"
        for version in versions:
            target = root / agent_id / f"{version}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f'"""夹具策略工件：{agent_id}@{version}"""\n\n\nclass Policy:\n'
                '    def solve(self, env, budget):\n        return ""\n',
                encoding="utf-8",
            )
        return root

    return _make


# ---------------------------------------------------------------------------
# 功能 015（短剧形态试水作品）夹具：素材最小可用产物（剧本/ShotList/片段/音轨/成片）、
# 形态配置夹具（movie 精简副本 + shortdrama）、pilot 临时目录、阶段执行入口桩。
# 全部为新增夹具，既有夹具行为不变；素材均为**合成内容**（不使用任何版权素材）。
# ---------------------------------------------------------------------------

PILOT_FORMS = ("movie", "shortdrama")


@pytest.fixture()
def pilot_material_script():
    """剧本素材最小可用产物（ScriptArtifact：2 场景 / 2 角色 / 4 行，含 key 行）。"""
    from agents.screenplay.artifact import ScriptArtifact

    return ScriptArtifact.from_dict(
        {
            "schema_version": "1.0.0",
            "stage": "script",
            "text": "夹具短剧片段：夜班护士发现值班记录被改写。",
            "beats": [
                {"beat_id": "opening_image", "act": "act1", "required": True, "description": "开场"}
            ],
            "scenes": [
                {
                    "scene_id": "scene-1",
                    "heading": "内景 - 护士站 - 夜",
                    "location": "护士站",
                    "time_marker": 0,
                    "characters": ["林静", "陈默"],
                    "axis_base": "A",
                },
                {
                    "scene_id": "scene-2",
                    "heading": "内景 - 走廊 - 夜",
                    "location": "走廊",
                    "time_marker": 1,
                    "characters": ["林静"],
                    "axis_base": "A",
                },
            ],
            "characters": [
                {"name": "林静", "aliases": ["阿静"]},
                {"name": "陈默", "aliases": []},
            ],
            "lines": [
                {
                    "line_id": "s1-l1",
                    "scene_id": "scene-1",
                    "kind": "dialogue",
                    "character": "林静",
                    "text": "这一栏不是我填的。",
                    "key": True,
                    "emotion": "tense",
                },
                {
                    "line_id": "s1-l2",
                    "scene_id": "scene-1",
                    "kind": "action",
                    "character": "陈默",
                    "text": "陈默把记录本翻回上一页。",
                    "key": False,
                    "emotion": "calm",
                },
                {
                    "line_id": "s2-l1",
                    "scene_id": "scene-2",
                    "kind": "dialogue",
                    "character": "林静",
                    "text": "监控里那三分钟是空的。",
                    "key": True,
                    "emotion": "tense",
                },
                {
                    "line_id": "s2-l2",
                    "scene_id": "scene-2",
                    "kind": "action",
                    "character": "林静",
                    "text": "林静把手机屏幕按灭。",
                    "key": False,
                    "emotion": "sorrow",
                },
            ],
        }
    )


@pytest.fixture()
def pilot_material_shotlist():
    """ShotList 素材最小可用产物（2 镜，逐条承接 key 行，景别/机位/运动合法枚举）。"""
    from agents.storyboard.shotlist import ShotList

    return ShotList.from_dict(
        {
            "schema_version": "1.0.0",
            "shots": [
                {
                    "shot_id": "shot-1",
                    "scene_id": "scene-1",
                    "covers": ["s1-l1"],
                    "shot_size": "close_up",
                    "camera": "eye_level",
                    "side": "A",
                    "movement": "static",
                    "est_duration_ms": 1200,
                    "alternatives": 2,
                },
                {
                    "shot_id": "shot-2",
                    "scene_id": "scene-2",
                    "covers": ["s2-l1"],
                    "shot_size": "medium",
                    "camera": "low_angle",
                    "side": "A",
                    "movement": "dolly",
                    "est_duration_ms": 1500,
                    "alternatives": 2,
                },
            ],
        }
    )


@pytest.fixture()
def pilot_material_clip():
    """片段素材最小可用产物：确定性编码的小规格 mp4 字节（64x64 / 4 帧 / 8fps）。"""
    import numpy as np

    from agents.editing.render import encode_mp4_deterministic

    frames = np.zeros((4, 64, 64, 3), dtype=np.uint8)
    frames[:, :, :32] = 90
    frames[:, :, 32:] = 180
    return encode_mp4_deterministic(frames, fps=8)


@pytest.fixture()
def pilot_material_track():
    """音轨素材最小可用产物：(wav 字节, 声学属性元数据) —— 0.5s / 16kHz 单声道。"""
    from agents.sound.audio import synthesize_wav

    return synthesize_wav(
        {"seed": 7, "duration_s": 0.5},
        {"base_freq_hz": 220.0, "harmonics": 4, "duration_seconds": 0.5},
        sample_rate=16000,
    )


@pytest.fixture()
def pilot_material_reel(pilot_material_clip):
    """成片素材最小可用产物：拼接片段帧后的确定性 mp4 字节（与片段同编码档）。"""
    import numpy as np

    from agents.editing.render import encode_mp4_deterministic

    frames = np.concatenate(
        [
            np.full((4, 64, 64, 3), 90, dtype=np.uint8),
            np.full((4, 64, 64, 3), 180, dtype=np.uint8),
        ]
    )
    return encode_mp4_deterministic(frames, fps=8)


@pytest.fixture()
def pilot_materials(
    tmp_path,
    pilot_material_script,
    pilot_material_shotlist,
    pilot_material_clip,
    pilot_material_track,
    pilot_material_reel,
):
    """素材汇总夹具：五类最小可用产物 + 各自落盘路径（同一临时素材根目录下）。"""
    root = tmp_path / "pilot-materials"
    root.mkdir(parents=True, exist_ok=True)
    wav_bytes, track_meta = pilot_material_track
    paths = {
        "script": root / "script.json",
        "shotlist": root / "shotlist.json",
        "clip": root / "clip.mp4",
        "track": root / "track.wav",
        "reel": root / "reel.mp4",
    }
    paths["script"].write_text(
        json.dumps(pilot_material_script.to_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    paths["shotlist"].write_text(
        json.dumps(pilot_material_shotlist.to_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    paths["clip"].write_bytes(pilot_material_clip)
    paths["track"].write_bytes(wav_bytes)
    paths["reel"].write_bytes(pilot_material_reel)
    return {
        "root": root,
        "paths": paths,
        "script": pilot_material_script,
        "shotlist": pilot_material_shotlist,
        "clip_bytes": pilot_material_clip,
        "track_bytes": wav_bytes,
        "track_meta": track_meta,
        "reel_bytes": pilot_material_reel,
    }


@pytest.fixture()
def pilot_form_config_path(tmp_path):
    """形态配置夹具：`movie` 为精简副本（只含加载器必需段 + form），`shortdrama` 为真实配置。

    返回工厂 `_path(form)` → Path。精简副本用于证明"同链双形态"不依赖仓库配置的具体取值；
    短剧侧刻意用真实的 `configs/shortdrama.yaml`（配置完整性由 T1511 另行机检）。
    """

    def _path(form: str):
        if form == "shortdrama":
            return REPO_ROOT / "configs" / "shortdrama.yaml"
        if form != "movie":
            raise ValueError(f"未知形态：{form!r}")
        target = tmp_path / f"configs/{form}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_MINIMAL_MOVIE_CONFIG, encoding="utf-8")
        return target

    return _path


@pytest.fixture()
def pilot_demo_config_path(tmp_path):
    """pilot 演示档形态配置：真实短剧配置的**等值派生**（成片目标时长 120s → 60s）。

    用途：把端到端试水运行的镜头数压到 4（`镜头数 = 目标时长 / 单镜时长`），避开
    连续 16 次 ffmpeg 编码在本机负载下的偶发抖动（单片段编码失败 → 视觉阶段如实 failed）。
    **形态配置的边界不受影响**：短剧真实配置（16 镜）在上限内的断言由
    `tests/unit/test_pilot_stages.py` 的"16 镜上限"边界用例守住。
    """
    source = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    assert "target_duration_s: 120" in source
    target = tmp_path / "configs" / "shortdrama-demo.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.replace("target_duration_s: 120", "target_duration_s: 30"), encoding="utf-8"
    )
    return target


@pytest.fixture()
def pilot_dirs(tmp_path):
    """pilot 临时目录夹具：runs/ 与 packages/（对应仓库 `pilot/` 数据目录结构）。"""
    base = tmp_path / "pilot"
    for sub in ("runs", "packages"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture()
def stage_entrypoint_stub():
    """阶段执行入口桩工厂：可编程产物/成本/候选/失败，并记录调用次数与入参。

    返回工厂 `_make(stage_id, *, products=(), cost_usd=0.0, candidates=(), detail=None,
    fail=None)` → 桩可调用对象（`.calls` 为调用次数、`.inputs` 为历次阶段输入）。
    产物项可为 `ProductRef` 或字典；候选项可为 `CandidateOutcome`、字典
    （`{candidate_id?, score, reasons}`）或字符串（判 0 理由）。桩只依赖
    `core/orchestration` 的模型（零业务概念），故可驱动任意阶段图。
    """
    from core.orchestration.errors import StageFailedError
    from core.orchestration.models import CandidateOutcome, ProductRef, StageOutcome

    def _candidate(stage_id: str, index: int, item) -> CandidateOutcome:
        if isinstance(item, CandidateOutcome):
            return item
        if isinstance(item, str):
            return CandidateOutcome(candidate_id=f"{stage_id}-c{index}", score=0.0, reasons=(item,))
        return CandidateOutcome(
            candidate_id=item.get("candidate_id", f"{stage_id}-c{index}"),
            score=item.get("score", 0.0),
            reasons=tuple(item.get("reasons") or ()),
        )

    def _make(
        stage_id: str,
        *,
        products=(),
        cost_usd: float = 0.0,
        candidates=(),
        detail=None,
        fail: str | None = None,
    ):
        made = tuple(
            item if isinstance(item, ProductRef) else ProductRef(**item) for item in products
        ) or (ProductRef(kind="stub", ref=f"{stage_id}-ref", content_hash="0" * 64),)
        made_candidates = tuple(
            _candidate(stage_id, index, item) for index, item in enumerate(candidates)
        )

        class _Stub:
            def __init__(self) -> None:
                self.calls = 0
                self.inputs: list = []

            def __call__(self, stage_input):
                self.calls += 1
                self.inputs.append(stage_input)
                if fail is not None:
                    raise StageFailedError(fail, candidates=made_candidates)
                return StageOutcome(
                    products=made,
                    cost_usd=cost_usd,
                    candidates=made_candidates,
                    detail=dict(detail or {}),
                )

        stub = _Stub()
        stub.stage_id = stage_id
        return stub

    return _make


# 精简 movie 形态配置：仅含全部加载器必需段（形态差异（权重/规格）在此表达，
# 与短剧真实配置做同链对照；不做任何形态特化，故取值可以刻意不同）。
_MINIMAL_MOVIE_CONFIG = """\
form: movie
evaluator_weights:
  screenplay:
    rule.beat_structure: gate
    proxy.entity_consistency: 1.0
  storyboard:
    rule.shot_grammar: gate
    proxy.emotion_alignment: 1.0
  visual:
    rule.format_compliance: gate
    proxy.aesthetic: 1.0
  sound:
    rule.av_sync: gate
    proxy.asr_transcript: 1.0
  editing:
    rule.duration_compliance: gate
    proxy.pacing_curve: 1.0
  promo:
    rule.material_compliance: gate
    proxy.ctr_history: 1.0
replay:
  worker_count: 4
  latency_quantum_ms: 50
  validation_split: latest_tree
  unbiasedness_tau_threshold: 0.95
  pooling:
    min_trees: 3
    dilution_hit_ratio_threshold: 0.7
    allow_cross_form: false
    enabled_for_dreaming: false
    pools_dir: replay/pools
promo:
  exploration_per_round_usd: 500
  promo_pilot_ratio: 0.02
  default_model: mock-copy-v1
  materials_per_round: 4
  material_spec: {max_copy_chars: 120, poster_size: "1080x1920", max_duration_seconds: 30}
  sensitive_words: ["最"]
  ctr_prior: {alpha: 2.0, beta: 40.0}
  ctr_cap: 0.2
  metric_weights: {ctr: 0.5, completion_rate: 0.3, conversions: 0.2}
  model_prices:
    mock-copy-v1: {prompt_per_1k: 0.001, completion_per_1k: 0.002}
  simulated_platform: {base_impressions: 1000, ctr_beta: [2.0, 40.0], completion_beta: [3.0, 7.0]}
visual:
  exploration_per_round_usd: 500
  clips_per_round: 3
  clip_spec: {width: 320, height: 240, fps: 8, duration_seconds: 2.0, codec: h264}
  frame_sampling: {count: 8, size: 64}
  simulated_gen: {frames: 16, estimated_cost_usd: 0.6, cost_per_clip_usd: 0.5}
  judge:
    anchor_gen_params: [{style: "史诗", shots: 2, seed_tier: 1}]
    prompts: ["哪一段镜头的电影感更强？"]
sound:
  exploration_per_round_usd: 300
  clips_per_round: 4
  loudness:
    dialogue: {target_lufs: -27.0, tolerance: 2.0}
    sfx: {target_lufs: -30.0, tolerance: 3.0}
    music: {target_lufs: -25.0, tolerance: 3.0}
  av_sync_threshold_ms: 120
  sample_rate: 16000
  prices:
    tts: {per_second_usd: 0.02}
    sfx: {per_event_usd: 0.05}
    music: {per_second_usd: 0.03}
  simulated_gen:
    base_freq_hz: 220.0
    harmonics: 4
    duration_seconds: 2.0
    estimated_cost_usd: 0.5
    cost_per_clip_usd: 0.4
  asr: {cer_cap: 0.2}
  emotion: {calibration_band: 0.5}
editing:
  exploration_per_round_usd: 400
  edits_per_round: 3
  target_duration_s: 120
  duration_tolerance_s: 10
  shot_limits: {min_shot_ms: 500, max_shot_ms: 20000}
  transition_rules:
    allowed: [cut, dissolve, fade]
    dissolve_max_ms: 2000
    forbid_jump_cut_within_scene: true
  pacing_baseline:
    d_cap: 2000000.0
    segments:
      - {span: [0.0, 0.15], mean_ms: 1200, var_ms: 160000, weight: 2.0}
      - {span: [0.15, 1.0], mean_ms: 4000, var_ms: 1000000, weight: 1.0}
  render:
    fps: 8
    width: 320
    height: 240
    codec: libx264
    encode_threads: 1
    price_per_second_usd: 0.05
  judge:
    prompts: ["哪一版剪辑的叙事更连贯？"]
    anchor_edls:
      - clips:
          - {shot_id: "anchor-a1", in_ms: 0, out_ms: 1500, transition: {type: cut, duration_ms: 0}}
        audio: []
storyboard:
  exploration_per_round_usd: 200
  boards_per_round: 3
  shot_grammar:
    shot_sizes: [extreme_close_up, close_up, medium, full, wide]
    max_size_jump: 2
    max_same_size_run: 2
    camera_positions: [eye_level, low_angle, high_angle, over_shoulder, side]
    movements: [static, pan, tilt, dolly, handheld]
  axis_rules: {require_transition_on_cross: true, allowed_transition_shots: 1, side_field: side}
  alignment: {cos_floor: 0.90}
  emotion_vectors:
    calm: [0.30, 0.55, 0.45]
    tense: [0.75, 0.18, 0.15]
  render:
    fps: 8
    width: 320
    height: 240
    codec: libx264
    encode_threads: 1
    price_per_shot_usd: 0.06
  judge:
    model: mock-copy-v1
    prompts: ["哪一版分镜的镜头语言更贴合剧本段落？"]
    anchor_shotlists:
      - schema_version: "1.0.0"
        shots:
          - shot_id: shot-anchor-a1
            scene_id: scene-1
            covers: [s1-l1]
            shot_size: close_up
            camera: eye_level
            side: A
            movement: static
            est_duration_ms: 1000
            alternatives: 2
screenplay:
  target_duration_min: 90
  page_tolerance: 5
  lines_per_page: 45
  dialogue_action_ratio: {min: 0.4, max: 0.8}
  beat_sheet:
    - {beat_id: opening_image, act: act1, required: true, description: "开场画面"}
    - {beat_id: climax, act: act3, required: true, description: "高潮"}
  character_aliases:
    林静: [阿静]
  upgrade_criteria: {judge_r_target: 0.6, min_samples: 5, drift_band: 0.1, gate_violation_max: 0.2}
  model: mock-copy-v1
  model_prices:
    mock-copy-v1: {prompt_per_1k: 0.001, completion_per_1k: 0.002}
  judge:
    model: mock-copy-v1
    prompts: ["哪一份大纲的戏剧张力更强？"]
    anchor_outlines:
      - schema_version: "1.0.0"
        stage: outline
        text: "锚点大纲 A：一名外科医生必须在真相与职位之间抉择。"
        beats:
          - {beat_id: opening_image, act: act1, required: true, description: "开场"}
        scenes:
          - scene_id: anchor-a1
            heading: "内景 - 手术室 - 夜"
            location: 手术室
            time_marker: 0
            characters: [林静]
            axis_base: A
        characters:
          - {name: 林静, aliases: [阿静]}
        lines:
          - line_id: a1-l1
            scene_id: anchor-a1
            kind: dialogue
            character: 林静
            text: "别停手。"
            key: true
            emotion: tense
calibration:
  period_days: 7
  top_k: 5
  min_samples: 3
  bias_threshold: 0.15
  reliability_target: 0.6
  ridge_lambda: 1.0
  self_pairing_exclusions:
    platform_truth: ["human.platform_metrics"]
  drift:
    window: 5
    buckets: 10
    psi_threshold: 0.2
    quantile_threshold: 0.1
    min_samples: 3
    suspect_weight: 0.5
    confirmed_exclude: true
    scope_kinds: ["judge"]
    double_signal:
      enabled: true
      reliability_target: 0.6
      base_level: warning
      escalated_level: critical
dreaming:
  candidates_per_round: 128
  demo_candidates: 8
  recent_k: 5
  lambda: 0.5
  epsilon_random: 0.1
  validation_top_ratio: 0.2
  collapse_window: 3
  collapse_threshold: 0.7
  replay_parallelism: 1
  no_auto_evolve_agents: [screenplay, dev]
cost_regression:
  threshold: 0.2
  history_root: dreaming/history
web:
  host: 127.0.0.1
  port: 8080
  dsn_env: CINEFLOW_WEB_DSN
  token: ""
  page_size: 50
  data_dirs:
    policies: policies/history
    dreaming: dreaming/history
    calibration: calibration
    pools: replay/pools
  export_dir: web/dist
deployment:
  mode_default: manual
  gate: {validation_top_ratio: 0.2, require_unbiasedness: true, allow_without_judge: false}
  shadow: {min_days: 14, min_candidates: 20}
  spot_check: {first_n: 5, ratio: 0.2, pending_alert_days: 7}
  screenplay:
    current_policy_version: fa6b7bca77ed
"""


# ---------------------------------------------------------------------------
# 功能 016（LLM 模型档案与角色路由）夹具：配置片段工厂 + 假环境变量
# ---------------------------------------------------------------------------

_LLM_DEEPSEEK = {
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "prices": {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012},
    "price_note": "峰时缓存未命中上限（保守高估）",
}
_LLM_LOCAL = {
    "base_url_env": "LOCAL_LLM_BASE_URL",
    "api_key_env": "LOCAL_LLM_API_KEY",
    "prices": {"prompt_per_1k": 0.0, "completion_per_1k": 0.0},
    "zero_marginal": True,
    "price_note": "自建推理：零边际成本（仍非免费）",
}
_LLM_LEGACY_PRICES = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


@pytest.fixture()
def llm_profiles_config_factory():
    """LLM 配置片段工厂（功能 016 / T1602）：返回 `_make(variant=..., **overrides)` → config dict。

    变体（覆盖契约 C1~C3 的场景与全部"缺项即报错"分支）：

    - `single`：单档案（自动认定默认，注释记入 notes）
    - `multi`：双档案 + 显式默认 + 逐角色映射
    - `multi_no_default`：双档案但缺 `default_profile`（报错路径）
    - `missing_prices` / `missing_endpoint` / `missing_key_env`：三类缺项（各报错）
    - `zero_not_declared`：价目全 0 但未声明 `zero_marginal`（报错路径）
    - `unknown_role`：角色名拼错（枚举外，报错并列合法枚举）
    - `multi_level` / `self_reference` / `unknown_profile`：映射多层 / 自指 / 指向不存在档案
    - `orphan`：多一个未被任何角色引用的档案（notes 列出）
    - `legacy_flat`：**只有**旧扁平写法（`screenplay.model` + `model_prices`）
    - `legacy_and_new`：新旧并存（以新为准 + "旧键被忽略"入 notes）
    - `no_llm_section`：既无 llm 段也无旧写法（报错路径）
    """

    def _make(variant: str = "single", **overrides) -> dict:
        import copy

        if variant == "legacy_flat":
            return {
                "form": "movie",
                "screenplay": {
                    "model": "mock-copy-v1",
                    "model_prices": copy.deepcopy(_LLM_LEGACY_PRICES),
                },
            }
        if variant == "legacy_missing_prices":
            return {"form": "movie", "screenplay": {"model": "ghost-model", "model_prices": {}}}
        if variant == "no_llm_section":
            return {"form": "movie", "screenplay": {"model": "mock-copy-v1"}}

        profiles: dict = {"deepseek-flash": copy.deepcopy(_LLM_DEEPSEEK)}
        roles: dict = {"generation": "deepseek-flash", "judge": "deepseek-flash"}
        default: object = None
        if variant in {"multi", "multi_no_default", "orphan"}:
            profiles["local-qwen"] = copy.deepcopy(_LLM_LOCAL)
            roles["dreaming_candidates"] = "local-qwen"
            default = "deepseek-flash"
            if variant == "multi_no_default":
                default = None
            if variant == "orphan":
                profiles["unused-profile"] = {
                    "base_url": "https://unused.example.com",
                    "api_key_env": "UNUSED_KEY",
                    "prices": {"prompt_per_1k": 0.01, "completion_per_1k": 0.02},
                    "price_note": "未被任何角色引用",
                }
        elif variant == "missing_prices":
            profiles["deepseek-flash"].pop("prices")
        elif variant == "missing_endpoint":
            profiles["deepseek-flash"].pop("base_url")
        elif variant == "missing_key_env":
            profiles["deepseek-flash"].pop("api_key_env")
        elif variant == "zero_not_declared":
            profiles["deepseek-flash"]["prices"] = {
                "prompt_per_1k": 0.0,
                "completion_per_1k": 0.0,
            }
        elif variant == "unknown_role":
            roles["judeg"] = "deepseek-flash"
        elif variant == "multi_level":
            roles["judge"] = {"primary": "deepseek-flash"}
        elif variant == "self_reference":
            roles["judge"] = "judge"
        elif variant == "unknown_profile":
            roles["judge"] = "ghost-profile"

        section: dict = {"profiles": profiles, "roles": roles}
        if default is not None:
            section["default_profile"] = default
        config: dict = {"form": "movie", "llm": section}
        if variant == "legacy_and_new":
            config["screenplay"] = {
                "model": "mock-copy-v1",
                "model_prices": copy.deepcopy(_LLM_LEGACY_PRICES),
            }
        config.update(overrides)
        return config

    return _make


@pytest.fixture()
def llm_env(monkeypatch):
    """假环境变量夹具（功能 016 / T1602）：显式设置/清除档案相关变量。

    返回 `_set(**flags)`：flags 里 `unrelated_openai=True` 模拟**环境中存在无关
    `OPENAI_API_KEY`**（本机真实事故：平台自带变量被误判为档案就绪）；其余键值按名设置。
    """

    def _set(*, unrelated_openai: bool = False, **values: str) -> None:
        for name in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "DEEPSEEK_API_KEY",
            "LOCAL_LLM_BASE_URL",
            "LOCAL_LLM_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        if unrelated_openai:
            monkeypatch.setenv("OPENAI_API_KEY", "unrelated-value-from-host")
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return _set


@pytest.fixture()
def stub_factory():
    """本地 stub 平台工厂（真实监听 127.0.0.1 随机端口；测试结束关闭）。

    供协议/注入类单测共用（渲染/生成/宣发/LLM 端点见 `tests/stubs_http.py`）。
    """
    from tests.stubs_http import StubPlatformServer

    servers: list[StubPlatformServer] = []

    def _make(**kwargs) -> StubPlatformServer:
        server = StubPlatformServer(**kwargs).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()
