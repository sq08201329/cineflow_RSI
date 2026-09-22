#!/usr/bin/env python
"""试水作品 CLI（功能 015 / T1523）：`run` / `resume` / `inspect`。

用法（与 quickstart 一致，退出码：0 成功 / 1 运行失败或拒绝 / 2 用法错误）：

    uv run python ops/pilot.py run     --form shortdrama --config configs/shortdrama.yaml \
        --topic "夜班记录" --minutes 2 --characters 林静,陈默 --data-dir pilot \
        --run-id demo-run --fixed-clock
    uv run python ops/pilot.py resume  --form shortdrama --config configs/shortdrama.yaml \
        --topic "夜班记录" --minutes 2 --characters 林静,陈默 --data-dir pilot --run-id demo-run
    uv run python ops/pilot.py inspect --data-dir pilot --run-id demo-run [--package]

`--fixed-clock`：全部时间戳取同一常量（两次运行逐字节一致的对照口径）；不给则用墙钟。
本 CLI 只做参数解析与结果打印，编排全在 `agents/pilot/`（零形态分支：形态只作透传参数）。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.pilot.package import PACKAGE_FILES, PackageError, load_manifest  # noqa: E402
from agents.pilot.pilot import (  # noqa: E402
    FileRunStore,
    PilotError,
    PilotInputs,
    precheck,
    resume_pilot,
    run_pilot,
)
from core.orchestration.errors import OrchestrationError  # noqa: E402

FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _fixed_clock():
    return lambda: FIXED_TIMESTAMP


def _parse_inputs(args) -> PilotInputs:
    characters = tuple(name.strip() for name in (args.characters or "").split(",") if name.strip())
    constraints = tuple(
        item.strip() for item in (args.constraints or "").split(",") if item.strip()
    )
    return PilotInputs(
        topic=args.topic,
        target_duration_min=args.minutes,
        characters=characters,
        constraints=constraints,
    )


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _run_payload(result) -> dict:
    return {
        "run_id": result.run_id,
        "form": result.form,
        "status": result.record.status.value,
        "package_dir": None if result.package_dir is None else str(result.package_dir),
        "stages": [
            {
                "stage_id": state.stage_id,
                "status": state.status.value,
                "cost_usd": state.cost_usd,
                "product_count": len(state.products),
                "failure_reason": state.failure_reason,
            }
            for state in result.record.stages
        ],
        "total_cost_usd": result.record.total_cost_usd,
        "precheck": {
            "loaders": list(result.precheck_report.get("loaders", ())),
            "config_fingerprint": result.precheck_report.get("config_fingerprint"),
        },
    }


def _cmd_run(args) -> int:
    clock = _fixed_clock() if args.fixed_clock else None
    try:
        result = run_pilot(
            form=args.form,
            config_path=args.config,
            inputs=_parse_inputs(args),
            data_dir=args.data_dir,
            artifacts_root=args.artifacts_root,
            run_id=args.run_id,
            clock=clock,
        )
    except (PilotError, OrchestrationError, PackageError) as exc:
        _print({"status": "rejected", "error": str(exc)})
        return 1
    payload = _run_payload(result)
    _print(payload)
    return 0 if payload["status"] == "done" else 1


def _cmd_resume(args) -> int:
    clock = _fixed_clock() if args.fixed_clock else None
    try:
        result = resume_pilot(
            form=args.form,
            config_path=args.config,
            inputs=_parse_inputs(args),
            data_dir=args.data_dir,
            artifacts_root=args.artifacts_root,
            run_id=args.run_id,
            clock=clock,
        )
    except (PilotError, OrchestrationError, PackageError) as exc:
        _print({"status": "rejected", "error": str(exc)})
        return 1
    payload = _run_payload(result)
    _print(payload)
    return 0 if payload["status"] == "done" else 1


def _cmd_inspect(args) -> int:
    store = FileRunStore(args.data_dir)
    try:
        record = store.load(args.run_id)
    except PilotError as exc:
        _print({"status": "missing", "error": str(exc)})
        return 1
    payload = {
        "status": record.status.value,
        "run_id": record.run_id,
        "form": record.form,
        "config_fingerprint": record.config_fingerprint,
        "input_fingerprint": record.input_fingerprint,
        "failure_stage": record.failure_stage,
        "failure_reason": record.failure_reason,
        "total_cost_usd": record.total_cost_usd,
        "stages": [
            {
                "stage_id": state.stage_id,
                "status": state.status.value,
                "attempts": state.attempts,
                "cost_usd": state.cost_usd,
                "candidate_scores": [candidate.score for candidate in state.candidates],
                "failure_reason": state.failure_reason,
            }
            for state in record.stages
        ],
    }
    if args.package:
        package_dir = Path(args.data_dir) / "packages" / args.run_id
        payload["package_dir"] = str(package_dir)
        payload["files"] = [
            {"name": name, "present": (package_dir / name).is_file()} for name in PACKAGE_FILES
        ]
        manifest_path = package_dir / "manifest.json"
        payload["manifest"] = load_manifest(package_dir) if manifest_path.is_file() else None
    _print(payload)
    return 0


def _cmd_precheck(args) -> int:
    try:
        report = precheck(
            form=args.form,
            config_path=args.config,
            inputs=_parse_inputs(args),
            data_dir=args.data_dir,
        )
    except (PilotError, OrchestrationError) as exc:
        _print({"status": "rejected", "error": str(exc)})
        return 1
    _print({"status": "ok", **report})
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--form", required=True, help="形态值（如 shortdrama/movie；只作透传）")
    parser.add_argument("--config", required=True, help="形态配置路径")
    parser.add_argument("--topic", required=True, help="题材（非空）")
    parser.add_argument("--minutes", type=int, required=True, help="目标时长（分钟）")
    parser.add_argument("--characters", default="", help="角色表，逗号分隔")
    parser.add_argument("--constraints", default="", help="约束清单，逗号分隔")
    parser.add_argument("--data-dir", required=True, help="数据目录（runs/ 与 packages/ 所在根）")
    parser.add_argument(
        "--artifacts-root", default=None, help="工件根目录（默认 <data-dir>/artifacts）"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="试水作品 CLI：run / resume / inspect / precheck")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="跑一次试水（预检 → 六阶段 → 样片包）")
    _add_common(run_parser)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--fixed-clock", action="store_true", help="固定时钟（可复现对照）")
    run_parser.set_defaults(func=_cmd_run)

    resume_parser = sub.add_parser("resume", help="断点续跑（指纹一致才继续）")
    _add_common(resume_parser)
    resume_parser.add_argument("--run-id", required=True)
    resume_parser.add_argument("--fixed-clock", action="store_true")
    resume_parser.set_defaults(func=_cmd_resume)

    inspect_parser = sub.add_parser("inspect", help="查看运行记录与样片包")
    inspect_parser.add_argument("--data-dir", required=True)
    inspect_parser.add_argument("--run-id", required=True)
    inspect_parser.add_argument("--package", action="store_true", help="同时列出样片包五件套")
    inspect_parser.set_defaults(func=_cmd_inspect)

    precheck_parser = sub.add_parser("precheck", help="只跑启动前预检（零成本零落树）")
    _add_common(precheck_parser)
    precheck_parser.set_defaults(func=_cmd_precheck)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
