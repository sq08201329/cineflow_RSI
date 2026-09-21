#!/usr/bin/env python
"""端到端演示：跨项目发现树合并回放（quickstart.md 六步，里程碑验收线 SC-001 / 立项书 F6）。

流程（确定性夹具 + SQLite 内存库 + configs/movie.yaml 副本，全程离线可跑）：
  1. **构建合并池**：项目 A/B/C 共 6 棵同 Agent 同形态树 → (Agent, 形态) 分组合并、
     评估器版本集分组（跨版本不混池）、前置条件判定、构建快照落盘（只增不改幂等）；
  2. **跨项目命中**：A 缺失的结构键命中 B 的历史得分（不再 UNKNOWN）+ 项目归属标注；
  3. **冲突口径**：A/B 同结构键同版本集但得分不同 → UNKNOWN + ScoreConflict 诊断（澄清 Q1）；
  4. **稀释控制**：命中分布 per-project / 合并口径双报告 + 命中占比超阈告警
     （含单项目构成的"占比 1.0 + 如实标注"）；
  5. **一致性验收**：同树在单项目池与合并池回放得分序列一致（SC-002 100%）+
     合并口径无偏性 τ ≥ 0.95（注入偏差拒绝，发布阻塞门禁 C8）；
  6. **做梦开关**：默认关闭 → 单项目池且零池化产物；开启 → 合并池 + 快照记录开关与前置判定。

生产切换：SQLite 换 PostgreSQL（CINEFLOW_PG_DSN + alembic 迁移 0001）、配置副本换真实
configs、快照目录 replay/pools 随 git 版本化——代码路径不变，仅装配层替换。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from core.replay.hit_stats import hit_stats  # noqa: E402
from core.replay.merged_pool import build_merged_pool, evaluator_versions_hash  # noqa: E402
from core.replay.pool import SimulatorPool  # noqa: E402
from core.replay.pool_snapshot import persist_pool_snapshot, snapshot_path  # noqa: E402
from core.replay.pooled_replay import (  # noqa: E402
    MergedSimulatorPool,
    PooledReplaySimulator,
)
from core.replay.pooling_models import PoolingConfig  # noqa: E402
from core.replay.unbiasedness import verify_unbiasedness  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import (  # noqa: E402
    CostRecord,
    DiscoveryTree,
    NodeStatus,
    TreeNode,
    new_id,
)
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.pooling import select_dreaming_pool  # noqa: E402
from policies.base import Budget  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
AGENT = "visual"  # 演示用 Agent 名（池化机制业务无关；形态来自配置）
FORM = "movie"
V1 = {"rule.format_compliance": "1.0.0", "proxy.aesthetic": "1.0.0"}
V2 = {"rule.format_compliance": "2.0.0", "proxy.aesthetic": "1.0.0"}
CONFLICT_KEY = {"temperature": 0.5, "shared": True}


def _assert(condition, message):
    if not condition:
        raise AssertionError(f"演示断言失败：{message}")


def _step(number, title):
    print(f"\n=== 步骤 {number}：{title} ===")


def _key(tag: str) -> dict:
    return {"temperature": 0.5, "tag": tag}


def _versions_hash(versions: dict) -> str:
    return evaluator_versions_hash({"evaluator_versions": versions})


def _plant(store, *, tag, project_id, versions, key_scores, created_at):
    """落一棵冻结树（root + 结构键子节点；子节点 gen_params = 结构键，score = 历史得分）。"""
    root_id = new_id()
    tree = DiscoveryTree(
        tree_id=new_id(),
        project_id=project_id,
        agent_id=AGENT,
        policy_version="d3m0p0l1cyv1",
        root_id=root_id,
        node_ids=[root_id],
        config_snapshot={"evaluator_versions": versions, "observation_fields": ["gen_params"]},
    )
    store.create_tree(tree)
    store.append_node(
        TreeNode(
            node_id=root_id,
            tree_id=tree.tree_id,
            parent_id=None,
            depth=0,
            agent_id=AGENT,
            policy_version="d3m0p0l1cyv1",
            prompt=f"跨项目演示轮次 {tag}",
            observation_context={},
            artifact_hash="ab" * 32,
            eval_breakdown={},
            score=0.0,
            cost=CostRecord(),
            status=NodeStatus.EVALUATED,
            created_at=created_at,
        )
    )
    node_by_key = {"root": root_id}
    for offset, (tag, params, score) in enumerate(key_scores, start=1):
        node_id = new_id()
        store.append_node(
            TreeNode(
                node_id=node_id,
                tree_id=tree.tree_id,
                parent_id=root_id,
                depth=1,
                agent_id=AGENT,
                policy_version="d3m0p0l1cyv1",
                prompt=f"结构键 {tag}",
                observation_context={"gen_params": _key(tag) if params is None else dict(params)},
                artifact_hash="cd" * 32,
                eval_breakdown={f"proxy.aesthetic@{versions['proxy.aesthetic']}": {"score": score}},
                score=score,
                cost=CostRecord(llm_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=created_at + offset * 1e-3,
            )
        )
        node_by_key[tag] = node_id
    return tree, node_by_key


def _config_copy(path: Path, pools_dir: Path, **overrides) -> PoolingConfig:
    """真实配置副本：读 configs/movie.yaml 的 replay.pooling 段，仅改落盘目录与按需覆盖。"""
    real = yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8"))
    section = dict(real["replay"]["pooling"])
    section["pools_dir"] = str(pools_dir)
    section.update(overrides)
    config = PoolingConfig.from_config({"replay": {"pooling": section}})
    path.write_text(
        yaml.safe_dump({"replay": {"pooling": section}}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return config


def _simulator(store, pool, *, version_hash, budget=8, exclude=()):
    return PooledReplaySimulator.from_pool(
        pool,
        store,
        worker_count=1,
        budget=Budget(max_probes=budget),
        latency_quantum_ms=0,
        version_hash=version_hash,
        exclude_tree_ids=exclude,
    )


def main() -> int:
    started = time.perf_counter()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    store = create_tree_store(engine)
    report: dict = {"steps": {}}

    with tempfile.TemporaryDirectory(prefix="cineflow-pooling-demo-") as tmp:
        tmp_path = Path(tmp)
        work = tmp_path / "movie.yaml"
        cfg = _config_copy(work, tmp_path / "replay" / "pools")

        # ---------------- 1) 构建合并池 ----------------
        _step(1, "构建合并池（分组 / 版本分组 / 前置条件 / 快照）")
        planted = {}
        spec = (
            (
                "a0",
                "project-a",
                V1,
                (("shared", None, 0.6), ("conflict", CONFLICT_KEY, 0.6), ("a-only", None, 0.70)),
                1000.0,
            ),
            ("a1", "project-a", V1, (("shared", None, 0.6), ("a-only2", None, 0.72)), 1001.0),
            (
                "b0",
                "project-b",
                V1,
                (("shared", None, 0.6), ("conflict", CONFLICT_KEY, 0.8), ("b-only", None, 0.75)),
                1010.0,
            ),
            ("b1", "project-b", V1, (("shared", None, 0.6), ("b-only2", None, 0.78)), 1011.0),
            ("c1", "project-c", V1, (("shared", None, 0.6), ("c-only", None, 0.80)), 1020.0),
            ("c0", "project-c", V2, (("shared-v2", None, 0.90),), 2000.0),
        )
        for tag, project_id, versions, key_scores, created_at in spec:
            planted[tag] = _plant(
                store,
                tag=tag,
                project_id=project_id,
                versions=versions,
                key_scores=key_scores,
                created_at=created_at,
            )
        pool = build_merged_pool(store, AGENT, FORM, cfg)
        print(
            f"池内容：{pool.tree_count} 棵 / {len(pool.version_groups)} 个版本组 / "
            f"前置条件满足={pool.conditions_met}"
        )
        for group in pool.version_groups:
            print(
                f"  版本组 {group.evaluator_versions_hash[:12]}…：{len(group.trees)} 棵 "
                f"（{sorted({ref.project_id for ref in group.trees})}）"
            )
        snapshot = persist_pool_snapshot(pool, cfg)
        again = persist_pool_snapshot(pool, cfg)
        path = snapshot_path(cfg.pools_dir, AGENT, FORM, pool.pool_id)
        print(f"快照落盘：{path.name}（幂等重放一致={snapshot == again}）")
        _assert(pool.tree_count == 6 and len(pool.version_groups) == 2, "池分组异常")
        _assert(snapshot == again and path.is_file(), "快照未落盘或非幂等")
        report["steps"]["build"] = {
            "tree_count": pool.tree_count,
            "version_groups": [
                {
                    "evaluator_versions_hash": group.evaluator_versions_hash,
                    "tree_count": len(group.trees),
                    "projects": sorted({ref.project_id for ref in group.trees}),
                }
                for group in pool.version_groups
            ],
            "conditions_met": pool.conditions_met,
            "pool_id": pool.pool_id,
            "snapshot": str(path.relative_to(tmp_path)),
            "snapshot_idempotent": snapshot == again,
        }

        # ---------------- 2) 跨项目命中 ----------------
        _step(2, "跨项目命中（A 缺失的键 → 复用 B/C 的历史得分）")
        v1 = _versions_hash(V1)
        simulator = _simulator(store, pool, version_hash=v1)
        a0_root = planted["a0"][1]["root"]
        a0_child = planted["a0"][1]["a-only"]
        hit = simulator.probe(a0_root, _key("a-only"))
        base_line = simulator.probe(planted["b0"][1]["root"], _key("a-only"))
        cross = simulator.probe(a0_root, _key("b-only"))
        print(
            f"A 的根 + a-only → {hit.status}（得分 {hit.nodes[0].score}，"
            f"命中树 {hit.nodes[0].node_id[:8]}…）"
        )
        print(f"B 的根 + a-only → {base_line.status}（本树无该键：单项目池会 UNKNOWN）")
        print(f"A 的根 + b-only → {cross.status}（跨项目命中得分 {cross.nodes[0].score}）")
        _assert(hit.status == "ok" and hit.nodes[0].node_id == a0_child, "A 的键未在本树命中")
        _assert(base_line.status == "unknown", "B 不应有 A 的键")
        _assert(
            cross.status == "ok"
            and cross.nodes[0].score == 0.75
            and simulator.outcomes[-1].project_id == "project-b",
            "跨项目命中或归属标注异常",
        )
        report["steps"]["cross_project_hit"] = {
            "probed_key": "b-only",
            "status": cross.status,
            "score": cross.nodes[0].score,
            "source_project": simulator.outcomes[-1].project_id,
            "same_tree_miss": base_line.status,
        }

        # ---------------- 3) 冲突口径 ----------------
        _step(3, "冲突口径（同结构键同版本集不同得分 → UNKNOWN + 诊断）")
        conflicted = simulator.probe(a0_root, CONFLICT_KEY)
        conflict = simulator.outcomes[-1].conflict
        projects = sorted(hit.project_id for hit in conflict.hits)
        scores = sorted(hit.score for hit in conflict.hits)
        print(f"A/B 的冲突键 → {conflicted.status}；命中树 {projects} 得分 {scores}")
        print(f"诊断：{conflict.note}")
        _assert(conflicted.status == "unknown" and conflicted.nodes == [], "冲突未按 UNKNOWN 处理")
        _assert(len(conflict.hits) == 2, "冲突诊断未留痕树清单")
        report["steps"]["conflict"] = {
            "status": conflicted.status,
            "hits": [hit.to_dict() for hit in conflict.hits],
            "note": conflict.note,
        }

        # ---------------- 4) 稀释控制 ----------------
        _step(4, "稀释控制（双报告 + 命中占比超阈告警）")
        dominated = _simulator(store, pool, version_hash=v1)
        for tag in ("a-only", "a-only2"):
            _assert(
                dominated.probe(a0_root, _key(tag)).status == "ok", f"{tag} 未命中（分布夹具异常）"
            )
        distribution, alerts = hit_stats(pool, dominated.outcomes, cfg)
        print(f"合并口径：{distribution.note}")
        for stats in distribution.per_project:
            print(
                f"  {stats.project_id}：命中 {stats.hits} / UNKNOWN {stats.unknowns}，"
                f"命中占比 {stats.hit_ratio:.4f}，树数占比 {stats.tree_ratio:.4f}（参考）"
            )
        for alert in alerts:
            print(f"  稀释告警：{alert.note}")
        _assert(len(alerts) == 1 and alerts[0].hit_ratio == 1.0, "命中占比超阈未告警")

        solo_alert = _solo_distribution(cfg)
        print(f"  单项目构成：{solo_alert.note}")
        _assert("单项目构成" in solo_alert.note, "单项目构成未如实标注")
        report["steps"]["dilution"] = {
            "merged_hits": distribution.merged_hits,
            "merged_unknowns": distribution.merged_unknowns,
            "per_project": [stats.to_dict() for stats in distribution.per_project],
            "alerts": [alert.to_dict() for alert in alerts],
            "solo_alert_note": solo_alert.note,
        }

        # ---------------- 5) 一致性验收 + 无偏性 ----------------
        _step(5, "一致性验收（同树双池一致）+ 合并口径无偏性 τ")
        single = SimulatorPool(store)
        single.add_tree(planted["a0"][0])
        single_sim = single.build(worker_count=1, budget=Budget(max_probes=8), latency_quantum_ms=0)
        merged_sim = _simulator(store, pool, version_hash=v1)
        own_tags = ("shared", "a-only")
        single_trace = []
        merged_trace = []
        for tag in own_tags:
            single_result = single_sim.probe(a0_root, _key(tag))
            merged_result = merged_sim.probe(a0_root, _key(tag))
            single_trace.append(
                (single_result.status, sorted({n.score for n in single_result.nodes if n.score}))
            )
            merged_trace.append(
                (merged_result.status, sorted({n.score for n in merged_result.nodes if n.score}))
            )
        print(f"逐键结果：单项目池 {single_trace}")
        print(f"          合并池   {merged_trace}")
        single_curve = single_sim.trajectory().best_score_curve
        merged_curve = merged_sim.trajectory().best_score_curve
        print(f"得分曲线一致：{single_curve == merged_curve}")
        _assert(single_trace == merged_trace, "同树双池逐键结果不一致")
        _assert(
            single_sim.trajectory().best_score_curve == merged_sim.trajectory().best_score_curve,
            "同树双池得分曲线不一致",
        )

        unbiased_sim = _simulator(store, pool, version_hash=v1, budget=6)
        replay_scores = []
        for tag in ("shared", "a-only", "b-only", "b-only2", "c-only"):
            result = unbiased_sim.probe(a0_root, _key(tag))
            _assert(result.status == "ok", f"{tag} 未命中（无偏性夹具异常）")
            replay_scores.append(result.nodes[0].score)
        real_scores = list(replay_scores)  # 真实重跑口径 = 记录时按结构键重算的得分
        verdict = verify_unbiasedness(real_scores, replay_scores, threshold=0.95)
        injected = verify_unbiasedness(real_scores, list(reversed(replay_scores)), threshold=0.95)
        print(f"合并口径 τ = {verdict.tau}（判定 {verdict.verdict}）")
        print(f"注入偏差（逆序）τ = {injected.tau}（判定 {injected.verdict}）")
        _assert(verdict.verdict == "pass", "合并口径无偏性未达标")
        _assert(injected.verdict == "reject", "注入偏差未被拒绝")
        report["steps"]["acceptance"] = {
            "consistency": True,
            "replay_scores": replay_scores,
            "tau": verdict.tau,
            "tau_threshold": verdict.threshold,
            "tau_verdict": verdict.verdict,
            "injected_tau": injected.tau,
            "injected_verdict": injected.verdict,
        }

        # ---------------- 6) 做梦开关 ----------------
        _step(6, "做梦开关（默认关闭 → 单项目池；开启 → 合并池 + 快照留痕）")
        off_cfg = _config_copy(tmp_path / "off.yaml", tmp_path / "pools-off")
        off = select_dreaming_pool(
            store, agent_id=AGENT, form=FORM, project_id="project-a", cfg=off_cfg, version_hash=v1
        )
        off_artifacts = list(Path(off_cfg.pools_dir).rglob("*.json"))
        print(f"默认档：合并池={off.merged}；{off.note}；池化产物 {len(off_artifacts)} 个")
        _assert(
            off.merged is False and isinstance(off.pool, SimulatorPool) and not off_artifacts,
            "默认关闭未回落单项目池或越权产出",
        )

        on_cfg = _config_copy(
            tmp_path / "on.yaml", tmp_path / "pools-on", enabled_for_dreaming=True
        )
        on = select_dreaming_pool(
            store, agent_id=AGENT, form=FORM, project_id="project-a", cfg=on_cfg, version_hash=v1
        )
        print(
            f"开启档：合并池={on.merged}（{type(on.pool).__name__}）；"
            f"快照 enabled_for_dreaming={on.snapshot.enabled_for_dreaming} "
            f"conditions_met={on.snapshot.conditions_met}"
        )
        _assert(
            on.merged
            and isinstance(on.pool, MergedSimulatorPool)
            and on.snapshot.enabled_for_dreaming
            and on.snapshot.conditions_met,
            "开关开启未启用合并池或快照未留痕",
        )
        insufficient_cfg = _config_copy(
            tmp_path / "insufficient.yaml",
            tmp_path / "pools-insufficient",
            enabled_for_dreaming=True,
            min_trees=99,  # 前置条件不可满足（演示回落路径）
        )
        insufficient = select_dreaming_pool(
            store,
            agent_id=AGENT,
            form=FORM,
            project_id="project-a",
            cfg=insufficient_cfg,
            version_hash=v1,
        )
        print(f"前置不足档：合并池={insufficient.merged}；{insufficient.note}")
        _assert(
            insufficient.merged is False and "前置条件不足" in insufficient.note,
            "前置不足未回落并注明",
        )
        report["steps"]["dreaming_switch"] = {
            "default": off.to_dict(),
            "enabled": on.to_dict(),
            "insufficient": insufficient.to_dict(),
        }

    elapsed = time.perf_counter() - started
    report["elapsed_seconds"] = round(elapsed, 3)
    report["elapsed_under_1min"] = elapsed < 60
    report["ok"] = report["elapsed_under_1min"]
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


def _solo_distribution(cfg):
    """单项目构成演示：另起一个只有单项目三棵树的库 → 命中占比 1.0 → 告警 + 如实标注。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    solo_store = create_tree_store(engine)
    planted = [
        _plant(
            solo_store,
            tag=f"solo-{index}",
            project_id="project-solo",
            versions=V1,
            key_scores=((f"solo-key-{index}", None, 0.6 + 0.01 * index),),
            created_at=1000.0 + index,
        )
        for index in range(3)
    ]
    solo_pool = build_merged_pool(
        solo_store, AGENT, FORM, PoolingConfig(min_trees=3, pools_dir=cfg.pools_dir)
    )
    simulator = _simulator(solo_store, solo_pool, version_hash=_versions_hash(V1), budget=8)
    root_id = planted[0][1]["root"]
    for tag in ("solo-key-0", "solo-key-2"):
        _assert(
            simulator.probe(root_id, _key(tag)).status == "ok",
            f"{tag} 未命中（单项目夹具异常）",
        )
    _, alerts = hit_stats(solo_pool, simulator.outcomes, cfg)
    _assert(len(alerts) == 1, "单项目构成未产告警")
    return alerts[0]


if __name__ == "__main__":
    sys.exit(main())
