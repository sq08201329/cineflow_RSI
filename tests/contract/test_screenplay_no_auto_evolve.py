"""C12 禁用自动进化契约（功能 009 / T924，先于实现编写）。

**宪章原则六的工程落点**：dreaming 层**禁止**为剧本 Agent（与开发 Agent）生成候选
策略——配置化名单 `dreaming.no_auto_evolve_agents` + 显式拒绝异常（`AutoEvolutionForbiddenError`，
**非静默跳过**），且拒绝发生在**候选生成之前**（0 候选 0 计费 0 落盘）。

三重保证（可机检）：
① 默认配置实值断言（configs/movie.yaml 含 screenplay 与 dev，防配置漂移）；
② 拒绝行为断言（run_dream_round 立即抛错，generator/gateway 零调用）；
③ 审计断言（候选生成次数恒 0 + 轮次零落盘 + 拒绝检查先于生成调用的源码顺序 +
   守卫与名单**不含 Agent 名字面量**（配置驱动而非硬编码黑名单））。
"""

import inspect
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import dreaming.config
import dreaming.pipeline
from core.replay.pool import SimulatorPool
from dreaming.candidates import MutatorGenerator
from dreaming.config import DreamConfig
from dreaming.pipeline import run_dream_round

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs" / "movie.yaml"
FORBIDDEN_NAMES = ("screenplay", "dev")  # 默认名单实值（防配置漂移）


class _CountingGenerator:
    """候选生成计数桩：命中拒绝名单时**生成调用次数必须恒 0**。"""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, champion_source: str, digest: dict, m: int) -> list[str]:
        self.calls += 1
        return []


@pytest.fixture()
def pool(tree_store):
    """模拟器池（空池合法）：拒绝发生在候选生成前，池不参与本次判定。"""
    return SimulatorPool(tree_store)


@pytest.fixture()
def champion(champion_source):
    return champion_source()


def _run(
    agent_id,
    *,
    champion,
    generator,
    pool,
    gateway,
    config,
    history_root,
    m=4,
):
    return run_dream_round(
        agent_id,
        champion,
        generator,
        pool,
        gateway,
        config,
        history_root=history_root,
        m=m,
    )


def _forbidden_error():
    """拒绝异常类型（模块级导入以免测试文件顶部导入失败时整套契约不可收集）。"""
    from dreaming.pipeline import AutoEvolutionForbiddenError

    return AutoEvolutionForbiddenError


class Test拒绝语义:
    def test_命中名单立即拒绝零候选零计费(
        self, champion, pool, dream_config, mock_gateway, tmp_path
    ):
        """C12 场景 1：run_dream_round(agent_id="screenplay") → 立即拒绝、未生成候选、未计费。"""
        generator = _CountingGenerator()
        with pytest.raises(_forbidden_error()) as exc:
            _run(
                "screenplay",
                champion=champion,
                generator=generator,
                pool=pool,
                gateway=mock_gateway,
                config=dream_config,
                history_root=tmp_path,
            )
        message = str(exc.value)
        assert "screenplay" in message  # 拒绝原因可读（点名 Agent）
        assert "原则六" in message  # 消息注明宪章原则六（可审计）
        assert generator.calls == 0  # 0 候选生成
        assert mock_gateway.call_count == 0  # 0 LLM 调用
        assert mock_gateway.total_cost_usd == 0.0  # 0 计费
        assert list(tmp_path.rglob("*")) == []  # 0 落盘（无 DreamRound）

    @pytest.mark.parametrize("agent_id", FORBIDDEN_NAMES)
    def test_默认名单内_agent_全部拒绝(
        self, agent_id, champion, pool, dream_config, mock_gateway, tmp_path
    ):
        generator = _CountingGenerator()
        with pytest.raises(_forbidden_error()):
            _run(
                agent_id,
                champion=champion,
                generator=generator,
                pool=pool,
                gateway=mock_gateway,
                config=dream_config,
                history_root=tmp_path,
            )
        assert generator.calls == 0


class Test默认配置防漂移:
    def test_movie_yaml_名单实值(self):
        """C12 场景 2：默认配置含 screenplay 与 dev（防配置漂移，机检实值）。"""
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        assert raw["dreaming"]["no_auto_evolve_agents"] == list(FORBIDDEN_NAMES)
        config = DreamConfig.from_yaml(CONFIG_PATH)
        assert tuple(config.no_auto_evolve_agents) == FORBIDDEN_NAMES

    def test_缺名单配置即报错(self, tmp_path):
        """名单缺失即报错（默认值断言兜底：不允许静默退化为"全都可以自动进化"）。"""
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        del raw["dreaming"]["no_auto_evolve_agents"]
        path = tmp_path / "movie.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with pytest.raises(Exception, match="no_auto_evolve_agents"):
            DreamConfig.from_yaml(path)

    def test_名单项非法即报错(self, dream_config, tmp_path):
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        raw["dreaming"]["no_auto_evolve_agents"] = ["screenplay", ""]
        path = tmp_path / "movie.yaml"
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with pytest.raises(Exception, match="no_auto_evolve_agents"):
            DreamConfig.from_yaml(path)


class Test其他Agent回归:
    """C12 场景 3：名单外 Agent 不受影响（拒绝语义不误伤）。"""

    def test_visual_正常跑一轮(self, champion, multi_tree_pool, dream_config, tmp_path):
        trees, store = multi_tree_pool(2)
        pool = SimulatorPool(store)
        for tree in trees:
            pool.add_tree(tree)
        result = _run(
            "visual",
            champion=champion,
            generator=MutatorGenerator("dream-visual-1"),
            pool=pool,
            gateway=None,  # MutatorGenerator 不用 LLM
            config=dream_config,
            history_root=tmp_path,
            m=2,
        )
        assert len(result.candidates) == 2
        assert result.agent_id == "visual"
        assert (tmp_path / "visual").is_dir()  # 正常落盘（未被拒绝语义影响）

    def test_名单可配置_非硬编码(self, champion, multi_tree_pool, dream_config, tmp_path):
        """守卫是**配置驱动**：名单为空时 screenplay 正常跑一轮（非硬编码黑名单）。"""
        trees, store = multi_tree_pool(2)
        pool = SimulatorPool(store)
        for tree in trees:
            pool.add_tree(tree)
        config = replace(dream_config, no_auto_evolve_agents=())
        result = _run(
            "screenplay",
            champion=champion,
            generator=MutatorGenerator("dream-screenplay-1"),
            pool=pool,
            gateway=None,
            config=config,
            history_root=tmp_path,
            m=2,
        )
        assert result.agent_id == "screenplay"
        assert len(result.candidates) == 2


class Test审计断言:
    def test_为_screenplay_生成候选次数恒_0(
        self, champion, pool, dream_config, mock_gateway, tmp_path
    ):
        """多次请求累计：候选生成调用恒 0、轮次零落盘（审计证据）。"""
        generator = _CountingGenerator()
        for _ in range(3):
            with pytest.raises(_forbidden_error()):
                _run(
                    "screenplay",
                    champion=champion,
                    generator=generator,
                    pool=pool,
                    gateway=mock_gateway,
                    config=dream_config,
                    history_root=tmp_path,
                )
        assert generator.calls == 0
        assert not (tmp_path / "screenplay").exists()  # 0 轮次落盘
        assert mock_gateway.call_count == 0

    def test_拒绝检查先于候选生成(self):
        """源码顺序断言：守卫出现在 `generator.generate(` 调用之前（候选生成前拒绝）。"""
        body = inspect.getsource(dreaming.pipeline.run_dream_round)
        assert body.index("AutoEvolutionForbiddenError") < body.index("generator.generate(")

    def test_守卫与名单不含_agent_名字面量(self):
        """配置驱动而非硬编码黑名单：dreaming 模块源码不得出现 Agent 名字面量。"""
        for module in (dreaming.pipeline, dreaming.config):
            source = inspect.getsource(module)
            for name in FORBIDDEN_NAMES:
                assert name not in source, f"{module.__name__} 出现 {name!r} 字面量（硬编码嫌疑）"
