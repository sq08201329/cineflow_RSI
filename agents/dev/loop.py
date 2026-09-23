"""开发 Agent 轮次循环（契约 C11~C13）：人工策略驱动一轮**单一产出**。

一轮产出：输入预检（缺题材边界/受众 → 执行前拒绝，0 副作用）→ 人工策略产立项组合计划
（探索哪些题材方向、分支数、组合取舍）→ 计划执行前校验（缺结构标记/形状非法 → 拒绝且
0 网关调用 0 成本，同 009 的阶段预检纪律）→ **逐条目**经网关生成立项论证要点（唯一昂贵动作，
原则三：缓存键 = 模型 + 提示词 + 采样参数，响应哈希落盘供回放核对）→ 工件 = 计划结构 +
网关正文，内容寻址入对象存储 → 评估器打分（评估器协议注入 + gate 短路 + 定点归一）→
节点一次性 INSERT 冻结 + 成本三方对账。

**单一产出**（澄清第 8 条：立项组合是唯一交付物、不设阶段划分）：运营表唯一键为
`(round_id, params_hash)`、job_id 由 round_id 确定性派生；重复触发撞树锚点 → 幂等重建
（`DuplicateError → _reconstruct`：0 重复生成、0 重复扣费、0 重复节点，对账字段留空不伪造）。

**回放纪律**（C13）：`slate_match_key` 只含**策略可复现的结构键**（立项约束摘要 + 组合区间 +
策略版本 + 模型 + 温度 + 输出预算），**不含生成产物摘要**——产物一次性且不可复现，入键即
命中率归零（007 教训）。

**评估器装配**：`evaluators` 为注入点——`None` 时装配真实四评估器（`build_dev_evaluators`，
两门禁 + 两模拟数据源驱动的确定性代理）；显式注入（回放重算 / 测试桩）按 `rule.*` 前缀
分列门禁与代理。合成走 dev 合成口径（门禁先行、任一判 0 短路不跑代理 + 适用权重归一 +
定点 6 位），口径常量随 `config_snapshot` 冻结（版本元信息，历史节点不重算）。

**失败隔离**（原则二）：网关失败 / 工件构造失败 / 评估器崩溃一律落 FAILED 节点 + 运营表
failed 行，**成本照常入账**（预估即上界，actual ≤ estimated 由 schema CHECK 兜底）。
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import blake3
from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

from agents.dev.artifact import TopicSlate, simulated_signal_sources
from agents.dev.config import DevConfig
from agents.dev.db import dev_jobs
from agents.dev.evaluators import COMPOSITE_POLICY, build_dev_evaluators, evaluate_dev
from core.evaluators.base import ArtifactRef, Evaluator
from core.llm_gateway.gateway import GatewayError, LLMGateway
from core.llm_gateway.profiles import gateway_profile_snapshot, with_llm_profiles
from core.llm_gateway.routing import Role
from core.tree.artifacts import ArtifactStore
from core.tree.errors import DuplicateError, ValidationError
from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode
from core.tree.store import TreeStore

AGENT_ID = "dev"
PROJECT_ID = "dev"
# 生成走确定性档（缓存收敛非确定性，原则三）
TEMPERATURE = 0.0
# 未产出工件的节点占位哈希（拒绝/失败节点无工件可引）
PLACEHOLDER_HASH = "00" * 32
# 执行前校验用占位论证要点（仅试构造工件，不落库、不调网关）
PLACEHOLDER_RATIONALE = "（待生成：仅供执行前校验）"

# 门禁语义前缀（与 dev 合成口径一致：`rule.*` 且判 0 即短路）
_GATE_PREFIX = "rule."
_ENTRY_KEYS = ("direction_id", "genre", "constraints", "characters")
# 条目可选键：论证草稿（人写的判断，入提示词，最终正文仍由网关生成）与条目级分量呈现
_OPTIONAL_ENTRY_KEYS = ("rationale_seed", "eval_components")


class DevLoopError(Exception):
    """开发 Agent 轮次循环错误（输入预检/计划预检/评估器装配等）。"""


class DevPolicy(Protocol):
    """题材方向探索策略协议（降级模式：策略由人工编写，不自动进化）。

    `plan(inputs, config)` 产**立项组合计划** `{entries, production_marks}`：条目给方向标识与
    可移交下游的题材/约束/角色设定要点（策略只定结构，论证要点正文由网关生成）；
    `production_marks` 给"本轮进入生产"的指向（数量与指向合法性由组合门禁判定，不由代码兜底）。
    """

    policy_version: str

    def plan(self, inputs: dict, config: DevConfig) -> dict: ...


@dataclass(frozen=True)
class DevRoundResult:
    """轮次收口：单一产出 job 汇总 + 成本对账。"""

    round_id: str
    tree_id: str
    policy_version: str
    # {"job_id","status","reason","artifact_hash","cache_key","response_hash"}
    job: dict
    spent_usd: float
    cost_reconciliation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def round_tree_id(round_id: str) -> str:
    """轮次树的确定性标识（幂等重建的依据）。"""
    return f"dev-round-{round_id}"


def _round_root_id(round_id: str) -> str:
    return f"{round_tree_id(round_id)}-root"


def _job_id(round_id: str) -> str:
    """单一产出的 job id（round_id 确定性派生，无阶段分量）。"""
    return f"{round_id}-slate"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value) -> str:
    """规范化 JSON（键排序）：参数哈希、提示词摘要与调用摘要的确定性底座。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def slate_match_key(*, policy_version: str, inputs: dict, config: DevConfig) -> dict:
    """回放匹配键（002 规范化精确匹配槽）：**策略可复现的结构键**。

    只含"给定策略源码 + 输入 + 配置即可重算"的部分（策略版本 / 模型与采样档 / 输出预算 /
    立项约束摘要 / 组合条目数区间）——**不含生成产物摘要**（论证要点正文依赖生成结果，
    回放不得触发生成，故不入匹配键；它另存节点观测与运营表供审计）。同结构同策略 → 同键
    （键序无关，002 `normalize_params` 口径）；未命中即 UNKNOWN。
    """
    return {
        "policy_version": policy_version,
        "model": config.model,
        "temperature": TEMPERATURE,
        "max_tokens": config.max_tokens,
        "constraint_digest": blake3.blake3(_canonical(inputs).encode()).hexdigest(),
        "slate_range": [config.slate_entries[0], config.slate_entries[1]],
    }


def generation_cache_key(model: str, prompt: str, temperature: float, max_tokens: int) -> str:
    """网关缓存键（与网关内部同构成：模型|提示词|温度|max_tokens）。

    同构成即"命中缓存 == 同一次调用的产出"：落盘后回放可核对（原则三）。
    """
    return blake3.blake3(f"{model}|{prompt}|{temperature}|{max_tokens}".encode()).hexdigest()


def _aggregate(values: list[str]) -> str:
    """逐调用摘要的规范化汇总（单产出表的 cache_key/response_hash 两列）。"""
    return blake3.blake3(_canonical(values).encode()).hexdigest()


def _estimate_cost(prompt: str, price: dict, max_tokens: int) -> float:
    """预估成本上界（网关价目）：输入 token 估计 + 满额输出 token。

    输出按 max_tokens 满额估算是刻意保守——保证 actual ≤ estimated（schema CHECK），
    失败调用照计预估成本（原则二）。
    """
    prompt_tokens = max(1, len(prompt) // 2)
    return (
        prompt_tokens / 1000 * price["prompt_per_1k"]
        + max_tokens / 1000 * price["completion_per_1k"]
    )


def _string_list(value, field_name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise DevLoopError(f"inputs.{field_name} 必须为非空字符串列表，实际为 {value!r}")
    for item in value:
        if not isinstance(item, str) or not item:
            raise DevLoopError(f"inputs.{field_name} 必须为非空字符串列表，实际为 {value!r}")
    return list(value)


def _validate_inputs(inputs: dict) -> dict:
    """输入预检先于一切副作用（缺题材边界/受众 → 执行前拒绝，0 网关 0 落库）。"""
    if not isinstance(inputs, dict):
        raise DevLoopError(f"inputs 必须为 dict，实际为 {inputs!r}")
    normalized = {
        "genre_bounds": _string_list(inputs.get("genre_bounds"), "genre_bounds"),
        "audience": inputs.get("audience"),
    }
    if not isinstance(normalized["audience"], str) or not normalized["audience"]:
        raise DevLoopError("inputs 缺少 audience（目标受众，非空字符串）")
    notes = inputs.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or not notes:
            raise DevLoopError(f"inputs.notes 必须为非空字符串，实际为 {notes!r}")
        normalized["notes"] = notes
    return normalized


def _round_params(*, policy_version: str, inputs: dict, config: DevConfig, marks) -> dict:
    """本轮参数（幂等键分量 + `inputs_json` 落盘内容）：立项约束 + 形态参数 + 组合计划摘要。

    只含策略可复现的结构键（生成产物摘要不入内）：同策略 + 同输入 + 同配置 ⇒ 同哈希，
    故 (round_id, params_hash) 是可信的幂等键；行内容自可复核（params_hash = BLAKE3(inputs_json)）。
    """
    return {
        "inputs": inputs,
        "match_key": slate_match_key(policy_version=policy_version, inputs=inputs, config=config),
        "production_marks": list(marks),
    }


def _slate_plan(plan, config: DevConfig) -> tuple[list[dict] | None, tuple[str, ...], str]:
    """计划执行前校验：缺条目/缺方向标识/形状非法一律拒绝（0 网关调用 0 成本）。

    校验口径 = 用占位论证要点试构造同 schema 工件（执行前校验即门禁的提前计算，008/009
    同款纪律）；真工件随后用网关正文构造，构造失败按失败处理（费用已发生，照计）。
    方向唯一性、条目数区间、标记区间与指向**不在此判定**——它们是门禁的判定对象
    （越界不由代码兜底）。
    """
    if not isinstance(plan, dict):
        return None, (), "策略计划必须为 mapping（{entries, production_marks}）（执行前拒绝）"
    raw_entries = plan.get("entries")
    if not isinstance(raw_entries, (list, tuple)) or not raw_entries:
        return None, (), "策略计划缺少 entries（立项组合条目列表非空）（执行前拒绝）"
    entries: list[dict] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            return None, (), f"策略计划的条目必须为 mapping，实际为 {raw!r}（执行前拒绝）"
        entry = {key: raw.get(key) for key in _ENTRY_KEYS}
        for key in _OPTIONAL_ENTRY_KEYS:
            value = raw.get(key)
            if value is None:
                continue
            if key == "rationale_seed" and (not isinstance(value, str) or not value):
                return None, (), "策略计划的 rationale_seed 必须为非空字符串（执行前拒绝）"
            entry[key] = value
        entries.append(entry)
    marks = plan.get("production_marks", ())
    try:
        TopicSlate.from_dict(
            {
                "entries": [{**entry, "rationale": PLACEHOLDER_RATIONALE} for entry in entries],
                "production_marks": marks,
                "signal_sources": list(simulated_signal_sources(config.signals)),
            }
        )
    except ValidationError as exc:
        return None, (), f"策略计划非法：{exc}（执行前拒绝）"
    return entries, tuple(marks), ""


def _entry_prompt(
    *,
    entry: dict,
    index: int,
    count: int,
    policy_version: str,
    inputs: dict,
    config: DevConfig,
) -> str:
    """条目提示词：立项约束 + 组合区间 + 方向结构 + 策略论证草稿（确定性，无时间戳/随机流）。

    确定性是缓存收敛的前提（同输入跨轮次命中缓存零成本复现，原则三）。
    """
    constraints = "；".join(entry["constraints"]) if entry["constraints"] else "无"
    characters = "；".join(entry["characters"]) if entry["characters"] else "无"
    rows = [
        f"【立项论证 · 第 {index}/{count} 个题材方向】人工策略版本 {policy_version}",
        f"题材边界：{'、'.join(inputs['genre_bounds'])}",
        f"目标受众：{inputs['audience']}",
        (
            f"本轮组合区间：条目数 {config.slate_entries[0]}~{config.slate_entries[1]}｜"
            f"进入生产标记数 {config.production_marks[0]}~{config.production_marks[1]}"
        ),
        f"方向标识：{entry['direction_id']}｜题材：{entry['genre']}",
        f"题材约束要点：{constraints}",
        f"角色设定要点：{characters}",
        f"策略论证草稿：{entry.get('rationale_seed') or '无'}",
    ]
    if inputs.get("notes"):
        rows.append(f"立项备注：{inputs['notes']}")
    rows.append(
        "任务：写出该题材方向的立项论证要点（2~4 句，说明为什么值得立项、对目标受众的吸引力"
        "与主要风险）；只输出论证正文，不要标题与编号。"
    )
    return "\n".join(rows)


def _assembly(evaluators) -> dict:
    """把注入的评估器序列按门禁语义分列（`rule.*` 前缀，与合成口径同源）。

    真实装配（`build_dev_evaluators`）本身就返回 {"gates","proxies","all"}；注入路径
    （回放重算 / 测试桩）给的是扁平列表，此处归一为同一形状——避免两套编排分支。
    """
    if isinstance(evaluators, dict):
        return evaluators

    def _is_gate(evaluator) -> bool:
        return evaluator.spec.evaluator_id.startswith(_GATE_PREFIX)

    gates = [evaluator for evaluator in evaluators if _is_gate(evaluator)]
    proxies = [evaluator for evaluator in evaluators if not _is_gate(evaluator)]
    if not gates or not proxies:
        raise DevLoopError(
            "评估器装配必须同时含门禁（rule.*）与代理分量："
            f"实际门禁 {len(gates)} 个、代理 {len(proxies)} 个"
        )
    return {"gates": gates, "proxies": proxies, "all": [*gates, *proxies]}


def _resolve_evaluators(evaluators, config: DevConfig) -> dict:
    """评估器装配：注入优先（测试桩 / 回放重算）；None → 真实四评估器装配（唯一装配点）。"""
    if evaluators is not None:
        if not evaluators:
            raise DevLoopError("evaluators 不能为空列表（评估器协议注入需要至少一个评估器）")
        return _assembly(evaluators)
    return build_dev_evaluators(config)


def _config_snapshot(config: DevConfig, assembly: dict) -> dict:
    """配置快照随树冻结（原则五/FR-005）：此后配置变更不影响历史节点与得分。

    冻结口径：权重 + 门禁阈值（条目数区间/标记区间/重复率上限）+ 合成口径 + 模拟数据源
    参数 + 升级判据阈值 + 生成档（模型与输出预算）+ 逐评估器 `id@version`
    （版本含实现哈希与数据源参数——数据源即行为口径，原则一）。
    """
    return {
        "evaluator_weights": config.evaluator_weights,
        "evaluator_versions": {
            evaluator.spec.evaluator_id: evaluator.spec.version for evaluator in assembly["all"]
        },
        # 回放投影白名单（002）：只暴露策略侧可消费的观测槽——网关核对键
        # （cache_key/response_hash）留运营表与节点观测，不进沙箱投影（原则四）
        "observation_fields": ["gen_params", "job_id"],
        "composite_policy": COMPOSITE_POLICY,
        "slate": {"min": config.slate_entries[0], "max": config.slate_entries[1]},
        "production_marks": {
            "min": config.production_marks[0],
            "max": config.production_marks[1],
        },
        "combination": {"max_direction_repeat_rate": config.max_direction_repeat_rate},
        "min_comparable_trees": config.min_comparable_trees,
        "signals": config.signals,
        "upgrade_criteria": config.upgrade_criteria,
        "model": config.model,
        # 生成输出预算随树冻结（原则五：决定实际产出与成本上界，历史节点不受此后变更影响）
        "max_tokens": config.max_tokens,
    }


def _root_node(round_id: str, tree_id: str, root_id: str, policy_version: str, inputs, plan: dict):
    """轮次锚点根节点（结构起点；输入摘要与组合计划结论存档供审计）。"""
    return TreeNode(
        node_id=root_id,
        tree_id=tree_id,
        parent_id=None,
        depth=0,
        agent_id=AGENT_ID,
        policy_version=policy_version,
        prompt=(
            f"立项组合轮次 {round_id}：题材边界 {'、'.join(inputs['genre_bounds'])} | "
            f"受众 {inputs['audience']}"
        ),
        observation_context={"round_id": round_id, "inputs": inputs, "plan": plan},
        artifact_hash=PLACEHOLDER_HASH,
        eval_breakdown={},
        score=0.0,
        cost=CostRecord(),
        status=NodeStatus.EVALUATED,
        created_at=time.time(),
    )


def _append_product_node(
    store: TreeStore,
    *,
    tree_id: str,
    root_id: str,
    job_id: str,
    policy_version: str,
    artifact_hash: str,
    status: NodeStatus,
    score: float | None,
    breakdown: dict,
    cost: CostRecord,
    observation: dict,
    prompt: str = "",
) -> None:
    """产出节点一次性完整 INSERT（落盘即冻结）；观测携带回放匹配槽与网关核对键。"""
    store.append_node(
        TreeNode(
            node_id=f"{job_id}-node",
            tree_id=tree_id,
            parent_id=root_id,
            depth=1,
            agent_id=AGENT_ID,
            policy_version=policy_version,
            prompt=prompt,
            observation_context={"job_id": job_id, **observation},
            artifact_hash=artifact_hash,
            eval_breakdown=breakdown,
            score=score,
            cost=cost,
            status=status,
            created_at=time.time(),
        )
    )


def _insert_job(
    engine: Engine,
    *,
    job_id: str,
    round_id: str,
    policy_version: str,
    inputs_json: str,
    params_hash: str,
    cache_key: str | None,
    response_hash: str | None,
    status: str,
    estimated: float,
    actual: float | None,
    artifact_hash: str | None,
    error: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(dev_jobs).values(
                job_id=job_id,
                round_id=round_id,
                policy_version=policy_version,
                inputs_json=inputs_json,
                params_hash=params_hash,
                cache_key=cache_key,
                response_hash=response_hash,
                status=status,
                estimated_cost_usd=estimated,
                actual_cost_usd=actual,
                artifact_hash=artifact_hash,
                error=error,
                created_at=_now_iso(),
            )
        )


def _mark_inserted(engine: Engine, job_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(dev_jobs.update().where(dev_jobs.c.job_id == job_id).values(status="inserted"))


def _round_spent(engine: Engine, round_id: str) -> float:
    with engine.connect() as conn:
        return float(
            conn.execute(
                select(func.sum(dev_jobs.c.actual_cost_usd)).where(dev_jobs.c.round_id == round_id)
            ).scalar()
            or 0.0
        )


def _reconcile(
    store: TreeStore, engine: Engine, tree_id: str, round_id: str, evaluator_cost: float
):
    """对账：树内成本合计 == 运营表实际扣费合计 + 评估器计费增量。

    生成成本两侧同源（运营表即网关折算入账），评估器计费只进树内节点（网关侧账目），
    故以评估器用量增量为桥（004/007/009 同口径）。本环节无 judge，增量恒 0 但**字段仍落盘**。
    """
    tree_total = sum(node.cost.generation_api_cost_usd for node in store.nodes_of(tree_id))
    ledger_total = _round_spent(engine, round_id)
    return {
        "tree_total_usd": tree_total,
        "ledger_total_usd": ledger_total,
        "evaluator_cost_usd": evaluator_cost,
        "consistent": abs(tree_total - (ledger_total + evaluator_cost)) < 1e-9,
    }


def run_dev_round(
    round_id: str,
    policy: DevPolicy,
    store: TreeStore,
    artifacts: ArtifactStore,
    engine: Engine,
    gateway: LLMGateway,
    config: DevConfig,
    inputs: dict,
    evaluators: list[Evaluator] | dict | None = None,
) -> DevRoundResult:
    """执行一轮立项组合产出（全流程幂等）。

    evaluators：None → 装配真实四评估器（两门禁 + 两确定性代理，无需网关）；list/dict →
    评估器协议注入（测试桩、US3 的回放重算）。
    失败隔离：网关失败 / 工件构造失败 / 评估器崩溃都只影响本轮产出（成本照计）。
    """
    normalized_inputs = _validate_inputs(inputs)  # 预检先于一切副作用
    assembly = _resolve_evaluators(evaluators, config)
    policy_version = getattr(policy, "policy_version", "unknown")
    raw_plan = policy.plan(normalized_inputs, config)
    entries, marks, reject_reason = _slate_plan(raw_plan, config)

    tree_id = round_tree_id(round_id)
    root_id = _round_root_id(round_id)
    job_id = _job_id(round_id)
    tree = DiscoveryTree(
        tree_id=tree_id,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        policy_version=policy_version,
        root_id=root_id,
        node_ids=[],
        config_snapshot=with_llm_profiles(
            _config_snapshot(config, assembly),
            gateway_profile_snapshot(gateway),  # 功能 016：档案与价目随快照冻结
        ),
    )
    plan_summary = {
        "entry_count": 0 if entries is None else len(entries),
        "production_marks": [] if entries is None else list(marks),
        "reject_reason": reject_reason,
    }
    # ---- 幂等：树锚点已存在 → 直接重建首轮结果返回（0 重复生成 0 重复扣费）----
    try:
        store.create_tree(tree)
        store.append_node(
            _root_node(round_id, tree_id, root_id, policy_version, normalized_inputs, plan_summary)
        )
    except DuplicateError:
        return _reconstruct(round_id, store, engine)

    params = _round_params(
        policy_version=policy_version, inputs=normalized_inputs, config=config, marks=marks
    )
    inputs_json = _canonical(params)
    params_hash = blake3.blake3(inputs_json.encode()).hexdigest()

    if entries is None:
        # 执行前拒绝：0 网关调用 0 成本，拒绝原因如实落节点与结果（不占运营表行）
        _append_product_node(
            store,
            tree_id=tree_id,
            root_id=root_id,
            job_id=job_id,
            policy_version=policy_version,
            artifact_hash=PLACEHOLDER_HASH,
            status=NodeStatus.EVALUATED,
            score=0.0,
            breakdown={},
            cost=CostRecord(),
            observation={"reject_reason": reject_reason},
        )
        return DevRoundResult(
            round_id=round_id,
            tree_id=tree_id,
            policy_version=policy_version,
            job={
                "job_id": job_id,
                "status": "rejected",
                "reason": reject_reason,
                "artifact_hash": None,
                "cache_key": None,
                "response_hash": None,
            },
            spent_usd=_round_spent(engine, round_id),
            cost_reconciliation=_reconcile(store, engine, tree_id, round_id, 0.0),
        )

    match_key = slate_match_key(
        policy_version=policy_version, inputs=normalized_inputs, config=config
    )
    # 估算与折算**同源**：都取网关本次调用生效的价目（016 接线形态，两价目不会脱钩）
    _, price = gateway.prices_for(role=Role.GENERATION, model=config.model)
    prompts = [
        _entry_prompt(
            entry=entry,
            index=index,
            count=len(entries),
            policy_version=policy_version,
            inputs=normalized_inputs,
            config=config,
        )
        for index, entry in enumerate(entries, start=1)
    ]
    cache_keys = [
        generation_cache_key(config.model, prompt, TEMPERATURE, config.max_tokens)
        for prompt in prompts
    ]
    estimated = sum(_estimate_cost(prompt, price, config.max_tokens) for prompt in prompts)
    shared = {
        "round_id": round_id,
        "job_id": job_id,
        "tree_id": tree_id,
        "root_id": root_id,
        "policy_version": policy_version,
        "params": params,
        "params_hash": params_hash,
        "inputs_json": inputs_json,
        "estimated": estimated,
        "store": store,
        "engine": engine,
    }

    # 1) 逐条目生成经网关（唯一昂贵动作，原则三）：失败 → 节点 FAILED + 预估成本照计
    texts: list[str] = []
    calls: list[dict] = []
    try:
        for entry, prompt, cache_key in zip(entries, prompts, cache_keys, strict=True):
            generated = gateway.chat(
                prompt,
                model=config.model,
                role=Role.GENERATION,
                temperature=TEMPERATURE,
                max_tokens=config.max_tokens,
            )
            texts.append(generated.text)
            calls.append(
                {
                    "direction_id": entry["direction_id"],
                    "cache_key": cache_key,
                    "response_hash": blake3.blake3(generated.text.encode()).hexdigest(),
                    "prompt_digest": blake3.blake3(prompt.encode()).hexdigest(),
                    "prompt_tokens": int(generated.usage["prompt_tokens"]),
                    "completion_tokens": int(generated.usage["completion_tokens"]),
                    "cost_usd": float(generated.cost_usd),
                }
            )
    except GatewayError as exc:
        return _fail_round(
            f"网关失败：{exc}",
            **shared,
            cache_key=_aggregate([call["cache_key"] for call in calls]),
            response_hash=None,
            actual=estimated,  # 失败照计预估成本（原则二）
            artifact_hash=None,
            cost=CostRecord(llm_calls=len(calls) + 1, generation_api_cost_usd=estimated),
            observation={"gen_params": match_key, "params_hash": params_hash, "calls": calls},
            prompt="\n\n".join(prompts),
        )

    actual = sum(call["cost_usd"] for call in calls)
    tokens = sum(call["prompt_tokens"] + call["completion_tokens"] for call in calls)
    observation = {
        # 回放匹配槽（002 固定读键）：仅策略可复现的结构键
        "gen_params": match_key,
        "params_hash": params_hash,
        "cache_key": _aggregate([call["cache_key"] for call in calls]),
        # 单产出表的两个核对列即逐调用摘要（逐调用明细另见 calls；回放只读历史节点）
        "response_hash": _aggregate([call["response_hash"] for call in calls]),
        "calls": calls,
    }

    # 2) 工件构造（计划结构 + 网关正文）：构造失败也照计已发生的费用
    try:
        artifact = TopicSlate.from_dict(
            {
                "entries": [
                    {**entry, "rationale": text, "in_production": entry["direction_id"] in marks}
                    for entry, text in zip(entries, texts, strict=True)
                ],
                "production_marks": list(marks),
                "signal_sources": list(simulated_signal_sources(config.signals)),
            }
        )
    except ValidationError as exc:
        return _fail_round(
            f"工件构造失败：{exc}",
            **shared,
            cache_key=observation["cache_key"],
            response_hash=observation["response_hash"],
            actual=actual,
            artifact_hash=None,
            cost=CostRecord(
                llm_calls=len(calls), llm_tokens=tokens, generation_api_cost_usd=actual
            ),
            observation=observation,
            prompt="\n\n".join(prompts),
        )

    # 3) 工件内容寻址入对象存储（哈希即 key，PUT-if-absent 幂等）
    artifact_hash = artifacts.put(artifact.canonical_json().encode())
    observation["artifact_hash"] = artifact_hash
    _insert_job(
        engine,
        job_id=job_id,
        round_id=round_id,
        policy_version=policy_version,
        inputs_json=inputs_json,
        params_hash=params_hash,
        cache_key=observation["cache_key"],
        response_hash=observation["response_hash"],
        status="generated",
        estimated=estimated,
        actual=actual,
        artifact_hash=artifact_hash,
        error=None,
    )

    # 4) 评估器协议注入打分（崩溃隔离：FAILED 成本照计，轮次收口如实）
    artifact_ref = ArtifactRef(artifact_hash=artifact_hash, metadata={"agent_id": AGENT_ID})
    context = {
        "artifact": artifact,
        "inputs": normalized_inputs,
        "config": config,
        "round_id": round_id,
        "job_id": job_id,
        "match_key": match_key,
    }
    try:
        breakdown, score, usage = evaluate_dev(
            assembly, artifact_ref, context, config.evaluator_weights
        )
    except Exception as exc:  # noqa: BLE001 - 崩溃隔离：FAILED 成本入账轮次继续
        _mark_inserted(engine, job_id)
        return _fail_round(
            f"评估器崩溃：{exc}",
            **shared,
            cache_key=observation["cache_key"],
            response_hash=observation["response_hash"],
            actual=actual,
            artifact_hash=artifact_hash,
            cost=CostRecord(
                llm_calls=len(calls), llm_tokens=tokens, generation_api_cost_usd=actual
            ),
            observation=observation,
            prompt="\n\n".join(prompts),
            recorded=True,
        )

    _append_product_node(
        store,
        tree_id=tree_id,
        root_id=root_id,
        job_id=job_id,
        policy_version=policy_version,
        artifact_hash=artifact_hash,
        status=NodeStatus.EVALUATED,
        score=score,
        breakdown=breakdown,
        cost=CostRecord(
            llm_calls=len(calls) + usage["llm_calls"],
            llm_tokens=tokens + usage["llm_tokens"],
            generation_api_cost_usd=actual + usage["cost_usd"],
        ),
        observation=observation,
        prompt="\n\n".join(prompts),
    )
    _mark_inserted(engine, job_id)
    return DevRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        job={
            "job_id": job_id,
            "status": "inserted",
            "reason": "",
            "artifact_hash": artifact_hash,
            "cache_key": observation["cache_key"],
            "response_hash": observation["response_hash"],
        },
        spent_usd=_round_spent(engine, round_id),
        cost_reconciliation=_reconcile(store, engine, tree_id, round_id, usage["cost_usd"]),
    )


def _fail_round(
    reason: str,
    *,
    round_id: str,
    job_id: str,
    tree_id: str,
    root_id: str,
    policy_version: str,
    params: dict,
    params_hash: str,
    inputs_json: str,
    estimated: float,
    actual: float,
    cache_key: str | None,
    response_hash: str | None,
    artifact_hash: str | None,
    cost: CostRecord,
    observation: dict,
    prompt: str,
    store: TreeStore,
    engine: Engine,
    recorded: bool = False,
) -> DevRoundResult:
    """轮次失败落盘：运营表 failed（成本照计）+ 节点 FAILED（原则二/三）。"""
    if not recorded:  # 运营表尚无本轮行（生成失败/构造失败）→ 补 failed 行
        _insert_job(
            engine,
            job_id=job_id,
            round_id=round_id,
            policy_version=policy_version,
            inputs_json=inputs_json,
            params_hash=params_hash,
            cache_key=cache_key,
            response_hash=response_hash,
            status="failed",
            estimated=estimated,
            actual=actual,
            artifact_hash=artifact_hash,
            error=reason,
        )
    _append_product_node(
        store,
        tree_id=tree_id,
        root_id=root_id,
        job_id=job_id,
        policy_version=policy_version,
        artifact_hash=artifact_hash if artifact_hash is not None else PLACEHOLDER_HASH,
        status=NodeStatus.FAILED,
        score=None,
        breakdown={},
        cost=cost,
        observation={**observation, "reject_reason": reason},
        prompt=prompt,
    )
    return DevRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=policy_version,
        job={
            "job_id": job_id,
            "status": "failed",
            "reason": reason,
            "artifact_hash": artifact_hash,
            "cache_key": cache_key,
            "response_hash": response_hash,
        },
        spent_usd=_round_spent(engine, round_id),
        cost_reconciliation=_reconcile(store, engine, tree_id, round_id, 0.0),
    )


def _reconstruct(round_id: str, store: TreeStore, engine: Engine) -> DevRoundResult:
    """幂等重建：二次触发撞树锚点后，从树与运营表还原首轮 DevRoundResult。

    job/成本合计逐项还原；**对账字段留空**——评估器计费增量不在重算范围内，
    宁可留空也不伪造"一致"（原则六：不编造证据）。
    """
    tree_id = round_tree_id(round_id)
    root = store.get_node(_round_root_id(round_id))
    node = next((item for item in store.nodes_of(tree_id) if item.parent_id is not None), None)
    with engine.connect() as conn:
        row = conn.execute(select(dev_jobs).where(dev_jobs.c.round_id == round_id)).fetchone()
    job_id = _job_id(round_id)
    context = {} if node is None else node.observation_context
    if row is not None:
        # 评估器崩溃：运营表已 inserted 但节点 FAILED —— 如实报 failed
        failed = node is not None and node.status is NodeStatus.FAILED
        status = "failed" if failed else row.status
        reason = row.error or context.get("reject_reason", "")
        artifact_hash = row.artifact_hash
        cache_key = row.cache_key
        response_hash = row.response_hash
    else:
        status = "rejected"
        reason = context.get("reject_reason", "")
        artifact_hash = cache_key = response_hash = None
    return DevRoundResult(
        round_id=round_id,
        tree_id=tree_id,
        policy_version=root.policy_version,
        job={
            "job_id": job_id,
            "status": status,
            "reason": reason,
            "artifact_hash": artifact_hash,
            "cache_key": cache_key,
            "response_hash": response_hash,
        },
        spent_usd=_round_spent(engine, round_id),
        cost_reconciliation={},
    )
