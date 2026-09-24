#!/usr/bin/env python
"""端到端演示：开发 Agent 降级模式（quickstart.md 六步，功能 017 —— 阶段 4 交付步①②）。

演示（确定性夹具 + Mock 网关 + SQLite 内存库 + **仓库真实人工策略 + 真实四评估器**，
全程离线无需凭证）：

  1. **立项组合产出落树与成本对账**：引导树人工策略（`policies/history/dev/{版本}.py`）+
     立项约束 → 逐条目经网关生成立项论证要点 → 立项组合工件内容寻址落树（节点含
     `policy_version`）→ 真实四评估器打分（两门禁 + 两模拟数据源驱动的确定性代理）→
     成本入账并对账（树内 == 运营表 + 评估器计费增量）；同轮次二次触发幂等重建
     （0 重复生成、0 重复扣费、0 重复节点）；产物与代理分量诊断均带模拟源来源标注（SC-009）
  2. **结构/组合门禁短路重算**：真实二门禁（`rule.slate_structure` / `rule.slate_combination`）
     ——违规组合（方向标识重复 + 条目数越界 + 标记越界且悬空）被判 0 → 总分 0，
     **两代理未跑也未落分量键**（gate 判 0 ⇒ 不跑后续分量，阶段 3 的桩组合无法断言这一点）
     且诊断点名违规项；合规组合重跑逐位一致且命中网关缓存（零边际成本复现，原则三）

其余四步属 US3（`_step_3.._step_6`，`steps` 键名带序号）：

  ③ **无偏性凭证与回放对比**：先提交两版人工策略（单一阶段形态）并跑够轮次 → 冷启动期池子
     不足 → **拒绝产出对比报告**（错误含实测树数与门槛）→ 无偏性验收（回放 vs 真实重跑
     τ ≥ 0.95）→ 回放对比报告（逐树/分项/pareto_auc/UNKNOWN）
  ④ **采纳门禁**：未采纳指针逐字节不变；采纳后部署指针更新 + AdoptionRecord 留痕
  ⑤ **禁止自动进化拒绝语义**（三重机检）：`run_dream_round(agent_id="dev")` 显式拒绝
     （0 候选 0 计费 0 落盘）+ 名单实值 + 策略 meta 审计位 + 部署门禁禁止名单判定
  ⑥ **升级判据材料**：全量阈值快照 + 逐项"实测值 / 无法评价（来源缺失）" + 系统结论非达标
     + 继续观察条件（待补齐阈值项清单）

演示档形态（同 009 演示纪律：**只改形态参数取值、不改口径**）：页数窗口类参数换成
`dev.signals.fixtures` 逐题材系数——模拟数据源按题材给出不同系数，逐题材得分谱系才分散
（无偏性 τ 与"谁更好"才有判别力）；两版演示策略只探索不同题材方向。

断言：六步全 ok=true，退出码 0；生产切换仅装配层替换（PG/S3/真实 LLM 网关），代码路径不变
（同 004/006/007/008/009 演示纪律）。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, func, select  # noqa: E402

from agents.dev.artifact import TopicSlate  # noqa: E402
from agents.dev.config import DevConfig  # noqa: E402
from agents.dev.db import create_jobs_schema, dev_jobs  # noqa: E402
from agents.dev.loop import run_dev_round  # noqa: E402
from agents.dev.policy_versions import list_policy_versions, load_policy_source  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402

DEV_YAML = REPO_ROOT / "configs" / "movie.yaml"
HISTORY_ROOT = REPO_ROOT / "policies" / "history"
INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}

# 演示档题材档位：模拟数据源按题材夹具给出不同系数（低/高两档）——逐档得分谱系才分散。
# 低档题材刻意取仓库首版策略方向池内的题材（部署侧 = 该策略，两版得分可分出高下）
_LOW_GENRES = ("医疗悬疑", "都市犯罪", "科幻悬疑")
_HIGH_GENRES = ("现实职场", "青春成长", "家庭剧情")
_FIXTURES = {genre: {"box_office_factor": 0.3, "buzz_factor": 0.3} for genre in _LOW_GENRES} | {
    genre: {"box_office_factor": 1.4, "buzz_factor": 0.9} for genre in _HIGH_GENRES
}

# 违规策略源码（演示档）：条目数越界（2 条 < 区间下界 3）+ 方向标识重复 +
# "本轮进入生产"标记越界且悬空（指向不存在方向）——三类违规分别落在两门禁的判定面。
_VIOLATING_SOURCE = '''class Policy:
    """演示档违规策略：条目数越界 + 方向标识重复 + 标记越界且悬空。"""

    def plan(self, inputs, config):
        return {
            "entries": [
                {
                    "direction_id": "dir-dup",
                    "genre": "都市犯罪",
                    "constraints": ["夜戏为主"],
                    "characters": ["方原"],
                },
                {
                    "direction_id": "dir-dup",
                    "genre": "家庭剧情",
                    "constraints": [],
                    "characters": [],
                },
            ],
            "production_marks": ["dir-dup", "dir-missing"],
        }
'''


def _policy_of(source: str, version: str):
    """实例化策略源码并绑定版本（与 ops/dev.py 的加载口径同构：版本 = 源码 BLAKE3 前 12 位）。"""
    namespace: dict = {"__name__": "dev_demo_policy"}
    exec(compile(source, "<demo-policy>", "exec"), namespace)  # noqa: S102 - 演示档自有源码
    policy = namespace["Policy"]()
    policy.policy_version = version
    return policy


class _CountingGenerator:
    """候选生成计数桩：命中拒绝名单时必须恒 0（宪章原则六审计）。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, champion_source, digest, m):
        self.calls += 1
        return []


def _dream_config():
    """做梦层形态配置（策略通道要用：谱系 meta 的 reward 占位 λ + 禁自动进化名单审计位）。"""
    from dreaming.config import DreamConfig

    return DreamConfig.from_yaml(DEV_YAML)


def _bootstrap_source() -> tuple[str, str]:
    """仓库引导树人工策略（源码, 版本）：版本取 `policies/history/dev/` 首个版本化版本。

    该策略的**计划是扁平形态**（`{entries, production_marks}`，历史不可改写）——步③的回放
    对比即以此验证"扁平计划经单一阶段声明同样可回放"（不再退化为全 UNKNOWN 报告）。
    """
    import blake3

    versions = list_policy_versions(history_root=HISTORY_ROOT, agent_id="dev")
    if not versions:
        raise SystemExit(
            "缺少人工策略首版：先 `uv run python ops/dev.py submit "
            "--source-file <策略.py> --by <人>`"
        )
    version = versions[0]
    source = load_policy_source(version, history_root=HISTORY_ROOT, agent_id="dev")
    digest = blake3.blake3(source.encode()).hexdigest()[:12]
    assert digest == version, f"策略版本 {version} 与源码内容不符（哈希 {digest}）"
    return source, version


def _bootstrap_policy():
    """仓库引导树人工策略对象（版本绑定到对象：节点落盘口径）。"""
    source, version = _bootstrap_source()
    return _policy_of(source, version)


def _node_of(store, tree_id: str):
    return next(item for item in store.nodes_of(tree_id) if item.parent_id is not None)


def _component_versions(eval_breakdown: dict) -> dict:
    """节点 `eval_breakdown` → {裸键: 版本}（分量键带 `@版本`，原则一）。"""
    return {key.split("@")[0]: key.split("@")[1] for key in eval_breakdown}


def _step_1_立项组合产出落树与对账(
    store, artifacts, engine, gateway, config, policy, report
) -> None:
    """步①：产出落树 + 真实四评估器打分 + 成本对账 + 幂等重建（0 重复生成/扣费/节点）。"""
    calls_before = gateway.call_count
    result = run_dev_round(
        round_id="demo-r1",
        policy=policy,
        store=store,
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=config,
        inputs=INPUTS,
        evaluators=None,  # 真实四评估器装配（两门禁 + 两确定性代理）
    )
    node = _node_of(store, result.tree_id)
    slate = TopicSlate.from_dict(json.loads(artifacts.get(node.artifact_hash)))
    sources = slate.to_dict()["signal_sources"]
    first_round_calls = gateway.call_count - calls_before

    replayed = run_dev_round(
        round_id="demo-r1",
        policy=policy,
        store=store,
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=config,
        inputs=INPUTS,
        evaluators=None,
    )
    with engine.connect() as conn:
        rows = conn.execute(
            select(func.count()).select_from(dev_jobs).where(dev_jobs.c.round_id == "demo-r1")
        ).scalar()
    versions = _component_versions(node.eval_breakdown)
    # 来源标注第二落点：代理分量诊断（对比报告与判据材料的数据面，SC-009）
    proxy_annotated = {
        key: (
            fragment["diagnostics"].get("simulated") is True
            and "非真实商业数据" in fragment["diagnostics"].get("note", "")
            and bool(fragment["diagnostics"].get("source"))
            and len(fragment["diagnostics"].get("params_digest", "")) == 12
        )
        for key, fragment in node.eval_breakdown.items()
        if key.startswith("proxy.")
    }
    step1 = {
        "policy_version": result.policy_version,
        "job_status": result.job["status"],
        "artifact_hash": result.job["artifact_hash"],
        "entries": list(slate.direction_ids()),
        "slate_interval": list(config.slate_entries),
        "production_marks": list(slate.produce_ids()),
        "marks_interval": list(config.production_marks),
        "signal_sources_annotated": all(
            source["simulated"] and "非真实商业数据" in source["note"] for source in sources
        ),
        "score": node.score,
        "components": sorted(versions),
        "evaluator_versions": versions,
        "proxy_components_annotated": proxy_annotated,
        "generation_calls": first_round_calls,
        "spent_usd": round(result.spent_usd, 6),
        "reconciliation": result.cost_reconciliation,
        "idempotent_replay": {
            "same_job": replayed.job == result.job,
            "extra_generation_calls": gateway.call_count - calls_before - first_round_calls,
            "rows": rows,
            "nodes": len(store.nodes_of(result.tree_id)),
            "reconciliation": replayed.cost_reconciliation,
        },
    }
    interval_ok = (
        config.slate_entries[0] <= len(step1["entries"]) <= config.slate_entries[1]
        and config.production_marks[0]
        <= len(step1["production_marks"])
        <= config.production_marks[1]
    )
    step1["ok"] = (
        step1["job_status"] == "inserted"
        and interval_ok
        and len(step1["components"]) == len(config.evaluator_weights) == 4
        and all(version.startswith("1.0.0+") for version in versions.values())
        and step1["signal_sources_annotated"]
        and len(proxy_annotated) == 2
        and all(proxy_annotated.values())
        and first_round_calls == len(slate.entries)
        and result.spent_usd > 0
        and result.cost_reconciliation["consistent"] is True
        and step1["idempotent_replay"]
        == {
            "same_job": True,
            "extra_generation_calls": 0,
            "rows": 1,
            "nodes": 2,
            "reconciliation": {},
        }
    )
    report["steps"]["1_立项组合产出落树与对账"] = step1


def _step_2_结构组合门禁短路与重算(
    store, artifacts, engine, gateway, config, policy, report
) -> None:
    """步②：真实门禁判 0（gate 短路，代理不跑）+ 合规组合重跑逐位一致（缓存命中复现）。"""
    from agents.dev.loop import run_dev_round as _run  # 同模块内取用，保持步函数自足

    violating = _run(
        round_id="demo-r2",
        policy=_policy_of(_VIOLATING_SOURCE, "demo-violating"),
        store=store,
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=config,
        inputs=INPUTS,
        evaluators=None,
    )
    violating_node = _node_of(store, violating.tree_id)
    violating_versions = _component_versions(violating_node.eval_breakdown)
    violations = {
        key.split("@")[0]: fragment.get("diagnostics", {}).get("violations", [])
        for key, fragment in violating_node.eval_breakdown.items()
    }

    # 重算：合规策略换轮次重跑——同一策略 + 同输入 ⇒ 同结构键 ⇒ 命中文档缓存零成本复现
    calls_before = gateway.call_count
    cache_hits_before = gateway.cache_hits
    rerun = _run(
        round_id="demo-r3",
        policy=policy,
        store=store,
        artifacts=artifacts,
        engine=engine,
        gateway=gateway,
        config=config,
        inputs=INPUTS,
        evaluators=None,
    )
    first_node = _node_of(store, "dev-round-demo-r1")
    rerun_node = _node_of(store, rerun.tree_id)
    rerun_slate = TopicSlate.from_dict(json.loads(artifacts.get(rerun_node.artifact_hash)))
    step2 = {
        "violating_score": violating_node.score,
        "violating_components": sorted(violating_versions),
        "violating_violations": violations,
        # gate 判 0 ⇒ 不跑后续分量：代理键缺席即"未跑也未计费"（阶段 3 的桩组合无法断言）
        "proxies_skipped_on_gate_zero": not any(
            key.startswith("proxy.") for key in violating_versions
        ),
        "rerun_score": rerun_node.score,
        "rerun_components": sorted(_component_versions(rerun_node.eval_breakdown)),
        "rerun_matches_first_round": rerun_node.score == first_node.score,
        "rerun_artifact_matches": rerun_node.artifact_hash == first_node.artifact_hash,
        "rerun_cache_hits": gateway.cache_hits - cache_hits_before,
        "rerun_generation_calls": gateway.call_count - calls_before,
        "rerun_marks": list(rerun_slate.produce_ids()),
    }
    step2["ok"] = (
        step2["violating_score"] == 0.0  # 两门禁判 0 → gate 短路，代理分不救场
        and step2["violating_components"] == ["rule.slate_combination", "rule.slate_structure"]
        and step2["proxies_skipped_on_gate_zero"]  # 代理未跑也未落分量键
        and any("重复" in item for item in violations["rule.slate_structure"])
        and any("区间" in item for item in violations["rule.slate_structure"])
        and any("不存在" in item for item in violations["rule.slate_combination"])
        and any("标记数" in item for item in violations["rule.slate_combination"])
        and len(step2["rerun_components"])
        == len(config.evaluator_weights)
        == 4  # 合规组合跑满四分量
        and step2["rerun_matches_first_round"]
        and step2["rerun_artifact_matches"]  # 内容寻址逐位一致（确定性）
        and step2["rerun_generation_calls"] == 0  # 缓存命中即零成本复现（原则三）
        and step2["rerun_cache_hits"] == len(rerun_slate.entries)
    )
    report["steps"]["2_结构组合门禁短路与重算"] = step2


def _demo_policy_source(genres) -> str:
    """演示档人工策略源码（单一阶段形态）：逐题材方向取一条，标记取首条。

    计划形态 = 单一阶段 `{slate: {entries, production_marks}}`（回放对比按阶段取结构键）；
    两版演示策略只探索不同**题材档位**（低/高系数），其余结构一致。
    """
    entries = [
        {
            "direction_id": f"dir-{genre}-{index}",
            "genre": genre,
            "constraints": ["单场景为主", f"档位 {index}"],
            "characters": [f"角色甲{index}", f"角色乙{index}"],
            "rationale_seed": f"{genre}方向的立项论证草稿（演示档，人写的判断力）。",
        }
        for index, genre in enumerate(genres)
    ]
    plan = {
        "slate": {
            "entries": entries,
            "production_marks": [entries[0]["direction_id"]],
        }
    }
    return (
        "class Policy:\n"
        '    """演示档人工题材方向探索策略（单一阶段计划内联）。"""\n'
        f"    PLAN = {plan!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLAN\n"
    )


def _demo_config_copy(root: Path, *, deployed_version: str) -> tuple[Path, DevConfig]:
    """movie.yaml 副本：题材夹具（模拟源逐档系数）+ 部署指针 `deployment.dev`。

    演示档只改**形态参数取值**（`dev.signals.fixtures` 夹具档），不改任何口径：
    模拟数据源按题材给出不同系数，逐档得分谱系才分散（τ 与"谁更好"才有判别力）。
    """
    import copy

    import yaml

    raw = copy.deepcopy(yaml.safe_load(DEV_YAML.read_text(encoding="utf-8")))
    raw["dev"]["signals"]["fixtures"] = {"genres": copy.deepcopy(_FIXTURES)}
    raw["deployment"] = {"dev": {"current_policy_version": deployed_version}}
    path = root / "movie.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, DevConfig.from_dict(raw)


def _pool_of(store):
    """回放池装配：已冻结树入池，未冻结树跳过并计数（不静默丢弃）。"""
    from core.replay.pool import PoolError, SimulatorPool

    pool = SimulatorPool(store)
    skipped = 0
    for tree in store.trees_by(agent_id="dev"):
        try:
            pool.add_tree(tree)
        except PoolError:
            skipped += 1
    return pool, skipped


def _rerun_score(node, artifacts, config, assembly):
    """真实重跑：读落盘工件 → 真实四评估器重算 → 合成定点（确定性、零 LLM）。"""
    from agents.dev.evaluators.composite import evaluate_dev
    from core.evaluators.base import ArtifactRef

    slate = TopicSlate.from_dict(json.loads(artifacts.get(node.artifact_hash)))
    reference = ArtifactRef(artifact_hash=node.artifact_hash, metadata={"agent_id": "dev"})
    context = {"artifact": slate, "inputs": INPUTS, "config": config}
    _, score, _ = evaluate_dev(assembly, reference, context, config.evaluator_weights)
    return score


def _step_3_无偏性凭证与回放对比(root, report, dream_config, gateway) -> None:
    """步③：人工改策略 → 静态检查 → 冷启动拒绝 → 无偏性凭证 → 回放对比报告。"""
    from agents.dev.evaluators import build_dev_evaluators
    from agents.dev.policy_versions import submit_policy
    from agents.dev.sandbox_compare import (
        CompareError,
        UnbiasednessAttestation,
        compare_versions,
        replay_policy,
        signal_sources_of,
    )
    from core.replay.unbiasedness import verify_unbiasedness

    history_root = root / "policies"
    # 部署侧 = 仓库真实人工策略首版（**扁平计划**，历史不可改写）；候选侧 = 演示档改进版
    deployed_source, _bootstrap_version = _bootstrap_source()
    candidate_source = _demo_policy_source(_HIGH_GENRES)
    deployed = submit_policy(deployed_source, "sunqi", dream_config, history_root=history_root)
    candidate = submit_policy(
        candidate_source,
        "sunqi",
        dream_config,
        parent_version=deployed.version,  # 谱系：改进来源指向现部署版本
        history_root=history_root,
    )
    sources = {deployed.version: deployed_source, candidate.version: candidate_source}
    policies = {
        deployed.version: _policy_of(deployed_source, deployed.version),
        candidate.version: _policy_of(candidate_source, candidate.version),
    }
    # 演示档形态（题材夹具档位）+ 部署指针 = 现部署版本（采纳门禁的基线一致性检查要用）
    config_path, config = _demo_config_copy(root, deployed_version=deployed.version)

    # 回放池用独立库：冷启动（池内只积累被对比版本产出的树）才检验得出最小池门槛
    pool_engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(pool_engine)
    create_jobs_schema(pool_engine)
    pool_store = create_tree_store(pool_engine)
    artifacts = LocalArtifactStore(root / "pool-artifacts")
    generation_calls_before = gateway.call_count

    def _produce(round_id: str, version: str):
        return run_dev_round(
            round_id=round_id,
            policy=policies[version],
            store=pool_store,
            artifacts=artifacts,
            engine=pool_engine,
            gateway=gateway,
            config=config,
            inputs=INPUTS,
            evaluators=None,  # 真实四评估器装配
        )

    for index, version in enumerate((deployed.version, candidate.version), start=1):
        _produce(f"demo-pool-r{index}", version)
    pool, skipped = _pool_of(pool_store)
    generation_calls = gateway.call_count - generation_calls_before
    cold_start_trees = len(pool.trees)

    assembly = build_dev_evaluators(config)

    def _attestation(current_pool, label: str):
        """无偏性验收凭证：回放（池 probe 揭示的历史得分）vs 真实重跑（工件重算）。"""
        replay_scores: list[float] = []
        real_scores: list[float] = []
        hits: list[str] = []
        for tree in current_pool.trees:
            replay = replay_policy(
                sources[tree.policy_version],
                [tree],
                cfg=config,
                inputs=INPUTS,
                store=pool_store,
            )
            row = replay.per_tree[0]
            hits.append(row["hits"][0] if row["hits"] else "")
            replay_scores.append(row["score"])
            node = _node_of(pool_store, tree.tree_id)
            real_scores.append(_rerun_score(node, artifacts, config, assembly))
        report_ = verify_unbiasedness(real_scores, replay_scores, threshold=0.95)
        path = root / label
        path.write_text(
            json.dumps(report_.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report_, path, hits, replay_scores == real_scores

    # 冷启动：可比对树数 < 形态下限 → 拒绝产出报告（错误含实测树数与门槛值）；
    # 无偏性凭证先行（回放口径可信），使本次拒绝严格来自最小池门槛而非凭证门禁
    _, cold_path, _, _ = _attestation(pool, "unbiasedness-cold-start.json")
    refusal = ""
    try:
        compare_versions(
            candidate.version,
            deployed.version,
            pool,
            config,
            store=pool_store,
            inputs=INPUTS,
            unbiasedness=UnbiasednessAttestation.load(cold_path),
            history_root=history_root,
            comparison_dir=root / "comparisons",
        )
        refusal = "未拒绝（契约破坏）"
    except CompareError as exc:
        refusal = str(exc)

    # 积累记录到门槛之上（"门槛按形态配置，记录积累够了才出报告"）
    for index in range(3, 5):
        _produce(f"demo-pool-r{index}", deployed.version if index % 2 else candidate.version)
    pool, skipped = _pool_of(pool_store)

    # 无偏性验收（积累后的全量记录）：回放（池 probe 揭示的历史得分）vs 真实重跑（工件重算）
    unbiased, attestation_path, hits, replay_covers_real = _attestation(pool, "unbiasedness.json")
    comparison = compare_versions(
        candidate.version,
        deployed.version,
        pool,
        config,
        store=pool_store,
        inputs=INPUTS,
        unbiasedness=UnbiasednessAttestation.load(attestation_path),
        history_root=history_root,
        comparison_dir=root / "comparisons",
    )
    deployed_hit_trees = [
        row["tree_id"] for row in comparison.per_tree if row["deployed_hits"] == ["slate"]
    ]
    step3 = {
        "policy_versions": {"deployed": deployed.version, "candidate": candidate.version},
        "deployed_is_repo_bootstrap": deployed.version == _bootstrap_version,
        "flat_policy_hits": len(deployed_hit_trees),  # 扁平首版策略命中的轮次树
        "parent_version": candidate.parent_version,
        "min_comparable_trees": config.min_comparable_trees,
        "cold_start_trees": cold_start_trees,
        "refused_on_cold_start": refusal,
        "comparable_trees": len(comparison.per_tree),
        "skipped_unfrozen_trees": skipped,
        "tau": unbiased.tau,
        "attestation": str(attestation_path.name),
        "replay_hits": sorted(set(hits)),
        "replay_covers_real": replay_covers_real,
        "generation_calls": generation_calls,
        "verdict": comparison.verdict,
        "mean_score": {key: round(value, 6) for key, value in comparison.mean_score.items()},
        "pareto_auc": {key: round(value, 6) for key, value in comparison.pareto_auc.items()},
        "per_evaluator": [item["evaluator_id"] for item in comparison.per_evaluator],
        "unknown_trees": len(comparison.unknown_trees),
        "comparison_id": comparison.comparison_id,
        "signal_sources_annotated": all(
            source["simulated"] is True and "非真实商业数据" in source["note"]
            for source in signal_sources_of(config)
        ),
        "note": comparison.note,
    }
    step3["ok"] = (
        cold_start_trees < config.min_comparable_trees  # 冷启动确实低于门槛
        and f"实测 {cold_start_trees}" in refusal  # 错误含实测树数
        and f"门槛 {config.min_comparable_trees}" in refusal  # 错误含门槛值
        and "可比对树数不足" in refusal
        and len(pool.trees) >= config.min_comparable_trees
        and len(comparison.per_tree) == len(pool.trees)
        and unbiased.verdict == "pass"
        and unbiased.tau is not None
        and unbiased.tau >= 0.95
        and step3["replay_covers_real"]  # 回放得分即历史得分（不重算、不生成）
        and step3["replay_hits"] == ["slate"]
        and comparison.verdict == "new_better"
        and len(step3["per_evaluator"]) == 4
        # 每棵树只承载**一个版本**的节点（一轮一个版本）→ 另一侧必然 UNKNOWN：
        # 报告如实呈现覆盖说明（不编造、不放宽匹配），与 009 演示同口径
        and len(comparison.unknown_trees) == len(pool.trees)
        and "UNKNOWN" in comparison.note
        and step3["signal_sources_annotated"]  # 报告面同带模拟源标注（SC-009）
        and step3["deployed_is_repo_bootstrap"]  # 部署侧 = 仓库首版策略（扁平计划）
        and step3["flat_policy_hits"] == 2  # 扁平计划在自身两轮树上均命中（旧行为下为 UNKNOWN）
    )
    report["steps"]["3_无偏性凭证与回放对比"] = step3
    # 步④~⑥ 复用本步的池与配置（同一闭环的证据面）
    report["_context"] = {
        "comparison": comparison,
        "pool_store": pool_store,
        "pool": pool,
        "config_path": config_path,
        "config": config,
        "history_root": history_root,
    }


def _step_4_采纳门禁与指针留痕(report) -> None:
    """步④：未采纳指针逐字节不变 → 采纳更新指针 + AdoptionRecord 留痕（SC-002）。"""
    import hashlib

    from agents.dev.adoption import adopt, deployed_version

    context = report["_context"]
    comparison = context["comparison"]
    config_path = context["config_path"]
    config_dir = config_path.parent
    comparison_dir = config_dir / "comparisons"
    adoption_dir = config_dir / "adoptions"

    before_text = config_path.read_text(encoding="utf-8")
    before_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    reject_record = adopt(
        comparison.comparison_id,
        "reject",
        "reviewer-a",
        "本周不采纳：等待更多历史树覆盖",
        config_path=config_path,
        comparison_dir=comparison_dir,
        adoption_dir=adoption_dir,
        history_root=context["history_root"],
    )
    after_reject_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    adopt_record = adopt(
        comparison.comparison_id,
        "adopt",
        "sunqi",
        "回放对比呈现优势且无偏性 τ 达标，采纳",
        config_path=config_path,
        comparison_dir=comparison_dir,
        adoption_dir=adoption_dir,
        history_root=context["history_root"],
    )
    step4 = {
        "pointer_before": reject_record.deployed_before,
        "pointer_on_reject": reject_record.deployed_after,
        "pointer_after_adopt": adopt_record.deployed_after,
        "yaml_bytes_unchanged_on_reject": after_reject_hash == before_hash,
        "yaml_changed_on_adopt": config_path.read_text(encoding="utf-8") != before_text,
        "reject_reason": reject_record.reason,
        "adopt_reason": adopt_record.reason,
        "records": [reject_record.record_path.name, adopt_record.record_path.name],
        "pointer_read_back": deployed_version(config_path),
    }
    step4["ok"] = (
        step4["pointer_before"] == comparison.deployed_version
        and step4["pointer_on_reject"] == comparison.deployed_version  # 未采纳指针不变
        and step4["pointer_after_adopt"] == comparison.new_version
        and step4["pointer_read_back"] == comparison.new_version
        and step4["yaml_bytes_unchanged_on_reject"]
        and step4["yaml_changed_on_adopt"]
        and len(step4["records"]) == 2  # 采纳与拒绝都留痕
    )
    report["steps"]["4_采纳门禁与指针留痕"] = step4


def _step_5_禁止自动进化三重机检(root, report, dream_config, gateway) -> None:
    """步⑤：dreaming 显式拒绝（0 候选 0 计费 0 落盘）+ 名单实值 + 审计位 + 门禁判定。"""
    from core.deployment.config import DeploymentConfig
    from core.replay.pool import SimulatorPool
    from core.tree.store import create_tree_store
    from dreaming.pipeline import AutoEvolutionForbiddenError, run_dream_round

    generator = _CountingGenerator()
    calls_before = gateway.call_count
    dreaming_root = root / "dreaming"
    error = ""
    try:
        run_dream_round(
            "dev",
            "class Policy:\n    pass\n",
            generator,
            SimulatorPool(create_tree_store(create_engine("sqlite+pysqlite:///:memory:"))),
            gateway,
            dream_config,
            history_root=dreaming_root,
            m=dream_config.demo_candidates,
        )
        error = "未拒绝（契约破坏）"
    except AutoEvolutionForbiddenError as exc:
        error = str(exc)
    deployment = DeploymentConfig.from_yaml(DEV_YAML)
    deployed_version = report["_context"]["comparison"].deployed_version
    meta_path = root / "policies" / "dev" / f"{deployed_version}.meta.json"
    meta_audit = json.loads(meta_path.read_text(encoding="utf-8"))
    step5 = {
        "error": error,
        "generator_calls": generator.calls,
        "llm_calls": gateway.call_count - calls_before,
        "round_files": len(list(dreaming_root.rglob("*.json"))) if dreaming_root.is_dir() else 0,
        "no_auto_evolve_agents": list(deployment.forbidden_agents),
        "forbidden": deployment.is_forbidden("dev"),
        "policy_meta_no_auto_evolve": meta_audit.get("no_auto_evolve"),
    }
    step5["ok"] = (
        step5["generator_calls"] == 0
        and step5["llm_calls"] == 0
        and step5["round_files"] == 0
        and "原则六" in step5["error"]
        and "dev" in step5["no_auto_evolve_agents"]
        and step5["forbidden"] is True
        and step5["policy_meta_no_auto_evolve"] is True
    )
    report["steps"]["5_禁止自动进化三重机检"] = step5


def _step_6_升级判据材料(root, report) -> None:
    """步⑥：全量阈值快照 + 逐项"实测值 / 无法评价（来源缺失）"+ 结论非达标 + 继续观察条件。"""
    from agents.dev.upgrade_evidence import (
        CONCLUSIONS,
        MISSING_SOURCES,
        THRESHOLD_KEYS,
        build_upgrade_evidence,
        continuation_conditions,
    )
    from core.degraded.evidence import MEASURED, MISSING_SOURCE

    context = report["_context"]
    store = context["pool_store"]
    nodes = [
        node
        for tree in store.trees_by(agent_id="dev")
        for node in store.nodes_of(tree.tree_id)
        if node.parent_id is not None
    ]
    data_dir = root / "upgrade-events"
    material = build_upgrade_evidence("2026-W38", context["config"], nodes=nodes, data_dir=data_dir)
    items = {item["key"]: item for item in material.items}
    pending = continuation_conditions(material.threshold_snapshot)
    step6 = {
        "period": material.period,
        "samples": len(nodes),
        "material": str((data_dir / "dev" / "2026-W38.json").relative_to(root)),
        "threshold_snapshot": material.threshold_snapshot,
        "value_forms": {key: item["status"] for key, item in items.items()},
        "measured": sorted(key for key, item in items.items() if item["status"] == MEASURED),
        "missing": {
            key: item["missing_reason"]
            for key, item in items.items()
            if item["status"] == MISSING_SOURCE
        },
        "conclusion": material.conclusion,
        "continuation_conditions": [entry["key"] for entry in pending],
        "signal_sources_annotated": all(
            source["simulated"] is True and "非真实商业数据" in source["note"]
            for source in material.raw["signal_sources"]
        ),
        "final_system_view": material.conclusion not in ("meets", "达标"),
    }
    step6["ok"] = (
        len(nodes) > 0
        and (data_dir / "dev" / "2026-W38.json").is_file()  # 按 agent 分目录
        and set(material.threshold_snapshot) >= set(THRESHOLD_KEYS)  # 阈值全量声明
        and all(item["status"] in (MEASURED, MISSING_SOURCE) for item in items.values())
        and all(
            item["value"] is not None
            if item["status"] == MEASURED
            else bool(str(item["missing_reason"]).strip())
            for item in items.values()
        )  # 无空白、无省略阈值项
        and set(step6["missing"])
        == {entry["key"] for entry in MISSING_SOURCES}
        == {"reliability", "drift"}
        and "010-weekly-calibration/spec.md:157" in step6["missing"]["reliability"]
        and "judge" in step6["missing"]["drift"]
        and material.conclusion in CONCLUSIONS
        and material.conclusion not in ("meets", "达标")  # 结论恒不为达标
        and step6["continuation_conditions"] == ["reliability", "drift"]
        and any("继续观察条件" in alert for alert in material.alerts)
        and step6["signal_sources_annotated"]  # SC-009 第三处标注
    )
    report["steps"]["6_升级判据材料"] = step6


def main() -> int:
    started = time.perf_counter()
    dream_config = _dream_config()
    policy = _bootstrap_policy()
    report: dict = {"agent_id": "dev", "steps": {}, "ok": False}

    with tempfile.TemporaryDirectory(prefix="cineflow-dev-demo-") as tmp:
        root = Path(tmp)
        config = DevConfig.from_yaml(DEV_YAML)
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        create_jobs_schema(engine)
        store = create_tree_store(engine)
        artifacts = LocalArtifactStore(root / "artifacts")
        gateway = LLMGateway(
            MockBackend(),
            price_book=config.model_prices,
            sleep=lambda _: None,
            spend_guard=None,  # 019：离线装配显式声明不接门禁（行为零变化）
        )
        report["policy_history"] = {
            "version": policy.policy_version,
            "policy_dir": str(HISTORY_ROOT / "dev"),
        }

        _step_1_立项组合产出落树与对账(store, artifacts, engine, gateway, config, policy, report)
        _step_2_结构组合门禁短路与重算(store, artifacts, engine, gateway, config, policy, report)
        # 步③~⑥ 用演示档形态（题材夹具档位按题材给出不同模拟源系数；只改取值、不改口径）
        _step_3_无偏性凭证与回放对比(root, report, dream_config, gateway)
        _step_4_采纳门禁与指针留痕(report)
        _step_5_禁止自动进化三重机检(root, report, dream_config, gateway)
        _step_6_升级判据材料(root, report)

    report.pop("_context", None)
    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
