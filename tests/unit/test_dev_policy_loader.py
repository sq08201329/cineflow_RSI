"""功能 018 阶段 2（契约 C2 / 宪章原则四例外的**三项替代约束**）：链首策略装载下沉。

`dev` 阶段只在链首**装载人工策略并调 017 既有轮次入口**（零新增执行路径），例外条款的
三项替代约束必须在该下沉路径上落**机检**：

① **装载即静态检查**：未过 `policies/static_check.check_policy_source` ⇒ 拒绝装载
   （0 网关、0 落树、不入策略历史）；
② **策略执行超时**：静态检查不禁循环 ⇒ 注入死循环策略必须**判失败而非挂死**；
③ **策略零环境对象 / 不触网关**：策略只被喂 `plan(inputs, config)`——不交付
   `observed()`/`probe()`/对象存储/账本/网关句柄（真值探测由宿主代执行），策略本体不持
   对象存储凭证、不经网关、不触生成（网关调用由宿主 `run_dev_round` 发出并受 019 门禁约束）。

另有两条口径断言：④ 版本取形态配置部署指针 `deployment.dev.current_policy_version`
（缺指针/源码不存在 ⇒ 启动前拒绝，不回落"最新/第一条"）；⑤ `ops/dev.py` 的装载与
`agents/dev/policy_loader.py` 是**同一实现**（`ops/` 侧为薄调用，不得留第二份）。
"""

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from agents.dev import policy_loader
from agents.dev.policy_loader import (
    LoadedPolicy,
    PolicyLoadError,
    deployed_policy_version,
    load_deployed_policy,
    load_policy_text,
)
from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
# 仓库引导树人工策略（谱系根，部署指针在形态配置 `deployment.dev.current_policy_version`）
BOOTSTRAP_VERSION = "34525518074d"
# 形态 id 面（021 T2146）：由 `configs/*.yaml` 的 `form:` 派生 ⇒ 新增形态自动进入遍历面
FORMS = declared_forms(REPO_ROOT / "configs")

# 合规策略（只做结构计算：静态检查白名单内，无环境对象）
_VALID_SOURCE = '''\
"""极简合规策略（测试夹具）。"""


class Policy:
    policy_version = "inline"

    def plan(self, inputs, config):
        return {
            "entries": [
                {
                    "direction_id": "dir-a",
                    "genre": inputs["genre_bounds"][0],
                    "constraints": ["受众：" + inputs["audience"]],
                    "characters": ["主角 甲"],
                }
            ],
            "production_marks": ["dir-a"],
        }
'''

# 死循环策略（plan 内循环）：静态检查不禁循环 ⇒ 靠**执行超时**截断
_DEAD_LOOP_IN_PLAN = '''\
"""死循环策略（测试夹具）：plan 内无限循环。"""


class Policy:
    policy_version = "inline"

    def plan(self, inputs, config):
        while True:
            pass
'''

# 死循环策略（模块体，装载期即循环）
_DEAD_LOOP_AT_LOAD = '''\
"""死循环策略（测试夹具）：装载期无限循环。"""

while True:
    pass


class Policy:
    def plan(self, inputs, config):
        return {}
'''

# 危险源码：静态检查拒绝（白名单外 import）
_FORBIDDEN_SOURCE = '''\
"""违规策略：白名单外 import。"""

import socket


class Policy:
    def plan(self, inputs, config):
        return {}
'''


class Test装载即静态检查:
    def test_合规策略可装载且版本等于源码哈希(self):
        loaded = load_policy_text(_VALID_SOURCE)
        assert isinstance(loaded, LoadedPolicy)
        assert loaded.policy.policy_version == loaded.version
        assert (
            load_policy_text(_VALID_SOURCE, declared_version=loaded.version).version
            == loaded.version
        )

    def test_未过静态检查即拒绝(self):
        # 未过静态检查 ⇒ 拒绝装载（0 网关 0 落树 0 策略历史；本路径不产生可回放版本）
        with pytest.raises(PolicyLoadError, match="静态检查"):
            load_policy_text(_FORBIDDEN_SOURCE, origin="<probe>")

    def test_版本不符即拒绝(self):
        with pytest.raises(PolicyLoadError, match="版本"):
            load_policy_text(_VALID_SOURCE, declared_version="0" * 12)

    def test_缺_policy_类即拒绝(self):
        with pytest.raises(PolicyLoadError, match="Policy"):
            load_policy_text('"""无 Policy 类。"""\n\nX = 1\n')

    def test_源码语法错误即拒绝(self):
        with pytest.raises(PolicyLoadError):
            load_policy_text("class Policy(:\n")


class Test策略执行超时:
    """② 静态检查不禁循环 ⇒ 装载与执行都必须带超时上限（判失败而非挂死）。"""

    def test_装载期死循环判失败(self):
        import time

        started = time.monotonic()
        with pytest.raises(PolicyLoadError, match="超时"):
            load_policy_text(_DEAD_LOOP_AT_LOAD, timeout_seconds=0.2)
        assert time.monotonic() - started < 5.0  # 不挂死

    def test_执行期死循环判失败(self):
        import time

        loaded = load_policy_text(_DEAD_LOOP_IN_PLAN, timeout_seconds=0.2)
        started = time.monotonic()
        with pytest.raises(PolicyLoadError, match="超时"):
            loaded.plan({"genre_bounds": ["悬疑"], "audience": "都市女性"}, object())
        assert time.monotonic() - started < 5.0

    def test_超时口径与核心件同源(self):
        """超时上限的默认值取核心件（`core.degraded.compare`），不留第二份口径。"""
        from core.degraded.compare import POLICY_EXECUTION_TIMEOUT_SECONDS

        assert policy_loader.DEFAULT_TIMEOUT_SECONDS == POLICY_EXECUTION_TIMEOUT_SECONDS


class Test策略零环境对象:
    """③ 策略只被喂 `plan(inputs, config)`：不交付任何环境对象，策略本体不触网关。"""

    def test_装载函数不接收任何环境句柄(self):
        for name in ("load_policy_text", "load_policy", "load_deployed_policy"):
            parameters = set(inspect.signature(getattr(policy_loader, name)).parameters)
            assert (
                parameters
                & {
                    "store",
                    "artifacts",
                    "engine",
                    "gateway",
                    "ledger",
                    "adapter",
                    "platform",
                }
                == set()
            ), f"{name} 不得接收环境句柄：{sorted(parameters)}"

    def test_装载产物不带环境通道(self):
        from core.degraded.compare import environment_leaks

        loaded = load_policy_text(_VALID_SOURCE)
        assert environment_leaks(loaded.policy, loaded) == []

    def test_只传计划输入与形态配置(self):
        """策略拿到的是**纯输入 + 形态配置**两个实参（按实际收到的形参断言）。"""
        source = '''\
"""记录形参的策略（测试夹具）。"""


class Policy:
    def plan(self, inputs, config):
        return {"inputs": dict(inputs), "config": config}
'''
        loaded = load_policy_text(source)
        result = loaded.plan({"genre_bounds": ["悬疑"]}, "都市女性")
        assert result == {"inputs": {"genre_bounds": ["悬疑"]}, "config": "都市女性"}

    def test_装载模块不构造网关不落树(self):
        source = inspect.getsource(policy_loader)
        for banned in (
            "LLMGateway(",
            "Artifacts(",
            "LocalArtifactStore(",
            "append_node(",
            "create_tree(",
        ):
            assert banned not in source, banned

    def test_绝不替策略做真值探测(self):
        """策略侧无 `observed()`/`probe()` 交付面：宿主代执行真值探测（本断言针对策略侧）。"""
        source = inspect.getsource(policy_loader)
        for banned in ("observed(", "probe("):
            assert banned not in source, banned


class Test部署指针与单一实现:
    def test_版本取形态配置部署指针(self, pilot_form_config_path):
        for form in FORMS:
            config_path = pilot_form_config_path(form)
            assert deployed_policy_version(config_path) == BOOTSTRAP_VERSION
            loaded = load_deployed_policy(config_path)
            assert loaded.version == BOOTSTRAP_VERSION
            assert loaded.policy.policy_version == BOOTSTRAP_VERSION

    def test_缺指针即拒绝不回落最新(self, pilot_form_config_path, tmp_path):
        source = pilot_form_config_path("shortdrama").read_text(encoding="utf-8")
        payload = yaml.safe_load(source)
        payload["deployment"].pop("dev")
        broken = tmp_path / "no-pointer.yaml"
        broken.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8")
        with pytest.raises(PolicyLoadError, match="指针"):
            deployed_policy_version(broken)
        with pytest.raises(PolicyLoadError):
            load_deployed_policy(broken)

    def test_源码不存在即拒绝(self, tmp_path):
        with pytest.raises(PolicyLoadError, match="不存在"):
            load_deployed_policy(
                REPO_ROOT / "configs" / "shortdrama.yaml", history_root=tmp_path / "empty"
            )

    def test_ops_侧装载与业务侧同一实现(self):
        """`ops/dev.py` 的 `_load_policy` 只做定位与薄调用，不得留第二份装载实现。"""
        source = (REPO_ROOT / "ops" / "dev.py").read_text(encoding="utf-8")
        assert "exec(" not in source  # 装载下沉后 ops/ 不再自己 exec 源码
        assert "check_policy_source" not in source  # 静态检查只在 agents/dev/ 侧一份
        tree = ast.parse(source, filename="ops/dev.py")
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert "load_policy_text" in called
