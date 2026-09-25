#!/usr/bin/env python
"""开发 Agent（选题 / IP 评估 / 立项组合）CLI（功能 017）：produce / submit。

- submit：提交人工策略版本（C14）——静态检查（002）与接口签名（`plan(inputs, config)`）均过
  才落 `policies/history/dev/{version}.py` + meta.json；`--draft` 只校验算版本、不入历史；
  同源码重提幂等（同版本号）；
- produce：按人工策略跑一轮**立项组合产出**（单一产出，无阶段划分）——策略源码过 002 静态
  检查后执行、版本 = 源码 BLAKE3 前 12 位（节点可回溯到产出它的策略版本）；**逐条目**经
  LLM 网关生成论证要点、成本入账、节点一次性落发现树；工件内容寻址入
  `<data-dir>/artifacts`；结果 JSON 打到 stdout。

评估器装配：`produce` 以 `evaluators=None` 请求**真实四评估器装配**
（`agents/dev/evaluators.build_dev_evaluators`：两门禁 + 两确定性代理，驱动自 `dev.signals`
模拟数据源，零 LLM 调用）；装配口径（权重/阈值/合成策略/评估器 `id@version`）随轮次树
`config_snapshot` 冻结，历史节点不重算（原则一/原则五）。

CLI 默认面向 PG 库（--dsn 或 CINEFLOW_PG_DSN，schema 由 Alembic 迁移管理，CLI 不隐式改 PG
schema）；SQLite DSN（测试/本地）自动建表。`--policy-dir` 为**策略历史根目录**（默认
`policies/history`；版本源码位于 `<policy-dir>/dev/{version}.py`）。人工策略版本核验：
`--policy <version>` 读该路径并校验源码哈希与版本一致（不符即拒绝）；`--policy-file` 按源码
哈希派生版本（本地验证用）。

- compare：新版本 vs 部署版本回放对比（**必须附无偏性验收结论**，FR-013 发布阻塞；
  **可比对树数低于 `dev.min_comparable_trees` 即拒绝产出**，SC-011）；
- adopt / reject：人工采纳/拒绝（采纳才更新部署指针 `deployment.dev.current_policy_version`；
  拒绝留痕理由非空）；
- evidence：生成周期升级判据材料（全量阈值快照 + 逐项"实测值 / 无法评价（来源缺失）"+
  系统结论（恒不为"达标"）+ 继续观察条件）；`--override` 追加推翻留痕（系统字段不变）。

退出码：0 成功；2 参数/依赖/门禁拒绝（缺 DSN、策略未过静态检查、版本不符、未过无偏性、
无可用冻结树等）；1 运行期错误（输入预检拒绝、评估器装配失败、可比对树数不足等）。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.billing.budget import (  # noqa: E402 - 019（C10）：归因分支——拒绝可辨
    BudgetRefusedError,
    refusal_reason,
)

AGENT_ID = "dev"


def _resolve_dsn(args) -> str | None:
    import os

    return args.dsn or os.environ.get("CINEFLOW_PG_DSN")


def _fail(message: str, code: int) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False))
    return code


def _policy_source_path(args) -> tuple[Path | None, str | None, str]:
    """策略源码定位：--policy-file（显式路径）或 --policy <version>（历史目录）。

    返回 (路径, 声明的版本 | None, 错误信息)；声明的版本来自文件名（--policy），
    加载时核验其与源码哈希一致（版本 = 源码内容，人工版本同样可机检）。
    """
    if args.policy_file:
        path = Path(args.policy_file)
        if not path.is_file():
            return None, None, f"策略源码不存在：{path}"
        return path, None, ""
    if args.policy:
        path = Path(args.policy_dir) / AGENT_ID / f"{args.policy}.py"
        if not path.is_file():
            return None, None, f"策略版本 {args.policy!r} 对应源码不存在：{path}"
        return path, args.policy, ""
    return None, None, "必须提供 --policy（历史目录版本）或 --policy-file（显式源码路径）"


def _load_policy(args):
    """加载人工策略：定位源码 → 薄调用 `agents/dev/policy_loader.load_policy_text`。

    装载三段（静态检查前置 → 版本核验 → 超时内实例化 + 零环境对象守护）是**业务侧单一实现**
    （功能 018 / C2：`agents/dev/policy_loader.py`）；本处只做**参数定位**与错误转述，
    不得留第二份装载路径（`agents/` 侧才是链首 dev 环节的装载入口）。
    """
    from agents.dev.policy_loader import PolicyLoadError, load_policy_text

    path, declared, locate_error = _policy_source_path(args)
    if path is None:
        return None, None, locate_error
    try:
        loaded = load_policy_text(
            path.read_text(encoding="utf-8"),
            declared_version=declared,
            origin=str(path),
        )
    except PolicyLoadError as exc:
        return None, None, str(exc)
    return loaded, str(path), ""


def _cmd_produce(args) -> int:
    from sqlalchemy import create_engine

    from agents.dev.config import DevConfig, DevConfigError
    from agents.dev.db import create_jobs_schema
    from agents.dev.loop import DevLoopError, run_dev_round
    from core.llm_gateway.gateway import GatewayError, LLMGateway

    dsn = _resolve_dsn(args)
    if not dsn:
        return _fail("缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）", 2)

    policy, policy_path, error = _load_policy(args)
    if policy is None:
        return _fail(error, 2)

    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)  # 数据目录（工件目录 + 常为 SQLite DSN 落点）
    if not Path(args.config).is_file():
        return _fail(f"形态配置不存在：{args.config}", 2)
    try:
        config = DevConfig.from_yaml(args.config)
    except DevConfigError as exc:
        return _fail(f"形态配置非法：{exc}", 2)
    try:
        backend = _backend(args)
    except BudgetRefusedError as exc:
        # 019（C10 调用点分支规则）：预算拒绝与"网关后端不可用"**可辨**（归因分支）。
        # 装配期今天不产生拒绝（本处注入后端路径当前不可用，见 C10 ③ 的条件义务），
        # 但分支先就位：一旦该路径变为可用真实装配，拒绝不会被误报成凭证/后端故障。
        return _fail(refusal_reason(exc), 1)
    except GatewayError as exc:
        return _fail(f"网关后端不可用：{exc}", 2)
    inputs = {"genre_bounds": list(args.genre_bounds or []), "audience": args.audience}
    if args.notes:
        inputs["notes"] = args.notes

    engine = create_engine(dsn)
    from core.tree.store import create_tree_store

    if dsn.startswith("sqlite"):
        # 测试/本地：SQLite 自动建表（树表首次建；触发器非幂等，已建即跳过）
        from sqlalchemy import inspect

        from core.tree.db import create_schema

        if not inspect(engine).has_table("discovery_trees"):
            create_schema(engine)
        create_jobs_schema(engine)
    # PG：schema 由 Alembic 迁移管理，CLI 不隐式改 schema（先 `alembic upgrade head`）
    store = create_tree_store(engine)
    artifacts = _artifact_store(data_dir)
    gateway = LLMGateway(
        backend,
        price_book=config.model_prices,
        # 019（C10 ③ 条件义务）：本处注入后端路径当前不可用（`--backend http` 走裸构造
        # `HttpBackend()` 恒抛），故显式登记为不接门禁；一旦改为可用真实装配（如
        # `HttpBackend.from_profile`），`test_billing_core_purity.py` 的保证性断言即红，
        # 必须连同非 None 守卫一起改（不得只补 None 了事）
        spend_guard=None,
    )

    try:
        result = run_dev_round(
            round_id=args.round,
            policy=policy,
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=inputs,
            evaluators=None,  # 真实四评估器装配（两门禁 + 两确定性代理）
        )
    except DevLoopError as exc:
        return _fail(str(exc), 1)
    except DevConfigError as exc:  # 装配期配置漂移（权重键与评估器集合不一致）
        return _fail(f"评估器装配失败：{exc}", 2)

    payload = result.to_dict()
    payload["policy_source"] = policy_path
    payload["data_dir"] = str(data_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_submit(args) -> int:
    from agents.dev.policy_versions import PolicySubmissionError, submit_policy
    from dreaming.config import DreamConfig, DreamConfigError

    source_path = Path(args.source_file)
    if not source_path.is_file():
        return _fail(f"策略源码不存在：{source_path}", 2)
    if not Path(args.config).is_file():
        return _fail(f"形态配置不存在：{args.config}", 2)
    try:
        cfg = DreamConfig.from_yaml(args.config)
    except DreamConfigError as exc:
        return _fail(f"做梦形态配置非法：{exc}", 2)
    try:
        record = submit_policy(
            source_path.read_text(encoding="utf-8"),
            args.by,
            cfg,
            parent_version=args.parent_version,
            draft=args.draft,
            history_root=Path(args.policy_dir).expanduser(),
        )
    except PolicySubmissionError as exc:
        return _fail(str(exc), 2)
    payload = record.to_dict()
    payload["policy_dir"] = str(args.policy_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _backend(args):
    """网关后端装配：默认 Mock（开发/CI 确定性）；--backend http 走真实后端（需凭证）。"""
    from core.llm_gateway.backends.http import HttpBackend
    from core.llm_gateway.backends.mock import MockBackend

    if args.backend == "http":
        return HttpBackend()
    return MockBackend()


def _store_and_pool(dsn: str):
    """引擎 + 树存储 + 冻结树池装配（回放对比用；未冻结树跳过并计数）。"""
    from sqlalchemy import create_engine

    from core.replay.pool import PoolError, SimulatorPool
    from core.tree.store import create_tree_store

    engine = create_engine(dsn)
    store = create_tree_store(engine)
    pool = SimulatorPool(store)
    skipped = 0
    for tree in store.trees_by(agent_id=AGENT_ID):
        try:
            pool.add_tree(tree)
        except PoolError:
            skipped += 1  # 未冻结（仍在写入）的树不入池
    return engine, store, pool, skipped


def _comparison_inputs(args) -> dict:
    """回放对比输入（结构键的重算输入）：dev 的立项约束与形态参数。"""
    inputs = {
        "genre_bounds": list(getattr(args, "genre_bounds", None) or []),
        "audience": getattr(args, "audience", ""),
    }
    if getattr(args, "notes", None):
        inputs["notes"] = args.notes
    return inputs


def _cmd_compare(args) -> int:
    """回放对比：前置无偏性验收结论 + 单一阶段结构键回放（零 LLM、零生成）。"""
    import json as _json

    from agents.dev.config import DevConfig, DevConfigError
    from agents.dev.sandbox_compare import (
        CompareError,
        UnbiasednessAttestation,
        compare_versions,
        signal_sources_of,
    )

    dsn = _resolve_dsn(args)
    if not dsn:
        return _fail("缺少 DSN（--dsn 或 CINEFLOW_PG_DSN）", 2)
    try:
        attestation = UnbiasednessAttestation.load(Path(args.unbiasedness))
    except CompareError as exc:
        return _fail(str(exc), 2)
    if attestation.verdict != "pass":
        return _fail(
            "未过无偏性验收（FR-013 发布阻塞）：回放口径不可信时不得产出对比报告"
            f"（verdict={attestation.verdict}，τ={attestation.tau}）",
            2,
        )
    try:
        config = DevConfig.from_yaml(args.config)
    except DevConfigError as exc:
        return _fail(f"形态配置非法：{exc}", 2)
    data_dir = Path(args.data_dir).expanduser()
    _, store, pool, skipped = _store_and_pool(dsn)
    if not pool.trees:
        return _fail("无可用冻结树（回放对比需要至少一棵已冻结的立项组合轮次树）", 2)
    try:
        comparison = compare_versions(
            args.new_version,
            args.deployed_version,
            pool,
            config,
            store=store,
            inputs=_comparison_inputs(args),
            unbiasedness=attestation,
            history_root=Path(args.policy_dir).expanduser(),
            comparison_dir=data_dir / "comparisons",
        )
    except (CompareError, ValueError) as exc:
        return _fail(str(exc), 1)
    payload = comparison.to_dict()
    payload["comparable_trees"] = len(comparison.per_tree)  # 可比对树数（= 池内冻结树）
    payload["signal_sources"] = list(signal_sources_of(config))  # 模拟源标注（与产物/材料同源）
    payload["skipped_unfrozen_trees"] = skipped
    payload["data_dir"] = str(data_dir)
    print(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _cmd_adopt(args) -> int:
    return _decide(args, decision="adopt")


def _cmd_reject(args) -> int:
    return _decide(args, decision="reject")


def _decide(args, *, decision: str) -> int:
    """人工采纳/拒绝（同一实现，decision 由子命令固定）：仅 adopt 更新部署指针。"""
    import json as _json

    from agents.dev.adoption import AdoptionError, adopt

    data_dir = Path(args.data_dir).expanduser()
    try:
        record = adopt(
            args.comparison,
            decision,
            args.by,
            args.reason,
            config_path=Path(args.config),
            comparison_dir=data_dir / "comparisons",
            adoption_dir=data_dir / "adoptions",
            history_root=Path(args.policy_dir).expanduser(),
        )
    except (AdoptionError, FileNotFoundError) as exc:
        return _fail(str(exc), 2)
    print(_json.dumps(record.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def _period_bounds(args, period_days: int) -> tuple[str, str]:
    """缺省窗口（含首尾跨 period_days 天）+ 端点校验：口径唯一实现见 `core/calibration/periods`。"""
    from datetime import UTC, datetime

    from core.calibration.periods import default_period_bounds

    return default_period_bounds(
        args.period_start, args.period_end, period_days, today=datetime.now(UTC).date()
    )


def _period_nodes(dsn: str, period_start: str, period_end: str, period_days: int) -> list:
    """窗口内已评估节点（判据材料的取数面：门禁违规率 / 代理分布 / 样本量）。"""
    from sqlalchemy import create_engine

    from core.calibration.periods import window_timestamps
    from core.tree.store import create_tree_store

    start_ts, end_ts = window_timestamps(period_start, period_days)
    store = create_tree_store(create_engine(dsn))
    return [
        node
        for tree in store.trees_by(agent_id=AGENT_ID)
        for node in store.nodes_of(tree.tree_id)
        if start_ts <= node.created_at < end_ts
    ]


def _cmd_evidence(args) -> int:
    """生成/推翻周期升级判据材料（全量阈值快照 + 逐项取值 + 系统结论非达标）。"""
    import json as _json

    from agents.dev.config import DevConfig, DevConfigError
    from agents.dev.upgrade_evidence import (
        UpgradeEvidenceError,
        build_upgrade_evidence,
        continuation_conditions,
        override_conclusion,
    )
    from core.calibration.config import CalibrationConfig
    from core.calibration.errors import CalibrationConfigError
    from core.calibration.periods import period_label

    data_dir = Path(args.data_dir).expanduser()
    if args.override:
        if not args.by or not args.reason:
            return _fail("--override 必须同时提供 --by（推翻人）与 --reason（理由）", 2)
        try:
            evidence = override_conclusion(
                args.period, by=args.by, reason=args.reason, data_dir=data_dir
            )
        except UpgradeEvidenceError as exc:
            return _fail(str(exc), 2)
        print(_json.dumps(evidence.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0

    try:
        config = DevConfig.from_yaml(args.config)
        calibration = CalibrationConfig.from_yaml(args.config)
    except (DevConfigError, CalibrationConfigError) as exc:
        return _fail(f"形态配置非法：{exc}", 2)

    period_start, period_end = _period_bounds(args, calibration.period_days)
    nodes: list = []
    dsn = _resolve_dsn(args)
    if dsn:
        nodes = _period_nodes(dsn, period_start, period_end, calibration.period_days)
    try:
        evidence = build_upgrade_evidence(args.period, config, nodes=nodes, data_dir=data_dir)
    except UpgradeEvidenceError as exc:
        return _fail(str(exc), 2)
    payload = evidence.to_dict()
    payload["period_window"] = [period_start, period_end]
    payload["week_label"] = period_label(period_end, calibration.period_days)
    payload["continuation_conditions"] = continuation_conditions(evidence.threshold_snapshot)
    print(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def _artifact_store(data_dir: Path):
    """工件存储装配：本地内容寻址目录（生产装配层换 S3ArtifactStore，接口不变）。"""
    from core.tree.artifacts import LocalArtifactStore

    return LocalArtifactStore(data_dir / "artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="开发 Agent（降级模式）：立项组合产出与治理")
    sub = parser.add_subparsers(dest="command", required=True)

    produce = sub.add_parser("produce", help="按人工策略跑一轮立项组合产出并落树")
    produce.add_argument("--round", required=True, help="轮次 ID（幂等键）")
    produce.add_argument(
        "--genre-bounds", nargs="+", required=True, help="题材边界（多个值以空格分隔）"
    )
    produce.add_argument("--audience", required=True, help="目标受众（非空）")
    produce.add_argument("--notes", default=None, help="立项备注（可选，入提示词）")
    produce.add_argument("--policy", default=None, help="人工策略版本（读 <policy-dir>/{版本}.py）")
    produce.add_argument("--policy-file", default=None, help="人工策略源码路径（本地验证用）")
    produce.add_argument(
        "--policy-dir",
        default=str(REPO_ROOT / "policies" / "history"),
        help="策略历史根目录（版本源码位于 <policy-dir>/dev/{version}.py）",
    )
    produce.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    produce.add_argument("--data-dir", default=str(REPO_ROOT / "dev"))
    produce.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    produce.add_argument(
        "--backend",
        choices=("mock", "http"),
        default="mock",
        help="网关后端：mock（默认，开发/CI）/ http（真实后端，需凭证）",
    )
    produce.set_defaults(func=_cmd_produce)

    submit = sub.add_parser("submit", help="提交人工策略版本（静态检查 + 版本化落盘）")
    submit.add_argument("--source-file", required=True, help="策略源码路径")
    submit.add_argument("--by", required=True, help="提交人（谱系 provenance 必填）")
    submit.add_argument("--parent-version", default=None, help="父版本（谱系根留空）")
    submit.add_argument("--draft", action="store_true", help="草稿：只校验算版本，不入历史")
    submit.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    submit.add_argument(
        "--policy-dir",
        default=str(REPO_ROOT / "policies" / "history"),
        help="策略历史根目录（默认仓库 policies/history）",
    )
    submit.set_defaults(func=_cmd_submit)

    compare = sub.add_parser("compare", help="新版本 vs 部署版本回放对比（需附无偏性结论）")
    compare.add_argument("--new-version", required=True, help="待评估的新策略版本")
    compare.add_argument("--deployed-version", required=True, help="当前部署策略版本")
    compare.add_argument(
        "--unbiasedness", required=True, help="无偏性验收结论 JSON 路径（FR-013 发布阻塞）"
    )
    compare.add_argument(
        "--genre-bounds", nargs="+", required=True, help="题材边界（回放结构键的重算输入）"
    )
    compare.add_argument("--audience", required=True, help="目标受众（结构键的重算输入）")
    compare.add_argument("--notes", default=None, help="立项备注（可选，须与产出时一致）")
    compare.add_argument(
        "--policy-dir",
        default=str(REPO_ROOT / "policies" / "history"),
        help="策略历史根目录",
    )
    compare.add_argument(
        "--data-dir",
        default=str(REPO_ROOT / "dev"),
        help="对比报告落盘目录（<data-dir>/comparisons）",
    )
    compare.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    compare.add_argument("--dsn", default=None, help="PG DSN，默认读 CINEFLOW_PG_DSN")
    compare.set_defaults(func=_cmd_compare)

    for name, handler, help_text in (
        ("adopt", _cmd_adopt, "人工采纳：更新部署指针并留痕"),
        ("reject", _cmd_reject, "人工拒绝：指针不变、理由留痕"),
    ):
        decide = sub.add_parser(name, help=help_text)
        decide.add_argument("--comparison", required=True, help="对比报告 ID（依据引用）")
        decide.add_argument("--by", required=True, help="决策人（留痕必填）")
        decide.add_argument("--reason", required=True, help="理由（留痕必填，非空）")
        decide.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
        decide.add_argument(
            "--policy-dir",
            default=str(REPO_ROOT / "policies" / "history"),
            help="策略历史根目录（采纳前校验版本已版本化）",
        )
        decide.add_argument(
            "--data-dir",
            default=str(REPO_ROOT / "dev"),
            help="对比/采纳记录落盘目录",
        )
        decide.set_defaults(func=handler)

    evidence = sub.add_parser("evidence", help="生成/推翻周期升级判据材料")
    evidence.add_argument("--period", required=True, help="周期标签（如 2026-W38）")
    evidence.add_argument("--period-start", default=None, help="取数窗口起（ISO 日期）")
    evidence.add_argument("--period-end", default=None, help="取数窗口止（ISO 日期）")
    evidence.add_argument(
        "--data-dir",
        default=str(REPO_ROOT / "calibration" / "upgrade-events"),
        help="判据材料落盘根目录（材料落 <根>/dev/<周期>.json，按 agent 分目录）",
    )
    evidence.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    evidence.add_argument("--dsn", default=None, help="PG DSN（用于本周期取数）")
    evidence.add_argument("--override", action="store_true", help="追加推翻留痕（不重产材料）")
    evidence.add_argument("--by", default=None, help="推翻人（--override 时必填）")
    evidence.add_argument("--reason", default=None, help="推翻理由（--override 时必填）")
    evidence.set_defaults(func=_cmd_evidence)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
