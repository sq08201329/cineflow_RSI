"""两维价目（峰谷 × 厂商缓存命中）单测（功能 019 / T1913 先于实现编写）：契约 C5 / C8。

四格 = `peak_miss` / `peak_hit` / `off_peak_miss` / `off_peak_hit`（格位键 `<峰谷>_<缓存>`）：

- **声明即四格齐备**（缺任一格、任一格缺键 ⇒ `ProfileConfigError`）；未声明 ⇒ 四格皆取基础
  `prices`（既有档案取价与改造前**逐字节一致**）；单格为 0 合法、四格全 0 仍须 `zero_marginal`；
- **折算口径唯一**：网关折算、预算估算与 `ModelProfile.cost_usd` 同取 `price_cell`
  （第二条硬编码线性式已委派，全仓不留第二份算术）；
- **本地网关缓存命中不进任何格位**（零成本、零后端调用）——与厂商 prompt 缓存维度正交。

四件套（C8，全部常驻）：① 改配置后历史复算逐字节不变；② 新旧形状读取等价；③ 缺格装配报错；
④ 快照含 matrix 且无密钥、指纹随 matrix 变化。
"""

import datetime as dt
import json
from dataclasses import replace

import pytest

from core.llm_gateway.gateway import BackendResult, LLMGateway
from core.llm_gateway.profiles import (
    DECLARED_DIMENSIONS,
    MATRIX_CELLS,
    ProfileConfigError,
    cost_from_prices,
    load_or_migrate,
    price_cell,
    snapshot_entry_cells,
)
from core.llm_gateway.routing import Role

_MOMENT = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)  # 夹具的"调用开始时刻"
# 四格取值刻意两两不同：取错格位即算错钱，断言可证伪
_CELLS = {
    "peak_miss": {"prompt_per_1k": 0.008, "completion_per_1k": 0.016},
    "peak_hit": {"prompt_per_1k": 0.002, "completion_per_1k": 0.004},
    "off_peak_miss": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
    "off_peak_hit": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.001},
}
_BASE_PRICES = {"prompt_per_1k": 0.003, "completion_per_1k": 0.006}
# 016 既定快照条目形状（改造前）：旧快照原语义要求这些键逐字保留、且不新增键
_LEGACY_ENTRY_KEYS = {
    "profile_id",
    "model",
    "endpoint",
    "endpoint_source",
    "api_key_env",
    "prices",
    "price_note",
    "zero_marginal",
    "legacy_env",
    "timeout_seconds",
    "request_options",
}


def _config(*, cells=None, prices=None, zero_marginal=None):
    profile = {
        "base_url": "https://api.example.invalid",
        "api_key_env": "EXAMPLE_API_KEY",
        "prices": dict(prices or _BASE_PRICES),
        "price_note": "夹具价目（四格）",
    }
    if cells is not None:
        profile["price_matrix"] = cells
    if zero_marginal is not None:
        profile["zero_marginal"] = zero_marginal
    return {"llm": {"profiles": {"p1": profile}, "roles": {}, "default_profile": "p1"}}


class _Backend:
    """假后端：固定 token 数 + 可选命中 token 读数（`None` = 厂商未报告该字段）。"""

    def __init__(
        self,
        *,
        prompt_tokens: int = 1000,
        completion_tokens: int = 500,
        cached_prompt_tokens: int | None = None,
    ) -> None:
        self.call_count = 0
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cached_prompt_tokens = cached_prompt_tokens

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        return BackendResult(
            text="伪文本",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens,
        )


class _Calendar:
    """渠道日历桩：网关只认**注入的判定值**（不读配置文件，保持独立可用）。"""

    def __init__(self, *, peak: bool) -> None:
        self.peak = peak
        self.attribution = "call_start"
        self.timezone = "Asia/Shanghai"
        self.windows = ()
        self.moments: list = []

    def is_peak(self, moment) -> bool:
        self.moments.append(moment)
        return self.peak


def _gateway(*, cells=_CELLS, prices=None, peak=True, backend=None, calendar=True):
    load = load_or_migrate(_config(cells=cells, prices=prices))
    return (
        LLMGateway(
            backend or _Backend(),
            price_book={},
            sleep=lambda _: None,
            profiles=load,
            peak_windows=_Calendar(peak=peak) if calendar else None,
        ),
        load,
    )


def _convert(prompt_tokens: int, completion_tokens: int, prices) -> float:
    return (
        prompt_tokens / 1000 * prices["prompt_per_1k"]
        + completion_tokens / 1000 * prices["completion_per_1k"]
    )


class Test四格取价:
    @pytest.mark.parametrize(
        "peak,cached",
        [(True, None), (True, 100), (False, None), (False, 100)],
    )
    def test_四格端到端取价正确(self, peak, cached):
        backend = _Backend(cached_prompt_tokens=cached)
        gateway, _ = _gateway(backend=backend, peak=peak)
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        cell = f"{'peak' if peak else 'off_peak'}_{'hit' if cached is not None else 'miss'}"
        assert result.cell_key == cell
        assert result.cost_usd == pytest.approx(
            _convert(backend.prompt_tokens, backend.completion_tokens, _CELLS[cell])
        )
        assert result.cached_prompt_tokens == cached

    def test_price_cell_按格位直取(self):
        load = load_or_migrate(_config(cells=_CELLS))
        profile = load.profile("p1")
        calendar = _Calendar(peak=True)
        moment = _MOMENT
        cell, prices = price_cell(profile, moment=moment, cache_hit=True, is_peak=calendar.is_peak)
        assert cell == "peak_hit" and prices == _CELLS["peak_hit"]
        cell, prices = price_cell(
            profile, moment=moment, cache_hit=False, is_peak=_Calendar(peak=False).is_peak
        )
        assert cell == "off_peak_miss" and prices == _CELLS["off_peak_miss"]
        assert set(MATRIX_CELLS) == set(_CELLS)

    def test_未声明矩阵_四格皆取基础价目且与改造前逐字节一致(self):
        load = load_or_migrate(_config())
        profile = load.profile("p1")
        assert load.snapshot().price_book()["p1"] == _BASE_PRICES  # 基础价目视图
        for peak in (True, False):
            for cached in (True, False):
                cell, prices = price_cell(
                    profile, moment=None, cache_hit=cached, is_peak=_Calendar(peak=peak).is_peak
                )
                assert cell == ""  # 未区分峰谷/缓存
                assert prices == _BASE_PRICES
        assert profile.cost_usd(prompt_tokens=1000, completion_tokens=500) == pytest.approx(
            _convert(1000, 500, _BASE_PRICES)  # 改改造前的硬编码线性式同值
        )
        entry = profile.to_snapshot()
        assert set(entry) == _LEGACY_ENTRY_KEYS  # 旧形状逐字保留（不新增键）
        assert entry["prices"] == _BASE_PRICES

    @pytest.mark.parametrize("broken", ["drop_cell", "drop_key"])
    def test_声明即四格齐备_缺格或缺键即装配报错(self, broken):
        cells = {cell: dict(value) for cell, value in _CELLS.items()}
        if broken == "drop_cell":
            cells.pop("peak_hit")
        else:
            cells["off_peak_hit"].pop("completion_per_1k")
        with pytest.raises(ProfileConfigError):
            load_or_migrate(_config(cells=cells))

    def test_矩阵取值校验沿用基础价目纪律(self):
        cells = {cell: dict(value) for cell, value in _CELLS.items()}
        cells["peak_miss"]["prompt_per_1k"] = -1.0
        with pytest.raises(ProfileConfigError):
            load_or_migrate(_config(cells=cells))
        cells = {cell: dict(value) for cell, value in _CELLS.items()}
        cells["peak_miss"]["prompt_per_1k"] = True
        with pytest.raises(ProfileConfigError):
            load_or_migrate(_config(cells=cells))

    def test_单格为_0_合法_四格全_0_须显式声明(self):
        cells = {cell: dict(value) for cell, value in _CELLS.items()}
        cells["off_peak_hit"] = {"prompt_per_1k": 0.0, "completion_per_1k": 0.0}
        load = load_or_migrate(_config(cells=cells))
        assert load.profile("p1").price_matrix["off_peak_hit"]["completion_per_1k"] == 0.0
        zero = {cell: {"prompt_per_1k": 0.0, "completion_per_1k": 0.0} for cell in MATRIX_CELLS}
        with pytest.raises(ProfileConfigError, match="zero_marginal"):
            load_or_migrate(_config(cells=zero))
        zero_prices = {"prompt_per_1k": 0.0, "completion_per_1k": 0.0}
        load = load_or_migrate(_config(cells=zero, prices=zero_prices, zero_marginal=True))
        assert load.profile("p1").zero_marginal is True

    def test_矩阵档案缺渠道日历即报错_不回落基础价(self):
        load = load_or_migrate(_config(cells=_CELLS))
        profile = load.profile("p1")
        with pytest.raises(ProfileConfigError, match="price_matrix"):
            price_cell(profile, moment=None, cache_hit=False)  # 无日历、无时刻
        with pytest.raises(ProfileConfigError, match="price_matrix"):
            profile.cost_usd(prompt_tokens=1, completion_tokens=1)  # 第二条路径同样不回落
        gateway, _ = _gateway(cells=_CELLS, calendar=False)
        with pytest.raises(ProfileConfigError, match="price_matrix"):
            gateway.chat("提示词", role=Role.GENERATION)

    def test_本地缓存命中不进任何格位(self):
        backend = _Backend(cached_prompt_tokens=100)
        gateway, _ = _gateway(backend=backend)
        first = gateway.chat("同一提示词", role=Role.GENERATION)
        second = gateway.chat("同一提示词", role=Role.GENERATION)
        assert first.cell_key == "peak_hit"
        assert second.cached is True and second.cost_usd == 0.0
        assert second.cell_key == "" and second.cached_prompt_tokens is None
        assert backend.call_count == 1 and gateway.call_count == 1
        assert gateway.total_cost_usd == pytest.approx(first.cost_usd)


class Test四件套:
    def test_四件套之一_改配置后历史复算逐字节不变(self):
        load_a = load_or_migrate(_config(cells=_CELLS))
        frozen = load_a.profile("p1").to_snapshot()  # 历史节点冻结的档案条目
        serialized = json.dumps(frozen, sort_keys=True, ensure_ascii=False)
        recomputed = cost_from_prices(
            snapshot_entry_cells(frozen)["peak_miss"], prompt_tokens=1000, completion_tokens=500
        )
        doubled = {
            cell: {key: value * 10 for key, value in prices.items()}
            for cell, prices in _CELLS.items()
        }
        load_b = load_or_migrate(_config(cells=doubled))
        assert load_b.profile("p1").price_matrix["peak_miss"]["prompt_per_1k"] == pytest.approx(
            0.08
        )
        # 历史快照永不重写：序列化逐字节不变、按它复算的金额不变
        assert json.dumps(frozen, sort_keys=True, ensure_ascii=False) == serialized
        assert (
            cost_from_prices(
                snapshot_entry_cells(frozen)["peak_miss"],
                prompt_tokens=1000,
                completion_tokens=500,
            )
            == recomputed
        )
        assert recomputed == pytest.approx(_convert(1000, 500, _CELLS["peak_miss"]))

    def test_四件套之二_新旧形状读取等价(self):
        legacy = load_or_migrate(_config()).profile("p1").to_snapshot()
        uniform = (
            load_or_migrate(_config(cells={cell: dict(_BASE_PRICES) for cell in MATRIX_CELLS}))
            .profile("p1")
            .to_snapshot()
        )
        legacy_cells = snapshot_entry_cells(legacy)
        uniform_cells = snapshot_entry_cells(uniform)
        assert legacy_cells == uniform_cells  # 无 matrix 快照 vs 四格同价快照：取价一致
        assert set(legacy_cells) == set(MATRIX_CELLS)
        assert "price_matrix" not in legacy and "price_matrix" in uniform

    def test_四件套之三_缺格装配报错见四格取价(self):
        cells = {cell: dict(value) for cell, value in _CELLS.items()}
        cells.pop("off_peak_miss")
        with pytest.raises(ProfileConfigError):
            load_or_migrate(_config(cells=cells))

    def test_四件套之四_快照含矩阵与维度且无密钥_指纹随矩阵变化(self):
        load_a = load_or_migrate(_config(cells=_CELLS))
        entry = load_a.profile("p1").to_snapshot()
        assert set(entry["price_matrix"]) == set(MATRIX_CELLS)
        assert entry["declared_dimensions"] == list(DECLARED_DIMENSIONS)
        assert entry["prices"] == _BASE_PRICES  # 基础两键逐字保留
        assert entry["api_key_env"] == "EXAMPLE_API_KEY"  # 只有变量名，没有密钥值
        assert "api_key" not in entry and "secret" not in json.dumps(entry)
        same = load_or_migrate(_config(cells={cell: dict(_CELLS[cell]) for cell in MATRIX_CELLS}))
        assert same.snapshot().fingerprint == load_a.snapshot().fingerprint  # 同矩阵同指纹
        changed = load_or_migrate(
            _config(
                cells={
                    **_CELLS,
                    "peak_hit": {"prompt_per_1k": 0.003, "completion_per_1k": 0.005},
                }
            )
        )
        assert changed.snapshot().fingerprint != load_a.snapshot().fingerprint  # 改矩阵即变

    def test_旧快照读取仍走基础价且旧键集不含新键(self):
        legacy = load_or_migrate(_config()).profile("p1").to_snapshot()
        assert "price_matrix" not in legacy and "declared_dimensions" not in legacy
        assert snapshot_entry_cells(legacy)["peak_hit"] == _BASE_PRICES  # 四格同价
        # 旧扁平价目表快照（未接档案形态）同样不受影响
        book = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}
        from core.llm_gateway.profiles import legacy_price_book_snapshot

        entry = legacy_price_book_snapshot(book).to_dict()["profiles"][0]
        assert set(entry) == _LEGACY_ENTRY_KEYS
        assert snapshot_entry_cells(entry)["peak_miss"] == book["mock-copy-v1"]

    def test_折算口径只有一份_第二条路径已委派(self):
        """`ModelProfile.cost_usd` 与网关折算同取 `price_cell` + `cost_from_prices`。"""
        import inspect

        from core.llm_gateway import profiles as profiles_module

        source = inspect.getsource(profiles_module.ModelProfile.cost_usd)
        assert "price_cell" in source and "cost_from_prices" in source
        assert "prompt_per_1k" not in source  # 不再自己写线性式
        legacy = load_or_migrate(_config()).profile("p1")
        assert legacy.cost_usd(prompt_tokens=1000, completion_tokens=1000) == pytest.approx(
            cost_from_prices(_BASE_PRICES, prompt_tokens=1000, completion_tokens=1000)
        )


def test_档案可替换且不影响矩阵字段():
    """`dataclasses.replace`（既有测试与运行期用法）对矩阵档案同样成立。"""
    load = load_or_migrate(_config(cells=_CELLS))
    profile = replace(load.profile("p1"), prices={"prompt_per_1k": 0.1, "completion_per_1k": 0.2})
    assert profile.price_matrix["peak_miss"]["prompt_per_1k"] == 0.008
    assert profile.prices["prompt_per_1k"] == 0.1
