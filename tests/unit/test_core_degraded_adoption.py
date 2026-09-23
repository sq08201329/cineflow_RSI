"""通用件采纳门禁与决策留痕单测（功能 017 / T1709，先于实现编写；契约 C1/C14）。

`core/degraded/adoption.py` = 009 `adoption.py` 的机制抽出，参数化为 `agent_id` + 注入目录：

- **人工采纳才动部署指针**：`adopt` → 定点改写 `deployment.{agent_id}.current_policy_version`
  （复用 `core/yaml_edit`：注释、空行与其他段逐字节保留）；
- `reject` → 指针**逐字节不变**；两种决定都留痕（人 / 时间 / 依据 comparison_id / 理由非空）；
- 采纳前机检：对比报告必须存在（依据引用）+ 报告基线版本与当前指针一致（防在漂移基线上采纳）
  + 待采纳版本必须在策略历史内（未版本化的策略不得部署）；
- 目录与 agent 全部注入：核心件内零 agent 名与默认目录。
"""

import hashlib
import json
from pathlib import Path

import pytest

from core.degraded.adoption import (
    DECISIONS,
    AdoptionError,
    AdoptionRecord,
    adopt,
    deployed_version,
    load_adoption_record,
)

_AGENT_ID = "screenplay"
_DEPLOYED = "9f2c41ab77de"
_NEW = "2b7f10c4de91"

_CONFIG_TEMPLATE = """\
# 形态配置副本（用例夹具：注释与空行必须逐字节保留）
screenplay:
  # 页数口径
  lines_per_page: 3

deployment:
  screenplay:
    current_policy_version: {pointer}  # 部署指针
  promo:
    current_policy_version: "111111111111"
"""


def _config_copy(tmp_path: Path, *, pointer: str | None = _DEPLOYED) -> Path:
    path = tmp_path / "movie.yaml"
    path.write_text(_CONFIG_TEMPLATE.format(pointer=pointer or _DEPLOYED), encoding="utf-8")
    return path


def _history(tmp_path: Path, agent_id: str, versions) -> Path:
    root = tmp_path / "history"
    (root / agent_id).mkdir(parents=True, exist_ok=True)
    for index, version in enumerate(versions):
        (root / agent_id / f"{version}.py").write_text(
            f"class Policy:\n    PLAN = {{'stage-a': {index}}}\n", encoding="utf-8"
        )
    return root


def _report(tmp_path: Path, comparison_id: str, *, deployed: str, new: str) -> Path:
    directory = tmp_path / f"comparisons-{_AGENT_ID}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{comparison_id}.json").write_text(
        json.dumps(
            {
                "comparison_id": comparison_id,
                "agent_id": _AGENT_ID,
                "deployed_version": deployed,
                "new_version": new,
                "verdict": "new_better",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture()
def adoption_env(tmp_path):
    comparison_id = "cmp-screenplay-new-deployed"
    return {
        "comparison_id": comparison_id,
        "comparison_dir": _report(tmp_path, comparison_id, deployed=_DEPLOYED, new=_NEW),
        "adoption_dir": tmp_path / "adoptions-screenplay",
        "history_root": _history(tmp_path, _AGENT_ID, [_NEW]),
        "config_path": _config_copy(tmp_path),
        "tmp_path": tmp_path,
    }


def _adopt(env, *, decision="adopt", reason="回放对比占优，采纳", by="sunqi", config_path=None):
    return adopt(
        env["comparison_id"],
        decision,
        by,
        reason,
        config_path=config_path or env["config_path"],
        agent_id=_AGENT_ID,
        comparison_dir=env["comparison_dir"],
        adoption_dir=env["adoption_dir"],
        history_root=env["history_root"],
    )


class Test采纳:
    def test_采纳更新指针且定点改写保注释(self, adoption_env):
        """采纳是**唯一**能移动部署指针的动作；其余行（含注释与空行）逐字节保留。"""
        config_path = adoption_env["config_path"]
        before = config_path.read_text(encoding="utf-8").splitlines()
        record = _adopt(adoption_env)
        assert isinstance(record, AdoptionRecord)
        assert deployed_version(config_path, agent_id=_AGENT_ID) == _NEW
        assert record.decision == "adopt"
        assert record.by == "sunqi" and record.reason == "回放对比占优，采纳"
        assert record.at
        assert record.comparison_id == adoption_env["comparison_id"]  # 依据引用
        assert record.deployed_before == _DEPLOYED and record.deployed_after == _NEW
        assert record.adopted_version == _NEW
        assert record.record_path.is_file()
        after = config_path.read_text(encoding="utf-8").splitlines()
        pointer_before = f"    current_policy_version: {_DEPLOYED}  # 部署指针"
        pointer_after = f"    current_policy_version: {_NEW}  # 部署指针"
        assert before.count(pointer_before) == 1 and after.count(pointer_after) == 1
        assert [line for line in before if line != pointer_before] == [
            line for line in after if line != pointer_after
        ]
        assert "# 形态配置副本（用例夹具：注释与空行必须逐字节保留）" in after
        assert '    current_policy_version: "111111111111"' in after  # 其他 agent 段未动
        stored = load_adoption_record(
            adoption_env["comparison_id"], "adopt", adoption_dir=adoption_env["adoption_dir"]
        )
        assert stored == {**record.to_dict(), "record_path": None}  # 落盘在回填路径之前

    def test_拒绝留痕且指针逐字节不变(self, adoption_env):
        config_path = adoption_env["config_path"]
        before = hashlib.sha256(config_path.read_bytes()).hexdigest()
        record = _adopt(adoption_env, decision="reject", reason="回放证据不足，保留现版本")
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before
        assert deployed_version(config_path, agent_id=_AGENT_ID) == _DEPLOYED
        assert record.decision == "reject"
        assert record.adopted_version is None
        assert record.deployed_before == record.deployed_after == _DEPLOYED
        assert record.record_path.is_file()  # 拒绝同样留痕

    def test_指针按_agent_分节(self, tmp_path):
        """同一份实现服务多个 Agent：指针写各自段（`deployment.{agent_id}`）。"""
        config_path = _config_copy(tmp_path)
        for agent_id, version in (("screenplay", _DEPLOYED), ("promo", "111111111111")):
            assert deployed_version(config_path, agent_id=agent_id) == version
        assert deployed_version(config_path, agent_id="dev") is None  # 未配置如实为 None

    def test_未配置指针返回_None(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("screenplay:\n  lines_per_page: 3\n", encoding="utf-8")
        assert deployed_version(path, agent_id=_AGENT_ID) is None


class Test采纳前置门禁:
    def test_决策枚举非法拒绝(self, adoption_env):
        config_path = adoption_env["config_path"]
        before = hashlib.sha256(config_path.read_bytes()).hexdigest()
        with pytest.raises(AdoptionError, match="decision"):
            _adopt(adoption_env, decision="maybe")
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before
        assert DECISIONS == ("adopt", "reject")

    @pytest.mark.parametrize(
        "by, reason",
        [("", "理由"), ("sunqi", ""), ("sunqi", "   ")],
        ids=["无人", "空理由", "仅空白"],
    )
    def test_决策人与理由必填(self, adoption_env, by, reason):
        with pytest.raises(AdoptionError):
            _adopt(adoption_env, by=by, reason=reason)
        assert list(adoption_env["adoption_dir"].glob("*.json")) == []

    def test_依据报告缺失拒绝(self, adoption_env):
        with pytest.raises(FileNotFoundError, match="对比报告"):
            adopt(
                "cmp-不存在",
                "adopt",
                "sunqi",
                "理由",
                config_path=adoption_env["config_path"],
                agent_id=_AGENT_ID,
                comparison_dir=adoption_env["comparison_dir"],
                adoption_dir=adoption_env["adoption_dir"],
                history_root=adoption_env["history_root"],
            )

    def test_基线漂移拒绝(self, adoption_env):
        """报告基线版本与当前指针不一致（基线已漂移）→ 拒绝决策，指针不变。"""
        config_path = _config_copy(adoption_env["tmp_path"], pointer="000000000000")
        before = hashlib.sha256(config_path.read_bytes()).hexdigest()
        with pytest.raises(AdoptionError, match="漂移"):
            _adopt(adoption_env, config_path=config_path)
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before

    def test_未版本化策略不得采纳(self, adoption_env):
        """待采纳版本不在策略历史内 → 拒绝（未版本化的策略不得部署）。"""
        env = dict(adoption_env)
        env["history_root"] = adoption_env["tmp_path"] / "empty-history"
        with pytest.raises(AdoptionError, match="策略历史"):
            _adopt(env)
        assert deployed_version(adoption_env["config_path"], agent_id=_AGENT_ID) == _DEPLOYED

    def test_二次决策拒绝只增不改(self, adoption_env):
        _adopt(adoption_env, decision="reject", reason="先拒绝")
        with pytest.raises(AdoptionError, match="只增不改"):
            _adopt(adoption_env, decision="reject", reason="再拒绝")

    def test_读取不存在的记录即报错(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="采纳记录"):
            load_adoption_record("cmp-不存在", "adopt", adoption_dir=tmp_path)


class Test记录字段:
    def test_字段集锁定(self):
        assert set(AdoptionRecord.__dataclass_fields__) == {
            "comparison_id",
            "agent_id",
            "decision",
            "by",
            "reason",
            "at",
            "deployed_before",
            "deployed_after",
            "adopted_version",
            "record_path",
        }

    def test_记录落盘含_agent_id(self, adoption_env):
        record = _adopt(adoption_env)
        assert record.record_path.parent == adoption_env["adoption_dir"]
        assert record.record_path.name == f"{adoption_env['comparison_id']}.adopt.json"
        payload = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert payload["agent_id"] == _AGENT_ID
        assert payload["record_path"] is None  # 落盘在回填路径之前（落盘内容不含指向自身）
        assert payload["deployed_after"] == _NEW
