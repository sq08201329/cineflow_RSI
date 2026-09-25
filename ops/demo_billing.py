#!/usr/bin/env python
"""端到端演示：真实渠道与账单对账（quickstart.md 六步，功能 019 —— US2/US3 交付）。

**全程离线**：Mock 网关 + 夹具账单 + 临时目录账本根，**零真实调用、零外部网络、零凭证**。
六步（`steps` 键名带序号，镜像 `ops/demo_dev_loop.py` 的演示纪律）：

  ① **额度声明与缺项拒绝**：档位/峰谷/校准/对账/账本/运行窗口全量声明 ⇒ 装配通过；
     删掉 `budget` 段 ⇒ 装配期拒绝（不取码内默认）；超限调用 ⇒ **调用前拒绝**：
     后端 0 次调用、网关三量不变、账本 `refusals` 计数 + `alerts.jsonl` 留痕
  ② **最小规模校准记录**：一次真实调用（Mock 后端）→ 运行记录（`source=real`）→
     `calibrate --from-records` 复述"样本量 + 按价目折算"落校准记录（含价目快照与峰谷快照、
     口径备注）；同键重产拒绝（append-only）
  ③ **未校准扩量拒绝留痕**：无记录/无效记录 ⇒ `raise-tier` 拒绝且**配置一字不改** +
     `uncalibrated_raise` 留痕；用②的合格记录 ⇒ 定点改写额度 + `calibrated_by` + `tier_raised`
  ④ **夹具账单导入 + 对账分类**：六类差异分类齐备、未分类项 ⇒ 未解释 ⇒ 告警落
     `alerts.jsonl`；重复批次被拒；未识别格式报错且零落盘；`reconcile`/`alert-check` 退出码 1
  ⑤ **跨进程账本并发共享额度**：两进程抢同一档 ⇒ 总入账 ≤ 额度、`revision` 单调（无丢失更新）
  ⑥ **两维价目四格 + 改价不漂移 + ≥7 天窗口**：峰/谷 × 命中/未命中四格取价正确；
     改配置后历史冻结快照复算**逐字节不变**；运行记录窗口（覆盖 ∧ 连续）：连续夹具通过、
     散点夹具（8 天真实但缺 2 天）**不通过**且断档如实报出（不插值）

退出码：0 = 六步全 ok；1 = 有任一步未通过。判定全在 core，本脚本只编排与打印。
"""

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"

# 夹具账单：**六类差异齐备** + 折扣与免费额度 + 异币种 + **取值域外行**（⇒ 未分类 ⇒ 告警）
BILL_HEADER = (
    "entry_id,amount,currency,period,line_kind,amount_sign,model_ref,fx_rate,fx_source,fx_at,note"
)
BILL_ROWS = [
    'b-1,2.00,USD,2026-09,usage,charge,deepseek-flash,,,,"与网关记账一致"',
    'b-2,0.05,USD,2026-09,discount,credit,deepseek-flash,,,,"折扣：不得静默按 0 记账"',
    'b-3,0.30,USD,2026-09,usage,charge,deepseek-flash-fast,,,,"判决档"',
    'b-4,0.04,EUR,2026-09,usage,charge,deepseek-flash,1.25,ecb,2026-09-30T00:00:00+00:00,"异币种"',
    'b-5,0.07,USD,2026-09,timing_shift,charge,deepseek-flash,,,,"上期调账：留待下期"',
    'b-6,0.06,USD,2026-09,unbilled,charge,deepseek-flash-fast,,,,"账单侧新增行 ⇒ 未入账"',
    'b-7,0.01,USD,2026-09,fx_adjustment,charge,deepseek-flash,,,,"汇率调整项"',
    'b-8,0.07,USD,2026-09,unsettled,charge,deepseek-flash,,,,"账期未到：留待下期"',
    'b-9,0.03,USD,2026-09,mystery,charge,deepseek-flash,,,,"取值域外 ⇒ 未分类 ⇒ 告警"',
]
GATEWAY_REPORT = {
    "by_profile": {
        "deepseek-flash": {"calls": 3, "cost_usd": 2.00},
        "deepseek-flash-fast": {"calls": 1, "cost_usd": 0.30},
        # 账单侧无该档案的计费线 ⇒ 未入账（网关记账不得自证）
        "local-qwen": {"calls": 1, "cost_usd": 0.25},
    },
    "total_usd": 2.55,
    "accounting_note": "演示夹具：网关折算记账值 ≠ 厂商账单",
}
# 两维价目（**演示夹具**：真实配置的价目取值属运营给定，本特性不发明厂商数字）
DEMO_MATRIX = {
    "peak_miss": {"prompt_per_1k": 0.0010, "completion_per_1k": 0.0020},
    "peak_hit": {"prompt_per_1k": 0.0006, "completion_per_1k": 0.0012},
    "off_peak_miss": {"prompt_per_1k": 0.0008, "completion_per_1k": 0.0016},
    "off_peak_hit": {"prompt_per_1k": 0.0004, "completion_per_1k": 0.0008},
}


def _budget_config_text(root: Path, *, tier_limit_usd: float = 5.0) -> str:
    """演示档形态配置：真实 movie.yaml 的派生（账本根落临时目录；额度按演示量级）。

    样本量下限压到 1：演示只跑**一次**最小规模调用，真实配置的 `min_samples: 3` 由
    运行记录逐日积累满足（本脚本不伪造记录凑数）。
    """
    payload = yaml.safe_load(MOVIE_YAML.read_text(encoding="utf-8"))
    payload["budget"]["ledger"]["root"] = str(root / "billing")
    for channel in payload["budget"]["channels"].values():
        for tier in channel["tiers"].values():
            tier["limit_usd"] = tier_limit_usd
    payload["budget"]["calibration"]["min_samples"] = 1
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _write_config(root: Path, text: str, name: str = "movie.yaml") -> Path:
    target = root / "configs" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _cli(argv: list[str]) -> tuple[int, dict]:
    """调用 CLI（薄转发；退出码与 JSON 输出即判定面的一份证据）。"""
    import importlib

    module = importlib.import_module("ops.billing")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main(argv)
    text = buffer.getvalue().strip()
    try:
        payload = json.loads(text.splitlines()[-1] if text.startswith("usage") else text)
    except json.JSONDecodeError:
        payload = {"_raw": text}
    return code, payload


def _step_1_额度声明与缺项拒绝(root: Path, config_path: Path, report: dict) -> None:
    from core.billing.budget import (
        BudgetConfig,
        BudgetConfigError,
        FileLedger,
        assemble_guard,
        ledger_path,
        tiers_of,
    )
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway

    assembly = assemble_guard(config_path)
    declared = tiers_of(assembly.cfg, assembly.channel_id)
    stated = {
        "tiers": sorted(declared),
        "peak_windows": assembly.cfg.peak_windows.to_snapshot(),
        "tier_limits": {tier_id: tier.limit_usd for tier_id, tier in declared.items()},
    }
    # 缺项即拒绝：删掉 budget 段
    broken = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    broken.pop("budget")
    broken_path = _write_config(root, yaml.safe_dump(broken, allow_unicode=True), "broken.yaml")
    try:
        BudgetConfig.from_yaml(broken_path)
        missing_refused = False
    except BudgetConfigError:
        missing_refused = True

    # 超限 ⇒ 调用前拒绝：把该档额度压到"任何预估额都超"
    tiny = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    tiny["budget"]["channels"][assembly.channel_id]["tiers"]["screenplay"]["limit_usd"] = 1e-6
    tiny_path = _write_config(root, yaml.safe_dump(tiny, allow_unicode=True), "tiny.yaml")
    guard = assemble_guard(tiny_path)
    backend = MockBackend()
    gateway = LLMGateway(
        backend,
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        spend_guard=guard.guard,
        channel_id=guard.channel_id,
        peak_windows=guard.peak_windows,
    )
    refused_reason = ""
    try:
        gateway.chat("演示：超限调用", model="mock-copy-v1", stage="screenplay")
    except Exception as exc:  # noqa: BLE001 - 拒绝是预期结果
        refused_reason = getattr(exc, "reason", type(exc).__name__)
    ledger = FileLedger(ledger_path(assembly.root, assembly.channel_id), timeout_seconds=1.0).read()
    record = (ledger.get("tiers") or {}).get("screenplay", {})
    report["steps"]["1_额度声明与缺项拒绝"] = {
        "ok": bool(
            missing_refused
            and refused_reason == "over_limit"
            and backend.call_count == 0
            and gateway.call_count == 0
            and gateway.total_cost_usd == 0.0
            and record.get("refusals") == 1
        ),
        "declared_tiers": stated["tiers"],
        "peak_windows": stated["peak_windows"],
        "missing_budget_refused": missing_refused,
        "refusal_reason": refused_reason,
        "backend_calls": backend.call_count,
        "gateway_call_count": gateway.call_count,
        "gateway_total_usd": gateway.total_cost_usd,
        "ledger_refusals": record.get("refusals"),
        "ledger_last_refusal": record.get("last_refusal"),
        "alerts": [entry["kind"] for entry in guard.alerts.entries()],
    }


def _step_2_最小规模校准记录(root: Path, config_path: Path, report: dict) -> None:
    from core.billing.budget import assemble_guard
    from core.billing.calibration import load_calibration
    from core.billing.runlog import RecordingGateway, load_run, run_path
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway

    assembly = assemble_guard(config_path)
    gateway = LLMGateway(
        MockBackend(),
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        spend_guard=assembly.guard,
        channel_id=assembly.channel_id,
        peak_windows=assembly.peak_windows,
    )
    # 装配面同源的运行记录包装：一次调用 = 一条 entry（source=real）
    recorder = RecordingGateway(
        gateway,
        cfg=assembly.cfg,
        root=assembly.root,
        channel_id=assembly.channel_id,
        source="real",
        adapter_ref=assembly.cfg.channel(assembly.channel_id).adapter,
    )
    result = recorder.chat("演示：最小规模单轮", model="mock-copy-v1", stage="screenplay")
    from datetime import UTC, datetime

    date = assembly.cfg.local_date(datetime.now(UTC))
    entries = load_run(date, channel_id=assembly.channel_id, root=assembly.root)["entries"]
    code, payload = _cli(
        [
            "calibrate",
            "--channel",
            assembly.channel_id,
            "--tier",
            "screenplay",
            "--config",
            str(config_path),
            "--from-records",
            "--measured-usd",
            str(round(result.cost_usd, 8)),
            "--cost-source",
            "gateway_accounting",
            "--calibration-id",
            "cal-demo-1",
        ]
    )
    stored = load_calibration("cal-demo-1", channel_id=assembly.channel_id, root=assembly.root)
    duplicate_refused = False
    try:
        _cli(
            [
                "calibrate",
                "--channel",
                assembly.channel_id,
                "--tier",
                "screenplay",
                "--config",
                str(config_path),
                "--expected-usd",
                "1.0",
                "--measured-usd",
                "1.0",
                "--sample-count",
                "1",
                "--calibration-id",
                "cal-demo-1",
            ]
        )
    except Exception:  # noqa: BLE001 - 同键重产
        duplicate_refused = True
    if not duplicate_refused:
        duplicate_refused = (
            _cli(
                [
                    "calibrate",
                    "--channel",
                    assembly.channel_id,
                    "--tier",
                    "screenplay",
                    "--config",
                    str(config_path),
                    "--expected-usd",
                    "1.0",
                    "--measured-usd",
                    "1.0",
                    "--sample-count",
                    "1",
                    "--calibration-id",
                    "cal-demo-1",
                ]
            )[0]
            == 1
        )
    report["steps"]["2_最小规模校准记录"] = {
        "ok": bool(
            code == 0
            and entries
            and entries[-1]["stage"] == "screenplay"
            and entries[-1]["source"] == "real"
            and payload.get("passed") is True
            and stored["prices_snapshot"]["peak_windows_snapshot"]["attribution"] == "call_start"
            and duplicate_refused
        ),
        "run_entries": len(entries),
        "run_entry_last": entries[-1],
        "run_path": str(run_path(assembly.root, assembly.channel_id, date).relative_to(root)),
        "calibration_id": payload.get("calibration_id"),
        "deviation": payload.get("deviation"),
        "passed": payload.get("passed"),
        "sample_count": payload.get("measured", {}).get("sample_count"),
        "cost_source": payload.get("measured", {}).get("cost_source"),
        "note": payload.get("note"),
        "duplicate_calibration_refused": duplicate_refused,
    }


def _step_3_未校准扩量拒绝留痕(root: Path, config_path: Path, report: dict) -> None:
    from core.billing.budget import AlertLog, alerts_path, assemble_guard

    assembly = assemble_guard(config_path)
    before = config_path.read_text(encoding="utf-8")
    code, payload = _cli(
        [
            "raise-tier",
            "--channel",
            assembly.channel_id,
            "--tier",
            "screenplay",
            "--limit-usd",
            "9.0",
            "--calibration",
            "no-such-record",
            "--by",
            "运营",
            "--reason",
            "演示：未校准即扩量",
            "--config",
            str(config_path),
        ]
    )
    refused_unchanged = config_path.read_text(encoding="utf-8") == before
    # 用②的合格记录再试 ⇒ 定点改写 + calibrated_by + tier_raised
    ok_code, ok_payload = _cli(
        [
            "raise-tier",
            "--channel",
            assembly.channel_id,
            "--tier",
            "screenplay",
            "--limit-usd",
            "9.0",
            "--calibration",
            "cal-demo-1",
            "--by",
            "运营",
            "--reason",
            "演示：最小规模校准合格后扩量",
            "--config",
            str(config_path),
        ]
    )
    rewritten = yaml.safe_load(config_path.read_text(encoding="utf-8"))["budget"]["channels"][
        assembly.channel_id
    ]["tiers"]["screenplay"]
    kinds = [
        entry["kind"]
        for entry in AlertLog(alerts_path(assembly.root, assembly.channel_id)).entries()
    ]
    config_path.write_text(before, encoding="utf-8")  # 演示不留副作用
    report["steps"]["3_未校准扩量拒绝留痕"] = {
        "ok": bool(
            code == 1
            and payload.get("reason") == "uncalibrated_raise"
            and payload.get("config_unchanged") is True
            and refused_unchanged
            and ok_code == 0
            and rewritten["limit_usd"] == 9.0
            and rewritten["calibrated_by"] == "cal-demo-1"
            and "uncalibrated_raise" in kinds
            and "tier_raised" in kinds
        ),
        "refusal_exit_code": code,
        "refusal_reason": payload.get("reason"),
        "config_unchanged": refused_unchanged,
        "raised_exit_code": ok_code,
        "new_limit_usd": rewritten["limit_usd"],
        "calibrated_by": rewritten["calibrated_by"],
        "alerts": kinds,
    }


def _step_4_夹具账单导入与对账分类(root: Path, config_path: Path, report: dict) -> None:
    from core.billing.budget import AlertLog, alerts_path, assemble_guard, channel_dir

    assembly = assemble_guard(config_path)
    bill_file = root / "fixtures" / "vendor-2026-09.csv"
    bill_file.parent.mkdir(parents=True, exist_ok=True)
    bill_file.write_text("\n".join([BILL_HEADER, *BILL_ROWS]) + "\n", encoding="utf-8")
    gateway_report = root / "fixtures" / "gateway-2026-09.json"
    gateway_report.write_text(json.dumps(GATEWAY_REPORT), encoding="utf-8")
    gateway_tiny = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    gateway_tiny["budget"]["reconcile"]["alert_threshold_usd"] = 0.1
    gateway_tiny_path = _write_config(
        root, yaml.safe_dump(gateway_tiny, allow_unicode=True), "reconcile.yaml"
    )

    base = ["--channel", assembly.channel_id, "--config", str(gateway_tiny_path)]
    import_code, imported = _cli(
        [
            "import-bill",
            *base,
            "--file",
            str(bill_file),
            "--bill-id",
            "vendor-2026-09",
            "--period",
            "2026-09",
        ]
    )
    duplicate_code, duplicate = _cli(
        [
            "import-bill",
            *base,
            "--file",
            str(bill_file),
            "--bill-id",
            "vendor-2026-09",
            "--period",
            "2026-09",
        ]
    )
    # 未识别格式：改配置声明一个未注册的格式 id
    unregistered = yaml.safe_load(gateway_tiny_path.read_text(encoding="utf-8"))
    unregistered["budget"]["channels"][assembly.channel_id]["bill"]["format"] = "vendor_only"
    unregistered_path = _write_config(
        root, yaml.safe_dump(unregistered, allow_unicode=True), "unregistered.yaml"
    )
    unknown_code, unknown = _cli(
        [
            "import-bill",
            "--channel",
            assembly.channel_id,
            "--config",
            str(unregistered_path),
            "--file",
            str(bill_file),
            "--bill-id",
            "vendor-only",
            "--period",
            "2026-09",
        ]
    )
    reconcile_code, reconciled = _cli(
        [
            "reconcile",
            *base,
            "--period",
            "2026-09",
            "--bill-id",
            "vendor-2026-09",
            "--gateway-report",
            str(gateway_report),
        ]
    )
    alert_code, alerting = _cli(["alert-check", *base])
    classes = sorted({item["classification"] for item in reconciled.get("items", [])})
    alerts_written = [
        entry["kind"]
        for entry in AlertLog(alerts_path(assembly.root, assembly.channel_id)).entries()
    ]
    # 未识别格式必须**零落盘**（无部分导入）：bills/ 下不得留下该批次
    stray_bill = channel_dir(assembly.root, assembly.channel_id) / "bills" / "vendor-only.json"
    expected_classes = sorted(
        {"计费口径", "未入账", "时序错位", "免费额度与折扣", "币种汇率", "未结账", "unclassified"}
    )
    report["steps"]["4_夹具账单导入与对账分类"] = {
        "ok": bool(
            import_code == 0
            and imported.get("entries") == len(BILL_ROWS)
            and duplicate_code == 1
            and "批次幂等" in duplicate.get("error", "")
            and unknown_code == 1
            and "未注册" in unknown.get("error", "")
            and not stray_bill.exists()
            and reconcile_code == 1  # 有告警 ⇒ 非零退出
            and classes == expected_classes
            and reconciled.get("unexplained")
            and reconciled.get("bill_refs")
            and alert_code == 1
            and alerting.get("has_alerts") is True
            and "unexplained_delta" in alerts_written
        ),
        "import_exit_code": import_code,
        "imported_entries": imported.get("entries"),
        "duplicate_batch_exit_code": duplicate_code,
        "unrecognized_format_exit_code": unknown_code,
        "unrecognized_format_wrote_nothing": not stray_bill.exists(),
        "classifications": classes,
        "unexplained": reconciled.get("unexplained"),
        "alerts": reconciled.get("alerts"),
        "bill_refs": [ref["bill_id"] for ref in reconciled.get("bill_refs", [])],
        "gateway_total_usd": reconciled.get("gateway_total_usd"),
        "bill_total_usd": reconciled.get("bill_total_usd"),
        "alert_check_exit_code": alert_code,
        "alerts_jsonl_kinds": alerts_written,
    }


def _step_5_跨进程账本并发共享额度(root: Path, config_path: Path, report: dict) -> None:
    from core.billing.budget import BudgetConfig, FileLedger, ledger_path

    concurrency_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for channel in concurrency_config["budget"]["channels"].values():
        for tier_id, tier in channel["tiers"].items():
            tier["window"] = {"kind": "day"}
            if tier_id == "dev":
                tier["limit_usd"] = 0.5
    concurrency_path = _write_config(
        root, yaml.safe_dump(concurrency_config, allow_unicode=True), "concurrency.yaml"
    )
    cfg = BudgetConfig.from_yaml(concurrency_path)
    channel = next(iter(cfg.channels))
    worker = root / "fixtures" / "worker.py"
    worker.parent.mkdir(parents=True, exist_ok=True)
    worker.write_text(_WORKER_SOURCE, encoding="utf-8")
    config_json = root / "fixtures" / "budget.json"
    config_json.write_text(
        json.dumps(_budget_section(cfg, channel), ensure_ascii=False), encoding="utf-8"
    )
    results = [
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy, sys; "
                f"sys.argv = ['w', {str(config_json)!r}, {str(cfg.ledger_root())!r}]; "
                f"runpy.run_path({str(worker)!r}, run_name='__main__')",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        for _ in range(2)
    ]
    totals = [
        json.loads(result.stdout.strip().splitlines()[-1])
        for result in results
        if result.returncode == 0
    ]
    ledger = FileLedger(ledger_path(cfg.ledger_root(), channel), timeout_seconds=1.0).read()
    record = (ledger.get("tiers") or {}).get("dev", {})
    booked = sum(item["ok"] for item in totals) * 0.1
    report["steps"]["5_跨进程账本并发共享额度"] = {
        "ok": bool(
            len(totals) == 2
            and booked <= 0.5 + 1e-9
            and record.get("spent_usd", 0.0) <= 0.5 + 1e-9
            # 无丢失更新：两进程各自报的入账次数合计 == 账本累计（加锁读改写保住每一次）
            and abs(float(record.get("spent_usd", 0.0)) - booked) < 1e-9
            and int(record.get("refusals", 0)) > 0
            and int(ledger.get("revision", 0)) > 0
        ),
        "processes": len(totals),
        "per_process": totals,
        "booked_usd": round(booked, 4),
        "limit_usd": 0.5,
        "ledger_spent_usd": record.get("spent_usd"),
        "ledger_revision": ledger.get("revision"),
        "refusals": record.get("refusals"),
    }


def _step_6_两维取价与窗口机检(root: Path, config_path: Path, report: dict) -> None:
    from datetime import UTC, datetime, timedelta

    from core.billing.budget import assemble_guard
    from core.billing.runlog import RunLogError, append_run, seal_run, window_coverage
    from core.llm_gateway.gateway import LLMGateway
    from core.llm_gateway.profiles import cost_from_prices, load_or_migrate, snapshot_entry_cells

    # ---- 两维价目四格（夹具矩阵；真实配置的价目取值属运营给定，本特性不发明厂商数字）----
    matrix_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # 零边际成本档案（`zero_marginal: true`）不挂矩阵：非 0 格位与"零边际成本"标记矛盾（装配期即拒）
    matrix_targets = [
        profile_id
        for profile_id, profile in matrix_payload["llm"]["profiles"].items()
        if not profile.get("zero_marginal")
    ]
    for profile_id in matrix_targets:
        matrix_payload["llm"]["profiles"][profile_id]["price_matrix"] = {
            cell: dict(values) for cell, values in DEMO_MATRIX.items()
        }
    matrix_path = _write_config(
        root, yaml.safe_dump(matrix_payload, allow_unicode=True), "matrix.yaml"
    )
    assembly = assemble_guard(matrix_path)
    load = load_or_migrate(matrix_payload)

    class _Calendar:
        def __init__(self, peak):
            self.peak = peak
            self.attribution = "call_start"
            self.timezone = assembly.cfg.peak_windows.timezone
            self.windows = assembly.cfg.peak_windows.windows

        def is_peak(self, moment):
            return self.peak

    cells: dict[str, dict] = {}
    for peak in (True, False):
        for cached in (None, 100):
            backend = _CountingBackend(cached=cached)
            gateway = LLMGateway(
                backend,
                price_book={},
                sleep=lambda _: None,
                profiles=load,
                spend_guard=assembly.guard,
                channel_id=assembly.channel_id,
                peak_windows=_Calendar(peak),
            )
            result = gateway.chat("演示：四格取价", role=_GENERATION_ROLE, stage="screenplay")
            expected_cell = (
                f"{'peak' if peak else 'off_peak'}_{'hit' if cached is not None else 'miss'}"
            )
            expected = cost_from_prices(
                DEMO_MATRIX[expected_cell],
                prompt_tokens=backend.prompt_tokens,
                completion_tokens=backend.completion_tokens,
            )
            cells[expected_cell] = {
                "cell_key": result.cell_key,
                "cost_usd": round(result.cost_usd, 8),
                "expected": round(expected, 8),
                "match": result.cell_key == expected_cell
                and abs(result.cost_usd - expected) < 1e-12,
            }
    # ---- 改价不漂移：历史冻结快照复算逐字节不变 ----
    frozen_entry = load.profile(matrix_targets[0]).to_snapshot()
    frozen_bytes = json.dumps(frozen_entry, sort_keys=True, ensure_ascii=False)
    frozen_cost = cost_from_prices(
        snapshot_entry_cells(frozen_entry)["peak_miss"],
        prompt_tokens=1000,
        completion_tokens=500,
    )
    changed_payload = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    for profile_id in matrix_targets:
        changed_payload["llm"]["profiles"][profile_id]["price_matrix"] = {
            cell: {key: value * 10 for key, value in prices.items()}
            for cell, prices in DEMO_MATRIX.items()
        }
    changed_path = _write_config(
        root, yaml.safe_dump(changed_payload, allow_unicode=True), "changed.yaml"
    )
    changed = load_or_migrate(yaml.safe_load(changed_path.read_text(encoding="utf-8")))
    drift_ok = (
        json.dumps(frozen_entry, sort_keys=True, ensure_ascii=False) == frozen_bytes
        and cost_from_prices(
            snapshot_entry_cells(frozen_entry)["peak_miss"],
            prompt_tokens=1000,
            completion_tokens=500,
        )
        == frozen_cost
        and changed.snapshot().fingerprint != load.snapshot().fingerprint
    )

    # ---- 运行记录窗口：连续夹具通过；散点夹具（8 天真实但缺 2 天）不通过 ----
    window_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    window_cfg["budget"]["runs"] = {"min_window_days": 7, "gap_tolerance_days": 0}
    window_cfg["budget"]["ledger"]["root"] = str(root / "billing-window")
    window_path = _write_config(root, yaml.safe_dump(window_cfg, allow_unicode=True), "window.yaml")
    window = assemble_guard(window_path)
    # 散点夹具走**另一份账本根**（同一渠道 id、同一档位声明）：运行记录目录互不覆盖
    scattered_payload = yaml.safe_load(window_path.read_text(encoding="utf-8"))
    scattered_payload["budget"]["ledger"]["root"] = str(root / "billing-scattered")
    scattered_path = _write_config(
        root, yaml.safe_dump(scattered_payload, allow_unicode=True), "scattered.yaml"
    )
    scattered_window = assemble_guard(scattered_path)
    base = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)

    def _fill(offsets: list[int], *, target) -> None:
        for offset in offsets:
            append_run(
                target.channel_id,
                cfg=target.cfg,
                root=target.root,
                moment=base + timedelta(days=offset),
                stage="screenplay",
                source="real",
                adapter_ref="pilot_llm",
                profile_id="deepseek-flash",
                result="ok",
                cost_source="gateway_accounting",
            )

    _fill(list(range(8)), target=window)
    continuous = window_coverage(
        window.channel_id, cfg=window.cfg, root=window.root, end="2026-09-08"
    )
    _fill([0, 1, 2, 3, 6, 7, 8, 9], target=scattered_window)
    scattered = window_coverage(
        scattered_window.channel_id,
        cfg=scattered_window.cfg,
        root=scattered_window.root,
        end="2026-09-10",
    )
    scattered_tolerant = window_coverage(
        scattered_window.channel_id,
        cfg=scattered_window.cfg,
        root=scattered_window.root,
        end="2026-09-10",
        gap_tolerance_days=2,
    )
    # 封存当日 ⇒ 此后追加拒绝（当日证据封存）
    seal_run("2026-09-08", channel_id=window.channel_id, root=window.root)
    sealed_refused = False
    try:
        _fill([7], target=window)
    except RunLogError:
        sealed_refused = True
    report["steps"]["6_两维取价与窗口机检"] = {
        "ok": bool(
            all(cell["match"] for cell in cells.values())
            and len(cells) == 4
            and drift_ok
            and continuous["meets"] is True
            and continuous["gaps"] == []
            and continuous["max_gap_days"] == 0
            and scattered["meets"] is False
            and scattered["covered_days"] == 8
            and scattered["max_gap_days"] == 2
            and scattered["gaps"]
            and scattered_tolerant["meets"] is True
            and scattered_tolerant["gaps"] == scattered["gaps"]
            and sealed_refused
        ),
        "cells": cells,
        "drift_ok": drift_ok,
        "frozen_snapshot_bytes_unchanged": json.dumps(
            frozen_entry, sort_keys=True, ensure_ascii=False
        )
        == frozen_bytes,
        "frozen_recompute_usd": round(frozen_cost, 8),
        "new_tree_fingerprint_changed": changed.snapshot().fingerprint
        != load.snapshot().fingerprint,
        "continuous": {
            "covered_days": continuous["covered_days"],
            "max_gap_days": continuous["max_gap_days"],
            "continuous": continuous["continuous"],
            "meets": continuous["meets"],
        },
        "scattered": {
            "covered_days": scattered["covered_days"],
            "max_gap_days": scattered["max_gap_days"],
            "continuous": scattered["continuous"],
            "meets": scattered["meets"],
            "gaps": scattered["gaps"],
            "reasons": scattered["reasons"],
        },
        "scattered_with_tolerance_2": {
            "meets": scattered_tolerant["meets"],
            "continuous": scattered_tolerant["continuous"],
            "gaps": scattered_tolerant["gaps"],
        },
        "sealed_append_refused": sealed_refused,
    }


class _CountingBackend:
    """演示后端：固定 token 数 + 可选命中 token 读数（`None` = 厂商未报告）。"""

    def __init__(self, *, cached: int | None = None) -> None:
        self.call_count = 0
        self.prompt_tokens = 1000
        self.completion_tokens = 500
        self.cached_prompt_tokens = cached

    def complete(self, prompt, *, model, temperature, max_tokens):
        from core.llm_gateway.gateway import BackendResult

        self.call_count += 1
        return BackendResult(
            text="演示正文",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens,
        )


_WORKER_SOURCE = """
import datetime as dt, json, sys
from dataclasses import dataclass

from core.billing.budget import (
    AlertLog,
    BudgetConfig,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
)


@dataclass(frozen=True)
class Req:
    channel_id: str
    stage: str
    estimated_usd: float


cfg = BudgetConfig.from_dict({"budget": json.loads(open(sys.argv[1], encoding="utf-8").read())})
channel = next(iter(cfg.channels))
root = sys.argv[2]
guard = SpendGuard(
    cfg=cfg,
    channel_id=channel,
    ledger=FileLedger(ledger_path(root, channel), timeout_seconds=10.0),
    alerts=AlertLog(alerts_path(root, channel)),
    clock=lambda: dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC),
)
ok = refused = 0
for _ in range(10):
    try:
        guard.check(Req(channel, "dev", 0.1)).settle(0.1)
        ok += 1
    except Exception:
        refused += 1
print(json.dumps({"ok": ok, "refused": refused}))
"""


def _budget_section(cfg, channel_id: str) -> dict:
    from core.billing.budget import tiers_of

    return {
        "channels": {
            channel.channel_id: {
                "adapter": channel.adapter,
                "bill": {
                    "format": channel.bill.format_id,
                    "fetch": channel.bill.fetch,
                    "columns": dict(channel.bill.columns),
                    "classification": {
                        "line_kind": dict(channel.bill.line_kind_classes),
                        "amount_sign": dict(channel.bill.amount_signs),
                    },
                },
            }
            for channel in cfg.channels.values()
        },
        "tiers": {
            tier_id: {
                "limit_usd": tier.limit_usd,
                "window": {"kind": tier.window_kind},
                "on_exhausted": "refuse",
                "note": tier.note,
            }
            for tier_id, tier in tiers_of(cfg, channel_id).items()
        },
        "peak_windows": {
            "timezone": cfg.peak_windows.timezone,
            "attribution": cfg.peak_windows.attribution,
            "windows": [window.to_snapshot() for window in cfg.peak_windows.windows],
        },
        "calibration": dict(cfg.calibration),
        "reconcile": dict(cfg.reconcile),
        "ledger": dict(cfg.ledger),
        "runs": dict(cfg.runs),
    }


def _generation_role():
    from core.llm_gateway.routing import Role

    return Role.GENERATION


_GENERATION_ROLE = _generation_role()


def main(argv: list[str] | None = None) -> int:
    from core.billing.budget import BudgetConfig

    parser = argparse.ArgumentParser(
        prog="ops/demo_billing.py",
        description="真实渠道与账单对账离线六步演示（零真实调用、零外部网络、零凭证）",
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true", help="保留临时工作目录（默认演示结束即清理）"
    )
    args = parser.parse_args(argv)
    started = time.perf_counter()
    report: dict = {"agent_id": "billing", "steps": {}, "ok": False}
    with tempfile.TemporaryDirectory(prefix="cineflow-billing-demo-") as tmp:
        root = Path(tmp)
        config_path = _write_config(root, _budget_config_text(root))
        report["work_dir"] = str(root) if args.keep_work_dir else "（临时目录已清理）"
        report["channel_id"] = next(iter(BudgetConfig.from_yaml(config_path).channels))
        _step_1_额度声明与缺项拒绝(root, config_path, report)
        _step_2_最小规模校准记录(root, config_path, report)
        _step_3_未校准扩量拒绝留痕(root, config_path, report)
        _step_4_夹具账单导入与对账分类(root, config_path, report)
        _step_5_跨进程账本并发共享额度(root, config_path, report)
        _step_6_两维取价与窗口机检(root, config_path, report)
    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
