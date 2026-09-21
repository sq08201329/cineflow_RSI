"""跨项目池化领域模型单测（功能 011 / T1005，先于实现编写）。

- frozen 不可变：构造后改字段必须抛 FrozenInstanceError；
- PoolingConfig：configs/movie.yaml 的 replay.pooling 段解析（缺段/缺项/非法值即拒绝）；
- 校验规则：占比 ∈ [0,1]、树清单非空、版本分组非空、树数合计与树清单一致、
  冲突必须"多棵且得分不同"、稀释告警必须在超阈时构造且单项目构成如实标注。
"""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from core.replay.errors import ValidationError
from core.replay.pooling_models import (
    ChildVersionRef,
    ConflictHit,
    CrossProjectLineage,
    DilutionAlert,
    HitDistribution,
    LineageTreeRef,
    MergedPool,
    PoolingConfig,
    PoolingConfigError,
    PoolSnapshot,
    PoolTreeRef,
    ProjectHitStats,
    ScoreConflict,
    VersionGroup,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_HASH = "ab" * 32

_TREE_A = PoolTreeRef(tree_id="tree-a", project_id="project-a", created_at=1000.0, node_count=3)
_TREE_B = PoolTreeRef(tree_id="tree-b", project_id="project-b", created_at=1001.0, node_count=3)
_GROUP = VersionGroup(evaluator_versions_hash=_HASH, trees=(_TREE_A,))
_POOL = {
    "pool_id": "cd" * 32,
    "agent_id": "agent-pool",
    "form": "movie",
    "version_groups": (_GROUP,),
    "min_trees": 3,
    "conditions_met": True,
    "tree_count": 1,
}
_SNAPSHOT = {
    "pool_id": "cd" * 32,
    "agent_id": "agent-pool",
    "form": "movie",
    "version_groups": (_GROUP,),
    "trees": (_TREE_A,),
    "min_trees": 3,
    "conditions_met": True,
    "enabled_for_dreaming": False,
    "built_at": "2026-09-21T10:00:00+00:00",
}


class TestPoolingConfig:
    def test_默认档(self):
        cfg = PoolingConfig()
        assert cfg.min_trees == 3
        assert cfg.dilution_hit_ratio_threshold == 0.7
        assert cfg.allow_cross_form is False
        assert cfg.enabled_for_dreaming is False
        assert cfg.pools_dir == "replay/pools"

    def test_读真实配置段(self):
        """configs/movie.yaml 的 replay.pooling 段（T1001）如实解析。"""
        cfg = PoolingConfig.from_config(_REAL_CONFIG)
        assert cfg.min_trees == 3
        assert cfg.dilution_hit_ratio_threshold == 0.7
        assert cfg.allow_cross_form is False
        assert cfg.enabled_for_dreaming is False
        assert cfg.pools_dir == "replay/pools"

    def test_从_yaml_读取(self):
        cfg = PoolingConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert cfg == PoolingConfig.from_config(_REAL_CONFIG)

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            PoolingConfig().min_trees = 5  # type: ignore[misc]

    def test_缺_pooling_段即拒绝(self):
        payload = {"replay": {"worker_count": 4}}
        with pytest.raises(PoolingConfigError, match="pooling"):
            PoolingConfig.from_config(payload)

    def test_缺_replay_段即拒绝(self):
        with pytest.raises(PoolingConfigError, match="replay"):
            PoolingConfig.from_config({})

    @pytest.mark.parametrize(
        "key",
        [
            "min_trees",
            "dilution_hit_ratio_threshold",
            "allow_cross_form",
            "enabled_for_dreaming",
        ],
    )
    def test_缺配置项即拒绝(self, key):
        section = dict(_REAL_CONFIG["replay"]["pooling"])
        section.pop(key)
        with pytest.raises(PoolingConfigError, match=key):
            PoolingConfig.from_config({"replay": {"pooling": section}})

    @pytest.mark.parametrize("bad", [0, -1, 2.0, True, "3"])
    def test_min_trees_必须为大于等于一的整数(self, bad):
        with pytest.raises(PoolingConfigError, match="min_trees"):
            PoolingConfig(min_trees=bad)

    @pytest.mark.parametrize("bad", [-0.1, 1.1, True, "0.7"])
    def test_稀释阈值必须在零到一之间(self, bad):
        with pytest.raises(PoolingConfigError, match="dilution_hit_ratio_threshold"):
            PoolingConfig(dilution_hit_ratio_threshold=bad)

    @pytest.mark.parametrize("field", ["allow_cross_form", "enabled_for_dreaming"])
    def test_开关必须为_bool(self, field):
        with pytest.raises(PoolingConfigError, match=field):
            PoolingConfig(**{field: "false"})

    def test_pools_dir_非空(self):
        with pytest.raises(PoolingConfigError, match="pools_dir"):
            PoolingConfig(pools_dir="")

    def test_路径可覆盖_不改语义(self):
        """pools_dir 是落盘位置而非池化语义：覆盖不改变其余配置。"""
        cfg = PoolingConfig(pools_dir="/tmp/pools")
        assert cfg.min_trees == 3 and cfg.pools_dir == "/tmp/pools"


class TestPoolTreeRef:
    def test_合法构造与默认附注(self):
        ref = PoolTreeRef(tree_id="t1", project_id="p1", created_at=10.0, node_count=2)
        assert ref.config_note == ""

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            _TREE_A.project_id = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("bad", [-1.0, "10", True])
    def test_时间戳必须为非负数(self, bad):
        with pytest.raises(ValidationError, match="created_at"):
            PoolTreeRef(tree_id="t1", project_id="p1", created_at=bad, node_count=1)

    @pytest.mark.parametrize("bad", [0, -1, 1.0, True])
    def test_节点数必须为正整数(self, bad):
        with pytest.raises(ValidationError, match="node_count"):
            PoolTreeRef(tree_id="t1", project_id="p1", created_at=1.0, node_count=bad)

    @pytest.mark.parametrize("field", ["tree_id", "project_id"])
    def test_标识字段非空(self, field):
        fields = {"tree_id": "t1", "project_id": "p1", "created_at": 1.0, "node_count": 1}
        with pytest.raises(ValidationError):
            PoolTreeRef(**{**fields, field: ""})


class TestVersionGroup:
    def test_合法构造(self):
        group = VersionGroup(
            evaluator_versions_hash=_HASH,
            trees=(_TREE_A, _TREE_B),
            evaluator_versions={"rule.x": "1.0.0"},
        )
        assert len(group.trees) == 2
        assert group.evaluator_versions == {"rule.x": "1.0.0"}

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            _GROUP.evaluator_versions_hash = _HASH  # type: ignore[misc]

    @pytest.mark.parametrize("bad", ["AB" * 32, "ab" * 16, "zz" * 32, ""])
    def test_分组键必须为_64_位小写十六进制(self, bad):
        with pytest.raises(ValidationError, match="evaluator_versions_hash"):
            VersionGroup(evaluator_versions_hash=bad, trees=(_TREE_A,))

    def test_树清单非空(self):
        with pytest.raises(ValidationError, match="trees"):
            VersionGroup(evaluator_versions_hash=_HASH, trees=())

    def test_树清单元素类型(self):
        with pytest.raises(ValidationError, match="trees"):
            VersionGroup(evaluator_versions_hash=_HASH, trees=({"tree_id": "t1"},))


class TestMergedPool:
    def test_合法构造与树清单展开(self):
        pool = MergedPool(**_POOL)
        assert pool.conditions_met is True
        assert [ref.tree_id for ref in pool.trees] == ["tree-a"]
        assert pool.build_snapshot == "" and pool.note == ""

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            MergedPool(**_POOL).agent_id = "other"  # type: ignore[misc]

    def test_版本分组非空(self):
        with pytest.raises(ValidationError, match="version_groups"):
            MergedPool(**{**_POOL, "version_groups": ()})

    def test_树数必须等于树清单合计(self):
        """分组与树清单双记账必须一致（不得虚报池规模）。"""
        with pytest.raises(ValidationError, match="tree_count"):
            MergedPool(**{**_POOL, "tree_count": 4})

    @pytest.mark.parametrize("field", ["agent_id", "form"])
    def test_分组字段非空(self, field):
        with pytest.raises(ValidationError):
            MergedPool(**{**_POOL, field: ""})

    def test_pool_id_必须为_64_位小写十六进制(self):
        with pytest.raises(ValidationError, match="pool_id"):
            MergedPool(**{**_POOL, "pool_id": "cd" * 16})

    @pytest.mark.parametrize("bad", [0, -1, "3"])
    def test_min_trees_必须为正整数(self, bad):
        with pytest.raises(ValidationError, match="min_trees"):
            MergedPool(**{**_POOL, "min_trees": bad})

    def test_前置条件判定必须为_bool(self):
        with pytest.raises(ValidationError, match="conditions_met"):
            MergedPool(**{**_POOL, "conditions_met": "yes"})

    def test_快照引用可回填(self):
        pool = MergedPool(**_POOL).with_build_snapshot("replay/pools/agent-pool/movie/x.json")
        assert pool.build_snapshot.endswith("x.json")
        assert MergedPool(**_POOL).build_snapshot == ""  # frozen：原对象不变


class TestScoreConflict:
    def test_合法构造(self):
        conflict = ScoreConflict(
            structure_key='{"temperature":0.5}',
            hits=(
                ConflictHit(tree_id="t1", project_id="project-a", score=0.6),
                ConflictHit(tree_id="t2", project_id="project-b", score=0.8),
            ),
            note="多棵命中得分不同 → UNKNOWN",
        )
        assert len(conflict.hits) == 2

    def test_frozen_不可变(self):
        conflict = ScoreConflict(
            structure_key="k",
            hits=(
                ConflictHit(tree_id="t1", project_id="p1", score=0.6),
                ConflictHit(tree_id="t2", project_id="p2", score=0.8),
            ),
        )
        with pytest.raises(FrozenInstanceError):
            conflict.note = "other"  # type: ignore[misc]

    def test_结构键非空(self):
        with pytest.raises(ValidationError, match="structure_key"):
            ScoreConflict(
                structure_key="",
                hits=(
                    ConflictHit(tree_id="t1", project_id="p1", score=0.6),
                    ConflictHit(tree_id="t2", project_id="p2", score=0.8),
                ),
            )

    def test_命中不足两棵即拒绝(self):
        with pytest.raises(ValidationError, match="hits"):
            ScoreConflict(
                structure_key="k",
                hits=(ConflictHit(tree_id="t1", project_id="p1", score=0.6),),
            )

    def test_得分相同不构成冲突(self):
        with pytest.raises(ValidationError, match="得分"):
            ScoreConflict(
                structure_key="k",
                hits=(
                    ConflictHit(tree_id="t1", project_id="p1", score=0.6),
                    ConflictHit(tree_id="t2", project_id="p2", score=0.6),
                ),
            )

    @pytest.mark.parametrize("bad", [-0.1, 1.1, "0.6"])
    def test_命中得分域(self, bad):
        with pytest.raises(ValidationError, match="score"):
            ConflictHit(tree_id="t1", project_id="p1", score=bad)


class TestHitDistribution:
    def _stats(self, **overrides):
        fields = {
            "project_id": "project-a",
            "hits": 8,
            "unknowns": 2,
            "hit_ratio": 0.8,
            "tree_count": 2,
            "tree_ratio": 0.5,
        }
        fields.update(overrides)
        return ProjectHitStats(**fields)

    def test_合法构造_冲突可空(self):
        dist = HitDistribution(per_project=(self._stats(),), merged_hits=8, merged_unknowns=2)
        assert dist.conflicts == ()

    def test_frozen_不可变(self):
        dist = HitDistribution(per_project=(self._stats(),), merged_hits=8, merged_unknowns=2)
        with pytest.raises(FrozenInstanceError):
            dist.merged_hits = 0  # type: ignore[misc]

    @pytest.mark.parametrize("field", ["hit_ratio", "tree_ratio"])
    def test_占比必须在零到一之间(self, field):
        with pytest.raises(ValidationError, match=field):
            self._stats(**{field: 1.5})

    @pytest.mark.parametrize("field", ["hits", "unknowns"])
    def test_计数必须为非负整数(self, field):
        with pytest.raises(ValidationError, match=field):
            self._stats(**{field: -1})

    def test_树数必须为正整数(self):
        with pytest.raises(ValidationError, match="tree_count"):
            self._stats(tree_count=0)

    def test_合并口径必须等于逐项目合计(self):
        """双报告双记账：合并口径 = 逐项目合计（不得虚报）。"""
        with pytest.raises(ValidationError, match="merged_hits"):
            HitDistribution(per_project=(self._stats(),), merged_hits=9, merged_unknowns=2)
        with pytest.raises(ValidationError, match="merged_unknowns"):
            HitDistribution(per_project=(self._stats(),), merged_hits=8, merged_unknowns=3)

    def test_空分布合法(self):
        dist = HitDistribution(per_project=(), merged_hits=0, merged_unknowns=0)
        assert dist.per_project == ()


class TestDilutionAlert:
    def test_超阈告警(self):
        alert = DilutionAlert(project_id="project-a", hit_ratio=0.8, tree_ratio=0.5, threshold=0.7)
        assert alert.note == ""

    def test_frozen_不可变(self):
        alert = DilutionAlert(project_id="p", hit_ratio=0.8, tree_ratio=0.5, threshold=0.7)
        with pytest.raises(FrozenInstanceError):
            alert.hit_ratio = 0.1  # type: ignore[misc]

    def test_未超阈不得构造告警(self):
        with pytest.raises(ValidationError, match="命中占比"):
            DilutionAlert(project_id="p", hit_ratio=0.7, tree_ratio=0.5, threshold=0.7)

    def test_单项目构成必须如实标注(self):
        with pytest.raises(ValidationError, match="单项目构成"):
            DilutionAlert(project_id="p", hit_ratio=1.0, tree_ratio=1.0, threshold=0.7)
        alert = DilutionAlert(
            project_id="p",
            hit_ratio=1.0,
            tree_ratio=1.0,
            threshold=0.7,
            note="单项目构成：命中占比 1.0 必然超阈",
        )
        assert "单项目构成" in alert.note

    @pytest.mark.parametrize("field", ["hit_ratio", "tree_ratio", "threshold"])
    def test_占比与阈值域(self, field):
        fields = {"project_id": "p", "hit_ratio": 1.0, "tree_ratio": 1.0, "threshold": 0.7}
        with pytest.raises(ValidationError, match=field):
            DilutionAlert(**{**fields, field: -0.1})


class TestPoolSnapshot:
    def test_合法构造_字段齐全(self):
        snapshot = PoolSnapshot(**_SNAPSHOT)
        assert snapshot.enabled_for_dreaming is False
        assert snapshot.built_at == "2026-09-21T10:00:00+00:00"
        assert snapshot.note == ""
        assert snapshot.content_hash == ""

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            PoolSnapshot(**_SNAPSHOT).built_at = "other"  # type: ignore[misc]

    def test_树清单非空(self):
        with pytest.raises(ValidationError, match="trees"):
            PoolSnapshot(**{**_SNAPSHOT, "trees": ()})

    def test_版本分组非空(self):
        with pytest.raises(ValidationError, match="version_groups"):
            PoolSnapshot(**{**_SNAPSHOT, "version_groups": ()})

    def test_构建时间非空(self):
        with pytest.raises(ValidationError, match="built_at"):
            PoolSnapshot(**{**_SNAPSHOT, "built_at": ""})

    def test_dreaming_开关必须为_bool(self):
        with pytest.raises(ValidationError, match="enabled_for_dreaming"):
            PoolSnapshot(**{**_SNAPSHOT, "enabled_for_dreaming": "false"})

    def test_内容哈希空或_64_位小写十六进制(self):
        assert PoolSnapshot(**{**_SNAPSHOT, "content_hash": _HASH}).content_hash == _HASH
        with pytest.raises(ValidationError, match="content_hash"):
            PoolSnapshot(**{**_SNAPSHOT, "content_hash": "AB" * 32})


class TestCrossProjectLineage:
    def test_合法构造_跨项目链路(self):
        lineage = CrossProjectLineage(
            policy_version="v1",
            trees=(LineageTreeRef(tree_id="t1", project_id="project-a"),),
            child_versions=(ChildVersionRef(version="v2", project_id="project-b"),),
        )
        assert lineage.trees[0].project_id == "project-a"
        assert lineage.child_versions[0].project_id == "project-b"

    def test_单项目版本_跨项目字段为空元组而非缺失(self):
        lineage = CrossProjectLineage(policy_version="v1")
        assert lineage.trees == () and lineage.child_versions == ()

    def test_frozen_不可变(self):
        with pytest.raises(FrozenInstanceError):
            CrossProjectLineage(policy_version="v1").policy_version = "v2"  # type: ignore[misc]

    @pytest.mark.parametrize("bad", ["", None])
    def test_策略版本非空(self, bad):
        with pytest.raises(ValidationError, match="policy_version"):
            CrossProjectLineage(policy_version=bad)

    def test_树条目项目归属非空(self):
        with pytest.raises(ValidationError, match="project_id"):
            LineageTreeRef(tree_id="t1", project_id="")

    def test_子版本条目项目归属非空(self):
        with pytest.raises(ValidationError, match="project_id"):
            ChildVersionRef(version="v2", project_id="")
