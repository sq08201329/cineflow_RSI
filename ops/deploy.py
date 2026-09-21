#!/usr/bin/env python
"""部署自动化 CLI（功能 014）：mode / evaluate / shadow-report 子命令。

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


def main() -> int:
    parser = argparse.ArgumentParser(description="策略部署：模式/评估/影子对照报告（功能 014）")
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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
