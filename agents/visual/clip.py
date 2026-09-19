"""片段生成编排（T323）：适配器生成 → 工件内容寻址落库 → ffprobe 探测。

返回 probe_meta（评估器输入）与成本明细；工件 bytes 经 ArtifactStore
内容寻址（同参数逐字节相同 → 天然去重，SC-003/FR-005）。
"""

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents.visual.frames import probe_clip
from agents.visual.platform.base import GenJobStatus
from core.tree.artifacts import ArtifactStore

STATUS_POLL_MAX = 5  # completed 轮询上限


@dataclass(frozen=True)
class ClipProduction:
    """一次片段生成的完整产出。"""

    clip_id: str
    gen_params: dict
    artifact_hash: str
    probe_meta: dict
    artifact_bytes: bytes = field(repr=False)
    actual_cost_usd: float = 0.0
    wall_clock_seconds: float = 0.0


def produce_clip(
    gen_params: dict,
    clip_id: str,
    adapter,
    artifacts: ArtifactStore,
) -> ClipProduction:
    """提交 → 轮询至 completed → 取工件 → 内容寻址落库 → ffprobe 探测。"""
    start = time.perf_counter()
    job = adapter.submit(gen_params, idempotency_key=clip_id)
    for _ in range(STATUS_POLL_MAX):
        if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
            break
    artifact_bytes = adapter.fetch_artifact(job.external_id)
    artifact_hash = artifacts.put(artifact_bytes)  # 内容寻址，天然去重

    # ffprobe 探测需要文件路径：写临时文件探测（工件本体已在内容寻址库）
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(artifact_bytes)
        tmp_path = Path(tmp.name)
    try:
        meta = probe_clip(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return ClipProduction(
        clip_id=clip_id,
        gen_params=gen_params,
        artifact_hash=artifact_hash,
        probe_meta=meta,
        artifact_bytes=artifact_bytes,
        actual_cost_usd=adapter.job_actual_cost(job.external_id),
        wall_clock_seconds=time.perf_counter() - start,
    )
