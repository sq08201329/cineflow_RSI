#!/usr/bin/env python
"""剧本 Agent CLI（功能 009）：produce / submit 子命令。

- submit：提交人工策略版本（C13）——静态检查（002）与接口签名（plan(inputs, config)）
  均过才落 `policies/history/screenplay/{version}.py` + meta.json；`--draft` 只校验算版本
  不入历史；同源码重提幂等（同版本号）；
- produce：按人工策略跑一轮分阶段产出（outline → scenes → script）——策略源码过
  002 静态检查后执行、版本 = 源码 BLAKE3 前 12 位（节点可回溯到产出它的策略版本）；
  逐阶段经 LLM 网关生成、成本入账、节点一次性落发现树；工件内容寻址入
  `<data-dir>/artifacts`；结果 JSON 打到 stdout。评估器 = 真实七评估器装配
  （四 gate + 两代理 + judge 仅大纲阶段，`build_screenplay_evaluators` 唯一装配点）。

CLI 默认面向 PG 库（--dsn 或 CINEFLOW_PG_DSN，schema 由 Alembic 迁移管理，CLI 不隐式
改 PG schema）；SQLite DSN（测试/本地）自动建表。`--policy-dir` 为**策略历史根目录**
（默认 `policies/history`；版本源码位于 `<policy-dir>/screenplay/{version}.py`）。
人工策略版本核验：`--policy <version>` 读该路径并校验源码哈希与版本一致（不符即拒绝）；
`--policy-file` 按源码哈希派生版本（本地验证用）。

退出码：0 成功；2 参数/依赖/策略拒绝（缺 DSN、策略未过静态检查、版本不符等）；1 运行期
错误（输入预检拒绝、评估器装配失败等）。后续 submit/compare/adopt/reject/evidence
子命令在 T931 扩展。
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

POLICY_VERSION_LENGTH = 12  # 版本 = 源码 BLAKE3 前 12 位（FR-009/FR-015 口径）
AGENT_ID = "screenplay"


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
    """加载人工策略：静态检查 → 版本核验/派生 → 实例化（版本绑定到策略对象）。

    静态检查是沙箱第一道防线（002）：未过检查不得进入产出。版本核验保证
    "策略版本 = 源码内容"（人工版本同样可回溯、可机检）。
    """
    import blake3

    from policies.static_check import StaticCheckError, check_policy_source

    path, declared, locate_error = _policy_source_path(args)
    if path is None:
        return None, None, locate_error
    source = path.read_text(encoding="utf-8")
    try:
        check_policy_source(source)
    except StaticCheckError as exc:
        return None, None, f"策略静态检查未通过：{exc}"
    version = blake3.blake3(source.encode()).hexdigest()[:POLICY_VERSION_LENGTH]
    if declared and declared != version:
        return (
            None,
            None,
            f"策略版本 {declared!r} 与源码内容不符（源码哈希前 {POLICY_VERSION_LENGTH} 位为 "
            f"{version!r}）——版本必须等于源码 BLAKE3 前 {POLICY_VERSION_LENGTH} 位",
        )
    namespace: dict = {"__name__": "screenplay_policy"}
    try:
        exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - 已过静态检查
    except Exception as exc:  # noqa: BLE001 - 源码执行失败即拒绝（不进入产出）
        return None, None, f"策略源码执行失败：{exc}"
    policy_class = namespace.get("Policy")
    if not isinstance(policy_class, type):
        return None, None, f"策略源码缺少 Policy 类：{path}"
    policy = policy_class()
    policy.policy_version = version  # 节点与运营表落盘口径（人工策略版本）
    try:
        has_plan = callable(policy.plan)
    except AttributeError:
        has_plan = False
    if not has_plan:
        return None, None, f"策略缺少 plan(inputs, config) 接口：{path}"
    return policy, str(path), ""


def _cmd_produce(args) -> int:
    from sqlalchemy import create_engine

    from agents.screenplay.config import ScreenplayConfig, ScreenplayConfigError
    from agents.screenplay.db import create_jobs_schema
    from agents.screenplay.evaluators import build_screenplay_evaluators
    from agents.screenplay.loop import ScreenplayLoopError, run_screenplay_round
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
        config = ScreenplayConfig.from_yaml(args.config)
    except ScreenplayConfigError as exc:
        return _fail(f"形态配置非法：{exc}", 2)
    try:
        backend = _backend(args)
    except GatewayError as exc:
        return _fail(f"网关后端不可用：{exc}", 2)
    inputs = {
        "topic": args.topic,
        "constraints": list(args.constraints or []),
        "characters": list(args.characters or []),
    }
    if args.target_duration_min is not None:
        inputs["target_duration_min"] = args.target_duration_min

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
    gateway = LLMGateway(backend, price_book=config.model_prices)

    try:
        evaluators = build_screenplay_evaluators(config, gateway)
    except ScreenplayLoopError as exc:
        return _fail(str(exc), 1)
    except ScreenplayConfigError as exc:
        return _fail(f"评估器装配失败：{exc}", 2)

    try:
        result = run_screenplay_round(
            round_id=args.round,
            policy=policy,
            store=store,
            artifacts=artifacts,
            engine=engine,
            gateway=gateway,
            config=config,
            inputs=inputs,
            evaluators=evaluators,
        )
    except ScreenplayLoopError as exc:
        return _fail(str(exc), 1)

    payload = result.to_dict()
    payload["policy_source"] = policy_path
    payload["data_dir"] = str(data_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_submit(args) -> int:
    from agents.screenplay.policy_versions import PolicySubmissionError, submit_policy
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


def _artifact_store(data_dir: Path):
    """工件存储装配：本地内容寻址目录（生产装配层换 S3ArtifactStore，接口不变）。"""
    from core.tree.artifacts import LocalArtifactStore

    return LocalArtifactStore(data_dir / "artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="剧本 Agent（降级模式）：分阶段产出与治理")
    sub = parser.add_subparsers(dest="command", required=True)

    produce = sub.add_parser("produce", help="按人工策略跑一轮分阶段产出并落树")
    produce.add_argument("--round", required=True, help="轮次 ID（幂等键）")
    produce.add_argument("--topic", required=True, help="题材（必填，非空）")
    produce.add_argument(
        "--constraints", nargs="*", default=[], help="题材约束（多个值以空格分隔）"
    )
    produce.add_argument("--characters", nargs="*", default=[], help="角色设定（多个值）")
    produce.add_argument(
        "--target-duration-min", type=int, default=None, help="目标时长分钟，默认取配置"
    )
    produce.add_argument("--policy", default=None, help="人工策略版本（读 <policy-dir>/{版本}.py）")
    produce.add_argument("--policy-file", default=None, help="人工策略源码路径（本地验证用）")
    produce.add_argument(
        "--policy-dir",
        default=str(REPO_ROOT / "policies" / "history"),
        help="策略历史根目录（版本源码位于 <policy-dir>/screenplay/{version}.py）",
    )
    produce.add_argument("--config", default=str(REPO_ROOT / "configs" / "movie.yaml"))
    produce.add_argument("--data-dir", default=str(REPO_ROOT / "screenplay"))
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
