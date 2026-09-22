"""就绪核查器的**档案化**矩阵单测（功能 016 / T1620，先于实现编写）：契约 C8。

**要消除的是本机真实发生的假阳性**：环境里存在一个与任何档案无关的 `OPENAI_API_KEY`
（平台/其它工具带的），旧核查口径会把它当成"LLM 就绪"。本文件钉死：

1. 档案只按**自己声明的变量名**判定（无关 `OPENAI_*` 完全不参与，报告显式标注已忽略）；
2. 某档案显式声明旧变量名 → 就绪**且**标注"沿用旧变量名（建议改中立名）"；
3. `unset`（未注入，零网络）与 `unreachable`（已注入但连不通）分型沿用；`--probe` 才有网络；
4. 报告含**变量名 + 所属档案 + 用途**；
5. A/B/C 路径判定与退出码语义不变（档案块是独立报告面）。
"""

import json

import pytest

from ops import check_credentials


@pytest.fixture()
def fake_http(monkeypatch):
    """假造探测层：记录请求并返回给定状态（测试绝不发真实网络）。"""
    calls: list[dict] = []
    answer = {"status": 200, "reason": "OK", "error": None}

    def _fake(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        if answer["error"] is not None:
            raise answer["error"]
        return answer["status"], answer["reason"]

    monkeypatch.setattr(check_credentials, "_http_get", _fake)
    return calls, answer


def _profile_entries(report: dict) -> dict:
    """`profile_matrix()` 的档案块（每档案状态/变量/用途）；主报告里嵌在 `llm_profiles` 键。"""
    block = report["llm_profiles"] if "llm_profiles" in report else report
    return {entry["profile_id"]: entry for entry in block["profiles"]}


class Test假阳性归零:
    def test_无关_OPENAI_API_KEY_不参与档案判定(self, llm_profiles_config_factory):
        """显式给环境（不受宿主影响）：只有无关 OPENAI_API_KEY 时，档案仍未就绪。"""
        environ = {"OPENAI_API_KEY": "unrelated-value-from-host"}
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ
        )
        entries = _profile_entries(report)
        # deepseek-flash 声明的是 DEEPSEEK_API_KEY（未设置）→ 未就绪；无关 OPENAI_* 不影响判定
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_MISSING
        assert report["ignored_environ"] == ["OPENAI_API_KEY"]
        assert "假阳性" in report["notes"][0] or "不隐式采纳" in report["notes"][0]

    def test_档案声明的变量已设置即就绪(self, llm_profiles_config_factory):
        environ = {"DEEPSEEK_API_KEY": "k", "LOCAL_LLM_BASE_URL": "u", "LOCAL_LLM_API_KEY": "k2"}
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ
        )
        entries = _profile_entries(report)
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_OK
        assert entries["local-qwen"]["status"] == check_credentials.PROBE_OK
        assert report["ignored_environ"] == []


class Test沿用旧变量名的标注:
    def test_旧变量名档案就绪且标注建议中立名(self, llm_profiles_config_factory):
        environ = {"OPENAI_API_KEY": "host-provided-key"}
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["api_key_env"] = "OPENAI_API_KEY"
        config["llm"]["profiles"]["deepseek-flash"]["legacy_env"] = True
        report = check_credentials.profile_matrix(config, environ=environ)
        entry = _profile_entries(report)["deepseek-flash"]
        assert entry["status"] == check_credentials.PROBE_OK
        assert entry["legacy_env"] is True
        assert "沿用旧变量名" in entry["note"] and "中立名" in entry["note"]


class Test分型与用途:
    def test_未设置零网络_已注入连不通(self, llm_profiles_config_factory, fake_http):
        """分型沿用既有口径：未注入 → unset（零网络）；已注入但连不通 → unreachable。"""
        calls, answer = fake_http
        environ = {"LOCAL_LLM_BASE_URL": "http://127.0.0.1:9", "LOCAL_LLM_API_KEY": "k"}
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ, probe=True
        )
        entries = _profile_entries(report)
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_MISSING
        assert entries["local-qwen"]["status"] == check_credentials.PROBE_OK
        assert len(calls) == 1  # 每档案只探一次；未注入的档案零网络

        answer["error"] = OSError("connection refused")
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ, probe=True
        )
        unreachable = _profile_entries(report)["local-qwen"]["status"]
        assert unreachable == check_credentials.PROBE_UNREACHABLE

    def test_报告含变量名_所属档案_用途(self, llm_profiles_config_factory):
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ={"DEEPSEEK_API_KEY": "k"}
        )
        entry = _profile_entries(report)["deepseek-flash"]
        assert entry["variables"]["DEEPSEEK_API_KEY"]["status"] == check_credentials.PROBE_OK
        assert entry["endpoint"] == "https://api.deepseek.com"  # 只记 host
        assert "generation" in entry["purpose"] or "LLM" in entry["purpose"]
        assert entry["roles"]  # 该档案承担的角色（来自配置映射）


class Test与既有路径判定共存:
    def test_主报告新增档案块且退出码语义不变(self, monkeypatch):
        monkeypatch.setattr(
            check_credentials,
            "_evaluate_path",
            lambda *a, **k: {
                "path_id": "A",
                "runnable": True,
                "missing": [],
                "present": [],
                "blocked_by": [],
            },
        )
        report = check_credentials.evaluate(environ={})
        assert "llm_profiles" in report  # 新块
        assert report["summary"]["all_ready"] is True  # A/B/C 判定口径不变

    def test_命令行输出含档案块(self, monkeypatch, capsys):
        monkeypatch.setattr(
            check_credentials,
            "evaluate",
            lambda **k: {"llm_profiles": {"profiles": []}, "summary": {"all_ready": True}},
        )
        assert check_credentials.main([]) == 0
        assert json.loads(capsys.readouterr().out)["llm_profiles"] == {"profiles": []}
