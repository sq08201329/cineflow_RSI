#!/usr/bin/env python
"""端到端演示：剧本 Agent 降级模式闭环（quickstart.md 六步，功能 009 收官验证）。

演示（确定性夹具 + Mock 网关 + SQLite 内存库 + movie.yaml 副本，全程离线无需凭证；
**演示档把页数窗口缩到夹具可行域**（target 3 页 / 3 行每页），形态参数口径不变）：

  1. **分阶段产出**：题材输入 + 人工策略 → outline/scenes/script 三阶段落树，
     成本按网关价目入账，树内 == 运营表 + judge 计费增量（三方对账）
  2. **七评估器与 gate 短路**：合法工件七分量齐全且正分；缺关键节拍 → gate 判 0 且
     judge 零调用（省 LLM 成本）；同结构重算逐位一致（定点归一）
  3. **人工改策略**：无偏性验收（回放历史得分 vs 落盘工件真实重跑，附结论凭证）→
     提交新策略版本（修正指称写法）→ 回放对比报告（逐树/分项/pareto_auc/UNKNOWN）
  4. **采纳门禁**：未采纳指针逐字节不变；采纳后指针更新 + AdoptionRecord 落盘
  5. **禁止自动进化**：`run_dream_round(agent_id="screenplay")` 显式拒绝
     （AutoEvolutionForbiddenError，0 候选 0 计费 0 落盘——宪章原则六）
  6. **升级判据**：阈值快照 + 原始数值 + 系统结论（带内漂移 + 达标台账 → meets；
     缺台账 → below 并如实标注"不得据此升级"）

断言：六步全 ok=true，退出码 0；生产切换仅装配层替换（PG/S3/真实 LLM 网关），
代码路径不变（同 004/006/007/008 演示纪律）。
"""

import copy
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from agents.screenplay.adoption import adopt  # noqa: E402
from agents.screenplay.config import ScreenplayConfig  # noqa: E402
from agents.screenplay.db import create_jobs_schema  # noqa: E402
from agents.screenplay.evaluators import build_screenplay_evaluators  # noqa: E402
from agents.screenplay.evaluators.composite import evaluate_screenplay  # noqa: E402
from agents.screenplay.loop import run_screenplay_round  # noqa: E402
from agents.screenplay.policy_versions import submit_policy  # noqa: E402
from agents.screenplay.sandbox_compare import (  # noqa: E402
    UnbiasednessAttestation,
    compare_versions,
)
from agents.screenplay.upgrade_evidence import (  # noqa: E402
    build_upgrade_evidence,
    gate_violation_rate_of,
)
from core.calibration.config import CalibrationConfig  # noqa: E402
from core.evaluators.base import ArtifactRef  # noqa: E402
from core.llm_gateway.backends.mock import MockBackend  # noqa: E402
from core.llm_gateway.gateway import LLMGateway  # noqa: E402
from core.replay.pool import PoolError, SimulatorPool  # noqa: E402
from core.replay.unbiasedness import verify_unbiasedness  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.config import DreamConfig  # noqa: E402
from dreaming.pipeline import AutoEvolutionForbiddenError, run_dream_round  # noqa: E402

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
INPUTS = {
    "topic": "病房里的三个月",
    "target_duration_min": 3,
    "constraints": ["单场景为主"],
    "characters": ["林静", "陈默", "周医生"],
}
_SCENES = (
    ("scene-1", "内景", "病房", "夜", 0, ("林静", "陈默", "周医生")),
    ("scene-2", "内景", "走廊", "夜", 30, ("林静", "周医生")),
    ("scene-3", "外景", "天台", "清晨", 75, ("陈默", "林静")),
)
_EMOTIONS = ("tense", "sorrow", "calm", "awe", "joyful")


def _plans(config: ScreenplayConfig, *, variant: str) -> dict:
    """分阶段计划（演示夹具）：3 场景 9 行，节拍取配置 required 项。

    variant="deployed"：s1-l1 的归属角色写成"小静"（未登记、与"林静"同源的另一种
    写法）→ 实体一致性代理扣分；variant="new"：修正为登记写法 → 代理满分。
    三阶段共用同一结构化标记（演示档口径；判据与工件 schema 一致）。
    """
    beats = [
        {
            "beat_id": beat["beat_id"],
            "act": beat["act"],
            "required": beat["required"],
            "description": beat["description"],
        }
        for beat in config.beat_sheet
        if beat["required"]
    ]
    scenes, lines = [], []
    for index, (scene_id, prefix, location, time_desc, marker, cast) in enumerate(_SCENES):
        scenes.append(
            {
                "scene_id": scene_id,
                "heading": " - ".join((prefix, location, time_desc)),
                "location": location,
                "time_marker": marker,
                "characters": list(cast),
                "axis_base": "A" if index % 2 == 0 else "B",
            }
        )
        for offset in range(3):
            name = cast[offset % len(cast)]
            is_dialogue = offset != 1
            speaker = name if is_dialogue else None
            if variant == "deployed" and scene_id == "scene-1" and offset == 0:
                speaker = "小静"  # 演示缺陷：同名异写（新版本修正为 林静）
            lines.append(
                {
                    "line_id": f"s{index + 1}-l{offset + 1}",
                    "scene_id": scene_id,
                    "kind": "dialogue" if is_dialogue else "action",
                    "text": (
                        f"{name}把话说完：{INPUTS['topic']}还没结束。"
                        if is_dialogue
                        else f"{location}里的动作点：{name}转身。"
                    ),
                    "character": speaker,
                    "key": offset == 0,
                    "emotion": _EMOTIONS[(index + offset) % len(_EMOTIONS)],
                }
            )
    markers = {
        "beats": beats,
        "scenes": scenes,
        "characters": [
            {"name": "林静", "aliases": ["阿静"]},
            {"name": "陈默", "aliases": ["默哥"]},
            {"name": "周医生", "aliases": []},
        ],
        "lines": lines,
    }
    return {
        "outline": copy.deepcopy(markers),
        "scenes": copy.deepcopy(markers),
        "script": copy.deepcopy(markers),
    }


def _policy_source(plans: dict) -> str:
    """人工策略源码（静态检查必过）：三阶段计划内联，plan(inputs, config) 原样返回。

    源码即版本（版本 = 源码 BLAKE3 前 12 位）：产出执行器与回放对比都执行同一份源码，
    故结构键必然一致（回放命中）——演示不引入第二套计划口径。
    """
    return (
        "class Policy:\n"
        '    """演示档人工剧本策略（结构计划内联；三阶段各自结构标记）。"""\n'
        f"    PLANS = {plans!r}\n\n"
        "    def plan(self, inputs, config):\n"
        "        return self.PLANS\n"
    )


def _policy_object(source: str, version: str):
    """实例化策略源码并绑定版本（产出与回放共用同一份源码）。"""
    namespace: dict = {"__name__": "screenplay_demo_policy"}
    exec(compile(source, "<demo-policy>", "exec"), namespace)  # noqa: S102 - 演示档自有源码
    policy = namespace["Policy"]()
    policy.policy_version = version
    return policy


class _CountingBackend:
    """确定性后端 + 调用计数（judge 计费审计）。"""

    def __init__(self) -> None:
        self.call_count = 0
        self._inner = MockBackend()

    def complete(self, prompt, *, model, temperature, max_tokens):
        self.call_count += 1
        return self._inner.complete(
            prompt, model=model, temperature=temperature, max_tokens=max_tokens
        )


class _CountingGenerator:
    """候选生成计数桩：命中拒绝名单时必须恒 0（宪章原则六审计）。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, champion_source, digest, m):
        self.calls += 1
        return []


def _config_copy(root: Path, *, deployed_version: str) -> tuple[Path, ScreenplayConfig]:
    """movie.yaml 副本：页数窗口按演示工件收窄（3 页 × 3 行/页）+ 部署指针。"""
    raw = copy.deepcopy(yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8")))
    raw["screenplay"].update({"target_duration_min": 3, "page_tolerance": 0, "lines_per_page": 3})
    raw["deployment"] = {"screenplay": {"current_policy_version": deployed_version}}
    path = root / "movie.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, ScreenplayConfig.from_dict(raw)


def _pool_of(store):
    pool = SimulatorPool(store)
    skipped = 0
    for tree in store.trees_by(agent_id="screenplay"):
        try:
            pool.add_tree(tree)
        except PoolError:
            skipped += 1
    return pool, skipped


def _artifact_of(artifact_hash: str, artifacts):
    """按哈希读回工件（演示辅助；生产由装配层注入工件存储）。"""
    from agents.screenplay.artifact import ScriptArtifact

    return ScriptArtifact.from_dict(json.loads(artifacts.get(artifact_hash)))


def _rerun_score(node, artifacts, config: ScreenplayConfig, gateway) -> float:
    """真实重跑：读落盘工件 → 真实七评估器重算 → 合成定点（网关缓存命中下确定性）。"""
    artifact = _artifact_of(node.artifact_hash, artifacts)
    assembly = build_screenplay_evaluators(config, gateway)
    reference = ArtifactRef(artifact_hash=node.artifact_hash, metadata={"stage": artifact.stage})
    context = {
        "artifact": artifact,
        "stage": artifact.stage,
        "inputs": INPUTS,
        "markers": None,
        "config": config,
    }
    _, score, _ = evaluate_screenplay(assembly, reference, context, config.evaluator_weights)
    return score


def main() -> int:
    started = time.perf_counter()
    dream_config = DreamConfig.from_yaml(MOVIE_YAML)
    calibration = CalibrationConfig.from_yaml(MOVIE_YAML)
    report: dict = {"agent_id": "screenplay", "steps": {}, "ok": False}

    with tempfile.TemporaryDirectory(prefix="cineflow-screenplay-demo-") as tmp:
        root = Path(tmp)
        history_root = root / "policies"
        # ① 两版人工策略先落策略历史（版本 = 源码 BLAKE3 前 12 位）；策略源码内联计划，
        #    产出执行器与回放对比执行同一份源码（结构键一致 → 回放命中）
        config_path, config = _config_copy(root, deployed_version="pending")
        deployed_plans = _plans(config, variant="deployed")
        new_plans = _plans(config, variant="new")
        deployed_source = _policy_source(deployed_plans)
        new_source = _policy_source(new_plans)
        deployed_version = submit_policy(
            deployed_source, "sunqi", dream_config, history_root=history_root
        ).version
        new_version = submit_policy(
            new_source, "sunqi", dream_config, history_root=history_root
        ).version
        config_path, config = _config_copy(root, deployed_version=deployed_version)
        report["policy_versions"] = {"deployed": deployed_version, "new": new_version}
        deployed_policy = _policy_object(deployed_source, deployed_version)
        new_policy = _policy_object(new_source, new_version)

        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_schema(engine)
        create_jobs_schema(engine)
        store = create_tree_store(engine)
        artifacts = LocalArtifactStore(root / "artifacts")
        backend = _CountingBackend()
        gateway = LLMGateway(backend, price_book=config.model_prices, sleep=lambda _: None)

        # ---- 步骤 1：分阶段产出（三阶段落树 + 成本入账 + 对账）----
        result = run_screenplay_round(
            round_id="demo-r1",
            policy=deployed_policy,
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=INPUTS,
            evaluators=None,  # 默认装配真实七评估器（T923 接线形态）
        )
        nodes = _stage_nodes(store, result.tree_id)
        step1 = {
            "jobs": [job["status"] for job in result.jobs],
            "scores": {stage: node.score for stage, node in nodes.items()},
            "spent_usd": round(result.spent_usd, 6),
            "reconciliation": result.cost_reconciliation,
            "artifact_hashes": [job["artifact_hash"] for job in result.jobs],
        }
        step1["ok"] = (
            step1["jobs"] == ["inserted"] * 3
            and result.spent_usd > 0
            and result.cost_reconciliation["consistent"]
            and all(node is not None for node in step1["artifact_hashes"])
        )
        report["steps"]["1_分阶段产出落树对账"] = step1

        # ---- 步骤 2：七评估器 + gate 短路 + 重算一致 ----
        outline = nodes["outline"]
        calls_before = backend.call_count
        broken_plans = copy.deepcopy(deployed_plans)
        for markers in broken_plans.values():
            markers["beats"] = [beat for beat in markers["beats"] if beat["beat_id"] != "climax"]
        broken = run_screenplay_round(
            round_id="demo-r2",
            policy=_policy_object(_policy_source(broken_plans), version="demo-broken"),
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=INPUTS,
            evaluators=None,
        )
        broken_nodes = _stage_nodes(store, broken.tree_id)
        judge_calls_on_gate_violation = backend.call_count - calls_before - 3
        rerun = _rerun_score(outline, artifacts, config, gateway)
        step2 = {
            "components": sorted({key.split("@")[0] for key in outline.eval_breakdown}),
            "judge_calls_on_gate_violation": judge_calls_on_gate_violation,
            "broken_scores": {stage: node.score for stage, node in broken_nodes.items()},
            "recompute_matches": rerun == outline.score,
        }
        step2["ok"] = (
            len(step2["components"]) == 7  # 四 gate + 两 proxy + judge
            and step2["judge_calls_on_gate_violation"] == 0  # gate 短路不跑 judge
            and set(step2["broken_scores"].values()) == {0.0}
            and step2["recompute_matches"]  # 定点归一后重算逐位一致（SC-004）
        )
        report["steps"]["2_七评估器gate短路重算"] = step2

        # ---- 步骤 3：无偏性验收凭证 + 提交新策略 + 回放对比报告 ----
        new_round = run_screenplay_round(
            round_id="demo-r3",
            policy=new_policy,
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=INPUTS,
            evaluators=None,
        )
        pool, skipped = _pool_of(store)
        recorded = [
            node
            for tree in pool.trees
            for node in store.nodes_of(tree.tree_id)
            if node.parent_id is not None
        ]
        replay_scores = [node.score for node in recorded]  # 回放侧 = 历史得分
        rerun_scores = [_rerun_score(node, artifacts, config, gateway) for node in recorded]
        unbiased = verify_unbiasedness(replay_scores, rerun_scores, threshold=0.95)
        attestation_path = root / "unbiasedness.json"
        attestation_path.write_text(
            json.dumps(unbiased.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        attestation = UnbiasednessAttestation.load(attestation_path)
        comparison = compare_versions(
            new_version,
            deployed_version,
            pool,
            config,
            store=store,
            inputs=INPUTS,
            unbiasedness=attestation,
            history_root=history_root,
            comparison_dir=root / "comparisons",
        )
        step3 = {
            "tau": unbiased.tau,
            "records": len(recorded),
            "new_round_jobs": [job["status"] for job in new_round.jobs],
            "verdict": comparison.verdict,
            "mean_score": {key: round(value, 6) for key, value in comparison.mean_score.items()},
            "pareto_auc": {key: round(value, 6) for key, value in comparison.pareto_auc.items()},
            "per_evaluator": [item["evaluator_id"] for item in comparison.per_evaluator],
            "unknown_trees": len(comparison.unknown_trees),
            "comparison_id": comparison.comparison_id,
            "report": str((root / "comparisons" / f"{comparison.comparison_id}.json").name),
            "skipped_unfrozen_trees": skipped,
            "note": comparison.note,
        }
        step3["ok"] = (
            unbiased.verdict == "pass"
            and unbiased.tau >= 0.95
            and step3["new_round_jobs"] == ["inserted"] * 3
            and comparison.verdict == "new_better"
            and len(step3["per_evaluator"]) >= 3
        )
        report["steps"]["3_无偏性凭证与回放对比"] = step3

        # ---- 步骤 4：采纳门禁（未采纳指针不变 → 采纳更新 + 留痕）----
        import hashlib

        before_text = config_path.read_text(encoding="utf-8")
        before_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        reject_record = adopt(
            comparison.comparison_id,
            "reject",
            "reviewer-a",
            "本周不采纳：等待更多历史树覆盖",
            config_path=config_path,
            comparison_dir=root / "comparisons",
            adoption_dir=root / "adoptions",
            history_root=history_root,
        )
        after_reject_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        adopt_record = adopt(
            comparison.comparison_id,
            "adopt",
            "sunqi",
            "回放对比呈现优势且无偏性 τ 达标，采纳",
            config_path=config_path,
            comparison_dir=root / "comparisons",
            adoption_dir=root / "adoptions",
            history_root=history_root,
        )
        step4 = {
            "pointer_before": reject_record.deployed_before,
            "pointer_on_reject": reject_record.deployed_after,
            "pointer_after_adopt": adopt_record.deployed_after,
            "yaml_bytes_unchanged_on_reject": after_reject_hash == before_hash,
            "reject_reason": reject_record.reason,
            "adopt_reason": adopt_record.reason,
            "records": [
                str(reject_record.record_path.name),
                str(adopt_record.record_path.name),
            ],
            "yaml_changed_on_adopt": config_path.read_text(encoding="utf-8") != before_text,
        }
        step4["ok"] = (
            step4["pointer_before"] == deployed_version
            and step4["pointer_on_reject"] == deployed_version  # 未采纳指针不变（SC-002）
            and step4["pointer_after_adopt"] == new_version
            and step4["yaml_bytes_unchanged_on_reject"]
            and step4["yaml_changed_on_adopt"]
        )
        report["steps"]["4_采纳门禁与留痕"] = step4

        # ---- 步骤 5：禁止自动进化（宪章原则六）----
        generator = _CountingGenerator()
        calls_before = backend.call_count
        step5 = {"error": "", "generator_calls": 0, "llm_calls": 0}
        try:
            run_dream_round(
                "screenplay",
                deployed_source,
                generator,
                SimulatorPool(store),
                gateway,
                dream_config,
                history_root=root / "dreaming",
                m=dream_config.demo_candidates,
            )
            step5["error"] = "未拒绝（契约破坏）"
        except AutoEvolutionForbiddenError as exc:
            step5["error"] = str(exc)
        step5["generator_calls"] = generator.calls
        step5["llm_calls"] = backend.call_count - calls_before
        step5["round_files"] = (
            len(list((root / "dreaming").rglob("*.json"))) if (root / "dreaming").is_dir() else 0
        )
        step5["ok"] = (
            step5["generator_calls"] == 0
            and step5["llm_calls"] == 0
            and step5["round_files"] == 0
            and "原则六" in step5["error"]
        )
        report["steps"]["5_禁止自动进化拒绝语义"] = step5

        # ---- 步骤 6：升级判据材料（meets 与 below 两路径如实呈现）----
        events_dir = root / "upgrade-events"
        # meets 路径：合法产出轮次（无门禁违规）+ 达标台账 + 带内漂移
        clean_rate = gate_violation_rate_of(list(nodes.values()))
        # below 路径：含 gate 短路轮次的全量记录（门禁违规率超限）+ 无台账 + 漂移未测量
        period_rate = gate_violation_rate_of(recorded)
        meets = build_upgrade_evidence(
            "2026-W38",
            config,
            {"samples": 8, "kendall_tau": 0.72},
            calibration,
            drift=0.05,
            gate_violation_rate=clean_rate,
            human_anchor_count=5,
            data_dir=events_dir,
        )
        below = build_upgrade_evidence(
            "2026-W39",
            config,
            None,  # 无 010 台账记录 → 样本不足如实标注
            calibration,
            drift=None,  # 漂移未测量（F7 不在本特性）
            gate_violation_rate=period_rate,
            human_anchor_count=0,
            data_dir=events_dir,
        )
        step6 = {
            "meets": {
                "conclusion": meets.conclusion,
                "threshold_snapshot": meets.threshold_snapshot,
                "raw": {
                    key: meets.raw[key]
                    for key in (
                        "judge_correlation",
                        "judge_samples",
                        "drift",
                        "gate_violation_rate",
                    )
                },
            },
            "below": {"conclusion": below.conclusion, "reasons": below.reasons},
            "gate_violation_rate": {"clean_round": clean_rate, "all_recorded": period_rate},
            "materials": sorted(path.name for path in events_dir.glob("*.json")),
        }
        step6["ok"] = (
            meets.conclusion == "meets"
            and meets.system_digest
            and below.conclusion == "below"
            and any("不得据此升级" in reason for reason in below.reasons)
            and len(step6["materials"]) == 2
        )
        report["steps"]["6_升级判据材料"] = step6

    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


def _stage_nodes(store, tree_id) -> dict:
    return {
        node.observation_context["stage"]: node
        for node in store.nodes_of(tree_id)
        if node.parent_id is not None
    }


if __name__ == "__main__":
    sys.exit(main())
