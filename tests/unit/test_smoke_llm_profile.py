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
    def test_指定档案_打印档案与口径(self):
        payload = smoke_llm.run_gateway_smoke(
            MOVIE,
            profile_id="deepseek-flash",
            prompt="打个招呼",
            backend=_FakeBackend(prompt_tokens=1000, completion_tokens=500),
        )
        assert payload["profile_id"] == "deepseek-flash"
        assert payload["endpoint"] == "https://api.deepseek.com"  # 只记 host
        assert payload["legacy_env"] is True  # 沿用旧变量名的档案如实标注
        assert payload["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert payload["price_note"] and "峰时缓存未命中" in payload["price_note"]
        assert payload["cost_usd"] == pytest.approx(0.0003 + 0.0006)
        assert payload["role"] == "generation"

    def test_零边际成本档案(self):
        payload = smoke_llm.run_gateway_smoke(
            MOVIE, profile_id="local-qwen", prompt="打个招呼", backend=_FakeBackend()
        )
        assert payload["cost_usd"] == 0.0 and payload["zero_marginal"] is True
        assert payload["endpoint"] == "LOCAL_LLM_BASE_URL"  # env 形态记变量名

    def test_档案不存在列出可用(self):
        with pytest.raises(smoke_llm.SmokeError) as excinfo:
            smoke_llm.run_gateway_smoke(MOVIE, profile_id="ghost", backend=_FakeBackend())
        message = str(excinfo.value)
        assert "档案不存在" in message and "deepseek-flash" in message and "local-qwen" in message


class Test命令行:
    def test_profile_与_model_别名一致(self, monkeypatch, capsys):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:9")
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "k")
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

    def test_缺凭证退出码1且指名变量(self, monkeypatch, capsys):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code = smoke_llm.main(["--config", MOVIE, "--profile", "deepseek-flash"])
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_NO_CREDENTIALS == 1
        assert payload["missing"] == ["OPENAI_API_KEY"]  # 该档案声明的变量
        assert payload["profile_id"] == "deepseek-flash"
        assert "check_credentials" in payload["hint"]

    def test_本地档案缺变量也如实报(self, monkeypatch, capsys):
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
        code = smoke_llm.main(["--config", MOVIE, "--profile", "local-qwen"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert set(payload["missing"]) == {"LOCAL_LLM_BASE_URL", "LOCAL_LLM_API_KEY"}


class TestRound改角色映射:
    def test_round_改写角色映射且不动模型名(self, tmp_path, monkeypatch):
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
        monkeypatch.setenv("LOCAL_LLM_API_KEY", "k")
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:9")
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
