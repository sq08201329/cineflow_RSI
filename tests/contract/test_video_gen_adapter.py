"""视频生成适配器契约套件（US1 / T316）。

同一套契约用例对两个实现各跑一遍：预估/实际花费、幂等键、状态机推进、
工件可解码（ffprobe 可读）、错误映射、取消幂等。
真实实现无凭证跳过其用例但保留套件；模拟实现必须全过。
"""

import os

import pytest

from agents.visual.frames import probe_clip
from agents.visual.platform.base import (
    GenJobStatus,
    InvalidParamsError,
    VideoGenError,
)
from agents.visual.platform.simulated import SimulatedVideoGen

GEN_PARAMS = {"style": "史诗", "shots": 2, "seed_tier": 1}


def _make_real_adapter(visual_config):
    from agents.visual.platform.http_real import HttpRealVideoGen

    if not os.environ.get("VISUAL_GEN_BASE_URL"):
        pytest.skip("真实生成平台无凭证（VISUAL_GEN_BASE_URL 未配置），跳过其契约用例")
    return HttpRealVideoGen.from_env()


@pytest.fixture(params=["simulated", "http_real"])
def adapter(request, visual_config):
    if request.param == "simulated":
        return SimulatedVideoGen(visual_config.simulated_gen)
    return _make_real_adapter(visual_config)


class Test花费:
    def test_提交含预估花费(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r1:c1")
        assert job.estimated_cost_usd > 0

    def test_实际扣费不超过预估(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r1:c2")
        for _ in range(5):
            if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                break
        adapter.fetch_artifact(job.external_id)
        final = adapter.get_status(job.external_id)
        assert final is GenJobStatus.COMPLETED
        # 账本：实际 ≤ 预估
        assert adapter.job_actual_cost(job.external_id) <= job.estimated_cost_usd


class Test幂等键:
    def test_同幂等键返回同一任务(self, adapter):
        first = adapter.submit(GEN_PARAMS, idempotency_key="r2:c1")
        second = adapter.submit(GEN_PARAMS, idempotency_key="r2:c1")
        assert second.external_id == first.external_id
        assert second.job_id == first.job_id

    def test_不同键不同任务(self, adapter):
        a = adapter.submit(GEN_PARAMS, idempotency_key="r2:c2")
        b = adapter.submit(GEN_PARAMS, idempotency_key="r2:c3")
        assert a.external_id != b.external_id


class Test状态机与工件:
    def test_状态机推进至_completed(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r3:c1")
        assert adapter.get_status(job.external_id) in (
            GenJobStatus.SUBMITTED,
            GenJobStatus.GENERATING,
        )
        for _ in range(5):
            status = adapter.get_status(job.external_id)
            if status is GenJobStatus.COMPLETED:
                break
        assert status is GenJobStatus.COMPLETED

    def test_未完成_fetch_报错(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r3:c2")
        with pytest.raises(VideoGenError):
            adapter.fetch_artifact(job.external_id)

    def test_工件可解码且逐字节确定(self, adapter, tmp_path):
        """决策 2：同参数产出逐字节相同的 mp4；ffprobe 可读。"""
        job = adapter.submit(GEN_PARAMS, idempotency_key="r3:c3")
        for _ in range(5):
            if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                break
        first = adapter.fetch_artifact(job.external_id)
        assert first == adapter.fetch_artifact(job.external_id)  # 逐字节一致

        path = tmp_path / "artifact.mp4"
        path.write_bytes(first)
        meta = probe_clip(path)
        assert meta["width"] > 0 and meta["fps"] > 0

    def test_同参数跨任务逐字节相同(self, adapter):
        """同 gen_params 不同幂等键的两个任务产出逐字节相同的工件。"""
        a = adapter.submit(GEN_PARAMS, idempotency_key="r3:c4")
        b = adapter.submit(GEN_PARAMS, idempotency_key="r3:c5")
        for job in (a, b):
            for _ in range(5):
                if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                    break
        assert adapter.fetch_artifact(a.external_id) == adapter.fetch_artifact(b.external_id)


class Test错误映射:
    def test_未知任务_get_status(self, adapter):
        with pytest.raises(InvalidParamsError):
            adapter.get_status("ghost-external-id")

    def test_未知任务_fetch(self, adapter):
        with pytest.raises(InvalidParamsError):
            adapter.fetch_artifact("ghost-external-id")

    def test_错误类型统一(self, adapter):
        with pytest.raises(VideoGenError):
            adapter.cancel("ghost")


class Test取消:
    def test_cancel_幂等(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r4:c1")
        adapter.cancel(job.external_id)
        adapter.cancel(job.external_id)  # 二次调用无副作用

    def test_cancel_已完成无操作(self, adapter):
        job = adapter.submit(GEN_PARAMS, idempotency_key="r4:c2")
        for _ in range(5):
            if adapter.get_status(job.external_id) is GenJobStatus.COMPLETED:
                break
        adapter.cancel(job.external_id)
        assert adapter.get_status(job.external_id) is GenJobStatus.COMPLETED
