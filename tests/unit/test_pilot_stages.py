"""功能 015 US3（T1517）：`agents/pilot/stages.py` 六阶段定义与执行入口测试。

覆盖：六阶段 StageSpec（依赖 script→storyboard→visual→sound→editing→promo、DAG 合法）、
形态配置装配（各 Agent 配置对象按形态配置加载）、候选重试语义（环节内换候选、
**全败才 failed 并带全部候选判 0 理由**）、以及两条静态断言：
执行入口不新增落树路径、模块无形态分支。
"""

from pathlib import Path

import pytest

from agents.pilot import stages as stages_module
from agents.pilot.stages import (
    PILOT_STAGE_IDS,
    build_runtime,
    build_stage_specs,
    candidate_set,
)
from core.orchestration.dag import build_dag
from core.orchestration.models import StageOutcome

FORM = "shortdrama"


@pytest.fixture()
def runtime(pilot_form_config_path, pilot_dirs, tmp_path):
    return build_runtime(
        form=FORM,
        config_path=pilot_form_config_path(FORM),
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
    )


class Test阶段定义:
    def test_六阶段顺序与依赖(self, runtime):
        specs = build_stage_specs(runtime)
        assert [spec.stage_id for spec in specs] == list(PILOT_STAGE_IDS)
        dag = build_dag(specs)
        assert dag.topological_order() == list(PILOT_STAGE_IDS)
        for previous, stage_id in zip(PILOT_STAGE_IDS, PILOT_STAGE_IDS[1:], strict=False):
            assert dag.spec(stage_id).depends_on == (previous,)

    def test_阶段入口可调用且带输出工件类型(self, runtime):
        for spec in build_stage_specs(runtime):
            assert callable(spec.entrypoint)
            assert callable(spec.handoff)
            assert spec.output_kind

    def test_形态配置装配到各_Agent(self, runtime):
        assert runtime.form == FORM
        # 六段各自的配置对象按同一份形态配置加载（形态差异只在配置）
        for agent in ("screenplay", "storyboard", "visual", "sound", "editing", "promo"):
            assert getattr(runtime.configs, agent) is not None
        assert runtime.config_fingerprint

    def test_剪辑目标时长来自配置(self, runtime):
        # 试水体量 = 形态配置：分镜镜头数与成片目标时长都从配置派生（代码零硬编码）
        assert runtime.configs.editing.target_duration_s > 0
        assert runtime.shot_plan  # 由配置推导的镜头计划非空


class Test候选语义:
    def _node(self, **overrides):
        from core.tree.models import CostRecord, NodeStatus, TreeNode

        fields = {
            "node_id": "n1",
            "tree_id": "t1",
            "parent_id": "root",
            "depth": 1,
            "agent_id": "agent",
            "policy_version": "v1",
            "prompt": "p",
            "observation_context": {},
            "artifact_hash": "ab" * 32,
            "eval_breakdown": {"rule.x": {"score": 1.0}},
            "score": 0.8,
            "cost": CostRecord(),
            "status": NodeStatus.EVALUATED,
        }
        fields.update(overrides)
        return TreeNode(**fields)

    def test_全部候选产出且达标即全成功(self):
        outcome = candidate_set([self._node(), self._node(node_id="n2")])
        assert outcome.all_ok
        assert [candidate.score for candidate in outcome.candidates] == [0.8, 0.8]

    def test_判零候选带理由且判全败(self):
        gate_failed = self._node(
            node_id="n3", score=0.0, eval_breakdown={"rule.x": {"score": 0.0, "diagnostics": {}}}
        )
        outcome = candidate_set([gate_failed])
        assert not outcome.all_ok
        assert outcome.candidates[0].reasons  # 判 0 必须有理由（诚实边界）
        assert "rule.x" in outcome.candidates[0].reasons[0]

    def test_计数不匹配即判败(self):
        ok = candidate_set([self._node()], expected=2)
        assert not ok.all_ok
        assert ok.failure_reason and "数量" in ok.failure_reason

    def test_空候选即判败(self):
        empty = candidate_set([], expected=1)
        assert not empty.all_ok and empty.candidates == ()


class Test静态断言:
    def test_执行链不新增落树写入路径(self):
        """FR-011：落树只经各 Agent 既有 loop 入口（编排层不得直接写树/工件库）。"""
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        for banned in ("store.append_node(", "create_tree(", "store.create_tree("):
            assert banned not in source, banned

    def test_无形态分支(self):
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        for banned in ("shortdrama", '"movie"', "form ==", "form is ", "form !="):
            assert banned not in source, banned


def test_候选结果类型可序列化():
    outcome = StageOutcome(products=(), cost_usd=0.0)
    assert outcome.detail == {}
