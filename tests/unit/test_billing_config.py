"""分档额度与峰谷声明的配置校验矩阵（功能 019 / US1 / T1923）：契约 C9。

- **缺项即装配报错**（不取码内默认）：缺 `budget` 段 / 缺某环节档 / 缺 `peak_windows.timezone`；
- 取值域校验：`on_exhausted` 单元素 `refuse`（不排队、不降级为模拟）、`limit_usd` > 0 且非 bool、
  `window.kind ∈ {run, day, period}`；
- `note` 缺失 ⇒ notes 告警但**可启动**（口径须可追溯，运营补全前不阻塞装配）；
- **两形态取值差异由配置承载**（额度更小、窗口更短），且**零形态分支**（`core/` 无 `form ==`）。
"""

import copy

import pytest
import yaml

from core.billing.budget import (
    ON_EXHAUSTED_REFUSE,
    REPO_ROOT,
    WINDOW_KINDS,
    BudgetConfig,
    BudgetConfigError,
)
from core.llm_gateway.profiles import ProfileConfigError


def _payload(form: str = "movie", **overrides) -> dict:
    payload = yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))
    return payload


def _budget(form: str = "movie", **overrides) -> dict:
    section = copy.deepcopy(_payload(form)["budget"])
    section.update(overrides)
    return section


class Test缺项即报错:
    def test_缺预算段即报错(self):
        with pytest.raises(BudgetConfigError, match="budget"):
            BudgetConfig.from_dict({"form": "movie"})

    def test_缺某环节档即报错(self):
        section = _budget()
        section["tiers"].pop("screenplay")
        cfg = BudgetConfig.from_dict({"budget": section})
        assert "screenplay" not in cfg.tiers
        with pytest.raises(BudgetConfigError, match="未在 budget.tiers 声明"):
            cfg.tier("screenplay")  # 取缺档 ⇒ 拒绝，不取码内默认

    def test_缺峰谷时区即报错(self):
        section = _budget()
        section["peak_windows"].pop("timezone")
        with pytest.raises(BudgetConfigError, match="timezone"):
            BudgetConfig.from_dict({"budget": section})

    def test_缺峰谷归属即报错(self):
        section = _budget()
        section["peak_windows"].pop("attribution")
        with pytest.raises(BudgetConfigError, match="attribution"):
            BudgetConfig.from_dict({"budget": section})

    @pytest.mark.parametrize(
        "key", ["calibration", "reconcile", "ledger", "runs", "channels", "peak_windows"]
    )
    def test_缺任一必需子段即报错(self, key):
        section = _budget()
        section.pop(key)
        with pytest.raises(BudgetConfigError):
            BudgetConfig.from_dict({"budget": section})

    def test_缺额度键即报错(self):
        section = _budget()
        section["tiers"]["promo"].pop("limit_usd")
        with pytest.raises(BudgetConfigError, match="limit_usd"):
            BudgetConfig.from_dict({"budget": section})


class Test取值域:
    def test_额度耗尽语义单元素_refuse(self):
        section = _budget()
        section["tiers"]["promo"]["on_exhausted"] = "queue"
        with pytest.raises(BudgetConfigError, match="取值域单元素"):
            BudgetConfig.from_dict({"budget": section})
        assert ON_EXHAUSTED_REFUSE == "refuse"

    @pytest.mark.parametrize("value", [0, -1.0])
    def test_额度非正即报错(self, value):
        section = _budget()
        section["tiers"]["promo"]["limit_usd"] = value
        with pytest.raises(BudgetConfigError, match="limit_usd"):
            BudgetConfig.from_dict({"budget": section})

    def test_额度为_bool_即报错(self):
        """bool 是 int 的子类：`True` 不得被当成 1.0（金额口径必须显式）。"""
        section = _budget()
        section["tiers"]["promo"]["limit_usd"] = True
        with pytest.raises(BudgetConfigError, match="limit_usd"):
            BudgetConfig.from_dict({"budget": section})

    def test_窗口类型取值域(self):
        assert set(WINDOW_KINDS) == {"run", "day", "period"}
        section = _budget()
        section["tiers"]["promo"]["window"] = {"kind": "week"}
        with pytest.raises(BudgetConfigError, match="window.kind"):
            BudgetConfig.from_dict({"budget": section})

    def test_归属取值域单元素(self):
        section = _budget()
        section["peak_windows"]["attribution"] = "call_end"
        with pytest.raises(BudgetConfigError, match="取值域单元素"):
            BudgetConfig.from_dict({"budget": section})

    def test_峰时区间起止相同即拒绝(self):
        section = _budget()
        section["peak_windows"]["windows"] = [{"start": "09:00", "end": "09:00"}]
        with pytest.raises(BudgetConfigError, match="起止相同"):
            BudgetConfig.from_dict({"budget": section})

    def test_全谷时以空列表显式声明(self):
        section = _budget()
        section["peak_windows"]["windows"] = []
        cfg = BudgetConfig.from_dict({"budget": section})
        assert cfg.peak_windows.windows == ()  # 显式声明"全谷时"，不是缺项

    def test_渠道缺登记即报错(self):
        section = _budget()
        cfg = BudgetConfig.from_dict({"budget": section})
        with pytest.raises(BudgetConfigError, match="未在 budget.channels 登记"):
            cfg.channel("ghost-channel")

    def test_账单格式未声明即报错(self):
        section = _budget()
        section["channels"]["llm"]["bill"].pop("format")
        with pytest.raises(BudgetConfigError, match="format"):
            BudgetConfig.from_dict({"budget": section})


class Test口径备注:
    def test_缺_note_记告警但可启动(self):
        section = _budget()
        section["tiers"]["promo"].pop("note")
        cfg = BudgetConfig.from_dict({"budget": section})
        assert any("note" in note for note in cfg.notes)  # 口径不可追溯 ⇒ notes 告警
        assert cfg.tier("promo").limit_usd > 0  # 但不阻塞装配（运营补全前可跑）

    def test_未标定标注在真实配置的每个档位上(self):
        """运营给定前按最小规模档运行：每个档位的口径备注必须标注"未标定"。"""
        for form in ("movie", "shortdrama"):
            cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / f"{form}.yaml")
            assert cfg.tiers and all("未标定" in tier.note for tier in cfg.tiers.values())


class Test两形态差异由配置承载:
    def test_额度与窗口取值可指认(self):
        movie = BudgetConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml")
        short = BudgetConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        assert set(movie.tiers) == set(short.tiers)  # 键集一致（环节清单与形态无关）
        smaller = [
            tier_id
            for tier_id, tier in movie.tiers.items()
            if short.tiers[tier_id].limit_usd < tier.limit_usd
        ]
        assert len(smaller) == len(movie.tiers)  # 短剧额度**逐档更小**
        shorter = [
            tier_id
            for tier_id, tier in movie.tiers.items()
            if short.tiers[tier_id].window_kind != tier.window_kind
        ]
        assert shorter  # 短剧时间窗更短（取值差异可逐档指认）
        assert short.calibration["record_ttl_days"] < movie.calibration["record_ttl_days"]
        assert short.reconcile["alert_threshold_usd"] < movie.reconcile["alert_threshold_usd"]

    def test_零形态分支_stage_键集不随形态变(self):
        """形态差异只在**取值**：`core/billing` 不含形态字面量/分支。

        零形态分支由 `test_billing_core_purity` 常驻机检。
        """
        for form in ("movie", "shortdrama"):
            payload = yaml.safe_load(
                (REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8")
            )
            assert payload["form"] == form
            assert set(payload["budget"]["tiers"]) == {
                "screenplay",
                "dev",
                "promo",
                "screenplay_judge",
                "storyboard_judge",
                "visual_judge",
                "editing_judge",
                "dreaming_candidates",
            }


class Test登记点四_预检读声明值:
    def test_预检报告按环节登记声明额度(self, pilot_form_config_path):
        """登记点④：`precheck` 的 LLM 腿额度取自 `budget.tiers` 的**声明值**（不留 0.0 占位）。"""
        from agents.pilot.pilot import PilotInputs, precheck

        config_path = pilot_form_config_path("movie")
        report = precheck(
            form="movie",
            config_path=config_path,
            inputs=PilotInputs(
                topic="题材",
                target_duration_min=90,
                characters=("林一",),
                genre_bounds=("悬疑",),
                audience="都市女性",
            ),
            data_dir=config_path.parent / "pilot",
        )
        assert "budget" in report["loaders"]
        budgets = report["budgets"]
        for tier_id in ("screenplay", "dev", "promo", "dreaming_candidates"):
            assert budgets[tier_id] > 0  # 声明值（非 0.0 占位）

    def test_缺预算段即预检拒绝(self, pilot_form_config_path, tmp_path):
        from agents.pilot.pilot import PilotInputs, PrecheckError, precheck

        source = pilot_form_config_path("movie").read_text(encoding="utf-8")
        broken = tmp_path / "broken-budget.yaml"
        payload = yaml.safe_load(source)
        payload.pop("budget")
        broken.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        with pytest.raises(PrecheckError, match="budget"):
            precheck(
                form="movie",
                config_path=broken,
                inputs=PilotInputs(
                    topic="题材",
                    target_duration_min=90,
                    characters=("林一",),
                    genre_bounds=("悬疑",),
                    audience="都市女性",
                ),
                data_dir=tmp_path / "pilot",
            )


def test_档案价目与档位额度分属两层():
    """价目（按 token）与额度（按环节）是两层：缺价目报 `ProfileConfigError`。

    缺档则报 `BudgetConfigError`——两者不共基类，报错类型可分辨。
    """
    assert issubclass(ProfileConfigError, Exception)
    assert not issubclass(BudgetConfigError, ProfileConfigError)
