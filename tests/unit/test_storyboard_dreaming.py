"""分镜做梦一轮接入验证（功能 008 / T831，C16）。

agent_id="storyboard" 演示档 M=8 跑 dreaming 管线：候选生成 → 静态检查 →
沙箱回放（测试档 in_process_replay，同一管线函数）→ reward 排名 →
首轮基线落盘 history_root/storyboard/dream-storyboard-1.json。
research 决策 8：dreaming 对 agent_id 已泛化（005/006/007 实证），本套件验证
零改动接入；champion 策略与谱系（policies/history/storyboard/）单源加载（T830 落盘）。

champion 网格是"剧本 + 规则约束 → ShotList"的手工首版：**必须过三门禁**
（景别语法/覆盖率/轴规则），否则网格候选在真实评估中恒定 0 分、进化失去信号。
"""

import importlib.util
import json
from pathlib import Path

from agents.storyboard.shotlist import ShotList
from core.evaluators.base import ArtifactRef
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus, new_id
from dreaming.candidates import MutatorGenerator
from dreaming.lineage import validate_meta
from dreaming.pipeline import in_process_replay, run_dream_round
from policies.versioning import policy_version

REPO_ROOT = Path(__file__).resolve().parents[2]
STORYBOARD_POLICY_DIR = REPO_ROOT / "policies" / "history" / "storyboard"


def _champion():
    """champion 单源加载：目录内唯一 {version}.py + 配套 meta.json。"""
    sources = sorted(STORYBOARD_POLICY_DIR.glob("*.py"))
    assert len(sources) == 1, "policies/history/storyboard/ 应恰有一个 champion 版本文件"
    path = sources[0]
    source = path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("storyboard_champion", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path.stem, source, module._GRID


class TestChampion落盘纪律:
    def test_版本号等于内容哈希(self):
        version, source, _ = _champion()
        assert version == policy_version(source)  # 文件名 = 源码 BLAKE3 前 12 位

    def test_meta_谱系首版合法(self):
        version, _, _ = _champion()
        meta_path = STORYBOARD_POLICY_DIR / f"{version}.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        validate_meta(meta)  # schema 校验（dreaming/lineage 口径）
        assert meta["version"] == version
        assert meta["parent_version"] is None  # 首版谱系根
        assert meta["source"] == "manual"
        assert meta["approval"]["decision"] == "approved"  # 人工闸门不变

    def test_网格为合法_ShotList(self):
        """网格 3 组（对齐 boards_per_round）：ShotList 形状合法。"""
        _, _, grid = _champion()
        assert len(grid) == 3
        for item in grid:
            shotlist = ShotList.from_dict(item)  # 形状校验（构造即拒绝非法）
            assert len(shotlist.shots) >= 3
            rebuilt = ShotList.from_dict(shotlist.to_dict())
            assert rebuilt.shotlist_hash() == shotlist.shotlist_hash()

    def test_网格过三门禁(self, script_segment, storyboard_config):
        """champion 网格必须过三门禁（景别语法/覆盖率/轴规则）——进化信号有效的前提。"""
        from agents.storyboard.evaluators.axis_rule import AxisRuleEvaluator
        from agents.storyboard.evaluators.coverage import CoverageEvaluator
        from agents.storyboard.evaluators.shot_grammar import ShotGrammarEvaluator

        _, _, grid = _champion()
        artifact = ArtifactRef(artifact_hash="ab" * 32)
        gates = [
            ShotGrammarEvaluator(storyboard_config.shot_grammar),
            CoverageEvaluator(storyboard_config.axis_rules),
            AxisRuleEvaluator(storyboard_config.axis_rules),
        ]
        for item in grid:
            ctx = {"shotlist": ShotList.from_dict(item), "script": script_segment}
            for gate in gates:
                assert gate.evaluate(artifact, ctx).score == 1.0, gate.spec.evaluator_id

    def test_网格备选数与档位齐全(self, storyboard_config):
        """下游视觉线消费面（alternatives 备选数）与档位枚举在网格内齐备。"""
        _, _, grid = _champion()
        grammar = storyboard_config.shot_grammar
        sizes, cameras, movements = set(), set(), set()
        for item in grid:
            for shot in ShotList.from_dict(item).shots:
                assert shot.alternatives >= 1
                assert shot.shot_size in grammar["shot_sizes"]
                assert shot.camera in grammar["camera_positions"]
                assert shot.movement in grammar["movements"]
                sizes.add(shot.shot_size)
                cameras.add(shot.camera)
                movements.add(shot.movement)
        assert len(sizes) >= 2 and len(cameras) >= 2 and len(movements) >= 2


def _dream_pool(tree_store, make_tree, make_node, grid, n_trees=3):
    """分镜做梦池：N 棵不同时间树，子节点 gen_params = champion ShotList 网格（得分递增）。"""
    trees = []
    for t in range(n_trees):
        tree = make_tree(
            agent_id="storyboard",
            project_id="storyboard-dream",
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "shotlist", "shotlist_hash", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="storyboard",
                eval_breakdown={},
                score=0.4,
                status=NodeStatus.EVALUATED,
                created_at=float(t * 10),
            )
        )
        for i, shotlist_dict in enumerate(grid):
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="storyboard",
                    observation_context={
                        "gen_params": shotlist_dict,  # 回放匹配槽（与执行器落盘形态一致）
                        "shotlist": shotlist_dict,
                        "shotlist_hash": ShotList.from_dict(shotlist_dict).shotlist_hash(),
                        "job_id": f"dream-j{i}",
                    },
                    score=0.5 + 0.05 * t + 0.05 * i,
                    cost=CostRecord(llm_calls=1),  # 历史虚拟成本（渲染调用不入回放审计口径）
                    status=NodeStatus.EVALUATED,
                    created_at=float(t * 10 + 1 + i),
                )
            )
        trees.append(tree)
    pool = SimulatorPool(tree_store)
    for tree in trees:
        pool.add_tree(tree)
    return pool


class Test做梦一轮接入:
    def test_演示档_M8_全流程(self, tree_store, make_tree, make_node, dream_config, tmp_path):
        """C16 场景 3：一轮做梦（M=8）→ reward 排名 + 首轮基线落盘，LLM/渲染零调用。"""
        version, champion_source, grid = _champion()
        pool = _dream_pool(tree_store, make_tree, make_node, grid)
        history_root = tmp_path / "dreaming" / "history"

        dream_round = run_dream_round(
            "storyboard",
            champion_source,
            MutatorGenerator("storyboard-seed-1"),
            pool,
            None,  # MutatorGenerator 不调 LLM（LLMGenerator 为生产形态）
            dream_config,
            replay_fn=in_process_replay,
            history_root=history_root,
            m=dream_config.demo_candidates,
        )

        assert dream_round.agent_id == "storyboard"
        assert dream_round.champion_version == version
        assert dream_config.demo_candidates == 8
        assert dream_round.diagnostics["m_requested"] == 8
        assert len(dream_round.candidates) == 8
        passed = [c for c in dream_round.candidates if c.static_check == "passed"]
        assert passed, "champion 网格候选应过静态检查"
        assert any(c.trajectory is not None for c in passed)
        assert dream_round.winner_version is not None
        winner = next(c for c in dream_round.candidates if c.version == dream_round.winner_version)
        assert winner.reward is not None
        # 零生成审计（FR-012）：全程渲染/LLM 调用恒 0
        assert dream_round.diagnostics["zero_generation"] is True
        assert dream_round.diagnostics["generation_api_calls"] == 0
        # 首轮基线落盘：history_root/storyboard/dream-storyboard-1.json（只增不改）
        baseline = history_root / "storyboard" / "dream-storyboard-1.json"
        assert baseline.exists()
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload["agent_id"] == "storyboard"
        assert payload["round_id"] == "dream-storyboard-1"
        assert payload["winner_version"] == dream_round.winner_version
        assert payload["champion_version"] == version

    def test_二轮_seq_递增不覆盖(self, tree_store, make_tree, make_node, dream_config, tmp_path):
        """落盘只增不改：二轮产出 dream-storyboard-2.json，首轮基线原样保留。"""
        _, champion_source, grid = _champion()
        pool = _dream_pool(tree_store, make_tree, make_node, grid)
        history_root = tmp_path / "dreaming" / "history"
        for seed in ("storyboard-seed-1", "storyboard-seed-2"):
            run_dream_round(
                "storyboard",
                champion_source,
                MutatorGenerator(seed),
                pool,
                None,
                dream_config,
                replay_fn=in_process_replay,
                history_root=history_root,
                m=dream_config.demo_candidates,
            )
        first = history_root / "storyboard" / "dream-storyboard-1.json"
        second = history_root / "storyboard" / "dream-storyboard-2.json"
        assert first.exists() and second.exists()
        assert json.loads(first.read_text(encoding="utf-8"))["round_id"] == "dream-storyboard-1"

    def test_dreaming_零改动证明(self):
        """dreaming 管线无 storyboard 特判分支（泛化缺口 = 0 的静态证明）。"""
        import inspect

        import dreaming.pipeline

        source = inspect.getsource(dreaming.pipeline)
        assert '"storyboard"' not in source and "'storyboard'" not in source


class Test候选回放语义:
    def test_候选回放不产生渲染与生成调用(
        self, tree_store, make_tree, make_node, dream_config, tmp_path
    ):
        """回放纪律：候选轨迹的生成调用恒 0（原则三：回放零渲染零生成）。"""
        _, champion_source, grid = _champion()
        pool = _dream_pool(tree_store, make_tree, make_node, grid)
        dream_round = run_dream_round(
            "storyboard",
            champion_source,
            MutatorGenerator("storyboard-seed-3"),
            pool,
            None,
            dream_config,
            replay_fn=in_process_replay,
            history_root=tmp_path / "dreaming" / "history",
            m=4,
        )
        replayed = [c for c in dream_round.candidates if c.trajectory is not None]
        assert replayed
        for candidate in replayed:
            assert candidate.trajectory["total_cost"]["generation_api_calls"] == 0
            assert candidate.trajectory["best_score_curve"]  # 命中历史节点 → 曲线非空
