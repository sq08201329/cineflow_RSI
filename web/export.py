"""静态导出：把当前数据面导出为可离线浏览的静态资产（导出目录由 `web.export_dir` 配置）。

导出与服务**共用同一查询层**（`web/queries.py`）——预生成 JSON + 资产拷贝，杜绝
"看板一套数、导出另一套数"的双口径风险；导出目录脱离服务可直接浏览。

本模块是 `web/` 内**唯一**允许写盘的模块，且写入必须满足两条约束（见
`tests/contract/test_web_readonly.py` 的静态断言与运行时兜底）：

1. **静态**：每处写调用都位于带 `dest` 参数（导出根目录）的函数内，且写目标表达式由
   `dest` 派生（本模块统一走 `_write_text()` / `_write_bytes()` 两个写助手）；
2. **运行时**：目标路径一律经 `_target(dest, relative)` 解析——越出导出根即报错，
   拒绝路径穿越。

导出形态：

```
{dest}/
├── index.html        树浏览器（注入 window.CINEFLOW_STATIC = true → 页面改读 data/*.json）
├── board.html        进化看板（同上）
├── static/           app.js / style.css（与在线服务同一套资产，逐字节拷贝）
└── data/             facets / trees / nodes / node_details / lineage / evolution / costs /
                      summary / manifest.json（全部由同一查询层产出）
```

离线浏览：用任意静态服务器挂载导出目录（`python -m http.server -d {dest}`）后打开
index.html / board.html 即可；直接以 `file://` 打开时浏览器会以同源策略拦截 fetch
（浏览器行为，不是本特性约束）。导出快照有节点数上限（`max_nodes`，默认 2000），
被截断时在 manifest 中如实列出，不静默丢数据。
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from web import queries
from web.queries import WebConfig

DEFAULT_MAX_NODES = 2000
MANIFEST_NAME = "manifest.json"
PAGE_FILES = ("index.html", "board.html")
ASSET_FILES = ("app.js", "style.css")
STATIC_FLAG = "<script>window.CINEFLOW_STATIC = true;</script>"


def _target(dest: str | Path, relative: str) -> Path:
    """导出目标路径：解析后必须仍在导出根目录内（拒绝路径穿越）。"""
    root = Path(dest).resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"导出路径越出导出目录：{relative!r}（拒绝路径穿越）")
    return target


def _write_text(dest: str | Path, relative: str, text: str) -> Path:
    """写文本（唯一文本写入口：目标经 `_target` 包含性校验）。"""
    target = _target(dest, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _write_bytes(dest: str | Path, relative: str, payload: bytes) -> Path:
    """写字节（唯一字节写入口：目标经 `_target` 包含性校验）。"""
    target = _target(dest, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _write_json(dest: str | Path, relative: str, payload: dict) -> Path:
    """写 JSON（紧凑确定性序列化：同数据两次导出逐字节一致）。"""
    return _write_text(dest, relative, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _decorate_page(html: str) -> str:
    """页面注入静态模式标记（离线快照：页面改读 data/*.json，不触碰 /api/*）。"""
    marker = '<script src="static/app.js"></script>'
    if marker not in html:
        raise ValueError("页面缺少 app.js 引用（导出模板与页面不匹配）")
    return html.replace(marker, f"    {STATIC_FLAG}\n    {marker}")


# ---------------------------------------------------------------------------
# 快照组装（全部经 web/queries.py：同一查询层，不另建取数口径）
# ---------------------------------------------------------------------------


def _snapshot_trees(config: WebConfig) -> dict:
    """全量树快照：按配置页长逐页取尽（组装成单文件，供离线过滤/分页）。"""
    items: list[dict] = []
    total = 0
    page = 1
    while True:
        payload = queries.list_trees(config, page=page, page_size=config.page_size)
        total = payload["total"]
        items += payload["items"]
        if not payload["items"] or len(items) >= total:
            break
        page += 1
    return {
        "items": items,
        "total": total,
        "page": 1,
        "page_size": len(items),
        "note": (
            "全量快照（离线浏览用）；在线接口按 web.page_size 分页，page/page_size 为快照元信息"
        ),
    }


def _snapshot_nodes(config: WebConfig, trees: dict, max_nodes: int) -> tuple[dict, list[str]]:
    """节点列表快照：逐树取尽节点（总量受 max_nodes 限制，超限的树如实记入 truncated）。"""
    trees_payload: dict[str, dict] = {}
    node_ids: list[str] = []
    truncated: list[str] = []
    for item in trees["items"]:
        tree_id = item["tree_id"]
        if len(node_ids) >= max_nodes:
            truncated.append(tree_id)
            continue
        page = 1
        collected: list[dict] = []
        total = 0
        while True:
            payload = queries.list_nodes(config, tree_id, page=page, page_size=config.page_size)
            if payload is None:
                break
            total = payload["total"]
            collected += payload["items"]
            if not payload["items"] or len(collected) >= total:
                break
            page += 1
        remaining = max_nodes - len(node_ids)
        if len(collected) > remaining:  # 单棵树自身超限：如实截断（记入 truncated）
            collected = collected[:remaining]
            truncated.append(tree_id)
        trees_payload[tree_id] = {
            "items": collected,
            "total": total,
            "truncated": len(collected) < total,
        }
        node_ids += [row["node_id"] for row in collected]
    return {"trees": trees_payload, "node_count": len(node_ids)}, truncated


def _snapshot_node_details(config: WebConfig, nodes: dict) -> dict:
    """节点详情快照：逐节点取详情（与 /api/nodes/{node_id} 同形的投影）。"""
    details: dict[str, dict] = {}
    for entry in nodes["trees"].values():
        for row in entry["items"]:
            detail = queries.get_node(config, row["node_id"])
            if detail is not None:
                details[row["node_id"]] = detail
    return {"nodes": details}


def _snapshot_lineage(config: WebConfig) -> dict:
    """谱系快照：版本全集（meta ∪ 树上）的逐版本谱系链路。"""
    versions: dict[str, dict] = {}
    for version in queries.list_lineage_versions(config):
        payload = queries.get_lineage(config, version)
        if payload is not None:
            versions[version] = payload
    return {"versions": versions}


def _file_only_agents(config: WebConfig) -> list[str]:
    """仅按文件枚举分线（dreaming/history 目录）——DB 不可用时的降级口径（不触碰 DB）。"""
    history_root = config.data_dir("dreaming")
    if not history_root.is_dir():
        return []
    return sorted(path.name for path in history_root.iterdir() if path.is_dir())


def _snapshot_evolution(config: WebConfig, *, agents: list[str] | None = None) -> dict:
    """曲线快照：分线全集（dreaming/history 目录 ∪ 树上 Agent）的逐 Agent 曲线。

    曲线本身读文件（不依赖 DB）；`agents` 给定时按该清单取（DB 不可用时的降级路径）。
    """
    payload: dict[str, dict] = {}
    for agent_id in queries.list_dreaming_agents(config) if agents is None else agents:
        payload[agent_id] = queries.get_evolution(config, agent_id)
    return {"agents": payload}


def _data_payloads(
    config: WebConfig, max_nodes: int
) -> tuple[dict[str, dict], dict, list[str], str, str | None]:
    """组装全部数据快照；DB 不可用时**降级**（树相关快照为空 + 如实标注，文件面板照常）。"""
    db_state = "up"
    db_note = None
    truncated: list[str] = []
    try:
        trees = _snapshot_trees(config)
        nodes, truncated = _snapshot_nodes(config, trees, max_nodes)
        facets = queries.list_facets(config)
        lineage = _snapshot_lineage(config)
        costs = queries.get_costs(config)
        node_details = _snapshot_node_details(config, nodes)
        evolution = _snapshot_evolution(config)
    except queries.DatabaseUnavailableError as exc:
        # 与只读服务同款韧性：DB 不可用时服务照常起（树接口 503），导出照常完成（树快照为空）
        db_state = "down"
        db_note = f"只读库不可用，树相关快照为空（如实标注，非静默）：{exc}"
        trees = {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 0,
            "note": db_note,
        }
        nodes = {"trees": {}, "node_count": 0}
        facets = {"projects": [], "agents": [], "policy_versions": [], "forms": []}
        lineage = {"versions": {}}
        costs = {"items": [], "agents": [], "periods": [], "total_usd": 0.0, "node_count": 0}
        node_details = {"nodes": {}}
        evolution = _snapshot_evolution(config, agents=_file_only_agents(config))
    payloads = {
        "facets.json": facets,
        "trees.json": trees,
        "nodes.json": nodes,
        "node_details.json": node_details,
        "lineage.json": lineage,
        "evolution.json": evolution,
        "costs.json": costs,
        "summary.json": queries.get_summary(config),
    }
    counts = {
        "trees": trees["total"],
        "nodes": nodes["node_count"],
        "agents": len(payloads["evolution.json"]["agents"]),
        "versions": len(payloads["lineage.json"]["versions"]),
    }
    return payloads, counts, truncated, db_state, db_note


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def export_all(
    config: WebConfig,
    *,
    dest: str | Path | None = None,
    static_root: str | Path | None = None,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Path]:
    """导出静态资产 + 数据快照到导出目录；返回 {相对路径: 绝对路径}。

    - 页面：index.html / board.html（注入静态模式标记）+ static/{app.js,style.css} 逐字节拷贝；
    - 数据：`data/*.json`（全部经 `web/queries.py`，与在线接口逐字段一致）；
    - 清单：`data/manifest.json`（计数、上限、被截断的树、导出时间与形态说明）。
    """
    root = Path(config.export_dir) if dest is None else Path(dest)
    default_static = Path(__file__).resolve().parent / "static"
    static = default_static if static_root is None else Path(static_root)
    payloads, counts, truncated, db_state, db_note = _data_payloads(config, max_nodes)

    written: dict[str, Path] = {}
    for page in PAGE_FILES:
        source = static / page
        if not source.is_file():
            raise FileNotFoundError(f"导出缺页面资产：{source}")
        written[page] = _write_text(root, page, _decorate_page(source.read_text(encoding="utf-8")))
    for asset in ASSET_FILES:
        source = static / asset
        if not source.is_file():
            raise FileNotFoundError(f"导出缺静态资产：{source}")
        written[f"static/{asset}"] = _write_bytes(root, f"static/{asset}", source.read_bytes())
    for name, payload in payloads.items():
        written[f"data/{name}"] = _write_json(root, f"data/{name}", payload)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "export_dir": str(root),
        "data_dirs": dict(config.data_dirs),
        "files": sorted([*written, f"data/{MANIFEST_NAME}"]),
        "counts": counts,
        "max_nodes": max_nodes,
        "truncated": sorted(truncated),
        "db": db_state,
        "db_note": db_note,
        "note": (
            "只读快照（web/export.py 产出）：静态资产 + 同一查询层预生成 JSON；"
            "用任意静态服务器挂载本目录即可离线浏览两视图（file:// 下浏览器禁止 fetch）"
        ),
    }
    written[f"data/{MANIFEST_NAME}"] = _write_json(root, f"data/{MANIFEST_NAME}", manifest)
    if db_state != "up":
        sys.stderr.write(f"[export] 注意：{db_note}\n")
    return written


def main(argv: list[str] | None = None) -> int:
    """CLI：`python -m web.export [--config configs/movie.yaml] [--dest web/dist]`。"""
    import argparse

    parser = argparse.ArgumentParser(description="CineFlow 静态导出（同一查询层快照）")
    parser.add_argument("--config", default="configs/movie.yaml", help="形态配置文件路径")
    parser.add_argument("--dest", default=None, help="导出目录（缺省用配置 web.export_dir）")
    parser.add_argument(
        "--max-nodes", type=int, default=DEFAULT_MAX_NODES, help="节点详情快照上限（默认 2000）"
    )
    args = parser.parse_args(argv)
    try:
        config = WebConfig.from_yaml(args.config)
        written = export_all(config, dest=args.dest, max_nodes=args.max_nodes)
    except Exception as exc:  # noqa: BLE001 - CLI 失败即非零退出并说明原因
        sys.stderr.write(f"[export] 导出失败：{exc}\n")
        return 1
    target = Path(config.export_dir) if args.dest is None else Path(args.dest)
    sys.stderr.write(f"[export] 已导出 {len(written)} 个文件到 {target}（只读快照）\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
