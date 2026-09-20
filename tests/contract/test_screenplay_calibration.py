"""C16 周校准接入契约（功能 009 / T932，先于实现编写）。

- `build_blind_list(agent_id="screenplay")` 正常产出（仅 promo 被特判拒绝）；
  **盲评对象 = 大纲阶段 top-k**——010 侧以**通用观测槽精确匹配**机制消费
  `BLIND_REVIEW_OBSERVATION_MATCH = {"stage": "outline"}`（不特化任何 Agent）；
  零泄露键白名单回归（清单不含 score/eval_breakdown）；
- 信度报告含 screenplay judge 条目（相关系数/样本量/达标标记）；
- promo 特判回归不破；dreaming 与 010 源码无 screenplay 特判（泛化缺口 = 0）。
"""

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.screenplay.upgrade_evidence import BLIND_REVIEW_OBSERVATION_MATCH
from core.calibration.anchors import intake_anchors
from core.calibration.config import CalibrationConfig
from core.calibration.report import build_report
from core.calibration.rounds import close_round, iso_week_label
from core.calibration.selection import BLIND_LIST_KEYS, build_blind_list, load_round
from core.evaluators.errors import ValidationError
from core.tree.models import CostRecord, NodeStatus, new_id

REPO_ROOT = Path(__file__).resolve().parents[2]
_MOVIE_YAML = REPO_ROOT / "configs" / "movie.yaml"
_PERIOD = ("2026-09-14", "2026-09-20")  # 落入 ISO 周 2026-W38
_JUDGE_ID = "judge.dramatic_tension"
_GATE_ID = "rule.beat_structure"

_BASE_TS = datetime(2026, 9, 15, tzinfo=UTC).timestamp()  # 周期内基准时刻
# 节点切片：大纲阶段两枚（0.9 / 0.8）、分场与剧本各一枚（分数更高但不该入盲评清单）
_STAGE_SCORES = (
    ("outline", 0.90, _BASE_TS),
    ("outline", 0.80, _BASE_TS + 2),
    ("outline", 0.70, _BASE_TS + 4),
    ("scenes", 0.95, _BASE_TS + 6),
    ("script", 0.85, _BASE_TS + 8),
)


@pytest.fixture()
def calibration_config():
    return CalibrationConfig.from_yaml(_MOVIE_YAML)


@pytest.fixture()
def screenplay_tree(tree_store, make_tree, make_node):
    """剧本轮次树：root + 四枚节点（含大纲/分场/剧本三阶段观测槽与七分量明细）。"""
    tree = make_tree(
        agent_id="screenplay",
        project_id="screenplay-calibration",
        config_snapshot={
            "evaluator_weights": {"rule.beat_structure": "gate", "judge.dramatic_tension": 0.5},
            "observation_fields": ["gen_params", "stage", "job_id"],
        },
    )
    tree_store.create_tree(tree)
    tree_store.append_node(
        make_node(
            node_id=tree.root_id,
            tree_id=tree.tree_id,
            agent_id="screenplay",
            eval_breakdown={},
            score=0.0,
            status=NodeStatus.EVALUATED,
            created_at=_BASE_TS,
        )
    )
    for stage, score, created_at in _STAGE_SCORES:
        tree_store.append_node(
            make_node(
                node_id=new_id(),
                tree_id=tree.tree_id,
                parent_id=tree.root_id,
                depth=1,
                agent_id="screenplay",
                observation_context={
                    "gen_params": {"stage": stage, "plan_digest": f"digest-{stage}-{score}"},
                    "stage": stage,
                    "job_id": f"job-{stage}-{score}",
                },
                eval_breakdown={
                    f"{_GATE_ID}@1.0.0": {"score": 1.0},
                    "proxy.entity_consistency@1.0.0": {"score": score},
                    f"{_JUDGE_ID}@1.0.0+jabcdef12": {"score": score},
                },
                score=score,
                cost=CostRecord(llm_calls=1),
                status=NodeStatus.EVALUATED,
                created_at=created_at,
            )
        )
    return tree


class Test盲评清单:
    def test_只取大纲阶段_top_k(
        self, tree_store, screenplay_tree, calibration_data_dir, calibration_config
    ):
        """C16 场景 1：盲评对象 = 大纲阶段 top-k（分场/剧本节点分数更高也不入选）。"""
        round_ = build_blind_list(
            tree_store,
            agent_id="screenplay",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=3,
            data_dir=calibration_data_dir,
            observation_match=BLIND_REVIEW_OBSERVATION_MATCH,
        )
        assert round_.agent_id == "screenplay"
        nodes = [tree_store.get_node(node_id) for node_id in round_.node_ids]
        assert len(nodes) == 3  # 大纲阶段节点 3 枚（top_k=3 恰好取满）
        assert {node.observation_context["stage"] for node in nodes} == {"outline"}
        assert [node.score for node in nodes] == [0.90, 0.80, 0.70]  # 降序取 top
        assert round_.note == ""  # 取满 top_k，样本充足

    def test_清单零泄露键白名单(
        self, tree_store, screenplay_tree, calibration_data_dir, calibration_config
    ):
        round_ = build_blind_list(
            tree_store,
            agent_id="screenplay",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=3,
            data_dir=calibration_data_dir,
            observation_match=BLIND_REVIEW_OBSERVATION_MATCH,
        )
        _, blind_list = load_round(calibration_data_dir, round_.round_id)
        for entry in blind_list:
            assert set(entry) == BLIND_LIST_KEYS  # {node_id, artifact_hash, round_id}
        payload = (
            calibration_data_dir / "rounds" / "screenplay" / f"{round_.round_id}.json"
        ).read_text(encoding="utf-8")
        assert "score" not in payload and "eval_breakdown" not in payload

    def test_不过滤则可取三阶段(
        self, tree_store, screenplay_tree, calibration_data_dir, calibration_config
    ):
        """通用机制：未给观测匹配时按得分取全阶段 top-k（剧本线口径由常量表达）。"""
        round_ = build_blind_list(
            tree_store,
            agent_id="screenplay",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=3,
            data_dir=calibration_data_dir,
        )
        stages = {
            tree_store.get_node(node_id).observation_context["stage"] for node_id in round_.node_ids
        }
        assert stages == {"scenes", "outline", "script"}

    def test_观测匹配形态非法拒绝(
        self, tree_store, screenplay_tree, calibration_data_dir, calibration_config
    ):
        with pytest.raises(ValidationError, match="observation_match"):
            build_blind_list(
                tree_store,
                agent_id="screenplay",
                period_start=_PERIOD[0],
                period_end=_PERIOD[1],
                top_k=3,
                data_dir=calibration_data_dir,
                observation_match=["stage"],
            )


class Test信度报告含_judge_条目:
    def test_端到端_盲评_录入_收口_报告(
        self,
        tree_store,
        screenplay_tree,
        anchors_engine,
        calibration_data_dir,
        calibration_config,
    ):
        """C16 场景 2：收口后信度报告含 screenplay judge 条目（相关系数/样本量/达标标记）。"""
        round_ = build_blind_list(
            tree_store,
            agent_id="screenplay",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=3,
            data_dir=calibration_data_dir,
            observation_match=BLIND_REVIEW_OBSERVATION_MATCH,
        )
        entries = [
            {"node_id": node_id, "score": score, "reviewer": "sunqi"}
            for node_id, score in zip(round_.node_ids, (0.95, 0.60, 0.55), strict=True)
        ]
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
        assert accepted == 3  # 人评与自动分单调同向（judge τ = 1）
        with anchors_engine.begin() as conn:
            close_round(
                tree_store,
                conn,
                calibration_data_dir,
                round_id=round_.round_id,
                config=calibration_config,
            )
        period = iso_week_label(_PERIOD[1])
        report = build_report(
            calibration_data_dir, period, target=calibration_config.reliability_target
        )
        screenplay_entries = report["agents"]["screenplay"]
        judge_keys = [key for key in screenplay_entries if key.startswith(_JUDGE_ID)]
        assert len(judge_keys) == 1
        entry = screenplay_entries[judge_keys[0]]
        assert entry["samples"] == 3
        assert entry["kendall_tau"] == pytest.approx(1.0)  # judge 走 Kendall τ 口径
        assert entry["meets_target"] is True  # ≥ calibration.reliability_target
        assert report["alerts"] == []

    def test_信度报告落盘(self, calibration_data_dir, calibration_config):
        report = build_report(
            calibration_data_dir, "2026-W38", target=calibration_config.reliability_target
        )
        path = calibration_data_dir / "reports" / "2026-W38.json"
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8")) == report


class Testpromo特判回归:
    def test_promo_不盲评(self, tree_store, calibration_data_dir, calibration_config):
        with pytest.raises(ValidationError, match="promo"):
            build_blind_list(
                tree_store,
                agent_id="promo",
                period_start=_PERIOD[0],
                period_end=_PERIOD[1],
                top_k=3,
                data_dir=calibration_data_dir,
            )


class Test无特判静态证明:
    def test_dreaming_与_010_无_screenplay_特判(self):
        """泛化缺口 = 0：dreaming 与 010 模块源码不得出现 screenplay 字面量。"""
        import core.calibration.pairing
        import core.calibration.refit
        import core.calibration.report
        import core.calibration.rounds
        import core.calibration.selection
        import dreaming.candidates
        import dreaming.digest
        import dreaming.lineage
        import dreaming.pipeline

        modules = [
            dreaming.pipeline,
            dreaming.candidates,
            dreaming.digest,
            dreaming.lineage,
            core.calibration.selection,
            core.calibration.pairing,
            core.calibration.rounds,
            core.calibration.report,
            core.calibration.refit,
        ]
        for module in modules:
            source = inspect.getsource(module)
            assert "screenplay" not in source, f"{module.__name__} 出现 screenplay 特判"

    def test_观测匹配为通用机制(self):
        """010 的观测过滤不引入 Agent 专属分支（参数化键值全等）。"""
        from core.calibration import selection

        source = inspect.getsource(selection)
        assert "observation_match" in source  # 通用参数化过滤
        assert "screenplay" not in source  # 剧本线口径不出现在 010
        assert "observation_context.get(key) == value" in source  # 键值全等（无 Agent 分支）
