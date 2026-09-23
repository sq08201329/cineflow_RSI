"""HttpBackend 构造入参化单测（功能 016 / T1619，先于实现编写）：契约 C5 + FR-006/007。

**要消除的是本机真实发生的假阳性**：平台自带一个无关的 `OPENAI_API_KEY`，旧实现（构造器隐式
读 `OPENAI_*`）把它当成"档案就绪"。本文件钉死：

1. **显式传入即用传入值**：注入端点/密钥后，环境里的 `OPENAI_*`（哪怕是错的）**完全不参与**；
2. **不再隐式读 `OPENAI_*`**：裸构造 `HttpBackend()` 即报错（要求从档案或显式入参注入）；
3. **按档案声明读取**（`from_profile` / `from_profiles`）：URL 形态用档案里的端点、env 形态读
   声明的变量名；密钥读 `api_key_env` 声明的变量名；**声明的变量未设置即报错并指名道姓**；
4. **多档案分派**：按 `model`（= 路由命中的档案 id）打到各自端点；
5. `legacy_env=True` 的档案在报告里标注**沿用旧变量名**；
6. 旧路径（`from_env()` 显式读旧变量名）保留：给清单/核查器一个稳定的"代码读取点"。
"""

import pytest

from core.llm_gateway.backends.http import HttpBackend
from core.llm_gateway.gateway import BackendResult, GatewayError
from core.llm_gateway.profiles import load_or_migrate


def _chat_response(content: str = "标准回复", prompt_tokens: int = 11, completion_tokens: int = 7):
    def _provider(payload: dict) -> dict:
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        }

    return _provider


class Test显式注入:
    def test_显式传入即用传入值(self, stub_factory, llm_env):
        """环境里 OPENAI_* 指向错误端点/错误密钥时，注入值的调用仍然成功。"""
        server = stub_factory(chat_provider=_chat_response("注入命中"))
        llm_env(unrelated_openai=True)  # 环境里的 OPENAI_API_KEY = unrelated-value-from-host
        backend = HttpBackend(server.base_url, server.api_key, timeout_seconds=5)
        result = backend.complete("问题", model="m", temperature=0.0, max_tokens=16)
        assert isinstance(result, BackendResult) and result.text == "注入命中"
        assert len(server.chat_requests()) == 1  # 打到了注入端点而非环境里的 OPENAI_BASE_URL

    def test_裸构造不再隐式读环境(self, llm_env):
        llm_env(
            OPENAI_API_KEY="unrelated-value-from-host", OPENAI_BASE_URL="https://example.invalid"
        )
        with pytest.raises(GatewayError, match="显式注入"):
            HttpBackend()

    def test_缺密钥即报错注明端点(self):
        with pytest.raises(GatewayError, match="api_key"):
            HttpBackend("https://example.invalid", "")


class Test按档案注入:
    def _config(self, profiles: dict) -> dict:
        return {"llm": {"profiles": profiles, "roles": {}, "default_profile": next(iter(profiles))}}

    def test_URL_形态_档案端点与声明密钥(self, stub_factory, llm_env):
        server = stub_factory(chat_provider=_chat_response("档案端点命中"))
        llm_env(DEEPSEEK_API_KEY=server.api_key, LOCAL_LLM_API_KEY="unused")
        loaded = load_or_migrate(
            self._config(
                {
                    "deepseek-flash": {
                        "base_url": server.base_url,
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "prices": {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012},
                        "price_note": "测试档",
                    }
                }
            )
        )
        backend = HttpBackend.from_profile(loaded.profiles["deepseek-flash"])
        assert backend.base_url == server.base_url
        assert backend.complete(
            "问题", model="deepseek-flash", temperature=0.0, max_tokens=8
        ).text == ("档案端点命中")
        assert backend.legacy_env_note == ""  # 中立变量名：无"沿用旧变量名"标注

    def test_env_形态_按声明变量名读取且缺失即报错(self, llm_env):
        llm_env(LOCAL_LLM_BASE_URL="http://127.0.0.1:9", LOCAL_LLM_API_KEY="k")
        loaded = load_or_migrate(
            self._config(
                {
                    "local-qwen": {
                        "base_url_env": "LOCAL_LLM_BASE_URL",
                        "api_key_env": "LOCAL_LLM_API_KEY",
                        "prices": {"prompt_per_1k": 0.0, "completion_per_1k": 0.0},
                        "zero_marginal": True,
                        "price_note": "自建",
                    }
                }
            )
        )
        backend = HttpBackend.from_profile(loaded.profiles["local-qwen"])
        assert backend.base_url == "http://127.0.0.1:9"

        llm_env(LOCAL_LLM_BASE_URL="http://127.0.0.1:9")  # 只缺密钥 → 报错指明缺哪个变量
        with pytest.raises(GatewayError, match="LOCAL_LLM_API_KEY"):
            HttpBackend.from_profile(loaded.profiles["local-qwen"])
        llm_env(LOCAL_LLM_API_KEY="k")  # 只缺端点变量 → 同样指名（凭证只按声明读取）
        with pytest.raises(GatewayError, match="LOCAL_LLM_BASE_URL"):
            HttpBackend.from_profile(loaded.profiles["local-qwen"])

    def test_沿用旧变量名的档案会标注(self, llm_env):
        llm_env(OPENAI_BASE_URL="https://legacy.invalid", OPENAI_API_KEY="k")
        loaded = load_or_migrate(
            {
                "screenplay": {
                    "model": "m",
                    "model_prices": {"m": {"prompt_per_1k": 1.0, "completion_per_1k": 2.0}},
                }
            }
        )
        profile = loaded.profiles["m"]
        assert profile.legacy_env is True
        backend = HttpBackend.from_profile(profile)
        assert "沿用旧变量名" in backend.legacy_env_note
        assert backend.base_url == "https://legacy.invalid"

    def test_多档案按_model_分派到各自端点(self, stub_factory, llm_env):
        first = stub_factory(chat_provider=_chat_response("端点一"))
        second = stub_factory(chat_provider=_chat_response("端点二"))
        llm_env(DEEPSEEK_API_KEY=first.api_key, LOCAL_LLM_API_KEY=second.api_key)
        loaded = load_or_migrate(
            self._config(
                {
                    "profile-a": {
                        "base_url": first.base_url,
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                        "price_note": "A",
                    },
                    "profile-b": {
                        "base_url": second.base_url,
                        "api_key_env": "LOCAL_LLM_API_KEY",
                        "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                        "price_note": "B",
                    },
                }
            )
        )
        backend = HttpBackend.from_profiles(loaded)
        assert (
            backend.complete("x", model="profile-a", temperature=0.0, max_tokens=4).text == "端点一"
        )
        assert (
            backend.complete("x", model="profile-b", temperature=0.0, max_tokens=4).text == "端点二"
        )
        assert len(first.chat_requests()) == 1 and len(second.chat_requests()) == 1

    def test_未知模型即报错(self, stub_factory, llm_env):
        server = stub_factory(chat_provider=_chat_response())
        llm_env(DEEPSEEK_API_KEY=server.api_key)
        loaded = load_or_migrate(
            self._config(
                {
                    "profile-a": {
                        "base_url": server.base_url,
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                        "price_note": "A",
                    }
                }
            )
        )
        backend = HttpBackend.from_profiles(loaded)
        with pytest.raises(GatewayError, match="未知档案端点"):
            backend.complete("x", model="ghost", temperature=0.0, max_tokens=4)


class Test旧路径保留:
    def test_from_env_显式读旧变量名(self, llm_env):
        llm_env(OPENAI_BASE_URL="https://legacy.invalid", OPENAI_API_KEY="k")
        backend = HttpBackend.from_env()
        assert backend.base_url == "https://legacy.invalid"

    def test_from_env_缺失即报错(self, llm_env):
        llm_env()
        with pytest.raises(GatewayError, match="OPENAI_BASE_URL"):
            HttpBackend.from_env()


class Test档案声明的超时:
    """真实实测：推理模型的思维链+正文超过默认 30s（`The read operation timed out`），
    故超时可由档案声明——`from_profile` 必须采用档案值，显式入参优先。"""

    def _profile(self, **overrides):
        from core.llm_gateway.profiles import load_or_migrate

        raw = {
            "base_url": "https://api.example.invalid",
            "api_key_env": "PROVIDER_API_KEY",
            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
            "price_note": "测试档",
            **overrides,
        }
        loaded = load_or_migrate(
            {"llm": {"profiles": {"reasoner": raw}, "roles": {}, "default_profile": "reasoner"}}
        )
        return loaded.profiles["reasoner"]

    def test_档案声明的超时生效(self, llm_env):
        llm_env(PROVIDER_API_KEY="k")
        profile = self._profile(timeout_seconds=120)
        assert profile.timeout_seconds == 120.0
        assert HttpBackend.from_profile(profile)._timeout == 120.0

    def test_显式入参优先于档案(self, llm_env):
        llm_env(PROVIDER_API_KEY="k")
        backend = HttpBackend.from_profile(self._profile(timeout_seconds=120), timeout_seconds=5.0)
        assert backend._timeout == 5.0

    def test_未声明回落默认_30s(self, llm_env):
        llm_env(PROVIDER_API_KEY="k")
        assert HttpBackend.from_profile(self._profile())._timeout == 30.0

    def test_多档案各自用自己的超时(self, llm_env, stub_factory):
        from core.llm_gateway.profiles import load_or_migrate

        first, second = stub_factory(), stub_factory()
        llm_env(A_KEY=first.api_key, B_KEY=second.api_key)
        loaded = load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "slow": {
                            "base_url": first.base_url,
                            "api_key_env": "A_KEY",
                            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                            "price_note": "慢档",
                            "timeout_seconds": 300,
                        },
                        "fast": {
                            "base_url": second.base_url,
                            "api_key_env": "B_KEY",
                            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                            "price_note": "快档",
                            "timeout_seconds": 5,
                        },
                    },
                    "roles": {},
                    "default_profile": "slow",
                }
            }
        )
        backend = HttpBackend.from_profiles(loaded)
        assert backend._backends["slow"]._timeout == 300.0
        assert backend._backends["fast"]._timeout == 5.0

    def test_超时值非法即拒(self):
        from core.llm_gateway.profiles import ProfileConfigError

        with pytest.raises(ProfileConfigError, match="timeout_seconds"):
            self._profile(timeout_seconds=0)


class Test档案请求参数:
    """档案 = **模型 + 请求参数组合**：`request_options`（白名单 `thinking` / `reasoning_effort`）
    合并进请求体；多档案按 model 各用各的；显式入参不被覆盖。"""

    def _load(self, first, second=None):
        from core.llm_gateway.profiles import load_or_migrate

        profiles = {
            "reasoner": {
                "base_url": first.base_url,
                "api_key_env": "A_KEY",
                "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                "price_note": "生成档（思考模式）",
                "request_options": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            }
        }
        if second is not None:
            profiles["fast"] = {
                "base_url": second.base_url,
                "api_key_env": "B_KEY",
                "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                "price_note": "判决档（非思考模式）",
                "request_options": {"thinking": {"type": "disabled"}},
            }
        return load_or_migrate(
            {"llm": {"profiles": profiles, "roles": {}, "default_profile": "reasoner"}}
        )

    def test_单档案请求参数进请求体(self, stub_factory, llm_env):
        server = stub_factory(chat_provider=_chat_response("ok"))
        llm_env(A_KEY=server.api_key)
        backend = HttpBackend.from_profiles(self._load(server))
        backend.complete("问题", model="reasoner", temperature=0.0, max_tokens=64)
        body = server.chat_requests()[0]
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert body["temperature"] == 0.0 and body["max_tokens"] == 64  # 显式入参原样

    def test_多档案各用各的请求参数(self, stub_factory, llm_env):
        first, second = (
            stub_factory(chat_provider=_chat_response("一")),
            stub_factory(chat_provider=_chat_response("二")),
        )
        llm_env(A_KEY=first.api_key, B_KEY=second.api_key)
        backend = HttpBackend.from_profiles(self._load(first, second))
        backend.complete("生成", model="reasoner", temperature=0.0, max_tokens=64)
        backend.complete("判决", model="fast", temperature=0.0, max_tokens=512)
        assert first.chat_requests()[0]["thinking"] == {"type": "enabled"}
        assert second.chat_requests()[0]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in second.chat_requests()[0]  # 判决档不声明强度

    def test_显式构造的请求参数不被档覆盖(self, stub_factory, llm_env):
        """显式入参优先：单端点构造时给的选项就是本后端的选项（档案层不参与）。"""
        server = stub_factory(chat_provider=_chat_response("ok"))
        backend = HttpBackend(
            server.base_url, server.api_key, request_options={"reasoning_effort": "low"}
        )
        backend.complete("问题", model="m", temperature=0.0, max_tokens=8)
        assert server.chat_requests()[0]["reasoning_effort"] == "low"


class Test厂商模型名:
    """真实故障：档案 id（`deepseek-flash-fast`）被当成模型名发给厂商 → HTTP 400
    `The supported API model names are deepseek-flash, deepseek-v4-pro`。
    修法：档案可声明 `model`（厂商模型名），`model` 参数仍是**档案 id**（分派键）。"""

    def _load(self, server):
        from core.llm_gateway.profiles import load_or_migrate

        return load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "gen": {
                            "model": "vendor-model-x",
                            "base_url": server.base_url,
                            "api_key_env": "A_KEY",
                            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                            "price_note": "生成档",
                        },
                        "judge-fast": {
                            "model": "vendor-model-x",  # 同模型，差异只在 request_options
                            "base_url": server.base_url,
                            "api_key_env": "A_KEY",
                            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                            "price_note": "判决档",
                            "request_options": {"thinking": {"type": "disabled"}},
                        },
                    },
                    "roles": {},
                    "default_profile": "gen",
                }
            }
        )

    def test_请求体用厂商模型名而非档案_id(self, stub_factory, llm_env):
        server = stub_factory(chat_provider=_chat_response("ok"))
        llm_env(A_KEY=server.api_key)
        backend = HttpBackend.from_profiles(self._load(server))
        backend.complete("判决", model="judge-fast", temperature=0.0, max_tokens=64)
        body = server.chat_requests()[0]
        assert body["model"] == "vendor-model-x"  # 厂商模型名
        assert body["thinking"] == {"type": "disabled"}  # 该档案的请求参数

    def test_缺省_model_即档案_id_旧形态兼容(self, llm_env):
        from core.llm_gateway.profiles import load_or_migrate

        loaded = load_or_migrate(
            {
                "screenplay": {
                    "model": "legacy-model",
                    "model_prices": {
                        "legacy-model": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0}
                    },
                }
            }
        )
        assert loaded.profiles["legacy-model"].vendor_model == "legacy-model"
