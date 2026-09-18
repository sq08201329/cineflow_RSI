"""宣发配置与真实适配器骨架的补充单测（覆盖率与缺配置语义）。"""

from pathlib import Path

import pytest
import yaml

from agents.promo.config import PromoConfig, PromoConfigError
from agents.promo.platform.base import UnavailableError
from agents.promo.platform.http_real import HttpRealPlatform

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPromoConfig:
    def test_from_yaml_真实配置(self):
        config = PromoConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        assert config.budget_cap_usd == pytest.approx(10.0)  # 500 × 0.02
        assert config.default_model == "mock-copy-v1"

    def test_缺_promo_段报错(self):
        with pytest.raises(PromoConfigError, match="promo"):
            PromoConfig.from_dict({"form": "x"})

    def test_缺_promo_权重段报错(self, promo_config):
        with pytest.raises(PromoConfigError, match="evaluator_weights"):
            PromoConfig.from_dict(
                {
                    "promo": yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text())[
                        "promo"
                    ]
                }
            )

    def test_缺关键项报错(self):
        config = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text())
        del config["promo"]["sensitive_words"]
        with pytest.raises(PromoConfigError, match="sensitive_words"):
            PromoConfig.from_dict(config)


class TestHttpRealPlatform骨架:
    def test_缺凭证构造即不可用(self, monkeypatch):
        monkeypatch.delenv("PROMO_PLATFORM_BASE_URL", raising=False)
        monkeypatch.delenv("PROMO_PLATFORM_API_KEY", raising=False)
        with pytest.raises(UnavailableError, match="凭证"):
            HttpRealPlatform.from_env()

    def test_有凭证但本期未接入_方法统一不可用(self):
        adapter = HttpRealPlatform("https://example.invalid", "test-key")
        with pytest.raises(UnavailableError):
            adapter.get_status("any")
        with pytest.raises(UnavailableError):
            adapter.pause("any")
