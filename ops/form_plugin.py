#!/usr/bin/env python
"""形态插件验证 CLI（功能 021 / 契约 C14，T2159 + T2160）：四子命令、薄转发、退出码 0/1/2。

```
uv run python ops/form_plugin.py guard        [--configs-dir configs] [--roots core agents]
                                              [--out <目录>]
uv run python ops/form_plugin.py registration --config configs/<form>.yaml [--out <目录>]
uv run python ops/form_plugin.py onboarding   --baseline <ref> --config configs/<form>.yaml \
        --out <临时目录> [--mechanism-ref <ref>]
uv run python ops/form_plugin.py sync-versions --check|--write --config configs/<form>.yaml
```

**薄转发**：判定全在 `ops/form_guard.py`（两层扫描：字面量层 + 判断分支层）与
`ops/form_onboarding.py`（五处登记点 + 登记完备三条 + 接入改动清单 + 机制侧总账）；本脚本只解析
参数、打印 JSON、映射退出码。

- **退出码**（常量符号沿用 `ops/transfer.py` 的先例）：`0` 通过 ｜ `1` 判定失败或越界
  （**逐条点名**，不得只报总数）｜ `2` 用法或配置错误（缺 `--baseline`、基线 ref 不可解析、
  配置不可读、形态派生失败）；
- **只读纪律**：只读 git 与文件、**只写** `--out`（`--out` 必须是 tmp_path 类**临时目录**：
  `.gitignore` 没有 `.specify` 规则，落仓库内路径会让"仓库根零新增文件"这条判据失真）；
  本模块**不改造工作区**、**不暂存或提交**任何改动——工具不得成为"改代码来过门禁"的通道；
- **`sync-versions`**：`--check`（默认）只报差集、`--write` 才回写且**只改** `version` 一个叶子键
  （走 `core/yaml_edit.py` 定点改写，其余字节逐字不变）；**判定永远是装配期三方一致性校验**
  （声明 `version` == 实现产出 == `registry` 内实例值），sync 的产物**不是**判据。
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from core.evaluators.errors import PluginAssemblyError  # noqa: E402
from core.evaluators.plugin import (  # noqa: E402
    assemble,
    implementation_version_of,
    parse_manifest,
)
from core.yaml_edit import replace_section_entries  # noqa: E402
from ops.form_guard import (  # noqa: E402
    FormConfigError,
    FormScanError,
    branch_violations,
    classify_exception,
    declared_forms,
    iter_sources,
    literal_violations,
)
from ops.form_onboarding import (  # noqa: E402
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    OnboardingError,
    build_manifest,
    registration_completeness,
    sixth_site_scan,
    write_manifest,
)

#: 插件绑定的六个 Agent（与 `core/evaluators/plugin.py` 的 `slots` 入参一一对应）
PLUGIN_AGENTS: tuple[str, ...] = (
    "screenplay",
    "storyboard",
    "visual",
    "sound",
    "editing",
    "dev",
)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _out_dir(raw: str | None) -> Path:
    """产物目录：缺省即临时目录；**显式传入时必须落在仓库之外**（I-07 的机检前提）。"""
    if raw is None:
        return Path(tempfile.mkdtemp(prefix="form-plugin-"))
    candidate = Path(raw).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError:
        return candidate
    raise OnboardingError(
        f"--out 不得落仓库内路径（{candidate}）：.gitignore 无 .specify 规则，"
        "产物会让「仓库根零新增文件」的判据失真 ⇒ 请用 tmp_path 类临时目录"
    )


def _targets():
    """六个 Agent 的绑定模块与配置类（**静态导入**：不新增第二解析路径）。"""
    from agents.dev.config import DevConfig
    from agents.dev.evaluators import plugins as dev_plugins
    from agents.editing.config import EditingConfig
    from agents.editing.evaluators import plugins as editing_plugins
    from agents.screenplay.config import ScreenplayConfig
    from agents.screenplay.evaluators import plugins as screenplay_plugins
    from agents.sound.config import SoundConfig
    from agents.sound.evaluators import plugins as sound_plugins
    from agents.storyboard.config import StoryboardConfig
    from agents.storyboard.evaluators import plugins as storyboard_plugins
    from agents.visual.config import VisualConfig
    from agents.visual.evaluators import plugins as visual_plugins

    return (
        (screenplay_plugins, ScreenplayConfig),
        (storyboard_plugins, StoryboardConfig),
        (visual_plugins, VisualConfig),
        (sound_plugins, SoundConfig),
        (editing_plugins, EditingConfig),
        (dev_plugins, DevConfig),
    )


class _ZeroCostGateway:
    """**零成本网关桩**（不是真实网关）：只满足注入槽位的存在性，调用即报错。

    版本校验与装配只构造实例、不发起调用 ⇒ 本桩**不产生任何外部网络与凭证面**
    （本特性的判定面既不需要网关也不需要凭证）。
    """

    total_cost_usd = 0.0

    def chat(self, *args, **kwargs):  # pragma: no cover - 判定面永不调用
        raise RuntimeError("零成本网关桩不得被调用（判定面不需要真实渠道调用）")


# ---------------------------------------------------------------------------
# 四子命令（判定在 form_guard / form_onboarding；此处只解析、打印与映射退出码）
# ---------------------------------------------------------------------------


def _guard_report(configs_dir: Path, roots: tuple[str, ...]) -> dict:
    sources = iter_sources(roots)
    literal = literal_violations(configs_dir, sources=sources)
    branch = branch_violations(configs_dir, sources=sources)
    literal_candidates = literal_violations(configs_dir, sources=sources, include_exceptions=True)
    branch_candidates = branch_violations(configs_dir, sources=sources, include_exceptions=True)
    exceptions: dict[str, list[dict]] = {"E1": [], "E2": [], "E3": []}
    for hit in (*literal_candidates, *branch_candidates):
        code = classify_exception(hit)
        if code in exceptions:
            exceptions[code].append(
                {"relative_path": hit.path, "symbol": hit.symbol, "line": hit.line, "hit": hit.hit}
            )
    return {
        "ok": not literal and not branch,
        "layers": {
            "literal": {"violations": len(literal), "candidates": len(literal_candidates)},
            "branch": {"violations": len(branch), "candidates": len(branch_candidates)},
        },
        # 逐条 `(相对路径, 所属符号名, 行号, 命中内容)`（符号名锚点；行号只作人读辅助）
        "hits": [
            {
                "relative_path": hit.path,
                "symbol": hit.symbol,
                "line": hit.line,
                "hit": hit.hit,
                "layer": hit.layer,
            }
            for hit in (*literal, *branch)
        ],
        "exceptions": exceptions,
    }


def _write_report(name: str, payload: dict, raw_out: str | None) -> None:
    if raw_out is None:
        return
    out = _out_dir(raw_out)
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def cmd_guard(args: argparse.Namespace) -> int:
    configs_dir = Path(args.configs_dir)
    roots = tuple(args.roots)
    declared_forms(configs_dir)
    report = _guard_report(configs_dir, roots)
    _emit(report)
    try:
        _write_report("guard-report.json", report, args.out)
    except OnboardingError as exc:
        _emit({"ok": False, "error": str(exc)})
        return EXIT_USAGE
    return EXIT_OK if report["ok"] else EXIT_FAILED


def cmd_registration(args: argparse.Namespace) -> int:
    from agents.pilot.pilot import PrecheckError, form_clause_completeness

    config_path = Path(args.config)
    if not config_path.is_file():
        raise OnboardingError(f"配置不可读：{config_path}")
    try:
        clauses = list(form_clause_completeness(config_path))
        clause_error = ""
    except PrecheckError as exc:
        clauses, clause_error = [], str(exc)
    completeness = registration_completeness()
    sixth = sixth_site_scan()
    report = {
        "ok": not completeness.violations() and not sixth and not clause_error,
        "sites": [
            {
                "site": status.site.path,
                "symbol": status.site.symbol,
                "kind": status.site.kind,
                "forms": list(status.registered),
                "delegated": status.delegated,
                "missing": list(status.missing),
                "detail": status.detail,
            }
            for status in completeness.sites
        ],
        "completeness": {
            "forms": list(completeness.declared),
            "unique": completeness.unique,
            "at_least_two": completeness.at_least_two,
            "registered_matches": completeness.registered_matches,
            "violations": list(completeness.violations()),
        },
        "sixth_sites": [site.describe() for site in sixth],
        "clauses": clauses,
        "clause_error": clause_error,
    }
    _emit(report)
    try:
        _write_report("registration-report.json", report, args.out)
    except OnboardingError as exc:
        _emit({"ok": False, "error": str(exc)})
        return EXIT_USAGE
    return EXIT_OK if report["ok"] else EXIT_FAILED


def cmd_onboarding(args: argparse.Namespace) -> int:
    out = _out_dir(args.out)
    manifest = build_manifest(
        args.config, args.baseline, mechanism_ledger_ref=args.mechanism_ref or "HEAD"
    )
    path = write_manifest(manifest, out)
    _emit(
        {
            "ok": manifest["exit_code"] == EXIT_OK,
            "manifest_path": str(path),
            "out_dir": str(out),
            "form": manifest["form"],
            "baseline_ref": manifest["baseline_ref"],
            "mechanism_ledger_ref": manifest["mechanism_ledger_ref"],
            "config_fingerprint": manifest["config_fingerprint"],
            "counts": manifest["counts"],
            "violations": manifest["violations"],
            "mechanism_changes_included": manifest["mechanism_changes_included"],
            "zero_code_onboarding": manifest["zero_code_onboarding"],
            "exit_code": manifest["exit_code"],
        }
    )
    return manifest["exit_code"]


def cmd_sync_versions(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_file():
        raise OnboardingError(f"配置不可读：{config_path}")
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise OnboardingError(f"形态配置顶层不是映射：{config_path}")
    work_dir = Path(tempfile.mkdtemp(prefix="form-plugin-sync-"))
    diffs: list[dict] = []
    ok = True
    for module, config_class in _targets():
        config = config_class.from_dict(document)
        manifest = parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT)
        from core.tree.artifacts import LocalArtifactStore

        provided = {
            "agent_config": config,
            "gateway": _ZeroCostGateway(),
            "artifacts": LocalArtifactStore(work_dir / module.AGENT),
        }
        try:
            # **权威判据**：装配期三方一致性校验（声明值 == 实现产出 == 注册实例值）
            assemble(
                manifest,
                agent_config=config,
                gateway=provided["gateway"],
                artifacts=provided["artifacts"],
            )
        except PluginAssemblyError:
            ok = False
            for declaration in manifest.declarations:
                actual = implementation_version_of(declaration, **provided)
                if actual != declaration.version:
                    diffs.append(
                        {
                            "agent": manifest.agent,
                            "slot": declaration.slot,
                            "evaluator_id": declaration.evaluator_id,
                            "declared": declaration.version,
                            "actual": actual,
                            "section_path": [
                                "evaluators",
                                "plugins",
                                manifest.agent,
                                declaration.slot,
                                declaration.evaluator_id,
                            ],
                        }
                    )
    if diffs and args.write:
        text = config_path.read_text(encoding="utf-8")
        for diff in diffs:
            text = replace_section_entries(
                text, tuple(diff["section_path"]), {"version": diff["actual"]}
            )
        config_path.write_text(text, encoding="utf-8")
    _emit({"ok": ok, "mode": "write" if args.write else "check", "diffs": diffs})
    if ok:
        return EXIT_OK
    return EXIT_OK if args.write else EXIT_FAILED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops/form_plugin.py",
        description="形态插件验证（021 C14）：四子命令、只读、退出码 0/1/2",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    guard = sub.add_parser("guard", help="两层扫描（字面量层 + 判断分支层），零违规 ⇒ 0")
    guard.add_argument("--configs-dir", default="configs")
    guard.add_argument("--roots", nargs="+", default=["core", "agents"])
    guard.add_argument("--out", default=None)

    registration = sub.add_parser("registration", help="五处登记点 + 登记完备三条 + 无第六处")
    registration.add_argument("--config", required=True)
    registration.add_argument("--out", default=None)

    onboarding = sub.add_parser("onboarding", help="接入改动清单（由 git 派生、append-only）")
    onboarding.add_argument("--baseline", required=True)
    onboarding.add_argument("--config", required=True)
    onboarding.add_argument("--out", default=None)
    onboarding.add_argument(
        "--mechanism-ref",
        default=None,
        help="机制落地后的提交 ref（缺省 = HEAD；必须 != --baseline）",
    )

    sync = sub.add_parser("sync-versions", help="声明 version 与实现产出的差集（--check 不回写）")
    sync.add_argument("--config", required=True)
    mode = sync.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只报差集（默认）")
    mode.add_argument(
        "--write", action="store_true", help="回写 version 叶子键（门禁只跑 --check）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "guard":
            return cmd_guard(args)
        if args.command == "registration":
            return cmd_registration(args)
        if args.command == "onboarding":
            return cmd_onboarding(args)
        return cmd_sync_versions(args)
    except (OnboardingError, FormConfigError, FormScanError) as exc:
        _emit({"ok": False, "error": str(exc), "exit_code": EXIT_USAGE})
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
