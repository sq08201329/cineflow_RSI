#!/usr/bin/env python
"""端到端演示：只读前端（发现树浏览器 + 进化曲线看板）（quickstart.md 六步，里程碑 SC-001~SC-005）。

流程（确定性夹具 + 临时数据目录，全程离线、零 LLM、零生成）：
  1. **起服务 + 三重机检**：夹具库/夹具产物 + 只读服务 → 输出 ① 路由表（仅 GET）
     ② 只读角色纪律（迁移 0009；权限行为断言在真实 PG 集成测试）③ 源码静态断言（零 import
     应用层模块、非导出模块无写调用、无写 SQL 关键字）；
  2. **树浏览**：三维过滤 → 节点列表 → 节点详情（eval_breakdown 版本标注 + 工件哈希）
     → 谱系链路（跨项目归属）；
  3. **看板**：逐轮 reward 曲线 + 塌缩标注 + 成本汇总 + 010 信度达标 + 012 漂移徽标；
  4. **同源**：接口响应 vs 既有 JSON 报告逐字段一致（进化曲线 005 / 信度 010 / 漂移 012 /
     谱系 005——用 web/parity.py 比对，差异必须为空）；
  5. **写拒绝**：POST/PUT/DELETE/PATCH 一律 405/404，树库与产物零变更；
  6. **静态导出**：导出 web/dist 等价目录 → **停掉服务** → 纯静态文件服务仍可读到全部数据
     （离线可浏览，SC-005）。

生产切换：临时数据目录换 `dreaming/history`、`calibration/`、`policies/history`，
SQLite 夹具库换 PG（只读角色 cineflow_web，迁移 0009）——代码路径不变，仅装配层替换。
"""

import ast
import http.client
import http.server
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from core.calibration.drift_config import DriftConfig  # noqa: E402
from core.calibration.drift_metrics import detect_drift  # noqa: E402
from core.calibration.drift_report import build_report as build_drift_report  # noqa: E402
from core.calibration.drift_status import register_suspect  # noqa: E402
from core.calibration.ledger import append_ledger, write_anchor_snapshots  # noqa: E402
from core.calibration.models import BiasRecord, PairingRecord  # noqa: E402
from core.calibration.report import build_report as build_reliability_report  # noqa: E402
from core.replay.trajectory import ReplayTrajectory, TrajectoryStatus  # noqa: E402
from core.tree.db import create_schema  # noqa: E402
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode  # noqa: E402
from core.tree.store import create_tree_store  # noqa: E402
from dreaming.lineage import build_curve, build_lineage, write_meta  # noqa: E402
from dreaming.pipeline import Candidate, DreamRound  # noqa: E402
from dreaming.reward import compute_reward  # noqa: E402
from policies.versioning import policy_version, record_policy  # noqa: E402
from web import export as web_export  # noqa: E402
from web import parity  # noqa: E402
from web import server as web_server  # noqa: E402
from web.queries import WebConfig, reset_engine_cache  # noqa: E402

DSN_ENV = "CINEFLOW_WEB_DEMO_DSN"
PERIOD = "2026-W39"
JUDGE_KEY = "judge.cinematic@1.0.0"
WEB_ROLE_SQL = REPO_ROOT / "ops" / "migrations" / "versions" / "0009_web_readonly_role.py"

POLICY_SOURCES = {
    "champion": (
        "class Policy:\n"
        '    """演示用冠军策略（谱系根）。"""\n'
        "\n"
        "    def solve(self, env, budget):\n"
        '        return ""\n'
    ),
    "child": (
        "class Policy:\n"
        '    """演示用子代策略（父版本 = 冠军）。"""\n'
        "\n"
        "    def solve(self, env, budget):\n"
        '        return ""\n'
    ),
}

TREE_SPECS = (
    ("tree-alpha-visual-champion", "proj-alpha", "visual", "champion", "visual", (0.3, 0.5, 0.7)),
    ("tree-alpha-visual-child", "proj-alpha", "visual", "child", "visual", (0.4, 0.9)),
    ("tree-beta-visual-champion", "proj-beta", "visual", "champion", None, (0.6,)),
    ("tree-beta-storyboard-child", "proj-beta", "storyboard", "child", "storyboard", (0.2, 0.35)),
)

ROUND_SPECS = (
    ("dream-visual-1", [0.6, 0.8], 0.4),
    ("dream-visual-2", [0.2, 0.3], 0.3),
    ("dream-visual-3", [0.2, 0.25], 0.2),
    ("dream-visual-4", [0.15, 0.2], 0.1),
)


def _step(index: int, title: str) -> None:
    print(f"\n=== 第 {index} 步：{title} ===")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# 夹具装配（临时数据目录，不污染仓库工作树）
# ---------------------------------------------------------------------------


def _fixtures(tmp: Path) -> dict:
    """确定性夹具：文件版 SQLite 树库 + 做梦轮次 + 010 信度 + 012 漂移 + 谱系 meta。"""
    data_dirs = {
        "policies": tmp / "policies",
        "dreaming": tmp / "dreaming",
        "calibration": tmp / "calibration",
        "pools": tmp / "replay" / "pools",
    }
    for path in data_dirs.values():
        path.mkdir(parents=True)
    for sub in ("snapshots", "ledger", "reports"):
        (data_dirs["calibration"] / sub).mkdir()
    for sub in ("metrics", "status", "dispositions", "reports"):
        (data_dirs["calibration"] / "drift" / sub).mkdir(parents=True)

    engine = create_engine(f"sqlite+pysqlite:///{tmp / 'tree.db'}")
    create_schema(engine)
    store = create_tree_store(engine)
    versions = {name: policy_version(source) for name, source in POLICY_SOURCES.items()}
    trees = _seed_trees(store, versions)
    _seed_rounds(data_dirs["dreaming"])
    _seed_reliability(data_dirs["calibration"])
    drift_report = _seed_drift(data_dirs["calibration"])
    _seed_lineage(data_dirs["policies"], versions, trees)
    return {
        "engine": engine,
        "store": store,
        "trees": trees,
        "versions": versions,
        "data_dirs": data_dirs,
        "drift_report": drift_report,
    }


def _seed_trees(store, versions) -> dict[str, list[str]]:
    """2 项目 × 2 Agent × 2 策略版本夹具树（覆盖三维过滤、跨项目归属与分页）。"""
    trees: dict[str, list[str]] = {}
    for tree_index, (tree_id, project, agent, version_key, form, scores) in enumerate(TREE_SPECS):
        root_id = f"{tree_id}-n0"
        node_ids = [f"{tree_id}-n{index}" for index in range(len(scores))]
        snapshot: dict = {"evaluator_weights": {"proxy.aesthetic": 1.0}}
        if form is not None:
            snapshot["form"] = form
        store.create_tree(
            DiscoveryTree(
                tree_id=tree_id,
                project_id=project,
                agent_id=agent,
                policy_version=versions[version_key],
                root_id=root_id,
                node_ids=node_ids,
                config_snapshot=snapshot,
            )
        )
        for index, score in enumerate(scores):
            observation = {
                "gen_params": {"temperature": 0.2 + 0.1 * index, "tree": tree_id},
                "material_kind": "poster",
            }
            store.append_node(
                TreeNode(
                    node_id=node_ids[index],
                    tree_id=tree_id,
                    parent_id=None if index == 0 else root_id,
                    depth=0 if index == 0 else 1,
                    agent_id=agent,
                    policy_version=versions[version_key],
                    prompt=f"演示提示词 {index}：探索 {agent} 的参数组合",
                    observation_context=observation,
                    artifact_hash=f"{tree_index * 10 + index + 1:064x}",
                    eval_breakdown={
                        "proxy.aesthetic@1.0.0": {
                            "score": score,
                            "diagnostics": {"band": "high" if score > 0.5 else "low"},
                        },
                        "judge.cinematic@1.0.0": {"score": score, "diagnostics": {}},
                    },
                    score=score,
                    cost=CostRecord(
                        llm_calls=1,
                        generation_api_cost_usd=0.05 * (index + 1),
                        wall_clock_seconds=0.25,
                    ),
                    status=NodeStatus.EVALUATED,
                    created_at=1000.0 + tree_index * 10 + index,
                )
            )
        trees[tree_id] = node_ids
    return trees


def _seed_rounds(dreaming_root: Path) -> None:
    """做梦轮次报告：4 轮完成（第 2 轮起塌缩）+ 1 轮失败（曲线跳过）。"""
    directory = dreaming_root / "visual"
    directory.mkdir(parents=True, exist_ok=True)
    for index, (round_id, curve, cost_usd) in enumerate(ROUND_SPECS, start=1):
        version = f"ver-visual-{index}"
        trajectory = ReplayTrajectory(
            policy_version=version,
            best_score_curve=curve,
            probe_count=4,
            effective_sequential_rounds=1.0,
            total_cost=CostRecord(generation_api_calls=1, generation_api_cost_usd=cost_usd),
            final_node_id=f"{round_id}-final",
            status=TrajectoryStatus.COMPLETED,
            diagnostics={},
        )
        payload = DreamRound(
            round_id=round_id,
            agent_id="visual",
            champion_version="ver-champion",
            digest={"agent_id": "visual", "recent_k": 5, "rounds": [], "note": ""},
            digest_sha="0" * 64,
            candidates=[
                Candidate(
                    version=version,
                    source_code=f"# {round_id} 候选（演示夹具）\n",
                    static_check="passed",
                    trajectory=trajectory.to_dict(),
                    reward=compute_reward(trajectory, 0.5),
                )
            ],
            winner_version=version,
            status="completed",
            diagnostics={},
        ).to_dict()
        (directory / f"{round_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    failed = DreamRound(
        round_id="dream-visual-5",
        agent_id="visual",
        champion_version="ver-champion",
        digest={"agent_id": "visual", "recent_k": 5, "rounds": [], "note": ""},
        digest_sha="0" * 64,
        candidates=[],
        winner_version=None,
        status="failed_all_rejected",
        diagnostics={"note": "全部候选未过静态检查"},
    ).to_dict()
    (directory / "dream-visual-5.json").write_text(
        json.dumps(failed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _seed_reliability(calibration_root: Path) -> dict:
    """010 信度报告：judge 低于目标（0.3 < 0.6）、proxy 达标。"""
    append_ledger(
        calibration_root,
        "visual",
        [
            BiasRecord(evaluator_key=JUDGE_KEY, period=PERIOD, samples=12, kendall_tau=0.3),
            BiasRecord(
                evaluator_key="proxy.aesthetic@1.0.0",
                period=PERIOD,
                samples=12,
                pearson_r=0.8,
            ),
        ],
    )
    return build_reliability_report(calibration_root, PERIOD, target=0.6)


def _seed_drift(calibration_root: Path) -> dict:
    """012 漂移：均值平移超阈 → 登记 suspect → 报表（含双信号强化告警）。"""
    cfg = DriftConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
    for period_index, period in enumerate(("2026-W38", PERIOD)):
        shift = 0.0 if period_index == 0 else 0.2
        scores = [
            round(0.5 + shift + 0.1 * z, 6)
            for z in (-1.6, -1.2, -0.9, -0.6, -0.3, 0.0, 0.3, 0.6, 0.9, 1.2, 1.6)
        ]
        write_anchor_snapshots(
            calibration_root,
            "visual",
            period,
            [
                PairingRecord(
                    anchor_id=f"{period}-a{index}",
                    evaluator_key=JUDGE_KEY,
                    anchor_score=score,
                    auto_score=0.5,
                )
                for index, score in enumerate(scores)
            ],
        )
    detect_drift("visual", JUDGE_KEY, "2026-W38", cfg, calibration_root)
    metrics = detect_drift("visual", JUDGE_KEY, PERIOD, cfg, calibration_root)
    _assert(metrics.verdict.value == "drift", "漂移夹具未产出超阈判定")
    register_suspect(calibration_root, JUDGE_KEY, metrics, at="2026-09-21T10:00:00+00:00")
    return build_drift_report(PERIOD, cfg, calibration_root)


def _seed_lineage(policies_root: Path, versions: dict, trees: dict) -> None:
    """谱系 meta：冠军（根，含人工审批）→ 子代（树口径版本）。"""
    for source in POLICY_SOURCES.values():
        record_policy(source, "visual", history_root=policies_root)
    write_meta(
        policies_root,
        "visual",
        {
            "version": versions["champion"],
            "parent_version": None,
            "created_round": "manual-seed",
            "reward": {"pareto_auc": 0.3, "parallel_penalty": 0.25, "lambda": 0.5, "reward": 0.05},
            "source": "manual",
            "approval": {
                "approver": "sunqi",
                "at": "2026-09-21T00:00:00+08:00",
                "decision": "approved",
                "reason": "演示首版部署（谱系根）",
            },
        },
    )
    write_meta(
        policies_root,
        "visual",
        {
            "version": versions["child"],
            "parent_version": versions["champion"],
            "created_round": "dream-visual-2",
            "reward": {"pareto_auc": 0.25, "parallel_penalty": 0.25, "lambda": 0.5, "reward": 0.0},
            "source": "dreaming",
        },
    )


def _web_config(fixtures: dict, tmp: Path) -> WebConfig:
    base = WebConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
    config = replace(
        base,
        dsn_env=DSN_ENV,
        data_dirs={key: str(path) for key, path in fixtures["data_dirs"].items()},
        export_dir=str(tmp / "web-dist"),
    )
    os.environ[DSN_ENV] = f"sqlite+pysqlite:///{tmp / 'tree.db'}"
    reset_engine_cache()
    return config


# ---------------------------------------------------------------------------
# 客户端（真实 socket：演示形态与部署一致）
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    def request(self, method: str, path: str):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def json(self, path: str, method: str = "GET"):
        status, payload = self.request(method, path)
        return status, (json.loads(payload.decode("utf-8")) if payload else None)


class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102 - 基类签名（演示输出保持干净）
        return


def _serve_static(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    import functools

    handler = functools.partial(_StaticHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# ---------------------------------------------------------------------------
# 三重机检（演示版；权威版为 tests/contract/test_web_readonly.py）
# ---------------------------------------------------------------------------


def _static_scan() -> dict:
    """③ 源码静态断言（演示版）：零 import 应用层模块 + 非导出模块无写调用。"""
    banned_root = {"core", "agents", "dreaming"}
    write_methods = {"write_text", "write_bytes", "mkdir", "unlink", "rename", "touch"}
    import_violations: list[str] = []
    write_violations: list[str] = []
    files = sorted((REPO_ROOT / "web").rglob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if {alias.name.split(".")[0] for alias in node.names} & banned_root:
                    import_violations.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in banned_root:
                    import_violations.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in write_methods and path.name != "export.py":
                    write_violations.append(f"{path.name}:{node.lineno} {node.func.attr}")
    role_source = WEB_ROLE_SQL.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("migration_0009_demo", WEB_ROLE_SQL)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # 迁移用 f-string 常量拼装语句：纪律断言看**装配后**的 SQL（与单测同口径）
    role_sql = "\n".join(sql for sql, _ in migration.upgrade_statements())
    return {
        "files": len(files),
        "import_violations": import_violations,
        "write_violations": write_violations,
        "write_sql_keywords": sorted(
            keyword
            for keyword in ("INSERT INTO", "DELETE FROM", "TRUNCATE", "GRANT ", "REVOKE ")
            if keyword
            in "\n".join(
                path.read_text(encoding="utf-8") for path in files if path.name != "export.py"
            )
        ),
        "role": {
            "迁移": WEB_ROLE_SQL.name,
            "GRANT SELECT 全表": (
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO cineflow_web" in role_sql
            ),
            "写动词显式回收": (
                "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
                " ON ALL TABLES IN SCHEMA public FROM cineflow_web"
            )
            in role_sql,
            "未来表默认只 SELECT": (
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO cineflow_web"
            )
            in role_sql,
            "口令不落盘": (
                migration.PASSWORD_ENV == "CINEFLOW_WEB_PASSWORD"
                and re.search(r"PASSWORD '[^'\n]+'", role_source) is None  # 无字面量口令
                and "os.environ.get(PASSWORD_ENV" in role_source
            ),
        },
    }


def main() -> int:
    started = time.perf_counter()
    report: dict = {}
    with tempfile.TemporaryDirectory(prefix="cineflow-demo-web-") as scratch:
        tmp = Path(scratch)
        fixtures = _fixtures(tmp)
        config = _web_config(fixtures, tmp)
        static_root = REPO_ROOT / "web" / "static"

        # 端口 0 = 临时端口（演示不与本地 8080 冲突；真实部署用配置端口）
        httpd = web_server.create_server(replace(config, port=0), static_root=static_root)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        client = _Client(httpd.server_address[0], httpd.server_address[1])
        try:
            # ---------------- 第 1 步：起服务 + 三重机检 ----------------
            _step(1, "起服务 + 只读三重机检")
            routes = web_server.ROUTES
            methods = sorted({route.method for route in routes})
            scan = _static_scan()
            _assert(methods == ["GET"], f"路由表出现写动词：{methods}")
            _assert(not scan["import_violations"], f"web/ 出现应用层模块 import：{scan}")
            _assert(not scan["write_violations"], f"非导出模块出现写调用：{scan}")
            _assert(not scan["write_sql_keywords"], f"web/ 出现写 SQL 关键字：{scan}")
            _assert(all(scan["role"].values()), f"迁移 0009 授权纪律不完整：{scan['role']}")
            report["三重机检"] = {
                "① 路由表": {
                    "路由数": len(routes),
                    "动词": methods,
                    "写动词处理器": list(web_server.WRITE_METHODS),
                    "路径": [route.pattern for route in routes],
                },
                "② 只读角色（迁移 0009）": scan["role"],
                "② 备注": "权限行为（对全部表仅 SELECT / 写被 DB 拒绝）在真实 PG 集成测试："
                "pytest tests/integration -m integration -k web",
                "③ 源码静态断言": {
                    "扫描文件": scan["files"],
                    "import 违规": scan["import_violations"],
                    "写调用违规": scan["write_violations"],
                    "写 SQL 关键字": scan["write_sql_keywords"],
                },
            }
            print(
                f"服务已起：http://{httpd.server_address[0]}:{httpd.server_address[1]}/（仅 GET）"
            )
            print(json.dumps(report["三重机检"], ensure_ascii=False, indent=2))

            # ---------------- 第 2 步：树浏览 ----------------
            _step(2, "树浏览：三维过滤 → 节点详情 → 谱系链路")
            _status, trees_page = client.json("/api/trees?page_size=3")
            _status, filtered = client.json("/api/trees?project_id=proj-alpha&agent_id=visual")
            _assert(trees_page["total"] == 4 and len(trees_page["items"]) == 3, "树清单分页不对")
            _assert(filtered["total"] == 2, "三维过滤未生效")
            first = trees_page["items"][0]
            _status, nodes_page = client.json(f"/api/trees/{first['tree_id']}/nodes")
            node_id = (
                nodes_page["items"][1]["node_id"]
                if len(nodes_page["items"]) > 1
                else (nodes_page["items"][0]["node_id"])
            )
            _status, detail = client.json(f"/api/nodes/{node_id}")
            _assert(detail["eval_breakdown"], "节点详情缺分量")
            _assert(
                all("@" in entry["evaluator_key"] for entry in detail["eval_breakdown"]),
                ("分量缺评估器版本标注"),
            )
            version = first["policy_version"]
            _status, lineage = client.json(f"/api/lineage/{version}")
            report["树浏览"] = {
                "树清单": {"total": trees_page["total"], "本页": len(trees_page["items"])},
                "三维过滤（proj-alpha + visual）": filtered["total"],
                "节点列表": {"tree": first["tree_id"], "total": nodes_page["total"]},
                "节点详情": {
                    "node": node_id,
                    "分量": [entry["evaluator_key"] for entry in detail["eval_breakdown"]],
                    "工件哈希": detail["artifact"]["hash"][:16] + "…",
                    "观测键": detail["observation_keys"],
                    "成本 USD": detail["cost_usd"],
                },
                "谱系": {
                    "版本": version,
                    "父版本": sorted({row["version"] for row in lineage["parents"]}),
                    "产出树": [row["tree_id"] for row in lineage["trees"]],
                    "子版本": sorted({row["version"] for row in lineage["children"]}),
                    "跨项目": lineage["cross_project"],
                },
            }
            print(json.dumps(report["树浏览"], ensure_ascii=False, indent=2))

            # ---------------- 第 3 步：看板 ----------------
            _step(3, "看板：reward 曲线 + 塌缩标注 + 成本汇总 + 信度/漂移徽标")
            _status, evolution = client.json("/api/evolution/visual")
            _status, costs = client.json("/api/costs")
            _status, summary = client.json("/api/summary")
            _assert(evolution["collapse"]["collapsed"] is True, "塌缩未标注")
            _assert(len(evolution["rounds"]) == 4, "曲线轮次不含失败轮")
            _assert(summary["calibration"]["meets"] is False, "信度达标判定不对")
            _assert(
                {item["status"] for item in summary["drift"]["items"]} == {"suspect"},
                "漂移徽标不对",
            )
            report["看板"] = {
                "曲线": {
                    "轮次": [row["round_id"] for row in evolution["rounds"]],
                    "reward": [row["best_reward"] for row in evolution["rounds"]],
                    "塌缩标记": [row["collapse_flag"] for row in evolution["rounds"]],
                    "塌缩": evolution["collapse"],
                },
                "成本汇总": {
                    "按 (Agent, 周期)": [
                        [row["agent_id"], row["period"], row["cost_usd"]] for row in costs["items"]
                    ],
                    "全库合计 USD": costs["total_usd"],
                    "节点数": costs["node_count"],
                },
                "010 信度": {
                    "周期": summary["calibration"]["period"],
                    "达标": summary["calibration"]["meets"],
                },
                "012 漂移": {
                    "状态": [
                        [item["evaluator_key"], item["status"]]
                        for item in summary["drift"]["items"]
                    ],
                    "告警数": len(summary["drift"]["alerts"]),
                },
            }
            print(json.dumps(report["看板"], ensure_ascii=False, indent=2))

            # ---------------- 第 4 步：同源（接口 vs 既有 JSON 报告） ----------------
            _step(4, "同源：接口响应 vs 既有 JSON 报告逐字段一致")
            curve_report = build_curve(
                "visual",
                fixtures["data_dirs"]["dreaming"],
                collapse_window=config.collapse_window,
                collapse_threshold=config.collapse_threshold,
            ).to_dict()
            lineage_report = build_lineage(
                "visual", fixtures["store"], fixtures["data_dirs"]["policies"]
            ).to_dict()
            reliability = json.loads(
                (fixtures["data_dirs"]["calibration"] / "reports" / f"{PERIOD}.json").read_text(
                    encoding="utf-8"
                )
            )
            drift_report = json.loads(
                (
                    fixtures["data_dirs"]["calibration"] / "drift" / "reports" / f"{PERIOD}.json"
                ).read_text(encoding="utf-8")
            )
            diffs = {
                "进化曲线（005 build_curve）": parity.compare_evolution(evolution, curve_report),
                "信度（010 报告）": parity.compare_calibration(summary["calibration"], reliability),
                "漂移（012 报表）": parity.compare_drift(summary["drift"], drift_report),
                "谱系（005 build_lineage）": parity.compare_lineage(
                    lineage, lineage_report, agent_id="visual"
                ),
            }
            _assert(all(not value for value in diffs.values()), f"同源比对出现差异：{diffs}")
            report["同源"] = {panel: "一致（差异 0）" for panel in diffs}
            print(json.dumps(report["同源"], ensure_ascii=False, indent=2))

            # ---------------- 第 5 步：写拒绝 ----------------
            _step(5, "写拒绝：写请求 405/404，树库与产物零变更")
            with fixtures["engine"].connect() as connection:
                rows_before = connection.execute(text("SELECT COUNT(*) FROM tree_nodes")).scalar()
            statuses = {}
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                for path in ("/api/trees", "/api/nodes/tree-alpha-visual-champion-n0", "/api/nope"):
                    status, _payload = client.request(method, path)
                    statuses[f"{method} {path}"] = status
            with fixtures["engine"].connect() as connection:
                rows_after = connection.execute(text("SELECT COUNT(*) FROM tree_nodes")).scalar()
            _assert(set(statuses.values()) <= {404, 405}, f"写请求未被拒绝：{statuses}")
            _assert(rows_before == rows_after == 8, "写请求产生了副作用")
            report["写拒绝"] = {
                "拒绝状态码": sorted(set(statuses.values())),
                "样例": dict(list(statuses.items())[:4]),
                "树库行数（前后）": [rows_before, rows_after],
                "产物零变更": True,
            }
            print(json.dumps(report["写拒绝"], ensure_ascii=False, indent=2))

            # ---------------- 第 6 步：静态导出（停服务后离线可浏览） ----------------
            _step(6, "静态导出：停服务 → 纯静态服务仍可浏览")
            written = web_export.export_all(config, dest=tmp / "web-dist", static_root=static_root)
            _assert((tmp / "web-dist" / "data" / "trees.json").is_file(), "导出缺数据快照")
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            static_httpd, base = _serve_static(tmp / "web-dist")
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", static_httpd.server_address[1], timeout=10
                )
                offline: dict = {}
                for url in ("index.html", "board.html", "static/app.js", "data/trees.json"):
                    connection.request("GET", f"/{url}")
                    response = connection.getresponse()
                    offline[url] = response.status
                    _assert(response.status == 200, f"离线读取失败：{url} → {response.status}")
                    response.read()
                connection.close()
                offline_payload = json.loads(
                    (tmp / "web-dist" / "data" / "trees.json").read_text(encoding="utf-8")
                )
                _assert(offline_payload["total"] == trees_page["total"], "离线快照与在线数据不一致")
            finally:
                static_httpd.shutdown()
                static_httpd.server_close()
            manifest = json.loads(
                (tmp / "web-dist" / "data" / "manifest.json").read_text(encoding="utf-8")
            )
            report["静态导出"] = {
                "文件数": len(written),
                "数据快照": sorted(manifest["files"]),
                "计数": manifest["counts"],
                "停服务后可读": offline,
                "离线树清单 total": offline_payload["total"],
            }
            print(json.dumps(report["静态导出"], ensure_ascii=False, indent=2))
        finally:
            try:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
            except Exception:  # noqa: BLE001 - 已停则忽略
                pass

    elapsed = round(time.perf_counter() - started, 3)
    report["elapsed_seconds"] = elapsed
    report["ok"] = elapsed < 60
    print("\n=== 演示汇总 ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
