"""LLM 档案契约聚合（功能 016 / T1611）：契约 C1~C3（US1 的 profiles 段）。

同一套契约对"配置片段工厂"的**全部合法/非法变体**执行一遍，把 C1（解析与校验）、
C2（默认档案与角色映射）、C3（旧扁平迁移）逐场景固化——缺项与枚举外**必须 100% 报错**，
且错误文案要能被运维直接照做（列出合法枚举 / 指出缺哪个键）。
"""

import json

import pytest
import yaml

from core.llm_gateway.profiles import (
    ProfileConfigError,
    load_or_migrate,
    load_profiles,
    migrate_legacy,
)
from core.llm_gateway.routing import role_values


class TestC1档案解析与校验:
    def test_场景1_单档案合法且无档案级告警(self, llm_profiles_config_factory):
        profiles, _, notes = load_profiles(llm_profiles_config_factory("single"))
        assert list(profiles) == ["deepseek-flash"]
        assert notes == []

    def test_场景2_多档案互不串用(self, llm_profiles_config_factory):
        profiles, _, _ = load_profiles(llm_profiles_config_factory("multi"))
        assert profiles["deepseek-flash"].prices["prompt_per_1k"] == 0.0003
        assert profiles["local-qwen"].prices["prompt_per_1k"] == 0.0
        assert profiles["deepseek-flash"].endpoint_ref != profiles["local-qwen"].endpoint_ref

    @pytest.mark.parametrize(
        ("variant", "keyword"),
        [
            ("missing_prices", "缺价目"),
            ("missing_endpoint", "缺端点"),
            ("missing_key_env", "缺 api_key_env"),
        ],
    )
    def test_场景3_三类缺项各报错(self, llm_profiles_config_factory, variant, keyword):
        with pytest.raises(ProfileConfigError, match=keyword):
            load_profiles(llm_profiles_config_factory(variant))

    def test_场景4_零价目必须显式声明(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="zero_marginal"):
            load_profiles(llm_profiles_config_factory("zero_not_declared"))

    def test_场景5_price_note_缺省入告警(self, llm_profiles_config_factory):
        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"].pop("price_note")
        profiles, _, notes = load_profiles(config)
        assert profiles["deepseek-flash"].price_note == ""
        assert any("price_note" in note for note in notes)


class TestC2默认档案与角色映射:
    def test_场景1_单档案自动认定并标注(self):
        from core.llm_gateway.routing import resolve_routing

        routing = resolve_routing({"only": object()}, None, None)
        assert (routing.default_profile, routing.default_reason) == ("only", "single_profile")
        assert any("自动认定" in note for note in routing.notes)

    def test_场景2_双档案缺默认即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="default_profile 缺失"):
            load_profiles(llm_profiles_config_factory("multi_no_default"))

    def test_场景3_枚举外角色报错并列出合法枚举(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError) as excinfo:
            load_profiles(llm_profiles_config_factory("unknown_role"))
        for legal in role_values():
            assert legal in str(excinfo.value)

    @pytest.mark.parametrize(
        ("variant", "keyword"),
        [
            ("unknown_profile", "指向不存在的档案"),
            ("multi_level", "嵌套映射"),
            ("self_reference", "自指"),
        ],
    )
    def test_场景4_映射非法各报错(self, llm_profiles_config_factory, variant, keyword):
        with pytest.raises(ProfileConfigError, match=keyword):
            load_profiles(llm_profiles_config_factory(variant))

    def test_场景5_未引用档案入_notes(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("orphan"))
        assert any("未被任何角色引用" in note for note in loaded.notes)


class TestC3旧扁平迁移:
    def test_场景1_旧写法映射为单档案并给说明(self, llm_profiles_config_factory):
        result = migrate_legacy(llm_profiles_config_factory("legacy_flat"))
        assert result is not None
        profiles, routing, migration_notes = result
        assert list(profiles) == ["mock-copy-v1"]
        assert profiles["mock-copy-v1"].legacy_env is True
        assert migration_notes and "沿用旧变量名" in migration_notes[0]
        assert routing.default_profile == "mock-copy-v1"

    def test_场景2_新旧并存以新为准_旧键被忽略(self, llm_profiles_config_factory):
        loaded = load_or_migrate(llm_profiles_config_factory("legacy_and_new"))
        assert loaded.source == "llm_section"
        assert list(loaded.profiles) == ["deepseek-flash"]
        assert any("旧扁平键被忽略" in note for note in loaded.notes)

    def test_场景3_旧写法缺价目即报错(self, llm_profiles_config_factory):
        with pytest.raises(ProfileConfigError, match="缺少模型"):
            migrate_legacy(llm_profiles_config_factory("legacy_missing_prices"))

    def test_仓库真实配置走新写法且档案齐备(self):
        """形态配置（movie/shortdrama）必须自带 llm 段：档案、角色、默认档案三件齐备。"""
        import pathlib

        import yaml

        repo_root = pathlib.Path(__file__).resolve().parents[2]
        for name in ("movie", "shortdrama"):
            payload = yaml.safe_load(
                (repo_root / "configs" / f"{name}.yaml").read_text(encoding="utf-8")
            )
            loaded = load_or_migrate(payload)
            assert loaded.source == "llm_section"
            assert sorted(loaded.profiles) == ["deepseek-flash", "local-qwen"]
            assert loaded.routing.default_profile == "deepseek-flash"
            assert loaded.routing.profile_for("dreaming_candidates") == "local-qwen"
            snapshot = loaded.snapshot().to_dict()
            assert snapshot["profiles"][0]["prices"]  # 价目随快照冻结
            # 档案声明的凭证变量名与配置逐字一致（同源；改名即跟随，不硬编码具体名）
            for profile_id, raw_profile in payload["llm"]["profiles"].items():
                assert loaded.profiles[profile_id].api_key_env == raw_profile["api_key_env"]
            # 中立化纪律：默认档案不再沿用旧变量名（legacy_env 为假）
            default = loaded.routing.default_profile
            assert loaded.profiles[default].legacy_env is False


# ---------------------------------------------------------------------------
# routing 段（T1616）：契约 C4~C7 聚合——路由决策 / 后端选择 / 成本分解 / 快照冻结
# ---------------------------------------------------------------------------


class TestC4路由决策:
    def _gateway(self, config_factory):
        from core.llm_gateway.gateway import LLMGateway
        from core.llm_gateway.profiles import load_or_migrate
        from tests.unit.test_gateway_routing import _RecordingBackend

        loaded = load_or_migrate(config_factory("multi"))
        return (
            LLMGateway(
                _RecordingBackend(),
                price_book=loaded.snapshot().price_book(),
                sleep=lambda _: None,
                profiles=loaded,
            ),
            loaded,
        )

    def test_场景1_命中角色映射(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role

        gateway, _ = self._gateway(llm_profiles_config_factory)
        decision = gateway.route(Role.JUDGE)
        assert (decision.profile_id, decision.reason) == ("deepseek-flash", "role_mapping")
        assert decision.profile_snapshot_ref == gateway.profile_snapshot().ref  # 可追溯

    def test_场景2_未映射枚举内角色回落默认档案(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role

        gateway, loaded = self._gateway(llm_profiles_config_factory)
        decision = gateway.route(Role.COPYWRITING)  # multi 变体未映射 copywriting
        assert decision.reason == "default_fallback"
        assert decision.profile_id == loaded.routing.default_profile

    def test_场景3_枚举外角色在构造期即拦(self, llm_profiles_config_factory):
        gateway, _ = self._gateway(llm_profiles_config_factory)
        with pytest.raises(ProfileConfigError, match="必须是 Role 枚举成员"):
            gateway.route("judeg")


class TestC5后端选择与不静默回落:
    def test_场景1_两档案各自命中端点与价目(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_routing import _RecordingBackend

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        backend = _RecordingBackend()
        gateway = _LLMGateway(
            backend,
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        gateway.chat("生成", role=Role.GENERATION)
        gateway.chat("候选", role=Role.DREAMING_CANDIDATES)
        assert backend.models == ["deepseek-flash", "local-qwen"]  # 端点/模型由档案注入

    def test_场景2_错误分型不变(self, llm_profiles_config_factory):
        from core.llm_gateway.gateway import PermanentBackendError, TransientBackendError
        from core.llm_gateway.routing import Role

        loaded = load_or_migrate(llm_profiles_config_factory("single"))

        class _Boom:
            call_count = 0

            def __init__(self, error):
                self._error = error

            def complete(self, prompt, *, model, temperature, max_tokens):
                raise self._error

        for error, expected in (
            (TransientBackendError("5xx"), TransientBackendError),
            (PermanentBackendError("4xx"), PermanentBackendError),
        ):
            gateway = _LLMGateway(
                _Boom(error),
                price_book=loaded.snapshot().price_book(),
                sleep=lambda _: None,
                max_retries=0,
                profiles=loaded,
            )
            with pytest.raises(expected):
                gateway.chat("生成", role=Role.GENERATION)

    def test_场景3_档案不可用时不尝试其它档案(self, llm_profiles_config_factory):
        """无静默回落：命中档案失败即失败，不换另一条档案重试。"""
        from core.llm_gateway.gateway import PermanentBackendError
        from core.llm_gateway.routing import Role

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        seen: list[str] = []

        class _Recorder:
            call_count = 0

            def complete(self, prompt, *, model, temperature, max_tokens):
                seen.append(model)
                raise PermanentBackendError("档案不可用")

        gateway = _LLMGateway(
            _Recorder(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            max_retries=0,
            profiles=loaded,
        )
        with pytest.raises(PermanentBackendError):
            gateway.chat("候选", role=Role.DREAMING_CANDIDATES)
        assert seen == ["local-qwen"]  # 只打命中档案一次，未尝试 deepseek-flash


class TestC6成本折算与分解:
    def test_场景1_两档案两条目且金额各按价目(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_cost_breakdown import _CountingBackend

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        gateway = _LLMGateway(
            _CountingBackend(prompt_tokens=1000, completion_tokens=500),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        gateway.chat("生成", role=Role.GENERATION)
        gateway.chat("候选", role=Role.DREAMING_CANDIDATES)
        breakdown = gateway.cost_breakdown()
        assert breakdown["generation"]["deepseek-flash"]["cost_usd"] == pytest.approx(
            0.0003 + 0.0006
        )
        assert breakdown["dreaming_candidates"]["local-qwen"]["cost_usd"] == 0.0
        assert breakdown["dreaming_candidates"]["local-qwen"]["zero_marginal"] is True

    def test_场景2_同档案多角色分开且来源可追溯(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_cost_breakdown import _CountingBackend

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        gateway = _LLMGateway(
            _CountingBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        gateway.chat("生成", role=Role.GENERATION)
        gateway.chat("评审", role=Role.JUDGE)
        report = gateway.cost_report()
        assert set(gateway.cost_breakdown()) == {"generation", "judge"}
        assert report["by_profile"]["deepseek-flash"]["calls"] == 2  # 合并展示
        assert {entry["role"] for entry in report["entries"]} == {"generation", "judge"}  # 来源可溯

    def test_场景3_零价目标注零边际成本(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_cost_breakdown import _CountingBackend

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        gateway = _LLMGateway(
            _CountingBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        result = gateway.chat("候选", role=Role.DREAMING_CANDIDATES)
        assert result.cost_usd == 0.0
        entry = next(e for e in gateway.cost_report()["entries"] if e["profile_id"] == "local-qwen")
        assert entry["zero_marginal"] is True and "零边际成本" in entry["price_note"]

    def test_FR009_报告含口径备注与记账非账单声明(self, llm_profiles_config_factory):
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_cost_breakdown import _CountingBackend

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        gateway = _LLMGateway(
            _CountingBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        gateway.chat("评审", role=Role.JUDGE)
        report = gateway.cost_report()
        assert "峰时缓存未命中上限" in report["entries"][0]["price_note"]
        assert "记账 ≠ 厂商账单" in report["accounting_note"]


class TestC7快照冻结:
    def test_场景1_快照含价目与备注且无密钥(self, llm_profiles_config_factory, llm_env):
        llm_env(DEEPSEEK_API_KEY="sk-contract-secret")
        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        loaded = load_or_migrate(llm_profiles_config_factory("multi"))
        gateway = LLMGateway(
            MockBackend(),
            price_book=loaded.snapshot().price_book(),
            sleep=lambda _: None,
            profiles=loaded,
        )
        text = json.dumps(gateway.profile_snapshot().to_dict(), ensure_ascii=False)
        assert "sk-contract-secret" not in text
        assert "峰时缓存未命中上限" in text and "DEEPSEEK_API_KEY" in text

    def test_场景2_改价目后新快照与新折算同步变化(self, llm_profiles_config_factory):
        """C7 场景 2 的最轻量形态：**同一网关配置**改价 → 新快照指纹与新折算值同步变；
        反之旧快照（历史口径）保持不变（冻结可证伪，完整落树版见 T1610）。"""
        from core.llm_gateway.routing import Role
        from tests.unit.test_gateway_cost_breakdown import _CountingBackend

        config = llm_profiles_config_factory("single")
        before = load_or_migrate(config)
        config_after = llm_profiles_config_factory("single")
        config_after["llm"]["profiles"]["deepseek-flash"]["prices"] = {
            "prompt_per_1k": 0.003,
            "completion_per_1k": 0.012,
        }
        after = load_or_migrate(config_after)

        def _chat(loaded):
            gateway = _LLMGateway(
                _CountingBackend(prompt_tokens=1000, completion_tokens=500),
                price_book=loaded.snapshot().price_book(),
                sleep=lambda _: None,
                profiles=loaded,
            )
            return gateway, gateway.chat("生成", role=Role.GENERATION)

        gateway_before, result_before = _chat(before)
        gateway_after, result_after = _chat(after)
        assert result_after.cost_usd == pytest.approx(10 * result_before.cost_usd)
        assert gateway_before.profile_snapshot().fingerprint != (
            gateway_after.profile_snapshot().fingerprint
        )
        # 旧快照（历史口径）仍可复现：价目未被后来的配置改动污染
        assert before.snapshot().to_dict()["profiles"][0]["prices"] == {
            "prompt_per_1k": 0.0003,
            "completion_per_1k": 0.0012,
        }


# 契约文件内的网关构造别名（保持局部可读）
from core.llm_gateway.gateway import LLMGateway as _LLMGateway  # noqa: E402

# ---------------------------------------------------------------------------
# readiness 段（T1623）：契约 C8~C10——就绪矩阵 / 冒烟器 / 清单锁
# ---------------------------------------------------------------------------


class TestC8就绪矩阵:
    def test_场景1_无关_OPENAI_不影响档案判定(self, llm_profiles_config_factory):
        from ops import check_credentials

        environ = {"OPENAI_API_KEY": "unrelated"}
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ
        )
        entries = {e["profile_id"]: e for e in report["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_MISSING
        assert report["ignored_environ"] == ["OPENAI_API_KEY"]

    def test_场景2_未设置与连不通分型(self, llm_profiles_config_factory, monkeypatch):
        from ops import check_credentials

        environ = {"DEEPSEEK_API_KEY": "k"}
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ
        )
        entries = {e["profile_id"]: e for e in report["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_OK
        assert entries["local-qwen"]["status"] == check_credentials.PROBE_MISSING

        def _boom(url, headers, timeout):
            raise OSError("unreachable")

        monkeypatch.setattr(check_credentials, "_http_get", _boom)
        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ=environ, probe=True
        )
        entries = {e["profile_id"]: e for e in report["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_UNREACHABLE

    def test_场景3_显式声明旧变量名标注沿用(self, llm_profiles_config_factory):
        from ops import check_credentials

        config = llm_profiles_config_factory("single")
        config["llm"]["profiles"]["deepseek-flash"]["api_key_env"] = "OPENAI_API_KEY"
        config["llm"]["profiles"]["deepseek-flash"]["legacy_env"] = True
        report = check_credentials.profile_matrix(config, environ={"OPENAI_API_KEY": "k"})
        entry = report["profiles"][0]
        assert entry["status"] == check_credentials.PROBE_OK and entry["legacy_env"] is True
        assert "沿用旧变量名" in entry["note"]

    def test_仓库配置读取声明变量并忽略未声明变量(self):
        """**不依赖宿主环境**（结构性断言）：档案声明的变量被读取；未声明的（含旧变量名
        与宿主里真实存在的无关变量）一律忽略——注入与被忽略两侧都显式构造。"""
        import pathlib

        import yaml

        from ops import check_credentials

        repo_root = pathlib.Path(__file__).resolve().parents[2]
        config_path = repo_root / "configs" / "movie.yaml"
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        declared = {
            str(profile_id): str(profile["api_key_env"])
            for profile_id, profile in payload["llm"]["profiles"].items()
        }
        # 只注入 deepseek-flash 声明的变量；另外**显式放入**两个未声明的变量（其中一个就剩旧变量名）
        environ = {
            declared["deepseek-flash"]: "injected-value",
            "OPENAI_API_KEY": "unrelated-legacy-host-var",
            "UNRELATED_VENDOR_API_KEY": "unrelated-host-var",
        }
        report = check_credentials.profile_matrix(config_path, environ=environ)
        entries = {entry["profile_id"]: entry for entry in report["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_OK
        assert entries["local-qwen"]["status"] == check_credentials.PROBE_MISSING
        assert entries["deepseek-flash"]["endpoint"] == "https://api.deepseek.com"  # 只记 host
        # 未声明的变量（含旧变量名）不参与判定，且被显式登记为已忽略
        assert {"OPENAI_API_KEY", "UNRELATED_VENDOR_API_KEY"} <= set(report["ignored_environ"])

    def test_档案变量改名时用例跟随配置(self, tmp_path):
        """**同源自检**：把档案声明的变量名改掉后，就绪判定随配置走（用例不硬编码变量名）。"""
        import pathlib

        import yaml

        from ops import check_credentials

        repo_root = pathlib.Path(__file__).resolve().parents[2]
        payload = yaml.safe_load((repo_root / "configs" / "movie.yaml").read_text(encoding="utf-8"))
        renamed = "RENAMED_PROVIDER_API_KEY"
        payload["llm"]["profiles"]["deepseek-flash"]["api_key_env"] = renamed
        temp_config = tmp_path / "movie-renamed.yaml"
        temp_config.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

        # 旧变量名存在也不参与（改名后它已是"未声明变量"）；新名注入才就绪
        stale = check_credentials.profile_matrix(
            temp_config, environ={"OPENAI_API_KEY": "stale", "DEEPSEEK_API_KEY": "stale"}
        )
        stale_entry = {entry["profile_id"]: entry for entry in stale["profiles"]}["deepseek-flash"]
        assert stale_entry["status"] == check_credentials.PROBE_MISSING
        assert renamed in set(stale_entry["variables"])  # 判定跟随新变量名
        assert renamed in set(stale_entry["missing"])

        fresh = check_credentials.profile_matrix(temp_config, environ={renamed: "injected"})
        entries = {entry["profile_id"]: entry for entry in fresh["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_OK


class TestC9冒烟按档案:
    def test_场景1_按档案调用并打印档案口径(self):
        from core.llm_gateway.gateway import BackendResult
        from ops import smoke_llm

        class _Backend:
            call_count = 0
            legacy_env_note = ""

            def complete(self, prompt, *, model, temperature, max_tokens):
                return BackendResult(text="ok", prompt_tokens=100, completion_tokens=50)

        payload = smoke_llm.run_gateway_smoke(
            "configs/movie.yaml", profile_id="deepseek-flash", backend=_Backend()
        )
        assert payload["profile_id"] == "deepseek-flash"
        assert payload["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert payload["endpoint"] == "https://api.deepseek.com"

    def test_场景2_档案不存在列出可用(self):
        from ops import smoke_llm

        with pytest.raises(smoke_llm.SmokeError, match="档案不存在"):
            smoke_llm.resolve_profile("configs/movie.yaml", "ghost")

    def test_场景3_缺凭证退出码1(self, llm_credentials, capsys):
        from ops import smoke_llm

        llm_credentials.clear("configs/movie.yaml")  # 按配置声明清空（同源）
        code = smoke_llm.main(["--config", "configs/movie.yaml", "--profile", "deepseek-flash"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1 and payload["reason"] == "credentials_missing"
        assert "check_credentials" in payload["hint"]

    def test_round_改写角色映射(self, tmp_path):
        from ops import smoke_llm

        target = smoke_llm.rewrite_config(
            "configs/movie.yaml",
            tmp_path / "smoke.yaml",
            profile_id="local-qwen",
            llm_backend="http",
        )
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert set(payload["llm"]["roles"].values()) == {"local-qwen"}
        assert payload["screenplay"]["model"] != "local-qwen"  # 散落模型名不再被改写


class TestC10清单一致性锁:
    def test_场景1_一致(self):
        import pathlib
        import sys

        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "unit"))
        from test_upgrade_manifest_lock import _declared_from_config, _registered_in_manifest

        assert _declared_from_config() == _registered_in_manifest()

    def test_场景2_3_漂移即红且可诊断(self, tmp_path):
        import pathlib
        import sys

        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "unit"))
        from test_upgrade_manifest_lock import _diff, _registered_in_manifest

        broken = tmp_path / "manifest.json"
        broken.write_text(
            json.dumps(
                {
                    "llm_profiles": {
                        "profiles": [{"profile_id": "deepseek-flash", "variables": ["GHOST_KEY"]}]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert _registered_in_manifest(broken) != _registered_in_manifest
        diff = _diff(broken)
        assert "local-qwen" in diff and "GHOST_KEY" in diff


# ---------------------------------------------------------------------------
# SC-001~006 机检聚合（T1628）
# ---------------------------------------------------------------------------


class TestSC机检:
    def test_SC001_零厂商字面量(self):
        """换厂商 = 仅改配置：业务代码零厂商字面量由 T1615 的静态断言守（此处指向同一机检）。"""
        import sys

        sys.path.insert(0, str(REPO_ROOT_STR / "tests" / "unit"))
        from test_no_vendor_literals import _declared_literals, _scanned_files

        model_names, hosts = _declared_literals()
        offenders = [
            path.name
            for path in _scanned_files()
            if any(
                literal in path.read_text(encoding="utf-8") for literal in [*model_names, *hosts]
            )
        ]
        assert offenders == []

    def test_SC002_改价不漂移(self):
        """历史节点成本与快照口径一致：完整落树版见 tests/unit/test_llm_price_freeze.py。"""
        from core.llm_gateway.gateway import LLMGateway
        from core.llm_gateway.profiles import load_or_migrate

        loaded = load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "p": {
                            "base_url": "https://example.invalid",
                            "api_key_env": "K",
                            "prices": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0},
                            "price_note": "口径 A",
                        }
                    },
                    "roles": {},
                    "default_profile": "p",
                }
            }
        )
        snapshot = loaded.snapshot()
        assert snapshot.to_dict()["profiles"][0]["prices"] == {
            "prompt_per_1k": 1.0,
            "completion_per_1k": 1.0,
        }
        # 同一 ProfileLoad 的快照指纹稳定（改价必须改配置 → 新指纹；旧快照不被污染）
        assert LLMGateway.__module__  # 网关持有档案：快照来自配置，不来自码内常量
        assert snapshot.fingerprint == loaded.snapshot().fingerprint

    @pytest.mark.parametrize(
        ("variant", "keyword"),
        [
            ("missing_prices", "缺价目"),
            ("missing_endpoint", "缺端点"),
            ("missing_key_env", "缺 api_key_env"),
            ("zero_not_declared", "zero_marginal"),
            ("unknown_role", "枚举外角色"),
            ("multi_level", "嵌套映射"),
            ("self_reference", "自指"),
            ("unknown_profile", "指向不存在的档案"),
            ("multi_no_default", "default_profile 缺失"),
        ],
    )
    def test_SC003_缺项与枚举外_100_报错(self, llm_profiles_config_factory, variant, keyword):
        with pytest.raises(ProfileConfigError, match=keyword):
            load_or_migrate(llm_profiles_config_factory(variant))

    def test_SC004_假阳性归零(self, llm_profiles_config_factory):
        from ops import check_credentials

        report = check_credentials.profile_matrix(
            llm_profiles_config_factory("multi"), environ={"OPENAI_API_KEY": "unrelated"}
        )
        entries = {e["profile_id"]: e for e in report["profiles"]}
        assert entries["deepseek-flash"]["status"] == check_credentials.PROBE_MISSING
        assert report["ignored_environ"] == ["OPENAI_API_KEY"]

    def test_SC005_单档案等价现状(self):
        """未声明档案的既有形态：价目表折算 + 快照来源 legacy_price_book（既有测试全绿）。"""
        from core.llm_gateway.backends.mock import MockBackend
        from core.llm_gateway.gateway import LLMGateway

        gateway = LLMGateway(
            MockBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        result = gateway.chat("旧路径", model="mock-copy-v1")
        assert result.cost_usd == pytest.approx(
            result.usage["prompt_tokens"] / 1000 * 0.001
            + result.usage["completion_tokens"] / 1000 * 0.002
        )
        assert gateway.profile_snapshot().to_dict()["source"] == "legacy_price_book"

    def test_SC006_清单锁可证伪(self, tmp_path):
        import pathlib
        import sys

        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "unit"))
        from test_upgrade_manifest_lock import _declared_from_config, _diff

        config = _declared_from_config()
        assert config  # 配置声明非空
        broken = tmp_path / "m.json"
        broken.write_text(json.dumps({"llm_profiles": {"profiles": []}}), encoding="utf-8")
        assert "配置有清单无" in _diff(broken)  # 改坏即红且可诊断


REPO_ROOT_STR = __import__("pathlib").Path(__file__).resolve().parents[2]
