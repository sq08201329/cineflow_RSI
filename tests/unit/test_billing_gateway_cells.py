"""两维价目（峰谷 × 厂商缓存命中）的**装配面端到端**与改价不漂移（功能 019 / US3 / T1938）。

契约 C5/C6/C7/C8。`tests/unit/test_billing_price_matrix.py` 已覆盖纯解析与四件套的模块级口径；
本文件补的是**装配面**的四件事：

1. **经 `gateway.chat` 的四格端到端**：注入门禁 + 渠道日历后，四格的 `cost_usd` 各等于该格
   价目的折算值、`LLMResult.cell_key` 标注所用格位、`pricing_note` 记「按格位取价 + 格位键」
   （未命中档另记「厂商未报告命中 token（按未命中计）」）；
2. **本地缓存命中与厂商缓存不混算**：本地内容哈希命中零成本、不进任何格位、**不过门禁
   （不占额）**；厂商报告的命中 token 才决定 `*_hit` 格；
3. **改价不漂移**：历史节点 `config_snapshot` 落盘后**逐字节不变**、按它复算的金额不变，
   新节点用新价目（指纹随之变化）；
4. **交付配置的实情**（登记为运营侧输入）：真实两形态 `llm.profiles.*` **未声明** `price_matrix`
   ——矩阵取值是**厂商费率**（事实数字），运营给定前不发明；故四格能力由夹具举证，真实配置的
   `price_note` 用文字写明峰时/缓存口径。这条断言把该决策变成**可机检事实**。

峰谷判定：网关的 `chat` 取墙钟 `now()` 作调用开始时刻，故"峰/谷"要可复现只能**钉死判定值**；
但归属口径三字段（`attribution`/`timezone`/`windows`）原样来自**渠道配置声明**——报告与校准
记录里的口径备注因此仍是真实声明；真实声明的日历自身在固定时刻的判峰/判谷另有一例。
"""

import copy
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from core.billing.budget import FileLedger, assemble_guard, ledger_path
from core.llm_gateway.gateway import BackendResult, LLMGateway
from core.llm_gateway.profiles import (
    MATRIX_CELLS,
    cost_from_prices,
    load_or_migrate,
    snapshot_entry_cells,
)
from core.llm_gateway.routing import Role

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = {form: REPO_ROOT / "configs" / f"{form}.yaml" for form in ("movie", "shortdrama")}
PROFILE_ID = "deepseek-flash"  # `generation` 角色映射的生成档（夹具矩阵挂它）
# 四格取值两两不同：取错格位即算错钱（断言可证伪）
CELLS = {
    "peak_miss": {"prompt_per_1k": 0.0080, "completion_per_1k": 0.0160},
    "peak_hit": {"prompt_per_1k": 0.0020, "completion_per_1k": 0.0040},
    "off_peak_miss": {"prompt_per_1k": 0.0010, "completion_per_1k": 0.0020},
    "off_peak_hit": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.0010},
}
PEAK_START, PEAK_END = "08:30", "00:30"  # 两形态声明的峰时区间（闭开 [start, end)，跨夜）


class _CountingBackend:
    """假后端：固定 token 数 + 可选厂商命中 token 读数（`None` = 厂商未报告该字段）。"""

    def __init__(self, *, cached: int | None = None) -> None:
        self.call_count = 0
        self.prompt_tokens = 1000
        self.completion_tokens = 500
        self.cached_prompt_tokens = cached

    def complete(self, prompt, *, model, temperature, max_tokens) -> BackendResult:
        self.call_count += 1
        return BackendResult(
            text="伪文本",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens,
        )


class _PinnedCalendar:
    """渠道日历适配器：判定值钉死、口径字段原样取真实声明（见模块 docstring）。"""

    def __init__(self, declared, *, peak: bool) -> None:
        self.attribution = declared.attribution
        self.timezone = declared.timezone
        self.windows = declared.windows
        self.peak = peak
        self.moments: list = []

    def is_peak(self, moment) -> bool:
        self.moments.append(moment)
        return self.peak


def _payload(tmp_path: Path, *, matrix=None, form: str = "movie") -> dict:
    """形态配置的派生：账本根落 tmp；`matrix` 只挂**非零边际成本**档案。"""
    payload = copy.deepcopy(yaml.safe_load(CONFIGS[form].read_text(encoding="utf-8")))
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    if matrix:
        for profile in payload["llm"]["profiles"].values():
            if not profile.get("zero_marginal"):
                profile["price_matrix"] = {cell: dict(values) for cell, values in matrix.items()}
    return payload


def _write(tmp_path: Path, payload: dict, name: str = "form.yaml") -> Path:
    target = tmp_path / name
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _cadence(assembly, *, peak: bool):
    return _PinnedCalendar(assembly.cfg.peak_windows, peak=peak)


def _gateway(assembly, load, *, peak: bool, backend) -> LLMGateway:
    return LLMGateway(
        backend,
        price_book={},
        sleep=lambda _: None,
        profiles=load,
        spend_guard=assembly.guard,
        channel_id=assembly.channel_id,
        peak_windows=_cadence(assembly, peak=peak),
    )


def _ledger_of(assembly) -> dict:
    return FileLedger(ledger_path(assembly.root, assembly.channel_id), timeout_seconds=1.0).read()


class Test四格端到端:
    @pytest.mark.parametrize(
        "peak,cached", [(True, None), (True, 100), (False, None), (False, 100)]
    )
    def test_四格取价与格位标注(self, tmp_path, peak, cached):
        assembly = assemble_guard(_write(tmp_path, _payload(tmp_path, matrix=CELLS)))
        load = load_or_migrate(_payload(tmp_path, matrix=CELLS))
        backend = _CountingBackend(cached=cached)
        gateway = _gateway(assembly, load, peak=peak, backend=backend)

        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")

        cell = f"{'peak' if peak else 'off_peak'}_{'hit' if cached is not None else 'miss'}"
        expected = cost_from_prices(
            CELLS[cell],
            prompt_tokens=backend.prompt_tokens,
            completion_tokens=backend.completion_tokens,
        )
        assert result.cell_key == cell
        assert result.cost_usd == pytest.approx(expected)
        assert result.cached_prompt_tokens == cached
        assert result.profile_id == PROFILE_ID and result.role == str(Role.GENERATION)
        assert "按格位取价" in result.pricing_note and cell in result.pricing_note
        if cached is None:
            assert "厂商未报告命中 token（按未命中计）" in result.pricing_note
        assert backend.call_count == 1 and gateway.call_count == 1
        assert gateway.total_cost_usd == pytest.approx(expected)

    def test_报告与账目口径备注含归属与区间口径(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        assembly = assemble_guard(_write(tmp_path, payload))
        load = load_or_migrate(payload)
        gateway = _gateway(assembly, load, peak=True, backend=_CountingBackend(cached=None))
        gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")

        report = gateway.cost_report()
        entry = next(item for item in report["entries"] if item["profile_id"] == PROFILE_ID)
        notes = "；".join(entry["pricing_notes"])
        assert f"attribution={assembly.cfg.peak_windows.attribution}" in notes
        assert "call_start" in notes and f"[{PEAK_START}, {PEAK_END})" in notes
        assert assembly.cfg.peak_windows.timezone in notes
        # 三数分离的报告侧材料：估算额与所用格位都留痕（估算按未命中档保守估）
        assert entry["price_cells"] == ["peak_miss"]
        assert entry["estimated_usd"] > 0
        assert report["by_profile"][PROFILE_ID]["cost_usd"] == pytest.approx(gateway.total_cost_usd)
        assert "记账 ≠ 厂商账单" in report["accounting_note"]

    def test_未声明矩阵的档案记未区分峰谷缓存(self, tmp_path):
        payload = _payload(tmp_path)  # 不挂矩阵 = 交付配置的实情
        assembly = assemble_guard(_write(tmp_path, payload))
        load = load_or_migrate(payload)
        gateway = _gateway(assembly, load, peak=True, backend=_CountingBackend(cached=100))

        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")

        assert result.cell_key == ""  # 未区分峰谷/缓存
        assert "未区分峰谷/缓存" in result.pricing_note
        entry = next(
            item for item in gateway.cost_report()["entries"] if item["profile_id"] == PROFILE_ID
        )
        assert entry["prices"] == {"prompt_per_1k": 0.0003, "completion_per_1k": 0.0012}
        assert entry["price_cells"] == [""]


class Test本地缓存与厂商缓存不混算:
    def test_本地命中零成本不进格位不占额(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        assembly = assemble_guard(_write(tmp_path, payload))
        load = load_or_migrate(payload)
        backend = _CountingBackend(cached=100)  # 厂商报告命中 ⇒ 走 *_hit 格
        gateway = _gateway(assembly, load, peak=True, backend=backend)

        first = gateway.chat("同一提示词", role=Role.GENERATION, stage="screenplay")
        after_first = _ledger_of(assembly)
        second = gateway.chat("同一提示词", role=Role.GENERATION, stage="screenplay")
        after_second = _ledger_of(assembly)

        assert first.cell_key == "peak_hit" and first.cost_usd > 0
        # 本地命中：零成本、零后端调用、不进格位、不报厂商命中数
        assert second.cached is True and second.cost_usd == 0.0
        assert second.cell_key == "" and second.cached_prompt_tokens is None
        assert "本地内容哈希缓存命中" in second.pricing_note
        assert backend.call_count == 1 and gateway.call_count == 1
        assert gateway.total_cost_usd == pytest.approx(first.cost_usd)
        # **不占额**：本地命中完全不过门禁（账本零变化）
        assert after_second == after_first
        assert after_first["tiers"]["screenplay"]["spent_usd"] == pytest.approx(first.cost_usd)
        assert after_first["tiers"]["screenplay"]["reserved_usd"] == 0.0
        assert gateway.cache_hits == 1

    def test_不同提示词各自取格位(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        assembly = assemble_guard(_write(tmp_path, payload))
        load = load_or_migrate(payload)
        backend = _CountingBackend(cached=None)
        gateway = _gateway(assembly, load, peak=False, backend=backend)

        miss = gateway.chat("提示词甲", role=Role.GENERATION, stage="screenplay")
        hit = gateway.chat("提示词乙", role=Role.GENERATION, stage="screenplay")
        backend.cached_prompt_tokens = 250  # 厂商对第三笔报告命中
        hit2 = gateway.chat("提示词丙", role=Role.GENERATION, stage="screenplay")

        assert [miss.cell_key, hit.cell_key, hit2.cell_key] == [
            "off_peak_miss",
            "off_peak_miss",
            "off_peak_hit",
        ]
        assert hit2.cost_usd == pytest.approx(
            cost_from_prices(
                CELLS["off_peak_hit"],
                prompt_tokens=backend.prompt_tokens,
                completion_tokens=backend.completion_tokens,
            )
        )
        assert backend.call_count == 3 and gateway.call_count == 3


class Test改价不漂移:
    def test_历史节点逐字节不变_新节点用新价目(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        load_a = load_or_migrate(payload)
        frozen_entry = load_a.profile(PROFILE_ID).to_snapshot()
        # 历史节点：快照条目一旦随树冻结即**永不被重写**（落盘后逐字节比对）
        node = tmp_path / "tree-node.json"
        node.write_text(
            json.dumps(
                {"config_snapshot": {"llm_profiles": {"profiles": [frozen_entry]}}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        frozen_bytes = node.read_bytes()
        frozen_cost = cost_from_prices(
            snapshot_entry_cells(frozen_entry)["peak_miss"],
            prompt_tokens=1000,
            completion_tokens=500,
        )

        doubled = {
            cell: {key: value * 10 for key, value in prices.items()}
            for cell, prices in CELLS.items()
        }
        load_b = load_or_migrate(_payload(tmp_path, matrix=doubled))

        # ① 历史节点**逐字节不变**（改价只影响此后新装配）
        assert node.read_bytes() == frozen_bytes
        reread = json.loads(node.read_text(encoding="utf-8"))["config_snapshot"]["llm_profiles"][
            "profiles"
        ][0]
        assert json.dumps(reread, sort_keys=True, ensure_ascii=False) == json.dumps(
            frozen_entry, sort_keys=True, ensure_ascii=False
        )
        # ② 按历史节点复算的金额不变
        assert (
            cost_from_prices(
                snapshot_entry_cells(reread)["peak_miss"],
                prompt_tokens=1000,
                completion_tokens=500,
            )
            == frozen_cost
        )
        assert frozen_cost == pytest.approx(1000 / 1000 * 0.0080 + 500 / 1000 * 0.0160)
        # ③ 新节点用新价目（四格整体 ×10）
        assert load_b.profile(PROFILE_ID).price_matrix["peak_miss"][
            "prompt_per_1k"
        ] == pytest.approx(0.08)
        new_cost = cost_from_prices(
            snapshot_entry_cells(load_b.profile(PROFILE_ID).to_snapshot())["peak_miss"],
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert new_cost == pytest.approx(frozen_cost * 10)
        # ④ 指纹随价目变化（路由引用可追溯）
        assert load_b.snapshot().fingerprint != load_a.snapshot().fingerprint


class Test交付配置的实情:
    @pytest.mark.parametrize("form", ["movie", "shortdrama"])
    def test_真实配置未声明价目矩阵(self, form):
        """矩阵取值是**厂商费率**（事实数字）：运营给定前不发明，故真实配置不声明矩阵。"""
        payload = yaml.safe_load(CONFIGS[form].read_text(encoding="utf-8"))
        load = load_or_migrate(payload)

        assert load.profiles, "两形态配置必须声明 llm.profiles"
        priced = []
        for profile_id, profile in load.profiles.items():
            entry = profile.to_snapshot()
            assert profile.price_matrix == {}, (
                f"{form}:{profile_id} 不得声明 price_matrix（运营侧输入）"
            )
            assert "price_matrix" not in entry and "declared_dimensions" not in entry
            assert entry["price_note"].strip(), f"{form}:{profile_id} 的价目口径必须可追溯"
            if not profile.zero_marginal:
                priced.append(profile_id)
                # 未声明矩阵时用**文字**写明峰时/缓存口径（口径可见性不靠矩阵兜底）
                assert "峰时" in entry["price_note"] and "缓存" in entry["price_note"]
        assert priced, f"{form}: 必须有非零边际成本的计费档案"
        # 未声明矩阵 ⇒ 四格同价（旧语义）；读取端按**键集**分派，不按版本号猜
        cells = snapshot_entry_cells(load.profile(priced[0]).to_snapshot())
        assert set(cells) == set(MATRIX_CELLS)
        assert len({json.dumps(value, sort_keys=True) for value in cells.values()}) == 1

    def test_真实声明的峰谷区间在固定时刻判峰判谷(self):
        """交付配置声明的日历自身可判定（端到端用的钉死判定不影响声明的真实性）。"""
        from core.billing.budget import BudgetConfig

        cfg = BudgetConfig.from_yaml(CONFIGS["movie"])  # 只读判定，不落任何产物
        zone = ZoneInfo(cfg.peak_windows.timezone)
        assert cfg.peak_windows.is_peak(dt.datetime(2026, 9, 23, 12, 0, tzinfo=zone)) is True
        assert cfg.peak_windows.is_peak(dt.datetime(2026, 9, 23, 3, 0, tzinfo=zone)) is False
        # 跨夜区间的边界：00:30 起为谷、00:29 仍属峰
        assert cfg.peak_windows.is_peak(dt.datetime(2026, 9, 23, 0, 30, tzinfo=zone)) is False
        assert cfg.peak_windows.is_peak(dt.datetime(2026, 9, 23, 0, 29, tzinfo=zone)) is True
