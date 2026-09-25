"""视觉 Agent 的绑定薄工厂与槽位布局（021 C1/C3）。

一评估器一函数、**纯关键字签名**：既有参数从 `agent_config` 槽位读取（**零拷贝**——
单一事实源仍是 `VisualConfig` 的 dataclass 字段与配置原段），故声明面 `params: {}`。
`SLOT_LAYOUT` 是槽位名的**唯一映射声明**，与 `build_evaluators` 的返回形状一一对齐
（`all` 是装配点按本顺序拼接的派生汇总键，不可声明）。

`_judge_anchor_hashes` 自 `agents/visual/loop.py` 整体迁入（语义逐字不变）；`_` 前缀私有
辅助不计入"孤立插件"扫描面——本模块公开函数必须全部被 `configs/*.yaml` 的 `impl` 引用。
"""

from agents.visual.config import VisualConfig
from agents.visual.evaluators.aesthetic import AestheticEvaluator
from agents.visual.evaluators.cinematic import CinematicJudgeEvaluator
from agents.visual.evaluators.flicker import FlickerEvaluator
from agents.visual.evaluators.format_compliance import FormatComplianceEvaluator
from agents.visual.evaluators.identity import IdentityConsistencyEvaluator
from agents.visual.platform.simulated import encode_mp4, render_frames
from core.evaluators.base import Evaluator
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import ArtifactStore

AGENT = "visual"
SLOT_LAYOUT = ("compliance", "proxies", "judge")
SINGLE_EVALUATOR_SLOTS = ("compliance", "judge")


def format_compliance(*, agent_config: VisualConfig) -> Evaluator:
    return FormatComplianceEvaluator(agent_config.clip_spec)


def aesthetic(*, agent_config: VisualConfig) -> Evaluator:
    return AestheticEvaluator(agent_config.frame_sampling)


def identity_consistency(*, agent_config: VisualConfig) -> Evaluator:
    return IdentityConsistencyEvaluator(agent_config.frame_sampling)


def flicker(*, agent_config: VisualConfig) -> Evaluator:
    return FlickerEvaluator(agent_config.frame_sampling)


def cinematic(
    *, agent_config: VisualConfig, gateway: LLMGateway, artifacts: ArtifactStore
) -> Evaluator:
    return CinematicJudgeEvaluator(
        gateway,
        model=_judge_model(agent_config),
        prompts=list(agent_config.judge["prompts"]),
        anchor_hashes=_judge_anchor_hashes(agent_config, artifacts),
        sampling_spec=agent_config.frame_sampling,
        max_tokens=agent_config.judge["max_tokens"],
    )


def _judge_model(config: VisualConfig) -> str:
    """judge 经网关调用的模型（价目表必须覆盖；缺价目网关即报错）。"""
    return config.judge.get("model", "mock-copy-v1")


def _judge_anchor_hashes(config: VisualConfig, artifacts: ArtifactStore) -> list[str]:
    """锚点集（决策 5）：configs 固定生成参数集经确定性模拟生成器产出锚点工件。

    参数哈希与工件哈希双双进入 judge 版本号；真实环境切换为固定素材的
    工件哈希清单，代码路径不变。
    """
    hashes = []
    for anchor_params in config.judge["anchor_gen_params"]:
        frames = render_frames(anchor_params, config.simulated_gen)
        hashes.append(artifacts.put(encode_mp4(frames, fps=config.clip_spec["fps"])))
    return hashes


def _to_return_shape(assembled: dict) -> dict:
    """把装配点的槽位映射（每槽位 `list`）还原为该 Agent **既有返回形状**：单评估器槽位取
    首元素，列表槽位（含派生的 `all`）原样——签名与返回形状逐字不变是 021 C2 的兼容承诺。

    **零实例（= 声明面未声明的槽位）不产出该键**：返回形状**如实反映已声明集合**，下游据此
    把"该槽位未声明"当**明确语义**处理（跳过依赖该槽位的工作并如实标注，或显式报错）——
    不猜、不补兜底默认。既有两形态全槽位声明 ⇒ 返回形状逐字不变（021 T2111 基线）。
    """
    return {
        slot: (instances[0] if slot in SINGLE_EVALUATOR_SLOTS else instances)
        for slot, instances in assembled.items()
        if instances
    }
