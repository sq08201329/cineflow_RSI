#!/usr/bin/env python
"""真实渠道与账单对账 CLI（功能 019）：额度台账 / 最小规模校准 / 扩量。

**判定全在 core**（`core/billing/`），本脚本只做参数解析、调用与 JSON 打印（薄转发）。
子命令（命名与契约 C16 逐字一致；`runs` 属 US3，随后续阶段落地）：

```
uv run python ops/billing.py tiers     --channel <id> [--config configs/*.yaml]
uv run python ops/billing.py calibrate --channel <id> --tier <环节> --measured-usd <实测> \
        --sample-count <n> [--profile <档案 id> | --expected-usd <按价目折算>] [--cost-source <源>]
uv run python ops/billing.py import-bill --channel <id> --file <账单> --bill-id <批次> \
        --period <周期> [--currency USD] [--source export|api] [--config configs/*.yaml]
uv run python ops/billing.py reconcile --channel <id> --period <周期> \
        --gateway-report <网关账目 JSON> [--config configs/*.yaml]
uv run python ops/billing.py alert-check --channel <id> [--period <周期>] [--since <YYYY-MM-DD>] \
        [--config configs/*.yaml]
uv run python ops/billing.py raise-tier --channel <id> --tier <环节> --limit-usd <额> \
        --calibration <单轮校准 id> --by <人> --reason <理由> [--config configs/*.yaml]
```

- `tiers`：各档余量 / 拒绝计数 / **未结算预留**（崩溃残留如实列出，不自动清零）；
- `calibrate`：**只读既有记录、不联网、不构造后端、不新测花费**——"实测花费"由运营从
  厂商侧读出（`--measured-usd`，口径写在 `--cost-source`），"按价目折算"由档案价目 × token
  数算出；本阶段以显式输入落记录（US3 接上运行记录/账单侧的自动复述）；
- `import-bill`：账单导入（**不联网**：人工上传厂商导出文件）；格式/列映射由配置声明，
  缺声明即报错、**零落盘**；批次幂等（同批次重复导入拒绝）；
- `reconcile`：逐项对账并落报告 + 告警留痕；**无账单批次即拒绝产出**（不产"零差异"报告）；
  有告警 ⇒ 退出码 1；
- `alert-check`：告警门禁**只读入口**（报告的未解释项/告警 + `alerts.jsonl` 留痕证据）；
  有告警 ⇒ 退出码 1——供定时工作流非零退出即告警；
- `raise-tier`：六条先决条件全过才**定点改写**配置额度（`core/yaml_edit.py`，其余段与注释
  逐字节不变）+ 写 `calibrated_by` + `alerts.jsonl` 留痕；任一条件不满足即拒绝并留
  `uncalibrated_raise`（配置一字不改）。

退出码（契约 C16）：`0` 成功（`tiers`/`calibrate` 成功；`raise-tier` 改写成功）｜
`1` 执行失败或拒绝（预算拒绝、校准未过、扩量被拒）｜`2` 用法或配置错误。
凭证只报"是否设置 + 长度"，**绝不回显值**（沿用 `ops/smoke_llm.py` 口径）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.billing.bill import (  # noqa: E402 - 019：账单导入面（C2/C3）
    BillError,
    UnknownBillFormatError,
    load_bill,
    normalize_bill,
    save_bill,
)
from core.billing.budget import (  # noqa: E402 - 019：账本/档位/告警读取
    AlertLog,
    BudgetConfig,
    BudgetConfigError,
    alerts_path,
    channel_dir,
    ledger_path,
    sole_channel,
)
from core.billing.calibration import (  # noqa: E402 - 019：校准记录与扩量
    CalibrationRecordError,
    calibration_status,
    raise_tier,
    record_calibration,
)
from core.billing.reconcile import (  # noqa: E402 - 019：对账与报告（C13/C14）
    ReconciliationError,
    load_report,
    reconcile,
    report_path,
    save_report,
)

DEFAULT_CONFIG = "configs/movie.yaml"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _fail(message: str, code: int) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False, sort_keys=True))
    return code


def _load_config(path: str | Path) -> BudgetConfig:
    return BudgetConfig.from_yaml(path)


def _channel_of(cfg: BudgetConfig, requested: str) -> str:
    """渠道 id 必须与配置声明一致（码内零渠道字面量：不一致即用法错误）。"""
    declared = sole_channel(cfg)
    if requested and str(requested) != declared.channel_id:
        raise BudgetConfigError(
            f"渠道 {requested!r} 与配置声明的 {declared.channel_id!r} 不一致"
            "（渠道 id 由 budget.channels 声明，命令行取值须与配置一致）"
        )
    return declared.channel_id


def _read_tier_rows(cfg: BudgetConfig, channel_id: str) -> dict:
    """各档当前状态（档位定义 ∪ 账本记录）：余量、拒绝计数、**未结算预留**。"""
    ledger_file = ledger_path(cfg.ledger_root(), channel_id)
    payload = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
    records = payload.get("tiers", {})
    rows: list[dict] = []
    for tier_id, tier in sorted(cfg.tiers.items()):
        record = records.get(tier_id, {})
        spent = float(record.get("spent_usd", 0.0))
        reserved = float(record.get("reserved_usd", 0.0))
        rows.append(
            {
                "tier_id": tier_id,
                "limit_usd": tier.limit_usd,
                "window_kind": tier.window_kind,
                "window_key": record.get("window_key", []),
                "spent_usd": spent,
                "reserved_usd": reserved,
                "remaining_usd": tier.remaining_usd(spent_usd=spent, reserved_usd=reserved),
                "refusals": int(record.get("refusals", 0)),
                "last_refusal": record.get("last_refusal"),
                # 崩溃残留的未结算预留**如实列出**（含时刻与预估价），不自动清零
                "unsettled": [
                    {
                        "reservation_id": item.get("reservation_id", ""),
                        "at": item.get("at", ""),
                        "estimated_usd": float(item.get("estimated_usd", 0.0)),
                    }
                    for item in record.get("pending", [])
                ],
                "calibrated_by": tier.calibrated_by,
                "note": tier.note,
            }
        )
    return {
        "channel_id": channel_id,
        "ledger_path": str(ledger_file),
        "alerts_path": str(alerts_path(cfg.ledger_root(), channel_id)),
        "revision": int(payload.get("revision", 0)),
        "updated_at": payload.get("updated_at", ""),
        "tiers": rows,
        "notes": list(cfg.notes),
        "calibration_status": calibration_status(channel_id, cfg=cfg, root=cfg.ledger_root()),
    }


def _cmd_tiers(args) -> int:
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    print(
        json.dumps(_read_tier_rows(cfg, channel_id), ensure_ascii=False, indent=2, sort_keys=True)
    )
    return EXIT_OK


def _cmd_calibrate(args) -> int:
    """只读既有记录落校准记录：**不联网、不构造后端、不新测花费**（C12）。"""
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    cfg.tier(args.tier)  # 缺档即用法/配置错误（不发明档位）
    if args.expected_usd is None and (args.prompt_tokens is None or args.completion_tokens is None):
        return _fail(
            "需要给出按价目折算的金额：--expected-usd，或 "
            "--prompt-tokens/--completion-tokens（+ --profile）",
            EXIT_USAGE,
        )
    if args.measured_usd is None:
        return _fail(
            "需要给出实测花费 --measured-usd（厂商侧读数；本命令不联网取数、不新测花费）",
            EXIT_USAGE,
        )
    expected = (
        float(args.expected_usd)
        if args.expected_usd is not None
        else _expected_from_tokens_for(args, int(args.prompt_tokens), int(args.completion_tokens))
    )
    calibration_id = args.calibration_id or f"cal-{channel_id}-{args.tier}-{args.at or 'latest'}"
    try:
        record = record_calibration(
            calibration_id,
            cfg=cfg,
            channel_id=channel_id,
            tier_id=args.tier,
            prices_snapshot=_prices_snapshot(args),
            sample_count=int(args.sample_count),
            measured_cost_usd=float(args.measured_usd),
            expected_cost_usd=expected,
            root=cfg.ledger_root(),
            note=(
                "最小规模校准（口径：实测来源="
                f"{args.cost_source}；按价目折算由配置价目 × token 数得出）。"
                f"{args.note or ''}"
            ),
            cost_source=str(args.cost_source),
        )
    except CalibrationRecordError as exc:
        return _fail(f"校准记录落盘失败：{exc}", EXIT_FAILED)
    print(
        json.dumps(
            {
                "calibration_id": record.calibration_id,
                "channel_id": record.channel_id,
                "tier_id": record.tier_id,
                "deviation": record.deviation,
                "passed": record.passed,
                "reasons": list(record.reasons),
                "measured": dict(record.measured),
                "note": record.note,
                "credentials": _credential_report(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return EXIT_OK


def _prices_snapshot(args) -> dict:
    """当时价目快照（含 price_matrix 时逐格复述；缺档案则空表——校准记录如实标注）。"""
    from core.llm_gateway.profiles import DECLARED_DIMENSIONS, load_or_migrate

    payload = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    load = load_or_migrate(payload)
    return {
        profile_id: {
            "prices": dict(profile.prices),
            "price_matrix": {
                cell: dict(values) for cell, values in (profile.price_matrix or {}).items()
            },
            "declared_dimensions": (list(DECLARED_DIMENSIONS) if profile.price_matrix else []),
        }
        for profile_id, profile in load.profiles.items()
    }


def _expected_from_tokens_for(args, prompt_tokens: int, completion_tokens: int) -> float:
    """按配置档案价目折算（唯一折算算术在 core/llm_gateway）。"""
    from core.llm_gateway.profiles import cost_from_prices, load_or_migrate

    load = load_or_migrate(yaml.safe_load(Path(args.config).read_text(encoding="utf-8")))
    profile = (
        load.profile(args.profile)
        if args.profile
        else load.profile(load.routing.default_profile or next(iter(load.profiles)))
    )
    return cost_from_prices(
        profile.prices, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )


def _cmd_raise_tier(args) -> int:
    """扩量：六条先决全过才定点改写额度；拒绝时配置一字不改 + `uncalibrated_raise` 留痕。"""
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    try:
        report = raise_tier(
            channel_id,
            args.tier,
            float(args.limit_usd),
            calibration_id=args.calibration,
            by=args.by,
            reason=args.reason,
            cfg=cfg,
            config_path=args.config,
            root=cfg.ledger_root(),
        )
    except BudgetConfigError as exc:
        # 缺档/渠道不符/额度非法 = **用法或配置错误**（不发明档位、不猜渠道）
        return _fail(f"配置错误：{exc}", EXIT_USAGE)
    except CalibrationRecordError as exc:
        # 拒绝：配置未被改写（raise_tier 在改写之前就抛），留痕已写 `uncalibrated_raise`
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "uncalibrated_raise",
                    "error": str(exc),
                    "config_path": str(args.config),
                    "config_unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_FAILED
    print(
        json.dumps(
            {"ok": True, "reason": "tier_raised", **report},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return EXIT_OK


def _cmd_import_bill(args) -> int:
    """账单导入（**不联网**）：规范化 + 批次幂等落盘；任一行非法 ⇒ 整体拒绝、零落盘。"""
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    spec = cfg.channel(channel_id).bill
    path = Path(args.file)
    if not path.is_file():
        return _fail(f"账单文件不存在：{path}", EXIT_USAGE)
    try:
        bill = normalize_bill(
            path.read_text(encoding="utf-8"),
            channel_id=channel_id,
            bill_id=args.bill_id,
            period=args.period,
            currency=args.currency,
            source=args.source,
            fmt=spec.format_id,
            columns=spec.columns,
        )
    except UnknownBillFormatError as exc:
        return _fail(f"未注册的账单格式（零落盘）：{exc}", EXIT_FAILED)
    except BillError as exc:
        return _fail(f"账单导入被拒（整体拒绝、零落盘）：{exc}", EXIT_FAILED)
    try:
        target = save_bill(bill, root=cfg.ledger_root())
    except Exception as exc:  # noqa: BLE001 - 同批次已存在等：拒绝覆盖（append-only）
        return _fail(f"账单落盘被拒（批次幂等）：{exc}", EXIT_FAILED)
    print(
        json.dumps(
            {
                "channel_id": channel_id,
                "bill_id": bill.bill_id,
                "period": bill.period,
                "currency": bill.currency,
                "source": bill.source,
                "format": bill.format_id,
                "entries": len(bill.entries),
                "raw_ref": bill.raw_ref,
                "fetched_at": bill.fetched_at,
                "system_digest": bill.system_digest,
                "path": str(target),
                "network": "none",  # 本命令不联网（导出形态人工上传）
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return EXIT_OK


def _gateway_ledger_from(args) -> dict:
    """网关账目（C13 的输入之一）：由文件给出（网关内存账目不在产物面）。"""
    path = Path(args.gateway_report)
    if not path.is_file():
        raise ReconciliationError(f"网关账目文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _cmd_reconcile(args) -> int:
    """逐项对账：**无账单即拒绝产出**（不产"零差异"报告）；有告警 ⇒ 退出码 1。"""
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    try:
        bill_payload = load_bill(args.bill_id, channel_id=channel_id, root=cfg.ledger_root())
    except Exception as exc:  # noqa: BLE001 - 无账单/账单不可用 ⇒ 拒绝产出
        return _fail(
            f"无账单批次即拒绝产出（不产零差异报告）：{exc}",
            EXIT_FAILED,
        )
    from core.billing.bill import BillEntry, VendorBill

    bill = VendorBill(
        channel_id=bill_payload["channel_id"],
        bill_id=bill_payload["bill_id"],
        period=bill_payload["period"],
        currency=bill_payload["currency"],
        source=bill_payload["source"],
        raw_ref=bill_payload["raw_ref"],
        fetched_at=bill_payload["fetched_at"],
        format_id=bill_payload["format_id"],
        entries=tuple(BillEntry(**entry) for entry in bill_payload["entries"]),
        system_digest=bill_payload.get("system_digest", ""),
    )
    try:
        report = reconcile(
            args.period,
            channel_id=channel_id,
            gateway_ledger=_gateway_ledger_from(args),
            bill=bill,
            cfg=cfg,
        )
    except ReconciliationError as exc:
        return _fail(f"对账拒绝产出：{exc}", EXIT_FAILED)
    try:
        save_report(report, root=cfg.ledger_root())
    except Exception as exc:  # noqa: BLE001 - 同周期重产拒绝（append-only）
        return _fail(f"报告落盘被拒（同周期重产）：{exc}", EXIT_FAILED)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_FAILED if report.has_alerts() else EXIT_OK


def _cmd_alert_check(args) -> int:
    """告警门禁只读入口（C14）：报告未解释项/超阈值 ⇒ 退出码 1；`alerts.jsonl` 逐条列出。"""
    cfg = _load_config(args.config)
    channel_id = _channel_of(cfg, args.channel)
    directory = channel_dir(cfg.ledger_root(), channel_id) / "reports"
    periods = (
        [args.period] if args.period else sorted(path.stem for path in directory.glob("*.json"))
    )
    reports: list[dict] = []
    alerting: list[str] = []
    for period in periods:
        if not args.period:
            path = directory / f"{period}.json"
        else:
            path = report_path(cfg.ledger_root(), channel_id, period)
        if not path.is_file():
            if args.period:
                return _fail(f"报告不存在：{path}（先 reconcile 再 alert-check）", EXIT_USAGE)
            continue
        try:
            payload = load_report(period, channel_id=channel_id, root=cfg.ledger_root())
        except Exception as exc:  # noqa: BLE001 - 报告被改写/机检失败 ⇒ 有告警（不静默放行）
            alerting.append(f"{period}: 报告完整性校验失败（{exc}）")
            continue
        reports.append(payload)
        if payload.get("unexplained") or payload.get("alerts"):
            alerting.append(
                f"{period}: unexplained={len(payload.get('unexplained', []))} "
                f"alerts={list(payload.get('alerts', []))}"
            )
    entries = AlertLog(alerts_path(cfg.ledger_root(), channel_id)).entries()
    since = args.since or ""
    incremental = [entry for entry in entries if not since or str(entry.get("at", "")) >= since]
    gate_kinds = {"unexplained_delta", "delta_over_threshold", "budget_refused", "over_limit"}
    if since:
        new_gate = [entry for entry in incremental if entry.get("kind") in gate_kinds]
        if new_gate:
            alerting.append(f"since {since}：新增门禁类告警 {len(new_gate)} 条")
    payload = {
        "channel_id": channel_id,
        "periods_scanned": periods,
        "reports": [
            {
                "period": report.get("period"),
                "unexplained": list(report.get("unexplained", [])),
                "alerts": list(report.get("alerts", [])),
                "deviates": report.get("deviates"),
                "bill_refs": list(report.get("bill_refs", [])),
            }
            for report in reports
        ],
        "alerts_total": len(entries),
        "alerts_jsonl": str(alerts_path(cfg.ledger_root(), channel_id)),
        "since": since,
        "alerting": alerting,
        "has_alerts": bool(alerting),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_FAILED if alerting else EXIT_OK


def _credential_report() -> dict:
    """凭证只报"是否设置 + 长度"（**绝不回显值**，沿用 smoke 口径）。"""
    report: dict = {}
    for variable in sorted(name for name in os.environ if name.endswith(("_API_KEY", "_KEY"))):
        value = os.environ.get(variable, "")
        report[variable] = {"set": bool(value), "length": len(value)}
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops/billing.py",
        description="真实渠道与账单对账 CLI（功能 019；判定全在 core，脚本只做薄转发）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    tiers = sub.add_parser("tiers", help="各档余量 / 拒绝计数 / 未结算预留")
    tiers.add_argument("--channel", required=True, help="渠道 id（须与 budget.channels 一致）")
    tiers.add_argument("--config", default=DEFAULT_CONFIG, help="形态配置路径")
    tiers.set_defaults(handler=_cmd_tiers)

    calibrate = sub.add_parser("calibrate", help="落最小规模校准记录（只读既有记录，不联网）")
    calibrate.add_argument("--channel", required=True)
    calibrate.add_argument("--tier", required=True, help="环节 id（budget.tiers 的键）")
    calibrate.add_argument("--measured-usd", dest="measured_usd", type=float, default=None)
    calibrate.add_argument("--expected-usd", dest="expected_usd", type=float, default=None)
    calibrate.add_argument("--prompt-tokens", dest="prompt_tokens", type=int, default=None)
    calibrate.add_argument("--completion-tokens", dest="completion_tokens", type=int, default=None)
    calibrate.add_argument(
        "--profile", default=None, help="档案 id（按价目折算用；缺省取默认档案）"
    )
    calibrate.add_argument("--sample-count", dest="sample_count", type=int, required=True)
    calibrate.add_argument("--cost-source", dest="cost_source", default="operator_reported")
    calibrate.add_argument("--calibration-id", dest="calibration_id", default=None)
    calibrate.add_argument("--at", default=None, help="记录时刻（缺省=现在）")
    calibrate.add_argument("--note", default="")
    calibrate.add_argument("--config", default=DEFAULT_CONFIG, help="形态配置路径")
    calibrate.set_defaults(handler=_cmd_calibrate)

    raise_tier_parser = sub.add_parser("raise-tier", help="按合格校准记录扩量（定点改写额度）")
    raise_tier_parser.add_argument("--channel", required=True)
    raise_tier_parser.add_argument("--tier", required=True)
    raise_tier_parser.add_argument("--limit-usd", dest="limit_usd", type=float, required=True)
    raise_tier_parser.add_argument("--calibration", required=True, help="校准记录 id")
    raise_tier_parser.add_argument("--by", required=True, help="操作人（留痕必填）")
    raise_tier_parser.add_argument("--reason", required=True, help="理由（留痕必填）")
    raise_tier_parser.add_argument("--config", default=DEFAULT_CONFIG, help="形态配置路径")
    raise_tier_parser.set_defaults(handler=_cmd_raise_tier)

    import_bill = sub.add_parser("import-bill", help="账单导入（不联网；批次幂等、零部分导入）")
    import_bill.add_argument("--channel", required=True)
    import_bill.add_argument("--file", required=True, help="账单文件（厂商导出的人工上传副本）")
    import_bill.add_argument("--bill-id", dest="bill_id", required=True, help="批次标识（幂等键）")
    import_bill.add_argument("--period", required=True, help="账单周期（如 2026-09）")
    import_bill.add_argument("--currency", default="USD", help="记账币种（异币种按条目 fx 折算）")
    import_bill.add_argument("--source", default="export", choices=("export", "api"))
    import_bill.add_argument("--config", default=DEFAULT_CONFIG)
    import_bill.set_defaults(handler=_cmd_import_bill)

    reconcile_parser = sub.add_parser(
        "reconcile", help="逐项对账（无账单即拒绝产出；有告警退出码 1）"
    )
    reconcile_parser.add_argument("--channel", required=True)
    reconcile_parser.add_argument("--period", required=True)
    reconcile_parser.add_argument(
        "--bill-id", dest="bill_id", required=True, help="账单批次（先 import-bill）"
    )
    reconcile_parser.add_argument(
        "--gateway-report",
        dest="gateway_report",
        required=True,
        help="网关账目 JSON（cost_report() 形状；网关内存账目不在产物面，故以文件给出）",
    )
    reconcile_parser.add_argument("--config", default=DEFAULT_CONFIG)
    reconcile_parser.set_defaults(handler=_cmd_reconcile)

    alert_check = sub.add_parser("alert-check", help="告警门禁只读入口（有告警退出码 1）")
    alert_check.add_argument("--channel", required=True)
    alert_check.add_argument("--period", default=None, help="只查该周期（缺省查全部报告）")
    alert_check.add_argument("--since", default=None, help="只看该时刻之后的告警增量（ISO8601）")
    alert_check.add_argument("--config", default=DEFAULT_CONFIG)
    alert_check.set_defaults(handler=_cmd_alert_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except BudgetConfigError as exc:
        return _fail(f"配置错误：{exc}", EXIT_USAGE)
    except CalibrationRecordError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    except Exception as exc:  # noqa: BLE001 - CLI 边界：如实报错给运行方，不吞异常
        return _fail(f"未预期错误：{exc!r}", EXIT_FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
