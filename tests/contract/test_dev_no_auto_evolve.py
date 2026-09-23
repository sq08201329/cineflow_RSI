"""禁止自动进化契约（开发 Agent 侧，功能 017 / T1738；契约 C15 / FR-007 / FR-014）。

**宪章原则六的工程落点**（开发 Agent 是本特性的核心所指）：dreaming 层**禁止**为开发 Agent
生成候选策略——配置化名单 `dreaming.no_auto_evolve_agents`（实值已含 `dev`）+ 显式拒绝异常
（`AutoEvolutionForbiddenError`，**非静默跳过**），且拒绝发生在**候选生成之前**（0 候选
0 计费 0 落盘）；部署门禁对 `dev` 一律返回**禁止名单**判定，且**优先级高于"证据不足"**。

三重保证（可机检，与 009 侧 `test_screenplay_no_auto_evolve.py` 同口径；本特性只补 dev 侧
断言，不新增 plumbing、不另立第二份名单）：

① 配置名单实值断言（`configs/*.yaml` 两形态均含 `dev`，防配置漂移）；
② 拒绝行为断言（`run_dream_round(agent_id="dev")` 立即抛错，generator/gateway 零调用）；
③ 审计断言（人工策略 meta 标记 `no_auto_evolve: true` + 候选生成次数恒 0 + 轮次零落盘）。
"""

import inspect
from pathlib import Path

import pytest
import yaml

import dreaming.config
import dreaming.pipeline
from agents.dev.policy_versions import submit_policy
from core.deployment.gate import gate
from core.deployment.models import GateDecision
from core.replay.pool import SimulatorPool
from dreaming.config import DreamConfig
from dreaming.pipeline import run_dream_round

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATHS = (REPO_ROOT / "configs" / "movie.yaml", REPO_ROOT / "configs" / "shortdrama.yaml")
FORBIDDEN_NAMES = ("screenplay", "dev")  # 默认名单实值（防配置漂移）
DEV = "dev"


class _CountingGenerator:
    """候选生成计数桩：命中拒绝名单时**生成调用次数必须恒 0**。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]:
        self.calls += 1
        return []


def _forbidden_error():
    """拒绝异常类型（模块级导入以免测试文件顶部导入失败时整套契约不可收集）。"""
    from dreaming.pipeline import AutoEvolutionForbiddenError

    return AutoEvolutionForbiddenError


def _run_dev(champion, generator, pool, gateway, config, history_root, m=4):
    return run_dream_round(
        DEV,
        champion,
        generator,
        pool,
        gateway,
        config,
        history_root=history_root,
        m=m,
    )


class Test配置名单实值:
    @pytest.mark.parametrize("config_path", CONFIG_PATHS, ids=lambda path: path.name)
    def test_两形态名单均含_dev(self, config_path):
        """①：开发 Agent 在禁自动进化名单内（防配置漂移，机检实值）。"""
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert raw["dreaming"]["no_auto_evolve_agents"] == list(FORBIDDEN_NAMES)
        assert DEV in DreamConfig.from_yaml(config_path).no_auto_evolve_agents

    def test_名单缺失即报错(self, tmp_path):
        """名单缺失即报错（不允许静默退化为"可以自动进化"）。"""
        raw = yaml.safe_load(CONFIG_PATHS[0].read_text(encoding="utf-8"))
        del raw["dreaming"]["no_auto_evolve_agents"]
        path = tmp_path / "movie.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with pytest.raises(Exception, match="no_auto_evolve_agents"):
            DreamConfig.from_yaml(path)


class Test拒绝语义:
    def test_为_dev_生成候选立即拒绝零候选零计费(
        self, champion_source, tree_store, dream_config, mock_gateway, tmp_path
    ):
        """②：`run_dream_round(agent_id="dev")` → 立即拒绝、未生成候选、未计费、零落盘。"""
        generator = _CountingGenerator()
        with pytest.raises(_forbidden_error()) as exc:
            _run_dev(
                champion_source(),
                generator,
                SimulatorPool(tree_store),
                mock_gateway,
                dream_config,
                tmp_path,
            )
        message = str(exc.value)
        assert DEV in message  # 拒绝原因可读（点名 Agent）
        assert "原则六" in message  # 消息注明宪章原则六（可审计）
        assert generator.calls == 0  # 0 候选生成
        assert mock_gateway.call_count == 0  # 0 LLM 调用
        assert mock_gateway.total_cost_usd == 0.0  # 0 计费
        assert list(tmp_path.rglob("*")) == []  # 0 落盘（无 DreamRound）

    def test_候选生成次数恒_0_审计(self, champion_source, tree_store, dream_config, tmp_path):
        """③：多次请求累计候选生成恒 0、轮次零落盘（审计证据）。"""
        generator = _CountingGenerator()
        for _ in range(3):
            with pytest.raises(_forbidden_error()):
                _run_dev(
                    champion_source(),
                    generator,
                    SimulatorPool(tree_store),
                    None,  # 拒绝发生在网关装配之前（零计费与零调用同源）
                    dream_config,
                    tmp_path,
                )
        assert generator.calls == 0
        assert not (tmp_path / DEV).exists()
        assert list(tmp_path.rglob("*.json")) == []

    def test_拒绝检查先于候选生成(self):
        """源码顺序断言：守卫出现在 `generator.generate(` 调用之前。"""
        body = inspect.getsource(dreaming.pipeline.run_dream_round)
        assert body.index("AutoEvolutionForbiddenError") < body.index("generator.generate(")

    def test_守卫与名单不含_agent_名字面量(self):
        """配置驱动而非硬编码黑名单：dreaming 模块源码不得出现 Agent 名字面量。"""
        for module in (dreaming.pipeline, dreaming.config):
            source = inspect.getsource(module)
            for name in FORBIDDEN_NAMES:
                assert name not in source, f"{module.__name__} 出现 {name!r} 字面量（硬编码嫌疑）"


class Test部署门禁优先级:
    """C15 ②：部署门禁对 `dev` 返回禁止名单，且优先级最高（先于"证据不足"分支）。"""

    def test_禁止名单优先于证据不足(
        self, deployment_config, deployment_evidence_matrix, deployment_drift_registry
    ):
        from core.deployment.evidence import collect_evidence

        kwargs = dict(deployment_evidence_matrix["forbidden_agent"]["kwargs"])
        kwargs["agent_id"] = DEV
        bundle = collect_evidence(cfg=deployment_config, **kwargs)
        assert gate(bundle, deployment_config).decision is GateDecision.FORBIDDEN_AGENT

        # 三要件与前置**全缺**（证据不足形态）：判定仍为禁止名单（优先级最高）
        incomplete = {
            "agent_id": DEV,
            "candidate_version": kwargs["candidate_version"],
            "deployed_version": kwargs["deployed_version"],
            "unbiasedness": None,
            "validation_rewards": None,
            "judge_keys": (),
            "drift_registry": deployment_drift_registry("confirmed_drift"),
        }
        verdict = gate(collect_evidence(cfg=deployment_config, **incomplete), deployment_config)
        assert verdict.decision is GateDecision.FORBIDDEN_AGENT
        assert "禁止自动进化名单" in verdict.reason

    def test_禁止名单判定绝不_eligible(self, deployment_config, deployment_evidence_matrix):
        from core.deployment.evidence import collect_evidence

        kwargs = dict(deployment_evidence_matrix["all_satisfied"]["kwargs"])
        kwargs["agent_id"] = DEV
        verdict = gate(collect_evidence(cfg=deployment_config, **kwargs), deployment_config)
        assert verdict.decision is not GateDecision.ELIGIBLE


class Test人工策略审计位:
    """③：人工策略 meta 留痕 `no_auto_evolve`（策略来源是人的机读依据）。"""

    def test_提交策略_meta_含审计位(self, dev_policy_source, dream_config, tmp_path):
        record = submit_policy(dev_policy_source(), "sunqi", dream_config, history_root=tmp_path)
        meta = record.meta_path.read_text(encoding="utf-8")
        assert '"no_auto_evolve": true' in meta
        assert record.recorded is True

    def test_名单外_agent_审计位为_false(self, dream_config, tmp_path):
        """审计位是**配置驱动**的值（不是无脑 true）：名单为空时如实记 false。"""
        from dataclasses import replace

        source = (
            "class Policy:\n"
            '    """名单外 Agent 的策略（只用于审计位取值面）。"""\n\n'
            "    def plan(self, inputs, config):\n"
            '        return {"entries": [], "production_marks": []}\n'
        )
        config = replace(dream_config, no_auto_evolve_agents=())
        record = submit_policy(source, "sunqi", config, history_root=tmp_path, agent_id="visual")
        assert '"no_auto_evolve": false' in record.meta_path.read_text(encoding="utf-8")
