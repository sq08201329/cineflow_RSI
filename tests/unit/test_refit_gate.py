"""权重再拟合提案门禁单测（功能 010 US3 / T524，先于实现编写；契约 C7/C8 全场景）。

C7：超阈生成 pending（含 BiasRecord 引用 + current/candidate + based_version +
fit_objective）；未超阈 None；负相关禁止；首轮无历史台账记基线不判超阈。
C8：confirm 逻辑事务（新版本注册 + yaml 定点改写保注释 + 提案 confirmed）；
shelve 零变更机检；过期 based_version 拒绝；生效中途失败回滚为 failed。
"""

import hashlib
import json
from pathlib import Path

import pytest

from core.calibration.config import CalibrationConfig
from core.calibration.ledger import append_ledger
from core.calibration.models import BiasRecord, PairingRecord, ProposalStatus
from core.calibration.refit import (
    composite_version,
    confirm_proposal,
    load_proposal,
    maybe_propose,
    shelve_proposal,
)
from core.evaluators.errors import ValidationError
from core.evaluators.registry import Registry
from core.yaml_edit import YamlEditError, replace_section_entries

_CFG = CalibrationConfig.from_dict(
    {
        "calibration": {
            "period_days": 7,
            "top_k": 5,
            "min_samples": 3,
            "bias_threshold": 0.15,
            "reliability_target": 0.6,
            "ridge_lambda": 1.0,
            "self_pairing_exclusions": {"platform_truth": ["human.platform_metrics"]},
        }
    }
)

_YAML = """# 形态配置：电影（movie）
# 形态即配置：评估器组合与权重在此表达
form: movie

evaluator_weights:
  visual:
    # gate 表示硬规则门禁：score 为 0 时总分直接为 0
    rule.format_compliance: gate
    proxy.aesthetic: 0.5
    judge.cinematic: 0.5
  promo:
    rule.material_compliance: gate
    proxy.ctr_history: 0.4

# 回放形态参数
replay:
  worker_count: 4
"""

_CURRENT = {"rule.format_compliance": 0.0, "proxy.aesthetic": 0.5, "judge.cinematic": 0.5}
_GATE_KEYS = frozenset({"rule.format_compliance"})


def _bias_record(mean_shift=0.2, samples=5, pearson=0.7, key="proxy.aesthetic@1.0.0"):
    return BiasRecord(
        evaluator_key=key,
        period="2026-W38",
        samples=samples,
        mean_shift=mean_shift,
        pearson_r=pearson,
    )


def _pairs(n: int = 5) -> list[PairingRecord]:
    pairs = []
    for i in range(n):
        for key, auto in (("proxy.aesthetic@1.0.0", 0.4 + i * 0.05), ("judge.cinematic@1.0.0", 0.6)):
            pairs.append(
                PairingRecord(
                    anchor_id=f"a{i}",
                    evaluator_key=key,
                    anchor_score=0.6 + i * 0.05,
                    auto_score=auto,
                )
            )
    return pairs


def _propose(data_dir, bias_records=None, **overrides):
    kwargs = {
        "agent_id": "visual",
        "bias_records": bias_records if bias_records is not None else [_bias_record()],
        "pairs": _pairs(),
        "current_weights": dict(_CURRENT),
        "cfg": _CFG,
        "has_history": True,
        "data_dir": data_dir,
        "fixed_keys": _GATE_KEYS,
    }
    kwargs.update(overrides)
    return maybe_propose(**kwargs)


@pytest.fixture()
def config_file(tmp_path):
    path = tmp_path / "movie.yaml"
    path.write_text(_YAML, encoding="utf-8")
    return path


class TestYaml定点改写:
    def test_段内行替换保注释(self):
        new_text = replace_section_entries(
            _YAML, ("evaluator_weights", "visual"), {"proxy.aesthetic": 0.65}
        )
        assert "    proxy.aesthetic: 0.65\n" in new_text
        # 注释与其他段原样保留
        assert "# gate 表示硬规则门禁：score 为 0 时总分直接为 0" in new_text
        assert "# 形态配置：电影（movie）" in new_text
        assert "    rule.format_compliance: gate\n" in new_text  # gate 行不被触碰
        assert "    proxy.ctr_history: 0.4\n" in new_text
        assert "# 回放形态参数" in new_text

    def test_缺段报错(self):
        with pytest.raises(YamlEditError, match="evaluator_weights"):
            replace_section_entries(_YAML, ("evaluator_weights", "ghost"), {"a": 1.0})

    def test_缺键报错(self):
        with pytest.raises(YamlEditError, match="ghost"):
            replace_section_entries(_YAML, ("evaluator_weights", "visual"), {"ghost": 1.0})


class Test提案生成:
    def test_超阈生成_pending(self, calibration_data_dir):
        proposal = _propose(calibration_data_dir)
        assert proposal is not None
        assert proposal.status is ProposalStatus.PENDING
        assert proposal.based_version == composite_version(_CURRENT)
        assert len(proposal.bias_evidence) == 1
        assert proposal.bias_evidence[0].mean_shift == 0.2
        assert proposal.current_weights == _CURRENT
        # 候选权重约束：gate 冻结为 0，自由键非负且和为一
        assert proposal.candidate_weights["rule.format_compliance"] == 0.0
        free = [v for k, v in proposal.candidate_weights.items() if k not in _GATE_KEYS]
        assert all(v >= 0 for v in free)
        assert sum(free) == pytest.approx(1.0, abs=1e-9)
        assert "1.0" in proposal.fit_objective  # λ_ridge 注入说明
        # 提案文件落盘且可还原
        loaded = load_proposal(calibration_data_dir, proposal.proposal_id)
        assert loaded == proposal

    def test_未超阈返回_none(self, calibration_data_dir):
        assert _propose(calibration_data_dir, bias_records=[_bias_record(mean_shift=0.05)]) is None
        assert not list((calibration_data_dir / "proposals").glob("*.json"))

    def test_负相关禁止生成(self, calibration_data_dir):
        record = _bias_record(mean_shift=0.3, pearson=-0.5)
        assert _propose(calibration_data_dir, bias_records=[record]) is None

    def test_首轮记基线不判超阈(self, calibration_data_dir):
        assert _propose(calibration_data_dir, has_history=False) is None

    def test_样本不达标不触发(self, calibration_data_dir):
        record = _bias_record(mean_shift=0.3, samples=2)  # < min_samples=3
        assert _propose(calibration_data_dir, bias_records=[record]) is None


class TestConfirm生效:
    def _prepare(self, data_dir, config_file):
        # 台账最新快照（calibration 字段填充来源）
        append_ledger(data_dir, "visual", [_bias_record()])
        return _propose(data_dir)

    def test_生效逻辑事务(self, calibration_data_dir, config_file):
        proposal = self._prepare(calibration_data_dir, config_file)
        registry = Registry()
        new_version = confirm_proposal(
            calibration_data_dir,
            config_file,
            proposal_id=proposal.proposal_id,
            by="ops-user",
            registry=registry,
        )
        # ① 新版本注册：版本号即权重哈希元信息，calibration 填台账最新快照
        assert new_version.startswith("1.0.0+w")
        assert new_version == composite_version(proposal.candidate_weights)
        spec = registry.get("composite.visual", new_version).spec
        assert "proxy.aesthetic" in spec.calibration["ledger_latest"]
        assert spec.calibration["ledger_latest"]["proxy.aesthetic"]["period"] == "2026-W38"
        # ② yaml 定点改写：候选权重落盘、gate 行与注释保留、其他段不动
        text = config_file.read_text(encoding="utf-8")
        candidate = proposal.candidate_weights
        assert f"    proxy.aesthetic: {candidate['proxy.aesthetic']:.12g}" in text
        assert "    rule.format_compliance: gate\n" in text
        assert "# gate 表示硬规则门禁" in text
        assert "    proxy.ctr_history: 0.4\n" in text
        # ③ 提案 confirmed：确认人/时间落盘
        confirmed = load_proposal(calibration_data_dir, proposal.proposal_id)
        assert confirmed.status is ProposalStatus.CONFIRMED
        assert confirmed.confirmed_by == "ops-user"
        assert confirmed.confirmed_at

    def test_非_pending_拒绝(self, calibration_data_dir, config_file):
        proposal = self._prepare(calibration_data_dir, config_file)
        shelve_proposal(calibration_data_dir, proposal.proposal_id, by="ops-user")
        with pytest.raises(ValidationError):
            confirm_proposal(
                calibration_data_dir,
                config_file,
                proposal_id=proposal.proposal_id,
                by="ops-user",
                registry=Registry(),
            )

    def test_过期_based_version_拒绝(self, calibration_data_dir, config_file):
        proposal = self._prepare(calibration_data_dir, config_file)
        # 提案后 yaml 权重被改动 → 当前部署版本 ≠ based_version
        config_file.write_text(_YAML.replace("proxy.aesthetic: 0.5", "proxy.aesthetic: 0.7"))
        with pytest.raises(ValidationError, match="过期"):
            confirm_proposal(
                calibration_data_dir,
                config_file,
                proposal_id=proposal.proposal_id,
                by="ops-user",
                registry=Registry(),
            )
        # 提案保持 pending（须重新提案），注册中心零变更
        assert load_proposal(calibration_data_dir, proposal.proposal_id).status is (
            ProposalStatus.PENDING
        )

    def test_生效中途失败回滚(self, calibration_data_dir, config_file, monkeypatch):
        proposal = self._prepare(calibration_data_dir, config_file)
        yaml_hash_before = hashlib.sha256(config_file.read_bytes()).hexdigest()

        import core.calibration.refit as refit_module

        def _boom(*args, **kwargs):
            raise OSError("模拟写入失败")

        monkeypatch.setattr(refit_module, "replace_section_entries", _boom)
        with pytest.raises(OSError, match="模拟写入失败"):
            confirm_proposal(
                calibration_data_dir,
                config_file,
                proposal_id=proposal.proposal_id,
                by="ops-user",
                registry=Registry(),
            )
        # 回滚：提案 → failed，配置不切换（文件逐字节不变）
        assert load_proposal(calibration_data_dir, proposal.proposal_id).status is (
            ProposalStatus.FAILED
        )
        assert hashlib.sha256(config_file.read_bytes()).hexdigest() == yaml_hash_before


class TestShelve零变更:
    def test_注册中心与配置前后一致(self, calibration_data_dir, config_file):
        proposal = _propose(calibration_data_dir)
        registry = Registry()
        specs_before = sorted(s.key for s in registry.list_all())
        yaml_hash_before = hashlib.sha256(config_file.read_bytes()).hexdigest()

        shelved = shelve_proposal(calibration_data_dir, proposal.proposal_id, by="ops-user")
        assert shelved.status is ProposalStatus.SHELVED
        assert sorted(s.key for s in registry.list_all()) == specs_before
        assert hashlib.sha256(config_file.read_bytes()).hexdigest() == yaml_hash_before
        assert load_proposal(calibration_data_dir, proposal.proposal_id).status is (
            ProposalStatus.SHELVED
        )
