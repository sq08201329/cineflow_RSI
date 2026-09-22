"""冒烟器按档案/角色单测（功能 016 / T1621，先于实现编写）：契约 C9。

覆盖：`--profile` 用指定档案调用（打印档案 id / 端点 / 价目 / 口径备注）；`--profile` 不存在
→ 报错**列出可用档案**；`--model` 为等价别名（两者不一致即拒）；`--round` 改写**角色映射**
（`llm.roles`）而非散落模型名；缺凭证 → 退出码 1 且指名档案与缺失变量（凭证按档案声明读取）；
遗留 4 收敛：`base_host` 即 core 的 `host_of_url`（同一实现）。
"""

import json

import pytest

from core.llm_gateway.gateway import BackendResult
from ops import smoke_llm

MOVIE = "configs/movie.yaml"


class _FakeBackend:
    def __init__(self, *, prompt_tokens=1000, completion_tokens=500) -> None:
        self.call_count = 0
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.legacy_env_note = ""

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        return BackendResult(
            text=f"[{model}] 伪文本",
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


class Test按档案冒烟:
    def test_指定档案_打印档案与口径(self, llm_credentials):
        llm_credentials.inject()  # 注入假凭证（不再依赖宿主环境）
        payload = smoke_llm.run_gateway_smoke(
            MOVIE,
            profile_id="deepseek-flash",
            prompt="打个招呼",
            backend=_FakeBackend(prompt_tokens=1000, completion_tokens=500),
        )
        assert payload["profile_id"] == "deepseek-flash"
        assert payload["endpoint"] == "https://api.deepseek.com"  # 只记 host
        assert payload["legacy_env"] is False  # 中立化后不再沿用旧变量名
        assert payload["legacy_env_note"] == ""
        assert payload["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert payload["price_note"] and "峰时缓存未命中" in payload["price_note"]
        assert payload["cost_usd"] == pytest.approx(0.0003 + 0.0006)
        assert payload["role"] == "generation"

    def test_零边际成本档案(self, llm_credentials):
        llm_credentials.inject()
        payload = smoke_llm.run_gateway_smoke(
            MOVIE, profile_id="local-qwen", prompt="打个招呼", backend=_FakeBackend()
        )
        assert payload["cost_usd"] == 0.0 and payload["zero_marginal"] is True
        assert payload["endpoint"] == "LOCAL_LLM_BASE_URL"  # env 形态记变量名

    def test_档案不存在列出可用(self, llm_credentials):
        llm_credentials.inject()  # 档案解析先于凭证检查；注入以免宿主环境干扰
        with pytest.raises(smoke_llm.SmokeError) as excinfo:
            smoke_llm.run_gateway_smoke(MOVIE, profile_id="ghost", backend=_FakeBackend())
        message = str(excinfo.value)
        assert "档案不存在" in message and "deepseek-flash" in message and "local-qwen" in message


class Test命令行:
    def test_profile_与_model_别名一致(self, monkeypatch, llm_credentials, capsys):
        llm_credentials.inject()
        monkeypatch.setattr(
            smoke_llm,
            "run_gateway_smoke",
            lambda *a, **k: {"mode": "gateway", "profile_id": k["profile_id"]},
        )
        code = smoke_llm.main(
            ["--config", MOVIE, "--profile", "local-qwen", "--model", "local-qwen"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0 and payload["profile_id"] == "local-qwen"

    def test_profile_与_model_不一致即拒(self, capsys):
        code = smoke_llm.main(
            ["--config", MOVIE, "--profile", "local-qwen", "--model", "deepseek-flash"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_FAILED and payload["reason"] == "usage_error"

    def test_缺凭证退出码1且指名变量(self, llm_credentials, llm_profile_vars, capsys):
        """清空**该档案声明的变量**（同源）→ 退出码 1 且 missing 正是这些变量名。"""
        declared = llm_profile_vars.of("deepseek-flash", MOVIE)
        cleared = llm_credentials.clear(MOVIE)
        assert declared <= cleared
        code = smoke_llm.main(["--config", MOVIE, "--profile", "deepseek-flash"])
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_NO_CREDENTIALS == 1
        assert set(payload["missing"]) == declared  # 该档案声明的变量（配置改名即跟随）
        assert payload["profile_id"] == "deepseek-flash"
        assert "check_credentials" in payload["hint"]

    def test_本地档案缺变量也如实报(self, llm_credentials, llm_profile_vars, capsys):
        declared = llm_profile_vars.of("local-qwen", MOVIE)
        llm_credentials.clear(MOVIE)
        code = smoke_llm.main(["--config", MOVIE, "--profile", "local-qwen"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert set(payload["missing"]) == declared


class TestRound改角色映射:
    def test_round_改写角色映射且不动模型名(self, tmp_path, monkeypatch, llm_credentials):
        import yaml

        captured: dict = {}

        def _fake_run(**kwargs):
            captured.update(kwargs)
            return type(
                "R",
                (),
                {
                    "run_id": "smoke",
                    "record": type(
                        "Rec", (), {"status": type("S", (), {"value": "done"})(), "stages": ()}
                    )(),
                    "package_dir": None,
                },
            )()

        monkeypatch.setattr(smoke_llm, "run_pilot", _fake_run)
        llm_credentials.inject()
        payload = smoke_llm.run_round_smoke(
            MOVIE,
            profile_id="local-qwen",
            llm_backend="http",
            work_dir=tmp_path,
            run_id="smoke-1",
        )
        temp_config = yaml.safe_load(captured["config_path"].read_text(encoding="utf-8"))
        original = yaml.safe_load(open(MOVIE, encoding="utf-8"))
        assert set(temp_config["llm"]["roles"].values()) == {"local-qwen"}
        assert temp_config["llm"]["default_profile"] == "local-qwen"
        assert temp_config["pilot"]["llm_backend"] == "http"
        # 散落模型名与价目表都**不再被改写**（路由只在 llm 段）
        assert temp_config["screenplay"]["model"] == original["screenplay"]["model"]
        assert temp_config["screenplay"]["model_prices"] == original["screenplay"]["model_prices"]
        assert payload["profile_id"] == "local-qwen" and payload["status"] == "done"


class Test环境助手同源自检:
    """自检：用例使用的变量集合**来自配置**，改名即跟随（不硬编码变量名、不依赖宿主环境）。"""

    def test_助手变量集合与配置档案逐项一致(self, llm_profile_vars):
        import yaml

        for name in ("movie", "shortdrama"):
            payload = yaml.safe_load(open(f"configs/{name}.yaml", encoding="utf-8"))
            expected = {
                str(profile_id): {
                    str(profile["api_key_env"]),
                    *([str(profile["base_url_env"])] if profile.get("base_url_env") else []),
                }
                for profile_id, profile in payload["llm"]["profiles"].items()
            }
            declared = llm_profile_vars.declared(f"configs/{name}.yaml")
            assert {pid: set(names) for pid, names in declared.items()} == expected

    def test_改名后助手与清空操作同步跟随(
        self, tmp_path, monkeypatch, llm_profile_vars, llm_credentials
    ):
        """把配置里的变量名改掉 → 助手读到的集合与清空行为都跟着变（真正的同源）。"""
        import yaml

        payload = yaml.safe_load(open("configs/movie.yaml", encoding="utf-8"))
        renamed = "RENAMED_PROVIDER_KEY"
        payload["llm"]["profiles"]["deepseek-flash"]["api_key_env"] = renamed
        for profile in payload["llm"]["profiles"].values():
            profile.pop("base_url_env", None)  # 端点写在档案里 → 只留 api_key_env
        temp_config = tmp_path / "renamed.yaml"
        temp_config.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

        declared = llm_profile_vars.declared(temp_config)
        assert renamed in declared["deepseek-flash"]
        monkeypatch.setenv(renamed, "injected")
        cleared = llm_credentials.clear(temp_config)
        assert renamed in cleared
        import os

        assert renamed not in os.environ  # 清空按配置声明生效（改名即跟随）

    def test_助手不碰未声明的变量(self, llm_profile_vars, llm_credentials, monkeypatch):
        """旧变量名（`OPENAI_*`）不属于任何档案声明 → 既不清也不注入（不参与判定）。"""
        monkeypatch.setenv("OPENAI_API_KEY", "host-var-must-survive")
        declared_names = llm_profile_vars.flat()
        assert "OPENAI_API_KEY" not in {
            name for group in llm_profile_vars.declared().values() for name in group
        }
        injected = llm_credentials.inject()
        assert set(injected) == set(declared_names)

        assert "OPENAI_API_KEY" not in injected
