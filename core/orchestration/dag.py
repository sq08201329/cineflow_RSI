"""轻量依赖图（功能 015 阶段 2 / T1506，契约 C1）：拓扑序 + 环检测 + 依赖校验。

**自研轻量 DAG**（宪章原则五：禁外部编排框架，依赖清单机检）：只处理 stage_id、
依赖与执行入口引用，**零业务概念**——不认识任何环节语义与形态差异（静态断言见
`tests/unit/test_orchestration_executor.py`）。

校验一次到位（拒绝而非静默容忍）：空图、stage_id 重复、依赖不存在、环依赖全部在
`build_dag` 时抛 `DagError`；拓扑序按**输入序稳定**（同层节点保持声明顺序，
执行顺序因此可复现——可复现性是 SC-001 的前提）。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from core.orchestration.errors import DagError
from core.orchestration.models import StageSpec


@dataclass(frozen=True)
class Dag:
    """依赖图（不可变）：阶段映射按声明序 + 拓扑序元组。"""

    stages: Mapping[str, StageSpec]
    order: tuple[str, ...]

    def topological_order(self) -> list[str]:
        """拓扑序副本（调用方改写不影响图本身）。"""
        return list(self.order)

    @property
    def stage_ids(self) -> tuple[str, ...]:
        return tuple(self.stages)

    def spec(self, stage_id: str) -> StageSpec:
        try:
            return self.stages[stage_id]
        except KeyError as exc:
            raise DagError(f"图中不存在阶段 {stage_id!r}") from exc

    def upstream(self, stage_id: str) -> tuple[str, ...]:
        """直接上游（依赖）阶段标识，按声明序。"""
        return self.spec(stage_id).depends_on


def build_dag(stages: Sequence[StageSpec]) -> Dag:
    """校验并构建依赖图（拒绝即报错；不静默裁剪）。"""
    specs = tuple(stages)
    if not specs:
        raise DagError("依赖图至少需要一个阶段（空图无意义）")

    by_id: dict[str, StageSpec] = {}
    for spec in specs:
        if spec.stage_id in by_id:
            raise DagError(f"stage_id 重复：{spec.stage_id!r}（阶段标识必须唯一）")
        by_id[spec.stage_id] = spec

    normalized: dict[str, StageSpec] = {}
    for spec in specs:
        # 重复依赖去重（声明序保留），让拓扑序不受写法冗余影响
        depends = tuple(dict.fromkeys(spec.depends_on))
        for dep in depends:
            if dep not in by_id:
                raise DagError(f"阶段 {spec.stage_id!r} 依赖不存在的阶段 {dep!r}（先声明依赖项）")
        normalized[spec.stage_id] = (
            spec if depends == spec.depends_on else _with_deps(spec, depends)
        )

    order = _topological_order(normalized)
    if order is None:
        raise DagError(f"依赖图存在环依赖：{_cycle_description(normalized)}")
    return Dag(stages=MappingProxyType(normalized), order=tuple(order))


def _with_deps(spec: StageSpec, depends: tuple[str, ...]) -> StageSpec:
    from dataclasses import replace

    return replace(spec, depends_on=depends)


def _topological_order(specs: Mapping[str, StageSpec]) -> list[str] | None:
    """稳定拓扑序：每轮按声明序取"依赖已就绪"的节点；无可进展即判定有环。"""
    order: list[str] = []
    placed: set[str] = set()
    while len(order) < len(specs):
        progressed = False
        for stage_id, spec in specs.items():
            if stage_id in placed:
                continue
            if all(dep in placed for dep in spec.depends_on):
                order.append(stage_id)
                placed.add(stage_id)
                progressed = True
        if not progressed:
            return None
    return order


def _cycle_description(specs: Mapping[str, StageSpec]) -> str:
    """定位一条具体环路径（自环优先），作为拒绝理由——让调用方一眼看到问题所在。"""
    for stage_id, spec in specs.items():
        if stage_id in spec.depends_on:
            return f"{stage_id} → {stage_id}（自依赖）"
    remaining = set(specs)
    start = next(stage for stage in specs if stage in remaining)
    path = [start]
    seen = {start}
    current = start
    while True:
        nxt = next((dep for dep in specs[current].depends_on if dep in remaining), None)
        if nxt is None:
            break
        path.append(nxt)
        if nxt in seen:
            break
        seen.add(nxt)
        current = nxt
    return " → ".join(path)
