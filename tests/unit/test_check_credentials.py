"""凭证就绪核查器单测（ops/check_credentials.py）。

覆盖四类行为：
1. 就绪矩阵与退出码：**全缺 / 部分齐 / 全齐** 三种输入；
2. **默认零网络**：不给 `--probe` 时绝不允许发起任何请求；
3. `--probe` 的探测**分类**：可用（2xx）/ 鉴权被拒（401/403）/ 连不通（DNS/超时）/
   探测路径未命中（404/405，不阻断）/ 平台 5xx——并区分**未设置**与**设置了但不可用**；
4. 用法错误退出码 2、缺失项给出真实可跑的最小验证方式。

http 层一律 monkeypatch（`check_credentials._http_get`）：**测试绝不发起真实网络请求**。
"""

import json
import urllib.error
from pathlib import Path

import pytest

from ops import check_credentials

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"

_B_PATH_ENVS = 14  # B 路径登记凭证数（含 LLM 网关 OPENAI_*）
_C_PATH_ENVS = 2
_ALL_GROUPS = 8  # B 七组（OPENAI/VISUAL_GEN/STORYBOARD_RENDER/SOUND×3/EDIT_RENDER）+ C 一组


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _envs_of(path_id: str) -> tuple[str, ...]:
    path = next(p for p in _manifest()["paths"] if p["path_id"] == path_id)
    return tuple(path["credential_envs"])


def _all_envs() -> tuple[str, ...]:
    return tuple(n for p in _manifest()["paths"] for n in p["credential_envs"])


def _adapters_of(path_id: str) -> tuple[str, ...]:
    path = next(p for p in _manifest()["paths"] if p["path_id"] == path_id)
    return tuple(path["adapters"])


@pytest.fixture()
def no_credentials(monkeypatch):
    """清空清单涉及的全部凭证环境变量（保证测试不受宿主环境影响）。"""
    for name in _all_envs():
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture()
def all_credentials(no_credentials):
    for name in _all_envs():
        no_credentials.setenv(name, "test-value")
    return no_credentials


@pytest.fixture()
def recorded_requests(monkeypatch):
    """假造 http 层：记录请求并返回给定 (status, reason) 或抛给定异常。"""
    calls: list[dict] = []
    answer = {"status": 200, "reason": "OK", "error": None}

    def _fake_http_get(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        if answer["error"] is not None:
            raise answer["error"]
        return answer["status"], answer["reason"]

    monkeypatch.setattr(check_credentials, "_http_get", _fake_http_get)
    return calls, answer


@pytest.fixture()
def forbid_network(monkeypatch):
    """默认口径断言：任何请求都视为违规。"""
    calls: list[str] = []

    def _boom(url, headers, timeout):
        calls.append(url)
        raise AssertionError(f"未给 --probe 时不得发起网络请求：{url}")

    monkeypatch.setattr(check_credentials, "_http_get", _boom)
    return calls


def _run(capsys, argv):
    code = check_credentials.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def _path(payload, path_id):
    return next(p for p in payload["paths"] if p["path_id"] == path_id)


# --- 一、就绪矩阵与退出码 -------------------------------------------------


def test_全缺_时_A_可跑_B_C_列出全部缺失凭证(no_credentials, forbid_network, capsys):
    code, payload = _run(capsys, [])
    assert code == 1  # 有缺失 → 1（便于脚本化）
    assert payload["summary"]["all_ready"] is False
    assert payload["summary"]["runnable"] == ["A"]
    assert payload["summary"]["blocked"] == ["B", "C"]
    assert payload["summary"]["missing_total"] == _B_PATH_ENVS + _C_PATH_ENVS
    a = _path(payload, "A")
    assert a["runnable"] is True  # A 恒为基线：零凭证
    assert a["zero_credential_baseline"] is True
    assert a["credential_count"] == 0
    assert a["missing"] == [] and a["present"] == []
    assert "基线" in a["baseline_note"]
    b = _path(payload, "B")
    assert b["runnable"] is False
    assert tuple(b["missing"]) == _envs_of("B")
    assert b["present"] == []
    assert not forbid_network  # 默认口径零网络（fixture 未被触发）


def test_部分齐_时只列缺失项(no_credentials, forbid_network, capsys):
    for name in ("VISUAL_GEN_BASE_URL", "VISUAL_GEN_API_KEY"):
        no_credentials.setenv(name, "v")
    code, payload = _run(capsys, [])
    assert code == 1
    b = _path(payload, "B")
    assert b["present"] == ["VISUAL_GEN_BASE_URL", "VISUAL_GEN_API_KEY"]  # 按清单登记顺序
    assert b["missing"] == [n for n in _envs_of("B") if not n.startswith("VISUAL_GEN_")]
    assert len(b["missing"]) == _B_PATH_ENVS - 2
    c = _path(payload, "C")
    assert tuple(c["missing"]) == _envs_of("C")
    # 缺失清单只覆盖缺失项：已齐的组不出现"缺一个"的提示
    assert [s["env"] for s in b["next_steps"]] == b["missing"]


def test_全齐_时不探测也全就绪退出码_0(all_credentials, forbid_network, capsys):
    code, payload = _run(capsys, [])
    assert code == 0
    assert payload["summary"] == {
        "runnable": ["A", "B", "C"],
        "blocked": [],
        "all_ready": True,
        "missing_total": 0,
    }
    assert payload["probe"] is False
    for path_id in ("A", "B", "C"):
        assert _path(payload, path_id)["runnable"] is True
    assert not forbid_network


def test_路径过滤只核查指定路径(no_credentials, forbid_network, capsys):
    code, payload = _run(capsys, ["--path", "A"])
    assert code == 0  # 只核查 A：基线恒可跑
    assert payload["checked_paths"] == ["A"]
    assert _path(payload, "A")["runnable"] is True


def test_输出登记清单版本与零计费检查清单(no_credentials, forbid_network, capsys):
    _, payload = _run(capsys, [])
    assert payload["schema_version"] == _manifest()["schema_version"]
    free = " ".join(payload["free_checks"])
    assert "零网络" in free and "零计费" in free and "precheck" in free


# --- 二、探测分类（假造 http 层，零真实网络） -------------------------------


@pytest.mark.parametrize(
    ("http_status", "reason", "expected", "blocking"),
    [
        (200, "OK", check_credentials.PROBE_OK, False),
        (204, "No Content", check_credentials.PROBE_OK, False),
        (401, "Unauthorized", check_credentials.PROBE_AUTH_REJECTED, True),
        (403, "Forbidden", check_credentials.PROBE_AUTH_REJECTED, True),
        (500, "Server Error", check_credentials.PROBE_SERVER_ERROR, True),
        (404, "Not Found", check_credentials.PROBE_PATH_UNKNOWN, False),
        (405, "Method Not Allowed", check_credentials.PROBE_PATH_UNKNOWN, False),
        (429, "Too Many Requests", check_credentials.PROBE_HTTP_OTHER, False),
    ],
)
def test_probe_状态分类与阻断口径(
    all_credentials, recorded_requests, capsys, http_status, reason, expected, blocking
):
    calls, answer = recorded_requests
    answer.update({"status": http_status, "reason": reason})
    code, payload = _run(capsys, ["--probe"])
    assert len(calls) == _ALL_GROUPS  # 全齐时每组各探一次
    probes = [g["probe"] for p in payload["paths"] for g in p["groups"]]
    assert {p["status"] for p in probes} == {expected}
    assert all(p["blocking"] is blocking for p in probes)
    assert code == (1 if blocking else 0)
    assert payload["summary"]["all_ready"] is (not blocking)


def test_probe_鉴权被拒与连不通的文案不同(all_credentials, recorded_requests, capsys):
    _, answer = recorded_requests
    answer.update({"status": 401, "reason": "Unauthorized"})
    _, payload = _run(capsys, ["--probe"])
    detail = _path(payload, "B")["groups"][0]["probe"]["detail"]
    assert "鉴权被拒" in detail and "HTTP 401" in detail

    answer.update({"error": urllib.error.URLError("name resolution failed")})
    _, payload = _run(capsys, ["--probe"])
    probe = _path(payload, "B")["groups"][0]["probe"]
    assert probe["status"] == check_credentials.PROBE_UNREACHABLE
    assert "连不通" in probe["detail"] and "非凭证缺失" in probe["detail"]


def test_probe_未设置的组不发请求且与连不通区分(no_credentials, recorded_requests, capsys):
    """只设 LLM 一组：仅该组被探测；其余组状态为 missing（不是 unreachable）。"""
    no_credentials.setenv("OPENAI_BASE_URL", "https://llm.example/v1")
    no_credentials.setenv("OPENAI_API_KEY", "k")
    calls, _ = recorded_requests
    code, payload = _run(capsys, ["--probe"])
    assert len(calls) == 1
    assert calls[0]["url"] == "https://llm.example/v1/models"
    assert calls[0]["headers"]["Authorization"] == "Bearer k"
    b = _path(payload, "B")
    statuses = {g["prefix"]: g["probe"]["status"] for g in b["groups"]}
    assert statuses["OPENAI"] == check_credentials.PROBE_OK
    assert statuses["VISUAL_GEN"] == check_credentials.PROBE_MISSING
    assert code == 1


def test_probe_凭证齐但连不通_缺失为空且阻断原因可读(all_credentials, recorded_requests, capsys):
    """关键区分：**未设置**（missing）与**设置了但不可用**（unreachable）不是一回事。"""
    _, answer = recorded_requests
    answer.update({"error": urllib.error.URLError("connection refused")})
    code, payload = _run(capsys, ["--probe"])
    assert code == 1
    b = _path(payload, "B")
    assert b["missing"] == []  # 一个都不缺
    assert b["runnable"] is False  # 但仍不可跑
    assert "VISUAL_GEN:unreachable" in b["blocked_by"]
    assert b["next_steps"] == []  # 无需注入凭证，需修环境/网络


def test_probe_只发只读_GET_到登记路径(all_credentials, recorded_requests, capsys):
    calls, _ = recorded_requests
    _run(capsys, ["--probe"])
    paths = {call["url"].rsplit("/", 1)[-1] for call in calls}
    assert paths <= {"models", "health"}  # 只有只读端点，绝不指向生成端点
    assert all(call["headers"]["Accept"] == "application/json" for call in calls)
    assert all(call["timeout"] == check_credentials.DEFAULT_TIMEOUT_SECONDS for call in calls)


def test_probe_路径可覆盖(all_credentials, recorded_requests, capsys):
    calls, _ = recorded_requests
    code, _ = _run(capsys, ["--probe", "--probe-path", "VISUAL_GEN=/v1/ready"])
    assert code == 0
    assert "test-value/v1/ready" in [c["url"] for c in calls]


# --- 三、最小验证方式与用法错误 --------------------------------------------


def test_缺失项给出最小验证方式(no_credentials, forbid_network, capsys):
    _, payload = _run(capsys, [])
    steps = {s["env"]: s for s in _path(payload, "B")["next_steps"]}
    assert "uv run python -c" in steps["VISUAL_GEN_API_KEY"]["verify"]
    assert "HttpRealVideoGen.from_env()" in steps["VISUAL_GEN_API_KEY"]["verify"]
    assert "HttpBackend.from_env()" in steps["OPENAI_API_KEY"]["verify"]  # 显式旧路径入口
    assert all(s["verify_note"] for s in steps.values())
    # 提示里点名的适配器就是清单登记的真实实现类（不是手抄幻觉）
    for path_id in ("B", "C"):
        assert all(
            any(dotted.rsplit(".", 1)[1] in s["verify"] for dotted in _adapters_of(path_id))
            for s in _path(payload, path_id)["next_steps"]
        )


def test_探测复核命令写实(no_credentials, forbid_network, capsys):
    _, payload = _run(capsys, [])
    assert "--probe --path B" in _path(payload, "B")["probe_rerun"]
    assert "--probe --path C" in _path(payload, "C")["probe_rerun"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--probe-path", "VISUAL_GEN"],  # 缺 =PATH
        ["--probe-path", "VISUAL_GEN=health"],  # 路径不以 / 开头
        ["--manifest", "/nonexistent/manifest.json"],
    ],
)
def test_用法错误退出码_2(argv, no_credentials, forbid_network, capsys):
    code, out = _run(capsys, argv)
    assert code == 2
    assert out["status"] == "usage_error"


def test_argparse_拒绝非法路径值(capsys):
    with pytest.raises(SystemExit) as excinfo:
        check_credentials.main(["--path", "D"])
    assert excinfo.value.code == 2


def test_清单漂移_未登记的凭证前缀即拒(no_credentials, forbid_network, tmp_path, capsys):
    """清单里出现未登记探测路径的前缀：拒绝并提示（不静默按默认路径探测）。"""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["paths"][1]["credential_envs"] = [
        "MYSTERY_PLATFORM_BASE_URL",
        "MYSTERY_PLATFORM_API_KEY",
    ]
    drifted = tmp_path / "manifest.json"
    drifted.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    code, out = _run(capsys, ["--manifest", str(drifted)])
    assert code == 2
    assert out["status"] == "usage_error"
    assert "MYSTERY_PLATFORM" in out["error"]


def test_清单里半组凭证即拒(no_credentials, forbid_network, tmp_path, capsys):
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["paths"][2]["credential_envs"] = ["PROMO_PLATFORM_BASE_URL"]  # 缺 _API_KEY
    drifted = tmp_path / "manifest.json"
    drifted.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    code, out = _run(capsys, ["--manifest", str(drifted)])
    assert code == 2
    assert "不成对" in out["error"]
