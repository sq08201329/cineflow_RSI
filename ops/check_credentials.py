#!/usr/bin/env python
"""凭证就绪核查器（升级路径 A/B/C 的"能不能跑"判定；默认零网络）。

背景：升级清单 `docs/pilot-upgrade-manifest.json` 是**路径 → 凭证环境变量名 → 适配器实现类**
的权威登记表，但"清单说需要什么"与"环境里是否已有"此前只能靠人工对照。本 CLI 把这件事
做成一条命令的**就绪矩阵**：每条路径是否可跑、缺哪些凭证、已设哪些凭证、每项缺失怎么最小验证。

口径（诚实边界）：
- **默认零网络调用**：只读环境变量 + 读清单，任何探测都不发生；
- `--probe` 才做**连通性/鉴权探测**，且只发 **GET**（登记路径：LLM `/models`，其余 `/health`；
  可用 `--probe-path PREFIX=PATH` 覆盖为真实端点）——**绝不触发生成类 POST**（零计费红线）；
- 探测失败严格区分两类：**未设置**（`missing`，环境未注入）与**设置了但连不通/鉴权被拒**
  （`unreachable` / `auth_rejected`，环境已注入但不可用）——二者的处置动作完全不同；
- **A 路径是基线**：全模拟链路零凭证，恒为"可跑"（零外部计费），本核查器显式体现。

用法与退出码（0 全就绪 / 1 有缺失或探测未通过 / 2 用法错误）：

    uv run python ops/check_credentials.py                          # 零网络就绪矩阵
    uv run python ops/check_credentials.py --path B --probe         # 只核查 B 并探测连通性
    uv run python ops/check_credentials.py --probe-path VISUAL_GEN=/v1/models
"""

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"
DEFAULT_TIMEOUT_SECONDS = 5.0
ZERO_CREDENTIAL_PATH_IDS = ("A",)  # 零凭证基线：全模拟链路（零外部计费）

_BASE_SUFFIX = "_BASE_URL"
_KEY_SUFFIX = "_API_KEY"

# 探测结论（未设置与"设置了但不可用"必须分开：处置动作不同）
PROBE_OK = "ok"
PROBE_MISSING = "missing"
PROBE_AUTH_REJECTED = "auth_rejected"
PROBE_UNREACHABLE = "unreachable"
PROBE_PATH_UNKNOWN = "path_unknown"
PROBE_SERVER_ERROR = "server_error"
PROBE_HTTP_OTHER = "http_other"

# 阻断就绪的探测结论：凭证不可用（鉴权被拒/连不通）或平台侧故障
BLOCKING_PROBE_STATUSES = frozenset({PROBE_AUTH_REJECTED, PROBE_UNREACHABLE, PROBE_SERVER_ERROR})

STATUS_LABELS = {
    PROBE_OK: "可用：端点可达且鉴权通过",
    PROBE_MISSING: "未设置：环境未注入该组凭证",
    PROBE_AUTH_REJECTED: "设置了但鉴权被拒（HTTP 401/403：凭证无效或权限不足）",
    PROBE_UNREACHABLE: "设置了但连不通（DNS/连接/超时：环境层问题，非凭证缺失）",
    PROBE_PATH_UNKNOWN: "已连通（HTTP 404/405：探测路径未命中，鉴权未验证，不阻断）",
    PROBE_SERVER_ERROR: "已连通但平台报 5xx（平台侧故障，非凭证问题）",
    PROBE_HTTP_OTHER: "已连通（HTTP 4xx：探测请求未必符合该平台约定，不阻断）",
}

_PROBE_STATUS_BY_HTTP = {
    401: PROBE_AUTH_REJECTED,
    403: PROBE_AUTH_REJECTED,
    404: PROBE_PATH_UNKNOWN,
    405: PROBE_PATH_UNKNOWN,
    501: PROBE_PATH_UNKNOWN,
}

# 不花钱的检查清单（写进输出，供运维判断哪些动作可以随便跑）
FREE_CHECKS = (
    "本核查器默认口径：零网络调用，只读环境变量与清单（可随意重跑）",
    "`--probe` 只发 GET 到登记只读端点（LLM /models、其余 /health）：零生成、零计费",
    "`ops/pilot.py precheck`：零成本零落树（只验配置完整性，**不验凭证**）",
    "A 路径 `uv run python ops/pilot.py run`（模拟链路）：零外部计费，成本仍如实入账",
    "禁止项：对生成/投放端点发 POST「试一下」——本核查器无此代码路径",
)


@dataclass(frozen=True)
class AdapterHint:
    """某组凭证的真实装配入口 + 最小验证方式（构造即验证：缺凭证即报错）。"""

    adapter: str
    has_from_env: bool
    extra: str = ""

    def command(self) -> str:
        module_name, class_name = self.adapter.rsplit(".", 1)
        call = f"{class_name}.from_env()" if self.has_from_env else f"{class_name}()"
        return f'uv run python -c "from {module_name} import {class_name}; {call}"'


# 前缀 → 装配入口与验证方式。前缀由清单里的 env 名派生（`prefix_of`），
# 覆盖性与"提示适配器真的读这组 env"由 tests/unit/test_credential_env_lock.py 反向机检。
ADAPTER_HINTS = {
    "OPENAI": AdapterHint(
        "core.llm_gateway.backends.http.HttpBackend",
        has_from_env=False,
        extra="真实 LLM 走网关 http 后端；`ops/pilot.py precheck` 只验配置不验凭证",
    ),
    "VISUAL_GEN": AdapterHint(
        "agents.visual.platform.http_real.HttpRealVideoGen", has_from_env=True
    ),
    "STORYBOARD_RENDER": AdapterHint(
        "agents.storyboard.platform.http_real.HttpRealStoryboardRender", has_from_env=True
    ),
    "SOUND_TTS": AdapterHint("agents.sound.platform.http_real.HttpRealTTSGen", has_from_env=True),
    "SOUND_SFX": AdapterHint("agents.sound.platform.http_real.HttpRealSFXGen", has_from_env=True),
    "SOUND_MUSIC": AdapterHint(
        "agents.sound.platform.http_real.HttpRealMusicGen", has_from_env=True
    ),
    "EDIT_RENDER": AdapterHint(
        "agents.editing.platform.http_real.HttpRealEditRender", has_from_env=True
    ),
    "PROMO_PLATFORM": AdapterHint(
        "agents.promo.platform.http_real.HttpRealPlatform",
        has_from_env=True,
        extra=(
            "生产回流链路亦会构造它："
            "uv run python ops/ingest_metrics.py --round-id <round> --dsn $CINEFLOW_PG_DSN"
            "（缺凭证即报错退出，不静默回落模拟平台）"
        ),
    ),
}

# 探测路径：只读端点。真实平台的健康端点路径未在本仓登记，可经 --probe-path 覆盖；
# 未命中（404/405）如实标为"连通但鉴权未验证"，不阻断——
# 不把"探测路径没猜对"伪装成"凭证不可用"。
PROBE_PATHS = {
    "OPENAI": "/models",
    "VISUAL_GEN": "/health",
    "STORYBOARD_RENDER": "/health",
    "SOUND_TTS": "/health",
    "SOUND_SFX": "/health",
    "SOUND_MUSIC": "/health",
    "EDIT_RENDER": "/health",
    "PROMO_PLATFORM": "/health",
}


class CredentialCheckError(Exception):
    """用法/清单错误（退出码 2：与"凭证缺失"退出码 1 区分）。"""


def prefix_of(env_name: str) -> str:
    """从 `PREFIX_BASE_URL` / `PREFIX_API_KEY` 还原凭证前缀（不成对即拒）。"""
    for suffix in (_BASE_SUFFIX, _KEY_SUFFIX):
        if env_name.endswith(suffix):
            return env_name[: -len(suffix)]
    raise ValueError(
        f"凭证名不符合 <PREFIX>{_BASE_SUFFIX} / <PREFIX>{_KEY_SUFFIX} 口径：{env_name}"
    )


@dataclass
class CredentialGroup:
    """一组凭证（base_url + api_key）：同生共死，探测只针对这一组。"""

    prefix: str
    names: tuple[str, ...]
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    probe: dict | None = None


def _groups_of(credential_envs: list[str]) -> list[CredentialGroup]:
    """把清单里的 env 名按前缀聚成组（清单未成对即拒：半组凭证不可用）。"""
    by_prefix: dict[str, list[str]] = {}
    for name in credential_envs:
        try:
            prefix = prefix_of(name)
        except ValueError as exc:
            raise CredentialCheckError(str(exc)) from exc
        if prefix not in PROBE_PATHS:
            raise CredentialCheckError(
                f"凭证前缀 {prefix} 未登记探测路径（清单漂移：先补 PROBE_PATHS）"
            )
        by_prefix.setdefault(prefix, []).append(name)
    groups = [
        CredentialGroup(prefix=prefix, names=tuple(sorted(names)))
        for prefix, names in sorted(by_prefix.items())
    ]
    for group in groups:
        expected = {f"{group.prefix}{_BASE_SUFFIX}", f"{group.prefix}{_KEY_SUFFIX}"}
        if set(group.names) != expected:
            raise CredentialCheckError(
                f"前缀 {group.prefix} 凭证不成对：{sorted(group.names)}（应为 {sorted(expected)}）"
            )
    return groups


def _http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    """探测唯一出口：**只读 GET**（绝不发 POST——生成类端点的零计费红线）。

    真实 HTTP 层；测试一律 monkeypatch 本函数，绝不发起真实网络请求。
    """
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), str(response.reason)
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.reason)


def probe_group(
    group: CredentialGroup,
    environ: dict[str, str],
    *,
    timeout: float,
    probe_paths: dict[str, str] | None = None,
) -> dict:
    """最小连通性/鉴权探测：GET 只读端点（零生成、零计费）。

    未设置 → 直接返回 `missing`（**不发任何请求**）；已设置才探测，
    并把"鉴权被拒"与"连不通"分开归类（处置动作不同）。
    """
    paths = {**PROBE_PATHS, **(probe_paths or {})}
    base_url = environ.get(f"{group.prefix}{_BASE_SUFFIX}") or ""
    api_key = environ.get(f"{group.prefix}{_KEY_SUFFIX}") or ""
    if not base_url or not api_key:
        return {
            "prefix": group.prefix,
            "status": PROBE_MISSING,
            "target": None,
            "http_status": None,
            "blocking": True,
            "detail": STATUS_LABELS[PROBE_MISSING],
        }
    target = base_url.rstrip("/") + paths[group.prefix]
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        status, reason = _http_get(target, headers, timeout)
    except (OSError, http.client.HTTPException) as exc:
        return {
            "prefix": group.prefix,
            "status": PROBE_UNREACHABLE,
            "target": target,
            "http_status": None,
            "blocking": True,
            "detail": f"{STATUS_LABELS[PROBE_UNREACHABLE]}：{exc}",
        }
    if 200 <= status < 300:
        probe_status = PROBE_OK
    elif status in _PROBE_STATUS_BY_HTTP:
        probe_status = _PROBE_STATUS_BY_HTTP[status]
    elif status >= 500:
        probe_status = PROBE_SERVER_ERROR
    else:
        probe_status = PROBE_HTTP_OTHER
    return {
        "prefix": group.prefix,
        "status": probe_status,
        "target": target,
        "http_status": status,
        "blocking": probe_status in BLOCKING_PROBE_STATUSES,
        "detail": f"{STATUS_LABELS[probe_status]}（HTTP {status} {reason}）",
    }


def _verification_note(name: str, prefix: str) -> dict:
    """单个缺失项的**最小验证方式**（按本仓现有 CLI 能力写实，不虚构命令）。"""
    hint = ADAPTER_HINTS.get(prefix)
    entry = {
        "env": name,
        "how": "设为非空值（凭证只经环境变量注入，不落盘）",
        "verify": None if hint is None else hint.command(),
        "verify_note": (
            "无输出即通过：构造成功表示凭证已注入且形状合规（缺凭证必报错，不静默回落模拟）"
        ),
    }
    if hint is not None and hint.extra:
        entry["also"] = hint.extra
    return entry


def _evaluate_path(
    path: dict,
    environ: dict[str, str],
    *,
    probe: bool,
    timeout: float,
    probe_paths: dict[str, str] | None,
) -> dict:
    zero_credential = path["path_id"] in ZERO_CREDENTIAL_PATH_IDS
    groups = _groups_of(list(path["credential_envs"]))
    for group in groups:
        group.present = [n for n in group.names if environ.get(n)]
        group.missing = [n for n in group.names if not environ.get(n)]
    blocked: list[str] = []
    probes: list[dict] = []
    if probe:
        for group in groups:
            # 已设置但不成对（只有 base_url 或只有 api_key）时也照探：由 missing 兜住
            result = probe_group(group, environ, timeout=timeout, probe_paths=probe_paths)
            group.probe = result
            probes.append(result)
            if result["blocking"]:
                blocked.append(f"{group.prefix}:{result['status']}")
    else:
        for group in groups:
            if group.missing:
                blocked.append(f"{group.prefix}:未设置")
    if zero_credential:
        runnable = True  # 零凭证基线：与凭证环境无关，恒可跑
    else:
        runnable = not blocked
    # 路径级清单按**清单登记顺序**输出（与 `credential_envs` 逐项对照，便于人工核对）
    present = [name for name in path["credential_envs"] if environ.get(name)]
    missing = [name for name in path["credential_envs"] if not environ.get(name)]
    next_steps = [_verification_note(name, prefix_of(name)) for name in missing]
    report = {
        "path_id": path["path_id"],
        "name": path["name"],
        "status": path["status"],
        "runnable": runnable,
        "zero_credential_baseline": zero_credential,
        "credential_count": len(path["credential_envs"]),
        "present": present,
        "missing": missing,
        "groups": [
            {
                "prefix": group.prefix,
                "present": group.present,
                "missing": group.missing,
                "probe": group.probe,
            }
            for group in groups
        ],
        "blocked_by": blocked,
        "next_steps": next_steps,
    }
    if zero_credential:
        report["baseline_note"] = (
            "A 路径为基线：全模拟链路零凭证，恒为可跑（零外部计费；成本仍如实入账）"
        )
    if probe:
        report["probe_rerun"] = (
            f"改设置后复核：uv run python ops/check_credentials.py --probe --path {path['path_id']}"
        )
    elif groups:
        report["probe_rerun"] = (
            f"连通性另测（只读 GET，零计费）："
            f"uv run python ops/check_credentials.py --probe --path {path['path_id']}"
        )
    return report


def _load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.is_file():
        raise CredentialCheckError(f"升级清单不存在：{manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not payload.get("paths"):
        raise CredentialCheckError(f"升级清单缺 paths：{manifest_path}")
    return payload


def _parse_probe_paths(items: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise CredentialCheckError(f"--probe-path 需形如 PREFIX=/path，收到：{item!r}")
        prefix, _, probe_path = item.partition("=")
        if not prefix or not probe_path.startswith("/"):
            raise CredentialCheckError(f"--probe-path 需形如 PREFIX=/path，收到：{item!r}")
        overrides[prefix] = probe_path
    return overrides


def evaluate(
    *,
    manifest_path: str | Path | None = None,
    path_ids: list[str] | None = None,
    probe: bool = False,
    probe_paths: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    environ: dict[str, str] | None = None,
) -> dict:
    """核查并产出就绪矩阵（纯读：默认零网络；`probe=True` 才发只读 GET）。"""
    manifest_file = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST
    manifest = _load_manifest(manifest_file)
    env = os.environ if environ is None else environ
    wanted = [path_id.upper() for path_id in path_ids] if path_ids else None
    paths = [p for p in manifest["paths"] if wanted is None or p["path_id"] in wanted]
    if not paths:
        raise CredentialCheckError(f"未匹配任何路径：{path_ids}")
    reports = [
        _evaluate_path(p, env, probe=probe, timeout=timeout, probe_paths=probe_paths) for p in paths
    ]
    runnable = [r["path_id"] for r in reports if r["runnable"]]
    blocked = [r["path_id"] for r in reports if not r["runnable"]]
    return {
        "manifest": str(manifest_file),
        "schema_version": manifest.get("schema_version"),
        "probe": probe,
        "checked_paths": [r["path_id"] for r in reports],
        "paths": reports,
        "free_checks": list(FREE_CHECKS),
        "summary": {
            "runnable": runnable,
            "blocked": blocked,
            "all_ready": not blocked,
            "missing_total": sum(len(r["missing"]) for r in reports),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="凭证就绪核查器：就绪矩阵（A 恒可跑；B/C 缺凭证即列出）"
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="升级清单路径")
    parser.add_argument(
        "--path",
        action="append",
        choices=("A", "B", "C"),
        help="只核查指定路径（可重复；缺省核查全部）",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="做最小连通性探测（只读 GET；缺省零网络调用）",
    )
    parser.add_argument(
        "--probe-path",
        action="append",
        default=[],
        metavar="PREFIX=PATH",
        help="覆盖某组凭证的探测路径（如 VISUAL_GEN=/v1/models）",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="探测超时秒")
    args = parser.parse_args(argv)
    try:
        probe_paths = _parse_probe_paths(args.probe_path)
        report = evaluate(
            manifest_path=args.manifest,
            path_ids=args.path,
            probe=args.probe,
            probe_paths=probe_paths,
            timeout=args.timeout,
        )
    except CredentialCheckError as exc:
        print(json.dumps({"status": "usage_error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["summary"]["all_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
