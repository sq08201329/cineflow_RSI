"""只读查询层：发现树只读 SQL + 文件化产物只读读取（同一查询层的唯一取数实现）。

**依赖纪律**（宪章原则五新条款）：本模块零 import core/agents/dreaming——表结构按迁移
0001 的列名用只读 SQL 对接（`discovery_trees` / `tree_nodes`，SQL 只有 SELECT），文件化
产物按既有落盘 schema 直接解析（policies/history 谱系 meta、dreaming/history 轮次报告、
calibration 的 010 信度报告与 012 漂移状态/报表）。任何取数都不重算口径。

**字段语义与 001 落库口径一致**（不另立口径）：

- `node_count` = `discovery_trees.node_ids` 长度（树自报的冻结节点清单，不另点数）；
- `cost_usd` = 成本记录的 `generation_api_cost_usd`（与各 Agent 的 `tree_total` 对账字段同口径）；
- `created_at` = 001 的 Float 时间戳；树无时间列，故树清单的 `created_at` 取**根节点**时间戳
  （建树时刻的等价表示），根节点缺失如实为 `null`；
- `form` = `config_snapshot["form"]`（011 树自报形态；未标注为 `null`，不推断）；
- 谱系按 (版本, 项目) 展开行（跨项目归属取 011 的树级 `project_id` 口径）；
- 曲线/塌缩：round/winner/reward 与 005 同源（读轮次报告），塌缩标注按配置的
  `dreaming.collapse_window` / `dreaming.collapse_threshold` 复现 005 的同一判定规则
  （连续 window 轮 < 首轮基线 × threshold），逐字段一致性由 tests/contract/test_web_parity.py
  对 005 的判定结果机检。

**连接**：惰性——首个需要 DB 的请求才建引擎（按 DSN 缓存复用）；DSN 取自配置的环境变量名
（只读角色 `cineflow_web`，口令不落盘）。DB 侧任何失败（未配置 DSN / 连不上 / 未迁移）抛
`DatabaseUnavailableError`，服务侧映射 503 + 明确说明；文件化面板不受影响。
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

# 四类文件化产物目录（键即配置 `web.data_dirs` 的键集）
WEB_DATA_DIR_KEYS = ("policies", "dreaming", "calibration", "pools")

_COST_FIELDS = (
    "llm_calls",
    "llm_tokens",
    "generation_api_calls",
    "generation_api_cost_usd",
    "human_review_minutes",
    "wall_clock_seconds",
)

# 工件元信息键的前缀口径：001 无工件元信息列，元信息键取自观测白名单中工件相关键
# （原则四：只给键不给值；无匹配即空列表，不推断）
_ARTIFACT_METADATA_PREFIXES = ("material_", "artifact_")

# WHERE 子句按"实际给出的过滤条件"拼装（不写恒真式）：既让两种方言都用上索引，也避开
# "未类型化 NULL 参数"在 PostgreSQL 上的歧义（实测 AmbiguousParameter: 无法确定参数类型）。
# 拼入的只有本模块固定的列名，参数一律走绑定参数。
_TREE_SELECT_SQL = """
SELECT t.tree_id, t.project_id, t.agent_id, t.policy_version, t.node_ids, t.config_snapshot,
       r.created_at AS root_created_at
  FROM discovery_trees AS t
  LEFT JOIN tree_nodes AS r ON r.node_id = t.root_id
"""

_TREE_ORDER_SQL = (
    " ORDER BY r.created_at DESC NULLS LAST, t.tree_id ASC LIMIT :limit OFFSET :offset"
)

_TREES_ALL_SQL = (
    "SELECT t.tree_id, t.project_id, t.agent_id, t.policy_version FROM discovery_trees AS t"
    " ORDER BY t.tree_id ASC"
)

_COST_SQL = "SELECT n.agent_id, n.created_at, n.cost FROM tree_nodes AS n"

_FACETS_SQL = (
    "SELECT t.project_id, t.agent_id, t.policy_version, t.config_snapshot"
    " FROM discovery_trees AS t ORDER BY t.tree_id ASC"
)

_NODE_SELECT_SQL = """
SELECT n.node_id, n.parent_id, n.depth, n.score, n.cost, n.status, n.created_at
  FROM tree_nodes AS n
"""

_NODE_ORDER_SQL = (
    " ORDER BY n.depth ASC, n.created_at ASC, n.node_id ASC LIMIT :limit OFFSET :offset"
)


def _where_clause(clauses: Sequence[tuple[str, str, Any]]) -> tuple[str, dict]:
    """(列, 参数名, 值) 三元组 → (WHERE 子句, 参数映射)：值为 None 的条件不拼入。"""
    conditions: list[str] = []
    params: dict = {}
    for column, name, value in clauses:
        if value is None:
            continue
        conditions.append(f"{column} = :{name}")
        params[name] = value
    if not conditions:
        return "", params
    return " WHERE " + " AND ".join(conditions), params


_NODE_DETAIL_SQL = """
SELECT node_id, tree_id, parent_id, depth, prompt, observation_context, artifact_hash,
       eval_breakdown, score, cost, status, created_at
  FROM tree_nodes WHERE node_id = :node_id
"""

_TREE_EXISTS_SQL = "SELECT COUNT(*) AS total FROM discovery_trees WHERE tree_id = :tree_id"


class WebQueryError(Exception):
    """查询层参数/配置错误（服务侧映射 400）。"""


class DatabaseUnavailableError(WebQueryError):
    """DB 不可用（未配置 DSN / 连不上 / 表缺失）——只影响树相关接口（服务侧映射 503）。"""


class DataSourceError(WebQueryError):
    """产物文件损坏或 schema 不符（不静默：如实报错，服务侧映射 500）。"""


@dataclass(frozen=True)
class WebConfig:
    """web 配置段（`configs/movie.yaml` 的 `web` 段 + 同文件的 dreaming 塌缩口径）。"""

    host: str
    port: int
    dsn_env: str
    token: str
    page_size: int
    data_dirs: dict[str, str]
    export_dir: str
    collapse_window: int
    collapse_threshold: float

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WebConfig":
        source = Path(path)
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise WebQueryError(f"配置文件不可读：{source}（{exc}）") from exc
        if not isinstance(payload, Mapping):
            raise WebQueryError(f"配置文件必须为映射：{source}")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping) -> "WebConfig":
        section = payload.get("web")
        if not isinstance(section, Mapping):
            raise WebQueryError("配置缺 web 段（只读前端全部参数在该段内，缺即报错）")
        for field_name in (
            "host",
            "port",
            "dsn_env",
            "token",
            "page_size",
            "data_dirs",
            "export_dir",
        ):
            if field_name not in section:
                raise WebQueryError(f"web 段缺字段 {field_name!r}")
        dreaming = payload.get("dreaming") or {}
        if not isinstance(dreaming, Mapping):
            raise WebQueryError("dreaming 段必须为映射（塌缩标注口径来源）")
        for field_name in ("collapse_window", "collapse_threshold"):
            if field_name not in dreaming:
                raise WebQueryError(f"dreaming 段缺字段 {field_name!r}（塌缩标注口径）")

        host = section["host"]
        if not isinstance(host, str) or not host:
            raise WebQueryError(f"host 必须为非空字符串，实际为 {host!r}")
        port = section["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise WebQueryError(
                f"port 必须为 0~65535 的整数（0 = 临时端口，测试用），实际为 {port!r}"
            )
        dsn_env = section["dsn_env"]
        if not isinstance(dsn_env, str) or not dsn_env:
            raise WebQueryError(f"dsn_env 必须为非空字符串，实际为 {dsn_env!r}")
        token = section["token"]
        if not isinstance(token, str):
            raise WebQueryError(f"token 必须为字符串（空串 = 仅本机语义），实际为 {token!r}")
        page_size = section["page_size"]
        if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
            raise WebQueryError(f"page_size 必须为 ≥ 1 的整数，实际为 {page_size!r}")
        export_dir = section["export_dir"]
        if not isinstance(export_dir, str) or not export_dir:
            raise WebQueryError(f"export_dir 必须为非空字符串，实际为 {export_dir!r}")
        raw_dirs = section["data_dirs"]
        if not isinstance(raw_dirs, Mapping) or set(raw_dirs) != set(WEB_DATA_DIR_KEYS):
            raise WebQueryError(
                f"data_dirs 必须为恰好含 {list(WEB_DATA_DIR_KEYS)} 的映射，实际为 {raw_dirs!r}"
            )
        data_dirs: dict[str, str] = {}
        for key in WEB_DATA_DIR_KEYS:
            value = raw_dirs[key]
            if not isinstance(value, str) or not value:
                raise WebQueryError(f"data_dirs[{key!r}] 必须为非空字符串，实际为 {value!r}")
            data_dirs[key] = value
        collapse_window = dreaming["collapse_window"]
        if (
            not isinstance(collapse_window, int)
            or isinstance(collapse_window, bool)
            or (collapse_window < 1)
        ):
            raise WebQueryError(f"collapse_window 必须为 ≥ 1 的整数，实际为 {collapse_window!r}")
        collapse_threshold = dreaming["collapse_threshold"]
        if (
            not isinstance(collapse_threshold, (int, float))
            or isinstance(collapse_threshold, bool)
            or not 0 < collapse_threshold <= 1
        ):
            raise WebQueryError(
                f"collapse_threshold 必须 ∈ (0,1] 的数值，实际为 {collapse_threshold!r}"
            )
        return cls(
            host=host,
            port=port,
            dsn_env=dsn_env,
            token=token,
            page_size=page_size,
            data_dirs=data_dirs,
            export_dir=export_dir,
            collapse_window=collapse_window,
            collapse_threshold=float(collapse_threshold),
        )

    def data_dir(self, key: str) -> Path:
        """文件化产物目录（键集固定；未知键即报错，不静默取空）。"""
        if key not in self.data_dirs:
            raise WebQueryError(f"未知产物目录 {key!r}（可选：{list(WEB_DATA_DIR_KEYS)}）")
        return Path(self.data_dirs[key])


# ---------------------------------------------------------------------------
# 连接：惰性 + 按 DSN 复用
# ---------------------------------------------------------------------------

_ENGINES: dict[str, Engine] = {}


def engine_cache_size() -> int:
    """已缓存的只读引擎数（诊断用；不暴露 DSN 本身，避免口令进出日志与断言）。"""
    return len(_ENGINES)


def reset_engine_cache() -> None:
    """释放并清空引擎缓存（测试隔离用）。"""
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()


def _resolve_dsn(config: WebConfig) -> str:
    dsn = os.environ.get(config.dsn_env, "")
    if not dsn:
        raise DatabaseUnavailableError(
            f"只读 DSN 未配置：环境变量 {config.dsn_env} 为空（只读角色连接串由环境注入）"
        )
    return dsn


def _engine(config: WebConfig) -> Engine:
    """惰性建引擎：首个 DB 请求才创建；同 DSN 复用（不重复建连）。"""
    dsn = _resolve_dsn(config)
    engine = _ENGINES.get(dsn)
    if engine is not None:
        return engine
    options: dict[str, Any] = {}
    if dsn.startswith("sqlite"):
        # 本地/测试库：多线程（服务为 ThreadingHTTPServer）共享同一连接
        options = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    try:
        engine = create_engine(dsn, **options)
    except Exception as exc:  # noqa: BLE001 - 驱动缺失/DSN 形态非法 → 明确报不可用
        raise DatabaseUnavailableError(f"只读引擎创建失败：{exc}") from exc
    _ENGINES[dsn] = engine
    return engine


def _fetch(config: WebConfig, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
    """执行只读 SQL 并返回映射行；DB 侧任何失败统一报"不可用"（服务侧 503）。"""
    try:
        with _engine(config).connect() as conn:
            result = conn.execute(text(sql), dict(params or {}))
            return [dict(row) for row in result.mappings()]
    except DatabaseUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - 连不上/未迁移/表缺失一律如实报不可用
        raise DatabaseUnavailableError(f"只读查询失败：{exc}") from exc


def _fetch_scalar(config: WebConfig, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    rows = _fetch(config, sql, params)
    return rows[0]["total"] if rows and "total" in rows[0] else (1 if rows else None)


# ---------------------------------------------------------------------------
# JSON 列与产物文件读取
# ---------------------------------------------------------------------------


def _json_column(value: Any, *, default: Any, source: str) -> Any:
    """JSON 列取值：PG 的 jsonb 直接返回对象，SQLite 存文本需解析（方言差异在层内吸收）。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise DataSourceError(f"{source} 的 JSON 列无法解析：{exc}") from exc


def _load_json(path: Path) -> dict | None:
    """读产物文件（缺失 → None；损坏 → 报错不静默；顶层非对象 → 报错）。"""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DataSourceError(f"产物文件无法解析：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise DataSourceError(f"产物文件顶层必须为 JSON 对象：{path}")
    return payload


def _cost_record(value: Any, source: str) -> dict:
    record = _json_column(value, default=None, source=source)
    if not isinstance(record, dict):
        raise DataSourceError(f"{source} 必须为成本记录对象，实际为 {type(record).__name__}")
    missing = [field for field in _COST_FIELDS if field not in record]
    if missing:
        raise DataSourceError(f"{source} 缺成本字段 {missing}（与 001 口径不符）")
    return {field: record[field] for field in _COST_FIELDS}


def _page_bounds(config: WebConfig, page: Any, page_size: Any) -> tuple[int, int, int, int]:
    """分页边界：page ≥ 1；page_size 缺省取配置值、请求值由配置**封顶**。"""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise WebQueryError(f"page 必须为 ≥ 1 的整数，实际为 {page!r}")
    if page_size is None:
        effective = config.page_size
    else:
        if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
            raise WebQueryError(f"page_size 必须为 ≥ 1 的整数，实际为 {page_size!r}")
        effective = min(page_size, config.page_size)
    return page, effective, effective, (page - 1) * effective


# ---------------------------------------------------------------------------
# 过滤选项（facets）
# ---------------------------------------------------------------------------


def list_facets(config: WebConfig) -> dict:
    """过滤控件选项：项目/Agent/策略版本/形态（去重升序，取自树库——权威来源）。

    形态取自 `config_snapshot["form"]`（011 树自报形态）：未标注的树不进选项
    （页面需要"未标注"选项时可另加常量，此处不臆造形态名）。
    """
    projects: set[str] = set()
    agents: set[str] = set()
    versions: set[str] = set()
    forms: set[str] = set()
    for row in _fetch(config, _FACETS_SQL):
        projects.add(row["project_id"])
        agents.add(row["agent_id"])
        versions.add(row["policy_version"])
        snapshot = _json_column(
            row["config_snapshot"], default={}, source="discovery_trees.config_snapshot"
        )
        if isinstance(snapshot, dict) and isinstance(snapshot.get("form"), str):
            forms.add(snapshot["form"])
    return {
        "projects": sorted(projects),
        "agents": sorted(agents),
        "policy_versions": sorted(versions),
        "forms": sorted(forms),
    }


def list_lineage_versions(config: WebConfig) -> list[str]:
    """谱系版本全集：meta 版本 ∪ 树上策略版本（升序）——离线快照与版本索引的枚举口径。"""
    versions = set(_meta_index(config))
    for row in _fetch(config, _TREES_ALL_SQL):
        versions.add(row["policy_version"])
    return sorted(versions)


def list_dreaming_agents(config: WebConfig) -> list[str]:
    """分线全集：dreaming/history 下的 Agent 目录 ∪ 树上 Agent（升序）——曲线面板的枚举口径。"""
    agents = set()
    history_root = config.data_dir("dreaming")
    if history_root.is_dir():
        agents |= {path.name for path in history_root.iterdir() if path.is_dir()}
    for row in _fetch(config, _TREES_ALL_SQL):
        agents.add(row["agent_id"])
    return sorted(agents)


# ---------------------------------------------------------------------------
# 树清单 / 节点列表 / 节点详情
# ---------------------------------------------------------------------------


def list_trees(
    config: WebConfig,
    *,
    project_id: str | None = None,
    agent_id: str | None = None,
    policy_version: str | None = None,
    page: Any = 1,
    page_size: Any = None,
) -> dict:
    """树清单（三维过滤 + 分页；按根节点时间戳倒序，tree_id 升序为确定性 tie-break）。"""
    page, effective, limit, offset = _page_bounds(config, page, page_size)
    where, params = _where_clause(
        (
            ("t.project_id", "project_id", project_id),
            ("t.agent_id", "agent_id", agent_id),
            ("t.policy_version", "policy_version", policy_version),
        )
    )
    total = _fetch_scalar(
        config, "SELECT COUNT(*) AS total FROM discovery_trees AS t" + where, params
    )
    rows = _fetch(
        config,
        _TREE_SELECT_SQL + where + _TREE_ORDER_SQL,
        {**params, "limit": limit, "offset": offset},
    )
    items = []
    for row in rows:
        node_ids = _json_column(row["node_ids"], default=None, source="discovery_trees.node_ids")
        if not isinstance(node_ids, list):
            raise DataSourceError("discovery_trees.node_ids 必须为节点清单数组（001 口径）")
        snapshot = _json_column(
            row["config_snapshot"], default={}, source="discovery_trees.config_snapshot"
        )
        if not isinstance(snapshot, dict):
            raise DataSourceError("discovery_trees.config_snapshot 必须为对象（001 口径）")
        form = snapshot.get("form")
        items.append(
            {
                "tree_id": row["tree_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "policy_version": row["policy_version"],
                "node_count": len(node_ids),
                "created_at": row["root_created_at"],
                "form": form if isinstance(form, str) else None,
            }
        )
    return {"items": items, "page": page, "page_size": effective, "total": total}


def list_nodes(
    config: WebConfig,
    tree_id: str,
    *,
    depth: Any = None,
    page: Any = 1,
    page_size: Any = None,
) -> dict | None:
    """某棵树的节点列表（深度可选过滤 + 分页）；树不存在 → None（服务侧 404）。"""
    if depth is not None and (not isinstance(depth, int) or isinstance(depth, bool) or depth < 0):
        raise WebQueryError(f"depth 必须为 ≥ 0 的整数，实际为 {depth!r}")
    page, effective, limit, offset = _page_bounds(config, page, page_size)
    if not _fetch_scalar(config, _TREE_EXISTS_SQL, {"tree_id": tree_id}):
        return None
    where, params = _where_clause((("n.tree_id", "tree_id", tree_id), ("n.depth", "depth", depth)))
    total = _fetch_scalar(config, "SELECT COUNT(*) AS total FROM tree_nodes AS n" + where, params)
    rows = _fetch(
        config,
        _NODE_SELECT_SQL + where + _NODE_ORDER_SQL,
        {**params, "limit": limit, "offset": offset},
    )
    items = []
    for row in rows:
        cost = _cost_record(row["cost"], "tree_nodes.cost")
        items.append(
            {
                "node_id": row["node_id"],
                "parent_id": row["parent_id"],
                "depth": row["depth"],
                "score": row["score"],
                "cost_usd": cost["generation_api_cost_usd"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
        )
    return {"items": items, "page": page, "page_size": effective, "total": total}


def get_node(config: WebConfig, node_id: str) -> dict | None:
    """节点详情（prompt / 观测键 / 工件引用 / 分量含评估器版本 / 成本）；不存在 → None。"""
    rows = _fetch(config, _NODE_DETAIL_SQL, {"node_id": node_id})
    if not rows:
        return None
    row = rows[0]
    observation = _json_column(
        row["observation_context"], default=None, source="tree_nodes.observation_context"
    )
    if not isinstance(observation, dict):
        raise DataSourceError("tree_nodes.observation_context 必须为对象（001 口径）")
    observation_keys = sorted(observation)
    breakdown = _json_column(
        row["eval_breakdown"], default=None, source="tree_nodes.eval_breakdown"
    )
    if not isinstance(breakdown, dict):
        raise DataSourceError("tree_nodes.eval_breakdown 必须为对象（001 口径）")
    entries = []
    for evaluator_key in sorted(breakdown):
        entry = breakdown[evaluator_key]
        if not isinstance(entry, dict):
            raise DataSourceError(f"eval_breakdown[{evaluator_key!r}] 必须为对象（001 口径）")
        diagnostics = entry.get("diagnostics") or {}
        if not isinstance(diagnostics, dict):
            raise DataSourceError(f"eval_breakdown[{evaluator_key!r}].diagnostics 必须为对象")
        entries.append(
            {
                "evaluator_key": evaluator_key,
                "score": entry.get("score"),
                "diagnostics_keys": sorted(diagnostics),
            }
        )
    cost = _cost_record(row["cost"], "tree_nodes.cost")
    return {
        "node_id": row["node_id"],
        "tree_id": row["tree_id"],
        "parent_id": row["parent_id"],
        "depth": row["depth"],
        "prompt": row["prompt"],
        "observation_keys": observation_keys,
        "artifact": {
            "hash": row["artifact_hash"],
            "metadata_keys": [
                key for key in observation_keys if key.startswith(_ARTIFACT_METADATA_PREFIXES)
            ],
        },
        "eval_breakdown": entries,
        "score": row["score"],
        "cost_usd": cost["generation_api_cost_usd"],
        "cost": cost,
        "status": row["status"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# 谱系
# ---------------------------------------------------------------------------


def _meta_index(config: WebConfig) -> dict[str, dict]:
    """谱系 meta 索引：version → {"agent_id", "parent_version", ...}（跨 Agent 目录扫描）。"""
    root = config.data_dir("policies")
    index: dict[str, dict] = {}
    if not root.is_dir():
        return index
    for agent_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        for path in sorted(agent_dir.glob("*.meta.json")):
            meta = _load_json(path)
            if meta is None:
                continue
            version = meta.get("version")
            filename_version = path.name[: -len(".meta.json")]
            if version != filename_version:
                raise DataSourceError(
                    f"谱系 meta 冲突：{path} 的 version {version!r} 与文件名 {filename_version!r}"
                    " 不一致（谱系完整性优先，不静默）"
                )
            index.setdefault(version, {**meta, "agent_id": agent_dir.name})
    return index


def _lineage_rows(
    versions: Sequence[str],
    trees_by_version: Mapping[str, list[dict]],
    metas: Mapping[str, dict],
) -> list[dict]:
    """版本清单 → (version, project_id, agent_id) 行：跨项目按项目展开，无树按空归属标注。

    **输入顺序即输出顺序**（父链保持"直接父 → 更早"的世代顺序，子版本按版本号升序），
    同一版本内部按 (project_id, agent_id) 排序——顺序是页面呈现与同源比对的语义之一。
    """
    rows: list[dict] = []
    for version in versions:
        trees = trees_by_version.get(version, [])
        version_rows = [
            {
                "version": version,
                "project_id": tree["project_id"],
                "agent_id": tree["agent_id"],
            }
            for tree in trees
        ] or [
            {
                "version": version,
                "project_id": None,
                "agent_id": metas.get(version, {}).get("agent_id"),
            }
        ]
        rows += sorted(
            version_rows, key=lambda row: (row["project_id"] or "", row["agent_id"] or "")
        )
    return rows


def _ancestor_versions(version: str, metas: Mapping[str, dict]) -> list[str]:
    """父链（直接父 → 更早）：meta 的 parent_version 逐级回指，环即报错（不静默）。"""
    chain: list[str] = []
    seen = {version}
    current = metas.get(version, {}).get("parent_version")
    while current:
        if current in seen:
            raise DataSourceError(f"谱系父链成环：{version} → … → {current}（meta 数据损坏）")
        seen.add(current)
        chain.append(current)
        current = metas.get(current, {}).get("parent_version")
    return chain


def get_lineage(config: WebConfig, policy_version: str) -> dict | None:
    """谱系链路：父版本 / 产出树（含项目归属）/ 子版本；版本完全未知 → None（服务侧 404）。"""
    metas = _meta_index(config)
    trees_by_version: dict[str, list[dict]] = {}
    for row in _fetch(config, _TREES_ALL_SQL):
        trees_by_version.setdefault(row["policy_version"], []).append(row)
    for rows in trees_by_version.values():
        rows.sort(key=lambda row: row["tree_id"])

    if policy_version not in metas and policy_version not in trees_by_version:
        return None

    own_trees = sorted(trees_by_version.get(policy_version, []), key=lambda row: row["tree_id"])
    children_versions = sorted(
        version for version, meta in metas.items() if meta.get("parent_version") == policy_version
    )
    agents = sorted({row["agent_id"] for row in own_trees})
    return {
        "policy_version": policy_version,
        "agent_id": metas.get(policy_version, {}).get("agent_id")
        or (agents[0] if agents else None),
        "parents": _lineage_rows(
            _ancestor_versions(policy_version, metas), trees_by_version, metas
        ),
        "trees": [
            {
                "tree_id": row["tree_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
            }
            for row in own_trees
        ],
        "children": _lineage_rows(children_versions, trees_by_version, metas),
        "cross_project": len({row["project_id"] for row in own_trees}) > 1,
    }


# ---------------------------------------------------------------------------
# 进化曲线（dreaming/history 只读）
# ---------------------------------------------------------------------------


def _winner_entry(payload: dict, winner: str) -> tuple[Any, Any]:
    """胜出候选的 (reward, 生成成本)：读轮次报告的候选记录（005 同源 schema）。"""
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("version") != winner:
            continue
        reward = candidate.get("reward")
        trajectory = candidate.get("trajectory") or {}
        cost = (trajectory.get("total_cost") or {}) if isinstance(trajectory, dict) else {}
        return (
            (reward or {}).get("reward") if isinstance(reward, dict) else None,
            cost.get("generation_api_cost_usd"),
        )
    return None, None


def get_evolution(config: WebConfig, agent_id: str) -> dict:
    """逐轮进化曲线：胜出 reward 序列 + 塌缩标注 + 成本（读 dreaming/history 轮次报告）。"""
    directory = config.data_dir("dreaming") / agent_id
    rounds: list[dict] = []
    if directory.is_dir():
        for seq, path in enumerate(sorted(directory.glob("dream-*.json")), start=1):
            payload = _load_json(path)
            if payload is None:
                continue
            winner = payload.get("winner_version")
            if payload.get("status") != "completed" or not winner:
                continue  # 失败/无胜出轮次不进曲线（005 口径）
            reward, cost_usd = _winner_entry(payload, winner)
            rounds.append(
                {
                    "round_id": payload.get("round_id") or path.stem,
                    "round": seq,
                    "winner_version": winner,
                    "best_reward": reward,
                    "cost_usd": cost_usd,
                }
            )

    rewards = [row["best_reward"] for row in rounds if row["best_reward"] is not None]
    baseline = rewards[0] if rewards else None
    window = config.collapse_window
    threshold = config.collapse_threshold
    collapsed = False
    start_round: int | None = None
    # 塌缩判定复现 005 的同一规则（连续 window 轮 < 首轮基线 × threshold），口径由配置驱动；
    # 与 005 CollapseResult 的逐字段一致性由 T1310 的 parity 机检（不允许悄悄漂移）
    if rewards and len(rewards) >= window:
        limit = baseline * threshold
        for start in range(1, len(rewards) - window + 1):
            if all(value < limit for value in rewards[start : start + window]):
                collapsed = True
                start_round = start + 1
                break

    position = 0
    for row in rounds:
        if row["best_reward"] is None:
            row["collapse_flag"] = False
            continue
        position += 1
        row["collapse_flag"] = bool(
            collapsed and start_round is not None and start_round <= position < start_round + window
        )

    plateau_note = None
    winners = [row["winner_version"] for row in rounds]
    for index in range(len(winners) - 1):
        if winners[index] and winners[index] == winners[index + 1]:
            plateau_note = (
                f"第 {rounds[index]['round']}、{rounds[index + 1]['round']} 轮连续胜出同一版本 "
                f"{winners[index]}（平台期观察）"
            )
            break

    return {
        "agent_id": agent_id,
        "rounds": rounds,
        "baseline_reward": baseline,
        "collapse": {
            "collapsed": collapsed,
            "start_round": start_round,
            "threshold": threshold,
            "window": window,
        },
        "plateau_note": plateau_note,
    }


# ---------------------------------------------------------------------------
# 成本汇总（读树库聚合，不重算口径）
# ---------------------------------------------------------------------------


def _iso_period(created_at: Any) -> str:
    """周期口径：节点时间戳（001 的 Float）→ ISO 周标签（%G-W%V，UTC）——与 010/012 同形。"""
    try:
        moment = datetime.fromtimestamp(float(created_at), UTC)
    except (TypeError, ValueError, OSError) as exc:
        raise DataSourceError(f"节点时间戳无法解析：{created_at!r}") from exc
    return moment.strftime("%G-W%V")


def get_costs(config: WebConfig, *, agent_id: str | None = None) -> dict:
    """成本汇总（按 Agent / 周期）：读树库聚合 `generation_api_cost_usd`，不重算任何口径。

    - 成本口径 = 成本记录的 `generation_api_cost_usd`（各 Agent 的 `tree_total` 同字段）；
    - 周期口径 = 节点 `created_at` 换算的 ISO 周（与 010 信度报告/012 漂移报表的周期同形）；
    - 与 CostRecord 聚合的对账一致性由 tests/unit/test_web_board.py 独立聚合机检。
    """
    where, params = _where_clause((("n.agent_id", "agent_id", agent_id),))
    rows = _fetch(config, _COST_SQL + where, params)
    buckets: dict[tuple[str, str], dict] = {}
    agent_totals: dict[str, dict] = {}
    periods: set[str] = set()
    total_usd = 0.0
    node_count = 0
    for row in rows:
        cost = _cost_record(row["cost"], "tree_nodes.cost")
        value = float(cost["generation_api_cost_usd"])
        period = _iso_period(row["created_at"])
        key = (row["agent_id"], period)
        bucket = buckets.setdefault(key, {"cost_usd": 0.0, "node_count": 0})
        bucket["cost_usd"] += value
        bucket["node_count"] += 1
        subtotal = agent_totals.setdefault(row["agent_id"], {"cost_usd": 0.0, "node_count": 0})
        subtotal["cost_usd"] += value
        subtotal["node_count"] += 1
        periods.add(period)
        total_usd += value
        node_count += 1
    return {
        "items": [
            {
                "agent_id": key[0],
                "period": key[1],
                "cost_usd": bucket["cost_usd"],
                "node_count": bucket["node_count"],
            }
            for key, bucket in sorted(buckets.items())
        ],
        "agents": [
            {
                "agent_id": key,
                "cost_usd": value["cost_usd"],
                "node_count": value["node_count"],
            }
            for key, value in sorted(agent_totals.items())
        ],
        "periods": sorted(periods),
        "total_usd": total_usd,
        "node_count": node_count,
    }


# ---------------------------------------------------------------------------
# 摘要（010 信度 + 012 漂移，均为只读产物投影）
# ---------------------------------------------------------------------------


def _latest_report(directory: Path) -> tuple[Path, dict] | None:
    latest: tuple[Path, dict] | None = None
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            payload = _load_json(path)
            if payload is not None:
                latest = (path, payload)
    return latest


def _calibration_panel(config: WebConfig) -> dict:
    found = _latest_report(config.data_dir("calibration") / "reports")
    if found is None:
        return {"period": None, "target": None, "meets": None, "agents": [], "alerts": []}
    _path, report = found
    raw_agents = report.get("agents") or {}
    if not isinstance(raw_agents, dict):
        raise DataSourceError("010 信度报告的 agents 必须为对象（报告 schema 不符）")
    agents = []
    meets: list[Any] = []
    for agent_id in sorted(raw_agents):
        entries = raw_agents[agent_id] or {}
        if not isinstance(entries, dict):
            raise DataSourceError(f"010 信度报告 agents[{agent_id!r}] 必须为对象")
        evaluators = []
        for evaluator_key in sorted(entries):
            entry = entries[evaluator_key] or {}
            if not isinstance(entry, dict):
                raise DataSourceError(f"010 信度报告条目 {evaluator_key!r} 必须为对象")
            # 口径与 010 一致：judge 走 kendall_tau、连续走 pearson_r（只投影，不重算）
            if entry.get("kendall_tau") is not None:
                metric, value = "kendall_tau", entry["kendall_tau"]
            else:
                metric, value = "pearson_r", entry.get("pearson_r")
            evaluators.append(
                {
                    "evaluator_key": evaluator_key,
                    "metric": metric,
                    "value": value,
                    "samples": entry.get("samples"),
                    "meets_target": entry.get("meets_target"),
                }
            )
            meets.append(entry.get("meets_target"))
        agents.append({"agent_id": agent_id, "evaluators": evaluators})
    return {
        "period": report.get("period"),
        "target": report.get("target"),
        "meets": all(flag is True for flag in meets) if meets else None,
        "agents": agents,
        "alerts": report.get("alerts") or [],
    }


def _drift_panel(config: WebConfig) -> dict:
    """漂移面板：优先读 012 报表（逐字段投影），报表缺失时退回状态登记（只读）。"""
    drift_root = config.data_dir("calibration") / "drift"
    found = _latest_report(drift_root / "reports")
    if found is not None:
        _path, report = found
        items = []
        for item in report.get("items") or []:
            status = item.get("status") or {}
            items.append(
                {
                    "evaluator_key": item.get("evaluator_key"),
                    "status": status.get("status"),
                    "since": status.get("since"),
                }
            )
        alerts = [
            {
                "evaluator_key": alert.get("evaluator_key"),
                "agent_id": alert.get("agent_id"),
                "status": alert.get("status"),
                "level": alert.get("level"),
                "double_signal": alert.get("double_signal"),
                "note": alert.get("note"),
            }
            for alert in report.get("alerts") or []
        ]
        return {
            "period": report.get("period"),
            "items": items,
            "alerts": alerts,
            "source": "report",
        }

    status_root = drift_root / "status"
    if status_root.is_dir():
        items = []
        for path in sorted(status_root.glob("*.json")):
            payload = _load_json(path)
            if payload is None:
                continue
            items.append(
                {
                    "evaluator_key": payload.get("evaluator_key"),
                    "status": payload.get("status"),
                    "since": payload.get("since"),
                }
            )
        return {"period": None, "items": items, "alerts": [], "source": "registry"}

    return {"period": None, "items": [], "alerts": [], "source": None}


def get_summary(config: WebConfig) -> dict:
    """一屏摘要：010 信度达标状态 + 012 漂移徽标（缺失如实空态，不伪造）。"""
    return {"calibration": _calibration_panel(config), "drift": _drift_panel(config)}


# ---------------------------------------------------------------------------
# 健康
# ---------------------------------------------------------------------------


def _files_ok(config: WebConfig) -> bool:
    """文件面板可用性：目录缺失 = 空态（合法）；存在但非目录或不可读 = error（不静默）。"""
    for key in WEB_DATA_DIR_KEYS:
        path = config.data_dir(key)
        if not path.exists():
            continue
        if not path.is_dir():
            return False
        try:
            next(iter(path.iterdir()), None)
        except OSError:
            return False
    return True


def get_health(config: WebConfig) -> dict:
    """健康：DB 连通性（down 时树接口 503）+ 文件面板可用性（DB 不可用也照常可用）。"""
    db_up = True
    detail = None
    try:
        _fetch(config, "SELECT 1 AS total")
    except WebQueryError as exc:
        db_up = False
        detail = str(exc)
    files = "ok" if _files_ok(config) else "error"
    return {
        "ok": db_up and files == "ok",
        "db": "up" if db_up else "down",
        "files": files,
        "detail": detail,
    }
