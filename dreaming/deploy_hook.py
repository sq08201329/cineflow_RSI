"""做梦轮次收口后的部署评估接线（功能 014 T1416，契约 C6 唯一入口）。

**只做一件事**：把一轮做梦的胜出者交给 `core.deployment.auto_deploy.evaluate_candidate`
（唯一入口），并把评估摘要交回 `run_dream_round` 记入轮次报告。部署逻辑一律在 core
（原则五：单向依赖 dreaming → core）——本模块不写指针、不写门禁判据、不新造口径。

证据来源（本批已交付的口径入口；缺失即 `missing` → 判定拦截，绝不推测放行）：
- `unbiasedness`：无偏性验收结论（002 `verify_unbiasedness` 产物，JSON）；
- `reward_compare`：候选 vs 现部署的**池化回放对比产物**（011 口径；由调用方给出引用）；
- `validation_rewards`：validation 池逐版本 reward（005 口径现场推导名次）；
- `judge_keys` / `drift_registry`：相关 judge 版本与 012 状态登记。

模式从 `deployment/mode.json` 读真实状态（唯一入口内读取）：`manual` 只落快照、
`shadow` 落快照 + 影子事件、`auto` 才可能部署——**接线本身不改变任何模式**。
"""

from collections.abc import Callable
from pathlib import Path

from core.deployment.auto_deploy import evaluate_candidate
from core.deployment.config import DeploymentConfig
from core.deployment.models import HumanDecision

DEFAULT_DEPLOYMENT_DIR = Path("deployment")
DEFAULT_DEPLOY_CONFIG = Path("configs/movie.yaml")


def current_policy_version(agent_id: str, config_path: str | Path) -> str | None:
    """读部署指针（configs 的 deployment.{agent}.current_policy_version）；缺失 → None。

    与 `dreaming.approve.current_policy_version` 的差别：此处**不做 SC-005 审批机检**
    （那是人工采纳路径的守卫）——评估阶段只需知道"现在部署的是哪个版本"，无指针即
    "无现部署版本"（证据缺失，判拦截）。
    """
    import yaml

    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return (payload.get("deployment") or {}).get(agent_id, {}).get("current_policy_version")


def deployment_hook(
    *,
    data_dir: str | Path = DEFAULT_DEPLOYMENT_DIR,
    config_path: str | Path = DEFAULT_DEPLOY_CONFIG,
    cfg: DeploymentConfig | None = None,
    unbiasedness: object = None,
    reward_compare: dict | None = None,
    validation_rewards: dict | None = None,
    validation_source: str = "",
    judge_keys: tuple = (),
    drift_registry: object = None,
    human_decision: HumanDecision | str = HumanDecision.NONE,
    unacceptable: bool = False,
    at: str | None = None,
) -> Callable[[object], dict]:
    """装配收口钩子：`run_dream_round(..., deploy_hook=deployment_hook(...))`。

    证据参数由调用方（ops/CLI/demo）注入；未注入的一律按缺失处理（判拦截并留痕）。
    """
    resolved_cfg = cfg or DeploymentConfig.from_yaml(config_path)

    def hook(round_) -> dict:
        if not round_.winner_version:
            return {
                "status": "skipped",
                "note": f"轮次 {round_.round_id} 无胜出者（{round_.status}）：无候选可评估",
            }
        deployed_version = current_policy_version(round_.agent_id, config_path)
        outcome = evaluate_candidate(
            round_.agent_id,
            round_.winner_version,
            cfg=resolved_cfg,
            data_dir=data_dir,
            deployed_version=deployed_version,
            unbiasedness=unbiasedness,
            reward_compare=reward_compare,
            validation_rewards=validation_rewards,
            validation_source=validation_source,
            judge_keys=judge_keys,
            drift_registry=drift_registry,
            human_decision=human_decision,
            unacceptable=unacceptable,
            config_path=config_path,
            at=at,
        )
        summary = outcome.to_dict()
        summary["round_id"] = round_.round_id
        summary["status"] = "evaluated"
        return summary

    return hook
