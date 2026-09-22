"""功能 015 T1523：升级路径结构化清单机检（FR-012，字段齐全可机检）。

清单 `docs/pilot-upgrade-manifest.json` 承载 A/B/C 三条路径的凭证环境变量名、适配器实现类、
切换命令与预算口径；本文件逐项断言其**字段齐全且可验证**（适配器类必须真实可导入、
预算口径键必须在形态配置中存在），保证"升级路径"不是纸面承诺。
"""

import importlib
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"
DOC = REPO_ROOT / "docs" / "二期升级路径-真实生成与投放.md"
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"

ENV_PATTERN = re.compile(r"^CINEFLOW_[A-Z0-9_]+$")
PATH_IDS = ("A", "B", "C")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_文档与清单齐备():
    assert DOC.is_file() and MANIFEST.is_file()
    for keyword in ("凭证", "预算口径", "切换方式", "模拟生成"):
        assert keyword in DOC.read_text(encoding="utf-8")


def test_三条路径齐备():
    data = _manifest()
    assert tuple(path["path_id"] for path in data["paths"]) == PATH_IDS
    for path in data["paths"]:
        assert path["name"] and path["status"] in {"delivered", "not_delivered"}
        assert path["notes"]


def test_A_路径已交付且零凭证():
    path = next(p for p in _manifest()["paths"] if p["path_id"] == "A")
    assert path["status"] == "delivered"
    assert path["credential_envs"] == []  # 全模拟链路：零凭证


def test_B_C_路径凭证名规范且非空():
    for path in _manifest()["paths"]:
        if path["path_id"] == "A":
            continue
        assert path["credential_envs"], f"{path['path_id']} 必须登记凭证环境变量名"
        for name in path["credential_envs"]:
            assert ENV_PATTERN.match(name), name
        assert path["status"] == "not_delivered"  # 未交付如实登记（不假装可达）


def test_适配器实现类均可导入():
    for path in _manifest()["paths"]:
        assert path["adapters"], f"{path['path_id']} 必须登记适配器实现类"
        for dotted in path["adapters"]:
            module_name, class_name = dotted.rsplit(".", 1)
            module = importlib.import_module(module_name)
            assert hasattr(module, class_name), f"{dotted} 不存在（升级路径不得指向空类）"


def test_切换命令非空且指向本仓_CLI():
    for path in _manifest()["paths"]:
        assert path["switch_commands"]
        for command in path["switch_commands"]:
            assert "uv run python" in command
            assert "ops/pilot.py" in command or "ops/ingest_metrics.py" in command


def test_预算口径引用真实配置键():
    config = yaml.safe_load(SHORTDRAMA.read_text(encoding="utf-8"))
    for path in _manifest()["paths"]:
        policy = path["budget_policy"]
        assert policy["unit"] == "USD"
        assert policy["real_spend"]
        for dotted in policy["round_budget_keys"]:
            section, key = dotted.split(".", 1)
            assert key in config[section], f"预算键 {dotted} 在形态配置中不存在"
        formula = policy["campaign_cap_formula"]
        assert "promo.exploration_per_round_usd" in formula
        assert "promo.promo_pilot_ratio" in formula


def test_诚实边界登记():
    data = _manifest()
    boundary = " ".join(data["honesty_boundary"])
    assert "模拟生成" in boundary
    assert "版权" in boundary
    assert "零形态分支" in boundary or "零代码" in boundary
