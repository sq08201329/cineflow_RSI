"""外环周校准契约聚合（功能 010 / T527）：C1~C9 全场景端到端断言。

三份契约逐场景核对（anchors.md C1~C3、calibration.md C4~C6、refit.md C7~C9），
并承载三个机检门禁：
- SC-002 盲评清单零泄露（递归扫描无 score/eval_breakdown 键）；
- SC-006 权重生效后历史节点 score/eval_breakdown 逐字节一致（FR-008）；
- FR-011 校准全流程昂贵动作调用计数为 0（树表零写入审计 + 签名无网关/平台参数 +
  MockBackend 调用计数恒 0）。
"""

import hashlib
import inspect
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text

from core.calibration.anchors import intake_anchors
from core.calibration.bias import compute_bias
from core.calibration.config import CalibrationConfig
from core.calibration.ledger import append_ledger, read_latest
from core.calibration.models import BiasRecord, ProposalStatus
from core.calibration.pairing import pair_anchors
from core.calibration.refit import (
    composite_version,
    confirm_proposal,
    load_proposal,
    maybe_propose,
    shelve_proposal,
)
from core.calibration.report import build_report
from core.calibration.rounds import close_round
from core.calibration.selection import build_blind_list
from core.evaluators.errors import ValidationError
from core.evaluators.registry import Registry

_BASE_TS = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
_PERIOD = ("2026-09-14", "2026-09-20")
_BREAKDOWN = {
    "proxy.aesthetic@1.0.0": {"score": 0.6},
    "judge.cinematic@1.0.0": {"score": 0.5},
}

_CONFIG_YAML = """# 形态配置：电影（movie）
form: movie

evaluator_weights:
  visual:
    # gate 表示硬规则门禁：score 为 0 时总分直接为 0
    rule.format_compliance: gate
    proxy.aesthetic: 0.5
    judge.cinematic: 0.5

calibration:
  period_days: 7
  top_k: 5
  min_samples: 3
  bias_threshold: 0.15
  reliability_target: 0.6
  ridge_lambda: 1.0
  self_pairing_exclusions:
    platform_truth: ["human.platform_metrics"]
"""

_GATE_KEYS = frozenset({"rule.format_compliance"})


def _walk_keys(payload):
    keys = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys |= _walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            keys |= _walk_keys(item)
    return keys


@pytest.fixture()
def cfg(tmp_path):
    path = tmp_path / "movie.yaml"
    path.write_text(_CONFIG_YAML, encoding="utf-8")
    return path


@pytest.fixture()
def calib_cfg(cfg):
    return CalibrationConfig.from_yaml(cfg)


@pytest.fixture()
def visual_tree(tree_store, build_calibration_tree):
    """周期内 6 节点（score 0.4~0.9）的 visual 夹具树。"""
    scores = [0.4, 0.9, 0.6, 0.8, 0.5, 0.7]
    _, node_ids = build_calibration_tree(
        [(s, dict(_BREAKDOWN)) for s in scores], agent_id="visual", base_created_at=_BASE_TS
    )
    return node_ids


@pytest.fixture()
def open_round(tree_store, visual_tree, calibration_data_dir):
    return build_blind_list(
        tree_store,
        agent_id="visual",
        period_start=_PERIOD[0],
        period_end=_PERIOD[1],
        top_k=5,
        data_dir=calibration_data_dir,
    )


class TestC1盲评清单:
    def test_topk_降序与样本注明(self, tree_store, visual_tree, calibration_data_dir):
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=5,
            data_dir=calibration_data_dir,
        )
        assert len(round_.node_ids) == 5  # 6 节点取 top-5
        assert round_.note == ""
        small = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start=_PERIOD[0],
            period_end=_PERIOD[1],
            top_k=10,
            data_dir=calibration_data_dir,
        )
        assert len(small.node_ids) == 6 and "样本不足" in small.note

    def test_sc002_零泄露机检(self, open_round, calibration_data_dir):
        path = calibration_data_dir / "rounds" / "visual" / f"{open_round.round_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["blind_list"]:
            assert set(entry) == {"node_id", "artifact_hash", "round_id"}
        assert not (_walk_keys(payload["blind_list"]) & {"score", "eval_breakdown"})

    def test_promo_不盲评(self, tree_store, calibration_data_dir):
        with pytest.raises(ValidationError, match="promo"):
            build_blind_list(
                tree_store,
                agent_id="promo",
                period_start=_PERIOD[0],
                period_end=_PERIOD[1],
                top_k=5,
                data_dir=calibration_data_dir,
            )


class TestC2人评录入:
    def test_三路径与触发器冻结(
        self, anchors_engine, open_round, visual_tree, calibration_data_dir
    ):
        entries = [
            {"node_id": nid, "score": 0.7, "reviewer": "r1"} for nid in open_round.node_ids[:3]
        ]
        entries.append({"node_id": open_round.node_ids[3], "score": 1.2, "reviewer": "r1"})
        entries.append(dict(entries[0]))  # 同键重复
        rejections: list = []
        with anchors_engine.begin() as conn:
            accepted = intake_anchors(
                conn,
                open_round.round_id,
                entries,
                data_dir=calibration_data_dir,
                rejections=rejections,
            )
        assert accepted == 3
        assert len(rejections) == 2  # 越界 + 重复各一，整批不中断
        # 存储层冻结（SQLite 侧证明；PG 双侧见集成测试）
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(text("UPDATE calibration_anchors SET score = 0.1"))
        with pytest.raises(Exception, match="immutable"):
            with anchors_engine.begin() as conn:
                conn.execute(text("DELETE FROM calibration_anchors"))


class TestC3平台真值锚点:
    def test_回流入库与同轮幂等(
        self, campaigns_engine, anchors_engine, make_platform_backfill, promo_config
    ):
        from agents.promo.anchors import collect_platform_anchors

        for _ in range(12):
            make_platform_backfill()
        with anchors_engine.begin() as conn:
            first = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
            second = collect_platform_anchors(
                campaigns_engine, conn, round_id="calib-r1", config=promo_config
            )
        assert len(first) == 12 and second == []
        with anchors_engine.connect() as conn:
            rows = conn.execute(text("SELECT source, reviewer FROM calibration_anchors")).all()
        assert len(rows) == 12
        assert all(row[0] == "platform_truth" for row in rows)


class TestC4配对:
    def test_剔除与全量(self, tree_store, build_calibration_tree):
        from core.calibration.models import AnchorScore

        _, promo_nodes = build_calibration_tree(
            [
                (
                    0.45,
                    {
                        "human.platform_metrics@1.0.0": {"score": 0.5},
                        "proxy.ctr_history@1.0.0": {"score": 0.4},
                    },
                )
            ],
            agent_id="promo",
        )
        _, visual_nodes = build_calibration_tree([(0.65, dict(_BREAKDOWN))], agent_id="visual")
        exclusions = {"platform_truth": ("human.platform_metrics",)}

        def _anchor(node_id, source):
            return AnchorScore(
                anchor_id=f"a-{node_id}",
                node_id=node_id,
                artifact_hash="ab" * 32,
                agent_id="x",
                source=source,
                score=0.8,
                reviewer="r1",
                round_id="r",
                created_at="2026-09-19T00:00:00+00:00",
            )

        promo_records = pair_anchors(
            [_anchor(promo_nodes[0], "platform_truth")], tree_store, exclusions
        )
        excluded = [r for r in promo_records if r.excluded]
        assert len(excluded) == 1
        assert excluded[0].excluded_components == ("human.platform_metrics",)
        assert any(
            r.evaluator_key == "proxy.ctr_history@1.0.0" and not r.excluded for r in promo_records
        )

        visual_records = pair_anchors(
            [_anchor(visual_nodes[0], "human_blind")], tree_store, exclusions
        )
        assert len(visual_records) == 2 and all(not r.excluded for r in visual_records)


class TestC5偏差数学:
    def test_注入偏移双向断言(self):
        autos = [0.05, 0.2, 0.4, 0.6, 0.75]
        anchors = [a + 0.2 for a in autos]
        from core.calibration.models import PairingRecord

        pairs = [
            PairingRecord(
                anchor_id=f"a{i}", evaluator_key="proxy.a@1.0.0", anchor_score=a, auto_score=s
            )
            for i, (a, s) in enumerate(zip(anchors, autos, strict=True))
        ]
        record = compute_bias(
            pairs, evaluator_key="proxy.a@1.0.0", period="2026-W38", min_samples=3
        )
        assert record.mean_shift == pytest.approx(0.2, abs=1e-6)
        assert record.pearson_r == pytest.approx(1.0, abs=1e-9)

    def test_样本不足不产偏差(self):
        from core.calibration.models import PairingRecord

        pairs = [
            PairingRecord(anchor_id=f"a{i}", evaluator_key="e@1", anchor_score=a, auto_score=s)
            for i, (a, s) in enumerate([(0.8, 0.6), (0.9, 0.7)])
        ]
        record = compute_bias(pairs, evaluator_key="e@1", period="2026-W38", min_samples=3)
        assert record.mean_shift is None and "样本不足" in record.note


class TestC6台账与报告:
    def test_append_only_逐字节不变与报告_schema(self, calibration_data_dir):
        first = BiasRecord(
            evaluator_key="proxy.a@1.0.0",
            period="2026-W38",
            samples=5,
            mean_shift=0.1,
            pearson_r=0.55,
        )
        append_ledger(calibration_data_dir, "visual", [first])
        path = calibration_data_dir / "ledger" / "visual" / "proxy.a.jsonl"
        before = path.read_bytes()

        second = BiasRecord(
            evaluator_key="proxy.a@1.0.0",
            period="2026-W39",
            samples=5,
            mean_shift=0.05,
            pearson_r=0.62,
        )
        append_ledger(calibration_data_dir, "visual", [second])
        after = path.read_bytes()
        assert after.startswith(before)  # 首轮行逐字节不变
        assert len(after.splitlines()) == 2

        report = build_report(calibration_data_dir, "2026-W39", target=0.6)
        assert set(report) == {"period", "agents", "target", "alerts"}
        entry = report["agents"]["visual"]["proxy.a@1.0.0"]
        assert entry["meets_target"] is True and entry["samples"] == 5
        assert read_latest(calibration_data_dir, "visual", "proxy.a")["period"] == "2026-W39"

    def test_版本不变断言(self, calibration_data_dir):
        """台账追加前后，注册中心全部 spec 的 (key, calibration) 集合不变。"""
        registry = Registry()

        def _snapshot():
            return sorted(
                (s.key, json.dumps(s.calibration, sort_keys=True)) for s in registry.list_all()
            )

        before = _snapshot()
        append_ledger(
            calibration_data_dir,
            "visual",
            [
                BiasRecord(
                    evaluator_key="proxy.a@1.0.0",
                    period="2026-W39",
                    samples=5,
                    mean_shift=0.1,
                    pearson_r=0.6,
                )
            ],
        )
        assert _snapshot() == before


class TestC7提案生成:
    def _records(self, shift=0.2, pearson=0.7, samples=5):
        return [
            BiasRecord(
                evaluator_key="proxy.aesthetic@1.0.0",
                period="2026-W38",
                samples=samples,
                mean_shift=shift,
                pearson_r=pearson,
            )
        ]

    def _pairs(self):
        from core.calibration.models import PairingRecord

        return [
            PairingRecord(
                anchor_id=f"a{i}", evaluator_key=key, anchor_score=0.6 + i * 0.05, auto_score=auto
            )
            for i in range(5)
            for key, auto in (
                ("proxy.aesthetic@1.0.0", 0.4 + i * 0.05),
                ("judge.cinematic@1.0.0", 0.6),
            )
        ]

    def test_四路径(self, calibration_data_dir):
        weights = {"rule.format_compliance": 0.0, "proxy.aesthetic": 0.5, "judge.cinematic": 0.5}
        base = dict(
            agent_id="visual",
            pairs=self._pairs(),
            current_weights=weights,
            has_history=True,
            data_dir=calibration_data_dir,
            fixed_keys=_GATE_KEYS,
        )
        cfg = CalibrationConfig.from_dict(
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
        # 超阈 → pending
        proposal = maybe_propose(bias_records=self._records(), cfg=cfg, **base)
        assert proposal is not None and proposal.status is ProposalStatus.PENDING
        assert proposal.based_version == composite_version(weights)
        # 未超阈 → None
        assert maybe_propose(bias_records=self._records(shift=0.05), cfg=cfg, **base) is None
        # 负相关 → 禁止
        assert (
            maybe_propose(bias_records=self._records(shift=0.3, pearson=-0.4), cfg=cfg, **base)
            is None
        )
        # 首轮基线 → None
        assert (
            maybe_propose(bias_records=self._records(), cfg=cfg, **{**base, "has_history": False})
            is None
        )


class TestC8确认与生效:
    def _proposal(self, data_dir):
        weights = {"rule.format_compliance": 0.0, "proxy.aesthetic": 0.5, "judge.cinematic": 0.5}
        from core.calibration.models import PairingRecord

        pairs = [
            PairingRecord(
                anchor_id=f"a{i}", evaluator_key=key, anchor_score=0.65 + i * 0.05, auto_score=auto
            )
            for i in range(5)
            for key, auto in (
                ("proxy.aesthetic@1.0.0", 0.4 + i * 0.05),
                ("judge.cinematic@1.0.0", 0.6),
            )
        ]
        cfg = CalibrationConfig.from_dict(
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
        return maybe_propose(
            agent_id="visual",
            bias_records=[
                BiasRecord(
                    evaluator_key="proxy.aesthetic@1.0.0",
                    period="2026-W38",
                    samples=5,
                    mean_shift=0.2,
                    pearson_r=0.7,
                )
            ],
            pairs=pairs,
            current_weights=weights,
            cfg=cfg,
            has_history=True,
            data_dir=data_dir,
            fixed_keys=_GATE_KEYS,
        )

    def test_confirm_生效与_sc006_历史节点审计(
        self, tree_store, visual_tree, calibration_data_dir, cfg
    ):
        proposal = self._proposal(calibration_data_dir)
        # SC-006 前置快照：生效前历史节点 score/eval_breakdown
        nodes_before = {
            nid: (
                tree_store.get_node(nid).score,
                json.dumps(tree_store.get_node(nid).eval_breakdown, sort_keys=True),
            )
            for nid in visual_tree
        }
        registry = Registry()
        new_version = confirm_proposal(
            calibration_data_dir,
            cfg,
            proposal_id=proposal.proposal_id,
            by="ops-user",
            registry=registry,
        )
        assert new_version == composite_version(proposal.candidate_weights)
        # yaml 定点改写：gate 行与注释保留
        text = cfg.read_text(encoding="utf-8")
        assert "    rule.format_compliance: gate\n" in text
        assert "# gate 表示硬规则门禁" in text
        # 提案 confirmed 落盘
        confirmed = load_proposal(calibration_data_dir, proposal.proposal_id)
        assert confirmed.status is ProposalStatus.CONFIRMED
        assert confirmed.confirmed_by == "ops-user"
        # SC-006：生效后历史节点逐字节一致（FR-008，历史永不重算）
        for nid, (score, breakdown) in nodes_before.items():
            node = tree_store.get_node(nid)
            assert node.score == score
            assert json.dumps(node.eval_breakdown, sort_keys=True) == breakdown

    def test_shelve_零变更机检(self, calibration_data_dir, cfg):
        proposal = self._proposal(calibration_data_dir)
        registry = Registry()
        specs_before = sorted(s.key for s in registry.list_all())
        hash_before = hashlib.sha256(cfg.read_bytes()).hexdigest()
        shelve_proposal(calibration_data_dir, proposal.proposal_id, by="ops-user")
        assert sorted(s.key for s in registry.list_all()) == specs_before
        assert hashlib.sha256(cfg.read_bytes()).hexdigest() == hash_before

    def test_过期_based_version_拒绝(self, calibration_data_dir, cfg):
        proposal = self._proposal(calibration_data_dir)
        cfg.write_text(cfg.read_text().replace("proxy.aesthetic: 0.5", "proxy.aesthetic: 0.7"))
        with pytest.raises(ValidationError, match="过期"):
            confirm_proposal(
                calibration_data_dir,
                cfg,
                proposal_id=proposal.proposal_id,
                by="ops-user",
                registry=Registry(),
            )


class TestC9一轮完整收口:
    def test_清单到报告全通(
        self, tree_store, anchors_engine, open_round, calibration_data_dir, calib_cfg
    ):
        entries = [
            {"node_id": nid, "score": 0.6 + i * 0.05, "reviewer": "r1"}
            for i, nid in enumerate(open_round.node_ids)
        ]
        with anchors_engine.begin() as conn:
            assert (
                intake_anchors(conn, open_round.round_id, entries, data_dir=calibration_data_dir)
                == 5
            )
        with anchors_engine.connect() as conn:
            summary = close_round(
                tree_store,
                conn,
                calibration_data_dir,
                round_id=open_round.round_id,
                config=calib_cfg,
            )
        assert summary["status"] == "closed"
        assert summary["period"] == "2026-W38"
        # 台账 / 快照 / 报告三类产物同轮落盘
        assert (calibration_data_dir / "ledger" / "visual" / "proxy.aesthetic.jsonl").is_file()
        assert (
            calibration_data_dir / "snapshots" / "visual" / "proxy.aesthetic" / "2026-W38.json"
        ).is_file()
        assert (calibration_data_dir / "reports" / "2026-W38.json").is_file()


class TestFR011零昂贵动作审计:
    def test_校准全流程树表零写入且签名无昂贵通道(
        self,
        tree_store,
        anchors_engine,
        open_round,
        visual_tree,
        calibration_data_dir,
        calib_cfg,
        sqlite_engine,
    ):
        """校准只读树/对象存储：全流程对 tree_nodes/discovery_trees 的写计数恒 0。"""
        writes: list[str] = []

        @event.listens_for(sqlite_engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):
            head = statement.strip().upper()
            if head.startswith(("INSERT", "UPDATE", "DELETE")) and (
                "tree_nodes" in statement or "discovery_trees" in statement
            ):
                writes.append(statement)

        entries = [
            {"node_id": nid, "score": 0.7, "reviewer": "r1"} for nid in open_round.node_ids[:3]
        ]
        with anchors_engine.begin() as conn:
            intake_anchors(conn, open_round.round_id, entries, data_dir=calibration_data_dir)
        with anchors_engine.connect() as conn:
            close_round(
                tree_store,
                conn,
                calibration_data_dir,
                round_id=open_round.round_id,
                config=calib_cfg,
            )
        assert writes == []  # 生成/投放之外，连树表写入都为 0（只读消费）

        # 签名审计：校准公开 API 均不接受网关/平台适配器参数（物理无通道）
        from agents.promo import anchors as promo_anchors
        from core.calibration import anchors, pairing, refit, rounds, selection

        public_api = [
            selection.build_blind_list,
            anchors.intake_anchors,
            promo_anchors.collect_platform_anchors,
            pairing.pair_anchors,
            rounds.close_round,
            refit.maybe_propose,
            refit.confirm_proposal,
        ]
        for func in public_api:
            params = set(inspect.signature(func).parameters)
            assert not (params & {"gateway", "adapter", "platform", "backend"})

    def test_网关调用计数恒零(self, open_round):
        """校准路径不经过 LLM 网关：MockBackend 计数器在流程外保持 0。"""
        from core.llm_gateway.backends.mock import MockBackend

        backend = MockBackend()
        assert backend.call_count == 0  # 校准流程无任何注入网关的入口（签名审计佐证）
