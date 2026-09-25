"""静态断言：`core/degraded/` 与业务无关（功能 017 / T1715；宪章原则五）。

**为什么**：`core/degraded/` 承接的是"降级模式纪律"本身（人工策略版本化、回放对比、
采纳留痕、判据材料），服务多个降级 Agent。一旦里面出现环节名、形态字面量或厂商字面量，
"同一套机制、多形态复用"即失效，且第二个 Agent 接入时会诱发按名分支（`agent_id == …`）。
本文件用**文本 + AST 双层机检**把这个性质钉死：

1. 形态字面量（`shortdrama` / `"movie"`）与形态判断分支（`form ==` 等）；
2. 厂商字面量（配置里声明的档案 id / 端点 host）与厂商词（deepseek / openai / qwen）；
3. **环节名与业务词**（`screenplay`/`storyboard`/`visual`/`sound`/`editing`/`promo`/`dev`
   与剧本线、选题线、宣发线的业务词）——先例 `tests/unit/test_orchestration_executor.py`
   的 `Test静态断言`（那一处只扫 `core/orchestration/*.py` 且不递归，本处补上 `core/degraded/`）；
4. **零反向依赖**：不得 `import agents.*`，也不得 `import dreaming.*`（原则五单向依赖：
   `agents → core`、`dreaming → core`）；
5. 零 `agent_id == …` 分支：`agent_id` 只作参数值。
"""

import ast
import pathlib
import re

import yaml

from ops.form_guard import form_branch_patterns, form_literals, violations_in

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "core" / "degraded"

# 形态值字面量（同 tests/unit/test_form_switch.py 的 BANNED_LITERALS 口径）：**唯一**来源是
# `ops/form_guard.py` 的派生面（形态名由 `configs/*.yaml` 的 `form:` + `form_aliases` 派生）
# ⇒ 零人工常量、副本数恒 1、新增形态自动纳入；判定（词边界/子串）与例外（E1/E2/E3）都在守卫内
FORM_LITERALS = form_literals(REPO_ROOT / "configs")
FORM_PATTERNS = form_branch_patterns()
# 环节名（英）+ 业务词（中）：降级机制件不得出现任何一条线的词汇
AGENT_NAMES = (
    "screenplay",
    "storyboard",
    "visual",
    "sound",
    "editing",
    "promo",
    "pilot",
    "dev",
)
BUSINESS_WORDS = (
    "剧本",
    "分镜",
    "镜头",
    "节拍",
    "剪辑",
    "宣发",
    "字幕",
    "选题",
    "票房",
    "舆情",
    "立项",
    "短剧",
    "漫剧",
)
AGENT_NAME_PATTERN = re.compile(r"\b(" + "|".join(AGENT_NAMES) + r")\b")
# 只捉**条件分支**（`if/elif/while` 里判断 agent_id）：成员测试（名单审计）不算分支
AGENT_ID_BRANCH_PATTERN = re.compile(
    r"\b(?:if|elif|while)\b[^\n]*agent_id\s*(?:==|!=|\bin\b|\bis\b)"
)
FORBIDDEN_IMPORT_ROOTS = ("agents", "dreaming")
VENDOR_WORDS = ("deepseek", "openai", "qwen")


def _sources() -> list[pathlib.Path]:
    files = sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)
    assert files, "未找到 core/degraded 源码（包落点变了？）"
    return files


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _declared_literals() -> list[str]:
    """两套形态配置里声明的档案 id 与端点 host（core 里出现即意味着路由写死）。"""
    literals: set[str] = set()
    for name in ("movie", "shortdrama"):
        payload = yaml.safe_load((REPO_ROOT / "configs" / f"{name}.yaml").read_text("utf-8"))
        for profile_id, profile in payload["llm"]["profiles"].items():
            literals.add(str(profile_id))
            if profile.get("base_url"):
                host = str(profile["base_url"]).rstrip("/")
                literals.add(host)
                literals.add(re.search(r"https?://([^/]+)", host).group(1))
    return sorted(literals)


class Test源码扫描集:
    def test_包内源码被扫描(self):
        assert {path.name for path in _sources()} >= {
            "__init__.py",
            "adoption.py",
            "compare.py",
            "evidence.py",
            "policy.py",
        }


class Test零形态与厂商字面量:
    def test_无形态字面量与形态分支(self):
        for path in _sources():
            source = path.read_text(encoding="utf-8")
            for banned in FORM_LITERALS:
                assert not violations_in(source, banned, path=_rel(path)), (
                    f"{_rel(path)} 不得出现形态字面量：{banned}"
                )
            for pattern in FORM_PATTERNS:
                assert pattern not in source, f"{_rel(path)} 不得出现形态判断：{pattern}"

    def test_无配置声明的厂商字面量(self):
        literals = _declared_literals()
        assert "deepseek-flash" in literals  # 扫描面有效（配置里确有该档案）
        offenders = [
            f"{_rel(path)}:{line} 出现 {literal!r}"
            for path in _sources()
            for literal in literals
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if literal in text
        ]
        assert offenders == [], "core/degraded 出现配置声明字面量：\n" + "\n".join(offenders)

    def test_无厂商词(self):
        offenders = [
            f"{_rel(path)}:{line} 出现厂商词 {word!r}"
            for path in _sources()
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            for word in VENDOR_WORDS
            if word in text.lower()
        ]
        assert offenders == [], "core/degraded 出现厂商词：\n" + "\n".join(offenders)


class Test零环节名与业务词:
    def test_无环节名(self):
        offenders = [
            f"{_rel(path)}:{match.start()} 出现环节名 {match.group(1)!r}"
            for path in _sources()
            for match in AGENT_NAME_PATTERN.finditer(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], "core/degraded 出现环节名（业务概念混入 core）：\n" + "\n".join(
            offenders
        )

    def test_无业务词(self):
        offenders = [
            f"{_rel(path)}:{line} 出现业务词 {word!r}"
            for path in _sources()
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            for word in BUSINESS_WORDS
            if word in text
        ]
        assert offenders == [], "core/degraded 出现业务词：\n" + "\n".join(offenders)

    def test_零_agent_id_分支(self):
        """参数化而非分支：`agent_id` 只作参数值，不得参与判断。"""
        offenders = [
            f"{_rel(path)}:{match.start()} {match.group(0)!r}"
            for path in _sources()
            for match in AGENT_ID_BRANCH_PATTERN.finditer(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], "core/degraded 出现 agent_id 分支：\n" + "\n".join(offenders)


class Test零反向依赖:
    """原则五：依赖单向（`agents → core`、`dreaming → core`），core 不得反向 import。"""

    def test_无_agents_与_dreaming_导入(self):
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    roots = {(node.module or "").split(".")[0]}
                else:
                    continue
                for root in sorted(roots & set(FORBIDDEN_IMPORT_ROOTS)):
                    offenders.append(f"{_rel(path)}:{node.lineno} 反向 import {root}")
        assert offenders == [], "core/degraded 不得反向依赖：\n" + "\n".join(offenders)

    def test_源码文本无_agents_导入形态(self):
        offenders = [
            f"{_rel(path)}:{line}"
            for path in _sources()
            for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "from agents." in text or "import agents" in text
        ]
        assert offenders == [], f"core/degraded 不得 import agents.*：{offenders}"
