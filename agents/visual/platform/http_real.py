"""真实视频生成适配器（契约同构骨架，T325）。

凭证经环境变量注入（VISUAL_GEN_BASE_URL / VISUAL_GEN_API_KEY）；
无凭证环境构造即 UnavailableError——不假装生成（宪章原则六）。
契约语义（预估/实际花费、幂等键、状态机、错误映射）与
SimulatedVideoGen 完全一致，受 tests/contract 同一套件约束。
"""

import os

from agents.visual.platform.base import (
    GenJob,
    GenJobStatus,
    UnavailableError,
)


class HttpRealVideoGen:
    """真实生成 API 适配器骨架：凭证缺失即不可用（本期不发起真实生成）。"""

    def __init__(self, base_url: str, api_key: str) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实生成平台缺凭证：需要 VISUAL_GEN_BASE_URL / VISUAL_GEN_API_KEY"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @classmethod
    def from_env(cls) -> "HttpRealVideoGen":
        return cls(
            os.environ.get("VISUAL_GEN_BASE_URL", ""),
            os.environ.get("VISUAL_GEN_API_KEY", ""),
        )

    def submit(self, gen_params: dict, *, idempotency_key: str) -> GenJob:
        raise UnavailableError("真实生成平台接入是凭证配置的运维动作，本期未接入")

    def get_status(self, external_id: str) -> GenJobStatus:
        raise UnavailableError("真实生成平台未接入")

    def fetch_artifact(self, external_id: str) -> bytes:
        raise UnavailableError("真实生成平台未接入")

    def job_actual_cost(self, external_id: str) -> float:
        raise UnavailableError("真实生成平台未接入")

    def cancel(self, external_id: str) -> None:
        raise UnavailableError("真实生成平台未接入")
