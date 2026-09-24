"""峰谷时段 / 时区 / 归属口径单测（功能 019 / T1914 先于实现编写）：契约 C6。

- `budget.peak_windows` 三键必填、`attribution` **取值域单元素 `call_start`**（其它取值报错
  ⇒ 不静默换口径）；`windows` 空列表 = 全谷时**显式声明**；
- `is_peak(moment, cfg)` 按调用**开始时刻**在**其本地时区**判定，区间口径 = `[start, end)`
  （闭开；支持跨夜）；一次调用**只取一个格位**（跨切换时刻不拆分、不按 token 摊分）；
- 归属口径**三处可见**：① 报告/运行记录口径备注（网关 `LLMResult.pricing_note`）；
  ② 档位快照 `peak_windows_snapshot`；③ 校准记录 `note`；
- 日历单点：额度 `day` 窗口与运行记录 `{date}` 同取 `peak_windows.timezone`（不引入第二个时区键）。
"""

import datetime as dt

import pytest

from core.billing.budget import BudgetConfig, BudgetConfigError, PeakWindows
from core.billing.calibration import record_calibration
from core.billing.runlog import run_path
from core.llm_gateway.gateway import LLMGateway
from core.llm_gateway.profiles import ProfileConfigError, load_or_migrate
from core.llm_gateway.routing import Role

_SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_CROSS_NIGHT = "08:30–00:30"  # 跨夜峰时区间（C6 场景）
_CELLS = {
    "peak_miss": {"prompt_per_1k": 0.008, "completion_per_1k": 0.016},
    "peak_hit": {"prompt_per_1k": 0.002, "completion_per_1k": 0.004},
    "off_peak_miss": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
    "off_peak_hit": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.001},
}


def _moment(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 23, hour, minute, tzinfo=_SHANGHAI)


def _payload(**peak_overrides) -> dict:
    """两形态真实 `budget:` 段的最小派生（只改本测试用到的字段）。"""
    import copy

    import yaml

    from core.billing.budget import REPO_ROOT

    payload = copy.deepcopy(
        yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))["budget"]
    )
    payload["peak_windows"].update(peak_overrides)
    return payload


class _Calendar:
    """渠道日历桩：记录判定时收到的时刻（证"按调用开始时刻判定、只判一次"）。"""

    def __init__(self, *, peak: bool) -> None:
        self.peak = peak
        self.attribution = "call_start"
        self.timezone = "Asia/Shanghai"
        self.windows = ()
        self.moments: list[dt.datetime] = []

    def is_peak(self, moment) -> bool:
        self.moments.append(moment)
        return self.peak


class _Backend:
    def __init__(self, *, cached_prompt_tokens: int | None = None, delay=None) -> None:
        self.call_count = 0
        self.started_at: list[dt.datetime] = []
        self.cached_prompt_tokens = cached_prompt_tokens

    def complete(self, prompt, *, model, temperature, max_tokens):
        from core.llm_gateway.gateway import BackendResult

        self.call_count += 1
        self.started_at.append(dt.datetime.now(dt.UTC))
        return BackendResult(
            text="伪文本",
            prompt_tokens=1000,
            completion_tokens=500,
            cached_prompt_tokens=self.cached_prompt_tokens,
        )


class Test峰谷判定:
    def test_跨夜窗口_23点判峰_1点判谷(self):
        calendar = PeakWindows(timezone="Asia/Shanghai", attribution="call_start", windows=())
        from core.billing.budget import PeakWindow

        peak_windows = PeakWindows(
            timezone="Asia/Shanghai",
            attribution="call_start",
            windows=(PeakWindow(start="08:30", end="00:30"),),
        )
        assert peak_windows.is_peak(_moment(23, 0))  # 跨夜段内（08:30 → 次日 00:30）
        assert not peak_windows.is_peak(_moment(1, 0))  # 已过 00:30 ⇒ 谷
        assert calendar.windows == () and not calendar.is_peak(_moment(23, 0))  # 全谷时

    def test_边界时刻按闭开区间裁定(self):
        from core.billing.budget import PeakWindow

        peak_windows = PeakWindows(
            timezone="Asia/Shanghai",
            attribution="call_start",
            windows=(PeakWindow(start="08:30", end="00:30"),),
        )
        assert peak_windows.is_peak(_moment(8, 30))  # 起点含
        assert not peak_windows.is_peak(_moment(8, 29))
        assert not peak_windows.is_peak(_moment(0, 30))  # 终点不含
        assert peak_windows.is_peak(_moment(0, 29))
        # 同刻不同时区口径不同（判定在**渠道本地时区**）：UTC 00:30 = 上海 08:30
        utc = dt.UTC
        assert peak_windows.is_peak(dt.datetime(2026, 9, 23, 0, 30, tzinfo=utc))

    def test_跨峰谷切换时刻的调用按开始时刻取档且不拆分(self):
        load = load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "p1": {
                            "base_url": "https://api.example.invalid",
                            "api_key_env": "EXAMPLE_API_KEY",
                            "prices": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
                            "price_note": "夹具",
                            "price_matrix": _CELLS,
                        }
                    },
                    "roles": {},
                    "default_profile": "p1",
                }
            }
        )
        calendar = _Calendar(peak=True)  # 调用开始时判峰；调用期间即使过了切换点也不重判
        backend = _Backend()
        gateway = LLMGateway(
            backend, price_book={}, sleep=lambda _: None, profiles=load, peak_windows=calendar
        )
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert result.cell_key == "peak_miss"  # 只取一格（不拆分成峰谷两段）
        assert len(calendar.moments) == 1  # 只判一次
        assert calendar.moments[0] <= backend.started_at[0]  # 判定用**调用开始时刻**
        assert result.cost_usd == pytest.approx(1000 / 1000 * 0.008 + 500 / 1000 * 0.016)


class Test装配校验:
    def test_缺时区即装配报错(self):
        payload = _payload()
        payload["peak_windows"].pop("timezone")
        with pytest.raises(BudgetConfigError, match="timezone"):
            BudgetConfig.from_dict({"budget": payload})

    def test_缺归属即装配报错(self):
        payload = _payload()
        payload["peak_windows"].pop("attribution")
        with pytest.raises(BudgetConfigError, match="attribution"):
            BudgetConfig.from_dict({"budget": payload})

    def test_归属取值非法即装配报错(self):
        payload = _payload(attribution="call_end")
        with pytest.raises(BudgetConfigError, match="取值域单元素"):
            BudgetConfig.from_dict({"budget": payload})

    def test_时区非法即装配报错(self):
        payload = _payload(timezone="Mars/Olympus")
        with pytest.raises(BudgetConfigError, match="时区"):
            BudgetConfig.from_dict({"budget": payload})

    def test_矩阵档案缺渠道日历即装配期拒绝(self):
        load = load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "p1": {
                            "base_url": "https://api.example.invalid",
                            "api_key_env": "EXAMPLE_API_KEY",
                            "prices": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
                            "price_note": "夹具",
                            "price_matrix": _CELLS,
                        }
                    },
                    "roles": {},
                    "default_profile": "p1",
                }
            }
        )
        gateway = LLMGateway(_Backend(), price_book={}, sleep=lambda _: None, profiles=load)
        with pytest.raises(ProfileConfigError, match="price_matrix"):
            gateway.chat("提示词", role=Role.GENERATION)


class Test归属口径三处可见:
    def test_之一_报告口径备注含归属与区间口径(self):
        calendar = _Calendar(peak=True)
        load = load_or_migrate(
            {
                "llm": {
                    "profiles": {
                        "p1": {
                            "base_url": "https://api.example.invalid",
                            "api_key_env": "EXAMPLE_API_KEY",
                            "prices": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
                            "price_note": "夹具",
                            "price_matrix": _CELLS,
                        }
                    },
                    "roles": {},
                    "default_profile": "p1",
                }
            }
        )
        from core.billing.budget import PeakWindow

        calendar.windows = (PeakWindow(start="08:30", end="00:30"),)
        gateway = LLMGateway(
            _Backend(), price_book={}, sleep=lambda _: None, profiles=load, peak_windows=calendar
        )
        result = gateway.chat("提示词", role=Role.GENERATION)
        assert "attribution=call_start" in result.pricing_note
        assert "[08:30, 00:30)" in result.pricing_note
        assert "Asia/Shanghai" in result.pricing_note

    def test_之二_档位快照含峰谷快照(self, budget_config_factory):
        cfg = budget_config_factory()
        snapshot = cfg.to_snapshot(next(iter(cfg.channels)))
        peak = snapshot["peak_windows_snapshot"]
        assert peak["attribution"] == "call_start"
        assert peak["timezone"] == cfg.peak_windows.timezone
        assert peak["windows"]

    def test_之三_校准记录备注含归属口径(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        channel = next(iter(cfg.channels))
        record = record_calibration(
            "cal-peak",
            cfg=cfg,
            channel_id=channel,
            tier_id="screenplay",
            prices_snapshot={},
            sample_count=3,
            measured_cost_usd=1.0,
            expected_cost_usd=1.0,
            root=billing_root,
            at=dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC),
        )
        assert "call_start" in record.note
        assert "[08:30, 00:30)" in record.note
        assert cfg.peak_windows.timezone in record.note


class Test日历单点:
    def test_额度_day_窗口与运行记录日期同取渠道时区(self, budget_config_factory, billing_root):
        cfg = budget_config_factory(window_kind="day")
        channel = next(iter(cfg.channels))
        moment = dt.datetime(2026, 9, 22, 17, 30, tzinfo=dt.UTC)  # 上海次日
        assert cfg.local_date(moment) == "2026-09-23"
        assert run_path(billing_root, channel, cfg.local_date(moment)).name == "2026-09-23.json"

    def test_不引入第二个时区配置键(self):
        import yaml

        from core.billing.budget import REPO_ROOT

        for form in ("movie", "shortdrama"):
            payload = yaml.safe_load(
                (REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8")
            )
            assert "timezone" not in yaml.safe_dump(payload["budget"]["runs"])
            assert "timezone" not in yaml.safe_dump(payload["budget"]["calibration"])
            assert "timezone" not in yaml.safe_dump(payload["budget"]["reconcile"])
            # 唯一时区声明点在 peak_windows
            assert payload["budget"]["peak_windows"]["timezone"]
