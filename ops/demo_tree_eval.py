#!/usr/bin/env python
"""端到端演示：发现树与评估器框架（quickstart.md 验证 3 的六步场景）。

本演示使用 SQLite 内存库 + 本地目录工件存储，无需 Docker。
生产环境切换 PostgreSQL/MinIO 只需更换 DSN 与 ArtifactStore 实现
（core 的公开接口完全一致，见 contracts/）。

依次执行并输出 JSON 报告：
  1. US1-1 immutable：建树 → 追加 3 节点 → 尝试改历史节点被拒，原文一致性哈希不变
  2. US1-2 谱系查询：按（项目, Agent, 策略版本）过滤 → 命中树与节点层级
  3. US1-3 失败节点：模拟评估器崩溃 → failed 落盘、score 为 null、cost 完整
  4. US2-1/2 注册校验：重复注册 / 非确定性注册被拒；human 锚点正常注册
  5. US3-1/2 合成评分：硬规则 0 分 → 总分 0；正常时加权求和（权重来自 configs）
  6. US3-3 快照冻结：修改形态配置权重后，已落盘树的 config_snapshot 不受影响

全部步骤通过时退出码为 0。
"""

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 以脚本路径直接运行时把仓库根与 tests/（桩评估器唯一来源）加入 sys.path
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import blake3  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import create_engine, update  # noqa: E402
from stubs import (  # noqa: E402
    StubHumanEvaluator,
    StubJudgeEvaluator,
    StubNonDeterministicEvaluator,
    StubProxyEvaluator,
    StubRuleEvaluator,
)

from core.evaluators.base import ArtifactRef  # noqa: E402
from core.evaluators.composite import composite_score  # noqa: E402
from core.evaluators.errors import RegistrationError  # noqa: E402
from core.evaluators.registry import Registry  # noqa: E402
from core.evaluators.weights import load_evaluator_weights  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema, tree_nodes  # noqa: E402
from core.tree.errors import ImmutableViolationError  # noqa: E402
from core.tree.models import CostRecord, NodeStatus  # noqa: E402
from core.tree.store import create_tree_store, translate_immutable_errors  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"

# 演示用桩评估器组合（键与 configs/movie.yaml 的 evaluator_weights 对齐）
STUB_EVALUATORS = {
    "rule.format_compliance": lambda score: StubRuleEvaluator("rule.format_compliance", score),
    "proxy.aesthetic": lambda score: StubProxyEvaluator("proxy.aesthetic", score),
    "proxy.identity_consistency": lambda score: StubProxyEvaluator(
        "proxy.identity_consistency", score
    ),
    "proxy.flicker": lambda score: StubProxyEvaluator("proxy.flicker", score),
    "judge.cinematic": lambda score: StubJudgeEvaluator("judge.cinematic", score),
}


def _node_hash(node) -> str:
    """节点原文一致性哈希：全部字段规范化后的 BLAKE3。"""
    canonical = json.dumps(asdict(node), sort_keys=True, ensure_ascii=False, default=str)
    return blake3.blake3(canonical.encode()).hexdigest()


def main() -> int:
    from core.tree.models import DiscoveryTree, TreeNode, new_id

    report: dict = {"steps": {}}

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        store = create_tree_store(engine)
        artifacts = LocalArtifactStore(Path(tmp) / "artifacts")

        def make_node(
            tree_id,
            parent_id,
            depth,
            *,
            status=NodeStatus.EVALUATED,
            score=0.8,
            breakdown=None,
            cost=None,
            created_at=0.0,
        ):
            content = f"artifact-{new_id()}".encode()
            artifact_hash = artifacts.put(content)
            return TreeNode(
                node_id=new_id(),
                tree_id=tree_id,
                parent_id=parent_id,
                depth=depth,
                agent_id="demo-agent",
                policy_version="a1b2c3d4e5f6",
                prompt="演示提示词",
                observation_context={"demo": True},
                artifact_hash=artifact_hash,
                eval_breakdown=breakdown
                if breakdown is not None
                else {"rule.format_compliance@1.0.0": {"score": 1.0}},
                score=score,
                cost=cost or CostRecord(llm_calls=1, wall_clock_seconds=1.0),
                status=status,
                created_at=created_at,
            )

        # ---------- 步骤 1：US1-1 immutable ----------
        weights_v1 = load_evaluator_weights(MOVIE_YAML, "visual")
        tree = DiscoveryTree(
            tree_id=new_id(),
            project_id="demo-project",
            agent_id="demo-agent",
            policy_version="a1b2c3d4e5f6",
            root_id=new_id(),
            node_ids=[],
            config_snapshot={"evaluator_weights": weights_v1},
        )
        store.create_tree(tree)
        root = make_node(tree.tree_id, None, 0, created_at=1.0)
        store.append_node(root)
        for i in range(2):  # 共 3 个节点
            store.append_node(make_node(tree.tree_id, root.node_id, 1, created_at=2.0 + i))

        # 相同内容工件二次写入：内容寻址去重，对象数不增
        content = b"demo-artifact"
        h1 = artifacts.put(content)
        objects_before = len(list(artifacts._root.iterdir()))
        h2 = artifacts.put(content)
        artifact_dedup = h1 == h2 and len(list(artifacts._root.iterdir())) == objects_before

        hash_before = _node_hash(store.get_node(root.node_id))
        rejected = False
        try:
            with translate_immutable_errors(), engine.begin() as conn:
                conn.execute(
                    update(tree_nodes).where(tree_nodes.c.node_id == root.node_id).values(score=0.0)
                )
        except ImmutableViolationError:
            rejected = True
        hash_after = _node_hash(store.get_node(root.node_id))
        report["steps"]["us1_1_immutable"] = {
            "update_rejected": rejected,
            "content_unchanged": hash_before == hash_after,
            "consistency_hash": hash_after,
            "artifact_dedup": artifact_dedup,
            "ok": rejected and hash_before == hash_after and artifact_dedup,
        }

        # ---------- 步骤 2：US1-2 三维谱系查询 ----------
        other = DiscoveryTree(
            tree_id=new_id(),
            project_id="demo-project",
            agent_id="other-agent",
            policy_version="ffff00001111",
            root_id=new_id(),
            node_ids=[],
            config_snapshot={"evaluator_weights": weights_v1},
        )
        store.create_tree(other)
        store.append_node(make_node(other.tree_id, None, 0, created_at=1.0))

        hits = store.trees_by(
            project_id="demo-project", agent_id="demo-agent", policy_version="a1b2c3d4e5f6"
        )
        hierarchy = [
            {"node_id": n.node_id, "depth": n.depth, "status": n.status.value}
            for n in store.nodes_of(tree.tree_id)
        ]
        report["steps"]["us1_2_lineage_query"] = {
            "matched_tree_ids": [t.tree_id for t in hits],
            "exclusive_hit": [t.tree_id for t in hits] == [tree.tree_id],
            "node_hierarchy": hierarchy,
            "ok": [t.tree_id for t in hits] == [tree.tree_id] and len(hierarchy) == 3,
        }

        # ---------- 步骤 3：US1-3 失败节点 ----------
        # 模拟评估器崩溃：节点以 failed 落盘，score 为 null，成本照常入账
        failed = make_node(
            tree.tree_id,
            root.node_id,
            1,
            status=NodeStatus.FAILED,
            score=None,
            breakdown={},
            cost=CostRecord(llm_calls=3, llm_tokens=1500, wall_clock_seconds=2.5),
            created_at=9.0,
        )
        store.append_node(failed)
        got = store.get_node(failed.node_id)
        report["steps"]["us1_3_failed_node"] = {
            "status": got.status.value,
            "score_is_null": got.score is None,
            "cost": asdict(got.cost),
            "ok": got.status is NodeStatus.FAILED and got.score is None and got.cost.llm_calls == 3,
        }

        # ---------- 步骤 4：US2-1/2 注册校验 ----------
        registry = Registry()
        proxy = StubProxyEvaluator("proxy.aesthetic", 0.8)
        registry.register(proxy)
        duplicate_rejected = False
        try:
            registry.register(StubProxyEvaluator("proxy.aesthetic", 0.9))  # 同键重复注册
        except RegistrationError:
            duplicate_rejected = True
        nondet_rejected = False
        try:
            registry.register(StubNonDeterministicEvaluator())
        except RegistrationError:
            nondet_rejected = True
        human = StubHumanEvaluator()
        registry.register(human)  # 人类锚点：非确定性例外放行
        report["steps"]["us2_registration"] = {
            "duplicate_rejected": duplicate_rejected,
            "nondeterministic_rejected": nondet_rejected,
            "human_anchor_registered": human.spec.key in {s.key for s in registry.list_all()},
            "ok": duplicate_rejected and nondet_rejected,
        }

        # ---------- 步骤 5：US3-1/2 合成评分 ----------
        artifact_ref = ArtifactRef(artifact_hash=h1)

        def run_breakdown(gate_score: float) -> dict:
            return {
                key: factory(gate_score if key.startswith("rule.") else 0.8).evaluate(
                    artifact_ref, {}
                )
                for key, factory in STUB_EVALUATORS.items()
            }

        gated = composite_score(run_breakdown(0.0), weights_v1)
        normal = composite_score(run_breakdown(1.0), weights_v1)
        expected_normal = sum(
            weights_v1[k] * (0.8 if not k.startswith("rule.") else 1.0) for k in weights_v1
        )
        report["steps"]["us3_composite"] = {
            "gate_zero_gives_zero": gated == 0.0,
            "weighted_sum": normal,
            "expected_weighted_sum": expected_normal,
            "weights_source": "configs/movie.yaml#evaluator_weights.visual",
            "ok": gated == 0.0 and abs(normal - expected_normal) < 1e-9,
        }

        # ---------- 步骤 6：US3-3 快照冻结 ----------
        # 模拟配置变更：写入一份权重不同的配置副本，已落盘树的快照不受影响
        modified = yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8"))
        modified["evaluator_weights"]["visual"]["proxy.aesthetic"] = 0.9
        modified_path = Path(tmp) / "movie_modified.yaml"
        modified_path.write_text(yaml.safe_dump(modified, allow_unicode=True))
        weights_v2 = load_evaluator_weights(modified_path, "visual")
        persisted = store.trees_by(project_id="demo-project", agent_id="demo-agent")[0]
        frozen_weights = persisted.config_snapshot["evaluator_weights"]
        report["steps"]["us3_3_snapshot_frozen"] = {
            "config_changed": weights_v2 != weights_v1,
            "snapshot_unchanged": frozen_weights == weights_v1,
            "ok": weights_v2 != weights_v1 and frozen_weights == weights_v1,
        }

    report["all_ok"] = all(step["ok"] for step in report["steps"].values())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
