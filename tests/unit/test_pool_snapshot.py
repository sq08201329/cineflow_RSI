"""构建快照落盘单测（功能 011 / T1008，先于实现编写；C2 场景 1~3）。

- 首次落盘：replay/pools/{agent}/{form}/{pool_id}.json，字段齐全（分组/版本分组/树清单/
  min_trees 判定/dreaming 开关状态/构建时间/内容哈希）；
- 幂等：重复构建同输入 → 同快照（不重写文件、构建时间保持首次值）；
- 内容哈希不含构建时间（同输入必得同哈希，池内容漂移可机检）；
- 只增不改：既有快照被改写 / 同 id 内容不一致 → 拒绝覆盖；
- 前置判定与开关状态如实入快照（含前置不足路径）。
"""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from core.replay.errors import PoolError, ValidationError
from core.replay.merged_pool import build_merged_pool
from core.replay.pool_snapshot import (
    load_pool_snapshot,
    persist_pool_snapshot,
    snapshot_content_hash,
    snapshot_from_dict,
    snapshot_path,
    snapshot_to_dict,
)
from core.replay.pooling_models import PoolingConfig, PoolSnapshot

AGENT = "agent-pool"
FORM = "movie"
BUILT_AT = "2026-09-21T10:00:00+00:00"

_SNAPSHOT_KEYS = {
    "pool_id",
    "agent_id",
    "form",
    "version_groups",
    "trees",
    "min_trees",
    "conditions_met",
    "enabled_for_dreaming",
    "built_at",
    "content_hash",
    "note",
}


@pytest.fixture()
def pool(tree_store, pooling_config, pool_multi_project_trees):
    return build_merged_pool(tree_store, AGENT, FORM, pooling_config)


class Test首次落盘:
    def test_快照路径按分组约定(self, pool, pooling_config):
        expected = Path(pooling_config.pools_dir) / AGENT / FORM / f"{pool.pool_id}.json"
        assert snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id) == expected

    def test_快照字段齐全(self, pool, pooling_config):
        """C2 场景 1：首次构建 → 快照落盘（字段齐全）。"""
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload) == _SNAPSHOT_KEYS
        assert payload["pool_id"] == pool.pool_id
        assert payload["agent_id"] == AGENT and payload["form"] == FORM
        assert payload["min_trees"] == 3 and payload["conditions_met"] is True
        assert payload["enabled_for_dreaming"] is False
        assert payload["built_at"] == BUILT_AT
        assert datetime.fromisoformat(payload["built_at"])
        assert len(payload["content_hash"]) == 64
        # 版本分组：版本集哈希 + 组内树清单（项目归属 + 时间戳）
        group = payload["version_groups"][0]
        assert group["evaluator_versions_hash"] == pool.version_groups[0].evaluator_versions_hash
        assert group["evaluator_versions"] == {"rule.x": "1.0.0", "proxy.y": "1.0.0"}
        assert len(group["trees"]) == 4
        assert {ref["project_id"] for ref in group["trees"]} == {"project-a", "project-b"}
        assert all("created_at" in ref and "node_count" in ref for ref in group["trees"])
        # 树清单：project_id + created_at（跨项目可追溯）
        assert len(payload["trees"]) == 4
        assert all({"tree_id", "project_id", "created_at"} <= set(ref) for ref in payload["trees"])
        assert snapshot.pool_id == payload["pool_id"]
        assert snapshot == snapshot_from_dict(payload)

    def test_快照对象与池一致(self, pool, pooling_config):
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        assert isinstance(snapshot, PoolSnapshot)
        assert snapshot.pool_id == pool.pool_id
        assert snapshot.trees == pool.trees
        assert snapshot.version_groups == pool.version_groups
        assert snapshot.note == pool.note
        assert snapshot.content_hash == snapshot_content_hash(snapshot)

    def test_快照可读回(self, pool, pooling_config):
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        assert load_pool_snapshot(path) == snapshot

    def test_内层目录自动创建(self, tree_store, pooling_data_dir, pool_multi_project_trees):
        cfg = PoolingConfig(pools_dir=str(pooling_data_dir / "fresh"))
        pool = build_merged_pool(tree_store, AGENT, FORM, cfg)
        assert not (pooling_data_dir / "fresh").exists()
        persist_pool_snapshot(pool, cfg, built_at=BUILT_AT)
        assert (pooling_data_dir / "fresh" / AGENT / FORM).is_dir()


class Test幂等:
    def test_重复构建幂等(self, pool, pooling_config):
        """C2 场景 2：重复构建同输入 → 幂等（快照一致，不重复落盘）。"""
        first = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        before = path.read_bytes()
        second = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        assert first == second
        assert path.read_bytes() == before
        assert list(path.parent.glob("*.json")) == [path]  # 同 id 只有一个文件

    def test_构建时间不同也不重写(self, pool, pooling_config):
        first = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        before = path.read_bytes()
        second = persist_pool_snapshot(pool, pooling_config, built_at="2026-09-22T10:00:00+00:00")
        assert second.built_at == first.built_at == BUILT_AT  # 首写为准（只增不改）
        assert path.read_bytes() == before

    def test_内容哈希不含构建时间(self, pool, pooling_config):
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        later = replace(snapshot, built_at="2026-09-30T00:00:00+00:00")
        assert snapshot_content_hash(later) == snapshot_content_hash(snapshot)

    def test_同输入两次构建同哈希(self, tree_store, pooling_config, pool_multi_project_trees):
        """C1 场景 4 的快照侧：同输入 → 同 pool_id → 同内容哈希。"""
        first = persist_pool_snapshot(
            build_merged_pool(tree_store, AGENT, FORM, pooling_config),
            pooling_config,
            built_at=BUILT_AT,
        )
        second = persist_pool_snapshot(
            build_merged_pool(tree_store, AGENT, FORM, pooling_config),
            pooling_config,
            built_at="2026-09-22T10:00:00+00:00",
        )
        assert first.pool_id == second.pool_id
        assert first.content_hash == second.content_hash


class Test开关与前置判定入快照:
    def test_dreaming_开关状态入快照(self, tree_store, pooling_config, pool_multi_project_trees):
        """C2 场景 3：开关开关状态如实入快照（默认关闭）。"""
        off = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        snapshot_off = persist_pool_snapshot(off, pooling_config, built_at=BUILT_AT)
        assert snapshot_off.enabled_for_dreaming is False

        paired = PoolingConfig(
            min_trees=pooling_config.min_trees,
            dilution_hit_ratio_threshold=pooling_config.dilution_hit_ratio_threshold,
            allow_cross_form=pooling_config.allow_cross_form,
            enabled_for_dreaming=True,
            pools_dir=pooling_config.pools_dir,
        )
        on = build_merged_pool(tree_store, AGENT, FORM, paired)
        snapshot_on = persist_pool_snapshot(on, paired, built_at=BUILT_AT)
        assert snapshot_on.enabled_for_dreaming is True
        # 开关状态是分组输入的一部分：两份快照各自落盘（只增不改）
        assert snapshot_on.pool_id != snapshot_off.pool_id
        assert snapshot_path(pooling_config.pools_dir, AGENT, FORM, snapshot_off.pool_id).exists()
        assert snapshot_path(pooling_config.pools_dir, AGENT, FORM, snapshot_on.pool_id).exists()

    def test_前置不足如实入快照(self, tree_store, pooling_config, make_pool_tree):
        for i in range(2):
            make_pool_tree(project_id="project-a", created_at=1000.0 + i)
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config, enforce_min_trees=False)
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        assert snapshot.conditions_met is False
        assert "前置条件不足" in snapshot.note
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        assert json.loads(path.read_text(encoding="utf-8"))["conditions_met"] is False

    def test_开关与池标识绑定(self, tree_store, pooling_config, pool_multi_project_trees):
        """落盘配置的内部一致性：池是关闭档构建的，却按开启档落盘 → 拒绝（标识不符）。"""
        pool = build_merged_pool(tree_store, AGENT, FORM, pooling_config)
        paired = PoolingConfig(enabled_for_dreaming=True, pools_dir=pooling_config.pools_dir)
        with pytest.raises(PoolError, match="pool_id"):
            persist_pool_snapshot(pool, paired, built_at=BUILT_AT)


class Test只增不改:
    def test_既有快照被改写即拒绝(self, pool, pooling_config):
        """只增不改机检：文件内容哈希不符（被改写）→ 读取即拒绝。"""
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["conditions_met"] = False  # 手工篡改（content_hash 未同步）
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(PoolError, match="哈希"):
            load_pool_snapshot(path)
        with pytest.raises(PoolError, match="哈希"):
            persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        assert snapshot.conditions_met is True

    def test_同_id_不同内容拒绝覆盖(self, pool, pooling_config):
        """同 pool_id 但内容不一致（池内容漂移）→ 拒绝覆盖既有快照。"""
        snapshot = persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        path = snapshot_path(pooling_config.pools_dir, AGENT, FORM, pool.pool_id)
        drifted = replace(snapshot, note="被第三方改写的附注")
        drifted = replace(drifted, content_hash=snapshot_content_hash(drifted))
        path.write_text(json.dumps(snapshot_to_dict(drifted), ensure_ascii=False), encoding="utf-8")
        with pytest.raises(PoolError, match="只增不改"):
            persist_pool_snapshot(pool, pooling_config, built_at=BUILT_AT)
        assert snapshot.note != drifted.note


class Test读取面校验:
    def test_快照不存在即拒绝(self, pooling_data_dir):
        with pytest.raises(ValidationError, match="不存在"):
            load_pool_snapshot(pooling_data_dir / "agent-pool" / "movie" / "missing.json")

    def test_池类型非法即拒绝(self, pooling_config):
        with pytest.raises(ValidationError, match="pool"):
            persist_pool_snapshot({"pool_id": "x"}, pooling_config)

    def test_配置类型非法即拒绝(self, pool):
        with pytest.raises(ValidationError, match="cfg"):
            persist_pool_snapshot(pool, {"min_trees": 3})

    def test_缺字段的快照文件即拒绝(self, pooling_data_dir, pool):
        path = snapshot_path(pooling_data_dir, AGENT, FORM, pool.pool_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pool_id": pool.pool_id}), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_pool_snapshot(path)
