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

其余四步属 US3，按序追加 `_step_3.._step_6` 即可（报告结构已按步分列，`steps` 键名带序号）：

  ③ 无偏性凭证与回放对比（含最小池门槛拒绝分支）④ 采纳/拒绝与部署指针留痕
  ⑤ 禁止自动进化拒绝语义（三重机检）⑥ 升级判据材料（来源缺失逐项标注）

断言：两步全 ok=true，退出码 0；生产切换仅装配层替换（PG/S3/真实 LLM 网关），代码路径不变
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


def _bootstrap_policy():
    """仓库引导树人工策略：版本取 `policies/history/dev/` 首个已版本化版本（人工提交产物）。"""
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


def main() -> int:
    started = time.perf_counter()
    config = DevConfig.from_yaml(DEV_YAML)
    policy = _bootstrap_policy()
    report: dict = {"agent_id": "dev", "steps": {}, "ok": False}

    with tempfile.TemporaryDirectory(prefix="cineflow-dev-demo-") as tmp:
        root = Path(tmp)
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        create_jobs_schema(engine)
        store = create_tree_store(engine)
        artifacts = LocalArtifactStore(root / "artifacts")
        gateway = LLMGateway(MockBackend(), price_book=config.model_prices, sleep=lambda _: None)
        report["policy_history"] = {
            "version": policy.policy_version,
            "policy_dir": str(HISTORY_ROOT / "dev"),
        }

        _step_1_立项组合产出落树与对账(store, artifacts, engine, gateway, config, policy, report)
        _step_2_结构组合门禁短路与重算(store, artifacts, engine, gateway, config, policy, report)
        # 步③~⑥（无偏性凭证与回放对比 / 采纳留痕 / 三重机检 / 判据材料）属 US3，按序追加。

    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
