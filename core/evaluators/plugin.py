"""插件声明面解析与唯一装配点（021 C1~C4，业务无关）。

职责只有两件：把形态配置里的 `evaluators.plugins.<agent>` 声明解析成**保序清单**
（`parse_manifest`），再用 `importlib` 解析 `impl`、按签名注入槽位并调用（`assemble`）。

三条口径决定本模块的形状：

- **零业务词汇**：槽位名由调用方以 `slots`（业务侧 `SLOT_LAYOUT`）传入，本模块不出现任何
  Agent 名与形态名，也不 import `agents.*`（原则五单向依赖）；
- **配置声明是唯一注册面**：目录扫描 / `entry_points` / 导入即注册**都不存在**——目录里有
  插件文件但未声明 ⇒ 装配集合缺项 ⇒ 报错（"目录不决定可用性"）；
- **缺声明即报错、不取码内默认**：`required(impl) == set(params) | 注入槽位集` 是这条口径的
  机检形式；`params` 缺一个、多一个，或目标签名残留默认值 / `*args` / `**kwargs`，一律拒绝。

`slots` 的两义由入参区分、无人工特例分支：单槽 `("all",)` 的 Agent 其返回即扁平列表
（`all` = 列表本体）；多槽 Agent 的 `all` 是装配点按 `slots` 顺序拼接的**派生汇总键**，
声明它即报错。
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.evaluators.base import Evaluator, EvaluatorKind, EvaluatorSpec
from core.evaluators.errors import PluginAssemblyError, PluginDeclarationError

INJECTION_SLOTS = ("agent_config", "gateway", "artifacts", "registry")

_LEAF_KEYS = ("impl", "version", "params")
_DERIVED_KEY = "all"
_UNSPECIFIED = "<未声明>"

_KIND_BY_PREFIX = {
    "rule.": EvaluatorKind.RULE,
    "proxy.": EvaluatorKind.PROXY_MODEL,
    "judge.": EvaluatorKind.JUDGE,
    "human.": EvaluatorKind.HUMAN,
}


@dataclass(frozen=True)
class PluginDeclaration:
    """单条插件声明（叶子 `impl`/`version`/`params` 已校验，保序）。"""

    slot: str
    evaluator_id: str
    impl: str
    version: str
    params: dict
    path: str

    @property
    def key(self) -> str:
        return f"{self.evaluator_id}@{self.version}"


@dataclass(frozen=True)
class PluginManifest:
    """解析后的装配清单：槽位首现顺序 + 槽位内声明顺序（`yaml.safe_load` 保序）。"""

    agent: str
    slots: tuple[str, ...]
    declarations: tuple[PluginDeclaration, ...]

    def by_slot(self) -> tuple[tuple[str, tuple[PluginDeclaration, ...]], ...]:
        return tuple(
            (slot, tuple(decl for decl in self.declarations if decl.slot == slot))
            for slot in self.slots
        )


def declaration_subtree(document: Mapping | None, agent: str) -> Mapping | None:
    """取 `evaluators.plugins.<agent>` 子树的**逐字拷贝**；缺任一层 ⇒ `None`（不补默认）。

    各 `*Config` 的实现用本函数承载声明面：`None` 这一"缺失事实"原样保留到装配期，
    由 `parse_manifest` 抛 `PluginDeclarationError`（"缺段即报错、不回落硬编码装配"）。
    """
    if not isinstance(document, Mapping):
        return None
    section = document.get("evaluators")
    if not isinstance(section, Mapping):
        return None
    plugins = section.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    if agent not in plugins:
        return None
    return plugins[agent]


def parse_manifest(
    document: Mapping | None, agent: str, *, slots: tuple[str, ...]
) -> PluginManifest:
    """解析 `<agent>` 的插件声明（唯一声明面入口）。

    `document` = 该 Agent 的声明子树（各 `*Config.plugin_declarations` 的逐字拷贝）；
    为 `None`（缺 `evaluators` 段 / 缺 `plugins` / 缺本 Agent 子键 ⇒ 不补默认）即报错。
    亦接受完整形态配置文档（含 `evaluators` 段），此时自动定位 `evaluators.plugins.<agent>`。
    """
    if document is None:
        raise PluginDeclarationError(
            f"缺插件声明段：evaluators.plugins.{agent}（实测 {_UNSPECIFIED}；"
            "缺段即报错，不回落硬编码装配）"
        )
    if not isinstance(document, Mapping):
        raise PluginDeclarationError(
            f"evaluators.plugins.{agent} 必须为映射，实际为 {type(document).__name__}"
        )
    if "evaluators" in document:
        document = _locate_in_document(document, agent)

    declarations: list[PluginDeclaration] = []
    for slot, slot_value in document.items():
        if slot not in slots:
            raise PluginDeclarationError(
                f"槽位非法：evaluators.plugins.{agent}.{slot}（实测槽位 {slot!r}；"
                f"{agent} 的可声明槽位为 {list(slots)}）"
            )
        if not isinstance(slot_value, Mapping):
            raise PluginDeclarationError(
                f"evaluators.plugins.{agent}.{slot} 必须为映射，实际为 {type(slot_value).__name__}"
            )
        for evaluator_id, leaf in slot_value.items():
            declarations.append(_parse_leaf(agent, slot, evaluator_id, leaf))
    return PluginManifest(agent=agent, slots=tuple(slots), declarations=tuple(declarations))


def assemble(
    manifest: PluginManifest,
    *,
    agent_config: Any,
    gateway: Any = None,
    artifacts: Any = None,
    registry: Any = None,
) -> dict[str, list[Evaluator]]:
    """按清单装配：解析 `impl` → 按签名注入槽位 → 纯关键字调用 → 逐项一致性校验。

    返回槽位映射（`slots` 顺序）；多槽 Agent 追加按 `slots` 顺序拼接的派生键 `all`。
    仅在提供 `registry` 时逐实例 `registry.register`（每次装配得到独立实例）。
    """
    if not isinstance(manifest, PluginManifest):
        raise PluginAssemblyError(
            f"manifest 必须为 PluginManifest，实际为 {type(manifest).__name__}"
        )
    provided = {
        "agent_config": agent_config,
        "gateway": gateway,
        "artifacts": artifacts,
        "registry": registry,
    }
    _require_weights_parity(manifest, agent_config)
    assembled: dict[str, list[Evaluator]] = {}
    for slot, declarations in manifest.by_slot():
        instances: list[Evaluator] = []
        for declaration in declarations:
            instances.append(_instantiate(declaration, provided, registry))
        assembled[slot] = instances

    if _DERIVED_KEY not in manifest.slots:
        assembled[_DERIVED_KEY] = [ev for slot in manifest.slots for ev in assembled[slot]]
    return assembled


def _locate_in_document(document: Mapping, agent: str) -> Mapping:
    section = document.get("evaluators")
    if not isinstance(section, Mapping):
        raise PluginDeclarationError(
            f"缺少 evaluators 段（实测 {type(section).__name__}；新形态接入必须声明插件面）"
        )
    plugins = section.get("plugins")
    if not isinstance(plugins, Mapping):
        raise PluginDeclarationError(f"缺少 evaluators.plugins 段（实测 {type(plugins).__name__}）")
    if agent not in plugins:
        raise PluginDeclarationError(
            f"缺少 evaluators.plugins.{agent} 子键（实测键 {sorted(plugins)}）"
        )
    return plugins[agent]


def _parse_leaf(agent: str, slot: str, evaluator_id: Any, leaf: Any) -> PluginDeclaration:
    path = f"evaluators.plugins.{agent}.{slot}.{evaluator_id}"
    if not isinstance(evaluator_id, str) or not evaluator_id:
        raise PluginDeclarationError(
            f"{path} 的 <evaluator_id> 必须为非空字符串，实际为 {evaluator_id!r}"
        )
    if not isinstance(leaf, Mapping):
        raise PluginDeclarationError(f"{path} 必须为映射，实际为 {type(leaf).__name__}")
    missing = sorted(set(_LEAF_KEYS) - set(leaf))
    extra = sorted(set(leaf) - set(_LEAF_KEYS))
    if missing or extra:
        raise PluginDeclarationError(
            f"{path} 的叶子键必须恰为 {list(_LEAF_KEYS)}："
            f"缺 {missing}、多 {extra}（实测键 {sorted(leaf)}）"
        )

    impl = leaf["impl"]
    if not isinstance(impl, str) or impl.count(":") != 1:
        raise PluginDeclarationError(
            f"{path}.impl 必须为 '<module>:<attr>'（恰一个冒号），实际为 {impl!r}"
        )
    module, attr = impl.split(":")
    if not module or not attr:
        raise PluginDeclarationError(f"{path}.impl 的模块或属性为空，实际为 {impl!r}")

    version = leaf["version"]
    if not isinstance(version, str) or not version:
        raise PluginDeclarationError(
            f"{path}.version 必须为非空字符串（与实现产出逐字相等），实际为 {version!r}"
        )

    params = leaf["params"]
    if not isinstance(params, Mapping):
        raise PluginDeclarationError(
            f"{path}.params 必须为映射（既有评估器一律 {{}}），实际为 {type(params).__name__}"
        )
    if any(not isinstance(name, str) or not name for name in params):
        raise PluginDeclarationError(f"{path}.params 的键必须为非空字符串，实际为 {list(params)}")
    if "version" in params:
        raise PluginDeclarationError(f"{path}.params 不得承载 version（版本号是叶子键）")
    return PluginDeclaration(
        slot=slot,
        evaluator_id=evaluator_id,
        impl=impl,
        version=version,
        params=dict(params),
        path=path,
    )


def _instantiate(declaration: PluginDeclaration, provided: Mapping, registry: Any) -> Evaluator:
    target = _resolve(declaration)
    signature = inspect.signature(target)
    variadic = sorted(
        name
        for name, parameter in signature.parameters.items()
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    )
    if variadic:
        raise PluginAssemblyError(
            f"{declaration.path}.impl 的目标签名含可变参数 {variadic}"
            "（禁止用 *args/**kwargs 吸收未知声明）"
        )

    required = {
        name
        for name, parameter in signature.parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    injected = {name: provided[name] for name in signature.parameters if name in INJECTION_SLOTS}
    for name, value in injected.items():
        if value is None:
            raise PluginAssemblyError(
                f"{declaration.path}.impl 声明了注入槽位 {name!r}，但装配点未提供（值为 None）"
            )
    expected = set(declaration.params) | set(injected)
    if required != expected:
        undeclared = sorted(required - expected)
        unused = sorted(expected - required)
        raise PluginAssemblyError(
            f"{declaration.path} 参数严格性不符：impl 无默认值参数 {sorted(required)}、"
            f"声明 params 键 {sorted(declaration.params)}、注入槽位 {sorted(injected)}；"
            f"缺声明 {undeclared}（既未在 params 声明、也不属注入槽位 {list(INJECTION_SLOTS)}）、"
            f"多声明 {unused}（签名不接收；码内默认值不生效）"
        )

    result = target(**declaration.params, **injected)
    return _validate_result(declaration, result, registry)


def _resolve(declaration: PluginDeclaration) -> Any:
    module_name, attr = declaration.impl.split(":")
    try:
        target = importlib.import_module(module_name)
        for segment in attr.split("."):
            target = getattr(target, segment)
    except (ImportError, AttributeError) as exc:
        raise PluginAssemblyError(
            f"{declaration.path}.impl 不可解析（实测 {declaration.impl!r}）：{exc}"
        ) from exc
    if not callable(target):
        raise PluginAssemblyError(
            f"{declaration.path}.impl 必须指向可调用对象（实测 {declaration.impl!r} 为 "
            f"{type(target).__name__}）"
        )
    return target


def _validate_result(declaration: PluginDeclaration, result: Any, registry: Any) -> Evaluator:
    spec = getattr(result, "spec", None)
    if not isinstance(spec, EvaluatorSpec):
        raise PluginAssemblyError(
            f"{declaration.path}.impl 未产出评估器实例（实测 {declaration.impl!r} → "
            f"{type(result).__name__}；缺 EvaluatorSpec）"
        )
    if spec.evaluator_id != declaration.evaluator_id:
        raise PluginAssemblyError(
            f"{declaration.path} 的 evaluator_id 不一致：声明 {declaration.evaluator_id!r}、"
            f"实现 {spec.evaluator_id!r}"
        )
    expected_kind = _KIND_BY_PREFIX.get(declaration.evaluator_id.split(".")[0] + ".")
    if expected_kind is None:
        raise PluginAssemblyError(
            f"{declaration.path} 的 evaluator_id 前缀无法判定 kind："
            f"实测 {declaration.evaluator_id!r}；"
            f"允许前缀 {sorted(_KIND_BY_PREFIX)}"
        )
    if spec.kind is not expected_kind:
        raise PluginAssemblyError(
            f"{declaration.path} 的前缀与 kind 不一致："
            f"前缀 {declaration.evaluator_id.split('.')[0]!r} "
            f"应为 {expected_kind.value!r}、实现为 {spec.kind!r}"
        )
    if spec.version != declaration.version:
        raise PluginAssemblyError(
            f"{declaration.path} 声明的 version 与实现的 version 不一致："
            f"声明 {declaration.version!r}、实现 {spec.version!r}（不静默取任一侧）"
        )
    if registry is not None:
        registry.register(result)
    return result


def _require_weights_parity(manifest: PluginManifest, agent_config: Any) -> None:
    """装配集合与权重键集必须一一对应（声明 ↔ 权重，缺项/多项即拒绝装配）。

    在实例化**之前**按声明面核对：声明与装配（一个声明恰产出一个实例，由逐条
    `evaluator_id` 一致性校验保证）同构，故两项等价；先核对可让"配置漂移"这一最常见的
    错误拿到既有中文文案，而不是被某个评估器的版本/参数错误抢先。
    """
    weights = getattr(agent_config, "evaluator_weights", None)
    if not isinstance(weights, Mapping) or not weights:
        raise PluginAssemblyError(f"agent_config 缺少 evaluator_weights 键集（实测 {weights!r}）")
    declared = {declaration.evaluator_id for declaration in manifest.declarations}
    weighted = set(weights)
    if declared != weighted:
        raise PluginAssemblyError(
            f"evaluator_weights.{manifest.agent} 与装配的评估器不一致："
            f"缺权重键 {sorted(declared - weighted)}、多余权重键 {sorted(weighted - declared)}"
        )
