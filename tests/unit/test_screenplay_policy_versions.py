"""人工策略提交通道单测（功能 009 / T925，先于实现编写）。

C13：`submit_policy(source_text, submitter, cfg)` → 版本 = 源码 BLAKE3 前 12 位，
落 `policies/history/screenplay/{version}.py` + `{version}.meta.json`
（parent_version / 提交人 / 时间 / 静态检查结果，复用 005 谱系 meta schema）；
**静态检查与接口签名任一不过即拒绝且历史无新增**（002 静态检查复用——禁私有属性访问）；
同源码重复提交幂等（同版本号、不重复落盘、谱系只增不改）；**草稿不入历史、不参与回放**；
**参数调整走 configs —— 不产生策略版本**（版本只取决于源码，产出执行器不写策略历史）。
"""

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest
import yaml

from agents.screenplay.config import ScreenplayConfig
from agents.screenplay.policy_versions import (
    HumanPolicyVersion,
    PolicySubmissionError,
    submit_policy,
)
from dreaming.lineage import validate_meta
from policies.versioning import policy_version

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))

_PARENT = "9f2c41ab77de"


def _files(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


class Test合法提交:
    def test_版本化落盘含_parent_version_与提交人(
        self, tmp_path, dream_config, screenplay_policy_source
    ):
        source = screenplay_policy_source("valid")
        record = submit_policy(
            source, "sunqi", dream_config, parent_version=_PARENT, history_root=tmp_path
        )
        assert isinstance(record, HumanPolicyVersion)
        assert record.version == policy_version(source)  # 版本 = 源码 BLAKE3 前 12 位
        assert len(record.version) == 12
        assert record.parent_version == _PARENT
        assert record.submitter == "sunqi"
        assert record.static_check == "passed"
        assert record.violations == ()
        assert record.recorded is True
        # 源码与 meta 落盘（polices/history/screenplay/{version}.py + .meta.json）
        source_path = tmp_path / "screenplay" / f"{record.version}.py"
        meta_path = tmp_path / "screenplay" / f"{record.version}.meta.json"
        assert source_path.read_text(encoding="utf-8") == source
        assert record.source_path == source_path
        assert record.meta_path == meta_path
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        validate_meta(meta)  # 复用 005 谱系 meta schema
        assert meta["version"] == record.version
        assert meta["parent_version"] == _PARENT
        assert meta["source"] == "manual"
        assert meta["submission"]["submitter"] == "sunqi"
        assert meta["submission"]["at"]
        assert meta["static_check"]["result"] == "passed"
        assert meta["static_check"]["violations"] == []

    def test_首版_parent_version_为_null(self, tmp_path, dream_config, screenplay_policy_source):
        record = submit_policy(
            screenplay_policy_source("valid"), "sunqi", dream_config, history_root=tmp_path
        )
        assert record.parent_version is None
        meta = json.loads(record.meta_path.read_text(encoding="utf-8"))
        assert meta["parent_version"] is None  # 谱系根

    def test_名单审计字段_降级模式留痕(self, tmp_path, dream_config, screenplay_policy_source):
        """meta 记录提交时该 Agent 是否在禁自动进化名单内（宪章原则六可审计）。"""
        record = submit_policy(
            screenplay_policy_source("valid"), "sunqi", dream_config, history_root=tmp_path
        )
        meta = json.loads(record.meta_path.read_text(encoding="utf-8"))
        assert meta["no_auto_evolve"] is True  # screenplay 在默认名单内

    @pytest.mark.parametrize(
        "submitter, source",
        [("", "class Policy:\n    pass\n"), ("sunqi", ""), ("sunqi", "   \n")],
        ids=["提交人为空", "源码为空", "源码仅空白"],
    )
    def test_输入缺失拒绝(self, tmp_path, dream_config, submitter, source):
        with pytest.raises(PolicySubmissionError):
            submit_policy(source, submitter, dream_config, history_root=tmp_path)
        assert _files(tmp_path) == []


class Test违规拒绝:
    @pytest.mark.parametrize(
        "variant", ["forbidden_import", "forbidden_call"], ids=["白名单外 import", "危险内建"]
    )
    def test_静态检查不过拒绝且历史无新增(
        self, tmp_path, dream_config, screenplay_policy_source, variant
    ):
        with pytest.raises(PolicySubmissionError, match="静态检查"):
            submit_policy(
                screenplay_policy_source(variant), "sunqi", dream_config, history_root=tmp_path
            )
        assert _files(tmp_path) == []  # 未过检查不入历史

    def test_接口签名错拒绝且历史无新增(self, tmp_path, dream_config, screenplay_policy_source):
        """签名错（plan 缺 config 参数）→ 拒绝（人工策略接口 T914 定案）。"""
        with pytest.raises(PolicySubmissionError, match="plan"):
            submit_policy(
                screenplay_policy_source("bad_signature"),
                "sunqi",
                dream_config,
                history_root=tmp_path,
            )
        assert _files(tmp_path) == []

    @pytest.mark.parametrize(
        "source",
        [
            "VALUE = 1\n",
            "class Policy:\n    def solve(self, env, budget):\n        return ''\n",
            "class Policy:\n    def plan(self, inputs):\n        return {}\n",
            "class Policy:\n    def plan(self, config, inputs):\n        return {}\n",
        ],
        ids=["缺 Policy 类", "缺 plan 方法", "缺 config 参数", "参数次序错"],
    )
    def test_接口不符拒绝(self, tmp_path, dream_config, source):
        with pytest.raises(PolicySubmissionError):
            submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        assert _files(tmp_path) == []

    def test_语法错误拒绝(self, tmp_path, dream_config):
        with pytest.raises(PolicySubmissionError, match="静态检查"):
            submit_policy("class Policy(\n", "sunqi", dream_config, history_root=tmp_path)
        assert _files(tmp_path) == []


class Test幂等:
    def test_同源码重复提交同版本号不重复落盘(
        self, tmp_path, dream_config, screenplay_policy_source
    ):
        source = screenplay_policy_source("valid")
        first = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        before = _files(tmp_path)
        meta_before = first.meta_path.read_text(encoding="utf-8")
        second = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        assert second.version == first.version
        assert _files(tmp_path) == before  # 0 新增文件
        assert second.meta_path.read_text(encoding="utf-8") == meta_before  # 谱系只增不改

    def test_不同提交人重复提交不改写谱系(self, tmp_path, dream_config, screenplay_policy_source):
        """谱系只增不改：首位提交人的 provenance 不被后来者覆盖。"""
        source = screenplay_policy_source("valid")
        first = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        meta_before = first.meta_path.read_text(encoding="utf-8")
        second = submit_policy(source, "other", dream_config, history_root=tmp_path)
        assert second.version == first.version
        meta = json.loads(second.meta_path.read_text(encoding="utf-8"))
        assert meta["submission"]["submitter"] == "sunqi"
        assert second.meta_path.read_text(encoding="utf-8") == meta_before

    def test_不同源码版本不同(self, tmp_path, dream_config, screenplay_policy_source):
        first = submit_policy(
            screenplay_policy_source("valid"), "sunqi", dream_config, history_root=tmp_path
        )
        other = screenplay_policy_source("valid").replace("病房", "手术室")
        second = submit_policy(other, "sunqi", dream_config, history_root=tmp_path)
        assert second.version != first.version
        assert len(_files(tmp_path)) == 4  # 两份源码 + 两份 meta


class Test草稿:
    def test_草稿不入历史(self, tmp_path, dream_config, screenplay_policy_source):
        """草稿仅校验与算版本：不落盘、不参与回放（正式提交才版本化）。"""
        source = screenplay_policy_source("valid")
        draft = submit_policy(source, "sunqi", dream_config, history_root=tmp_path, draft=True)
        assert draft.recorded is False
        assert draft.source_path is None and draft.meta_path is None
        assert draft.version == policy_version(source)  # 版本照算（预检用）
        assert _files(tmp_path) == []

    def test_草稿校验同样严格(self, tmp_path, dream_config, screenplay_policy_source):
        with pytest.raises(PolicySubmissionError, match="静态检查"):
            submit_policy(
                screenplay_policy_source("forbidden_import"),
                "sunqi",
                dream_config,
                history_root=tmp_path,
                draft=True,
            )

    def test_草稿随后正式提交可落历史(self, tmp_path, dream_config, screenplay_policy_source):
        source = screenplay_policy_source("valid")
        draft = submit_policy(source, "sunqi", dream_config, history_root=tmp_path, draft=True)
        record = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        assert record.version == draft.version
        assert record.recorded is True
        assert len(_files(tmp_path)) == 2


class Testconfigs调参不产生策略版本:
    """澄清 Q2：策略 = 代码；参数调整走 configs（配置快照随树冻结），不产生策略版本。"""

    def test_调参后同源码版本不变且历史无新增(
        self, tmp_path, dream_config, screenplay_policy_source
    ):
        source = screenplay_policy_source("valid")
        first = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        before = _files(tmp_path)
        # 调参：形态配置副本变更（目标时长 / 比例区间 / 判据阈值）——配置即形态
        raw = copy.deepcopy(_REAL_CONFIG)
        raw["screenplay"]["target_duration_min"] = 120
        raw["screenplay"]["page_tolerance"] = 8
        raw["screenplay"]["dialogue_action_ratio"] = {"min": 0.3, "max": 0.9}
        raw["screenplay"]["upgrade_criteria"]["min_samples"] = 9
        config = ScreenplayConfig.from_dict(raw)
        assert config.target_duration_min == 120  # 调参已生效
        assert config.upgrade_criteria["min_samples"] == 9
        # 同源码再次提交：版本只取决于源码 → 版本不变、历史无新增
        again = submit_policy(source, "sunqi", dream_config, history_root=tmp_path)
        assert again.version == first.version
        assert _files(tmp_path) == before

    def test_版本只取决于源码(self, screenplay_policy_source):
        """版本 = 源码 BLAKE3 前 12 位（与配置、提交人、时间无关）。"""
        source = screenplay_policy_source("valid")
        assert policy_version(source) == policy_version(source)
        assert policy_version(source) != policy_version(source + "\n")

    def test_产出执行器不写策略历史(self):
        """产出执行器（loop）不产生策略版本：不引用策略历史路径与落盘函数。"""
        from agents.screenplay import loop

        source = inspect.getsource(loop)
        assert "policies/history" not in source
        assert "record_policy" not in source
        assert "write_meta" not in source

    def test_策略模块不自动生成版本(self):
        """提交通道仅由显式调用产生版本：模块不含自动生成/调度入口。"""
        from agents.screenplay import policy_versions

        tree = ast.parse(inspect.getsource(policy_versions))
        functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "submit_policy" in functions
        assert not {name for name in functions if "auto" in name.lower()}  # 无自动进化入口
