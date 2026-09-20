"""校准领域模型单测（功能 010 / T505，先于实现编写）。

- frozen 不可变：构造后改字段必须抛 FrozenInstanceError；
- AnchorScore：score ∈ [0,1]、source ∈ {human_blind, platform_truth}、
  artifact_hash 64 位小写十六进制（校验风格对齐 core/evaluators/base.py）；
- CalibrationRound 状态机 open → intake → closed；WeightProposal 状态机
  pending → confirmed | shelved | failed，终态不可逆。
"""

from dataclasses import FrozenInstanceError

import pytest

from core.calibration.models import (
    AnchorScore,
    AnchorSource,
    BiasRecord,
    CalibrationRound,
    PairingRecord,
    ProposalStatus,
    RoundStatus,
    WeightProposal,
)
from core.evaluators.errors import ValidationError

_ANCHOR = {
    "anchor_id": "a1",
    "node_id": "n1",
    "artifact_hash": "ab" * 32,
    "agent_id": "visual",
    "source": "human_blind",
    "score": 0.8,
    "reviewer": "reviewer-1",
    "round_id": "r1",
    "created_at": "2026-09-19T00:00:00Z",
}

_ROUND = {
    "round_id": "r1",
    "agent_id": "visual",
    "period_start": "2026-09-14",
    "period_end": "2026-09-20",
    "top_k": 5,
    "node_ids": ("n1", "n2"),
}

_PROPOSAL = {
    "proposal_id": "p1",
    "agent_id": "visual",
    "based_version": "1.0.0",
    "bias_evidence": (),
    "current_weights": {"proxy.aesthetic": 0.5, "judge.cinematic": 0.5},
    "candidate_weights": {"proxy.aesthetic": 0.6, "judge.cinematic": 0.4},
    "fit_objective": "min Σ(anchor − Σ w_e·s_e)² + 1.0·‖w − w_current‖²",
    "ridge_lambda": 1.0,
}


class TestAnchorScore:
    def test_合法构造与默认值(self):
        anchor = AnchorScore(**_ANCHOR)
        assert anchor.source is AnchorSource.HUMAN_BLIND  # 字符串入参被归一为枚举
        assert anchor.score == 0.8

    def test_frozen_不可变(self):
        anchor = AnchorScore(**_ANCHOR)
        with pytest.raises(FrozenInstanceError):
            anchor.score = 0.1  # type: ignore[misc]

    @pytest.mark.parametrize("bad_score", [-0.1, 1.2, True, "0.8"])
    def test_score_越界或类型非法(self, bad_score):
        with pytest.raises(ValidationError, match="score"):
            AnchorScore(**{**_ANCHOR, "score": bad_score})

    @pytest.mark.parametrize("edge_score", [0.0, 1.0])
    def test_score_边界可取(self, edge_score):
        assert AnchorScore(**{**_ANCHOR, "score": edge_score}).score == edge_score

    def test_source_非法枚举值(self):
        with pytest.raises(ValidationError, match="source"):
            AnchorScore(**{**_ANCHOR, "source": "guess"})

    @pytest.mark.parametrize("bad_hash", ["AB" * 32, "ab" * 16, "zz" * 32])
    def test_artifact_hash_必须_64_位小写十六进制(self, bad_hash):
        with pytest.raises(ValidationError, match="artifact_hash"):
            AnchorScore(**{**_ANCHOR, "artifact_hash": bad_hash})

    @pytest.mark.parametrize("field", ["anchor_id", "node_id", "agent_id", "reviewer", "round_id"])
    def test_标识字段非空(self, field):
        with pytest.raises(ValidationError):
            AnchorScore(**{**_ANCHOR, field: ""})

    def test_平台真值来源(self):
        anchor = AnchorScore(**{**_ANCHOR, "source": "platform_truth"})
        assert anchor.source is AnchorSource.PLATFORM_TRUTH


class TestCalibrationRound状态机:
    def test_默认_open(self):
        assert CalibrationRound(**_ROUND).status is RoundStatus.OPEN

    def test_合法迁移链(self):
        round_ = CalibrationRound(**_ROUND)
        intake = round_.transition(RoundStatus.INTAKE)
        assert intake.status is RoundStatus.INTAKE
        assert round_.status is RoundStatus.OPEN  # frozen：原对象不变
        closed = intake.transition(RoundStatus.CLOSED)
        assert closed.status is RoundStatus.CLOSED

    def test_跳态与回退被拒(self):
        round_ = CalibrationRound(**_ROUND)
        with pytest.raises(ValidationError):
            round_.transition(RoundStatus.CLOSED)  # open 不可直达 closed
        with pytest.raises(ValidationError):
            round_.transition(RoundStatus.OPEN)  # 自迁移同样非法

    def test_closed_终态不可逆(self):
        closed = CalibrationRound(**_ROUND, status="closed")
        with pytest.raises(ValidationError):
            closed.transition(RoundStatus.INTAKE)

    def test_top_k_校验(self):
        with pytest.raises(ValidationError, match="top_k"):
            CalibrationRound(**{**_ROUND, "top_k": 0})


class TestPairingRecord:
    def test_合法构造(self):
        record = PairingRecord(
            anchor_id="a1",
            evaluator_key="proxy.aesthetic@1.0.0",
            anchor_score=0.8,
            auto_score=0.7,
        )
        assert record.excluded is False
        assert record.excluded_components == ()

    def test_自循环剔除注明分量(self):
        record = PairingRecord(
            anchor_id="a1",
            evaluator_key="human.platform_metrics@1.0.0",
            anchor_score=0.8,
            auto_score=None,
            excluded=True,
            excluded_components=("human.platform_metrics",),
            note="防自循环剔除",
        )
        assert record.excluded is True
        assert "human.platform_metrics" in record.excluded_components

    def test_得分域校验(self):
        with pytest.raises(ValidationError, match="anchor_score"):
            PairingRecord(anchor_id="a1", evaluator_key="e@1", anchor_score=1.5, auto_score=0.5)
        with pytest.raises(ValidationError, match="auto_score"):
            PairingRecord(anchor_id="a1", evaluator_key="e@1", anchor_score=0.5, auto_score=-0.1)


class TestBiasRecord:
    def test_连续口径(self):
        record = BiasRecord(
            evaluator_key="proxy.aesthetic@1.0.0",
            period="2026-W39",
            samples=5,
            mean_shift=0.2,
            pearson_r=0.62,
        )
        assert record.kendall_tau is None

    def test_judge_口径(self):
        record = BiasRecord(
            evaluator_key="judge.cinematic@1.0.0",
            period="2026-W39",
            samples=5,
            kendall_tau=0.7,
        )
        assert record.pearson_r is None

    def test_样本不足不产偏差值(self):
        record = BiasRecord(evaluator_key="e@1", period="2026-W39", samples=2, note="样本不足")
        assert record.mean_shift is None and record.pearson_r is None

    @pytest.mark.parametrize("field", ["pearson_r", "kendall_tau"])
    def test_相关系数域(self, field):
        with pytest.raises(ValidationError, match=field):
            BiasRecord(evaluator_key="e@1", period="2026-W39", samples=5, **{field: 1.5})

    def test_样本量非负整数(self):
        with pytest.raises(ValidationError, match="samples"):
            BiasRecord(evaluator_key="e@1", period="2026-W39", samples=-1)


class TestWeightProposal状态机:
    def test_默认_pending(self):
        assert WeightProposal(**_PROPOSAL).status is ProposalStatus.PENDING

    def test_confirm_生效(self):
        proposal = WeightProposal(**_PROPOSAL)
        confirmed = proposal.confirm(by="ops-user", at="2026-09-19T12:00:00Z")
        assert confirmed.status is ProposalStatus.CONFIRMED
        assert confirmed.confirmed_by == "ops-user"
        assert confirmed.confirmed_at == "2026-09-19T12:00:00Z"
        assert proposal.status is ProposalStatus.PENDING  # 原对象不变

    def test_shelve_搁置(self):
        shelved = WeightProposal(**_PROPOSAL).shelve()
        assert shelved.status is ProposalStatus.SHELVED

    def test_fail_回滚落失败(self):
        failed = WeightProposal(**_PROPOSAL).fail()
        assert failed.status is ProposalStatus.FAILED

    @pytest.mark.parametrize("terminal", ["confirmed", "shelved", "failed"])
    def test_终态不可逆(self, terminal):
        proposal = WeightProposal(**_PROPOSAL, status=terminal)
        with pytest.raises(ValidationError):
            proposal.confirm(by="x", at="2026-09-19T12:00:00Z")
        with pytest.raises(ValidationError):
            proposal.shelve()
        with pytest.raises(ValidationError):
            proposal.fail()

    def test_权重校验(self):
        with pytest.raises(ValidationError, match="candidate_weights"):
            WeightProposal(**{**_PROPOSAL, "candidate_weights": {"proxy.a": -0.1}})
        with pytest.raises(ValidationError, match="current_weights"):
            WeightProposal(**{**_PROPOSAL, "current_weights": {}})

    def test_ridge_lambda_非负(self):
        with pytest.raises(ValidationError, match="ridge_lambda"):
            WeightProposal(**{**_PROPOSAL, "ridge_lambda": -1.0})
