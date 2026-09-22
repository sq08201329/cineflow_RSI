"""凭证环境变量名**机检锁**（清单 ⇄ 适配器代码 双向比对）。

背景：`docs/pilot-upgrade-manifest.json` 曾登记 `CINEFLOW_VIDEO_*` / `CINEFLOW_AUDIO_*` /
`CINEFLOW_PROMO_*` 等**代码里根本不存在**的环境变量名——纸面清单与真实读取点漂移，
运维按清单注入凭证会静默无效（`HttpBackend` 仍报缺凭证）。本文件把"清单是权威来源"
改为"**代码是权威来源**"：从各适配器实现类**程序化提取**它真正读取的环境变量名
（运行期记录 + AST 兜底），与该路径清单登记的名字**逐项比对**，不一致即红。

双向性：
- 清单少登记一个 → 集合差（代码侧多出）→ 红；
- 清单写错名字 → 集合差（两侧各多一个）→ 红；
- 零凭证路径（A）的适配器若偷偷读环境变量 → 兜底分支显式拒绝 → 红。

可证伪（"改坏即红"）：把清单里任一名字改一个字母，本文件立刻失败——
见 tests/unit/test_credential_env_lock.py 汇报中的实证记录。
"""

import ast
import importlib
import inspect
import json
import os
import re
from pathlib import Path
from unittest import mock

import pytest

from ops import check_credentials

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"

# 凭证名以**代码为权威**：平台侧命名不受本仓前缀约束（OPENAI_* / VISUAL_GEN_* …），
# 只要求符合通用环境变量命名规范（大写字母开头，字母/数字/下划线）。
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class _RecordingEnviron(dict):
    """记录型环境变量表：只记录**读过哪些键**，一律不提供值（读到空串/None）。

    适配器 `from_env()` 在无值时会按设计报错（`UnavailableError` / `GatewayError`），
    这正是我们要的"读键即记录、报错即忽略"的探针状态。
    """

    def __init__(self) -> None:
        super().__init__()
        self.keys_read: set[str] = set()

    def get(self, key, default=None):  # noqa: ANN001, ANN201 - 与 os.environ.get 同签名
        self.keys_read.add(key)
        return default

    def __getitem__(self, key):
        self.keys_read.add(key)
        return ""

    def __contains__(self, key):
        self.keys_read.add(key)
        return False


def _swallow(call) -> None:  # noqa: ANN001
    """调用并吞掉"缺凭证即报错"的预期异常：本函数只关心读了哪些键。"""
    try:
        call()
    except Exception:  # noqa: BLE001 - 缺凭证报错是预期路径（键已记录）
        pass


def _zero_arg_constructible(cls) -> bool:  # noqa: ANN001
    """能否零参构造（如 `HttpBackend()` / `MockBackend()`：构造器不需要业务实参）。"""
    for param in inspect.signature(cls).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is inspect.Parameter.empty:
            return False
    return True


def _module_reads_env(module) -> bool:  # noqa: ANN001
    """模块源码是否出现环境变量读取（兜底分支的"零凭证适配器不得读环境"断言）。"""
    source = inspect.getsource(module)
    return "os.environ" in source or "getenv" in source


def env_names_read_by(dotted: str) -> tuple[str, ...]:
    """程序化提取适配器实现类**真正读取**的环境变量名（不手抄、不猜）。

    优先级：`from_env()`（真实装配入口）→ 零参构造（`HttpBackend` / `MockBackend`）
    → AST 兜底（构造需要业务实参的适配器，如模拟生成器：要求它完全不读环境变量）。
    """
    module_name, class_name = dotted.rsplit(".", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    recorder = _RecordingEnviron()
    with mock.patch.object(os, "environ", recorder):
        factory = getattr(cls, "from_env", None)
        if factory is not None:
            _swallow(factory)
        elif _zero_arg_constructible(cls):
            _swallow(cls)
        else:
            # 无法运行核查：该实现类必须零环境依赖（模拟器/模拟平台即此档）
            assert not _module_reads_env(module), (
                f"{dotted} 读环境变量但无法运行期核查（构造需要实参）：请补 from_env() 或零参构造"
            )
            return _ast_env_literals(module)
    return tuple(sorted(recorder.keys_read))


def _is_environ_node(node: ast.AST) -> bool:
    """`os.environ` 字面（Attribute 链尾为 environ）。"""
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _str_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ast_env_literals(module) -> tuple[str, ...]:  # noqa: ANN001
    """AST 扫描模块内**环境变量**读取的字面量键（只认 os.environ / os.getenv，不误伤 dict.get）。"""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        # os.environ.get("X") / os.environ.setdefault("X", ...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if node.func.attr in {"get", "setdefault"} and _is_environ_node(owner) and node.args:
                literal = _str_literal(node.args[0])
                if literal is not None:
                    names.add(literal)
            # os.getenv("X")
            if node.func.attr == "getenv" and isinstance(owner, ast.Name) and node.args:
                literal = _str_literal(node.args[0])
                if literal is not None:
                    names.add(literal)
        # os.environ["X"]
        if isinstance(node, ast.Subscript) and _is_environ_node(node.value):
            literal = _str_literal(node.slice)
            if literal is not None:
                names.add(literal)
    return tuple(sorted(names))


@pytest.mark.parametrize("path_id", ["A", "B", "C"])
def test_清单登记的凭证名与适配器代码逐个一致(path_id):
    """核心机检锁：某路径的凭证名集合 == 该路径全部适配器实现类读过或声明的 env 名集合。"""
    path = next(p for p in _manifest()["paths"] if p["path_id"] == path_id)
    from_code: set[str] = set()
    for dotted in path["adapters"]:
        from_code |= set(env_names_read_by(dotted))
    assert from_code == set(path["credential_envs"]), (
        f"{path_id} 路径凭证名与代码不一致：\n"
        f"  代码真实读取：{sorted(from_code)}\n"
        f"  清单登记：{sorted(path['credential_envs'])}"
    )


def test_A_路径适配器零环境依赖():
    """A 路径是零凭证基线：其适配器实现类不得读取任何环境变量。"""
    path = next(p for p in _manifest()["paths"] if p["path_id"] == "A")
    assert path["credential_envs"] == []
    for dotted in path["adapters"]:
        assert env_names_read_by(dotted) == (), f"A 路径 {dotted} 不应读环境变量"


def test_清单凭证名符合通用环境变量规范():
    for path in _manifest()["paths"]:
        for name in path["credential_envs"]:
            assert ENV_NAME_PATTERN.match(name), name


def test_每个凭证名都有成对的_BASE_URL_与_API_KEY():
    """三组凭证一律成对（缺一半不可用）：机检成对性，防止登记单边半组。"""
    for path in _manifest()["paths"]:
        names = set(path["credential_envs"])
        prefixes = {n[: -len("_BASE_URL")] for n in names if n.endswith("_BASE_URL")}
        keys = {n[: -len("_API_KEY")] for n in names if n.endswith("_API_KEY")}
        assert prefixes == keys, f"{path['path_id']} 凭证不成对：base={prefixes} key={keys}"


def test_核查器的探测提示覆盖清单全部凭证名():
    """`ops/check_credentials.py` 的探测/验证提示表必须覆盖清单里的每一个凭证前缀。"""
    for path in _manifest()["paths"]:
        for name in path["credential_envs"]:
            prefix = check_credentials.prefix_of(name)
            assert prefix in check_credentials.ADAPTER_HINTS, f"{name} 缺探测/验证提示"
            assert prefix in check_credentials.PROBE_PATHS, f"{name} 缺探测路径"


def test_核查器的提示适配器真的读该前缀的凭证():
    """反向校验：提示里指的适配器类必须真的读这组环境变量（提示不得是手抄幻觉）。"""
    for prefix, hint in check_credentials.ADAPTER_HINTS.items():
        read = set(env_names_read_by(hint.adapter))
        assert read == {f"{prefix}_BASE_URL", f"{prefix}_API_KEY"}, (
            f"{prefix} 的提示适配器 {hint.adapter} 实际读取 {sorted(read)}"
        )


def test_探测一律为只读_GET_且不指向生成端点():
    """纪律机检：探测路径必须是只读端点（绝不 POST 生成端点：零计费红线）。"""
    for prefix, probe_path in check_credentials.PROBE_PATHS.items():
        assert probe_path.startswith("/"), prefix
        assert "chat/completions" not in probe_path
        assert "generat" not in probe_path
        assert "render" not in probe_path
