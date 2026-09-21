#!/usr/bin/env python
"""端到端演示：策略部署评估自动化（功能 014 / T1426；quickstart 六步，退出码 0）。

六步与 quickstart.md 逐条对应（全部在临时目录 + configs 副本上跑，不触仓库工作树）：

1. **门槛判定矩阵**：全满足 / 单要件不满足 / 证据缺失 / 禁止名单 → 判定与理由齐全；
2. **影子模式**：判定照跑、**指针不变**（机检）→ 产对照报告（差异分类 + 误入率口径）；
3. **影子门禁**：影子期未满即申请 auto → 拒绝并注明缺口；
4. **自动部署**：影子期达标开 auto + eligible → 指针更新 + 证据快照 + 部署事件（source=auto）；
5. **渐进抽检 + 否决回滚**：前 5 次全量复核；否决 → **三件事同时生效**（回滚 + manual + 重标定）；
6. **误入率可重算**：从事件留痕重算 == 报告值（SC-007）。

确定性：固定时钟（`at` 显式传入）+ 临时目录 + 配置副本；退出码 0 = 六步全部成立。
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SHADOW_START = "2026-09-21T00:00:00+00:00"
SHADOW_END = "2026-10-05T00:00:00+00:00"  # +14 天（影子期时长下限）
PERIOD = "2026-W39"
AGENT = "visual"
PREVIOUS = "dep-000"
CANDIDATE = "cand-auto-001"
JUDGE = "judge.cinematic@1.0.0"


def _step(index: int, title: str) -> None:
    print(f"\n=== 第 {index} 步：{title} ===")


def _evidence(candidate, deployed, registry, *, reward_candidate=0.62, reward_deployed=0.55):
    return {
        "deployed_version": deployed,
        "unbiasedness": {"verdict": "pass", "tau": 0.82, "threshold": 0.6},
        "reward_compare": {
            "candidate": reward_candidate,
            "deployed": reward_deployed,
            "source": "replay/pools/pool-visual-2026W39.json",
        },
        "validation_rewards": {candidate: 0.8, deployed: 0.5, "sibling": 0.4},
        "judge_keys": (JUDGE,),
        "drift_registry": registry,  # 012 状态登记（空登记 = normal，无登记即允许）
    }


def main() -> int:
    from core.calibration.drift_status import DriftRegistry
    from core.deployment import mode, shadow, spot_check
    from core.deployment.auto_deploy import (
        deploy_events,
        read_pointer,
        select_period_candidate,
    )
    from core.deployment.config import DeploymentConfig
    from core.deployment.errors import ModeTransitionError
    from core.deployment.evidence import collect_evidence
    from core.deployment.gate import gate
    from core.yaml_edit import upsert_section_entries

    with tempfile.TemporaryDirectory(prefix="cineflow-deploy-demo-") as tmp:
        base = Path(tmp)
        config = base / "movie.yaml"
        shutil.copyfile(REPO_ROOT / "configs" / "movie.yaml", config)
        config.write_text(
            upsert_section_entries(
                config.read_text(encoding="utf-8"),
                ("deployment", AGENT),
                {"current_policy_version": PREVIOUS},
            ),
            encoding="utf-8",
        )
        data_dir = base / "deployment"
        for sub in ("mode", "evidence", "shadow", "deploys", "spot_checks", "rollbacks"):
            (data_dir / sub).mkdir(parents=True, exist_ok=True)
        history_root = base / "policies" / "history"
        for version in (PREVIOUS, CANDIDATE):
            target = history_root / AGENT / f"{version}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                'class Policy:\n    """演示工件"""\n\n    def solve(self, env, budget):\n'
                '        return ""\n',
                encoding="utf-8",
            )

        cfg = DeploymentConfig.from_yaml(config)
        registry = DriftRegistry.load(base / "calibration")  # 空登记 = 全部 normal

        # ---- 1. 门槛判定矩阵 ------------------------------------------------
        _step(1, "门槛判定矩阵（缺证据即拦截 / 禁止名单优先）")
        matrix = {
            "全满足": dict(
                agent_id=AGENT,
                candidate_version="cand-ok",
                **_evidence("cand-ok", PREVIOUS, registry),
            ),
            "reward 未超过现部署": dict(
                agent_id=AGENT,
                candidate_version="cand-flat",
                **_evidence("cand-flat", PREVIOUS, registry, reward_candidate=0.55),
            ),
            "无池化回放结果": {
                "agent_id": AGENT,
                "candidate_version": "cand-noevi",
                **_evidence("cand-noevi", PREVIOUS, registry),
                "reward_compare": None,
            },
            "禁止名单（screenplay）": {
                "agent_id": "screenplay",
                "candidate_version": "cand-scr",
                **_evidence("cand-scr", PREVIOUS, registry),
            },
        }
        decisions = {}
        for label, kwargs in matrix.items():
            bundle = collect_evidence(cfg=cfg, **kwargs)
            verdict = gate(bundle, cfg)
            decisions[label] = verdict.decision.value
            print(f"  {label:<22} → {verdict.decision.value:<20} {verdict.reason[:60]}…")
        assert decisions == {
            "全满足": "eligible",
            "reward 未超过现部署": "blocked",
            "无池化回放结果": "insufficient_evidence",
            "禁止名单（screenplay）": "forbidden_agent",
        }, decisions

        # ---- 2. 影子模式（判定照跑、指针不动） -------------------------------
        _step(2, "影子模式：判定照跑 + 影子事件 + 对照报告（指针不变）")
        mode.set_mode(
            "shadow",
            by="ops",
            reason="开启影子期（上线前置）",
            cfg=cfg,
            data_dir=data_dir,
            at=SHADOW_START,
        )
        pointer_before = config.read_bytes()
        from core.deployment.auto_deploy import evaluate_candidate

        first = evaluate_candidate(
            AGENT,
            "cand-shadow-pass",
            cfg=cfg,
            data_dir=data_dir,
            config_path=config,
            period=PERIOD,
            human_decision="reject",
            at=SHADOW_START,
            **_evidence("cand-shadow-pass", PREVIOUS, registry),
        )
        second = evaluate_candidate(
            AGENT,
            "cand-shadow-blocked",
            cfg=cfg,
            data_dir=data_dir,
            config_path=config,
            period=PERIOD,
            human_decision="adopt",
            at=SHADOW_START,
            **_evidence("cand-shadow-blocked", PREVIOUS, registry, reward_candidate=0.5),
        )
        assert first.action.value == "shadow_recorded"
        assert second.action.value == "shadow_recorded"
        assert read_pointer(config, AGENT) == PREVIOUS
        assert config.read_bytes() == pointer_before  # 影子期指针逐字节不变（机检）
        assert not list((data_dir / "deploys").glob("*.json"))
        report = shadow.build_shadow_report(
            PERIOD, cfg, data_dir=data_dir, agent_id=AGENT, at=SHADOW_START
        )
        print(
            f"  影子事件：放行 {report.passes} / 拦截 {report.blocks}"
            f"（对照 {report.candidate_count} 个候选）"
        )
        print(f"  差异分类：{json.dumps(report.diff_counts, ensure_ascii=False)}")
        print(f"  拦截理由分布：{json.dumps(report.reason_distribution, ensure_ascii=False)}")
        print(f"  指针：{read_pointer(config, AGENT)}（未变）")
        assert report.diff_counts["sys_pass_human_reject"] == 1  # 门槛太松的直接信号
        assert report.diff_counts["human_pass_sys_block"] == 1  # 拦截候选也留痕才看得到

        # ---- 3. 影子门禁：未满即开 auto → 拒绝 ------------------------------
        _step(3, "影子门禁：影子期未满申请 auto → 拒绝并注明缺口")
        try:
            mode.set_mode(
                "auto",
                by="ops",
                reason="影子期未满即申请",
                cfg=cfg,
                data_dir=data_dir,
                at=SHADOW_START,
            )
            raise AssertionError("影子期未满竟然允许开 auto")
        except ModeTransitionError as exc:
            print(f"  拒绝：{exc}")
        state = mode.load_mode_state(data_dir)
        assert state.current.value == "shadow"
        assert state.shadow_candidate_count == 2

        # ---- 4. 影子期达标 → auto 部署 --------------------------------------
        _step(4, "影子期达标 → auto：eligible 候选自动接班（指针 + 快照 + 留痕）")
        for _ in range(20 - state.shadow_candidate_count):
            mode.record_shadow_candidate(data_dir, at=SHADOW_START)
        mode.set_mode(
            "auto",
            by="ops",
            reason="影子期达标（14 天 / 20 候选）",
            cfg=cfg,
            data_dir=data_dir,
            at=SHADOW_END,
        )
        selection = select_period_candidate(
            [{"version": CANDIDATE, "reward": 0.66}, {"version": "cand-rival", "reward": 0.61}],
            agent_id=AGENT,
            period="2026-W41",
            data_dir=data_dir,
            at=SHADOW_END,
        )
        outcome = evaluate_candidate(
            AGENT,
            CANDIDATE,
            cfg=cfg,
            data_dir=data_dir,
            config_path=config,
            period="2026-W41",
            at=SHADOW_END,
            **_evidence(CANDIDATE, PREVIOUS, registry),
        )
        assert outcome.action.value == "deployed"
        assert read_pointer(config, AGENT) == CANDIDATE
        ledger = deploy_events(data_dir, AGENT)
        print(
            f"  同周期择一（按 reward）：{selection['selected']}"
            f"（其余 {len(selection['rejected'])} 个如实记录）"
        )
        print(
            f"  部署事件：{ledger[0]['_path']}（source={ledger[0]['source']}，"
            f"{ledger[0]['pointer_before']} → {ledger[0]['pointer_after']}）"
        )
        print(f"  证据快照：{outcome.snapshot_path.name}")
        assert ledger[0]["source"] == "auto"

        # ---- 5. 渐进抽检 + 否决回滚（三件事） -------------------------------
        _step(5, "渐进抽检 + 否决回滚：前 N 次全量复核 → 三件事同时生效")
        task = spot_check.open_spot_check(
            ledger[0]["_path"], data_dir=data_dir, cfg=cfg, at=SHADOW_END
        )
        assert task is not None and task.trigger.value == "first_n"
        rollback = spot_check.veto_and_rollback(
            spot_check.record_path_from(task, data_dir),
            by="reviewer",
            reason="抽检否决：产出质量不达线",
            data_dir=data_dir,
            cfg=cfg,
            config_path=config,
            history_root=history_root,
            at=SHADOW_END,
        )
        state = mode.load_mode_state(data_dir)
        # 三件事机检：①指针回滚 ②模式回 manual ③重标定标记
        assert read_pointer(config, AGENT) == PREVIOUS
        assert state.current.value == "manual"
        assert state.recalibration_required is True
        print(f"  抽检任务：seq={task.seq} 触发={task.trigger.value}（前 5 次全量复核）")
        print(
            f"  三件事：①指针 {rollback.from_version} → {rollback.to_version} "
            f"②模式 → {rollback.mode_after.value} ③重标定标记={rollback.recalibration_required}"
        )
        print(f"  回滚留痕：{spot_check.rollback_events(data_dir, AGENT)[0]['_path']}")

        # ---- 6. 误入率可重算 ------------------------------------------------
        _step(6, "误入率可重算：从事件留痕重算 == 报告值（SC-007）")
        recomputed = shadow.recompute_misadmission_rate(data_dir, PERIOD, agent_id=AGENT)
        assert recomputed["numerator"] == report.misadmission_numerator
        assert recomputed["denominator"] == report.misadmission_denominator
        assert recomputed["rate"] == report.misadmission_rate
        print(
            f"  报告值：分子 {report.misadmission_numerator} / 分母 "
            f"{report.misadmission_denominator} = {report.misadmission_rate}"
        )
        print(
            f"  重算值：分子 {recomputed['numerator']} / 分母 "
            f"{recomputed['denominator']} = {recomputed['rate']}"
        )
        print(
            "  口径：分子 = 会放行但人工拒绝 ∪ 放行样本中被判不可接受（按候选去重）；"
            "分母 = 影子放行数"
        )

    print(
        "\n六步全部成立：判定矩阵可机检 / 影子指针不变 / 影子门禁拒绝 / 自动部署留痕完整 / "
        "否决三件事同时生效 / 误入率可重算。"
    )
    print("说明：真实 2 周影子期的运行属运营（本演示只证明机制、计时与对照报告成立）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
