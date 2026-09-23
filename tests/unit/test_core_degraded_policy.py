"""通用件人工策略提交通道单测（功能 017 / T1705，先于实现编写；契约 C1/C2）。

`core/degraded/policy.py` = 009 `policy_versions.py` 的机制抽出，参数化为 `agent_id`：

- 版本 = 源码 BLAKE3 前 12 位；落 `{history_root}/{agent_id}/{version}.py` + `{version}.meta.json`
  （谱系 meta 复用 005 `dreaming.lineage` 布局）；
- 静态检查（002 `policies.static_check`）与接口签名（AST 校验，不执行源码）任一不过即**拒绝**，
  且历史无新增（不静默放过）；
- 草稿（draft=True）不入历史、不参与回放；同源码重复提交幂等（谱系只增不改，
  首位提交人的 provenance 不被后来者覆盖）；
- meta 含 `no_auto_evolve` 审计位（宪章原则六：提交时该 Agent 是否在禁自动进化名单内）；
- **同一份实现服务多个 Agent**：`agent_id` 只作参数值，各落各的目录，代码内零 agent 名分支。
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.degraded.policy import (
    DEFAULT_POLICY_HISTORY_ROOT,
    HumanPolicyVersion,
    PolicySubmissionError,
    list_policy_versions,
    load_policy_source,
    read_policy_meta,
    submit_policy,
)
from policies.static_check import find_violations
from policies.versioning import policy_version

_POLICY_SOURCE = (
    "class Policy:\n"
    '    """测试用人工策略（纯计算 + plan(inputs, config) 接口）。"""\n'
    "    PLAN = {'stage-a': {'tag': 'a'}}\n\n"
    "    def plan(self, inputs, config):\n"
    "        return self.PLAN\n"
)
_PARENT = "9f2c41ab77de"


def _cfg(**overrides):
    fields = {"lambda_": 0.0, "no_auto_evolve_agents": ("screenplay", "dev")}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _files(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


class Test提交通道:
    def test_版本化落盘与谱系字段(self, tmp_path):
        """C1：版本 = 源码 BLAKE3 前 12 位；源码 + meta 落 {history_root}/{agent_id}/。"""
        source = _POLICY_SOURCE
        assert find_violations(source) == []  # 夹具合法（静态检查必过）
        record = submit_policy(
            source,
            "sunqi",
            _cfg(),
            agent_id="screenplay",
            history_root=tmp_path,
            parent_version=_PARENT,
        )
        assert isinstance(record, HumanPolicyVersion)
        assert record.version == policy_version(source)
        assert len(record.version) == 12
        assert record.parent_version == _PARENT
        assert record.submitter == "sunqi"
        assert record.static_check == "passed"
        assert record.recorded is True
        assert record.source == "manual"
        assert _files(tmp_path) == [
            f"screenplay/{record.version}.meta.json",
            f"screenplay/{record.version}.py",
        ]
        assert record.source_path == tmp_path / "screenplay" / f"{record.version}.py"

    def test_草稿不入历史(self, tmp_path):
        record = submit_policy(
            _POLICY_SOURCE,
            "sunqi",
            _cfg(),
            agent_id="screenplay",
            history_root=tmp_path,
            draft=True,
        )
        assert record.recorded is False
        assert record.version == policy_version(_POLICY_SOURCE)
        assert _files(tmp_path) == []

    def test_同源码重复提交幂等且不覆盖首位提交人(self, tmp_path):
        first = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path
        )
        before = _files(tmp_path)
        again = submit_policy(
            _POLICY_SOURCE, "later", _cfg(), agent_id="screenplay", history_root=tmp_path
        )
        assert again.version == first.version
        assert again.submitter == "sunqi"  # 谱系只增不改
        assert _files(tmp_path) == before


class Test按_agent_参数化:
    """C1 场景 2：同一份实现以不同 `agent_id` 调用 → 各落各的目录（无第二份实现）。"""

    def test_两个_agent_各落各的目录(self, tmp_path):
        script = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path
        )
        other = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="dev", history_root=tmp_path
        )
        assert script.version == other.version  # 同源码同版本（机制一致）
        assert _files(tmp_path) == [
            f"dev/{other.version}.meta.json",
            f"dev/{other.version}.py",
            f"screenplay/{script.version}.meta.json",
            f"screenplay/{script.version}.py",
        ]

    def test_读取面按_agent_隔离(self, tmp_path):
        version = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path
        ).version
        assert load_policy_source(version, agent_id="screenplay", history_root=tmp_path) == (
            _POLICY_SOURCE
        )
        assert list_policy_versions(agent_id="screenplay", history_root=tmp_path) == (version,)
        # 另一 Agent 的历史为空：不得读到别的 Agent 的版本（不伪造）
        assert list_policy_versions(agent_id="dev", history_root=tmp_path) == ()
        assert read_policy_meta(version, agent_id="dev", history_root=tmp_path) is None
        with pytest.raises(PolicySubmissionError, match="不存在"):
            load_policy_source(version, agent_id="dev", history_root=tmp_path)

    def test_默认历史根为仓库口径(self, tmp_path):
        record = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path
        )
        assert DEFAULT_POLICY_HISTORY_ROOT == Path("policies/history")
        assert (tmp_path / "screenplay" / f"{record.version}.py").is_file()


class Test拒绝语义:
    """未过检查即拒绝，且不入历史（不静默放过）。"""

    def test_静态检查未过即拒绝(self, tmp_path):
        source = "import socket\n\n" + _POLICY_SOURCE
        assert find_violations(source)  # 白名单外 import
        with pytest.raises(PolicySubmissionError, match="静态检查"):
            submit_policy(source, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path)
        assert _files(tmp_path) == []

    def test_接口签名不符即拒绝(self, tmp_path):
        source = "class Policy:\n    def plan(self, inputs):\n        return {}\n"
        with pytest.raises(PolicySubmissionError, match="接口"):
            submit_policy(source, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path)
        assert _files(tmp_path) == []

    @pytest.mark.parametrize(
        "source, submitter",
        [("", "sunqi"), ("   ", "sunqi"), (_POLICY_SOURCE, "")],
        ids=["空源码", "空白源码", "空提交人"],
    )
    def test_输入缺失即拒绝(self, tmp_path, source, submitter):
        with pytest.raises(PolicySubmissionError):
            submit_policy(source, submitter, _cfg(), agent_id="screenplay", history_root=tmp_path)
        assert _files(tmp_path) == []


class Test名单审计位:
    """宪章原则六：人工策略 meta 留痕"该 Agent 是否在禁自动进化名单内"。"""

    @pytest.mark.parametrize(
        "agent_id, expected", [("screenplay", True), ("dev", True), ("promo", False)]
    )
    def test_no_auto_evolve_审计位(self, tmp_path, agent_id, expected):
        record = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id=agent_id, history_root=tmp_path
        )
        meta = read_policy_meta(record.version, agent_id=agent_id, history_root=tmp_path)
        assert meta["no_auto_evolve"] is expected
        assert meta["source"] == "manual"
        assert meta["submission"] == {"submitter": "sunqi", "at": record.submitted_at}
        # 落盘 meta 与读取一致（实际写的是同一份）
        on_disk = json.loads(record.meta_path.read_text(encoding="utf-8"))
        assert on_disk["no_auto_evolve"] is expected

    def test_名单未配置即如实为_False(self, tmp_path):
        """cfg 未声明名单（duck-typed）→ 审计位 False，不伪造"已在名单内"。"""
        record = submit_policy(
            _POLICY_SOURCE, "sunqi", SimpleNamespace(), agent_id="dev", history_root=tmp_path
        )
        meta = read_policy_meta(record.version, agent_id="dev", history_root=tmp_path)
        assert meta["no_auto_evolve"] is False


class Test读取值语义:
    def test_meta_为值语义副本(self, tmp_path):
        record = submit_policy(
            _POLICY_SOURCE, "sunqi", _cfg(), agent_id="screenplay", history_root=tmp_path
        )
        meta = read_policy_meta(record.version, agent_id="screenplay", history_root=tmp_path)
        meta["submission"]["submitter"] = "篡改"
        reread = read_policy_meta(record.version, agent_id="screenplay", history_root=tmp_path)
        assert reread["submission"]["submitter"] == "sunqi"

    def test_历史目录不存在返回空(self, tmp_path):
        assert list_policy_versions(agent_id="screenplay", history_root=tmp_path / "none") == ()
