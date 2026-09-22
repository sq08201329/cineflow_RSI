"""升级清单一致性机检锁（功能 016 / T1622，先于实现编写）：契约 C10。

**以配置为权威**：`configs/*.yaml` 的 `llm.profiles` 声明的变量名集合 ⇄ 清单登记**逐项一致**
（漂移即红、两侧差异都要列出）：

- 配置加档案/改变量但清单未登记 → 红（并打印"配置有、清单无"的差额）；
- 改清单一个变量名 → 红（两侧各差一项，可诊断）；
- 每条登记必须带**用途**与档案 id；沿用旧变量名的档案要如实标注（`legacy_env`）。
"""

import json
import pathlib

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "docs" / "pilot-upgrade-manifest.json"
CONFIGS = ("configs/movie.yaml", "configs/shortdrama.yaml")


def _declared_from_config() -> dict[str, set[str]]:
    """配置档案声明的变量名（配置是唯一权威）：{profile_id: {变量名, ...}}。"""
    declared: dict[str, set[str]] = {}
    for name in CONFIGS:
        payload = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
        for profile_id, profile in payload["llm"]["profiles"].items():
            names = {str(profile["api_key_env"])}
            if not profile.get("base_url"):
                names.add(str(profile["base_url_env"]))
            declared[str(profile_id)] = names
    return declared


def _registered_in_manifest(manifest_path: pathlib.Path = MANIFEST) -> dict[str, set[str]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    block = payload["llm_profiles"]
    return {
        str(entry["profile_id"]): {str(name) for name in entry["variables"]}
        for entry in block["profiles"]
    }


def _diff(manifest_path: pathlib.Path = MANIFEST) -> str:
    config = _declared_from_config()
    manifest = _registered_in_manifest(manifest_path)
    config_only = {
        profile_id: sorted(names - manifest.get(profile_id, set()))
        for profile_id, names in config.items()
        if names - manifest.get(profile_id, set())
    }
    manifest_only = {
        profile_id: sorted(names - config.get(profile_id, set()))
        for profile_id, names in manifest.items()
        if names - config.get(profile_id, set())
    }
    missing_profiles = sorted(set(config) - set(manifest))
    extra_profiles = sorted(set(manifest) - set(config))
    return json.dumps(
        {
            "配置有清单无（变量）": config_only,
            "清单有配置无（变量）": manifest_only,
            "配置有清单无（档案）": missing_profiles,
            "清单有配置无（档案）": extra_profiles,
        },
        ensure_ascii=False,
        indent=2,
    )


class Test配置清单一致:
    def test_变量名与档案逐项一致(self):
        assert _declared_from_config() == _registered_in_manifest(), _diff()

    def test_登记含用途且标注沿用旧变量名(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))["llm_profiles"]
        assert payload["authority"] == "configs/*.yaml 的 llm.profiles（配置即权威）"
        for entry in payload["profiles"]:
            assert entry["profile_id"] and entry["purpose"]
            assert entry["variables"]
            for _variable in entry["variables"]:
                assert entry["purpose"]  # 每个变量都有用途说明（同一档案共用一条用途）
            if entry.get("legacy_env"):
                assert "沿用旧变量名" in entry["note"]

    def test_新增变量名已登记(self):
        """本批新增的本地档案变量必须登记（否则按设计红）：
        LOCAL_LLM_BASE_URL / LOCAL_LLM_API_KEY。"""
        registered = _registered_in_manifest()
        assert registered["local-qwen"] == {"LOCAL_LLM_BASE_URL", "LOCAL_LLM_API_KEY"}
        assert registered["deepseek-flash"] == {"OPENAI_API_KEY"}  # 端点写在档案里（URL 形态）

    def test_schema_已递增(self):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.6.0"


class Test改坏即红:
    def test_清单少登记一个变量即红并列出差异(self, tmp_path):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        for entry in payload["llm_profiles"]["profiles"]:
            if entry["profile_id"] == "local-qwen":
                entry["variables"] = [
                    name for name in entry["variables"] if name != "LOCAL_LLM_API_KEY"
                ]
        broken = tmp_path / "manifest.json"
        broken.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        assert _declared_from_config() != _registered_in_manifest(broken)
        assert "LOCAL_LLM_API_KEY" in _diff(broken)  # 差异可诊断（点名具体变量）

    def test_配置加档案未登记即红(self, tmp_path):
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        payload["llm_profiles"]["profiles"] = [
            entry
            for entry in payload["llm_profiles"]["profiles"]
            if entry["profile_id"] != "local-qwen"
        ]
        broken = tmp_path / "manifest.json"
        broken.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        diff = _diff(broken)
        assert "local-qwen" in diff and "配置有清单无" in diff
