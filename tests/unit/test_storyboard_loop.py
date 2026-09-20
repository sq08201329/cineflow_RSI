"""分镜线上探索执行器单测（功能 008 / T816，先于实现编写）。

C1/C2 全场景：非法 ShotList 四类执行前拒绝（0 渲染 0 成本）；一轮 3 组 ShotList 落树、
成本入账（镜头数 × 价目，实际 ≤ 预估）；预算超界拒绝且已执行照常入账；同 round_id
二次触发幂等重建（0 重复渲染 0 重复扣费）；渲染失败条目 failed + 成本照计；剧本不足
预检拒绝注明（0 副作用）；双键观测（shotlist + gen_params，002 回放匹配槽零特判）；
freeze_round_tree 终态门禁。
评估器桩注入（loop 面向评估器协议编程，US2 才接真实五评估器）。
"""

import copy
from pathlib import Path

import pytest
import yaml
from sqlalchemy import insert, select

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.db import storyboard_render_jobs
from agents.storyboard.loop import (
    StoryboardLoopError,
    freeze_round_tree,
    round_tree_id,
    run_storyboard_round,
)
from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
from agents.storyboard.shotlist import ShotList
from core.replay.matching import node_gen_params
from core.tree.models import NodeStatus
from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_SHOT_SIZES = ["extreme_close_up", "close_up", "medium", "full", "wide"]
_MOVEMENTS = ["static", "pan", "tilt", "dolly", "handheld"]


def _config_dict(**overrides) -> dict:
    """合法配置字典：渲染尺寸缩小提速（64x48）；其余按需覆盖。"""
    config = copy.deepcopy(_REAL_CONFIG)
    config["storyboard"]["render"].update(width=64, height=48)
    for key, value in overrides.items():
        config["storyboard"][key] = value
    return config


def _config(**overrides) -> StoryboardConfig:
    return StoryboardConfig.from_dict(_config_dict(**overrides))


class _StubPolicy:
    """策略桩：产出给定 ShotList 组合（做梦层接入前的手工策略形态）。"""

    policy_version = "stub-storyboard-v1"

    def __init__(self, shotlists):
        self._shotlists = list(shotlists)

    def plan(self, config, inputs):
        return list(self._shotlists)


def _stubs():
    """评估器桩三件套（gate/proxy/judge 各一，权重键取 config 内键名）。"""
    return [
        StubRuleEvaluator("rule.shot_grammar"),
        StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
        StubJudgeEvaluator("judge.script_fit", score=0.7),
    ]


@pytest.fixture()
def config():
    return _config()


@pytest.fixture()
def script(make_script_segment):
    return make_script_segment()


@pytest.fixture()
def adapter():
    return SimulatedStoryboardRenderer()


def _three_shotlists(make_shotlist) -> list[ShotList]:
    """三组哈希不同的合法 ShotList：基准 / 时长整体 +125ms / 去掉 shot-05（8 镜）。"""
    base = make_shotlist()
    shots_b = [
        {**shot.to_dict(), "est_duration_ms": shot.est_duration_ms + 125} for shot in base.shots
    ]
    shots_c = [
        {
            **shot.to_dict(),
            "shot_size": _SHOT_SIZES[(_SHOT_SIZES.index(shot.shot_size) + 1) % len(_SHOT_SIZES)],
            "movement": _MOVEMENTS[(_MOVEMENTS.index(shot.movement) + 2) % len(_MOVEMENTS)],
        }
        for shot in base.shots
        if shot.shot_id != "shot-05"  # scene-2 仍由 shot-04/shot-06 承接（合法）
    ]
    return [base, ShotList(shots=shots_b), ShotList(shots=shots_c)]


def _run(
    round_id,
    policy,
    store,
    artifacts,
    adapter,
    engine,
    config,
    script,
    evaluators=None,
):
    return run_storyboard_round(
        round_id=round_id,
        policy=policy,
        store=store,
        artifacts=artifacts,
        adapter=adapter,
        engine=engine,
        config=config,
        inputs={"script": script},
        evaluators=_stubs() if evaluators is None else evaluators,
    )


def _job_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(storyboard_render_jobs)).all()


def _board_nodes(store, tree_id):
    return [n for n in store.nodes_of(tree_id) if n.parent_id is not None]


class Test一轮分镜落树:
    def test_三组_ShotList_落树且成本入账(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        shotlists = _three_shotlists(make_shotlist)
        result = _run(
            "r1",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        assert [j["status"] for j in result.jobs] == ["inserted"] * 3
        assert result.budget_cap_usd == config.exploration_per_round_usd
        assert result.spent_usd == pytest.approx(adapter.total_spent)
        assert result.spent_usd > 0
        assert result.cost_reconciliation["consistent"] is True
        assert result.precheck == "ok"
        # 树：根 + 3 个已评估分镜节点（animatic mp4 内容寻址可回读）
        nodes = _board_nodes(tree_store, result.tree_id)
        assert len(nodes) == 3
        for node in nodes:
            assert node.status is NodeStatus.EVALUATED
            assert artifact_store.get(node.artifact_hash)  # mp4 可回读
            # 评估器桩三分量入 breakdown；合成 = (0.5×0.8 + 0.5×0.7)/1.0
            assert len(node.eval_breakdown) == 3
            assert node.score == pytest.approx(0.75)
            assert node.cost.generation_api_calls == 1
            assert node.cost.generation_api_cost_usd > 0
        # 运营表终态 inserted + 唯一键分量 shotlist_hash
        rows = _job_rows(storyboard_jobs_engine)
        assert {row.status for row in rows} == {"inserted"}
        assert {row.shotlist_hash for row in rows} == {sl.shotlist_hash() for sl in shotlists}
        assert result.spent_usd == pytest.approx(
            sum(len(sl.shots) for sl in shotlists) * config.render["price_per_shot_usd"]
        )

    def test_落树节点的渲染元数据可评估读取(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """双键观测与帧哈希随节点落盘：评估器读同一函数产出（禁止两套帧）。"""
        result = _run(
            "r1b",
            _StubPolicy(_three_shotlists(make_shotlist)[:1]),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        node = _board_nodes(tree_store, result.tree_id)[0]
        assert len(node.observation_context["shotlist"]["shots"]) == 9


class Test观测双键:
    def test_shotlist_与_gen_params_双键落观测(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """002 规范化精确匹配固定读 gen_params：分镜侧同一内容落双键（007 教训复用）。"""
        shotlists = _three_shotlists(make_shotlist)
        result = _run(
            "r1c",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        nodes = _board_nodes(tree_store, result.tree_id)
        by_hash = {n.observation_context["shotlist_hash"]: n for n in nodes}
        assert set(by_hash) == {sl.shotlist_hash() for sl in shotlists}
        for shotlist in shotlists:
            node = by_hash[shotlist.shotlist_hash()]
            context = node.observation_context
            assert context["shotlist"] == shotlist.to_dict()
            assert context["gen_params"] == shotlist.to_dict()  # 回放匹配槽
            assert node_gen_params(node) == shotlist.to_dict()
            assert context["job_id"]

    def test_快照白名单含_gen_params(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        result = _run(
            "r1d",
            _StubPolicy(_three_shotlists(make_shotlist)[:1]),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        trees = tree_store.trees_by(agent_id="storyboard")
        tree = next(t for t in trees if t.tree_id == result.tree_id)
        assert "gen_params" in tree.config_snapshot["observation_fields"]
        assert "shotlist" in tree.config_snapshot["observation_fields"]
        assert tree.config_snapshot["evaluator_weights"] == config.evaluator_weights


class Test非法ShotList执行前拒绝:
    @pytest.mark.parametrize(
        "variant",
        ["unknown_line", "scene_uncovered", "key_line_uncovered", "size_out_of_range"],
    )
    def test_四类非法变体零渲染零成本(
        self,
        variant,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """C1：违规一律执行前拒绝——适配器 0 调用、运营表 0 行、0 成本。"""
        result = _run(
            f"bad-{variant}",
            _StubPolicy([make_shotlist(variant)]),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        assert result.jobs[0]["status"] == "rejected"
        assert "ShotList 校验拒绝" in result.jobs[0]["reason"]
        assert adapter.render_calls == 0
        assert result.spent_usd == 0.0
        assert _job_rows(storyboard_jobs_engine) == []
        # 拒绝节点如实落盘（score=0，注明原因）
        nodes = _board_nodes(tree_store, result.tree_id)
        assert len(nodes) == 1
        assert nodes[0].score == 0.0
        assert nodes[0].observation_context["reject_reason"]

    def test_形状非法_ShotList_同样拒绝(
        self,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """策略产出形状非法（alternatives 缺失）→ 执行前拒绝，不静默补默认值。"""
        bad = {
            "shots": [
                {
                    "shot_id": "shot-x",
                    "scene_id": "scene-1",
                    "covers": ["s1-l1"],
                    "shot_size": "medium",
                    "camera": "eye_level",
                    "side": "A",
                    "movement": "static",
                    "est_duration_ms": 1000,
                }
            ]
        }
        result = _run(
            "bad-shape",
            _StubPolicy([bad]),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        assert result.jobs[0]["status"] == "rejected"
        assert "形状非法" in result.jobs[0]["reason"]
        assert adapter.render_calls == 0


class Test预算门禁:
    def test_超界拒绝且已执行照常入账(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """C2 场景 2：预算只够一组——后续拒绝，已渲染的照常入账。"""
        shotlists = _three_shotlists(make_shotlist)
        first_estimate = adapter.estimate(shotlists[0], config)
        capped = _config(exploration_per_round_usd=first_estimate * 1.2)
        result = _run(
            "r2",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            capped,
            script,
        )
        assert result.jobs[0]["status"] == "inserted"
        assert [j["status"] for j in result.jobs[1:]] == ["rejected", "rejected"]
        assert "预算门禁" in result.jobs[1]["reason"]
        assert adapter.render_calls == 1
        assert result.spent_usd == pytest.approx(adapter.total_spent)
        assert result.cost_reconciliation["consistent"] is True

    def test_首组即超界则零渲染(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
    ):
        capped = _config(exploration_per_round_usd=0.01)  # 低于单组预估 0.54
        result = _run(
            "r2b",
            _StubPolicy([make_shotlist()]),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            capped,
            script,
        )
        assert result.jobs[0]["status"] == "rejected"
        assert adapter.render_calls == 0
        assert result.spent_usd == 0.0


class Test幂等重建:
    def test_同_round_id_二次触发零重复(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """C2 场景 3：唯一键 (round_id, shotlist_hash) + 确定性 id 派生——重建首轮结果。"""
        shotlists = _three_shotlists(make_shotlist)
        first = _run(
            "r3",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        second = _run(
            "r3",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        assert second.jobs == first.jobs
        assert second.tree_id == first.tree_id
        assert second.spent_usd == pytest.approx(first.spent_usd)
        assert adapter.render_calls == 3  # 0 重复渲染
        assert len(tree_store.nodes_of(first.tree_id)) == 4  # 0 重复节点
        assert len(_job_rows(storyboard_jobs_engine)) == 3  # 0 重复行


class Test渲染失败:
    def test_失败条目_failed_且成本照计(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        storyboard_jobs_engine,
        config,
    ):
        """C2 场景 4：渲染失败 → status=failed + 预估成本照常入账，轮次继续。"""
        adapter = SimulatedStoryboardRenderer(fail_on_shots=("shot-05",))
        shotlists = _three_shotlists(make_shotlist)  # 第 3 组不含 shot-05
        result = _run(
            "r4",
            _StubPolicy(shotlists),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        assert [j["status"] for j in result.jobs] == ["failed", "failed", "inserted"]
        assert "渲染失败" in result.jobs[0]["reason"]
        price = config.render["price_per_shot_usd"]
        expected = (
            adapter.estimate(shotlists[0], config)
            + adapter.estimate(shotlists[1], config)
            + len(shotlists[2].shots) * price
        )
        assert result.spent_usd == pytest.approx(expected)
        nodes = _board_nodes(tree_store, result.tree_id)
        failed = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert len(failed) == 2
        assert all(n.score is None for n in failed)
        assert all("渲染失败" in n.observation_context["reject_reason"] for n in failed)
        rows = {row.job_id: row for row in _job_rows(storyboard_jobs_engine)}
        assert sum(1 for row in rows.values() if row.status == "failed") == 2
        assert all(row.error for row in rows.values() if row.status == "failed")


class Test崩溃隔离:
    def test_评估器崩溃_节点FAILED_轮次继续(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """SC-006 口径：任一评估器崩溃 → 该节点 FAILED（渲染成本照常入账），轮次继续。"""

        class _CrashOnThird(StubRuleEvaluator):
            def evaluate(self, artifact, context):
                if len(context["shotlist"].shots) == 8:  # 第三组变体
                    raise RuntimeError("评估器崩溃模拟")
                return super().evaluate(artifact, context)

        evaluators = [
            _CrashOnThird("rule.shot_grammar"),
            StubProxyEvaluator("proxy.emotion_alignment", score=0.8),
            StubJudgeEvaluator("judge.script_fit", score=0.7),
        ]
        result = _run(
            "r9",
            _StubPolicy(_three_shotlists(make_shotlist)),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
            evaluators=evaluators,
        )
        assert [j["status"] for j in result.jobs] == ["inserted", "inserted", "failed"]
        assert "评估器崩溃" in result.jobs[2]["reason"]
        nodes = _board_nodes(tree_store, result.tree_id)
        failed = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert len(failed) == 1
        assert failed[0].score is None
        assert failed[0].cost.generation_api_cost_usd > 0  # 渲染成本照常入账
        assert "评估器崩溃" in failed[0].observation_context["reject_reason"]
        # 渲染已成功落账：该 job 终态 inserted（节点 FAILED 不触发重复渲染）
        rows = {row.job_id: row for row in _job_rows(storyboard_jobs_engine)}
        assert rows["r9-j2"].status == "inserted"
        assert result.cost_reconciliation["consistent"] is True


class Test剧本不足预检:
    def test_空剧本执行前拒绝注明(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """C2 场景 5：剧本为空/无行 → 执行前拒绝注明"剧本不足"（0 渲染 0 树）。"""
        with pytest.raises(StoryboardLoopError, match="剧本不足"):
            _run(
                "r5",
                _StubPolicy([make_shotlist()]),
                tree_store,
                artifact_store,
                adapter,
                storyboard_jobs_engine,
                config,
                make_script_segment("empty_scenes"),
            )
        assert adapter.render_calls == 0
        assert tree_store.trees_by(agent_id="storyboard") == []
        assert _job_rows(storyboard_jobs_engine) == []

    def test_情绪取值非法预检拒绝(
        self,
        make_shotlist,
        make_script_segment,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """情绪取值配置化：未登记情绪（不在向量表）→ 执行前拒绝，0 副作用。"""
        with pytest.raises(StoryboardLoopError, match="melancholic"):
            _run(
                "r5b",
                _StubPolicy([make_shotlist()]),
                tree_store,
                artifact_store,
                adapter,
                storyboard_jobs_engine,
                config,
                make_script_segment("bad_emotion"),
            )
        assert adapter.render_calls == 0
        assert tree_store.trees_by(agent_id="storyboard") == []

    def test_缺剧本输入拒绝(self, tree_store, artifact_store, adapter, storyboard_jobs_engine):
        with pytest.raises(StoryboardLoopError, match="script"):
            run_storyboard_round(
                round_id="r5c",
                policy=_StubPolicy([]),
                store=tree_store,
                artifacts=artifact_store,
                adapter=adapter,
                engine=storyboard_jobs_engine,
                config=_config(),
                inputs={},
                evaluators=_stubs(),
            )


class Test冻结入池门禁:
    def test_全终态可冻结(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        _run(
            "r7",
            _StubPolicy(_three_shotlists(make_shotlist)),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        tree = freeze_round_tree("r7", tree_store, storyboard_jobs_engine)
        assert tree.tree_id == round_tree_id("r7")
        assert tree.agent_id == "storyboard"

    def test_未终态拒绝冻结(
        self,
        make_shotlist,
        script,
        tree_store,
        artifact_store,
        adapter,
        storyboard_jobs_engine,
        config,
    ):
        """004/006/007 同构：job 未到 inserted/failed 终态不得冻结入池。"""
        result = _run(
            "r8",
            _StubPolicy(_three_shotlists(make_shotlist)),
            tree_store,
            artifact_store,
            adapter,
            storyboard_jobs_engine,
            config,
            script,
        )
        with storyboard_jobs_engine.begin() as conn:  # 直接造一个 pending 中间态
            conn.execute(
                insert(storyboard_render_jobs).values(
                    job_id="r8-jx",
                    round_id="r8",
                    shotlist_json="{}",
                    shotlist_hash="ab" * 32,
                    status="pending",
                    estimated_cost_usd=0.1,
                    actual_cost_usd=None,
                    artifact_hash=None,
                    error=None,
                    created_at="2026-09-20T00:00:00Z",
                )
            )
        with pytest.raises(StoryboardLoopError, match="终态"):
            freeze_round_tree("r8", tree_store, storyboard_jobs_engine)
        assert result.tree_id == round_tree_id("r8")

    def test_轮次不存在拒绝(self, tree_store, storyboard_jobs_engine):
        with pytest.raises(StoryboardLoopError, match="不存在"):
            freeze_round_tree("ghost", tree_store, storyboard_jobs_engine)
