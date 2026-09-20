"""剪辑做梦一轮接入验证（功能 007 / T731，C16）。

agent_id="editing" 演示档 M=8 跑 dreaming 管线：候选生成 → 静态检查 →
沙箱回放（测试档 in_process_replay，同一管线函数）→ reward 排名 →
首轮基线落盘 history_root/editing/dream-editing-1.json。
research 决策 8：dreaming 对 agent_id 已泛化（005/006 实证），本套件验证
零改动接入；champion 策略与谱系（policies/history/editing/）单源加载（T730 落盘）。
"""

import importlib.util
import json
from pathlib import Path

from agents.editing.edl import EditDecisionList
from core.replay.pool import SimulatorPool
from core.tree.models import CostRecord, NodeStatus, new_id
from dreaming.candidates import MutatorGenerator
from dreaming.lineage import validate_meta
from dreaming.pipeline import in_process_replay, run_dream_round
from policies.versioning import policy_version

REPO_ROOT = Path(__file__).resolve().parents[2]
EDITING_POLICY_DIR = REPO_ROOT / "policies" / "history" / "editing"


def _champion():
    """champion 单源加载：目录内唯一 {version}.py + 配套 meta.json。"""
    sources = sorted(EDITING_POLICY_DIR.glob("*.py"))
    assert len(sources) == 1, "policies/history/editing/ 应恰有一个 champion 版本文件"
    path = sources[0]
    source = path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("editing_champion", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return path.stem, source, module._GRID


class TestChampion落盘纪律:
    def test_版本号等于内容哈希(self):
        version, source, _ = _champion()
        assert version == policy_version(source)  # 文件名 = 源码 BLAKE3 前 12 位

    def test_meta_谱系首版合法(self):
        version, _, _ = _champion()
        meta = json.loads((EDITING_POLICY_DIR / f"{version}.meta.json").read_text(encoding="utf-8"))
        validate_meta(meta)  # schema 校验（dreaming/lineage 口径）
        assert meta["version"] == version
        assert meta["parent_version"] is None  # 首版谱系根
        assert meta["source"] == "manual"

    def test_网格为合法_EDL(self):
        """网格 3 组（对齐 edits_per_round）：EDL 形状合法 + 分区顺序不降。"""
        from agents.editing.edl import EditDecisionList

        _, _, grid = _champion()
        assert len(grid) == 3
        for item in grid:
            edl = EditDecisionList.from_dict(item)  # 形状校验（构造即拒绝非法）
            assert len(edl.clips) >= 2


def _dream_pool(tree_store, make_tree, make_node, grid, n_trees=3):
    """剪辑做梦池：N 棵不同时间树，子节点 gen_params = champion EDL 网格（逐树得分递增）。"""
    trees = []
    for t in range(n_trees):
        tree = make_tree(
            agent_id="editing",
            project_id="editing-dream",
            config_snapshot={
                "evaluator_weights": {"rule.x": 0.0},
                "observation_fields": ["gen_params", "edl_hash", "job_id"],
            },
        )
        tree_store.create_tree(tree)
        tree_store.append_node(
            make_node(
                node_id=tree.root_id,
                tree_id=tree.tree_id,
                agent_id="editing",
                eval_breakdown={},
                score=0.4,
                status=NodeStatus.EVALUATED,
                created_at=float(t * 10),
            )
        )
        for i, edl_dict in enumerate(grid):
            tree_store.append_node(
                make_node(
                    node_id=new_id(),
                    tree_id=tree.tree_id,
                    parent_id=tree.root_id,
                    depth=1,
                    agent_id="editing",
                    observation_context={
                        "gen_params": edl_dict,  # 回放匹配槽（与执行器落盘形态一致）
                        "edl_hash": EditDecisionList.from_dict(edl_dict).edl_hash(),
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
            "editing",
            champion_source,
            MutatorGenerator("editing-seed-1"),
            pool,
            None,  # MutatorGenerator 不调 LLM（LLMGenerator 为生产形态）
            dream_config,
            replay_fn=in_process_replay,
            history_root=history_root,
            m=dream_config.demo_candidates,
        )

        assert dream_round.agent_id == "editing"
        assert dream_round.champion_version == version
        assert dream_config.demo_candidates == 8
        # 候选：M=8 全量生成（变异器占位：头部注释区分版本，行为同 champion）
        assert dream_round.diagnostics["m_requested"] == 8
        assert len(dream_round.candidates) == 8
        # 静态检查 → 回放 → reward 排名全链路产出
        passed = [c for c in dream_round.candidates if c.static_check == "passed"]
        assert passed, "champion 网格候选应过静态检查"
        assert any(c.trajectory is not None for c in passed)
        assert dream_round.winner_version is not None
        winner = next(c for c in dream_round.candidates if c.version == dream_round.winner_version)
        assert winner.reward is not None
        # 零生成审计（FR-012）：全程渲染/LLM 调用恒 0
        assert dream_round.diagnostics["zero_generation"] is True
        assert dream_round.diagnostics["generation_api_calls"] == 0
        # 首轮基线落盘：history_root/editing/dream-editing-1.json（只增不改）
        baseline = history_root / "editing" / "dream-editing-1.json"
        assert baseline.exists()
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload["agent_id"] == "editing"
        assert payload["round_id"] == "dream-editing-1"
        assert payload["winner_version"] == dream_round.winner_version

    def test_二轮_seq_递增不覆盖(self, tree_store, make_tree, make_node, dream_config, tmp_path):
        """落盘只增不改：二轮产出 dream-editing-2.json，首轮基线原样保留。"""
        _, champion_source, grid = _champion()
        pool = _dream_pool(tree_store, make_tree, make_node, grid)
        history_root = tmp_path / "dreaming" / "history"
        for seed in ("editing-seed-1", "editing-seed-2"):
            run_dream_round(
                "editing",
                champion_source,
                MutatorGenerator(seed),
                pool,
                None,
                dream_config,
                replay_fn=in_process_replay,
                history_root=history_root,
                m=dream_config.demo_candidates,
            )
        first = history_root / "editing" / "dream-editing-1.json"
        second = history_root / "editing" / "dream-editing-2.json"
        assert first.exists() and second.exists()
        assert json.loads(first.read_text(encoding="utf-8"))["round_id"] == "dream-editing-1"

    def test_dreaming_零改动证明(self):
        """dreaming 管线无 editing 特判分支（泛化缺口 = 0 的静态证明）。"""
        import inspect

        import dreaming.pipeline

        source = inspect.getsource(dreaming.pipeline)
        assert '"editing"' not in source and "'editing'" not in source
