"""功能 015 阶段 2（T1505）：`core/orchestration/dag.py` 依赖图测试（契约 C1）。

覆盖：拓扑序正确、环依赖拒绝、依赖不存在拒绝、stage_id 重复拒绝、空图拒绝、
映射只读。**零业务概念**：本文件用中性阶段名（a1/a2/...）——图算法不认识环节语义。
"""

import pytest

from core.evaluators.errors import ValidationError
from core.orchestration.dag import Dag, build_dag
from core.orchestration.errors import DagError
from core.orchestration.models import StageOutcome, StageSpec


def _spec(stage_id: str, *depends_on: str) -> StageSpec:
    return StageSpec(
        stage_id=stage_id,
        entrypoint=lambda stage_input: StageOutcome(),
        depends_on=tuple(depends_on),
    )


class Test拓扑序:
    def test_线性链(self):
        dag = build_dag([_spec("a1"), _spec("a2", "a1"), _spec("a3", "a2")])
        assert dag.topological_order() == ["a1", "a2", "a3"]
        assert dag.stage_ids == ("a1", "a2", "a3")

    def test_分支与汇聚(self):
        dag = build_dag(
            [
                _spec("a1"),
                _spec("a2", "a1"),
                _spec("a3", "a1"),
                _spec("a4", "a2", "a3"),
            ]
        )
        order = dag.topological_order()
        assert order.index("a1") < order.index("a2") < order.index("a4")
        assert order.index("a1") < order.index("a3") < order.index("a4")

    def test_同层按输入序稳定(self):
        first = build_dag([_spec("b1"), _spec("a1"), _spec("c1", "a1")])
        second = build_dag([_spec("b1"), _spec("a1"), _spec("c1", "a1")])
        assert first.topological_order() == second.topological_order()
        assert first.topological_order() == ["b1", "a1", "c1"]

    def test_拓扑序返回副本且映射只读(self):
        dag = build_dag([_spec("a1")])
        order = dag.topological_order()
        order.append("mutated")
        assert dag.topological_order() == ["a1"]
        with pytest.raises(TypeError):
            dag.stages["x"] = _spec("x")  # type: ignore[index]

    def test_阶段定义可按键取用(self):
        dag = build_dag([_spec("a1"), _spec("a2", "a1")])
        assert dag.spec("a2").depends_on == ("a1",)
        assert dag.upstream("a2") == ("a1",)
        assert dag.upstream("a1") == ()
        with pytest.raises(DagError):
            dag.spec("missing")


class Test非法图:
    def test_环依赖拒绝并给出路径(self):
        with pytest.raises(DagError) as excinfo:
            build_dag([_spec("a1", "a3"), _spec("a2", "a1"), _spec("a3", "a2")])
        message = str(excinfo.value)
        assert "环" in message
        assert "a1" in message and "a2" in message and "a3" in message

    def test_自依赖在模型层即被拒(self):
        # 自依赖在 StageSpec 构造时就被拒（比建图更早；图侧不存在自依赖输入）
        with pytest.raises(ValidationError):
            _spec("a1", "a1")

    def test_依赖不存在拒绝(self):
        with pytest.raises(DagError) as excinfo:
            build_dag([_spec("a1"), _spec("a2", "ghost")])
        assert "ghost" in str(excinfo.value)

    def test_stage_id_重复拒绝(self):
        with pytest.raises(DagError) as excinfo:
            build_dag([_spec("a1"), _spec("a1")])
        assert "a1" in str(excinfo.value)

    def test_空图拒绝(self):
        with pytest.raises(DagError):
            build_dag([])

    def test_重复依赖去重(self):
        dag = build_dag([_spec("a1"), _spec("a2", "a1", "a1")])
        assert dag.topological_order() == ["a1", "a2"]


class TestDag类型:
    def test_构建返回_Dag_且可枚举(self):
        dag = build_dag([_spec("a1"), _spec("a2", "a1")])
        assert isinstance(dag, Dag)
        assert list(dag.stages) == ["a1", "a2"]
