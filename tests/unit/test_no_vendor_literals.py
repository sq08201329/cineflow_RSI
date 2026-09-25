"""静态断言：业务代码零厂商字面量 + 调用点必传角色（功能 016 / T1615，先于实现编写）。

**为什么**：原则三要求"路由只在网关"——一旦业务代码里出现模型名/端点（如 `deepseek-flash`、
`api.deepseek.com`），换厂商就又变成改代码而非改配置，且"配置即权威"（原则一/五）随之破功。
本文件用 **AST + 文本双层机检**把它钉死：

1. `core/`（除 `core/llm_gateway/`）、`agents/`、`dreaming/` 的任何位置（含注释）都不得出现
   **配置里声明的档案 id / 端点 host**，也不得出现厂商词（deepseek / openai / qwen）；
   ——"改坏即红"：往任意业务文件塞一句 `MODEL = "deepseek-flash"` 或注释里写端点即红；
2. 上述目录里每个 `.chat(` 调用点都必须**显式传 `role=`**（8 处：剧本生成 / 开发立项论证生成 /
   四家 judge / 做梦候选 / 宣发文案）——漏传即红（接档案后会在运行期报错，这里提前到静态层拦截）。
"""

import ast
import pathlib
import re

import yaml

from ops.form_guard import declared_forms

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# 形态 id 面（021 T2146）：由 `configs/*.yaml` 的 `form:` 派生 ⇒ 新增形态自动进入遍历面
FORMS = declared_forms(REPO_ROOT / "configs")
SCAN_ROOTS = ("core", "agents", "dreaming")
# 除外：网关自身（路由/档案/后端实现里合法出现厂商变量名与协议实现）
EXCLUDED_PREFIXES = ("core/llm_gateway/",)
VENDOR_WORDS = ("deepseek", "openai", "qwen")


def _scanned_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root in SCAN_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith(EXCLUDED_PREFIXES):
                continue
            files.append(path)
    assert files, "扫描集为空（路径口径变了？）"
    return files


def _declared_literals() -> tuple[list[str], list[str]]:
    """从两套形态配置读出"不得出现在业务代码里"的字面量：档案 id 与端点 host。"""
    model_names: set[str] = set()
    hosts: set[str] = set()
    for name in FORMS:
        payload = yaml.safe_load(
            (REPO_ROOT / "configs" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        for profile_id, profile in payload["llm"]["profiles"].items():
            model_names.add(str(profile_id))
            if profile.get("base_url"):
                hosts.add(str(profile["base_url"]).rstrip("/"))
                hosts.add(re.search(r"https?://([^/]+)", str(profile["base_url"])).group(1))
    return sorted(model_names), sorted(hosts)


class Test零厂商字面量:
    def test_配置里声明的模型名与端点不出现在业务代码(self):
        model_names, hosts = _declared_literals()
        assert "deepseek-flash" in model_names and "api.deepseek.com" in hosts
        offenders: list[str] = []
        for path in _scanned_files():
            text = path.read_text(encoding="utf-8")
            for literal in [*model_names, *hosts]:
                if literal in text:
                    line = text[: text.index(literal)].count("\n") + 1
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} 出现 {literal!r}")
        assert offenders == [], (
            "业务代码出现配置声明字面量（路由/端点必须来自配置）：\n" + "\n".join(offenders)
        )

    def test_业务代码无厂商词(self):
        offenders: list[str] = []
        for path in _scanned_files():
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                lowered = line.lower()
                for word in VENDOR_WORDS:
                    if word in lowered:
                        rel = path.relative_to(REPO_ROOT)
                        offenders.append(
                            f"{rel}:{line_no} 出现厂商词 {word!r}：{line.strip()[:80]}"
                        )
        assert offenders == [], "业务代码出现厂商词（应改为配置声明 + 角色路由）：\n" + "\n".join(
            offenders
        )


def _chat_calls() -> list[tuple[pathlib.Path, ast.Call]]:
    """扫描域内的全部 `.chat(` 调用点（019 起：调用点计数钉死 8 处）。"""
    calls: list[tuple[pathlib.Path, ast.Call]] = []
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "chat"
            ):
                calls.append((path, node))
    return calls


class Test调用点必传角色:
    _chat_calls = staticmethod(_chat_calls)

    def test_全部调用点传_role(self):
        # 017 起 8 处：剧本生成 / 开发立项论证生成 / 四家 judge / 做梦候选 / 宣发文案
        calls = self._chat_calls()
        assert len(calls) == 8, f"调用点数量变化（应为 8 处）：{[str(p) for p, _ in calls]}"
        missing = [
            f"{path.relative_to(REPO_ROOT)}:{call.lineno}"
            for path, call in calls
            if not any(kw.arg == "role" for kw in call.keywords)
        ]
        assert missing == [], f"以下调用点未声明 role=（功能 016 要求按角色路由）：{missing}"

    def test_role_取值只来自枚举(self):
        """调用点必须传 `Role.X`（枚举成员），不得传裸字符串（防拼错静默回落）。"""
        bad: list[str] = []
        for path, call in self._chat_calls():
            for kw in call.keywords:
                if kw.arg != "role":
                    continue
                value = kw.value
                ok = isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                ok = ok and value.value.id == "Role"
                if not ok:
                    bad.append(
                        f"{path.relative_to(REPO_ROOT)}:{call.lineno} role={ast.dump(value)}"
                    )
        assert bad == [], f"role 必须为 Role 枚举成员：{bad}"

    def test_七个调用点与盘点表一致(self):
        """盘点表（routing.py docstring）与实际调用点必须一致：角色 → 调用文件集合。"""
        from core.llm_gateway.routing import role_values

        per_role: dict[str, set[str]] = {role: set() for role in role_values()}
        for path, call in self._chat_calls():
            role_attr = next(kw.value for kw in call.keywords if kw.arg == "role")
            per_role[role_attr.attr.lower()].add(path.name)
        assert per_role["generation"] == {"loop.py"}  # 剧本线三阶段生成
        assert len(per_role["judge"]) == 4  # 四家 judge（剧本/分镜/视觉/剪辑）
        assert per_role["copywriting"] == {"material.py"}
        assert per_role["dreaming_candidates"] == {"candidates.py"}


class Test调用点声明环节:
    """功能 019 / T1927：每个 `.chat(` 调用点声明 `stage=`（环节 id）且取值 ∈ 两形态档位键集。

    环节归属由**调用点**声明（网关只有 4 个 `role`，`judge` 一个角色覆盖四个环节，
    `role → tier` 表达不了"按环节分档"）；扫描域 = 本文件的扫描域（`core/` 除网关 / `agents/`
    / `dreaming/`）——故**含** `dreaming/candidates.py`、**不含** `ops/smoke_llm.py`
    （后者在 `ops/` 下，属装配面断言）。调用点计数仍为 8（不得新增）。
    """

    _chat_calls = staticmethod(_chat_calls)

    def test_每个调用点声明_stage_且取值在档位键集内(self):
        tier_ids: set[str] = set()
        llm_tier_ids: set[str] = set()
        for form in FORMS:
            payload = yaml.safe_load(
                (REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8")
            )
            # 020（C11）：档位在**渠道内** —— 扫描面取各渠道键并集，8 处调用点核对取 LLM 渠道
            for channel in payload["budget"]["channels"].values():
                tier_ids |= set(channel["tiers"])
                if channel.get("adapter") == "pilot_llm":
                    llm_tier_ids |= set(channel["tiers"])
        assert len(llm_tier_ids) == 8  # 两形态 LLM 渠道键集一致（8 处调用点各一档）
        calls = self._chat_calls()
        assert len(calls) == 8, f"调用点数量变化（应为 8 处）：{[str(p) for p, _ in calls]}"
        offenders: list[str] = []
        for path, call in calls:
            stage = next((kw for kw in call.keywords if kw.arg == "stage"), None)
            value = stage.value if stage is not None else None
            if not (isinstance(value, ast.Constant) and value.value in tier_ids):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{call.lineno}")
        assert offenders == [], "以下调用点未声明 stage=（或取值不在 budget.tiers 键集内）：" + str(
            offenders
        )
        scanned = {path.relative_to(REPO_ROOT).as_posix() for path, _ in calls}
        assert "dreaming/candidates.py" in scanned  # 扫描域含做梦层
        assert not any(name.startswith("ops/") for name in scanned)  # ops/ 不在本扫描域
