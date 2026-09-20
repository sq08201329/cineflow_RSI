"""真实渲染服务适配器骨架（契约同构，C12，T718）。

凭证经环境变量注入（EDIT_RENDER_BASE_URL / EDIT_RENDER_API_KEY）；
无凭证构造即 UnavailableError——不假装渲染（宪章原则六）。
契约语义（预估/实际花费、错误映射）与模拟器完全一致，受
tests/contract/test_editing_platform_contract.py 同一套件约束。
"""

import os

from agents.editing.edl import EditDecisionList
from agents.editing.platform.base import RenderedFilm, UnavailableError
from agents.editing.shots import ShotLibrary


class HttpRealEditRender:
    """真实渲染服务适配器骨架：本期不接真实端点，契约用例无凭证 skip。"""

    ENV_PREFIX = "EDIT_RENDER"

    def __init__(self, base_url: str, api_key: str) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实渲染服务缺凭证：需要 EDIT_RENDER_BASE_URL / EDIT_RENDER_API_KEY"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @classmethod
    def from_env(cls) -> "HttpRealEditRender":
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, edl: EditDecisionList, shots: ShotLibrary) -> float:
        raise UnavailableError("真实渲染服务接入是凭证配置的运维动作，本期未接入")

    def render(self, edl: EditDecisionList, shots: ShotLibrary) -> RenderedFilm:
        raise UnavailableError("真实渲染服务未接入")
