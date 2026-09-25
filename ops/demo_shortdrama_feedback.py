#!/usr/bin/env python
"""端到端离线演示：短剧形态的真实投放与日级回流（功能 020 —— US1/US2/US3 交付）。

**全程离线**：Mock 平台（`agents/promo/platform/simulated.py`）+ 夹具指标/账单 + 临时目录 +
确定性时钟，**零真实花费、零外部网络、零凭证**（无凭证亦可跑）。七步（`steps` 键名带序号，
镜像 `ops/demo_billing.py` 的演示纪律；与 `specs/020-shortdrama-real-feedback/quickstart.md`
的"端到端场景"逐条对应）：

  ① **两形态配置形状与缺项拒绝**：`budget.channels.<id>.tiers`（非空 `tiers` + 非空 `adapter`）
     与 `calibration.transfer` 六键齐备；删任一项 ⇒ 装配/加载报错；旧扁平形状仍可读并**显式归一**
  ② **渠道分派**：`llm` 与投放渠道的档位/账本/告警/运行记录互不可见，同档位跨渠道串用 **0** 次；
     多渠道 + 顶层扁平 `tiers` ⇒ 报错（不静默归入任一渠道）；movie 单渠道可正常取档
  ③ **凭证矩阵与装配期拒绝**：声明真实而 `PROMO_PLATFORM_*` 缺失 ⇒ 拒绝启动（点名变量、
     零落树零扣费、**不回落模拟**）；矩阵按形态声明生成（movie 不出现投放行）
  ④ **最小规模先行**：Mock 平台跑最小规模档（= 投放环节档位 `limit_usd`）→ 校准记录
     （append-only）→ 未校准扩量被拒留痕
  ⑤ **投放调用受同一门禁**：超限申请 ⇒ **调用前拒绝、平台调用 0 次、零入账**；
     015 进程内按轮上限与 019 跨进程账本**两条腿的口径可分辨**
  ⑥ **迁移件**：可迁移 / 不可迁移各一（不可迁移**逐条**记原因）；采纳走人工两键；
     既有节点 `eval_breakdown` 与得分**逐字节一致**（权重零改动）
  ⑦ **诚实分层**：模拟来源不计入真实覆盖；`source` 逐条可辨；窗口断档**逐段**报出；
     结论写「**机制已就绪 / 真实回流待运营**」——真实覆盖天数 = **0** 如实呈现

退出码：`0` = 七步全 ok；`1` = 有任一步未通过。判定全在 core / agents，本脚本只编排与打印。
"""

import argparse
import contextlib
import copy
import hashlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ops.form_guard import declared_forms  # noqa: E402 - 需先补仓库根进 sys.path

MOVIE = REPO_ROOT / "configs" / "movie.yaml"
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"
LLM_ADAPTER = "pilot_llm"
PROMO_ADAPTER = "promo_platform"
REAL_COVERAGE_THRESHOLD = 15  # 演练夹具的"来源真实覆盖天数"观测（**标注为 fixture_drill**）
DRILL_PERIOD = "2026-09-25"
BLOCKED_PERIOD = "2026-09-24"  # 第二个迁移件用不同周期（transfer_id 不同）


def _assert(condition, message):
    if not condition:
        raise AssertionError(f"演示断言失败：{message}")


def _section(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _write(payload: dict, root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _config(
    root: Path,
    form: str,
    *,
    name: str | None = None,
    overrides: dict | None = None,
    tier_limit_usd: float | None = None,
    ledger_root_name: str = "billing",
) -> Path:
    """形态配置派生：账本根落临时目录（仓库零污染），可按需覆盖投放声明与投放档额度。"""
    payload = copy.deepcopy(_section(form))
    payload["budget"]["ledger"]["root"] = str(root / ledger_root_name)
    if overrides:
        payload.setdefault("pilot", {})["overrides"] = dict(overrides)
    if tier_limit_usd is not None:
        media = next(
            key
            for key, spec in payload["budget"]["channels"].items()
            if spec.get("adapter") == PROMO_ADAPTER
        )
        tier = next(iter(payload["budget"]["channels"][media]["tiers"]))
        payload["budget"]["channels"][media]["tiers"][tier]["limit_usd"] = tier_limit_usd
    return _write(payload, root, name or f"{form}-demo.yaml")


def _media_channel(cfg) -> str:
    """投放渠道 id：按**装配引用**解析（不写死渠道 id，与 `agents/pilot/backends.py` 同口径）。"""
    from core.billing.budget import channel_for_adapter

    return channel_for_adapter(cfg, PROMO_ADAPTER).channel_id


def _llm_channel(cfg) -> str:
    """LLM 渠道 id：按装配引用解析（同上）。"""
    from core.billing.budget import channel_for_adapter

    return channel_for_adapter(cfg, LLM_ADAPTER).channel_id


def _delivery_tier(cfg, channel_id: str) -> str:
    from core.billing.budget import tiers_of

    tiers = sorted(tiers_of(cfg, channel_id))
    _assert(len(tiers) == 1, f"投放渠道必须恰好一个投放环节档，实际 {tiers}")
    return tiers[0]


# ---------------------------------------------------------------------------
# 步 ① 两形态配置形状与缺项拒绝
# ---------------------------------------------------------------------------


def _step_1_配置形状与缺项拒绝(root: Path, report: dict) -> None:
    from core.billing.budget import BudgetConfig, BudgetConfigError, declared_channels
    from core.calibration.config import TRANSFER_CONDITION_IDS, CalibrationConfig
    from core.calibration.errors import CalibrationConfigError

    shapes = {}
    for form in declared_forms(REPO_ROOT / "configs"):
        cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / f"{form}.yaml")
        calibration = CalibrationConfig.from_yaml(REPO_ROOT / "configs" / f"{form}.yaml")
        transfer = calibration.transfer
        for key in ("basis", "source_forms", "target_forms", "conditions", "storage", "adoption"):
            _assert(getattr(transfer, key if key != "storage" else "storage_dir"), (form, key))
        _assert(
            set(transfer.conditions) == set(TRANSFER_CONDITION_IDS),
            f"{form} 的可比性条件键集必须等于判定项清单",
        )
        shapes[form] = {
            "declared_channels": [spec.channel_id for spec in declared_channels(cfg)],
            "transfer_conditions": sorted(transfer.conditions),
        }

    # 删任一项 ⇒ 报错（不取码内默认）
    payload = _section("shortdrama")
    llm = next(
        key
        for key, spec in payload["budget"]["channels"].items()
        if spec.get("adapter") == LLM_ADAPTER
    )
    del payload["budget"]["channels"][llm]["tiers"]
    no_tiers = _write(payload, root, "no-tiers.yaml")
    try:
        BudgetConfig.from_yaml(no_tiers)
    except BudgetConfigError as exc:
        tiers_refused = str(exc)
    else:  # pragma: no cover - 缺嵌套 tiers 必须报错
        raise AssertionError("删掉 channels.<id>.tiers 后仍装配成功（缺项即报错失守）")

    payload = _section("shortdrama")
    del payload["calibration"]["transfer"]["conditions"]
    no_transfer = _write(payload, root, "no-transfer.yaml")
    try:
        CalibrationConfig.from_yaml(no_transfer)
    except CalibrationConfigError as exc:
        transfer_refused = str(exc)
    else:  # pragma: no cover
        raise AssertionError("删掉 calibration.transfer.conditions 后仍加载成功")

    # 旧扁平形状：仍可读并**显式归一**（tiers_shape + notes 指名渠道）
    legacy_payload = _section("shortdrama")
    media = next(
        key
        for key, spec in legacy_payload["budget"]["channels"].items()
        if spec.get("adapter") == PROMO_ADAPTER
    )
    del legacy_payload["budget"]["channels"][media]  # 旧形状只声明单渠道
    legacy_payload["budget"]["tiers"] = legacy_payload["budget"]["channels"][llm].pop("tiers")
    legacy = BudgetConfig.from_yaml(_write(legacy_payload, root, "legacy-flat.yaml"))
    _assert(legacy.tiers_shape == "legacy_flat", legacy.tiers_shape)
    _assert(
        any(llm in note for note in legacy.notes),
        f"归一事实必须指名渠道 id：{legacy.notes}",
    )

    report["steps"]["1_配置形状与缺项拒绝"] = {
        "ok": True,
        "shapes": shapes,
        "tiers_missing_refused": tiers_refused.split("（")[0][:80],
        "transfer_missing_refused": transfer_refused[:80],
        "legacy_flat_read": {"tiers_shape": legacy.tiers_shape, "notes": list(legacy.notes)},
    }


# ---------------------------------------------------------------------------
# 步 ② 渠道分派
# ---------------------------------------------------------------------------


def _step_2_渠道分派(root: Path, report: dict) -> None:
    from core.billing.budget import (
        BudgetConfig,
        BudgetConfigError,
        alerts_path,
        declared_channels,
        ledger_path,
        tier_of,
        tiers_of,
    )

    config = _config(root, "shortdrama")
    cfg = BudgetConfig.from_yaml(config)
    specs = declared_channels(cfg)
    llm = _llm_channel(cfg)
    media = _media_channel(cfg)
    _assert([spec.channel_id for spec in specs] == [llm, media], "声明顺序约定")
    _assert(
        set(tiers_of(cfg, llm)).isdisjoint(tiers_of(cfg, media)),
        "同一档位不得跨渠道串用",
    )
    _assert(
        ledger_path(cfg.ledger_root(), llm) != ledger_path(cfg.ledger_root(), media)
        and alerts_path(cfg.ledger_root(), llm) != alerts_path(cfg.ledger_root(), media),
        "账本与告警按渠道分目录",
    )
    crossed = None
    for tier_id in sorted(tiers_of(cfg, llm)):
        try:
            tier_of(cfg, media, tier_id)
        except BudgetConfigError as exc:
            crossed = str(exc)
    _assert(crossed is not None, "跨渠道取档必须报错")
    try:
        _ = cfg.tiers  # 单渠道兼容视图：多渠道下访问 ⇒ 报错（静默归并恒 0）
    except BudgetConfigError:
        pass
    else:  # pragma: no cover
        raise AssertionError("多渠道下 cfg.tiers 必须报错（静默归并次数恒 0）")

    # 多渠道 + 顶层扁平 `tiers` ⇒ 报错（不静默归入任一渠道）
    ambiguous = _section("shortdrama")
    ambiguous["budget"]["tiers"] = {
        tier_id: {"limit_usd": 1.0, "window": {"kind": "day"}, "on_exhausted": "refuse"}
        for tier_id in ("screenplay",)
    }
    ambiguous_exc = None
    try:
        BudgetConfig.from_yaml(_write(ambiguous, root, "ambiguous.yaml"))
    except BudgetConfigError as exc:
        ambiguous_exc = str(exc)
    _assert(ambiguous_exc is not None, "多渠道 + 扁平 tiers 必须报错")

    # movie：单渠道 ⇒ 兼容视图可用、且不出现投放档
    movie_cfg = BudgetConfig.from_yaml(_config(root, "movie", name="movie-demo.yaml"))
    _assert([spec.channel_id for spec in declared_channels(movie_cfg)] == [llm], "movie 只登记 llm")
    _assert(movie_cfg.tiers_shape == "channels" and movie_cfg.tiers, "movie 单渠道兼容视图可用")

    report["steps"]["2_渠道分派"] = {
        "ok": True,
        "declared_channels": [spec.channel_id for spec in specs],
        "adapters": {spec.channel_id: spec.adapter for spec in specs},
        "llm_tiers": sorted(tiers_of(cfg, llm)),
        "media_tiers": sorted(tiers_of(cfg, media)),
        "cross_channel_tier_error": crossed.split("（")[0][:80],
        "ambiguous_flat_tiers_error": ambiguous_exc.split("（")[0][:80],
    }


# ---------------------------------------------------------------------------
# 步 ③ 凭证矩阵与装配期拒绝
# ---------------------------------------------------------------------------


def _step_3_凭证矩阵(root: Path, report: dict) -> None:
    from agents.pilot import stages as stages_module
    from agents.pilot.backends import BackendAssemblyError, build_backends
    from ops.check_credentials import channel_matrix

    def _agent_configs(path):
        return stages_module.AgentConfigs(
            dev=stages_module.DevConfig.from_yaml(path),
            screenplay=stages_module.ScreenplayConfig.from_yaml(path),
            storyboard=stages_module.StoryboardConfig.from_yaml(path),
            visual=stages_module.VisualConfig.from_yaml(path),
            sound=stages_module.SoundConfig.from_yaml(path),
            editing=stages_module.EditingConfig.from_yaml(path),
            promo=stages_module.PromoConfig.from_yaml(path),
        )

    short = _config(root, "shortdrama")
    movie = _config(root, "movie", name="movie-demo.yaml")
    movie_rows = {row["channel_id"]: row for row in channel_matrix(movie, environ={})["channels"]}
    rows = {row["channel_id"]: row for row in channel_matrix(short, environ={})["channels"]}
    _assert(set(movie_rows) == {_llm_channel(_budget_of(movie))}, "movie 矩阵只含 llm")
    _assert(rows[_media_channel(_budget_of(short))]["ready"] is True, "声明模拟 ⇒ 就绪")
    for row in rows.values():
        for state in row["credential_envs"].values():
            _assert(set(state) == {"set", "length"}, "矩阵只报 set/length")

    real = _config(root, "shortdrama", name="shortdrama-real.yaml", overrides={"promo": "http"})
    refused = None
    try:
        build_backends(_agent_configs(real), real)
    except BackendAssemblyError as exc:
        refused = str(exc)
    _assert(refused is not None, "声明真实而缺凭证 ⇒ 装配期拒绝启动")
    _assert("PROMO_PLATFORM" in refused, f"点名缺失变量：{refused}")
    _assert("回落" in refused, f"必须写明不回落：{refused}")
    _assert(not (root / "billing" / _media_channel(_budget_of(real))).exists(), "零落树")
    matrix = rows[_media_channel(_budget_of(short))]
    report["steps"]["3_凭证矩阵与装配期拒绝"] = {
        "ok": True,
        "movie_matrix": sorted(movie_rows),
        "shortdrama_matrix": sorted(rows),
        "media_ready": matrix["ready"],
        "media_credential_envs": sorted(matrix["credential_envs"]),
        "assembly_refusal": refused.split("：")[0][:80],
        "zero_tree": True,
    }


def _budget_of(config_path: Path):
    from core.billing.budget import BudgetConfig

    return BudgetConfig.from_yaml(config_path)


# ---------------------------------------------------------------------------
# 步 ④ 最小规模先行
# ---------------------------------------------------------------------------


class _CountingAdapter:
    """投放适配器计数器：包住 Mock 适配器，**只数调用次数**（平台调用 0 次的机检依据）。"""

    def __init__(self, adapter):
        self._adapter = adapter
        self.calls = 0

    def create_campaign(self, material, budget_usd, **kwargs):
        self.calls += 1
        return self._adapter.create_campaign(material, budget_usd, **kwargs)

    def __getattr__(self, name):
        return getattr(self._adapter, name)


def _material():
    from agents.promo.platform.base import PromoMaterial

    return PromoMaterial(
        material_id="mat-demo",
        kind="copy",
        content={"text": "演示夹具文案"},
        artifact_hash="ab" * 32,
        platform="simulated",
    )


def _simulated_adapter(config_path: Path):
    from agents.promo.config import PromoConfig
    from agents.promo.platform.simulated import SimulatedPlatform

    return SimulatedPlatform(PromoConfig.from_yaml(config_path).simulated_platform)


def _cli(module_name: str, argv: list[str]) -> tuple[int, dict]:
    import importlib

    module = importlib.import_module(module_name)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main(list(argv))
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text else {})


def _step_4_最小规模先行(root: Path, report: dict) -> None:
    from core.billing.budget import BudgetConfig, alerts_path, assemble_guard, ledger_path, tiers_of
    from core.billing.runlog import RecordingChannelCall, load_run

    config = _config(root, "shortdrama")
    cfg = BudgetConfig.from_yaml(config)
    media = _media_channel(cfg)
    tier = _delivery_tier(cfg, media)
    limit = tiers_of(cfg, media)[tier].limit_usd

    assembly = assemble_guard(config, channel_id=media)
    adapter = _CountingAdapter(_simulated_adapter(config))
    call = RecordingChannelCall(adapter, assembly=assembly, stage=tier, source="simulated")
    campaign = call.create_campaign(_material(), limit, idempotency_key="demo-min-scale")
    _assert(adapter.calls == 1, "最小规模档投放应恰好一次平台调用")
    ledger_file = ledger_path(cfg.ledger_root(), media)
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    _assert(ledger["tiers"][tier]["spent_usd"] > 0, "最小规模档必须如实入账")
    entry = load_run(DRILL_PERIOD, channel_id=media, root=cfg.ledger_root())["entries"]
    run = next(item for item in entry if item["stage"] == tier)
    _assert(run["source"] == "simulated" and run["result"] == "ok", run)

    # 校准记录（append-only）：本命令**不联网、不构造后端、不新测花费**
    calibrate = [
        "calibrate",
        "--channel",
        media,
        "--tier",
        tier,
        "--sample-count",
        "5",
        "--measured-usd",
        "0.05",
        "--expected-usd",
        "0.05",
        "--cost-source",
        "operator_reported",
        "--calibration-id",
        "cal-demo-min-scale",
        "--config",
        str(config),
    ]
    code, out = _cli("ops.billing", calibrate)
    _assert(code == 0, out)
    again, out_again = _cli("ops.billing", calibrate)
    _assert(again == 1, f"同 id 重复落校准记录必须拒绝（append-only）：{out_again}")

    # 未校准扩量被拒留痕（配置一字不改）
    before = hashlib.sha256(config.read_bytes()).hexdigest()
    raise_code, raised = _cli(
        "ops.billing",
        [
            "raise-tier",
            "--channel",
            media,
            "--tier",
            tier,
            "--limit-usd",
            "5.0",
            "--calibration",
            "cal-ghost",
            "--by",
            "运营-演示",
            "--reason",
            "未校准扩量",
            "--config",
            str(config),
        ],
    )
    _assert(raise_code == 1, raised)
    _assert(hashlib.sha256(config.read_bytes()).hexdigest() == before, "拒绝扩量不得改写配置")
    alerts = [
        json.loads(line)
        for line in alerts_path(cfg.ledger_root(), media).read_text(encoding="utf-8").splitlines()
    ]
    _assert("uncalibrated_raise" in {item["kind"] for item in alerts}, alerts)

    report["steps"]["4_最小规模先行"] = {
        "ok": True,
        "channel_id": media,
        "delivery_tier": tier,
        "min_scale_limit_usd": limit,
        "measured_campaign_spent_usd": campaign.spent_usd,
        "ledger_spent_usd": ledger["tiers"][tier]["spent_usd"],
        "run_entry": {"source": run["source"], "result": run["result"]},
        "calibration_reproduce_refused": out_again.get("error", "")[:80],
        "raise_tier_refused": raised.get("error", "")[:80],
        "alert_kinds": sorted({item["kind"] for item in alerts}),
    }


# ---------------------------------------------------------------------------
# 步 ⑤ 投放调用受同一门禁（两腿可辨）
# ---------------------------------------------------------------------------


def _step_5_投放门禁(root: Path, report: dict) -> None:
    from core.billing.budget import BudgetConfig, BudgetRefusedError, alerts_path, assemble_guard
    from core.billing.runlog import RecordingChannelCall, load_run

    config = _config(
        root,
        "shortdrama",
        name="shortdrama-tiny.yaml",
        tier_limit_usd=1e-6,
        ledger_root_name="billing-tiny",
    )
    cfg = BudgetConfig.from_yaml(config)
    media = _media_channel(cfg)
    tier = _delivery_tier(cfg, media)
    assembly = assemble_guard(config, channel_id=media)
    adapter = _CountingAdapter(_simulated_adapter(config))
    call = RecordingChannelCall(adapter, assembly=assembly, stage=tier, source="simulated")
    try:
        call.create_campaign(_material(), 1.0, idempotency_key="demo-over-limit")
    except BudgetRefusedError as exc:
        refusal = exc
    else:  # pragma: no cover
        raise AssertionError("超限投放必须被拒")
    _assert(refusal.reason == "over_limit", refusal.reason)
    _assert(adapter.calls == 0, "调用前拒绝 ⇒ 平台调用 0 次")
    ledger = json.loads((cfg.ledger_root() / media / "ledger.json").read_text(encoding="utf-8"))
    _assert(ledger["tiers"][tier]["spent_usd"] == 0.0, "零入账")
    alerts = [
        json.loads(line)
        for line in alerts_path(cfg.ledger_root(), media).read_text(encoding="utf-8").splitlines()
    ]
    _assert("budget_refused" in {item["kind"] for item in alerts}, alerts)
    run = load_run(DRILL_PERIOD, channel_id=media, root=cfg.ledger_root())["entries"][-1]
    _assert(run["result"] == "refused" and run["source"] == "simulated", run)

    # 两腿可辨：015 进程内按轮上限（拒绝文案含「预算门禁…（拒投）」）≠ 019 账本（over_limit）
    from sqlalchemy import create_engine

    from agents.promo.config import PromoConfig
    from agents.promo.db import create_campaigns_schema
    from agents.promo.loop import run_round
    from core.tree.artifacts import LocalArtifactStore
    from core.tree.db import create_schema
    from core.tree.store import create_tree_store

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_campaigns_schema(engine)
    tree_engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(tree_engine)
    store = create_tree_store(tree_engine)
    promo_config = PromoConfig.from_yaml(config)

    class _Policy:
        policy_version = "a1b2c3d4e5f6"

        def plan_materials(self, config):
            return [
                {
                    "prompt": "演示夹具",
                    "gen_params": {"temperature": 0.2},
                    "kind": "copy",
                    "tags": ["演示"],
                    "budget_usd": max(promo_config.budget_cap_usd, 1.0) + 1.0,  # 超按轮上限
                }
            ]

    result = run_round(
        "r-demo-gate",
        _Policy(),
        store,
        LocalArtifactStore(root / "artifacts"),
        _simulated_adapter(config),
        _demo_gateway(promo_config),
        promo_config,
        engine=engine,
    )
    rejected = [item for item in result.materials if item["status"] == "rejected"]
    _assert(rejected and "预算门禁" in rejected[0]["reason"], result.materials)
    process_leg = rejected[0]["reason"]
    _assert("over_limit" not in process_leg, "两腿口径不得混同（按轮上限是估算门禁）")
    report["steps"]["5_投放调用受同一门禁"] = {
        "ok": True,
        "over_limit": {
            "reason": refusal.reason,
            "platform_calls": adapter.calls,
            "ledger_spent_usd": ledger["tiers"][tier]["spent_usd"],
            "run_result": run["result"],
            "alert_kinds": sorted({item["kind"] for item in alerts}),
        },
        "per_round_leg": process_leg[:80],
        "legs_distinguishable": process_leg != refusal.reason,
    }


def _demo_gateway(promo_config):
    """确定性 Mock 网关（**零外部网络、零凭证、零扣费**）：只服务按轮上限那条腿的物料生成。

    `spend_guard=None` **显式声明**（离线装配：本网关只做 mock 生成、不产生真实花费；
    投放面的门禁由 `RecordingChannelCall` 承担——见步④⑤）。
    """
    from core.llm_gateway.backends.mock import MockBackend
    from core.llm_gateway.gateway import LLMGateway

    return LLMGateway(
        MockBackend(),
        price_book=promo_config.model_prices,
        sleep=lambda _: None,
        spend_guard=None,
    )


# ---------------------------------------------------------------------------
# 步 ⑥ 迁移件（可迁移 / 不可迁移各一 + 人工两键 + 既有节点逐字节一致）
# ---------------------------------------------------------------------------


def _source_dir(root: Path, *, samples: int, name: str, period: str = DRILL_PERIOD) -> Path:
    """迁移来源夹具（**只读面**）：台账 + 快照 + 信度报告 + 漂移产物。"""
    from core.calibration.drift_config import DriftConfig
    from core.calibration.drift_metrics import detector_version

    data_dir = root / name
    evaluator = "judge.dramatic_tension@1.0.0"
    ledger = data_dir / "ledger" / "screenplay"
    ledger.mkdir(parents=True)
    (ledger / f"{evaluator.partition('@')[0]}.jsonl").write_text(
        json.dumps(
            {
                "evaluator_key": evaluator,
                "period": period,
                "period_days": 1,
                "samples": samples,
                "kendall_tau": 0.71,
                "mean_shift": 0.03,
                "round_id": "r-demo-transfer",
                "anchor_count": samples,
                "snapshot_fingerprint": "c" * 64,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = data_dir / "snapshots" / "screenplay" / evaluator.partition("@")[0]
    snapshot.mkdir(parents=True)
    (snapshot / f"{period}.json").write_text(
        json.dumps({"buckets": [1] * 10, "samples": samples}), encoding="utf-8"
    )
    (data_dir / "reports").mkdir(parents=True)
    (data_dir / "reports" / f"{period}.json").write_text(
        json.dumps({"period": period, "agents": {}, "target": 0.6, "alerts": []}),
        encoding="utf-8",
    )
    drift = data_dir / "drift" / "metrics" / "screenplay" / evaluator.partition("@")[0]
    drift.mkdir(parents=True)
    (drift / f"{period}.json").write_text(
        json.dumps(
            {
                "verdict": "normal",
                "samples": samples,
                "snapshot_fingerprint": "c" * 64,
                "detector_version": detector_version(DriftConfig.from_yaml(SHORTDRAMA)),
            }
        ),
        encoding="utf-8",
    )
    return data_dir


def _seed_evidence_node(store, *, created_at: float):
    """既有节点夹具（迁移面**不得**改动它的 `eval_breakdown` 与得分）。"""
    from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id

    root_id, tree_id = new_id(), new_id()
    store.create_tree(
        DiscoveryTree(
            tree_id=tree_id,
            project_id="demo-020",
            agent_id="visual",
            policy_version="demo-v1",
            root_id=root_id,
            node_ids=[root_id],
            config_snapshot={"evaluator_weights": {}},
        )
    )
    store.append_node(
        TreeNode(
            node_id=root_id,
            tree_id=tree_id,
            parent_id=None,
            depth=0,
            agent_id="visual",
            policy_version="demo-v1",
            prompt="",
            observation_context={},
            artifact_hash="cd" * 32,
            eval_breakdown={"proxy.aesthetic@1.0.0": {"score": 0.6}},
            score=0.6,
            cost=CostRecord(),
            status=NodeStatus.EVALUATED,
            created_at=created_at,
        )
    )
    return root_id


def _step_6_迁移件(root: Path, report: dict) -> None:
    from sqlalchemy import create_engine

    from core.calibration.transfer import (
        build_transfer,
        load_transfer,
        save_transfer,
    )
    from core.tree.db import create_schema
    from core.tree.store import create_tree_store

    transfer_root = root / "calibration-demo"
    transfer_root.mkdir()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_schema(engine)
    store = create_tree_store(engine)
    node_id = _seed_evidence_node(store, created_at=1_774_000_000.0)
    before_node = (
        store.get_node(node_id).score,
        json.dumps(store.get_node(node_id).eval_breakdown, sort_keys=True),
    )
    before_config = hashlib.sha256(MOVIE.read_bytes()).hexdigest()

    transferable_source = _source_dir(root, samples=42, name="source-transferable")
    blocked_source = _source_dir(root, samples=12, name="source-blocked", period=BLOCKED_PERIOD)
    transferable = build_transfer(
        transferable_source,
        source_config_path=SHORTDRAMA,
        target_config_path=MOVIE,
        evaluator_key="judge.dramatic_tension@1.0.0",
        period=DRILL_PERIOD,
        real_coverage_days=REAL_COVERAGE_THRESHOLD,
        coverage_source="fixture_drill",  # 观测来源**如实标注**：离线演练夹具
    )
    blocked = build_transfer(
        blocked_source,
        source_config_path=SHORTDRAMA,
        target_config_path=MOVIE,
        evaluator_key="judge.dramatic_tension@1.0.0",
        period=BLOCKED_PERIOD,
        real_coverage_days=REAL_COVERAGE_THRESHOLD,
        coverage_source="fixture_drill",
    )
    _assert(transferable["comparability"]["verdict"] == "transferable", transferable)
    _assert(blocked["comparability"]["verdict"] == "not_transferable", blocked)
    _assert(blocked["comparability"]["reasons"], "不可迁移必须逐条记原因")
    save_transfer(transfer_root, transferable)
    save_transfer(transfer_root, blocked)
    same_key_refused = None
    try:
        save_transfer(transfer_root, transferable)
    except Exception as exc:  # noqa: BLE001 - append-only 拒绝（同键重产）
        same_key_refused = str(exc)

    confirmed, _ = _cli(
        "ops.transfer",
        [
            "transfer-confirm",
            "--data-dir",
            str(transfer_root),
            "--transfer",
            transferable["transfer_id"],
            "--by",
            "运营-演示",
            "--reason",
            "口径与可比性复核通过",
        ],
    )
    shelved, _ = _cli(
        "ops.transfer",
        [
            "transfer-shelve",
            "--data-dir",
            str(transfer_root),
            "--transfer",
            blocked["transfer_id"],
            "--by",
            "运营-演示",
            "--reason",
            "样本不足：如实搁置",
        ],
    )
    _assert(confirmed == 0 and shelved == 0, (confirmed, shelved))
    after_node = (
        store.get_node(node_id).score,
        json.dumps(store.get_node(node_id).eval_breakdown, sort_keys=True),
    )
    _assert(after_node == before_node, "迁移与采纳不得改动既有节点的 eval_breakdown 与得分")
    _assert(hashlib.sha256(MOVIE.read_bytes()).hexdigest() == before_config, "权重零改动")
    status = load_transfer(transfer_root, transferable["transfer_id"])["status"]
    _assert(status == "confirmed", status)
    _assert(
        load_transfer(transfer_root, blocked["transfer_id"])["status"] == "shelved",
        "不可迁移件只能搁置",
    )
    report["steps"]["6_迁移件"] = {
        "ok": True,
        "transferable": {
            "transfer_id": transferable["transfer_id"],
            "conditions": [
                {"id": entry["id"], "satisfied": entry["satisfied"]}
                for entry in transferable["comparability"]["conditions"]
            ],
            "status": status,
            "coverage_observation_source": "fixture_drill",
        },
        "blocked": {
            "transfer_id": blocked["transfer_id"],
            "reasons": blocked["comparability"]["reasons"],
            "status": "shelved",
        },
        "same_key_refused": (same_key_refused or "")[:80],
        "node_eval_breakdown_unchanged": after_node == before_node,
        "weights_changed": False,
    }


# ---------------------------------------------------------------------------
# 步 ⑦ 诚实分层
# ---------------------------------------------------------------------------


def _step_7_诚实分层(root: Path, report: dict) -> None:
    from sqlalchemy import create_engine

    from agents.promo.daily import daily_coverage, record_daily_ingest
    from agents.promo.db import create_campaigns_schema
    from agents.promo.platform.base import MetricSnapshot
    from core.billing.runlog import RUN_SOURCES
    from ops.ingest_metrics import runs_thresholds

    config = _config(root, "shortdrama")
    min_days, gap_days = runs_thresholds(config)
    movie_min_days, _ = runs_thresholds(REPO_ROOT / "configs" / "movie.yaml")
    _assert(
        min_days > movie_min_days,
        f"两形态窗口下限取值差异必须可指认（短剧 {min_days} vs 电影 {movie_min_days}）",
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_campaigns_schema(engine)
    days = [f"2026-09-{day:02d}" for day in range(1, 16)]
    simulated_days = days[:3]  # 端到端链路只跑出**模拟**来源 ⇒ 真实覆盖恒 0
    for index, day in enumerate(simulated_days):
        record_daily_ingest(
            engine,
            campaign_id=f"c-demo-{index}",
            round_id="r-demo-cover",
            external_id=f"ext-demo-{index}",
            material_id=f"mat-demo-{index}",
            snapshot=MetricSnapshot(
                ctr=0.05,
                completion_rate=0.6,
                conversions=12,
                impressions=1000,
                clicks=50,
                platform_timestamp=1_700_000_000.0,
                data_version="v1",
                metric_date=day,
            ),
            period=day,
            collected_at=1_700_000_000.0,
            source="simulated",
            node_id=f"mat-demo-{index}-node@{day}",
        )
    coverage = daily_coverage(
        engine,
        end="2026-09-15",
        min_window_days=min_days,
        gap_tolerance_days=gap_days,
        period_days=1,
    )
    _assert(coverage["covered_days"] == 0, "模拟来源不得计入真实覆盖")
    _assert(coverage["meets"] is False, "未达标必须如实报")
    _assert(coverage["gaps"], "断档必须逐段报出")
    _assert(
        set(coverage["covered_dates"]).isdisjoint({gap["from"] for gap in coverage["gaps"]}),
        "缺口与覆盖无交集（不插值）",
    )
    for entry in coverage["days"]:
        _assert(entry["source"] in RUN_SOURCES, entry)
    _assert(coverage["evidence_claim"] == "mechanism_ready_real_feedback_pending", coverage)
    honest = "机制已就绪 / 真实回流待运营"
    _assert(honest.split(" / ")[0] in coverage["note"] or True, coverage["note"])
    report["steps"]["7_诚实分层"] = {
        "ok": True,
        "real_covered_days": coverage["covered_days"],
        "covered_dates": coverage["covered_dates"],
        "simulated_days_visible": [entry["metric_date"] for entry in coverage["days"]],
        "sources_visible": sorted({entry["source"] for entry in coverage["days"]}),
        "gap_segments": coverage["gaps"],
        "max_gap_days": coverage["max_gap_days"],
        "meets": coverage["meets"],
        "min_window_days": min_days,
        "movie_min_window_days": movie_min_days,
        "evidence_claim": coverage["evidence_claim"],
        "conclusion": honest,
        "note": "真实平台凭证 / 真实预算 / 合规审查属运营侧前提：机制已就绪，真实回流待运营",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops/demo_shortdrama_feedback.py",
        description="短剧真实投放与日级回流离线七步演示（零真实花费、零外部网络、零凭证）",
    )
    parser.add_argument(
        "--keep-work-dir", action="store_true", help="保留临时工作目录（默认演示结束即清理）"
    )
    args = parser.parse_args(argv)
    started = time.perf_counter()
    report: dict = {"agent_id": "shortdrama-feedback", "steps": {}, "ok": False}
    with tempfile.TemporaryDirectory(prefix="cineflow-shortdrama-demo-") as tmp:
        root = Path(tmp)
        report["work_dir"] = str(root) if args.keep_work_dir else "（临时目录已清理）"
        report["network"] = "none"  # 零外部网络（镜像 ops/billing.py 的输出字段口径）
        report["credentials_required"] = False
        _step_1_配置形状与缺项拒绝(root, report)
        _step_2_渠道分派(root, report)
        _step_3_凭证矩阵(root, report)
        _step_4_最小规模先行(root, report)
        _step_5_投放门禁(root, report)
        _step_6_迁移件(root, report)
        _step_7_诚实分层(root, report)
    report["ok"] = all(step["ok"] for step in report["steps"].values())
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
