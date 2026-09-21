"""功能 015 阶段 2（T1503）：`core/orchestration/models.py` 模型测试。

覆盖：状态枚举合法迁移、产物引用非空约束、指纹格式、候选判 0 理由、RunRecord 自洽
（run 状态与阶段状态一致、失败点其后阶段 skipped）、序列化往返。模型层承载可机检的
硬约束——非法数据在构造的第一刻就被拒绝（与 014 models 同款纪律）。
"""

import pytest

from core.evaluators.errors import ValidationError
from core.orchestration.models import (
    CandidateOutcome,
    ProductRef,
    RunRecord,
    RunStatus,
    StageInput,
    StageOutcome,
    StageSpec,
    StageState,
    StageStatus,
    combine_fingerprints,
    fingerprint_of,
)

FINGERPRINT_A = fingerprint_of("素材-A")
FINGERPRINT_B = fingerprint_of("素材-B")


def _product(kind: str = "script", ref: str = "tree:node-1") -> ProductRef:
    return ProductRef(kind=kind, ref=ref, content_hash=fingerprint_of(ref))


def _done(stage_id: str = "s1") -> StageState:
    return (
        StageState(stage_id=stage_id)
        .transition_to(StageStatus.RUNNING, at="2026-09-21T00:00:00+00:00")
        .transition_to(
            StageStatus.DONE,
            at="2026-09-21T00:00:01+00:00",
            products=(_product(),),
            cost_usd=0.5,
            input_fingerprint=FINGERPRINT_A,
        )
    )


class Test指纹:
    def test_同输入同指纹且为_blake3_十六进制(self):
        assert fingerprint_of("x") == fingerprint_of("x")
        assert len(fingerprint_of("x")) == 64
        assert all(ch in "0123456789abcdef" for ch in fingerprint_of("x"))

    def test_合并指纹顺序敏感且可复现(self):
        assert combine_fingerprints(FINGERPRINT_A, FINGERPRINT_B) == combine_fingerprints(
            FINGERPRINT_A, FINGERPRINT_B
        )
        assert combine_fingerprints(FINGERPRINT_A, FINGERPRINT_B) != combine_fingerprints(
            FINGERPRINT_B, FINGERPRINT_A
        )

    def test_合并指纹拒绝空分量(self):
        with pytest.raises(ValidationError):
            combine_fingerprints(FINGERPRINT_A, "")


class TestProductRef:
    def test_合法构造与往返(self):
        product = _product()
        assert ProductRef.from_dict(product.to_dict()) == product

    @pytest.mark.parametrize("kind,ref", [("", "r"), ("k", ""), (None, "r")])
    def test_字段非空约束(self, kind, ref):
        with pytest.raises(ValidationError):
            ProductRef(kind=kind, ref=ref, content_hash=FINGERPRINT_A)

    def test_指纹必须为_blake3_十六进制(self):
        with pytest.raises(ValidationError):
            ProductRef(kind="k", ref="r", content_hash="not-a-fingerprint")
        with pytest.raises(ValidationError):
            ProductRef(kind="k", ref="r", content_hash="A" * 64)  # 大写非法


class TestStageSpec:
    def test_默认依赖为空且可调用入口(self):
        spec = StageSpec(stage_id="s1", entrypoint=lambda stage_input: StageOutcome())
        assert spec.depends_on == ()
        assert spec.handoff is None
        assert spec.entrypoint(StageInput(stage_id="s1", form="f", upstream={})) == StageOutcome()

    @pytest.mark.parametrize("stage_id", ["", "S1", "1s", "s-1", None])
    def test_stage_id_格式约束(self, stage_id):
        with pytest.raises(ValidationError):
            StageSpec(stage_id=stage_id, entrypoint=lambda stage_input: StageOutcome())

    def test_依赖项不得为空且不得自依赖(self):
        with pytest.raises(ValidationError):
            StageSpec(stage_id="s1", entrypoint=lambda i: StageOutcome(), depends_on=("",))
        with pytest.raises(ValidationError):
            StageSpec(stage_id="s1", entrypoint=lambda i: StageOutcome(), depends_on=("s1",))

    def test_执行入口必须可调用(self):
        with pytest.raises(ValidationError):
            StageSpec(stage_id="s1", entrypoint="not-callable")


class TestCandidateOutcome:
    def test_判零候选必须给理由(self):
        with pytest.raises(ValidationError):
            CandidateOutcome(candidate_id="c1", score=0.0)
        zero = CandidateOutcome(candidate_id="c1", score=0.0, reasons=("门禁违规",))
        assert zero.reasons == ("门禁违规",)

    @pytest.mark.parametrize("score", [-0.1, 1.1])
    def test_分数区间约束(self, score):
        with pytest.raises(ValidationError):
            CandidateOutcome(candidate_id="c1", score=score, reasons=("理由",))

    def test_非空理由项(self):
        with pytest.raises(ValidationError):
            CandidateOutcome(candidate_id="c1", score=0.0, reasons=("",))


class TestStageState:
    def test_合法迁移链_pending_running_done(self):
        state = StageState(stage_id="s1")
        assert state.status is StageStatus.PENDING
        assert state.can_transition_to(StageStatus.RUNNING)
        running = state.transition_to(StageStatus.RUNNING, at="2026-09-21T00:00:00+00:00")
        assert running.attempts == 1 and running.started_at == "2026-09-21T00:00:00+00:00"
        done = running.transition_to(
            StageStatus.DONE,
            at="2026-09-21T00:00:02+00:00",
            products=(_product(),),
            cost_usd=1.5,
            input_fingerprint=FINGERPRINT_A,
        )
        assert done.status is StageStatus.DONE
        assert done.cost_usd == 1.5 and done.failure_reason == ""

    def test_失败迁移要求理由与失败候选(self):
        running = StageState(stage_id="s1").transition_to(StageStatus.RUNNING, at="t0")
        with pytest.raises(ValidationError):
            running.transition_to(StageStatus.FAILED, at="t1")
        failed = running.transition_to(
            StageStatus.FAILED,
            at="t1",
            failure_reason="全部候选判 0",
            candidates=(CandidateOutcome(candidate_id="c1", score=0.0, reasons=("门禁违规",)),),
        )
        assert failed.status is StageStatus.FAILED
        assert failed.candidates[0].reasons == ("门禁违规",)

    def test_非法迁移被拒(self):
        done = _done()
        assert not done.can_transition_to(StageStatus.RUNNING)
        with pytest.raises(ValidationError):
            done.transition_to(StageStatus.RUNNING, at="t2")
        with pytest.raises(ValidationError):
            StageState(stage_id="s1").transition_to(StageStatus.DONE, at="t1")  # 未经 RUNNING

    def test_完成态必须带产物引用(self):
        running = StageState(stage_id="s1").transition_to(StageStatus.RUNNING, at="t0")
        with pytest.raises(ValidationError):
            running.transition_to(StageStatus.DONE, at="t1")
        with pytest.raises(ValidationError):
            StageState(stage_id="s1", status=StageStatus.DONE, input_fingerprint=FINGERPRINT_A)

    def test_输出指纹与成本约束(self):
        with pytest.raises(ValidationError):
            StageState(stage_id="s1", input_fingerprint="bad")
        with pytest.raises(ValidationError):
            StageState(stage_id="s1", cost_usd=-1.0)
        assert StageState(stage_id="s1", input_fingerprint=FINGERPRINT_A).status is (
            StageStatus.PENDING
        )

    def test_续跑重排队_仅对失败与跳过成立且计数累计(self):
        running = StageState(stage_id="s1").transition_to(StageStatus.RUNNING, at="t0")
        failed = running.transition_to(StageStatus.FAILED, at="t1", failure_reason="r")
        requeued = failed.requeue()
        assert requeued.status is StageStatus.PENDING
        assert requeued.attempts == 1  # 调用计数累计（机检"不重跑"的依据）
        assert requeued.failure_reason == "" and requeued.finished_at is None
        skipped = StageState(stage_id="s1", status=StageStatus.SKIPPED)
        assert skipped.requeue().status is StageStatus.PENDING
        with pytest.raises(ValidationError):
            _done().requeue()  # 已完成阶段不得重跑
        with pytest.raises(ValidationError):
            StageState(stage_id="s1").requeue()  # pending 无需重排队

    def test_序列化往返(self):
        done = _done()
        assert StageState.from_dict(done.to_dict()) == done


class TestStageOutcome:
    def test_成本非负(self):
        with pytest.raises(ValidationError):
            StageOutcome(cost_usd=-0.1)
        assert StageOutcome().products == () and StageOutcome().detail == {}

    def test_列表自动升为元组(self):
        outcome = StageOutcome(products=[_product()])
        assert outcome.products == (_product(),)


class TestRunRecord:
    def _record(self, stages, **overrides):
        fields = {
            "run_id": "run-1",
            "form": "movie",
            "config_fingerprint": FINGERPRINT_A,
            "input_fingerprint": FINGERPRINT_B,
            "stages": tuple(stages),
            "started_at": "2026-09-21T00:00:00+00:00",
        }
        fields.update(overrides)
        return RunRecord(**fields)

    def test_运行中记录(self):
        record = self._record([StageState(stage_id="s1"), StageState(stage_id="s2")])
        assert record.status is RunStatus.RUNNING
        assert record.stage("s1").status is StageStatus.PENDING
        assert record.stage_ids == ("s1", "s2")
        with pytest.raises(ValidationError):
            record.stage("missing")

    def test_全完成才可置_done(self):
        with pytest.raises(ValidationError):
            self._record(
                [_done("s1"), StageState(stage_id="s2")],
                status=RunStatus.DONE,
                finished_at="2026-09-21T00:01:00+00:00",
            )
        record = self._record(
            [_done("s1"), _done("s2")],
            status=RunStatus.DONE,
            finished_at="2026-09-21T00:01:00+00:00",
        )
        assert record.completed_stages == ("s1", "s2")
        assert record.total_cost_usd == 1.0
        with pytest.raises(ValidationError):
            self._record([_done("s1")], status=RunStatus.DONE)  # done 必须带结束时间

    def test_失败记录要求失败点与其后阶段_skipped(self):
        failed = (
            StageState(stage_id="s2")
            .transition_to(StageStatus.RUNNING, at="t0")
            .transition_to(StageStatus.FAILED, at="t1", failure_reason="候选全败")
        )
        record = self._record(
            [
                _done("s1"),
                failed,
                StageState(stage_id="s3", status=StageStatus.SKIPPED),
            ],
            status=RunStatus.FAILED,
            failure_stage="s2",
            failure_reason="候选全败",
            finished_at="2026-09-21T00:02:00+00:00",
        )
        assert record.stage("s3").status is StageStatus.SKIPPED
        with pytest.raises(ValidationError):
            self._record(
                [_done("s1"), failed, StageState(stage_id="s3", status=StageStatus.PENDING)],
                status=RunStatus.FAILED,
                failure_stage="s2",
                failure_reason="候选全败",
            )
        with pytest.raises(ValidationError):
            self._record(
                [_done("s1"), failed],
                status=RunStatus.FAILED,
                failure_reason="候选全败",
            )  # 缺失败点
        with pytest.raises(ValidationError):
            self._record(
                [_done("s1"), failed],
                status=RunStatus.FAILED,
                failure_stage="s1",  # 失败点必须指向 failed 阶段
                failure_reason="候选全败",
            )

    def test_字段与唯一性约束(self):
        with pytest.raises(ValidationError):
            self._record([_done("s1"), _done("s1")])  # stage_id 不得重复
        with pytest.raises(ValidationError):
            self._record([], form="")  # stages 非空 + form 非空
        with pytest.raises(ValidationError):
            self._record([_done("s1")], config_fingerprint="bad")
        with pytest.raises(ValidationError):
            self._record([_done("s1")], status=RunStatus.DONE)  # running 不得带结束时间

    def test_阶段替换与序列化往返(self):
        record = self._record([StageState(stage_id="s1")])
        updated = record.with_stage(_done("s1"))
        assert updated.stage("s1").status is StageStatus.DONE
        assert record.stage("s1").status is StageStatus.PENDING  # frozen：原记录不变
        assert RunRecord.from_dict(updated.to_dict()) == updated

    def test_形态值如实透传(self):
        record = self._record([StageState(stage_id="s1")], form="shortdrama")
        assert record.to_dict()["form"] == "shortdrama"
