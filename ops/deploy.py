#!/usr/bin/env python
"""部署自动化 CLI（功能 014）：mode / evaluate / shadow-report / spot-check / veto / assess-drift。

- `mode`：读写部署模式状态（`--show` 只读；`--set manual|shadow|auto` 需 `--by/--reason`，
  门禁（manual→auto 直连、影子期双下限、重标定阻断）在 core 内判定，本 CLI 只转发结果）；
- `evaluate`：调唯一入口 `evaluate_candidate`（模式从 `deployment/mode.json` 读真实状态）——
  证据由命令行注入（`--unbiasedness` / `--reward-compare` / `--validation` / `--judges`，
  缺什么就按缺失判拦截，不推测放行）；
- `shadow-report`：产出/读取影子对照报告（含误入率口径与机检重算）。

风格对齐 ops/calibrate.py：argparse 子命令 + JSON 输出 + 退出码（0 成功 / 1 执行失败 /
2 用法错误）。
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CONFIG = "configs/movie.yaml"
DEFAULT_DATA_DIR = "deployment"


def _pointer_version(config_path: str | Path, agent_id: str) -> str | None:
    """读部署指针（与 dreaming.deploy_hook.current_policy_version 同口径：缺指针即 None）。"""
    import yaml

    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return (payload.get("deployment") or {}).get(agent_id, {}).get("current_policy_version")


class _UsageError(Exception):
    """命令行用法/证据文件错误（退出码 2；与"执行失败"退出码 1 区分）。"""


def _load_json(path: str | None, *, what: str) -> dict | None:
    """读证据 JSON；非法即用法错误（不静默当作"无证据"——错误与缺失必须可区分）。"""
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _UsageError(f"{what} 读取失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise _UsageError(f"{what} 必须为 JSON 对象")
    return payload


def _cmd_mode(args) -> int:
    from core.deployment import mode
    from core.deployment.config import DeploymentConfig

    cfg = DeploymentConfig.from_yaml(args.config)
    if args.set_mode:
        if not args.by or not args.reason:
            print(
                json.dumps(
                    {"error": "--set 必须带 --by 与 --reason（模式变更留痕，FR-004）"},
                    ensure_ascii=False,
                )
            )
            return 2
        try:
            state = mode.set_mode(
                args.set_mode,
                by=args.by,
                reason=args.reason,
                cfg=cfg,
                data_dir=args.data_dir,
            )
        except Exception as exc:  # noqa: BLE001 - 门禁拒绝如实回报（含缺口说明）
            print(json.dumps({"error": str(exc), "mode": args.set_mode}, ensure_ascii=False))
            return 1
    else:
        state = mode.load_mode_state(args.data_dir, mode_default=cfg.mode_default)
    print(
        json.dumps(
            {
                "current": state.current.value,
                "shadow_days_accumulated": state.shadow_days_accumulated,
                "shadow_candidate_count": state.shadow_candidate_count,
                "recalibration_required": state.recalibration_required,
                "gap": state.with_elapsed_shadow(datetime.now(UTC).isoformat()).shadow_window_gap(
                    min_days=cfg.shadow.min_days, min_candidates=cfg.shadow.min_candidates
                ),
                "state_path": str(mode.mode_path(args.data_dir)),
                "history": [dict(entry) for entry in state.history],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_evaluate(args) -> int:
    from core.deployment.auto_deploy import evaluate_candidate
    from core.deployment.config import DeploymentConfig

    cfg = DeploymentConfig.from_yaml(args.config)
    try:
        unbiasedness = _load_json(args.unbiasedness, what="无偏性验收结论") or None
        validation_payload = _load_json(args.validation, what="validation 池 reward")
    except _UsageError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    judges = tuple(item for item in (args.judges or "").split(",") if item)

    drift_registry = None
    if args.drift_dir:
        from core.calibration.drift_status import DriftRegistry

        drift_registry = DriftRegistry.load(args.drift_dir)

    reward_candidate = args.reward_candidate
    reward_deployed = args.reward_deployed
    reward_compare = None
    if reward_candidate is not None and reward_deployed is not None:
        from core.deployment.evidence import reward_compare_payload

        reward_compare = reward_compare_payload(
            reward_candidate,
            reward_deployed,
            source=args.reward_source or "ops/deploy evaluate（池化回放对比产物引用未给出）",
        )

    deployed_version = args.deployed_version or _pointer_version(args.config, args.agent)
    try:
        outcome = evaluate_candidate(
            args.agent,
            args.candidate,
            cfg=cfg,
            data_dir=args.data_dir,
            deployed_version=deployed_version,
            unbiasedness=unbiasedness,
            reward_compare=reward_compare,
            validation_rewards=validation_payload,
            judge_keys=judges,
            drift_registry=drift_registry,
            period=args.period,
            config_path=args.config,
        )
    except Exception as exc:  # noqa: BLE001 - 未落地的部署路径如实报错（不伪装成功）
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_shadow_report(args) -> int:
    from core.deployment import shadow
    from core.deployment.config import DeploymentConfig

    cfg = DeploymentConfig.from_yaml(args.config)
    try:
        report = shadow.build_shadow_report(
            args.period,
            cfg,
            data_dir=args.data_dir,
            agent_id=args.agent,
        )
    except Exception as exc:  # noqa: BLE001 - 无留痕/只增不改冲突如实报错
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    recomputed = shadow.recompute_misadmission_rate(args.data_dir, args.period, agent_id=args.agent)
    print(
        json.dumps(
            {
                "report": report.to_dict(),
                "recomputed_misadmission": recomputed,
                "report_path": str(shadow.report_path(args.data_dir, args.period)),
                "events_path": str(shadow.events_path(args.data_dir)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_spot_check(args) -> int:
    from core.deployment import spot_check
    from core.deployment.config import DeploymentConfig

    cfg = DeploymentConfig.from_yaml(args.config)
    if args.list_pending:
        pending = spot_check.pending_spot_checks(args.data_dir, agent_id=args.agent)
        # 阈值：--pending-alert-days 显式给则覆盖，缺省取配置（口径与判定同一处解析）
        alert_days = spot_check.resolve_pending_alert_days(
            max_age_days=args.pending_alert_days, cfg=cfg
        )
        stale = spot_check.stale_pending_checks(
            args.data_dir,
            agent_id=args.agent,
            max_age_days=alert_days,
        )
        print(
            json.dumps(
                {
                    "pending": pending,
                    "pending_count": len(pending),
                    "pending_alert_days": alert_days,
                    "stale": stale,
                    "alert": (
                        f"有 {len(stale)} 个抽检任务超过 {alert_days:g} 天未复核"
                        "（只告警，不自动视为通过）"
                        if stale
                        else ""
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.deploy_event:
        print(json.dumps({"error": "--deploy-event 或 --list 二选一"}, ensure_ascii=False))
        return 2
    try:
        record = spot_check.open_spot_check(args.deploy_event, data_dir=args.data_dir, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - 无源任务/留痕冲突如实报错
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    if record is None:
        print(
            json.dumps(
                {
                    "task": None,
                    "note": "本次部署未抽中（渐进策略：前 first_n 次全量，之后按比例）",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "task": record.to_dict(),
                "record_path": str(spot_check.record_path_from(record, args.data_dir)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_veto(args) -> int:
    from core.deployment import spot_check
    from core.deployment.config import DeploymentConfig

    cfg = DeploymentConfig.from_yaml(args.config)
    try:
        event = spot_check.veto_and_rollback(
            args.record,
            by=args.by,
            reason=args.reason,
            data_dir=args.data_dir,
            cfg=cfg,
            config_path=args.config,
            history_root=args.history_root,
        )
    except Exception as exc:  # noqa: BLE001 - 失败路径必须如实暴露（含状态已达成的部分）
        print(json.dumps({"error": str(exc), "alert": True}, ensure_ascii=False))
        return 1
    print(json.dumps({"rollback": event.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def _cmd_assess_drift(args) -> int:
    from core.calibration.drift_models import DriftState
    from core.calibration.drift_status import DriftRegistry
    from core.deployment import spot_check

    registry = DriftRegistry.load(args.drift_dir)
    statuses = [
        status
        for key, status in registry.current().items()
        if (args.evaluator_key is None or key == args.evaluator_key)
        and status.status in (DriftState.SUSPECT, DriftState.CONFIRMED_DRIFT)
    ]
    if not statuses:
        print(
            json.dumps(
                {
                    "assessments": [],
                    "note": "无 suspect/confirmed_drift 状态：无需回滚评估（不制造噪音证据）",
                    "pointer_unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    records = []
    for status in statuses:
        try:
            event = spot_check.record_drift_assessment(
                args.agent, status, data_dir=args.data_dir, config_path=args.config
            )
        except Exception as exc:  # noqa: BLE001 - 无部署留痕/无指针如实报错
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 1
        if event is not None:
            records.append(event.to_dict())
    print(
        json.dumps(
            {
                "assessments": records,
                "note": "回滚评估记录：不自动回滚（指针未变），处置权在人工（012 流程）",
                "pointer_unchanged": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="策略部署：模式/评估/影子报告/抽检/回滚/漂移评估")
    sub = parser.add_subparsers(dest="command", required=True)

    mode_parser = sub.add_parser("mode", help="查看/切换部署模式（门禁在 core 内判定）")
    mode_parser.add_argument(
        "--set", dest="set_mode", default=None, choices=["manual", "shadow", "auto"]
    )
    mode_parser.add_argument("--by", default=None, help="操作人（切换必填，留痕）")
    mode_parser.add_argument("--reason", default=None, help="切换理由（切换必填，留痕）")
    mode_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    mode_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    mode_parser.set_defaults(func=_cmd_mode)

    evaluate_parser = sub.add_parser("evaluate", help="评估候选（唯一入口：manual/shadow/auto）")
    evaluate_parser.add_argument("--agent", required=True, help="Agent ID")
    evaluate_parser.add_argument("--candidate", required=True, help="候选策略版本")
    evaluate_parser.add_argument(
        "--deployed-version", default=None, help="现部署版本（默认读部署指针）"
    )
    evaluate_parser.add_argument("--unbiasedness", default=None, help="无偏性验收结论 JSON 路径")
    evaluate_parser.add_argument(
        "--validation", default=None, help="validation 池 reward JSON 路径（005 口径）"
    )
    evaluate_parser.add_argument("--judges", default=None, help="相关 judge 版本，逗号分隔")
    evaluate_parser.add_argument("--drift-dir", default=None, help="012 漂移状态登记数据目录")
    evaluate_parser.add_argument("--reward-candidate", type=float, default=None)
    evaluate_parser.add_argument("--reward-deployed", type=float, default=None)
    evaluate_parser.add_argument("--reward-source", default=None, help="池化回放对比产物引用")
    evaluate_parser.add_argument("--period", default=None, help="影子期周期标签（默认 ISO 周）")
    evaluate_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    evaluate_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    evaluate_parser.set_defaults(func=_cmd_evaluate)

    report_parser = sub.add_parser("shadow-report", help="产出/重算影子对照报告（含误入率）")
    report_parser.add_argument("--period", required=True, help="周期标签（如 2026-W39）")
    report_parser.add_argument("--agent", required=True, help="Agent ID（报告按 Agent 分档）")
    report_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    report_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    report_parser.set_defaults(func=_cmd_shadow_report)

    spot_parser = sub.add_parser("spot-check", help="渐进抽检：产复核任务 / 列待复核与逾期待复核")
    spot_parser.add_argument("--deploy-event", default=None, help="部署事件留痕路径（产任务）")
    spot_parser.add_argument("--agent", default=None, help="限定 Agent（--list 用）")
    spot_parser.add_argument(
        "--list", dest="list_pending", action="store_true", help="列出待复核任务与逾期告警"
    )
    spot_parser.add_argument(
        "--pending-alert-days",
        type=float,
        default=None,
        help=(
            "超过 N 天未复核即告警（只告警，不自动通过）；缺省取配置 "
            "deployment.spot_check.pending_alert_days（运营节奏即形态）"
        ),
    )
    spot_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    spot_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    spot_parser.set_defaults(func=_cmd_spot_check)

    veto_parser = sub.add_parser("veto", help="抽检否决 → 三件事同时生效（回滚 + manual + 重标定）")
    veto_parser.add_argument("--record", required=True, help="抽检任务留痕路径")
    veto_parser.add_argument("--by", required=True, help="复核人（留痕必填）")
    veto_parser.add_argument("--reason", required=True, help="否决理由（留痕必填）")
    veto_parser.add_argument(
        "--history-root", default="policies/history", help="策略工件根（回滚目标存在性检查）"
    )
    veto_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    veto_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    veto_parser.set_defaults(func=_cmd_veto)

    drift_parser = sub.add_parser(
        "assess-drift", help="部署后漂移的回滚评估（记录落盘，不自动回滚）"
    )
    drift_parser.add_argument("--agent", required=True, help="Agent ID")
    drift_parser.add_argument(
        "--drift-dir", default=str(REPO_ROOT / "calibration"), help="012 漂移状态登记数据目录"
    )
    drift_parser.add_argument("--evaluator-key", default=None, help="限定 evaluator_id@version")
    drift_parser.add_argument("--config", default=str(REPO_ROOT / DEFAULT_CONFIG))
    drift_parser.add_argument("--data-dir", default=str(REPO_ROOT / DEFAULT_DATA_DIR))
    drift_parser.set_defaults(func=_cmd_assess_drift)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
