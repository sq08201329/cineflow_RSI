"""试水链后端装配（功能 015 升级）：A → B「一行切换」的**唯一装配点**。

此前 `agents/pilot/stages.py` 各阶段各自 `new` 实现类（`MockBackend()` / `Simulated*`），
"切换真实渠道是配置动作"在装配层不成立——要切真实生成就得改装配代码。本模块把它收成
一处：**形态配置 `pilot` 段的取值**决定装配哪个实现类，`stages.py` 只从
`PilotRuntime.backends` 取用，不再出现任何实现类字面量。

声明形态（段缺失即取默认值；取值非法或凭证缺失即**装配期拒绝**）：

```yaml
pilot:
  backend: simulated          # simulated | http（五个环节的平台适配器）
  llm_backend: mock           # mock | http（LLM 网关后端）
  overrides: {}               # 逐环节覆盖：{visual: http, llm: http, ...}
```

诚实边界（宪章原则六，不静默回落）：
- **A 路径零配置可跑**：`pilot` 段缺失 → 默认 `simulated`/`mock`，行为与既有 015 逐字一致；
- **声明真实后端而凭证缺失 = 装配期显式失败**：报错复用各 `http_real` 适配器的既有缺凭证
  报错（指出**缺哪个环境变量**），且构造先于一切落树/生成（零成本失败，与 precheck 同语义）；
- **不做形态判断**（宪章原则五）：只看 `pilot` 段取值（形态值只作透传），不含任何形态判定分支。

`--backend` / `--llm-backend` 是**运行时全局覆盖**（替换 `pilot.backend` / `pilot.llm_backend`）；
配置里的 `overrides` 是逐环节声明，**优先于全局取值**——要逐环节切换就改配置，保证
"一行切换"而不是"改装配代码"。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from agents.editing.platform.base import UnavailableError as EditingUnavailableError
from agents.promo.platform.base import UnavailableError as PromoUnavailableError
from agents.sound.platform.base import UnavailableError as SoundUnavailableError
from agents.storyboard.platform.base import UnavailableError as StoryboardUnavailableError
from agents.visual.platform.base import UnavailableError as VisualUnavailableError
from core.llm_gateway.gateway import GatewayError, LLMGateway
from core.orchestration.errors import OrchestrationError

# 形态配置中的声明位置（新段，置于 deployment 段之前）
PILOT_SECTION = "pilot"
BACKEND_KEY = "backend"
LLM_BACKEND_KEY = "llm_backend"
OVERRIDES_KEY = "overrides"

SIMULATED = "simulated"
HTTP = "http"
MOCK = "mock"

DEFAULT_BACKEND = SIMULATED  # A 路径基线：全模拟链路零凭证（段缺失即取此值）
DEFAULT_LLM_BACKEND = MOCK

PLATFORM_SLOTS = ("storyboard", "visual", "sound", "editing", "promo")
LLM_SLOT = "llm"
OVERRIDE_KEYS = (LLM_SLOT, *PLATFORM_SLOTS)
SOUND_TYPES = ("tts", "sfx", "music")

_ALLOWED_PLATFORM_BACKENDS = (SIMULATED, HTTP)
_ALLOWED_LLM_BACKENDS = (MOCK, HTTP)

# 凭证类失败（各平台适配器与网关的"缺凭证即不可用"错误）：装配期一律转为本模块的统一报错
_CREDENTIAL_ERRORS = (
    GatewayError,
    VisualUnavailableError,
    StoryboardUnavailableError,
    SoundUnavailableError,
    EditingUnavailableError,
    PromoUnavailableError,
)


class BackendAssemblyError(OrchestrationError):
    """后端装配被拒（取值非法 / 声明真实后端而凭证缺失）——拒绝启动，不静默回落模拟。"""


@dataclass(frozen=True)
class BackendSelection:
    """`pilot` 段的声明视图（取值 + 逐环节覆盖）；段缺失即全默认。"""

    backend: str = DEFAULT_BACKEND
    llm_backend: str = DEFAULT_LLM_BACKEND
    overrides: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> BackendSelection:
        """读形态配置 `pilot` 段：缺段取默认；段存在即逐键校验（非法取值即拒绝）。"""
        path = Path(config_path)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BackendAssemblyError(f"形态配置文件不可读：{path}（{exc}）") from exc
        section = payload.get(PILOT_SECTION) if isinstance(payload, Mapping) else None
        if section is None:
            return cls()  # 段缺失 = A 路径基线（零配置可跑，不是错误）
        if not isinstance(section, Mapping):
            raise BackendAssemblyError(
                f"形态配置 {PILOT_SECTION} 段必须是键值映射"
                f"（backend/llm_backend/overrides）：{path}"
            )
        backend = _platform_value(
            section.get(BACKEND_KEY, DEFAULT_BACKEND), f"{PILOT_SECTION}.{BACKEND_KEY}"
        )
        llm_backend = _llm_value(
            section.get(LLM_BACKEND_KEY, DEFAULT_LLM_BACKEND), f"{PILOT_SECTION}.{LLM_BACKEND_KEY}"
        )
        raw_overrides = section.get(OVERRIDES_KEY) or {}
        if not isinstance(raw_overrides, Mapping):
            raise BackendAssemblyError(
                f"形态配置 {PILOT_SECTION}.{OVERRIDES_KEY} 必须是逐环节映射"
                f"（键：{', '.join(OVERRIDE_KEYS)}）"
            )
        overrides: dict[str, str] = {}
        for slot, value in raw_overrides.items():
            if slot not in OVERRIDE_KEYS:
                raise BackendAssemblyError(
                    f"形态配置 {PILOT_SECTION}.{OVERRIDES_KEY} 出现未知环节 {slot!r}："
                    f"可覆盖环节为 {', '.join(OVERRIDE_KEYS)}（拼错即拒绝，不静默忽略）"
                )
            key = f"{PILOT_SECTION}.{OVERRIDES_KEY}.{slot}"
            overrides[slot] = (
                _llm_value(value, key) if slot == LLM_SLOT else _platform_value(value, key)
            )
        return cls(backend=backend, llm_backend=llm_backend, overrides=overrides)

    def with_runtime_overrides(
        self, *, backend: str | None = None, llm_backend: str | None = None
    ) -> BackendSelection:
        """应用 CLI 运行时全局覆盖（None 即保留声明值；取值同样逐键校验）。"""
        return replace(
            self,
            backend=self.backend if backend is None else _platform_value(backend, "--backend"),
            llm_backend=(
                self.llm_backend
                if llm_backend is None
                else _llm_value(llm_backend, "--llm-backend")
            ),
        )

    def resolved(self) -> dict[str, str]:
        """逐环节生效取值（排序固定便于机检）：覆盖优先，其次全局声明，最后默认。"""
        resolved = {LLM_SLOT: self.overrides.get(LLM_SLOT, self.llm_backend)}
        for slot in PLATFORM_SLOTS:
            resolved[slot] = self.overrides.get(slot, self.backend)
        return resolved


def _platform_value(value: Any, key: str) -> str:
    if value not in _ALLOWED_PLATFORM_BACKENDS:
        raise BackendAssemblyError(
            f"后端取值非法（{key}={value!r}）：只接受 "
            f"{' | '.join(_ALLOWED_PLATFORM_BACKENDS)}——换成配置取值，不改装配代码"
        )
    return str(value)


def _llm_value(value: Any, key: str) -> str:
    if value not in _ALLOWED_LLM_BACKENDS:
        raise BackendAssemblyError(
            f"网关后端取值非法（{key}={value!r}）：只接受 {' | '.join(_ALLOWED_LLM_BACKENDS)}"
        )
    return str(value)


@dataclass(frozen=True)
class PilotBackends:
    """一次运行的全部后端实现（唯一装配产物）；各阶段从这里取用，不各自 new。"""

    selection: BackendSelection
    resolved: Mapping[str, str]
    llm: Any  # MockBackend / HttpBackend
    gateway: LLMGateway
    storyboard: Any
    visual: Any
    sound: Mapping[str, Any]  # tts / sfx / music
    editing: Any
    promo: Any


def build_backends(
    configs,
    config_path: str | Path,
    *,
    backend: str | None = None,
    llm_backend: str | None = None,
) -> PilotBackends:
    """按 `pilot` 段（+ CLI 全局覆盖）装配全部后端——**唯一装配点**。

    构造即校验：声明真实后端的环节在此实例化真实适配器，缺凭证即抛
    `BackendAssemblyError`（带各适配器的既有缺凭证报错，指出缺哪个环境变量）——
    调用方（`build_runtime`）在落树/生成之前完成装配，故失败零成本、零落树。
    """
    selection = BackendSelection.from_yaml(config_path).with_runtime_overrides(
        backend=backend, llm_backend=llm_backend
    )
    resolved = selection.resolved()
    llm = _guard(LLM_SLOT, resolved[LLM_SLOT], lambda: _llm_backend(resolved[LLM_SLOT]))
    return PilotBackends(
        selection=selection,
        resolved=resolved,
        llm=llm,
        gateway=LLMGateway(
            llm,
            price_book=configs.screenplay.model_prices,
            sleep=lambda _: None,
            profiles=_llm_profiles_for(config_path),  # 功能 016：档案与价目随快照冻结
        ),
        storyboard=_guard(
            "storyboard", resolved["storyboard"], lambda: _storyboard(resolved["storyboard"])
        ),
        visual=_guard("visual", resolved["visual"], lambda: _visual(resolved["visual"], configs)),
        sound={
            gen_type: _guard(
                f"sound.{gen_type}",
                resolved["sound"],
                lambda gen_type=gen_type: _sound(gen_type, resolved["sound"], configs),
            )
            for gen_type in SOUND_TYPES
        },
        editing=_guard(
            "editing", resolved["editing"], lambda: _editing(resolved["editing"], configs)
        ),
        promo=_guard("promo", resolved["promo"], lambda: _promo(resolved["promo"], configs)),
    )


def _guard(slot: str, kind: str, factory):
    """装配单个后端槽位：凭证类失败转为统一的装配期拒绝（保留既有报错原文）。"""
    try:
        return factory()
    except _CREDENTIAL_ERRORS as exc:
        raise BackendAssemblyError(
            f"后端装配失败（{slot} 声明 {kind}，凭证缺失或不可用）：{exc}"
            "——声明真实后端即必须凭证就绪；此处拒绝启动（不静默回落模拟、不落树、不扣费）"
        ) from exc


def _llm_backend(kind: str):
    if kind == MOCK:
        from core.llm_gateway.backends.mock import MockBackend

        return MockBackend()
    from core.llm_gateway.backends.http import HttpBackend

    return HttpBackend()  # 构造器直接读环境变量（变量名由配置档案登记，见 llm.profiles）


def _storyboard(kind: str):
    if kind == SIMULATED:
        from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer

        return SimulatedStoryboardRenderer()
    from agents.storyboard.platform.http_real import HttpRealStoryboardRender

    return HttpRealStoryboardRender.from_env()


def _visual(kind: str, configs):
    if kind == SIMULATED:
        from agents.visual.platform.simulated import SimulatedVideoGen

        return SimulatedVideoGen(configs.visual.simulated_gen)
    from agents.visual.platform.http_real import HttpRealVideoGen

    return HttpRealVideoGen.from_env()


def _sound(gen_type: str, kind: str, configs):
    if kind == SIMULATED:
        from agents.sound.platform.simulated import (
            SimulatedMusicGen,
            SimulatedSFXGen,
            SimulatedTTSGen,
        )

        simulated = {
            "tts": SimulatedTTSGen,
            "sfx": SimulatedSFXGen,
            "music": SimulatedMusicGen,
        }
        return simulated[gen_type](configs.sound.simulated_gen, configs.sound.sample_rate)
    from agents.sound.platform.http_real import (
        HttpRealMusicGen,
        HttpRealSFXGen,
        HttpRealTTSGen,
    )

    real = {"tts": HttpRealTTSGen, "sfx": HttpRealSFXGen, "music": HttpRealMusicGen}
    return real[gen_type].from_env()


def _editing(kind: str, configs):
    if kind == SIMULATED:
        from agents.editing.platform.simulated import SimulatedEditRenderer

        return SimulatedEditRenderer(configs.editing.render)
    from agents.editing.platform.http_real import HttpRealEditRender

    return HttpRealEditRender.from_env()


def _promo(kind: str, configs):
    if kind == SIMULATED:
        from agents.promo.platform.simulated import SimulatedPlatform

        return SimulatedPlatform(configs.promo.simulated_platform)
    from agents.promo.platform.http_real import HttpRealPlatform

    return HttpRealPlatform.from_env()


def _llm_profiles_for(config_path: str | Path):
    """读形态配置的 LLM 档案（功能 016）：有 `llm` 段用新写法，否则按旧扁平写法迁移。

    解析失败即抛 `BackendAssemblyError`（缺项即报错，不静默给出空档案、不回落默认价）。
    """
    from core.llm_gateway.profiles import ProfileConfigError, load_or_migrate

    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    try:
        return load_or_migrate(payload if isinstance(payload, Mapping) else {})
    except ProfileConfigError as exc:
        raise BackendAssemblyError(f"LLM 档案解析失败（{config_path}）：{exc}") from exc
