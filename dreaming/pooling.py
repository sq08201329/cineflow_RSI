"""做梦回放池选择（功能 011 US3 / T1021，contracts/lineage-acceptance.md C9）。

**最小改动**：做梦侧只做一件事——读 `replay.pooling.enabled_for_dreaming` 决定用哪个池：

- 默认关闭（配置保守闸门）→ 单项目池（同 Agent 同项目的树，002 `SimulatorPool`）；
- 开启且前置条件满足（树数 ≥ min_trees）→ 合并池（跨项目，`MergedSimulatorPool`）；
- 开启但前置不足 / 合并池构建被拒 → **回落单项目池并注明**（不抛错、不静默）。

两种池同 `build(...)` 接口，故 `dreaming.pipeline.run_dream_round(replay_fn=...)` 与
沙箱回放路径**零改动**（池选择在装配处完成，pipeline 内部不特化任何 Agent 与池形态，原则五）。
"""

from core.replay.pooled_replay import PoolSelection, select_replay_pool
from core.replay.pooling_models import PoolingConfig
from core.tree.store import TreeStore


def single_project_trees(store: TreeStore, *, agent_id: str, project_id: str):
    """单项目池的树集合（001 三维索引查询：同 Agent 同项目）。"""
    return tuple(store.trees_by(agent_id=agent_id, project_id=project_id))


def select_dreaming_pool(
    store: TreeStore,
    *,
    agent_id: str,
    form: str,
    project_id: str,
    cfg: PoolingConfig,
    version_hash: str | None = None,
) -> PoolSelection:
    """做梦回放池选择（C9）：开关开 → 合并池；关闭/前置不足 → 单项目池并注明。

    返回值池对象的 `build(...)` 接口与 002 `SimulatorPool` 一致——做梦回放路径无改动。
    version_hash：部署评估器版本集哈希（多版本池必须显式给出，跨版本不混池）。
    """
    return select_replay_pool(
        store,
        agent_id,
        form,
        cfg,
        single_project_trees=single_project_trees(store, agent_id=agent_id, project_id=project_id),
        version_hash=version_hash,
    )
