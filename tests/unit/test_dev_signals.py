"""开发 Agent 模拟数据源与确定性代理单测（功能 017 / US2 / T1727，先于实现编写）。

契约 C9（两代理合并陈述）/ SC-009：
- 驱动源 = `agents/dev/signals.py` 的**确定性模拟数据源**（历史同类型票房回归 + 舆情热度），
  参数化自 `dev.signals`（`baseline_usd_million` / `sensitivity` / `buzz_baseline` /
  可选 `fixtures`）：零 LLM、零网络、零边际成本、可回放，**同输入逐位一致**；
- 来源标注：模拟数据源产出的取值必须携带机读标注（`simulated=true` + "非真实商业数据"），
  **不得让模拟信号看起来像真实商业数据**；
- 数据源即行为口径（原则一）：`dev.signals` 任一参数变更 ⇒ 新 `evaluator_id@version`，
  历史节点的 `eval_breakdown` **永不重算**（版本随树冻结）；同 `id@version` 重复注册被拒。
"""

import json

import pytest

from agents.dev.artifact import (
    SIMULATED_NOTE,
    SIMULATED_SOURCE_IDS,
    TopicSlate,
    simulated_signal_sources,
)
from agents.dev.config import DevConfigError
from agents.dev.evaluators.buzz_heat import BuzzHeatEvaluator
from agents.dev.evaluators.genre_regression import GenreRegressionEvaluator
from agents.dev.signals import (
    SOURCE_BOX_OFFICE,
    SOURCE_BUZZ,
    SOURCE_IDS,
    SimulatedSignalSource,
)
from core.evaluators.base import ArtifactRef, EvaluatorKind
from core.evaluators.errors import RegistrationError
from core.evaluators.registry import Registry


def _source(source_id: str, parameters: dict) -> SimulatedSignalSource:
    return SimulatedSignalSource(source_id, parameters)


def _ref() -> ArtifactRef:
    return ArtifactRef(artifact_hash="ab" * 32)


def _changed(parameters: dict, **overrides) -> dict:
    return {**parameters, **overrides}


class Test确定性模拟数据源:
    def test_同输入逐位一致(self, dev_signal_params, topic_slate):
        """确定性是回放可打分的前提（原则一）：同参数同输入 → 取值逐位一致。"""
        for source_id in SOURCE_IDS:
            first = _source(source_id, dev_signal_params)
            second = _source(source_id, dev_signal_params)
            for genre in ("医疗悬疑", "都市犯罪", "古装权谋"):
                assert repr(first.box_office_usd_million(genre=genre)) == repr(
                    second.box_office_usd_million(genre=genre)
                )
                assert repr(first.buzz_heat(genre=genre)) == repr(second.buzz_heat(genre=genre))
            assert first.params_digest() == second.params_digest()

    def test_无夹具即基线口径(self, dev_signal_params):
        source = _source(SOURCE_BOX_OFFICE, dev_signal_params)
        assert source.box_office_usd_million(genre="医疗悬疑") == (
            dev_signal_params["baseline_usd_million"] * dev_signal_params["sensitivity"]
        )
        assert source.buzz_heat(genre="医疗悬疑") == (
            dev_signal_params["buzz_baseline"] * dev_signal_params["sensitivity"]
        )

    def test_夹具钉住逐类型取值(self, dev_signal_params):
        fixtures = {"genres": {"医疗悬疑": {"box_office_factor": 1.25, "buzz_factor": 1.2}}}
        source = _source(SOURCE_BOX_OFFICE, _changed(dev_signal_params, fixtures=fixtures))
        assert source.box_office_usd_million(genre="医疗悬疑") == pytest.approx(50.0)
        assert source.buzz_heat(genre="医疗悬疑") == pytest.approx(0.6)
        # 未列夹具的类型按基线口径（不臆造取值）
        assert source.box_office_usd_million(genre="公路喜剧") == pytest.approx(40.0)

    def test_热度夹在_0_1_且如实报原值(self, dev_signal_params):
        high = _source(
            SOURCE_BUZZ,
            _changed(dev_signal_params, fixtures={"genres": {"悬疑": {"buzz_factor": 5.0}}}),
        )
        low = _source(
            SOURCE_BUZZ,
            _changed(dev_signal_params, fixtures={"genres": {"悬疑": {"buzz_factor": 0.0}}}),
        )
        assert high.buzz_heat(genre="悬疑") == 1.0
        assert low.buzz_heat(genre="悬疑") == 0.0

    def test_参数缺失或非法即拒绝(self, dev_signal_params):
        with pytest.raises(DevConfigError):
            _source(SOURCE_BOX_OFFICE, {})
        with pytest.raises(DevConfigError):
            _source(SOURCE_BOX_OFFICE, {"baseline_usd_million": 40.0})
        with pytest.raises(DevConfigError):
            _source("box_office_db", dev_signal_params)  # 只认模拟源（真实渠道属 G3）
        with pytest.raises(DevConfigError):
            _source(SOURCE_BOX_OFFICE, _changed(dev_signal_params, fixtures={"genres": {"x": -1}}))


class Test来源标注:
    def test_每路取值携带机读标注(self, dev_signal_params):
        for source_id in SOURCE_IDS:
            payload = _source(source_id, dev_signal_params).provenance()
            assert payload["source"] == source_id
            assert payload["simulated"] is True
            assert "非真实商业数据" in payload["note"]
            assert payload["note"] == SIMULATED_NOTE
            assert len(payload["params_digest"]) == 12

    def test_来源标识与工件标注同源(self):
        """单一事实源：数据源标识与产物 `signal_sources` 的标注必须逐字对应。"""
        assert SOURCE_IDS == SIMULATED_SOURCE_IDS
        assert {SOURCE_BOX_OFFICE, SOURCE_BUZZ} == set(SIMULATED_SOURCE_IDS)

    def test_产物标注与分量标注逐字段一致(self, dev_config, topic_slate):
        """三处标注（产物 / 对比报告 / 判据材料）共用同一份载荷：不得各写一份口径。"""
        payload = topic_slate.to_dict()
        payload["signal_sources"] = list(simulated_signal_sources(dev_config.signals))
        slate = TopicSlate.from_dict(payload)
        artifact_payload = {item["source"]: item for item in slate.to_dict()["signal_sources"]}
        for evaluator_cls, source_id in (
            (GenreRegressionEvaluator, SOURCE_BOX_OFFICE),
            (BuzzHeatEvaluator, SOURCE_BUZZ),
        ):
            diagnostics = (
                evaluator_cls(_source(source_id, dev_config.signals))
                .evaluate(_ref(), {"artifact": slate})
                .diagnostics
            )
            expected = artifact_payload[source_id]
            assert {key: diagnostics[key] for key in expected} == expected

    def test_代理诊断含来源标注(self, dev_config, topic_slate):
        """SC-009：标注随分量诊断入 `eval_breakdown`（对比报告与判据材料的数据面）。"""
        evaluators = (
            GenreRegressionEvaluator(_source(SOURCE_BOX_OFFICE, dev_config.signals)),
            BuzzHeatEvaluator(_source(SOURCE_BUZZ, dev_config.signals)),
        )
        for evaluator in evaluators:
            result = evaluator.evaluate(_ref(), {"artifact": topic_slate})
            assert result.diagnostics["simulated"] is True
            assert "非真实商业数据" in result.diagnostics["note"]
            assert result.diagnostics["source"] in SIMULATED_SOURCE_IDS
            assert len(result.diagnostics["params_digest"]) == 12
            assert evaluator.spec.kind is EvaluatorKind.PROXY_MODEL
            assert evaluator.spec.deterministic is True
            assert evaluator.spec.cost_per_call == 0.0
            assert evaluator.spec.version.startswith("1.0.0+")
            assert 0.0 <= result.score <= 1.0

    def test_同输入重跑分量逐位一致(self, dev_config, topic_slate):
        """SC-004：同输入重跑各分量逐位一致。"""
        for evaluator_cls, source_id in (
            (GenreRegressionEvaluator, SOURCE_BOX_OFFICE),
            (BuzzHeatEvaluator, SOURCE_BUZZ),
        ):
            first = evaluator_cls(_source(source_id, dev_config.signals)).evaluate(
                _ref(), {"artifact": topic_slate}
            )
            second = evaluator_cls(_source(source_id, dev_config.signals)).evaluate(
                _ref(), {"artifact": topic_slate}
            )
            assert first.score == second.score
            assert json.dumps(first.diagnostics, sort_keys=True, ensure_ascii=False) == json.dumps(
                second.diagnostics, sort_keys=True, ensure_ascii=False
            )


class Test参数即版本:
    def test_参数变更即新版本(self, dev_signal_params):
        """数据源即行为口径（原则一）：任一参数变更 ⇒ 新 `evaluator_id@version`。"""
        base = _source(SOURCE_BOX_OFFICE, dev_signal_params)
        for changed in (
            _changed(dev_signal_params, baseline_usd_million=41.0),
            _changed(dev_signal_params, sensitivity=1.1),
            _changed(dev_signal_params, buzz_baseline=0.6),
            _changed(dev_signal_params, fixtures={"genres": {"医疗悬疑": {"buzz_factor": 1.1}}}),
        ):
            variant = _source(SOURCE_BOX_OFFICE, changed)
            assert variant.params_digest() != base.params_digest()
            old_evaluator = GenreRegressionEvaluator(base)
            new_evaluator = GenreRegressionEvaluator(variant)
            assert new_evaluator.spec.evaluator_id == old_evaluator.spec.evaluator_id
            assert new_evaluator.spec.version != old_evaluator.spec.version

    def test_版本携带数据源实现摘要(self, dev_signal_params):
        """实现摘要覆盖数据源模块：改取值函数同样换版本，不只有参数变更才算行为变更。"""
        from agents.dev import signals

        part = _source(SOURCE_BOX_OFFICE, dev_signal_params).version_part()
        assert signals.IMPLEMENTATION_DIGEST in part
        assert len(signals.IMPLEMENTATION_DIGEST) == 64

    def test_历史节点_breakdown_逐字节不变(self, dev_signal_params, topic_slate):
        """版本随树冻结：改参数只产生新键，旧 `eval_breakdown` 记录不被改写、不重算。"""
        old = GenreRegressionEvaluator(_source(SOURCE_BOX_OFFICE, dev_signal_params))
        result = old.evaluate(_ref(), {"artifact": topic_slate})
        recorded = {old.spec.key: {"score": result.score, "diagnostics": result.diagnostics}}
        frozen = json.dumps(recorded, sort_keys=True, ensure_ascii=False)

        new = GenreRegressionEvaluator(
            _source(SOURCE_BOX_OFFICE, _changed(dev_signal_params, baseline_usd_million=41.0))
        )
        new_result = new.evaluate(_ref(), {"artifact": topic_slate})
        assert new.spec.key not in recorded
        assert (
            new_result.diagnostics["predicted_usd_million"]
            != result.diagnostics["predicted_usd_million"]
        )
        assert json.dumps(recorded, sort_keys=True, ensure_ascii=False) == frozen

    def test_同键重复注册被拒而新版本可注册(self, dev_signal_params):
        """C9：同 `id@version` 重复注册被拒（行为变更必须升版本号，原则一）。"""
        registry = Registry()
        registry.register(GenreRegressionEvaluator(_source(SOURCE_BOX_OFFICE, dev_signal_params)))
        with pytest.raises(RegistrationError):
            registry.register(
                GenreRegressionEvaluator(_source(SOURCE_BOX_OFFICE, dev_signal_params))
            )
        registry.register(
            GenreRegressionEvaluator(
                _source(SOURCE_BOX_OFFICE, _changed(dev_signal_params, sensitivity=1.4))
            )
        )
        assert len(registry.list_all()) == 2
