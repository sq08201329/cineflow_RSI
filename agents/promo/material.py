"""物料生成编排（T216）：网关生成文案 → 内容组装 → 工件内容寻址落库。

网关是唯一 LLM 入口（宪章原则三）；物料内容规范化序列化后 put 进
ArtifactStore，artifact_hash 即内容寻址引用（FR-005）。
"""

import json
import time

from agents.promo.platform.base import PromoMaterial
from core.llm_gateway.gateway import LLMGateway
from core.llm_gateway.routing import Role  # 功能 016：调用角色（路由只在网关）
from core.tree.artifacts import ArtifactStore
from core.tree.attribution import add_call  # 功能 022：角色 × 档案分解归集


def generate_material(
    brief: dict,
    material_id: str,
    gateway: LLMGateway,
    artifacts: ArtifactStore,
    *,
    model: str,
) -> tuple[PromoMaterial, dict]:
    """按简报生成一个物料，返回 (物料, 成本明细 dict)。

    成本明细：llm_calls / llm_tokens / gateway_usd / wall_clock_seconds。
    """
    start = time.perf_counter()
    # 功能 016：宣发文案角色（接档案后模型/价目由角色路由决定）
    result = gateway.chat(
        brief["prompt"],
        model=model,
        role=Role.COPYWRITING,
        temperature=0.0,
        stage="promo",  # 019（C9）：环节 id = budget.tiers 的键（宣发文案档）
    )
    gen_params = brief.get("gen_params", {})
    content = {
        "copy": result.text,
        "poster_size": gen_params.get("poster_size"),
        "duration_seconds": gen_params.get("duration_seconds"),
    }
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    artifact_hash = artifacts.put(canonical)  # 内容寻址，天然去重
    material = PromoMaterial(
        material_id=material_id,
        kind=brief.get("kind", "copy"),
        content=content,
        artifact_hash=artifact_hash,
        platform=brief.get("platform", "simulated"),
        tags=list(brief.get("tags", [])),
    )
    # 022：角色 × 档案分解——归属直接取 LLMResult 自带 role/profile_id；
    # cached（零计费）跳过、未接档案旧路径（归属空）⇒ 空分解（既有行为不变）
    breakdown: dict = {}
    if not result.cached and result.role and result.profile_id:
        breakdown = add_call(
            {},
            role=result.role,
            profile_id=result.profile_id,
            prompt_tokens=int(result.usage["prompt_tokens"]),
            completion_tokens=int(result.usage["completion_tokens"]),
            cost_usd=float(result.cost_usd),
        )
    cost = {
        "llm_calls": 1,
        "llm_tokens": result.usage["prompt_tokens"] + result.usage["completion_tokens"],
        "gateway_usd": result.cost_usd,
        "wall_clock_seconds": time.perf_counter() - start,
        "llm_breakdown": breakdown,
    }
    return material, cost
