"""真实 LLM 冒烟器单测（ops/smoke_llm.py）：**零真实网络、零真实密钥**。

覆盖：凭证缺失分支（退出码 1 + 指向 check_credentials，只报长度不回显值）、
base host 脱敏、网关级冒烟（注入假后端 → 按配置价目表折算成本）、价目表缺模型即拒、
模型引用改写（四处 + `pilot.llm_backend`，幂等且注释保留）、最小规模改写
（镜头数落到下界 4、单轮组合数 1、预算下调）、`--round` 的装配路径（注入假 runner，
断言 llm_backend/临时配置/工作目录）与 `--dry-run` 的凭证豁免。

**防网络**：autouse 夹具把 `urllib.request.urlopen` 换成"命中即失败"，
本文件任何用例都不可能发起真实请求。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.llm_gateway.gateway import BackendResult
from ops import smoke_llm

REPO_ROOT = Path(__file__).resolve().parents[2]
MOVIE_CONFIG = REPO_ROOT / "configs" / "movie.yaml"
SHORTDRAMA_CONFIG = REPO_ROOT / "configs" / "shortdrama.yaml"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """任何真实出网都视为违规（本文件一律注入假后端/假 runner）。"""

    def _boom(*args, **kwargs):  # pragma: no cover - 命中即测试失败
        raise AssertionError("单测禁止真实网络请求")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


class _FakeBackend:
    """假 LLM 后端：返回固定文本与 token 数（不联网）。"""

    def __init__(self, *, prompt_tokens=1000, completion_tokens=500, text="假的回复") -> None:
        self.call_count = 0
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._text = text

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        return BackendResult(
            text=self._text,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


class _FakeRun:
    """假 PilotRun：只暴露冒烟器打印所需的字段。"""

    def __init__(self, *, run_id="smoke-llm", package_dir=None, stages=(), status="done") -> None:
        self.run_id = run_id
        self.package_dir = package_dir
        self.record = SimpleNamespace(
            status=SimpleNamespace(value=status),
            stages=tuple(stages),
        )


def _fake_state(stage_id: str, *, cost_usd=0.01, status="done"):
    return SimpleNamespace(
        stage_id=stage_id,
        status=SimpleNamespace(value=status),
        attempts=1,
        cost_usd=cost_usd,
        failure_reason="",
        products=(SimpleNamespace(kind="script", ref="ref-1", content_hash="ab" * 32),),
    )


class Test凭证就位:
    def test_缺失即退出码1且指向核查器(self, monkeypatch, capsys):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code = smoke_llm.main(["--config", str(MOVIE_CONFIG)])
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_NO_CREDENTIALS == 1
        assert payload["reason"] == "credentials_missing"
        assert payload["missing"] == ["OPENAI_BASE_URL", "OPENAI_API_KEY"]
        assert "check_credentials" in payload["hint"]
        assert payload["ok"] is False

    def test_只报长度不回显值(self):
        secret = "sk-绝密密钥不应出现"
        report = smoke_llm.credential_report({"OPENAI_API_KEY": secret, "OPENAI_BASE_URL": "u"})
        assert report["OPENAI_API_KEY"] == {"set": True, "length": len(secret)}
        assert report["OPENAI_BASE_URL"] == {"set": True, "length": 1}
        assert secret not in json.dumps(report, ensure_ascii=False)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://api.deepseek.com", "https://api.deepseek.com"),
            ("https://api.deepseek.com/v1?debug=1", "https://api.deepseek.com"),
            ("http://127.0.0.1:8080/v1/chat", "http://127.0.0.1:8080"),
        ],
    )
    def test_base_只展示host(self, raw, expected):
        assert smoke_llm.base_host(raw) == expected

    def test_base_形态非法即拒(self):
        with pytest.raises(smoke_llm.SmokeError, match="形态不合法"):
            smoke_llm.base_host("api.deepseek.com")


class Test网关级冒烟:
    def test_按配置价目表折算成本(self):
        backend = _FakeBackend(prompt_tokens=1000, completion_tokens=500)
        payload = smoke_llm.run_gateway_smoke(
            MOVIE_CONFIG, model="deepseek-flash", prompt="打招呼", backend=backend
        )
        # deepseek-flash：prompt 0.0003/1k、completion 0.0012/1k（峰时缓存未命中上限）
        assert payload["cost_usd"] == pytest.approx(1000 / 1000 * 0.0003 + 500 / 1000 * 0.0012)
        assert payload["price_book_entry"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert payload["prompt_tokens"] == 1000 and payload["completion_tokens"] == 500
        assert payload["cached"] is False and payload["gateway_call_count"] == 1

    def test_pro_档位价目(self):
        payload = smoke_llm.run_gateway_smoke(
            MOVIE_CONFIG,
            model="deepseek-v4-pro",
            prompt="打招呼",
            backend=_FakeBackend(prompt_tokens=1000, completion_tokens=1000),
        )
        assert payload["cost_usd"] == pytest.approx(0.00132 + 0.00396)

    def test_价目表缺模型即拒(self):
        with pytest.raises(smoke_llm.SmokeError, match="价目表缺少模型"):
            smoke_llm.run_gateway_smoke(MOVIE_CONFIG, model="ghost-model", backend=_FakeBackend())

    def test_同提示词二次调用命中缓存(self):
        backend = _FakeBackend()
        gateway = smoke_llm.build_gateway(MOVIE_CONFIG, backend=backend)
        price = smoke_llm.load_price_book(MOVIE_CONFIG)
        assert "deepseek-flash" in price and "mock-copy-v1" in price  # 既有条目保留
        first = gateway.chat("同一个提示词", model="deepseek-flash")
        second = gateway.chat("同一个提示词", model="deepseek-flash")
        assert first.cached is False and second.cached is True
        assert second.cost_usd == 0.0 and backend.call_count == 1


class Test配置改写:
    def test_四处模型引用与后端声明(self, tmp_path):
        target = smoke_llm.rewrite_config(
            SHORTDRAMA_CONFIG,
            tmp_path / "smoke.yaml",
            model="deepseek-flash",
            llm_backend="http",
        )
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert payload["screenplay"]["model"] == "deepseek-flash"
        assert payload["screenplay"]["judge"]["model"] == "deepseek-flash"
        assert payload["storyboard"]["judge"]["model"] == "deepseek-flash"
        assert payload["promo"]["default_model"] == "deepseek-flash"
        assert payload["pilot"]["llm_backend"] == "http"
        assert payload["pilot"]["backend"] == "simulated"  # 平台适配器保持模拟
        # 注释放行：价目表里的 mock 条目与 DeepSeek 登记逐字保留
        text = target.read_text(encoding="utf-8")
        assert "mock-copy-v1: {prompt_per_1k: 0.001, completion_per_1k: 0.002}" in text
        assert "# DeepSeek 真实 LLM（OpenAI 兼容" in text

    def test_改写幂等(self, tmp_path):
        first = smoke_llm.rewrite_config(
            MOVIE_CONFIG, tmp_path / "a.yaml", model="deepseek-flash", llm_backend="mock"
        ).read_text(encoding="utf-8")
        second = smoke_llm.rewrite_config(
            MOVIE_CONFIG, tmp_path / "b.yaml", model="deepseek-flash", llm_backend="mock"
        ).read_text(encoding="utf-8")
        assert first == second
        assert yaml.safe_load(first)["pilot"]["llm_backend"] == "mock"

    def test_最小规模_镜头数落到下界且组合数与预算下调(self, tmp_path):
        from agents.pilot.stages import build_shot_plan

        target = smoke_llm.rewrite_config(
            MOVIE_CONFIG, tmp_path / "min.yaml", model="deepseek-flash", llm_backend="http"
        )
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert payload["screenplay"]["target_duration_min"] == 1
        assert payload["screenplay"]["page_tolerance"] == 1
        assert payload["visual"]["clips_per_round"] == 1
        assert payload["storyboard"]["boards_per_round"] == 1
        assert payload["editing"]["edits_per_round"] == 1
        assert payload["promo"]["materials_per_round"] == 1
        for path, budget in smoke_llm.MINIMAL_BUDGETS.items():
            assert payload[path[0]]["exploration_per_round_usd"] == budget
        # 目标成片时长 = 单镜时长 × 4 → 镜头计划恰好 4 镜（真正的"最小规模"）
        clip_ms = payload["visual"]["clip_spec"]["duration_seconds"] * 1000
        assert payload["editing"]["target_duration_s"] * 1000 == clip_ms * 4
        configs = SimpleNamespace(
            visual=SimpleNamespace(clip_spec=payload["visual"]["clip_spec"]),
            editing=SimpleNamespace(target_duration_s=payload["editing"]["target_duration_s"]),
        )
        assert len(build_shot_plan(configs, scene_count=4)) == 4

    def test_不改写规模时保留原取值(self, tmp_path):
        target = smoke_llm.rewrite_config(
            MOVIE_CONFIG,
            tmp_path / "full.yaml",
            model="deepseek-flash",
            llm_backend="http",
            minimal=False,
        )
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        original = yaml.safe_load(MOVIE_CONFIG.read_text(encoding="utf-8"))
        assert payload["visual"]["clips_per_round"] == original["visual"]["clips_per_round"]
        assert payload["screenplay"]["model"] == "deepseek-flash"


class Test单轮装配路径:
    def _runner(self, captured: dict):
        def _run(**kwargs):
            captured.update(kwargs)
            return _FakeRun(stages=(_fake_state("script"), _fake_state("promo", cost_usd=0.02)))

        return _run

    def test_装配路径_后端声明与临时配置(self, tmp_path):
        captured: dict = {}
        payload = smoke_llm.run_round_smoke(
            SHORTDRAMA_CONFIG,
            model="deepseek-flash",
            llm_backend="http",
            work_dir=tmp_path,
            run_id="smoke-1",
            runner=self._runner(captured),
        )
        # 传给 run_pilot 的装配参数：llm_backend=http、配置为改写后的临时文件、数据目录在工作目录下
        assert captured["llm_backend"] == "http"
        assert captured["backend"] is None  # 平台适配器不覆盖（保持形态配置的 simulated）
        assert captured["run_id"] == "smoke-1"
        temp_config = Path(captured["config_path"])
        assert temp_config.parent == tmp_path / "configs"
        assert Path(captured["data_dir"]) == tmp_path / "pilot"
        assert (
            yaml.safe_load(temp_config.read_text(encoding="utf-8"))["pilot"]["llm_backend"]
            == "http"
        )
        assert captured["inputs"].target_duration_min == 1

        # 打印面：运行摘要 + 账目 + 产物清单
        assert payload["status"] == "done"
        assert [stage["stage_id"] for stage in payload["stages"]] == ["script", "promo"]
        assert payload["cost"]["total_usd"] == pytest.approx(0.03)
        assert payload["products"][0]["kind"] == "script"
        assert payload["model"] == "deepseek-flash"

    def test_账目优先读样片包(self, tmp_path):
        package_dir = tmp_path / "packages" / "smoke-1"
        package_dir.mkdir(parents=True)
        (package_dir / "cost.json").write_text(
            json.dumps(
                {"total_usd": 1.25, "by_stage": {"script": 1.25}, "reconciled": True},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        payload = smoke_llm.run_round_smoke(
            SHORTDRAMA_CONFIG,
            work_dir=tmp_path,
            runner=lambda **kwargs: _FakeRun(package_dir=package_dir),
        )
        assert payload["cost"] == {
            "source": "package/cost.json",
            "total_usd": 1.25,
            "by_stage": {"script": 1.25},
            "reconciled": True,
        }


class Test命令行与退出码:
    def test_dry_run_不要求凭证且走_mock(self, monkeypatch, capsys, tmp_path):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        captured: dict = {}

        def _fake_runner(**kwargs):
            captured.update(kwargs)
            return _FakeRun(stages=(_fake_state("script"),))

        monkeypatch.setattr(smoke_llm, "run_pilot", _fake_runner)
        code = smoke_llm.main(
            [
                "--config",
                str(SHORTDRAMA_CONFIG),
                "--round",
                "--dry-run",
                "--work-dir",
                str(tmp_path),
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_OK == 0
        assert payload["ok"] is True and payload["llm_backend"] == "mock"
        assert captured["llm_backend"] == "mock"  # 零真实调用
        assert payload["credentials"]["OPENAI_API_KEY"] == {"set": False, "length": 0}

    def test_冒烟失败归退出码2(self, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        code = smoke_llm.main(["--config", str(MOVIE_CONFIG), "--model", "ghost-model"])
        payload = json.loads(capsys.readouterr().out)
        assert code == smoke_llm.EXIT_FAILED == 2
        assert payload["reason"] == "smoke_failed" and "价目表缺少模型" in payload["error"]

    def test_网关级成功路径_注入假后端(self, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        monkeypatch.setattr(smoke_llm, "HttpBackend", lambda *a, **k: _FakeBackend())
        code = smoke_llm.main(["--config", str(MOVIE_CONFIG), "--prompt", "打个招呼"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0 and payload["ok"] is True
        assert payload["base_host"] == "https://api.deepseek.com"
        assert payload["cost_usd"] > 0
        assert "sk-not-a-real-key" not in json.dumps(payload)  # 绝不回显密钥
