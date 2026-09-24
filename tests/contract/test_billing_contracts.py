"""功能 019 契约聚合（C1~C16）+ **本特性的对抗/篡改面**（T1940；SC-004/005/006/007）。

覆盖面一句话：**"真实渠道与账单对账"的每一条明文契约在这里都有可执行断言**——
包落点与业务无关性（C1）、账单导入与批次幂等（C2）、只增快照与摘要机检（C3）、磁盘布局（C4）、
两维价目与兼容规则（C5）、峰谷时段与归属口径（C6）、厂商缓存维度的来源（C7）、快照形状升级与
改价不漂移（C8）、分档 schema 与 `stage=` 环节归属（C9）、前置校验与拒绝分型（C10）、跨进程账本
（C11）、校准先决与扩量（C12）、对账分类完备性（C13）、告警门禁两条腿（C14）、运行记录与窗口
机检（C15）、CLI 退出码（C16）。

**对抗面（本特性的，不是"沿用既有"）**：伪造 `covered_days`（自造/改写运行记录条目）、改写账单
或报告后再对账、**门禁绕过**（不传 guard 的装配、绕过 `stage=`）——各 100% 被拒或报警。逐处
（11 处调用面）的零成本分支普查在 `tests/unit/test_billing_refusal_branch.py`；此处做**聚合**举证：
被拒的那一笔全零、同轮已发生花费照记、原因点名拒绝。
"""

import ast
import copy
import datetime as dt
import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

from core.billing.bill import (
    BILL_FIELDS,
    BILL_SOURCES,
    BILL_SYSTEM_FIELDS,
    BUILTIN_FORMAT_IDS,
    BillImportError,
    SnapshotIntegrityError,
    bill_path,
    builtin_format_ids,
    load_bill,
    normalize_bill,
    save_bill,
)
from core.billing.budget import (
    ALERT_KINDS,
    WINDOW_KINDS,
    AlertLog,
    BudgetConfig,
    BudgetConfigError,
    BudgetLedgerError,
    BudgetRefusedError,
    FileLedger,
    SpendGuard,
    alerts_path,
    assemble_guard,
    channel_dir,
    ledger_path,
)
from core.billing.calibration import (
    CalibrationRecordError,
    calibration_path,
    load_calibration,
    record_calibration,
    require_calibration,
)
from core.billing.reconcile import (
    ACCOUNTING_NOTE,
    CLASSIFICATIONS,
    UNCLASSIFIED,
    ReconciliationError,
    load_report,
    reconcile,
    report_path,
    save_report,
)
from core.billing.runlog import (
    RUN_SOURCES,
    RunLogError,
    append_run,
    load_run,
    run_path,
    seal_run,
    window_coverage,
)
from core.llm_gateway.gateway import (
    BackendResult,
    LLMGateway,
    PermanentBackendError,
    TransientBackendError,
)
from core.llm_gateway.profiles import (
    MATRIX_CELLS,
    ProfileConfigError,
    load_or_migrate,
    price_cell,
    snapshot_entry_cells,
)
from core.llm_gateway.routing import Role

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMS = ("movie", "shortdrama")
MOMENT = dt.datetime(2026, 9, 1, 3, 0, tzinfo=dt.UTC)  # 渠道本地（Asia/Shanghai）= 09-01 11:00
REFUSE_AT = 1e-6  # 额度小到"任何预估额都超"
CELLS = {
    "peak_miss": {"prompt_per_1k": 0.0080, "completion_per_1k": 0.0160},
    "peak_hit": {"prompt_per_1k": 0.0020, "completion_per_1k": 0.0040},
    "off_peak_miss": {"prompt_per_1k": 0.0010, "completion_per_1k": 0.0020},
    "off_peak_hit": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.0010},
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _section(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _write_config(base: Path, payload: dict, name: str = "form.yaml") -> Path:
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _payload(tmp_path: Path, *, matrix=None) -> dict:
    payload = _section("movie")
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    for profile in payload["llm"]["profiles"].values():
        if matrix and not profile.get("zero_marginal"):
            profile["price_matrix"] = {cell: dict(values) for cell, values in matrix.items()}
    return payload


def _channel(cfg) -> str:
    return next(iter(cfg.channels))


def _bill(cfg, channel, fixture, *, text=None, bill_id=None):
    spec = cfg.channel(channel).bill
    return normalize_bill(
        text if text is not None else fixture["text"],
        channel_id=channel,
        bill_id=bill_id or fixture["bill_id"],
        period=fixture["period"],
        currency=fixture["currency"],
        source="export",
        fmt=spec.format_id,
        columns=spec.columns,
    )


def _cli(argv, capsys):
    module = importlib.import_module("ops.billing")
    code = module.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


def _chat_call_sites(path: Path) -> list[tuple[int, object]]:
    """该文件里的 `.chat(` 调用点：`(行号, stage 取值)`（`stage=<模块常量>` 也解析出来）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    sites: list[tuple[int, object]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chat"
        ):
            continue
        stage = {kw.arg: kw.value for kw in node.keywords}.get("stage")
        if isinstance(stage, ast.Constant):
            value: object = stage.value
        elif isinstance(stage, ast.Name):
            value = constants.get(stage.id)
        else:
            value = None
        sites.append((node.lineno, value))
    return sites


def _scanned_modules() -> list[Path]:
    """扫描域 = `core/`（除协议实现区 `core/llm_gateway/`）+ `agents/` + `dreaming/`。"""
    return [
        path
        for root in ("core", "agents", "dreaming")
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if not path.relative_to(REPO_ROOT).as_posix().startswith("core/llm_gateway/")
    ]


def _peak_verdict(value: bool):
    """把"峰/谷"判定值包成 `is_peak(moment)`（工厂避免闭包捕获循环变量）。"""
    return lambda _moment: value


class _CountingBackend:
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
    """判定值钉死、口径字段取真实声明（`chat` 用墙钟 now()，四格要可复现）。"""

    def __init__(self, declared, *, peak: bool) -> None:
        self.attribution = declared.attribution
        self.timezone = declared.timezone
        self.windows = declared.windows
        self.peak = peak
        self.moments: list = []

    def is_peak(self, moment) -> bool:
        self.moments.append(moment)
        return self.peak


def _guarded_gateway(cfg, root, *, backend=None, peak=True, profiles=None):
    channel = _channel(cfg)
    guard = SpendGuard(
        cfg=cfg,
        channel_id=channel,
        ledger=FileLedger(ledger_path(root, channel), timeout_seconds=1.0),
        alerts=AlertLog(alerts_path(root, channel)),
        window_context={"period": "2026-09", "run": "2026-09-01"},  # run/period 窗口实例
    )
    gateway = LLMGateway(
        backend or _CountingBackend(),
        price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        profiles=profiles,
        spend_guard=guard,
        channel_id=channel,
        peak_windows=_PinnedCalendar(cfg.peak_windows, peak=peak),
    )
    return gateway, guard


def _ledger(cfg, root) -> dict:
    return FileLedger(ledger_path(root, _channel(cfg)), timeout_seconds=1.0).read()


def _alert_kinds(cfg, root) -> list[str]:
    return [entry["kind"] for entry in AlertLog(alerts_path(root, _channel(cfg))).entries()]


# ---------------------------------------------------------------------------
# C1 包落点与业务无关性
# ---------------------------------------------------------------------------


class TestC1包落点与业务无关性:
    def test_五模块落点(self):
        for name in ("budget", "bill", "reconcile", "calibration", "runlog"):
            assert (REPO_ROOT / "core" / "billing" / f"{name}.py").is_file(), name

    def test_零反向依赖(self):
        offenders = [
            f"{path.name}: {module}"
            for path in sorted((REPO_ROOT / "core" / "billing").glob("*.py"))
            for module in _imports(path)
            if module.split(".")[0] in {"agents", "dreaming", "web"}
        ]
        assert offenders == []
        # 网关对本包**零 import**（自 016 起的"独立可用"边界）：反向也成立
        offenders = [
            f"{path.relative_to(REPO_ROOT)}: {module}"
            for path in sorted((REPO_ROOT / "core" / "llm_gateway").rglob("*.py"))
            for module in _imports(path)
            if module == "core.billing" or module.startswith("core.billing.")
        ]
        assert offenders == []


# ---------------------------------------------------------------------------
# C2/C3/C4 账单导入、只增快照、磁盘布局
# ---------------------------------------------------------------------------


class TestC2导入面:
    def test_内置格式与必填列(self, budget_config_factory, billing_bill_fixture, billing_root):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        spec = cfg.channel(channel).bill
        assert tuple(builtin_format_ids()) == BUILTIN_FORMAT_IDS == ("csv_lines", "json_lines")
        assert BILL_FIELDS == (
            "entry_id",
            "amount",
            "currency",
            "period",
            "line_kind",
            "amount_sign",
        )
        assert BILL_SOURCES == ("export", "api")
        bill = _bill(cfg, channel, billing_bill_fixture)
        assert len(bill.entries) == len(billing_bill_fixture["rows"])
        assert bill.raw_ref and bill.fetched_at and bill.system_digest
        # 必填列缺声明 ⇒ 导入报错（分类的输入面只能是**声明的**驱动列）
        for missing in BILL_FIELDS:
            stripped = {k: v for k, v in spec.columns.items() if k != missing}
            with pytest.raises(BillImportError, match=missing):
                normalize_bill(
                    billing_bill_fixture["text"],
                    channel_id=channel,
                    bill_id="b1",
                    period="2026-09",
                    currency="USD",
                    source="export",
                    fmt=spec.format_id,
                    columns=stripped,
                )
        # 批次幂等（append-only 拒重产）
        save_bill(bill, root=billing_root)
        with pytest.raises(SnapshotIntegrityError):
            save_bill(bill, root=billing_root)


class TestC3只增快照与摘要机检:
    def test_改写系统字段即拒绝采信(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        assert (
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)["bill_id"]
            == bill.bill_id
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"] = payload["entries"][:-1]  # 省略条目（系统字段被改写）
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SnapshotIntegrityError):
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)
        assert BILL_SYSTEM_FIELDS

    def test_人工批注只追加(
        self, tmp_path, budget_config_factory, billing_bill_fixture, billing_root
    ):
        from core.billing.bill import append_override

        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        append_override(
            path,
            by="运营",
            reason="与厂商对过账",
            at="2026-09-30T00:00:00+00:00",
            system_fields=BILL_SYSTEM_FIELDS,
        )
        payload = load_bill(bill.bill_id, channel_id=channel, root=billing_root)
        assert payload["overrides"] == [
            {"by": "运营", "reason": "与厂商对过账", "at": "2026-09-30T00:00:00+00:00"}
        ]
        assert payload["system_digest"] == bill.system_digest  # 批注不触系统字段


class TestC4磁盘布局:
    def test_七处路径同源(self):
        root = Path("/tmp/root")
        assert bill_path(root, "llm", "b1") == root / "llm" / "bills" / "b1.json"
        assert report_path(root, "llm", "2026-09") == root / "llm" / "reports" / "2026-09.json"
        assert calibration_path(root, "llm", "c1") == root / "llm" / "calibrations" / "c1.json"
        assert run_path(root, "llm", "2026-09-01") == root / "llm" / "runs" / "2026-09-01.json"
        assert ledger_path(root, "llm") == root / "llm" / "ledger.json"
        assert alerts_path(root, "llm") == root / "llm" / "alerts.jsonl"
        assert channel_dir(root, "llm") == root / "llm"


# ---------------------------------------------------------------------------
# C5/C6/C7/C8 两维价目、峰谷口径、缓存维度、快照语义
# ---------------------------------------------------------------------------


class TestC5两维价目:
    def _config(self, cells=None):
        profile = {
            "base_url": "https://api.example.invalid",
            "api_key_env": "EXAMPLE_API_KEY",
            "prices": {"prompt_per_1k": 0.003, "completion_per_1k": 0.006},
            "price_note": "夹具价目",
        }
        if cells is not None:
            profile["price_matrix"] = cells
        return {"llm": {"profiles": {"p1": profile}, "roles": {}, "default_profile": "p1"}}

    def test_四格齐备与缺格报错(self):
        load = load_or_migrate(self._config(cells=CELLS))
        profile = load.profile("p1")
        assert set(profile.price_matrix) == set(MATRIX_CELLS) == set(CELLS)
        assert price_cell(profile, moment=MOMENT, cache_hit=True, is_peak=lambda _: True) == (
            "peak_hit",
            CELLS["peak_hit"],
        )
        broken = {cell: dict(value) for cell, value in CELLS.items()}
        broken.pop("off_peak_hit")
        with pytest.raises(ProfileConfigError):
            load_or_migrate(self._config(cells=broken))
        broken = {cell: dict(value) for cell, value in CELLS.items()}
        broken["peak_miss"].pop("completion_per_1k")
        with pytest.raises(ProfileConfigError):
            load_or_migrate(self._config(cells=broken))

    def test_未声明矩阵取基础价且不回落(self):
        legacy = load_or_migrate(self._config()).profile("p1")
        assert legacy.price_matrix == {}
        for peak in (True, False):
            for hit in (True, False):
                verdict = _peak_verdict(peak)
                assert price_cell(legacy, moment=None, cache_hit=hit, is_peak=verdict) == (
                    "",
                    legacy.prices,
                )
        matrix = load_or_migrate(self._config(cells=CELLS)).profile("p1")
        with pytest.raises(ProfileConfigError, match="price_matrix"):
            price_cell(matrix, moment=None, cache_hit=False)  # 声明矩阵后不回落基础价


class TestC6峰谷口径:
    def test_三键必填与取值域(self, billing_budget_factory):
        section = billing_budget_factory()
        for key in ("timezone", "attribution", "windows"):
            stripped = copy.deepcopy(section["peak_windows"])
            stripped.pop(key)
            payload = copy.deepcopy(section)
            payload["peak_windows"] = stripped
            with pytest.raises(BudgetConfigError):
                BudgetConfig.from_dict({"budget": payload})
        payload = copy.deepcopy(section)
        payload["peak_windows"]["attribution"] = "call_end"
        with pytest.raises(BudgetConfigError, match="call_start"):
            BudgetConfig.from_dict({"budget": payload})
        assert WINDOW_KINDS == ("run", "day", "period")

    def test_跨夜区间闭开判峰(self):
        from zoneinfo import ZoneInfo

        from core.billing.budget import PeakWindow, PeakWindows

        calendar = PeakWindows(
            timezone="Asia/Shanghai",
            attribution="call_start",
            windows=(PeakWindow(start="08:30", end="00:30"),),
        )
        zone = ZoneInfo("Asia/Shanghai")
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 23, 0, tzinfo=zone)) is True
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 12, 0, tzinfo=zone)) is True
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 0, 29, tzinfo=zone)) is True
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 0, 30, tzinfo=zone)) is False
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 1, 0, tzinfo=zone)) is False
        assert calendar.is_peak(dt.datetime(2026, 9, 23, 3, 0, tzinfo=zone)) is False
        assert (
            PeakWindows(timezone="Asia/Shanghai", attribution="call_start", windows=()).is_peak(
                dt.datetime(2026, 9, 23, 23, 0, tzinfo=zone)
            )
            is False
        )  # 空列表 = 全谷时（显式声明）

    def test_归属口径三处可见(
        self, tmp_path, billing_root, budget_config_factory, billing_calibration_factory
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        # ② 档位/门禁快照
        snapshot = cfg.to_snapshot(channel)["peak_windows_snapshot"]
        assert snapshot == {
            "timezone": cfg.peak_windows.timezone,
            "attribution": "call_start",
            "windows": [window.to_snapshot() for window in cfg.peak_windows.windows],
        }
        gateway, _ = _guarded_gateway(cfg, billing_root)
        gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        # ① 账目/报告口径备注（含归属与闭开区间口径）
        notes = json.dumps(gateway.cost_report(), ensure_ascii=False)
        assert "attribution=call_start" in notes and "[08:30, 00:30)" in notes
        # ③ 校准记录（快照 + note）
        params = billing_calibration_factory(cfg)
        record_calibration(cfg=cfg, **params, root=billing_root)
        stored = load_calibration(params["calibration_id"], channel_id=channel, root=billing_root)
        assert stored["prices_snapshot"]["peak_windows_snapshot"]["attribution"] == "call_start"
        assert "峰谷归属=call_start" in stored["note"] and "[08:30, 00:30)" in stored["note"]
        assert tmp_path  # 夹具根落 tmp（仓库零污染）


class TestC7缓存维度:
    def test_未报命中即按未命中计不按零(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        cfg = BudgetConfig.from_dict(payload)
        load = load_or_migrate(payload)
        gateway, _ = _guarded_gateway(
            cfg, tmp_path / "billing", backend=_CountingBackend(), profiles=load
        )
        result = gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert result.cell_key == "peak_miss" and result.cached_prompt_tokens is None
        assert "厂商未报告命中 token（按未命中计）" in result.pricing_note
        assert result.cost_usd > 0  # 未校准时命中档**不得**默认为 0

    def test_读数非法即报错不静默钳制(self, tmp_path):
        payload = _payload(tmp_path, matrix=CELLS)
        cfg = BudgetConfig.from_dict(payload)
        load = load_or_migrate(payload)
        backend = _CountingBackend(cached=1001)  # > prompt_tokens(1000)
        gateway, _ = _guarded_gateway(cfg, tmp_path / "billing", backend=backend, profiles=load)
        with pytest.raises(TransientBackendError, match="命中"):
            gateway.chat("提示词", role=Role.GENERATION, stage="screenplay")
        assert gateway.total_cost_usd > 0 and gateway.call_count == 1  # 费用照记（原则二）
        assert backend.call_count == 1
        # 分型互斥：命中读数非法是"可重试的厂商读数问题"，不是预算拒绝
        assert not issubclass(TransientBackendError, BudgetRefusedError)

    def test_旧后端不设字段行为不变(self, billing_root, budget_config_factory):
        cfg = budget_config_factory()
        gateway, _ = _guarded_gateway(cfg, billing_root)
        result = gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert result.cached_prompt_tokens is None and result.cell_key == ""
        assert result.usage == {"prompt_tokens": 1000, "completion_tokens": 500}


class TestC8快照升级与改价不漂移:
    def test_四件套(self):
        payload = {"llm": {"profiles": {}, "roles": {}, "default_profile": "p1"}}
        payload["llm"]["profiles"]["p1"] = {
            "base_url": "https://api.example.invalid",
            "api_key_env": "EXAMPLE_API_KEY",
            "prices": {"prompt_per_1k": 0.003, "completion_per_1k": 0.006},
            "price_note": "夹具价目",
            "price_matrix": {cell: dict(value) for cell, value in CELLS.items()},
        }
        load_a = load_or_migrate(payload)
        frozen = load_a.profile("p1").to_snapshot()
        serialized = json.dumps(frozen, sort_keys=True, ensure_ascii=False)
        # ① 改配置后历史复算逐字节不变
        changed = copy.deepcopy(payload)
        changed["llm"]["profiles"]["p1"]["price_matrix"] = {
            cell: {key: value * 10 for key, value in prices.items()}
            for cell, prices in CELLS.items()
        }
        load_b = load_or_migrate(changed)
        assert json.dumps(frozen, sort_keys=True, ensure_ascii=False) == serialized
        # ② 新旧形状读取等价（无 matrix ⇒ 四格同价）
        legacy = {
            "profile_id": "p1",
            "prices": {"prompt_per_1k": 0.003, "completion_per_1k": 0.006},
        }
        assert len(set(map(json.dumps, snapshot_entry_cells(legacy).values()))) == 1
        # ③ 缺格装配报错（见 C5）
        # ④ 快照含 matrix 与维度、无密钥、指纹随矩阵变化
        assert frozen["declared_dimensions"] == ["peak_off_peak", "cache_hit_miss"]
        assert "api_key" not in frozen and "EXAMPLE_API_KEY" in json.dumps(frozen)
        assert load_b.snapshot().fingerprint != load_a.snapshot().fingerprint
        assert load_b.profile("p1").price_matrix["peak_miss"]["prompt_per_1k"] == pytest.approx(
            0.08
        )


# ---------------------------------------------------------------------------
# C9/C10 分档 schema、环节归属、前置校验
# ---------------------------------------------------------------------------


class TestC9分档与环节归属:
    def test_缺项即拒绝启动(self, tmp_path):
        with pytest.raises(BudgetConfigError):
            BudgetConfig.from_dict({})
        with pytest.raises(BudgetConfigError):
            BudgetConfig.from_dict({"budget": {}})
        payload = _section("movie")
        payload.pop("budget")
        with pytest.raises(BudgetConfigError):
            assemble_guard(_write_config(tmp_path, payload))
        payload = _section("movie")
        payload["budget"]["tiers"]["screenplay"].pop("limit_usd")
        with pytest.raises(BudgetConfigError, match="limit_usd"):
            assemble_guard(_write_config(tmp_path, payload, name="no-limit.yaml"))

    def test_调用点_stage_取值属于声明的环节(self):
        declared = {str(key) for form in FORMS for key in _section(form)["budget"]["tiers"]}
        sites = [
            (path, lineno, value)
            for path in _scanned_modules()
            for lineno, value in _chat_call_sites(path)
        ]
        assert len(sites) == 8, f"调用点计数应为 8：{[(str(p), n) for p, n, _ in sites]}"
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} stage={value!r}"
            for path, lineno, value in sites
            if value not in declared
        ]
        assert offenders == []
        # `ops/smoke_llm.py` 的校准入口另有一处（不在扫描域内，同样声明 `stage=`）
        ops_sites = _chat_call_sites(REPO_ROOT / "ops" / "smoke_llm.py")
        assert len(ops_sites) == 1 and ops_sites[0][1] in declared


class TestC10前置校验与拒绝分型:
    def test_超限拒绝零调用零入账(
        self, billing_root, billing_budget_factory, budget_config_factory
    ):
        tiers = billing_budget_factory(tier_limit_usd=None, window_kind="day")["tiers"]
        for tier in tiers.values():
            tier["limit_usd"] = REFUSE_AT
        cfg = budget_config_factory(tiers=tiers)
        backend = _CountingBackend()
        gateway, _ = _guarded_gateway(cfg, billing_root, backend=backend)
        with pytest.raises(BudgetRefusedError) as exc_info:
            gateway.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert exc_info.value.reason == "over_limit"
        assert exc_info.value.remaining_usd is not None and exc_info.value.estimated_usd is not None
        # 后端 0 次调用、网关三量不变、缓存不写
        assert backend.call_count == 0
        assert gateway.call_count == 0 and gateway.total_cost_usd == 0.0
        assert gateway.cost_breakdown() == {}
        assert _ledger(cfg, billing_root)["tiers"]["screenplay"]["refusals"] == 1
        # 分型互斥：预算拒绝 ≠ 厂商 401 ≠ 厂商 429
        assert issubclass(BudgetRefusedError, Exception)
        assert not issubclass(BudgetRefusedError, (TransientBackendError, PermanentBackendError))
        assert "预算拒绝" in str(exc_info.value)
        assert _alert_kinds(cfg, billing_root) == ["budget_refused"]

    def test_缺_stage_即拒绝_tier_undeclared(self, billing_root, budget_config_factory):
        cfg = budget_config_factory()
        gateway, _ = _guarded_gateway(cfg, billing_root)
        with pytest.raises(BudgetRefusedError) as exc_info:
            gateway.chat("提示词", model="mock-copy-v1")  # 绕过 stage= 的尝试
        assert exc_info.value.reason == "tier_undeclared"
        # 未声明档不写账本（事实落在 alerts.jsonl）
        assert _ledger(cfg, billing_root).get("tiers", {}) == {}
        assert "budget_refused" in _alert_kinds(cfg, billing_root)

    def test_已发生花费照记_被拒的那一笔不入账(
        self, billing_root, billing_budget_factory, budget_config_factory
    ):
        probe = LLMGateway(
            _CountingBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        limit = probe.estimate_cost("第一笔", model="mock-copy-v1")  # 恰好够一笔的预估额
        tiers = billing_budget_factory(tier_limit_usd=None, window_kind="day")["tiers"]
        for tier in tiers.values():
            tier["limit_usd"] = limit
        cfg = budget_config_factory(tiers=tiers)
        backend = _CountingBackend()
        gateway, _ = _guarded_gateway(cfg, billing_root, backend=backend)
        first = gateway.chat("第一笔", model="mock-copy-v1", stage="screenplay")
        assert first.cost_usd > 0 and backend.call_count == 1
        spent_after_first = _ledger(cfg, billing_root)["tiers"]["screenplay"]["spent_usd"]
        assert spent_after_first == pytest.approx(first.cost_usd)
        with pytest.raises(BudgetRefusedError):
            gateway.chat("第二笔", model="mock-copy-v1", stage="screenplay")
        # 同轮已发生的花费**照记**；被拒的那一笔不入账（后端没被调用、账本未变）
        assert gateway.call_count == 1 and gateway.total_cost_usd == pytest.approx(first.cost_usd)
        assert backend.call_count == 1
        assert _ledger(cfg, billing_root)["tiers"]["screenplay"]["spent_usd"] == pytest.approx(
            spent_after_first
        )

    def test_真实装配点必传非空守卫(self):
        """**门禁绕过**的静态面：两个真实装配点都显式传 `spend_guard=`。"""
        for rel in ("agents/pilot/backends.py", "ops/smoke_llm.py"):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert re.search(r"spend_guard=\s*[A-Za-z_]", source), rel
            assert "spend_guard=None" not in source, rel

    def test_调用点零成本分支_被拒全零且原因点名(
        self,
        billing_root,
        billing_budget_factory,
        budget_config_factory,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        from agents.screenplay.loop import run_screenplay_round
        from core.tree.models import CostRecord, NodeStatus
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy

        tiers = billing_budget_factory(tier_limit_usd=None, window_kind="day")["tiers"]
        for tier in tiers.values():
            tier["limit_usd"] = REFUSE_AT
        cfg = budget_config_factory(tiers=tiers)
        backend = _CountingBackend()
        gateway, _ = _guarded_gateway(cfg, billing_root, backend=backend)
        result = run_screenplay_round(
            round_id="contract-refuse",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=gateway,
            config=screenplay_config,
            inputs={
                "topic": "病房里的三个月",
                "target_duration_min": 90,
                "constraints": ["单场景为主"],
            },
        )
        nodes = [node for node in tree_store.nodes_of(result.tree_id) if node.parent_id is not None]
        assert nodes and all(node.status is NodeStatus.FAILED for node in nodes)
        # 被拒的那一笔不入账：节点成本全零；原因点名"预算拒绝"
        assert all(node.cost == CostRecord() for node in nodes)
        assert all("预算拒绝" in node.observation_context["reject_reason"] for node in nodes)
        assert backend.call_count == 0 and gateway.call_count == 0
        assert gateway.total_cost_usd == 0.0 and gateway.cost_breakdown() == {}


# ---------------------------------------------------------------------------
# C11/C12 跨进程账本、校准先决
# ---------------------------------------------------------------------------


class TestC11跨进程账本:
    def test_锁被占即超时拒绝(self, billing_ledger_factory, billing_ledger_lock):
        ledger = billing_ledger_factory(timeout_seconds=0.05)
        with billing_ledger_lock(ledger):
            with pytest.raises(BudgetLedgerError, match="锁超时"):
                ledger.update(lambda payload: payload)  # 不无锁写、不静默放行

    def test_revision_单调且原子替换(self, billing_ledger_factory):
        ledger = billing_ledger_factory()
        assert ledger.read()["revision"] == 0
        first, _ = ledger.update(lambda payload: payload.update({"tiers": {}}))
        second, _ = ledger.update(lambda payload: payload.setdefault("updated_at", ""))
        assert first["revision"] == 1 and second["revision"] == 2
        assert ledger.read()["revision"] == 2
        assert not list(ledger.path.parent.glob("*.tmp*"))  # 原子替换不留临时文件


class TestC12校准先决与扩量:
    def test_记录只增不改(self, billing_root, budget_config_factory, billing_calibration_factory):
        cfg = budget_config_factory()
        params = billing_calibration_factory(cfg)
        record_calibration(cfg=cfg, **params, root=billing_root)
        with pytest.raises(CalibrationRecordError, match="不可变快照"):
            record_calibration(cfg=cfg, **params, root=billing_root)

    def test_六条先决任一不满足即拒绝(
        self, billing_root, budget_config_factory, billing_calibration_factory
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        with pytest.raises(CalibrationRecordError, match="无 calibration_id"):
            require_calibration(
                "", cfg=cfg, channel_id=channel, tier_id="screenplay", root=billing_root
            )
        with pytest.raises(CalibrationRecordError, match="不存在|不可用"):
            require_calibration(
                "ghost", cfg=cfg, channel_id=channel, tier_id="screenplay", root=billing_root
            )
        for outcome, pattern in (
            ("fail", "passed"),
            ("insufficient", "样本量不足"),
            ("expired", "超期"),
        ):
            params = billing_calibration_factory(cfg, outcome=outcome)
            record_calibration(cfg=cfg, **params, root=billing_root)
            with pytest.raises(CalibrationRecordError, match=pattern):
                require_calibration(
                    params["calibration_id"],
                    cfg=cfg,
                    channel_id=channel,
                    tier_id="screenplay",
                    root=billing_root,
                )
        params = billing_calibration_factory(cfg)
        record_calibration(cfg=cfg, **params, root=billing_root)
        assert (
            require_calibration(
                params["calibration_id"],
                cfg=cfg,
                channel_id=channel,
                tier_id="screenplay",
                root=billing_root,
            )["passed"]
            is True
        )
        # 环节不符同样拒绝（先决条件②的一半）
        with pytest.raises(CalibrationRecordError, match="环节"):
            require_calibration(
                params["calibration_id"],
                cfg=cfg,
                channel_id=channel,
                tier_id="promo",
                root=billing_root,
            )

    def test_扩量定点改写且留痕(
        self, tmp_path, billing_root, budget_config_factory, billing_calibration_factory
    ):
        from core.billing.calibration import raise_tier

        cfg = budget_config_factory()
        channel = _channel(cfg)
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        before = config.read_text(encoding="utf-8")
        params = billing_calibration_factory(cfg)
        record_calibration(cfg=cfg, **params, root=billing_root)
        with pytest.raises(CalibrationRecordError, match="拒绝扩量"):
            raise_tier(
                channel,
                "screenplay",
                9.0,
                calibration_id="ghost",
                by="运营",
                reason="扩量",
                cfg=cfg,
                config_path=config,
                root=billing_root,
            )
        assert config.read_text(encoding="utf-8") == before  # 拒绝时配置一字不改
        assert _alert_kinds(cfg, billing_root) == ["uncalibrated_raise"]
        report = raise_tier(
            channel,
            "screenplay",
            9.0,
            calibration_id=params["calibration_id"],
            by="运营",
            reason="最小规模校准合格",
            cfg=cfg,
            config_path=config,
            root=billing_root,
        )
        assert report["event"] == "tier_raised"
        assert _alert_kinds(cfg, billing_root) == ["uncalibrated_raise", "tier_raised"]
        rewritten = yaml.safe_load(config.read_text(encoding="utf-8"))["budget"]["tiers"][
            "screenplay"
        ]
        assert rewritten["limit_usd"] == pytest.approx(9.0)
        assert rewritten["calibrated_by"] == params["calibration_id"]


# ---------------------------------------------------------------------------
# C13/C14 对账分类完备性与告警门禁
# ---------------------------------------------------------------------------


class TestC13对账分类:
    def test_六类齐备且每条带分类与备注(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = reconcile(
            billing_bill_fixture["period"],
            channel_id=channel,
            gateway_ledger=billing_gateway_ledger_fixture,
            bill=_bill(cfg, channel, billing_bill_fixture),
            cfg=cfg,
        )
        assert len(CLASSIFICATIONS) == 6 and UNCLASSIFIED not in CLASSIFICATIONS
        classes = {item.classification for item in report.items}
        assert classes - {UNCLASSIFIED} == set(CLASSIFICATIONS)
        assert UNCLASSIFIED in classes
        for item in report.items:
            assert item.classification and item.note and isinstance(item.delta_usd, float)
        # 未分类 ⇒ 未解释 ⇒ 告警（100%）
        assert report.unexplained and "unexplained_delta" in report.alerts
        assert report.has_alerts() is True
        assert "留待下期" in report.accounting_note
        assert "不得" in report.accounting_note and "网关记账" in report.accounting_note
        # 免费额度与折扣条目不静默按 0 记账
        discounts = [item for item in report.items if item.classification == "免费额度与折扣"]
        assert discounts and all(item.bill_usd != 0.0 for item in discounts)
        assert ACCOUNTING_NOTE == report.accounting_note

    def test_无账单或批次不符即拒绝产出(
        self, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        bill = _bill(cfg, channel, billing_bill_fixture)
        with pytest.raises(ReconciliationError, match="拒绝产出"):
            reconcile(
                "2026-09",
                channel_id=channel,
                gateway_ledger=billing_gateway_ledger_fixture,
                bill=type(bill)(
                    **{
                        **{
                            key: getattr(bill, key)
                            for key in (
                                "channel_id",
                                "bill_id",
                                "period",
                                "currency",
                                "source",
                                "raw_ref",
                                "fetched_at",
                                "format_id",
                                "system_digest",
                            )
                        },
                        "entries": (),
                    }
                ),
                cfg=cfg,
            )

    def test_零差异仍须分类与备注(self, budget_config_factory):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        spec = cfg.channel(channel).bill
        text = (
            "entry_id,amount,currency,period,line_kind,amount_sign,model_ref,"
            "fx_rate,fx_source,fx_at,note\n"
            'b-1,2.00,USD,2026-09,usage,charge,deepseek-flash,,,,"与网关记账一致"\n'
        )
        bill = normalize_bill(
            text,
            channel_id=channel,
            bill_id="clean",
            period="2026-09",
            currency="USD",
            source="export",
            fmt=spec.format_id,
            columns=spec.columns,
        )
        report = reconcile(
            "2026-09",
            channel_id=channel,
            gateway_ledger={"by_profile": {"deepseek-flash": {"calls": 1, "cost_usd": 2.0}}},
            bill=bill,
            cfg=cfg,
        )
        assert report.has_alerts() is False and report.deviates is False
        assert report.items[0].classification == "计费口径"
        assert "零差异" in report.items[0].note


class TestC14告警门禁:
    def test_缺_alerts_落盘即红(
        self, tmp_path, budget_config_factory, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        report = reconcile(
            billing_bill_fixture["period"],
            channel_id=channel,
            gateway_ledger=billing_gateway_ledger_fixture,
            bill=_bill(cfg, channel, billing_bill_fixture),
            cfg=cfg,
        )
        root = tmp_path / "billing"
        save_report(report, root=root)
        assert set(report.alerts) <= set(ALERT_KINDS)
        kinds = [entry["kind"] for entry in AlertLog(alerts_path(root, channel)).entries()]
        assert set(report.alerts) <= set(kinds)  # 告警必须**落盘**（不能只躺在报告里）

    def test_报告含未解释项却返回0即红(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        channel = next(iter(BudgetConfig.from_yaml(config).channels))
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        assert (
            _cli(
                [
                    "import-bill",
                    "--channel",
                    channel,
                    "--file",
                    str(source),
                    "--bill-id",
                    billing_bill_fixture["bill_id"],
                    "--period",
                    billing_bill_fixture["period"],
                    "--config",
                    str(config),
                ],
                capsys,
            )[0]
            == 0
        )
        code, out = _cli(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                billing_bill_fixture["period"],
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        # 未解释项非空 ⇒ 退出码**必须**非 0（门禁不得放行）
        assert out["unexplained"] and code != 0
        # 省略未解释项（改写系统字段）⇒ 报告完整性机检拒绝采信 ⇒ 仍告警
        path = report_path(tmp_path / "billing", channel, billing_bill_fixture["period"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["unexplained"] = []
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SnapshotIntegrityError):
            load_report(
                billing_bill_fixture["period"], channel_id=channel, root=tmp_path / "billing"
            )
        code, out = _cli(["alert-check", "--channel", channel, "--config", str(config)], capsys)
        assert code == 1 and out["has_alerts"] is True

    def test_无账单不得产零差异报告(self, capsys, tmp_path, billing_gateway_ledger_fixture):
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        channel = next(iter(BudgetConfig.from_yaml(config).channels))
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        code, out = _cli(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                "2026-09",
                "--bill-id",
                "ghost",
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "拒绝产出" in out["error"]
        assert not report_path(tmp_path / "billing", channel, "2026-09").exists()


# ---------------------------------------------------------------------------
# C15/C16 运行记录窗口机检、CLI 退出码
# ---------------------------------------------------------------------------


class TestC15窗口机检:
    def _fill(self, cfg, channel, root, entries):
        for params in entries:
            append_run(channel, cfg=cfg, root=root, **params)

    def test_覆盖与连续双条件(self, tmp_path, budget_config_factory, billing_runlog_factory):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        root = tmp_path / "runs"
        self._fill(cfg, channel, root, billing_runlog_factory("scattered")["entries"])
        strict = window_coverage(channel, cfg=cfg, root=root, end="2026-09-10")
        tolerant = window_coverage(
            channel, cfg=cfg, root=root, end="2026-09-10", gap_tolerance_days=2
        )
        assert strict["covered_days"] == 8 and strict["meets"] is False
        assert strict["gaps"] == [{"from": "2026-09-05", "to": "2026-09-06", "days": 2}]
        assert strict["coverage_shortfall_days"] == 0 and strict["gap_shortfall_days"] == 2
        assert tolerant["meets"] is True and tolerant["continuous"] is False  # 通过不谎报连续
        # 不插值：缺口那两天记录面根本没有文件
        assert not run_path(root, channel, "2026-09-05").exists()

    def test_只计真实运行日且回落必须带原因(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        root = tmp_path / "runs"
        self._fill(cfg, channel, root, billing_runlog_factory("fallback")["entries"])
        coverage = window_coverage(channel, cfg=cfg, root=root, end="2026-09-03")
        assert coverage["covered_days"] == 2 and coverage["covered_dates"] == [
            "2026-09-01",
            "2026-09-03",
        ]
        assert set(RUN_SOURCES) == {"real", "simulated", "fallback"}
        with pytest.raises(RunLogError, match="fallback_reason"):
            append_run(
                channel,
                cfg=cfg,
                root=root,
                moment=MOMENT + dt.timedelta(days=5),
                stage="screenplay",
                source="fallback",
            )

    def test_封存后追加拒绝(self, tmp_path, budget_config_factory, billing_runlog_factory):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        root = tmp_path / "runs"
        self._fill(cfg, channel, root, billing_runlog_factory("continuous")["entries"])
        seal_run("2026-09-01", channel_id=channel, root=root)
        with pytest.raises(RunLogError, match="sealed"):
            append_run(
                channel, cfg=cfg, root=root, moment=MOMENT, stage="screenplay", source="real"
            )


class TestC16CLI退出码:
    def test_七子命令的退出码语义(self, capsys, tmp_path):
        module = importlib.import_module("ops.billing")
        for name in (
            "tiers",
            "calibrate",
            "raise-tier",
            "import-bill",
            "reconcile",
            "alert-check",
            "runs",
        ):
            with pytest.raises(SystemExit) as exit_info:
                module.main([name, "--help"])
            assert exit_info.value.code == 0
        capsys.readouterr()
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        channel = next(iter(BudgetConfig.from_yaml(config).channels))
        assert _cli(["tiers", "--channel", channel, "--config", str(config)], capsys)[0] == 0
        assert (
            _cli(["tiers", "--channel", "nope", "--config", str(config)], capsys)[0] == 2
        )  # 配置错误
        code, out = _cli(
            ["runs", "--channel", channel, "--end", "2026-09-08", "--config", str(config)], capsys
        )
        assert code == 1 and out["meets"] is False  # 未达标如实报缺口 ⇒ 1
        code, out = _cli(
            [
                "raise-tier",
                "--channel",
                channel,
                "--tier",
                "screenplay",
                "--limit-usd",
                "9.0",
                "--calibration",
                "ghost",
                "--by",
                "运营",
                "--reason",
                "扩量",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and out["reason"] == "uncalibrated_raise"


def _with_root(payload: dict, tmp_path: Path) -> dict:
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    return payload


# ---------------------------------------------------------------------------
# 本特性的对抗 / 篡改面（聚合举证）
# ---------------------------------------------------------------------------


class Test对抗与篡改面:
    def test_伪造_covered_days_即断链报错(
        self, tmp_path, budget_config_factory, billing_runlog_factory
    ):
        cfg = budget_config_factory()
        channel = _channel(cfg)
        root = tmp_path / "runs"
        for params in billing_runlog_factory("scattered")["entries"]:
            append_run(channel, cfg=cfg, root=root, **params)
        target = run_path(root, channel, "2026-09-10")
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["entries"] = payload["entries"][:-1]  # 自造"连续"证据（删掉一条真实运行日）
        target.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RunLogError, match="链校验失败"):
            load_run("2026-09-10", channel_id=channel, root=root)
        with pytest.raises(RunLogError, match="链校验失败"):
            window_coverage(channel, cfg=cfg, root=root, end="2026-09-10")

    def test_改写账单后再对账即拒绝(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        channel = next(iter(BudgetConfig.from_yaml(config).channels))
        source = tmp_path / "bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        assert (
            _cli(
                [
                    "import-bill",
                    "--channel",
                    channel,
                    "--file",
                    str(source),
                    "--bill-id",
                    billing_bill_fixture["bill_id"],
                    "--period",
                    billing_bill_fixture["period"],
                    "--config",
                    str(config),
                ],
                capsys,
            )[0]
            == 0
        )
        path = bill_path(tmp_path / "billing", channel, billing_bill_fixture["bill_id"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"][0]["amount"] = 0.01  # 篡改金额（自造账单）
        path.write_text(json.dumps(payload), encoding="utf-8")
        code, out = _cli(
            [
                "reconcile",
                "--channel",
                channel,
                "--period",
                billing_bill_fixture["period"],
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 1 and "拒绝产出" in out["error"]

    def test_不传守卫的装配与真实装配点对比(self, billing_root, budget_config_factory):
        """**门禁绕过**的机制面：不传 guard 的网关仍会调用（故装配点必须显式传守卫）。"""
        cfg = budget_config_factory()
        loose = LLMGateway(
            _CountingBackend(),
            price_book={"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
            sleep=lambda _: None,
        )
        result = loose.chat("提示词", model="mock-copy-v1", stage="screenplay")
        assert result.cost_usd > 0 and loose.call_count == 1  # 无门禁 ⇒ 无拒绝
        assert loose.spend_guard is None
        # 真实装配点必须传非空守卫（静态断言见 C10）
        assert cfg.channel(_channel(cfg)).adapter
        assert billing_root

    def test_缺档与渠道不符即配置错误(self, tmp_path, capsys):
        config = _write_config(tmp_path, _with_root(_section("movie"), tmp_path))
        channel = next(iter(BudgetConfig.from_yaml(config).channels))
        code, out = _cli(
            [
                "calibrate",
                "--channel",
                channel,
                "--tier",
                "ghost",
                "--measured-usd",
                "1.0",
                "--sample-count",
                "1",
                "--expected-usd",
                "1.0",
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 2 and "ghost" in out["error"]
        code, out = _cli(["tiers", "--channel", "ghost", "--config", str(config)], capsys)
        assert code == 2 and "不一致" in out["error"]
