#!/usr/bin/env python
"""校准结论迁移面 CLI（功能 020 / 契约 C15~C17）：提案 / 人工两键 / 只读报表。

**判定与落盘全在 core**（`core/calibration/transfer.py`），本脚本只做参数解析与 JSON 打印
（薄转发，与 `ops/billing.py` 同风格）。四个子命令（C17 逐字）：

```
uv run python ops/transfer.py transfer        --data-dir <dir> --from configs/shortdrama.yaml \
        --to configs/movie.yaml --evaluator <id@version> --period <周期> [--dry-run]
uv run python ops/transfer.py transfer-confirm --data-dir <dir> --transfer <id> \
        --by <人> --reason <理由>
uv run python ops/transfer.py transfer-shelve  --data-dir <dir> --transfer <id> \
        --by <人> --reason <理由>
uv run python ops/transfer.py transfer-report  --data-dir <dir> [--config <形态配置>]
```

- **只迁结论、不迁权重**：四个子命令**不写任何权重**、**不调 `confirm_proposal`**
  （`ops/calibrate.py` 那条路径只服务 010 的权重提案，二者互不调用）；
- **`--dry-run` 零落盘**：只做条件判定并打印预览（用"落盘前后目录文件集合相等"断言）；
- **来源只读**：只读既有台账 / 快照 / 信度报告 / 漂移产物，零写入这些目录；
- 无可迁移结论（真实回流待运营）⇒ 如实标注「无可迁移结论（来源缺失）」并给出继续观察条件，
  **不得**以模拟回流的结论充当来源。

退出码（与 `ops/billing.py` / `ops/check_credentials.py` 同口径）：
`0` 成功（迁移件已落盘且判定为 `transferable`；人工两键成功；报表读取成功）｜
`1` 执行失败或拒绝（来源缺失 / 判定不可迁移 / 同键重产被拒 / 件被改写不可采信）｜
`2` 用法或配置错误（缺 `calibration.transfer` 任一键、周期量纲不符、条件取值域外）。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.billing.bill import SnapshotIntegrityError  # noqa: E402 - 摘要校验失败 ⇒ 拒采信
from core.calibration.config import CalibrationConfig  # noqa: E402
from core.calibration.errors import CalibrationConfigError  # noqa: E402
from core.calibration.transfer import (  # noqa: E402 - 迁移面机制件（判定与落盘单点）
    COVERAGE_SOURCES,
    TransferError,
    append_transfer_decision,
    build_transfer,
    save_transfer,
    transfer_report,
)
from core.evaluators.errors import ValidationError  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return code


def _fail(message: str, code: int) -> int:
    return _emit({"error": message}, code)


def _cmd_transfer(args) -> int:
    try:
        payload = build_transfer(
            args.data_dir,
            source_config_path=args.source,
            target_config_path=args.target,
            evaluator_key=args.evaluator,
            period=args.period,
            real_coverage_days=args.real_covered_days,
            coverage_source=args.coverage_source,
        )
    except TransferError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    except (CalibrationConfigError, ValidationError) as exc:
        return _fail(f"配置或用法错误：{exc}", EXIT_USAGE)

    verdict = payload["comparability"]["verdict"]
    if args.dry_run:
        # 预览：**零落盘**（不调用 save_transfer，不动任何文件）
        return _emit(
            {"dry_run": True, "written": None, "transfer_id": payload["transfer_id"], **payload},
            EXIT_OK if verdict == "transferable" else EXIT_FAILED,
        )
    try:
        path = save_transfer(args.data_dir, payload)
    except TransferError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    except SnapshotIntegrityError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    return _emit(
        {
            "dry_run": False,
            "written": str(path),
            "transfer_id": payload["transfer_id"],
            "verdict": verdict,
            "conditions": payload["comparability"]["conditions"],
            "reasons": payload["comparability"]["reasons"],
            "status": payload["status"],
        },
        EXIT_OK if verdict == "transferable" else EXIT_FAILED,
    )


def _decision_cmd(args, decision: str) -> int:
    try:
        payload = append_transfer_decision(
            args.data_dir, args.transfer, decision=decision, by=args.by, reason=args.reason
        )
    except TransferError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    except SnapshotIntegrityError as exc:
        return _fail(f"拒绝（系统字段被改写，拒采信）：{exc}", EXIT_FAILED)
    except ValidationError as exc:
        return _fail(f"用法错误：{exc}", EXIT_USAGE)
    return _emit(
        {
            "transfer_id": payload["transfer_id"],
            "decision": decision,
            "status": payload["status"],
            "confirmed_by": payload["confirmed_by"],
            "confirmed_at": payload["confirmed_at"],
            "overrides": payload["overrides"],
            "weights_changed": False,  # 只采纳结论：权重与既有节点证据逐字节不变
        },
        EXIT_OK,
    )


def _cmd_transfer_report(args) -> int:
    config = None
    if args.config:
        try:
            config = CalibrationConfig.from_yaml(args.config).transfer
        except CalibrationConfigError as exc:
            return _fail(f"配置错误：{exc}", EXIT_USAGE)
    try:
        report = transfer_report(args.data_dir, transfer_config=config)
    except TransferError as exc:
        return _fail(f"拒绝：{exc}", EXIT_FAILED)
    if report["counts"]["untrusted"]:
        return _fail(
            "有迁移件的系统字段被改写（system_digest 校验失败，拒采信）："
            f"{report['untrusted']}；报表见 {report['dir']}",
            EXIT_FAILED,
        )
    return _emit(report, EXIT_OK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops/transfer.py",
        description="校准结论迁移面（只迁结论、不迁权重；append-only；人工两键）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    transfer = sub.add_parser("transfer", help="生成迁移件（可比性判定 + 落盘；--dry-run 零落盘）")
    transfer.add_argument("--data-dir", dest="data_dir", required=True, help="010/020 数据目录")
    transfer.add_argument("--from", dest="source", required=True, help="来源形态配置（shortdrama）")
    transfer.add_argument("--to", dest="target", required=True, help="目标形态配置（movie）")
    transfer.add_argument("--evaluator", required=True, help="评估器 id@version")
    transfer.add_argument("--period", required=True, help="周期标签（由来源 cadence 派生）")
    transfer.add_argument(
        "--real-covered-days",
        dest="real_covered_days",
        type=int,
        default=None,
        help="来源**真实**覆盖天数观测（由运营/C9 覆盖视图读出；缺省 ⇒ 未标定 ⇒ 条件不满足）",
    )
    transfer.add_argument(
        "--coverage-source",
        dest="coverage_source",
        default="untracked",
        choices=COVERAGE_SOURCES,
        help="覆盖观测的来源标注（装配面显式声明；fixture_drill = 离线演练夹具）",
    )
    transfer.add_argument("--dry-run", dest="dry_run", action="store_true", help="只判定、零落盘")
    transfer.set_defaults(handler=_cmd_transfer)

    confirm = sub.add_parser("transfer-confirm", help="人工两键之一：采纳（只采纳结论，不改权重）")
    confirm.add_argument("--data-dir", dest="data_dir", required=True)
    confirm.add_argument("--transfer", required=True, help="transfer_id")
    confirm.add_argument("--by", required=True, help="采纳人")
    confirm.add_argument("--reason", required=True, help="采纳理由")
    confirm.set_defaults(handler=lambda args: _decision_cmd(args, "confirmed"))

    shelve = sub.add_parser("transfer-shelve", help="人工两键之一：搁置（不可迁移件只能走这里）")
    shelve.add_argument("--data-dir", dest="data_dir", required=True)
    shelve.add_argument("--transfer", required=True, help="transfer_id")
    shelve.add_argument("--by", required=True, help="搁置人")
    shelve.add_argument("--reason", required=True, help="搁置理由")
    shelve.set_defaults(handler=lambda args: _decision_cmd(args, "shelved"))

    report = sub.add_parser("transfer-report", help="只读报表（零写入；无来源时如实标注）")
    report.add_argument("--data-dir", dest="data_dir", required=True)
    report.add_argument("--config", default=None, help="形态配置（给出即读其声明的观察条件阈值）")
    report.set_defaults(handler=_cmd_transfer_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (CalibrationConfigError, ValidationError) as exc:
        return _fail(f"配置或用法错误：{exc}", EXIT_USAGE)
    except Exception as exc:  # noqa: BLE001 - CLI 边界：如实报错给运行方，不吞异常
        return _fail(f"未预期错误：{exc!r}", EXIT_FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
