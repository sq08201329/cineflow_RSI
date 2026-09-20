"""真实预演渲染服务适配器骨架（契约同构，C12，T818）。

凭证经环境变量注入（STORYBOARD_RENDER_BASE_URL / STORYBOARD_RENDER_API_KEY）；
无凭证构造即 UnavailableError——不假装渲染（宪章原则六）。
契约语义（预估/实际花费、元数据键、错误映射）与模拟器完全一致，受
tests/contract/test_storyboard_platform_contract.py 同一套件约束。

接入口径（T817/T819 已定）：真实服务接入后必须返回同一套元数据键，且
`frame_hashes` 必须来自 board_render.storyboard_cards（渲染件与评估输入同源，
禁止两套帧——若真实服务产帧口径不同，属实现变更加升版本，路径不变）。
"""

import os

from agents.storyboard.config import StoryboardConfig
from agents.storyboard.platform.base import RenderedAnimatic, UnavailableError
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList


class HttpRealStoryboardRender:
    """真实预演渲染服务适配器骨架：本期不接真实端点，契约用例无凭证 skip。"""

    ENV_PREFIX = "STORYBOARD_RENDER"

    def __init__(self, base_url: str, api_key: str) -> None:
        if not base_url or not api_key:
            raise UnavailableError(
                "真实预演渲染服务缺凭证：需要 STORYBOARD_RENDER_BASE_URL / "
                "STORYBOARD_RENDER_API_KEY"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    @classmethod
    def from_env(cls) -> "HttpRealStoryboardRender":
        return cls(
            os.environ.get(f"{cls.ENV_PREFIX}_BASE_URL", ""),
            os.environ.get(f"{cls.ENV_PREFIX}_API_KEY", ""),
        )

    def estimate(self, shotlist: ShotList, cfg: StoryboardConfig) -> float:
        raise UnavailableError("真实预演渲染服务接入是凭证配置的运维动作，本期未接入")

    def render(
        self, shotlist: ShotList, script: ScriptSegment, cfg: StoryboardConfig
    ) -> RenderedAnimatic:
        raise UnavailableError("真实预演渲染服务未接入")
