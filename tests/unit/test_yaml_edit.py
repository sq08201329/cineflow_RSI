"""core/yaml_edit 扩展能力单测（WS3 遗留 #3：dreaming 审批部署指针定点改写）。

replace_section_entries 扩展到字符串标量（版本号），新增 upsert_section_entries：
段/键缺失时按缩进约定追加（段尾/文件尾），注释、空行与其他段逐字节保留。
"""

import pytest
import yaml

from core.yaml_edit import YamlEditError, replace_section_entries, upsert_section_entries

_YAML = """# 形态配置：电影（movie）
form: movie

# 做梦层形态参数
dreaming:
  candidates_per_round: 128 # 全量档 M
  lambda: 0.5 # reward λ

# 部署指针：仅 approved 版本可成为当期策略（SC-005 机检）
deployment:
  agent-dream:
    current_policy_version: champion-v1  # 当期部署策略版本
"""


class Test字符串标量:
    """部署指针的版本号是字符串：replace 需支持字符串值（010 仅支持数值）。"""

    def test_字符串替换保注释(self):
        new_text = replace_section_entries(
            _YAML, ("deployment", "agent-dream"), {"current_policy_version": "v2-abc"}
        )
        assert "    current_policy_version: v2-abc  # 当期部署策略版本\n" in new_text
        assert "# 部署指针：仅 approved 版本可成为当期策略（SC-005 机检）" in new_text
        assert (
            yaml.safe_load(new_text)["deployment"]["agent-dream"]["current_policy_version"]
            == "v2-abc"
        )

    def test_易被误解析的字符串自动加引号(self):
        # "0.5" 原样输出会被 YAML 解析成浮点，必须引号化保类型
        new_text = replace_section_entries(
            _YAML, ("deployment", "agent-dream"), {"current_policy_version": "0.5"}
        )
        assert (
            yaml.safe_load(new_text)["deployment"]["agent-dream"]["current_policy_version"] == "0.5"
        )

    def test_数值行为不变(self):
        new_text = replace_section_entries(_YAML, ("dreaming",), {"lambda": 0.30000000000000004})
        assert "  lambda: 0.3 # reward λ\n" in new_text


class TestUpsert定点改写:
    """upsert：存在则替换，缺失则追加；注释与其他段逐字节保留。"""

    def test_已存在键_等同替换不追加(self):
        new_text = upsert_section_entries(
            _YAML, ("deployment", "agent-dream"), {"current_policy_version": "v2-abc"}
        )
        assert new_text.count("current_policy_version") == 1
        assert "    current_policy_version: v2-abc  # 当期部署策略版本\n" in new_text

    def test_段内缺键_追加在段尾且缩进对齐(self):
        new_text = upsert_section_entries(
            _YAML, ("deployment", "agent-dream"), {"rollback_policy_version": "v0-root"}
        )
        assert (
            "    current_policy_version: champion-v1  # 当期部署策略版本\n"
            "    rollback_policy_version: v0-root\n"
        ) in new_text
        # 尾随注释/其他段原样保留，yaml 仍可解析
        assert "# 当期部署策略版本" in new_text
        assert (
            yaml.safe_load(new_text)["deployment"]["agent-dream"]["rollback_policy_version"]
            == "v0-root"
        )

    def test_缺末级段_在父段块尾补全(self):
        new_text = upsert_section_entries(
            _YAML, ("deployment", "agent-visual"), {"current_policy_version": "v1-x"}
        )
        assert "  agent-visual:\n    current_policy_version: v1-x\n" in new_text
        loaded = yaml.safe_load(new_text)
        assert loaded["deployment"]["agent-visual"]["current_policy_version"] == "v1-x"
        # 既有 agent-dream 段原样保留
        assert "    current_policy_version: champion-v1  # 当期部署策略版本\n" in new_text

    def test_顶层段缺失_文件尾追加_原文逐字节保留(self):
        text = "# 形态配置：电影（movie）\nform: movie\n"
        new_text = upsert_section_entries(
            text, ("deployment", "agent-dream"), {"current_policy_version": "v1-a"}
        )
        assert new_text.startswith(text)
        assert "deployment:\n  agent-dream:\n    current_policy_version: v1-a\n" in new_text
        assert (
            yaml.safe_load(new_text)["deployment"]["agent-dream"]["current_policy_version"]
            == "v1-a"
        )

    def test_文件尾无换行_追加前补换行(self):
        text = "form: movie"
        new_text = upsert_section_entries(text, ("deployment",), {"current_policy_version": "v1-a"})
        assert new_text.startswith("form: movie\n")
        assert yaml.safe_load(new_text)["deployment"]["current_policy_version"] == "v1-a"

    def test_不支持的值类型报错(self):
        with pytest.raises(YamlEditError):
            upsert_section_entries(
                _YAML, ("deployment", "agent-dream"), {"current_policy_version": ["v1"]}
            )
