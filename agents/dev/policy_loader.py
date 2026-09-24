"""链首策略装载（功能 018 阶段 2 / 契约 C2，宪章原则四例外条款的**三项替代约束**落点）。

`dev` 环节只在链首**装载人工策略并调 017 既有轮次入口**（零新增执行路径、零新增落树路径）；
本模块是该装载路径的**单一实现**（`ops/dev.py` 的 `_load_policy` 收敛为薄调用——两条装载
路径会漂移，静态检查/超时/零环境对象守护可能在一侧静默失效）。

装载三段（失败即 `PolicyLoadError`，**不静默取"最新/第一条"**）：

① **静态检查前置**（`policies.static_check.check_policy_source`）：未过即拒绝装载——不入策略
   历史、不落树、零网关调用（沙箱第一道防线，002 口径）；
② **版本核验**：版本必须等于源码 BLAKE3 前 12 位（人工版本同样可机检、可回溯）；
③ **实例化**（源码已过静态检查）：在**执行超时**内 `exec` 并构造 `Policy`。

例外的另两项替代约束（原则四末条）在此落机检：

- **执行超时**：静态检查不禁循环 ⇒ 装载与 `plan()` 执行都受超时上限约束（复用
  `core.degraded.compare.policy_execution_deadline` 的同一实现），超时判失败而不挂死宿主；
- **零环境对象 / 不触网关**：策略只被喂 `plan(inputs, config)`——**不交付**观测通道、真值探测
  接口、对象存储、账本或网关句柄（探测由宿主代执行），装载前置断言策略侧不带环境通道；
  策略本体不持凭证、不经网关、不触生成（网关调用由宿主 `run_dev_round` 发出并受 019 门禁约束）。

版本来源 = 形态配置部署指针 `deployment.dev.current_policy_version`（直读 YAML）；缺指针或
源码不存在 ⇒ **启动前拒绝**（不回落"最新/第一条"）。策略历史根与 `ops/dev.py` 的
`--policy-dir` 同源（默认 `policies/history`，相对进程工作目录）。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import blake3
import yaml

from agents.dev.policy_versions import DEFAULT_POLICY_HISTORY_ROOT, load_policy_source
from core.degraded.compare import (
    POLICY_EXECUTION_TIMEOUT_SECONDS,
    CompareError,
    assert_no_environment_objects,
    policy_execution_deadline,
)
from core.degraded.policy import PolicySubmissionError
from policies.static_check import StaticCheckError, check_policy_source

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LoadedPolicy",
    "PolicyLoadError",
    "deployed_policy_version",
    "load_deployed_policy",
    "load_policy",
    "load_policy_text",
]

# 策略版本长度（= 源码 BLAKE3 前 12 位；与 017/009 同口径）
POLICY_VERSION_LENGTH = 12
# 执行超时上限默认值：与回放沙盘（core.degraded.compare）**同一口径**
DEFAULT_TIMEOUT_SECONDS = POLICY_EXECUTION_TIMEOUT_SECONDS
# 部署指针位置（形态配置）：`deployment.dev.current_policy_version`
DEPLOYMENT_SECTION = "deployment"
AGENT_ID = "dev"


class PolicyLoadError(Exception):
    """策略装载被拒（未过静态检查 / 版本不符 / 源码不存在 / 缺部署指针 / 执行超时）。"""


@dataclass(frozen=True)
class LoadedPolicy:
    """装载产物：版本 + 来源 + 策略对象（对宿主暴露**只有** `plan(inputs, config)`）。

    本适配器是"策略执行"的唯一入口：`plan()` 在超时上限内调用策略本体，故宿主的每次策略
    执行都受超时约束（静态检查不禁循环）；策略拿不到任何句柄——交付面就是两个实参。
    """

    version: str
    origin: str
    policy: Any
    timeout_seconds: float | None = None

    @property
    def policy_version(self) -> str:
        return self.version

    def plan(self, inputs: Mapping, config: Any) -> Any:
        try:
            with policy_execution_deadline(self.timeout_seconds):
                return self.policy.plan(inputs, config)
        except CompareError as exc:
            raise PolicyLoadError(f"策略执行被截断（{self.origin}）：{exc}") from exc


def load_policy_text(
    source: str,
    *,
    declared_version: str | None = None,
    origin: str = "<inline>",
    namespace_name: str = "dev_policy",
    timeout_seconds: float | None = None,
) -> LoadedPolicy:
    """按源码文本装载策略（装载三段：静态检查 → 版本核验 → 超时内实例化）。

    入参 = 策略源码文本 + 声明版本（可空）+ 实例化上下文（来源标识与命名空间）+ 超时上限；
    **不接收任何环境句柄**（对象存储/账本/网关/引擎都不在此签名内，见模块 docstring）。
    """
    if not isinstance(source, str) or not source.strip():
        raise PolicyLoadError("策略源码不能为空（人工策略 = 人编写的代码）")
    try:
        check_policy_source(source)  # ① 静态检查前置（未过即拒绝装载，0 网关 0 落树）
    except StaticCheckError as exc:
        raise PolicyLoadError(f"策略静态检查未通过（拒绝装载）：{exc}") from exc
    version = blake3.blake3(source.encode()).hexdigest()[:POLICY_VERSION_LENGTH]
    if declared_version and declared_version != version:
        raise PolicyLoadError(
            f"策略版本 {declared_version!r} 与源码内容不符（源码哈希前 "
            f"{POLICY_VERSION_LENGTH} 位为 {version!r}）——版本必须等于源码 "
            f"BLAKE3 前 {POLICY_VERSION_LENGTH} 位"
        )
    namespace: dict = {"__name__": namespace_name, "__file__": origin}
    try:
        with policy_execution_deadline(timeout_seconds):
            exec(compile(source, origin, "exec"), namespace)  # noqa: S102 - 已过静态检查
            policy_class = namespace.get("Policy")
            if not isinstance(policy_class, type):
                raise PolicyLoadError(f"策略源码缺少 Policy 类：{origin}")
            policy = policy_class()
            if not callable(getattr(policy, "plan", None)):
                raise PolicyLoadError(f"策略缺少 plan(inputs, config) 接口：{origin}")
            policy.policy_version = version  # 节点与运营表落盘口径（人工策略版本）
    except PolicyLoadError:
        raise
    except CompareError as exc:
        raise PolicyLoadError(f"策略装载被超时截断（{origin}）：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 - 源码执行失败即拒绝（不进入产出）
        raise PolicyLoadError(f"策略源码执行失败（{origin}）：{exc}") from exc
    loaded = LoadedPolicy(
        version=version, origin=origin, policy=policy, timeout_seconds=timeout_seconds
    )
    # 零环境对象守护：策略侧不得持任何环境通道（真值探测由宿主代执行）
    assert_no_environment_objects(loaded.policy)
    return loaded


def load_policy(
    *,
    version: str,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    timeout_seconds: float | None = None,
) -> LoadedPolicy:
    """按版本从策略历史装载（源码不存在即拒绝，不回落其他版本）。"""
    path = Path(history_root) / AGENT_ID / f"{version}.py"
    try:
        source = load_policy_source(version, history_root=history_root)
    except PolicySubmissionError as exc:
        raise PolicyLoadError(f"策略版本 {version!r} 的源码不存在：{path}（{exc}）") from exc
    return load_policy_text(
        source,
        declared_version=version,
        origin=str(path),
        timeout_seconds=timeout_seconds,
    )


def deployed_policy_version(config_path: str | Path) -> str:
    """读形态配置的部署指针 `deployment.dev.current_policy_version`（直读 YAML）。

    缺段/缺键/取值非法 ⇒ 拒绝启动（**不**回落"最新/第一条"策略：部署对象必须是显式声明的
    那个版本，否则链首产出会随策略历史漂移）。
    """
    path = Path(config_path)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyLoadError(f"形态配置文件不可读：{path}（{exc}）") from exc
    pointer = None
    if isinstance(payload, Mapping):
        section = payload.get(DEPLOYMENT_SECTION)
        if isinstance(section, Mapping):
            pointer = section.get(AGENT_ID)
    version = pointer.get("current_policy_version") if isinstance(pointer, Mapping) else None
    if not isinstance(version, str) or not version:
        raise PolicyLoadError(
            f"形态配置缺少部署指针 {DEPLOYMENT_SECTION}.{AGENT_ID}.current_policy_version：{path}"
            "——缺指针即拒绝启动（不回落最新/第一条策略）"
        )
    return version


def load_deployed_policy(
    config_path: str | Path,
    *,
    history_root: str | Path = DEFAULT_POLICY_HISTORY_ROOT,
    timeout_seconds: float | None = None,
) -> LoadedPolicy:
    """按形态配置的部署指针装载链首人工策略（缺指针/缺源码 ⇒ 启动前拒绝）。"""
    return load_policy(
        version=deployed_policy_version(config_path),
        history_root=history_root,
        timeout_seconds=timeout_seconds,
    )
