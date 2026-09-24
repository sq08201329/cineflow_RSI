"""调用点零成本分支（功能 019 / US1）：11 处调用面逐处一例。

契约 C10 的**调用点分支规则**（绑定条款）：`BudgetRefusedError` 必须先于
`except GatewayError` / 更宽的 `except Exception` 处理，走**零成本分支**——

- **被拒的那一笔不入账**（该笔 `llm_calls=0`、`llm_tokens=0`、`generation_api_cost_usd=0.0`）；
- 同一轮里**已发生**的花费**照记**（"花了的钱"与"没花的钱"必须可分辨）；
- 后端 **0 次调用**、网关 `call_count`/`total_cost_usd`/`_breakdown` 不变；
- 失败原因**点名"预算拒绝"**并含剩余/所需额度（三处可辨之一）。

调用点普查（11 处调用面 = 10 处处理器 + 1 处裸上抛，逐处一例）：
① 计费分支 2 处、② 归因分支 3 处、③ 兜底吞没点 5 处、④ 裸上抛 1 处。
守门用的都是**真守卫**（`core/billing` 的 `SpendGuard` + `FileLedger` + `AlertLog`），
不另造分支替身；③ 按 T1950 的口径用最小桩评估器/真判官让拒绝上抛。
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from core.billing.budget import (
    AlertLog,
    BudgetRefusedError,
    FileLedger,
    SpendGuard,
    alerts_path,
    ledger_path,
)
from core.evaluators.base import ArtifactRef
from core.llm_gateway.backends.mock import MockBackend
from core.llm_gateway.gateway import LLMGateway
from core.tree.artifacts import LocalArtifactStore
from core.tree.db import create_schema
from core.tree.models import CostRecord, NodeStatus
from core.tree.store import create_tree_store
from tests.stubs import StubJudgeEvaluator

INPUTS = {"topic": "病房里的三个月", "target_duration_min": 90, "constraints": ["单场景为主"]}
DEV_INPUTS = {"genre_bounds": ["悬疑", "都市"], "audience": "都市女性"}
# 档位额度小到"任何预估额都超"：用于让**所有**调用都被拒绝
REFUSE_AT = 1e-6


class _RefusingEvaluator(StubJudgeEvaluator):
    """最小桩判官：让预算拒绝从 `evaluate` 上抛（T1950 口径的 ③ 断言方式）。"""

    def __init__(self, evaluator_id: str = "judge.refusal-fixture") -> None:
        super().__init__(evaluator_id, score=0.7)

    def evaluate(self, artifact: ArtifactRef, context: dict):
        raise BudgetRefusedError(
            "预算拒绝（over_limit）：判官档余量不足——拒绝调用，成本零入账",
            reason="over_limit",
            tier_id="screenplay_judge",
            remaining_usd=0.0,
            estimated_usd=0.5,
        )


@pytest.fixture()
def refusal_limits(billing_budget_factory):
    """按环节设定额度（默认全部拒绝；指定环节可放行）。"""

    def _make(**overrides):
        tiers = billing_budget_factory(tier_limit_usd=None, window_kind="day")["tiers"]
        for tier_id, tier in tiers.items():
            tier["limit_usd"] = float(overrides.get(tier_id, REFUSE_AT))
        return tiers

    return _make


def _config(budget_config_factory, limits):
    return budget_config_factory(tiers=limits)


def _guard(cfg, root):
    channel = next(iter(cfg.channels))
    return SpendGuard(
        cfg=cfg,
        channel_id=channel,
        ledger=FileLedger(ledger_path(root, channel), timeout_seconds=1.0),
        alerts=AlertLog(alerts_path(root, channel)),
    )


def _gateway(cfg, root, *, backend=None, price_book=None):
    channel = next(iter(cfg.channels))
    return LLMGateway(
        backend or MockBackend(),
        price_book=price_book
        or {"mock-copy-v1": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002}},
        sleep=lambda _: None,
        spend_guard=_guard(cfg, root),
        channel_id=channel,
    )


def _ledger(cfg, root) -> dict:
    channel = next(iter(cfg.channels))
    return FileLedger(ledger_path(root, channel), timeout_seconds=1.0).read()


def _alerts(cfg, root) -> list[dict]:
    channel = next(iter(cfg.channels))
    return AlertLog(alerts_path(root, channel)).entries()


# ---------------------------------------------------------------------------
# 计数桩（后端必须 0 次调用）
# ---------------------------------------------------------------------------


class _CountingBackend:
    def __init__(self, *, text: str = "伪文本") -> None:
        self.call_count = 0
        self._text = text

    def complete(self, prompt, *, model, temperature, max_tokens):
        from core.llm_gateway.gateway import BackendResult

        self.call_count += 1
        return BackendResult(
            text=self._text,
            prompt_tokens=max(1, len(prompt) // 2),
            completion_tokens=min(max_tokens, 32),
        )


class Test之一_计费分支:
    """① 生成计费路径：被拒的那一笔**不得**记成花费（原实现记成 `estimated`）。"""

    def test_剧本生成被拒_该笔全零且运营表可辨(
        self,
        refusal_limits,
        budget_config_factory,
        billing_root,
        tree_store,
        artifact_store,
        screenplay_jobs_engine,
        screenplay_config,
        make_script_artifacts,
    ):
        from agents.screenplay.loop import run_screenplay_round
        from tests.unit.test_llm_price_freeze import _plans, _StubPolicy

        cfg = _config(budget_config_factory, refusal_limits())
        backend = _CountingBackend()
        gateway = _gateway(cfg, billing_root, backend=backend)
        result = run_screenplay_round(
            round_id="refuse-script",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=gateway,
            config=screenplay_config,
            inputs=dict(INPUTS),
        )
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        assert nodes and all(node.status is NodeStatus.FAILED for node in nodes)
        # 被拒的那一笔不入账：节点成本**全零**
        assert all(node.cost == CostRecord() for node in nodes)
        assert all("预算拒绝" in node.observation_context["reject_reason"] for node in nodes)
        # 运营表：预估额在（供运营看"本来需要多少"），实际入账 0
        rows = _screening_rows(screenplay_jobs_engine, "refuse-script")
        assert rows and all(row.actual_cost_usd == 0.0 for row in rows)
        assert all(row.estimated_cost_usd > 0 for row in rows)
        assert all("预算拒绝" in (row.error or "") for row in rows)
        # 后端 0 次调用 + 网关三量不变
        assert backend.call_count == 0
        assert gateway.call_count == 0 and gateway.total_cost_usd == 0.0
        assert gateway.cost_breakdown() == {}
        # 账本计数 + 告警留痕（kind 六值之一；含剩余/所需额度）
        record = _ledger(cfg, billing_root)["tiers"]["screenplay"]
        assert record["refusals"] >= 1 and record["last_refusal"]["reason"] == "over_limit"
        alerts = [a for a in _alerts(cfg, billing_root) if a["kind"] == "budget_refused"]
        assert alerts and alerts[0]["detail"]["remaining_usd"] is not None

    def test_开发生成中途被拒_前序已发生照记_被拒的一笔不入账(
        self,
        refusal_limits,
        billing_budget_factory,
        budget_config_factory,
        billing_root,
        dev_jobs_engine,
        dev_data_dir,
        dev_config,
        dev_policy_source,
    ):
        from agents.dev.loop import run_dev_round
        from tests.unit.test_dev_loop import INPUTS as DEV_IN
        from tests.unit.test_dev_loop import _policy_of

        source = dev_policy_source()

        create_schema(dev_jobs_engine)  # 运营表 + 发现树表同库（dev_round_engine 口径）
        # 两步构造：先用"全拒"读出一条目的**预估价**，再把额度设为 1.5 倍 ⇒ 首条放行、次条被拒
        probe_cfg = _config(budget_config_factory, refusal_limits())
        run_dev_round(
            round_id="refuse-dev-probe",
            policy=_policy_of(source, "unit-v1"),
            store=create_tree_store(dev_jobs_engine),
            artifacts=LocalArtifactStore(dev_data_dir / "artifacts"),
            engine=dev_jobs_engine,
            gateway=_gateway(probe_cfg, billing_root),
            config=dev_config,
            inputs=dict(DEV_IN),
        )
        estimate = _alerts(probe_cfg, billing_root)[0]["detail"]["estimated_usd"]
        cfg = _config(budget_config_factory, refusal_limits(dev=estimate * 1.001))
        backend = _CountingBackend()
        gateway = _gateway(cfg, billing_root, backend=backend, price_book=dev_config.model_prices)
        result = run_dev_round(
            round_id="refuse-dev",
            policy=_policy_of(source, "unit-v1"),
            store=create_tree_store(dev_jobs_engine),
            artifacts=LocalArtifactStore(dev_data_dir / "artifacts"),
            engine=dev_jobs_engine,
            gateway=gateway,
            config=dev_config,
            inputs=dict(DEV_IN),
        )
        node = next(
            n for n in create_tree_store(dev_jobs_engine).nodes_of(result.tree_id) if n.parent_id
        )
        assert node.status is NodeStatus.FAILED
        assert "预算拒绝" in node.observation_context["reject_reason"]
        # 已发生的第 1 条照记（llm_calls=1 且成本 > 0），被拒的第 2 条**未计入**
        assert node.cost.llm_calls == 1
        assert node.cost.generation_api_cost_usd > 0
        assert backend.call_count == 1  # 只有已发生的那一条真调了后端
        assert gateway.call_count == 1
        rows = _dev_rows(dev_jobs_engine, "refuse-dev")
        assert rows and rows[0].actual_cost_usd > 0  # 已发生花费照记
        assert rows[0].actual_cost_usd == node.cost.generation_api_cost_usd
        record = _ledger(cfg, billing_root)["tiers"]["dev"]
        assert record["refusals"] >= 1


class Test之二_归因分支:
    """② 拒绝必须**可辨**：不得误报成厂商失败/后端不可用/笼统 smoke_failed。"""

    def test_冒烟网关拒绝_单列_budget_refused(self, monkeypatch, capsys, tmp_path):
        import ops.smoke_llm as smoke

        config_path = _tiny_tier_config(tmp_path, "movie")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
        backend = _CountingBackend()
        monkeypatch.setattr(
            smoke.HttpBackend, "from_profile", classmethod(lambda cls, *a, **k: backend)
        )
        code = smoke.main(["--config", str(config_path), "--prompt", "最小规模探针"])
        payload = json.loads(capsys.readouterr().out.strip())
        # 拒绝**单列** budget_refused（不与 smoke_failed / unexpected_error 混）
        assert code == smoke.EXIT_FAILED
        assert payload["reason"] == "budget_refused" and "预算拒绝" in payload["error"]
        assert backend.call_count == 0  # 拒绝发生在后端调用**之前**
        # 账本与告警留痕（原因 + 预估价落盘，供运营与后续 calibrate 分辨）
        alerts = _alerts_of(config_path, "llm")
        assert alerts and alerts[0]["kind"] == "budget_refused"

    @pytest.mark.parametrize("module_name", ["ops.screenplay", "ops.dev"])
    def test_CLI_装配期拒绝可辨(
        self,
        module_name,
        monkeypatch,
        capsys,
        tmp_path,
        screenplay_policy_source,
        dev_policy_source,
    ):
        """`--backend http` 路径当前不可用（C10 ③），但分支先就位：拒绝**不**被误报为后端不可用。"""
        import importlib

        module = importlib.import_module(module_name)
        screenplay = module_name.endswith("screenplay")
        source = (
            screenplay_policy_source("compliant") if screenplay else dev_policy_source("compliant")
        )
        policy_file = tmp_path / "policy.py"
        policy_file.write_text(source, encoding="utf-8")

        def _raise(args):
            raise BudgetRefusedError(
                "预算拒绝（over_limit）：预估价 1.0 / 余量 0.0——拒绝调用，成本零入账",
                reason="over_limit",
                remaining_usd=0.0,
                estimated_usd=1.0,
            )

        monkeypatch.setattr(module, "_backend", _raise)
        argv = [
            "produce",
            "--config",
            str(Path("configs/movie.yaml")),
            "--policy-file",
            str(policy_file),
            "--round",
            "r-refuse",
            "--dsn",
            "sqlite://",
            "--data-dir",
            str(tmp_path / "data"),
        ]
        argv += (
            ["--topic", "题材"]
            if screenplay
            else ["--genre-bounds", "悬疑", "--audience", "都市女性"]
        )
        code = module.main(argv)
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 1 and "预算拒绝" in payload["error"]
        assert "后端不可用" not in payload["error"]


class Test之三_兜底吞没点:
    """③ 崩溃隔离分支：原因须点名拒绝（不写成"评估器崩溃"），已发生花费照记。"""

    def test_剧本判官被拒_生成花费照记且原因点名(
        self,
        refusal_limits,
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

        # 只拒判官档（生成档放行）⇒ 生成已发生、判官被拒
        limits = refusal_limits(screenplay=1.0, screenplay_judge=REFUSE_AT)
        from agents.screenplay.evaluators.dramatic_tension import DramaticTensionJudgeEvaluator

        cfg = _config(budget_config_factory, limits)
        backend = _CountingBackend()
        gateway = _gateway(cfg, billing_root, backend=backend)
        real_judge = DramaticTensionJudgeEvaluator(
            gateway,
            model=screenplay_config.judge["model"],
            prompts=list(screenplay_config.judge["prompts"]),
            anchor_outlines=screenplay_config.anchor_outlines,
            max_tokens=screenplay_config.judge["max_tokens"],
        )
        result = run_screenplay_round(
            round_id="refuse-judge",
            policy=_StubPolicy(_plans(make_script_artifacts)),
            store=tree_store,
            artifacts=artifact_store,
            engine=screenplay_jobs_engine,
            gateway=gateway,
            config=screenplay_config,
            inputs=dict(INPUTS),
            evaluators=[*_stubs()[:5], real_judge],
        )
        nodes = [n for n in tree_store.nodes_of(result.tree_id) if n.parent_id is not None]
        failed = [n for n in nodes if n.status is NodeStatus.FAILED]
        assert failed
        assert all("预算拒绝" in n.observation_context["reject_reason"] for n in failed)
        assert not any("评估器崩溃" in n.observation_context["reject_reason"] for n in failed)
        # 已发生的生成花费照记（llm_calls=1，成本 > 0）；被拒的判官一笔不入账
        assert all(n.cost.generation_api_cost_usd > 0 for n in failed)
        assert all(n.cost.llm_calls == 1 for n in failed)
        assert backend.call_count >= 1  # 生成真调了后端；判官一次没调

    def test_分镜判官被拒_隔离分支点名拒绝(
        self,
        refusal_limits,
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
        from tests.unit.test_storyboard_loop import _StubPolicy, _three_shotlists

        limits = refusal_limits(storyboard_judge=REFUSE_AT)
        cfg = _config(budget_config_factory, limits)
        result = run_storyboard_round(
            round_id="refuse-board",
            policy=_StubPolicy(_three_shotlists(make_shotlist)),
            store=tree_store,
            artifacts=artifact_store,
            adapter=SimulatedStoryboardRenderer(),
            engine=storyboard_jobs_engine,
            config=storyboard_config,
            inputs={"script": script_segment},
            evaluators=[_RefusingEvaluator("judge.script_fit")],
            gateway=_gateway(cfg, billing_root),
        )
        assert result.jobs[0]["status"] == "failed"
        assert "预算拒绝" in result.jobs[0]["reason"]
        node = [
            n
            for n in tree_store.nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.FAILED
        ][0]
        assert "预算拒绝" in node.observation_context["reject_reason"]
        assert node.cost.llm_calls == 0  # 判官的那一笔不入账
        assert "评估器崩溃" not in node.observation_context["reject_reason"]

    def test_剪辑判官被拒_隔离分支点名拒绝(
        self,
        refusal_limits,
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

        limits = refusal_limits(editing_judge=REFUSE_AT)
        cfg = _config(budget_config_factory, limits)
        # 目标时长压到 30s（夹具素材 31s 即过时长门禁）——本用例要跑到**判官**那一面
        config = replace(editing_config, target_duration_s=30)
        result = run_editing_round(
            round_id="refuse-edit",
            policy=_StubPolicy(_three_edls(make_edl)),
            store=tree_store,
            artifacts=artifact_store,
            adapter=SimulatedEditRenderer(config.render),
            engine=editing_jobs_engine,
            config=config,
            inputs={
                "shot_library": make_shot_library(),
                "scene_structure": make_scene_structure(),
            },
            evaluators=[*_stubs()[:-1], _RefusingEvaluator("judge.narrative")],
            gateway=_gateway(cfg, billing_root),
        )
        job = result.jobs[0]
        assert job["status"] == "failed" and "预算拒绝" in job["reason"]
        node = [
            n
            for n in tree_store.nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.FAILED
        ][0]
        assert "预算拒绝" in node.observation_context["reject_reason"]
        assert node.cost.llm_calls == 0
        assert "评估器崩溃" not in node.observation_context["reject_reason"]

    def test_视觉判官被拒_生产花费照记且原因点名(
        self,
        refusal_limits,
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

        limits = refusal_limits(visual_judge=REFUSE_AT)
        cfg = _config(budget_config_factory, limits)
        gateway = _gateway(cfg, billing_root)
        result = run_round(
            "refuse-visual",
            StubVisualPolicy([_clip(1)]),
            tree_store,
            LocalArtifactStore(tmp_path / "artifacts"),
            SimulatedVideoGen(visual_config.simulated_gen),
            gateway,
            gen_jobs_engine,
            visual_config,
        )
        assert result.clips[0]["status"] == "failed"
        assert "预算拒绝" in result.clips[0]["reason"]
        node = [
            n
            for n in tree_store.nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.FAILED
        ][0]
        assert "预算拒绝" in node.observation_context["reject_reason"]
        assert "评估器崩溃" not in node.observation_context["reject_reason"]

    def test_宣发生成被拒_该处本就全零且原因点名(
        self,
        refusal_limits,
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

        limits = refusal_limits(promo=REFUSE_AT)
        cfg = _config(budget_config_factory, limits)
        result = run_round(
            "refuse-promo",
            StubPromoPolicy([_brief(), _brief(temperature=0.7)]),
            tree_store,
            LocalArtifactStore(tmp_path / "artifacts"),
            simulated_platform,
            _gateway(cfg, billing_root, price_book=promo_config.model_prices),
            promo_config,
            engine=campaigns_engine,
        )
        assert result.materials[0]["status"] == "failed"
        assert "预算拒绝" in result.materials[0]["reason"]
        node = [
            n
            for n in tree_store.nodes_of(result.tree_id)
            if n.parent_id is not None and n.status is NodeStatus.FAILED
        ][0]
        assert "预算拒绝" in node.observation_context["reject_reason"]
        assert node.cost == CostRecord()  # 被拒的那一笔不入账（该处本就全零）


class Test之四_裸上抛:
    """④ 做梦候选生成：无处理器 ⇒ 拒绝原样上抛（不入账、也不被泛化成功耗归因）。"""

    def test_做梦候选被拒_原样上抛且零入账(
        self, refusal_limits, budget_config_factory, billing_root
    ):
        from dreaming.candidates import LLMGenerator

        cfg = _config(budget_config_factory, refusal_limits())
        backend = _CountingBackend()
        gateway = _gateway(cfg, billing_root, backend=backend)
        generator = LLMGenerator(gateway, model="mock-copy-v1")
        with pytest.raises(BudgetRefusedError) as excinfo:
            generator.generate("class Policy:\n    pass\n", {"rounds": []}, 8)
        assert excinfo.value.reason == "over_limit"
        assert backend.call_count == 0
        assert gateway.call_count == 0 and gateway.total_cost_usd == 0.0
        assert gateway.cost_breakdown() == {}


def _tiny_tier_config(tmp_path, form: str = "movie"):
    """把某形态真实配置的**档位额度压到极小**（账本根落 tmp），用于装配点拒绝用例。"""
    import yaml

    payload = yaml.safe_load((Path("configs") / f"{form}.yaml").read_text(encoding="utf-8"))
    for tier in payload["budget"]["tiers"].values():
        tier["limit_usd"] = REFUSE_AT
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    target = tmp_path / "configs" / f"{form}-tiny-tiers.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _alerts_of(config_path, channel_id: str) -> list[dict]:
    from core.billing.budget import BudgetConfig

    cfg = BudgetConfig.from_yaml(config_path)
    return AlertLog(alerts_path(cfg.ledger_root(), channel_id)).entries()


def _screening_rows(engine, round_id: str):
    from agents.screenplay.db import screenplay_jobs

    with engine.connect() as conn:
        return [
            row for row in conn.execute(select(screenplay_jobs)).all() if row.round_id == round_id
        ]


def _dev_rows(engine, round_id: str):
    from agents.dev.db import dev_jobs

    with engine.connect() as conn:
        return [row for row in conn.execute(select(dev_jobs)).all() if row.round_id == round_id]
