"""功能 015 升级（A→B 一行切换）：后端选择**配置驱动** + 唯一装配点（契约 C10/FR-012）。

此前的装配是硬编码的：`agents/pilot/stages.py` 各阶段各自 `new` 模拟实现类（`MockBackend()` /
`SimulatedStoryboardRenderer()` / `SimulatedVideoGen(...)` / `SimulatedTTSGen(...)` /
`SimulatedEditRenderer(...)` / `SimulatedPlatform(...)`），"切换真实渠道"实际要改装配代码——
"切换是配置动作而非重写"这句话在装配层不成立。本文件把它钉死为**配置驱动 + 单点装配**：

1. **声明驱动**：形态配置 `pilot:` 段（`backend` / `llm_backend` / `overrides`）取值决定装配；
   段缺失 → 默认 `simulated`/`mock`（A 路径零配置可跑，既有 015 行为逐字不变）；
2. **不静默回落**（宪章原则六）：取值非法、或声明真实后端而凭证缺失 → **装配期显式拒绝**
   （报错信息必须指出缺哪个环境变量），且发生在**落树/生成之前**（零成本零失败）；
3. **唯一装配点**：模拟/真实适配器只在 `agents/pilot/backends.py` 构造一次，各阶段从
   `PilotRuntime.backends` 取用（`stages.py` 不再出现任何 `Simulated*`/`MockBackend` 字面量）；
4. **可证伪**（"移除修复即红"）：把任一阶段改回硬编码 `Simulated*` → 本文件的静态守卫与
   运行时覆盖用例立即变红（实证见汇报）。

凭证环境变量名一律取清单 `docs/pilot-upgrade-manifest.json`（以代码为权威的登记表），
不手抄：清单增删凭证即本文件的清理面自动跟随。全程 fake env，**绝不打真实网络**
（真实适配器构造即校验凭证，构造本身不发请求）。
"""

import json
from pathlib import Path

import pytest

from agents.pilot import backends as backends_module
from agents.pilot import stages as stages_module
from agents.pilot.backends import BackendAssemblyError
from agents.pilot.pilot import (
    FileRunStore,
    PilotInputs,
    precheck,
    run_pilot,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"

from tests.conftest import declared_profile_variables  # noqa: E402 - 功能 016 同源助手

# 六个后端槽位（`llm` 为网关后端，其余为五个环节的平台适配器）
SLOTS = ("llm", "storyboard", "visual", "sound", "editing", "promo")

# stages.py 不得再出现的硬编码实现类字面量（装配唯一入口 = backends.py）
HARDCODED_ADAPTER_LITERALS = (
    "MockBackend",
    "SimulatedVideoGen",
    "SimulatedStoryboardRenderer",
    "SimulatedTTSGen",
    "SimulatedSFXGen",
    "SimulatedMusicGen",
    "SimulatedEditRenderer",
    "SimulatedPlatform",
)


def _credential_envs() -> tuple[str, ...]:
    """B/C 路径登记的全部凭证环境变量名（清单为权威口径，不手抄）。"""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    names = {
        name
        for path in data["paths"]
        if path["path_id"] in {"B", "C"}
        for name in path["credential_envs"]
    }
    return tuple(sorted(names))


def _llm_profile_envs() -> tuple[str, ...]:
    """配置档案声明的 LLM 变量名（功能 016：**配置即权威**，与适配器代码读取点清单并列）。

    凭证中立化（`api_key_env` 改为中立厂商名）后，只清清单里的旧变量名会漏掉实际读取点——
    本函数让"缺凭证/注入假凭证"两套夹具与配置同源（改名即跟随，不硬编码变量名）。
    """
    declared = declared_profile_variables()
    names = {name for group in declared.values() for name in group}
    return tuple(sorted(names))


@pytest.fixture()
def no_credentials(monkeypatch):
    """清空全部真实渠道凭证 + **配置档案声明的 LLM 变量**（缺凭证路径的对照环境）。"""
    for name in sorted(
        set(_credential_envs()) | set(_llm_profile_envs()) | {"OPENAI_BASE_URL", "OPENAI_API_KEY"}
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def fake_credentials(monkeypatch):
    """注入假凭证（构造即校验通过；基址刻意指向不可解析域名，构造不发任何请求）。"""
    for name in sorted(set(_credential_envs()) | set(_llm_profile_envs())):
        value = "https://example.invalid/v1" if name.endswith("_BASE_URL") else "fake-api-key"
        monkeypatch.setenv(name, value)


def _config_with(tmp_path, base: Path, *, name: str, section: str) -> Path:
    """在 `deployment` 段**之前**插入 `pilot:` 段（不破坏既有定点改写位点）。"""
    source = base.read_text(encoding="utf-8")
    marker = "\ndeployment:\n"
    assert marker in source, "形态配置缺 deployment 段：定点插入位点失效"
    target = tmp_path / "configs" / f"{name}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.replace(marker, f"\n{section}\ndeployment:\n", 1),
        encoding="utf-8",
    )
    return target


def _inputs() -> PilotInputs:
    return PilotInputs(topic="夜班记录", target_duration_min=2, characters=("林静", "陈默"))


def _runtime(config_path: Path, tmp_path: Path, **overrides):
    return stages_module.build_runtime(
        form="shortdrama",
        config_path=config_path,
        data_dir=tmp_path / "pilot",
        artifacts_root=tmp_path / "artifacts",
        **overrides,
    )


def _class_name(obj) -> str:
    return type(obj).__name__


class Test声明驱动装配:
    """段缺失 / 段取值 / 逐环节覆盖三条声明路径。"""

    def test_缺段即默认全模拟且行为不变(self, pilot_form_config_path, tmp_path):
        # 精简 movie 形态配置没有 pilot 段：A 路径必须**零配置可跑**（默认 simulated/mock）
        runtime = _runtime(pilot_form_config_path("movie"), tmp_path)
        assert runtime.backends.resolved == {
            "llm": "mock",
            "storyboard": "simulated",
            "visual": "simulated",
            "sound": "simulated",
            "editing": "simulated",
            "promo": "simulated",
        }

    def test_段取值即装配依据(self, pilot_form_config_path, pilot_demo_config_path, tmp_path):
        # 同一份配置只改 pilot 段取值 → 装配结果不同（形态差异只体现在取值上）
        declared = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="declared-simulated",
            section="pilot:\n  backend: simulated\n  llm_backend: mock\n  overrides: {}\n",
        )
        runtime = _runtime(declared, tmp_path)
        assert runtime.backends.selection.backend == "simulated"
        assert runtime.backends.selection.llm_backend == "mock"
        assert set(_simulated_slots(runtime)) == _DEFAULT_SLOT_CLASSES

    def test_逐环节覆盖生效(self, pilot_demo_config_path, fake_credentials, tmp_path):
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="override-visual",
            section=(
                "pilot:\n  backend: simulated\n  llm_backend: mock\n  overrides: {visual: http}\n"
            ),
        )
        runtime = _runtime(config, tmp_path)
        assert _class_name(runtime.backends.visual) == "HttpRealVideoGen"
        # 未覆盖的环节仍是模拟实现（逐环节独立，不整链切换）
        assert _class_name(runtime.backends.storyboard) == "SimulatedStoryboardRenderer"
        assert {_class_name(a) for a in runtime.backends.sound.values()} == {
            "SimulatedTTSGen",
            "SimulatedSFXGen",
            "SimulatedMusicGen",
        }
        assert _class_name(runtime.backends.editing) == "SimulatedEditRenderer"
        assert _class_name(runtime.backends.promo) == "SimulatedPlatform"
        assert _class_name(runtime.backends.llm) == "MockBackend"

    def test_逐环节覆盖到网关(self, pilot_demo_config_path, fake_credentials, tmp_path):
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="override-llm",
            section="pilot:\n  backend: simulated\n  overrides: {llm: http}\n",
        )
        runtime = _runtime(config, tmp_path)
        # 功能 016：多档案下 LLM 后端是 HttpBackend 的按 model 分派子类（单档案仍为 HttpBackend）
        assert _class_name(runtime.backends.llm) in {"HttpBackend", "_RoutedHttpBackend"}
        assert _class_name(runtime.backends.visual) == "SimulatedVideoGen"

    def test_取值非法即装配期拒绝(self, pilot_demo_config_path, tmp_path):
        for section, keyword in (
            ("pilot:\n  backend: real\n", "pilot.backend"),
            ("pilot:\n  llm_backend: true\n", "pilot.llm_backend"),
            ("pilot:\n  overrides: {visual: real}\n", "pilot.overrides.visual"),
            ("pilot:\n  overrides: {narrator: http}\n", "pilot.overrides"),
            ("pilot: simulated\n", "pilot 段"),
        ):
            config = _config_with(tmp_path, pilot_demo_config_path, name="invalid", section=section)
            with pytest.raises(BackendAssemblyError) as excinfo:
                _runtime(config, tmp_path)
            assert keyword in str(excinfo.value), (section, str(excinfo.value))

    def test_声明http凭证齐即装配真实实现类(
        self, pilot_demo_config_path, fake_credentials, tmp_path
    ):
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="all-http",
            section="pilot:\n  backend: http\n  llm_backend: http\n",
        )
        runtime = _runtime(config, tmp_path)
        # 功能 016：多档案下 LLM 后端是 HttpBackend 的按 model 分派子类（单档案仍为 HttpBackend）
        assert _class_name(runtime.backends.llm) in {"HttpBackend", "_RoutedHttpBackend"}
        assert _class_name(runtime.backends.storyboard) == "HttpRealStoryboardRender"
        assert _class_name(runtime.backends.visual) == "HttpRealVideoGen"
        assert {_class_name(a) for a in runtime.backends.sound.values()} == {
            "HttpRealTTSGen",
            "HttpRealSFXGen",
            "HttpRealMusicGen",
        }
        assert _class_name(runtime.backends.editing) == "HttpRealEditRender"
        assert _class_name(runtime.backends.promo) == "HttpRealPlatform"


class Test缺凭证零成本失败:
    """声明真实后端而凭证缺失：装配期显式拒绝，**零落树零扣费**（构造先于一切生成）。"""

    def test_装配期拒绝且指出缺哪个环境变量(self, pilot_demo_config_path, no_credentials, tmp_path):
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="http-no-cred",
            section="pilot:\n  backend: http\n",
        )
        with pytest.raises(BackendAssemblyError) as excinfo:
            _runtime(config, tmp_path)
        message = str(excinfo.value)
        envs = _credential_envs()
        assert any(env in message for env in envs), message
        assert "缺凭证" in message
        assert "不回落模拟" in message or "不静默" in message

    def test_网关缺凭证亦在装配期拒绝(self, pilot_demo_config_path, no_credentials, tmp_path):
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="llm-http-no-cred",
            section="pilot:\n  backend: simulated\n  llm_backend: http\n",
        )
        with pytest.raises(BackendAssemblyError) as excinfo:
            _runtime(config, tmp_path)
        # 报错必须指出**配置档案声明的**缺失变量（同源：中立化改名后依然成立）
        assert any(name in str(excinfo.value) for name in _llm_profile_envs()), str(excinfo.value)

    def test_零落树零扣费(self, pilot_demo_config_path, no_credentials, tmp_path, monkeypatch):
        """端到端：命令在装配期失败时，DAG 一次都没跑（零节点、零账目、零样片包）。"""
        from agents.pilot import pilot as pilot_module

        calls = {"run": 0}
        real_run = pilot_module.run_dag

        def _spy(*args, **kwargs):
            calls["run"] += 1
            return real_run(*args, **kwargs)

        monkeypatch.setattr(pilot_module, "run_dag", _spy)
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="http-no-cred-e2e",
            section="pilot:\n  backend: http\n  llm_backend: http\n",
        )
        data_dir = tmp_path / "pilot"
        with pytest.raises(BackendAssemblyError):
            run_pilot(
                form="shortdrama",
                config_path=config,
                inputs=_inputs(),
                data_dir=data_dir,
                artifacts_root=tmp_path / "artifacts",
                run_id="never",
                clock=lambda: "2026-01-01T00:00:00+00:00",
            )
        assert calls["run"] == 0  # DAG 未启动：零节点、零扣费
        assert not (data_dir / "runs" / "never.json").exists()  # 无运行记录（账目载体）
        assert not (data_dir / "packages" / "never").exists()  # 无样片包
        artifacts = tmp_path / "artifacts"
        assert not artifacts.exists() or not any(artifacts.rglob("*"))  # 工件库空

    def test_模拟后端不读凭证环境变量(self, pilot_demo_config_path, monkeypatch, tmp_path):
        """A 路径是零凭证基线：清空全部凭证后，模拟装配照常成功。"""
        for name in _credential_envs():
            monkeypatch.delenv(name, raising=False)
        config = _config_with(
            tmp_path,
            pilot_demo_config_path,
            name="simulated-no-cred",
            section="pilot:\n  backend: simulated\n  llm_backend: mock\n",
        )
        runtime = _runtime(config, tmp_path)
        assert _class_name(runtime.backends.visual) == "SimulatedVideoGen"


class TestCLI后端覆盖:
    """CLI `--backend` / `--llm-backend` 运行时覆盖（缺省取配置）。"""

    def _argv(self, config: Path, data_dir: Path, *extra: str) -> list[str]:
        return [
            "run",
            "--form",
            "shortdrama",
            "--config",
            str(config),
            "--topic",
            "夜班记录",
            "--minutes",
            "2",
            "--characters",
            "林静,陈默",
            "--data-dir",
            str(data_dir),
            *extra,
        ]

    def test_cli_backend覆盖配置生效且缺凭证即拒(
        self, pilot_demo_config_path, no_credentials, tmp_path, capsys
    ):
        from ops import pilot as pilot_cli

        # 配置声明 simulated（可跑），CLI 覆盖为 http：凭证缺失 → 装配期拒绝、退出码非 0
        code = pilot_cli.main(
            self._argv(pilot_demo_config_path, tmp_path / "pilot", "--backend", "http")
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["status"] == "rejected"
        assert any(env in payload["error"] for env in _credential_envs()), payload

    def test_cli_llm_backend覆盖配置生效(
        self, pilot_demo_config_path, no_credentials, tmp_path, capsys
    ):
        from ops import pilot as pilot_cli

        code = pilot_cli.main(
            self._argv(pilot_demo_config_path, tmp_path / "pilot", "--llm-backend", "http")
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        # 报错必须指出**配置档案声明的**缺失变量（同源；中立化改名后依然成立）
        assert any(name in payload["error"] for name in _llm_profile_envs()), payload["error"]

    def test_cli_precheck体现后端选择且不验凭证(
        self, pilot_demo_config_path, no_credentials, tmp_path, capsys
    ):
        """precheck 零成本零落树、**不验凭证**：声明 http 也通过，并如实标注凭证面未验。"""
        from ops import pilot as pilot_cli

        argv = self._argv(pilot_demo_config_path, tmp_path / "pilot", "--backend", "http")
        argv[0] = "precheck"
        code = pilot_cli.main(argv)
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["status"] == "ok"
        report = payload["pilot_backend"]
        assert report["backend"] == "http"
        assert report["resolved"]["visual"] == "http"
        assert report["credentials_checked"] is False
        assert "不验凭证" in report["note"]
        assert not (tmp_path / "pilot" / "runs").exists()  # 零落树


class Test防回归可证伪:
    """移除修复即红：装配层不得再出现硬编码实现类字面量。"""

    def test_装配层无硬编码实现类(self):
        source = Path(stages_module.__file__).read_text(encoding="utf-8")
        offenders = [literal for literal in HARDCODED_ADAPTER_LITERALS if literal in source]
        assert not offenders, (
            f"stages.py 出现硬编码适配器字面量（应经 backends.py 装配）：{offenders}"
        )

    def test_阶段实际取用_runtime_装配的后端(
        self, pilot_demo_config_path, pilot_material_script, tmp_path
    ):
        """动态守卫：视觉阶段**真的调用** `runtime.backends.visual`（而非自行 new 一个实例）。

        注入记次代理（透明转发内层实现）：若阶段改回硬编码模拟实现，代理调用次数为 0 → 红。
        """
        from dataclasses import replace

        from agents.pilot import handoffs
        from core.orchestration.models import StageInput, StageOutcome

        runtime = _runtime(pilot_demo_config_path, tmp_path)
        counter = _CountingAdapter(runtime.backends.visual)
        runtime = replace(runtime, backends=replace(runtime.backends, visual=counter))
        stages_module.bind_runtime(runtime)
        segment = handoffs.script_to_segment(pilot_material_script)
        params = handoffs.shotlist_to_gen_params(
            stages_module.build_shotlist(runtime, segment), config=runtime.configs.visual
        )
        outcome = stages_module._visual_entry(
            StageInput(
                stage_id="visual",
                form="shortdrama",
                upstream={"storyboard": StageOutcome(detail={"segment": segment.to_dict()})},
                handoff_input=params,
                shared={"runtime": runtime, "run_id": "guard-run"},
            )
        )
        assert counter.calls > 0, "视觉阶段未取用 runtime 装配的后端（疑似自行 new 适配器）"
        assert outcome.products

    def test_唯一装配点(self):
        """`agents/pilot/` 内除了 backends.py，其它模块不得构造模拟/真实适配器。"""
        offenders = []
        for path in sorted((REPO_ROOT / "agents" / "pilot").glob("*.py")):
            if path.name == "backends.py":
                continue
            source = path.read_text(encoding="utf-8")
            offenders += [
                f"{path.name}:{literal}"
                for literal in HARDCODED_ADAPTER_LITERALS
                if literal in source
            ]
        assert not offenders, offenders

    def test_装配点构造的槽位与阶段消费一致(self, pilot_demo_config_path, tmp_path):
        runtime = _runtime(pilot_demo_config_path, tmp_path)
        assert set(runtime.backends.resolved) == set(SLOTS)
        assert set(runtime.backends.sound) == {"tts", "sfx", "music"}


class _CountingAdapter:
    """记次适配器：透明代理内层实现并统计被调用次数（证明阶段从 runtime 取后端）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def _wrapped(*args, **kwargs):
            self.calls += 1
            return attr(*args, **kwargs)

        return _wrapped


def _simulated_slots(runtime) -> list[str]:
    """当前装配结果里各槽位的实现类名（默认路径对照用；A 路径 = 全模拟 + Mock 网关）。"""
    names = [type(runtime.backends.llm).__name__]
    names += [type(b).__name__ for b in runtime.backends.sound.values()]
    names += [
        type(runtime.backends.visual).__name__,
        type(runtime.backends.storyboard).__name__,
        type(runtime.backends.editing).__name__,
        type(runtime.backends.promo).__name__,
    ]
    return names


# A 路径（默认）的槽位实现类集合：Mock 网关 + 六个模拟适配器
_DEFAULT_SLOT_CLASSES = {
    "MockBackend",
    "SimulatedStoryboardRenderer",
    "SimulatedVideoGen",
    "SimulatedTTSGen",
    "SimulatedSFXGen",
    "SimulatedMusicGen",
    "SimulatedEditRenderer",
    "SimulatedPlatform",
}


def test_运行记录落盘路径未被装配失败波及(pilot_demo_config_path, tmp_path):
    """装配失败不创建运行目录（FileRunStore 在装配之后构造）。"""
    assert not (tmp_path / "pilot" / "runs").exists()
    FileRunStore(tmp_path / "pilot")  # 正向对照：正常构造才会创建 runs/
    assert (tmp_path / "pilot" / "runs").is_dir()


def test_precheck_函数层体现后端选择(pilot_demo_config_path, no_credentials, tmp_path):
    report = precheck(
        form="shortdrama",
        config_path=pilot_demo_config_path,
        inputs=_inputs(),
        data_dir=tmp_path / "pilot",
        backend="http",
        llm_backend="http",
    )
    assert report["pilot_backend"]["backend"] == "http"
    assert report["pilot_backend"]["llm_backend"] == "http"
    assert report["pilot_backend"]["credentials_checked"] is False


def test_backends_模块不依赖形态分支():
    """形态无关（宪章原则五）：后端选择只看 `pilot` 段取值，不认形态字面量。"""
    source = Path(backends_module.__file__).read_text(encoding="utf-8")
    for banned in ("shortdrama", '"movie"', "form ==", "form is "):
        assert banned not in source, banned


class Test清单切换口径与代码一致:
    """升级清单 `switch_mechanism` 必须指向真实存在的装配点 / 配置键 / CLI 标志（漂移即红）。"""

    def _mechanism(self) -> dict:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))["switch_mechanism"]

    def test_schema_递增且装配点真实存在(self):
        import importlib

        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        # 1.7.0 = 档案请求参数与厂商模型名；1.6.0 = 档案化凭证中立登记；
        # 1.5.0 = 真实 LLM 冒烟；1.4.0 = 全部适配器族协议实现；1.3.0 = 媒体环节；1.2.0 = 后端选择
        assert data["schema_version"] == "1.7.0"
        module_name, attr = self._mechanism()["assembly_point"].split("::")
        assert callable(getattr(importlib.import_module(module_name), attr))

    def test_声明的配置键在两套形态配置里可读(self):
        import yaml

        mechanism = self._mechanism()
        for name in ("movie", "shortdrama"):
            payload = yaml.safe_load(
                (REPO_ROOT / "configs" / f"{name}.yaml").read_text(encoding="utf-8")
            )
            section = payload["pilot"]
            for key in mechanism["config_keys"]:
                assert key.split(".")[1] in section, (name, key)
            assert section["backend"] == mechanism["defaults"]["pilot.backend"]
            assert section["llm_backend"] == mechanism["defaults"]["pilot.llm_backend"]
            # 段位置约定：pilot 段在 deployment 段之前（不破坏既有定点改写位点）
            assert list(payload).index("pilot") < list(payload).index("deployment")

    def test_声明的_CLI_标志真实存在(self):
        cli_source = (REPO_ROOT / "ops" / "pilot.py").read_text(encoding="utf-8")
        for entry in self._mechanism()["cli_flags"]:
            flag = entry.split()[0]
            assert flag in cli_source, f"{flag} 未在 ops/pilot.py 中声明"

    def test_B_C_切换命令是真实的一行切换(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for path in data["paths"]:
            if path["path_id"] == "A":
                continue
            runs = [cmd for cmd in path["switch_commands"] if "ops/pilot.py run" in cmd]
            assert runs, f"{path['path_id']} 缺一条可照抄的试水切换命令"
            assert all("--backend http" in cmd for cmd in runs)

    def test_缺凭证不回落的口径写进清单(self):
        boundary = " ".join(json.loads(MANIFEST.read_text(encoding="utf-8"))["honesty_boundary"])
        assert "绝不静默回落模拟" in boundary
        assert "credentials_checked=false" in boundary
