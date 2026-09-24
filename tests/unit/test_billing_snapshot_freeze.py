"""额度与峰谷口径随节点快照冻结（功能 019 / T1929）：契约 C9 / C6 + 原则一。

- **键与形状**：`config_snapshot["budget_tiers"] = {channel_id, adapter, tiers{<环节 id>:
  {limit_usd, window.kind, on_exhausted, note, calibrated_by}}, peak_windows_snapshot
  {timezone, attribution, windows}, ledger_revision, calibration, reconcile, runs}`
  ——由 `SpendGuard.snapshot()` 产出、经 `with_budget_tiers()` 并入
  （**镜像 016 的 `with_llm_profiles` 写法**，不新造机制）；
- **键存在规则**：未接门禁（或空快照）**不落键**——绝不写空/占位快照，既有装配形态逐字不变；
- **冻结语义（原则一）**：改配置额度只影响此后新装配；**历史节点的快照逐字节不变**。

七个 Agent 循环各一例（声音线无 LLM 调用，经 `budget_tiers=` 透传参数并入）。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from core.billing.budget import (
    AlertLog,
    BudgetConfig,
    FileLedger,
    SpendGuard,
    alerts_path,
    gateway_budget_snapshot,
    ledger_path,
    with_budget_tiers,
)
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import LocalArtifactStore
from core.tree.store import create_tree_store

INPUTS = {"topic": "病房里的三个月", "target_duration_min": 90, "constraints": ["单场景为主"]}
DEV_INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}
PRICE_BOOK = {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}}


def _guard(cfg, root):
    channel = next(iter(cfg.channels))
    return (
        SpendGuard(
            cfg=cfg,
            channel_id=channel,
            ledger=FileLedger(ledger_path(root, channel), timeout_seconds=1.0),
            alerts=AlertLog(alerts_path(root, channel)),
        ),
        channel,
    )


def _gateway(cfg, root, *, price_book=None):
    guard, channel = _guard(cfg, root)
    return LLMGateway(
        MockBackend(),
        price_book=price_book or PRICE_BOOK,
        sleep=lambda _: None,
        spend_guard=guard,
        channel_id=channel,
    )


def _tree_of(store, tree_id: str, agent_id: str):
    return next(tree for tree in store.trees_by(agent_id=agent_id) if tree.tree_id == tree_id)


def _assert_frozen(snapshot: dict, cfg: BudgetConfig, channel: str) -> None:
    """快照形状与取值：档位逐档与配置一致 + 峰谷事实随行（C5/C6 口径三处可见之一）。"""
    assert snapshot["channel_id"] == channel
    assert set(snapshot["tiers"]) == set(cfg.tiers)
    for tier_id, tier in cfg.tiers.items():
        frozen = snapshot["tiers"][tier_id]
        assert frozen["limit_usd"] == pytest.approx(tier.limit_usd)  # 值 = 配置声明
        assert frozen["window"] == {"kind": tier.window_kind}
        assert frozen["on_exhausted"] == "refuse"
        assert frozen["note"] == tier.note
    peak = snapshot["peak_windows_snapshot"]
    assert peak["timezone"] == cfg.peak_windows.timezone
    assert peak["attribution"] == "call_start"  # 归属口径（取值域单元素）
    assert peak["windows"] == [window.to_snapshot() for window in cfg.peak_windows.windows]
    assert snapshot["ledger_revision"] >= 0  # 账本 revision（审计指针）
    assert snapshot["adapter"]  # 渠道登记（真实调用面的装配引用）随快照冻结
    assert json.dumps(snapshot, ensure_ascii=False, sort_keys=True)  # 可序列化（进树列）


def _frozen_bytes(snapshot: dict) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


class Test键与形状:
    def test_未接入或空快照不落键(self):
        base = {"evaluator_weights": {"rule.x": "gate"}}
        assert "budget_tiers" not in with_budget_tiers(base, None)  # 未接门禁
        assert "budget_tiers" not in with_budget_tiers(base, {})  # 空映射 = 未接入（不写占位）
        merged = with_budget_tiers(base, {"channel_id": "c"})
        assert merged["budget_tiers"] == {"channel_id": "c"}
        assert merged["evaluator_weights"] == base["evaluator_weights"]  # 其余键逐字保留
        assert "budget_tiers" not in base  # 纯函数：不改原快照

    def test_守卫快照内容齐备(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        guard, channel = _guard(cfg, billing_root)
        _assert_frozen(guard.snapshot(), cfg, channel)

    def test_无门禁时网关不提供档位快照(self, budget_config_factory, billing_root):
        cfg = budget_config_factory()
        plain = LLMGateway(MockBackend(), price_book={}, sleep=lambda _: None)
        assert gateway_budget_snapshot(plain) is None  # 既有装配形态零变化
        guarded = _gateway(cfg, billing_root)
        assert gateway_budget_snapshot(guarded)["tiers"]

    def test_真实配置派生的档位快照与配置逐档一致(self, billing_root, tmp_path):
        """从**真实配置派生**再装配：快照值 = 该配置的声明值（不是码内默认）。"""
        config_path = _config_file_with_limits(tmp_path, 3.5)
        cfg = BudgetConfig.from_yaml(config_path)
        guard, channel = _guard(cfg, billing_root)
        _assert_frozen(guard.snapshot(), cfg, channel)
        assert guard.snapshot()["tiers"]["screenplay"]["limit_usd"] == pytest.approx(3.5)


class Test各循环快照含档位:
    def test_剧本线(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        from agents.screenplay.loop import run_screenplay_round
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy, _stubs

        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        result = run_screenplay_round(
            round_id="freeze-script",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=_gateway(cfg, billing_root, price_book=screenplay_config.model_prices),
            config=screenplay_config,
            inputs=dict(INPUTS),
            evaluators=_stubs(),
        )
        tree = _tree_of(tree_store, result.tree_id, "screenplay")
        _assert_frozen(tree.config_snapshot["budget_tiers"], cfg, next(iter(cfg.channels)))
        assert "llm_profiles" in tree.config_snapshot  # 与 016 的键共存（两层快照并列）

    def test_开发线(
        self,
        budget_config_factory,
        billing_root,
        dev_jobs_engine,
        dev_data_dir,
        dev_config,
        dev_policy_source,
    ):
        from agents.dev.loop import run_dev_round
        from core.tree.db import create_schema
        from tests.unit.test_dev_loop import INPUTS as DEV_IN
        from tests.unit.test_dev_loop import _policy_of

        create_schema(dev_jobs_engine)
        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        store = create_tree_store(dev_jobs_engine)
        result = run_dev_round(
            round_id="freeze-dev",
            policy=_policy_of(dev_policy_source("compliant"), "unit-v1"),
            store=store,
            artifacts=LocalArtifactStore(dev_data_dir / "artifacts"),
            engine=dev_jobs_engine,
            gateway=_gateway(cfg, billing_root, price_book=dev_config.model_prices),
            config=dev_config,
            inputs=dict(DEV_IN),
        )
        _assert_frozen(
            _tree_of(store, result.tree_id, "dev").config_snapshot["budget_tiers"],
            cfg,
            next(iter(cfg.channels)),
        )

    def test_视觉线(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        gen_jobs_engine,
        tmp_path,
        visual_config,
    ):
        from agents.visual.loop import run_round
        from agents.visual.platform.simulated import SimulatedVideoGen
        from tests.unit.test_visual_loop import StubVisualPolicy, _clip

        cfg = budget_config_factory(tier_limit_usd=5.0, window_kind="day")
        result = run_round(
            "freeze-visual",
            StubVisualPolicy([_clip(1)]),
            tree_store,
            LocalArtifactStore(tmp_path / "artifacts"),
            SimulatedVideoGen(visual_config.simulated_gen),
            _gateway(cfg, billing_root),
            gen_jobs_engine,
            visual_config,
        )
        _assert_frozen(
            _tree_of(tree_store, result.tree_id, "visual").config_snapshot["budget_tiers"],
            cfg,
            next(iter(cfg.channels)),
        )

    def test_分镜线(
        self,
        budget_config_factory,
        billing_root,
        make_shotlist,
        script_segment,
        tree_store,
        artifact_store,
        storyboard_jobs_engine,
        storyboard_config,
    ):
        from agents.storyboard.loop import run_storyboard_round
        from agents.storyboard.platform.simulated import SimulatedStoryboardRenderer
        from tests.unit.test_storyboard_loop import _StubPolicy, _stubs, _three_shotlists

        cfg = budget_config_factory(tier_limit_usd=5.0, window_kind="day")
        result = run_storyboard_round(
            round_id="freeze-board",
            policy=_StubPolicy(_three_shotlists(make_shotlist)[:1]),
            store=tree_store,
            artifacts=artifact_store,
            adapter=SimulatedStoryboardRenderer(),
            engine=storyboard_jobs_engine,
            config=storyboard_config,
            inputs={"script": script_segment},
            evaluators=_stubs(),
            gateway=_gateway(cfg, billing_root),
        )
        _assert_frozen(
            _tree_of(tree_store, result.tree_id, "storyboard").config_snapshot["budget_tiers"],
            cfg,
            next(iter(cfg.channels)),
        )

    def test_剪辑线(
        self,
        budget_config_factory,
        billing_root,
        make_shot_library,
        make_scene_structure,
        make_edl,
        tree_store,
        artifact_store,
        editing_jobs_engine,
        editing_config,
    ):
        from agents.editing.loop import run_editing_round
        from agents.editing.platform.simulated import SimulatedEditRenderer
        from tests.unit.test_editing_loop import _StubPolicy, _stubs, _three_edls

        cfg = budget_config_factory(tier_limit_usd=5.0, window_kind="day")
        config = replace(editing_config, target_duration_s=30)
        result = run_editing_round(
            round_id="freeze-edit",
            policy=_StubPolicy(_three_edls(make_edl)[:1]),
            store=tree_store,
            artifacts=artifact_store,
            adapter=SimulatedEditRenderer(config.render),
            engine=editing_jobs_engine,
            config=config,
            inputs={
                "shot_library": make_shot_library(),
                "scene_structure": make_scene_structure(),
            },
            evaluators=_stubs(),
            gateway=_gateway(cfg, billing_root),
        )
        _assert_frozen(
            _tree_of(tree_store, result.tree_id, "editing").config_snapshot["budget_tiers"],
            cfg,
            next(iter(cfg.channels)),
        )

    def test_宣发线(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        campaigns_engine,
        simulated_platform,
        promo_config,
        tmp_path,
    ):
        from agents.promo.loop import run_round
        from tests.unit.test_promo_loop import StubPromoPolicy, _brief

        cfg = budget_config_factory(tier_limit_usd=5.0, window_kind="day")
        result = run_round(
            "freeze-promo",
            StubPromoPolicy([_brief()]),
            tree_store,
            LocalArtifactStore(tmp_path / "artifacts"),
            simulated_platform,
            _gateway(cfg, billing_root, price_book=promo_config.model_prices),
            promo_config,
            engine=campaigns_engine,
        )
        _assert_frozen(
            _tree_of(tree_store, result.tree_id, "promo").config_snapshot["budget_tiers"],
            cfg,
            next(iter(cfg.channels)),
        )

    def test_声音线经透传参数冻结(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        artifact_store,
        sound_jobs_engine,
        sound_config,
        make_timing_sheet,
        make_sound_gen_params,
    ):
        """声音线无 LLM 调用：档位快照由**透传参数**并入。

        调用方从网关取（见 `agents/pilot/stages.py` 的 `gateway_budget_snapshot`）。
        """
        from agents.sound.loop import run_sound_round
        from agents.sound.platform.simulated import (
            SimulatedMusicGen,
            SimulatedSFXGen,
            SimulatedTTSGen,
        )
        from tests.unit.test_sound_loop import _stub_evaluators, _StubPolicy

        cfg = budget_config_factory(tier_limit_usd=5.0, window_kind="day")
        guard, channel = _guard(cfg, billing_root)
        sim = sound_config.simulated_gen
        rate = sound_config.sample_rate
        result = run_sound_round(
            round_id="freeze-sound",
            policy=_StubPolicy(make_sound_gen_params),
            store=tree_store,
            artifacts=artifact_store,
            adapters={
                "tts": SimulatedTTSGen(sim, rate),
                "sfx": SimulatedSFXGen(sim, rate),
                "music": SimulatedMusicGen(sim, rate),
            },
            engine=sound_jobs_engine,
            config=sound_config,
            inputs={"timing_sheet": make_timing_sheet(), "mood": "悬疑"},
            evaluators=_stub_evaluators(),
            budget_tiers=guard.snapshot(),  # 与两处真实装配点同源（SpendGuard.snapshot）
        )
        _assert_frozen(
            _tree_of(tree_store, result.tree_id, "sound").config_snapshot["budget_tiers"],
            cfg,
            channel,
        )


class Test冻结语义:
    def test_未接门禁的循环不落键(
        self,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        from agents.screenplay.loop import run_screenplay_round
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy, _stubs

        gateway = LLMGateway(
            MockBackend(), price_book=screenplay_config.model_prices, sleep=lambda _: None
        )
        result = run_screenplay_round(
            round_id="freeze-noguard",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=gateway,
            config=screenplay_config,
            inputs=dict(INPUTS),
            evaluators=_stubs(),
        )
        tree = _tree_of(tree_store, result.tree_id, "screenplay")
        assert "budget_tiers" not in tree.config_snapshot  # 既有装配形态零变化
        assert "llm_profiles" in tree.config_snapshot  # 016 的键照旧

    def test_改额度后新树用新额度_历史树逐字节不变(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        """原则一：额度随快照冻结——改配置只影响此后新装配；历史节点不被重写。"""
        from agents.screenplay.loop import run_screenplay_round
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy, _stubs

        plans = _plans(make_script_artifacts)

        def _run(round_id: str, limit: float) -> str:
            cfg = budget_config_factory(tier_limit_usd=limit, window_kind="day")
            result = run_screenplay_round(
                round_id=round_id,
                policy=_StubPolicy(plans),
                store=tree_store,
                artifacts=artifact_store,
                engine=screenplay_jobs_engine,
                gateway=_gateway(cfg, billing_root, price_book=screenplay_config.model_prices),
                config=screenplay_config,
                inputs=dict(INPUTS),
                evaluators=_stubs(),
            )
            return result.tree_id

        first_id = _run("freeze-limit-a", 2.0)
        first_tree = _tree_of(tree_store, first_id, "screenplay")
        assert first_tree.config_snapshot["budget_tiers"]["tiers"]["screenplay"][
            "limit_usd"
        ] == pytest.approx(2.0)
        first_frozen = _frozen_bytes(first_tree.config_snapshot["budget_tiers"])
        # 改配置额度（×3.5）后跑新一轮：新装配用新额度
        second = _tree_of(tree_store, _run("freeze-limit-b", 7.0), "screenplay").config_snapshot[
            "budget_tiers"
        ]
        assert second["tiers"]["screenplay"]["limit_usd"] == pytest.approx(7.0)
        # 历史节点的快照**逐字节不变**（冻结、不重写）
        assert (
            _frozen_bytes(
                _tree_of(tree_store, first_id, "screenplay").config_snapshot["budget_tiers"]
            )
            == first_frozen
        )

    def test_峰谷口径随快照冻结_改配置只进新树(
        self,
        budget_config_factory,
        billing_root,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        """C6：时区/归属/峰时区间同样随节点冻结（改配置只影响新装配）。"""
        from agents.screenplay.loop import run_screenplay_round
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy, _stubs

        cfg = budget_config_factory(tier_limit_usd=1.0, window_kind="day")
        result = run_screenplay_round(
            round_id="freeze-peak",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=_gateway(cfg, billing_root, price_book=screenplay_config.model_prices),
            config=screenplay_config,
            inputs=dict(INPUTS),
            evaluators=_stubs(),
        )
        snapshot = _tree_of(tree_store, result.tree_id, "screenplay").config_snapshot[
            "budget_tiers"
        ]
        peak = snapshot["peak_windows_snapshot"]
        assert peak == {
            "timezone": cfg.peak_windows.timezone,
            "attribution": "call_start",
            "windows": [{"start": "08:30", "end": "00:30"}],
        }


def _config_file_with_limits(tmp_path: Path, limit: float) -> Path:
    """真实 movie.yaml 的派生（只改档位额度 + 账本根落 tmp）：供装配面用例复用。"""
    payload = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "configs" / "movie.yaml").read_text(encoding="utf-8")
    )
    for tier in payload["budget"]["tiers"].values():
        tier["limit_usd"] = limit
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    target = tmp_path / "configs" / "movie-frozen.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target
