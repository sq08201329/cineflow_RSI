"""内联配置字典夹具的插件声明面同步（021 T2125；测试侧公共夹具构造）。

`tests/**` 内的内联配置字典夹具多数从 `configs/movie.yaml` 深拷贝再定向改值，缺
`evaluators` 段即装配期报错（**实现里禁止"缺段回落硬编码装配"的兜底**）。本模块提供
两件事，仅**测试侧**使用：

1. `plugin_declarations(payload)` —— 从真实 `configs/movie.yaml` 复制 `evaluators.plugins`
   结构（`impl` / `params` / 槽位与 `evaluator_id` 键集），供不改版本相关取值的夹具直接采用；
2. `resync_plugin_versions(payload, ...)` —— 夹具**改了版本相关取值**（阈值/窗口/规则库等，
   它们经 `implementation_version(*parts)` 进版本号）时，按该夹具的实际取值**重新钉住**
   每一处 `version`：声明值必须与实现产出**逐字相等**（021 C4），否则装配期报错。
   钉法 = 按 `impl` 解析出目标可调用对象、注入 `agent_config`（+ 需要的 `gateway`/`artifacts`）
   调用一次读 `spec.version`——与唯一的装配点同口径，**不是**第二套装配路径（它只取版本值，
   不装配、不注册、不返回实例）。
"""

from __future__ import annotations

import copy
import importlib
import inspect
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
_REFERENCE_CONFIG = REPO_ROOT / "configs" / "movie.yaml"

_INJECTION_SLOTS = ("agent_config", "gateway", "artifacts", "registry")


def _loaders():
    """agent → 该 Agent 配置类的 `from_dict`（惰性导入，避免与 conftest 的导入顺序耦合）。"""
    from agents.dev.config import DevConfig
    from agents.editing.config import EditingConfig
    from agents.screenplay.config import ScreenplayConfig
    from agents.sound.config import SoundConfig
    from agents.storyboard.config import StoryboardConfig
    from agents.visual.config import VisualConfig

    return {
        "dev": DevConfig.from_dict,
        "editing": EditingConfig.from_dict,
        "screenplay": ScreenplayConfig.from_dict,
        "sound": SoundConfig.from_dict,
        "storyboard": StoryboardConfig.from_dict,
        "visual": VisualConfig.from_dict,
    }


def reference_plugins() -> dict:
    """真实 `configs/movie.yaml` 的 `evaluators.plugins` 子树（深拷贝）。"""
    document = yaml.safe_load(_REFERENCE_CONFIG.read_text(encoding="utf-8"))
    return copy.deepcopy(document["evaluators"]["plugins"])


def resync_plugin_versions(
    payload: dict,
    *,
    agents: tuple[str, ...] | None = None,
    injections: dict | None = None,
    configs: dict | None = None,
) -> dict:
    """就地钉住 `payload["evaluators"]["plugins"][<agent>]` 各条的 `version` 并返回 payload。

    - 缺 `evaluators` 段 ⇒ 先从真实配置复制结构（`impl`/`params`/键集）；
    - `agents` 缺省 = 声明面里已有的 Agent 键（即需要同步的那些）；
    - `injections` 提供 judge 类工厂需要的 `gateway` / `artifacts`（默认无 ⇒ 只同步不需要者；
      `visual` 的 judge 工厂会真实产出锚点工件，故同步 `visual` 时必须给 `artifacts`）；
    - `configs` 提供**已派生**的 Agent 配置对象（如排练档 `apply_rehearsal_scale` 的生效配置），
      优先于按 `payload` 现构造——运行期会 `dataclasses.replace` 体量键的形态必须按生效取值钉。
    """
    provided = dict(injections or {})
    prebuilt = dict(configs or {})
    plugins = payload.setdefault("evaluators", {}).setdefault("plugins", {})
    if not plugins:
        plugins.update(reference_plugins())
    targets = agents or tuple(plugins)
    loaders = _loaders()
    for agent in targets:
        declarations = plugins.get(agent)
        if declarations is None:
            continue
        agent_config = prebuilt.get(agent) or loaders[agent](payload)
        for entries in declarations.values():
            for leaf in entries.values():
                leaf["version"] = _derived_version(leaf["impl"], agent_config, provided)
    return payload


def _derived_version(impl: str, agent_config, provided: dict) -> str:
    module_name, attr = impl.split(":")
    target = importlib.import_module(module_name)
    for segment in attr.split("."):
        target = getattr(target, segment)
    kwargs = {"agent_config": agent_config}
    for name in inspect.signature(target).parameters:
        # 与唯一装配点同款"按签名 opt-in"；本处只取 `spec.version`，未提供的槽位值不参与版本口径
        if name in _INJECTION_SLOTS and name != "agent_config":
            kwargs[name] = provided.get(name)
    return target(**kwargs).spec.version
