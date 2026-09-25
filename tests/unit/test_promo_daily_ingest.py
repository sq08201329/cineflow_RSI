"""日级分片与覆盖窗口单测（功能 020 契约 C6/C8/C9/C10，T2010）。

本文件覆盖**两层规则的独立机检**（不得互相吞并）：

- **快照层（按事件）**：唯一键 =（活动, 周期）；同一活动跨多日**合法且不覆盖**；
  同（活动, 周期）第二次回流 = **幂等拒绝**（返回 `False`、零变更、整批不中断）；
- **外环产物层（按轮）**：同一周期两轮 `close_round` 的台账行数 == 2、报告文件数 == 2。

另覆盖：节点 id 确定性（历史 `f"{material_id}-node"` 不回改）、存储层拒改写、
覆盖 ∧ 连续双条件（两形态）、断档逐段报出不插值、来源纪律（模拟不冒充真实）、
"覆盖 ∧ 连续"判定只有一处实现（静态断言）、输出键与契约 C9 逐键对齐。

夹具形态：**短剧态**（`period_days: 1` ⇒ 周期标签即归属日），因为"同一活动跨多日"只有在
日级量纲下才是**不同周期**（同一 ISO 周内的三个日期是同一个周期）。
"""

import ast
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select, text, update

from agents.promo.daily import COVERAGE_KEYS, DAY_KEYS, daily_coverage, daily_node_id
from agents.promo.db import promo_campaigns, promo_daily_metrics
from core.evaluators.errors import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_PROMO = REPO_ROOT / "agents" / "promo"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED_SINCE = "2026-09-25"


@pytest.fixture()
def daily_config():
    """短剧态 promo 配置（cadence = 1 天 ⇒ 周期标签 = 归属日；本文件的主夹具形态）。"""
    from agents.promo.config import PromoConfig

    return PromoConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")


@pytest.fixture()
def daily_calibration():
    """短剧态 010 校准配置（`close_round` 用；cadence 与 `daily_config` 同源）。"""
    from core.calibration.config import CalibrationConfig

    return CalibrationConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")


class _StubPromoPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _brief(budget: float = 0.5, temperature: float = 0.3) -> dict:
    # 单条预算 0.5 < 短剧态按轮上限（120 × 0.02 = 2.4）⇒ 三条都能进本轮投放
    return {
        "prompt": "写一句宣发文案",
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": budget,
    }


@pytest.fixture()
def daily_scene(
    tree_store, campaigns_engine, tmp_path, simulated_platform, mock_gateway, daily_config
):
    """真实链路夹具：跑完一轮投放（3 条 delivered，树与运营行齐备）。"""
    from agents.promo.loop import run_round
    from core.tree.artifacts import LocalArtifactStore

    result = run_round(
        "r-daily",
        _StubPromoPolicy([_brief(0.5), _brief(0.5, 0.31), _brief(0.5, 0.32)]),
        tree_store,
        LocalArtifactStore(tmp_path / "artifacts"),
        simulated_platform,
        mock_gateway,
        daily_config,
        engine=campaigns_engine,
    )
    return {
        "store": tree_store,
        "engine": campaigns_engine,
        "round_id": "r-daily",
        "tree_id": result.tree_id,
        "config": daily_config,
    }


def _campaign_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(promo_campaigns)).all()


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(promo_daily_metrics)).all()


class _DeclaredAdapter:
    """夹具适配器：按 `external_id → 归属日` **显式声明**返回指标快照（不派生、不兜底）。"""

    def __init__(self, dates_by_external: dict, *, platform_timestamp: float = 1_700_000_000.0):
        from agents.promo.platform.base import MetricSnapshot

        self._dates = dict(dates_by_external)
        self._base = platform_timestamp
        self._snapshot_cls = MetricSnapshot
        self.calls = 0

    def fetch_metrics(self, external_id: str):
        self.calls += 1
        return self._snapshot_cls(
            ctr=0.05,
            completion_rate=0.6,
            conversions=12,
            impressions=1000,
            clicks=50,
            platform_timestamp=self._base + self.calls,
            data_version="v1",
            metric_date=self._dates[external_id],
        )


def _adapter_for_day(engine, day: str) -> _DeclaredAdapter:
    """该轮全部活动都声明同一归属日（跨日推进 = 换一次调用）。"""
    return _DeclaredAdapter({row.external_id: day for row in _campaign_rows(engine)})


def _daily_engine():
    """独立的 SQLite 内存库（避免与其它夹具共用行集，判定互不干扰）。"""
    from sqlalchemy import create_engine

    from agents.promo.db import create_campaigns_schema

    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_campaigns_schema(engine)
    return engine


def _insert_legacy_campaign(engine, *, campaign_id, external_id, round_id="r-legacy"):
    """历史运营行：`metrics.platform_metrics` 非空、日级表无对应行（020 之前的一次性快照）。"""
    import blake3

    with engine.begin() as conn:
        conn.execute(
            insert(promo_campaigns).values(
                campaign_id=campaign_id,
                round_id=round_id,
                material_id=f"mat-{campaign_id}",
                node_id=f"mat-{campaign_id}-node",
                status="ingested",
                spent_usd=1.0,
                external_id=external_id,
                metrics={
                    "platform_metrics": {
                        "ctr": 0.05,
                        "completion_rate": 0.6,
                        "conversions": 12,
                        "impressions": 1000,
                        "clicks": 50,
                        "platform_timestamp": 1000.0,
                        "data_version": "v1",
                    },
                    "material": {
                        "platform": "douyin",
                        "artifact_hash": blake3.blake3(campaign_id.encode()).hexdigest(),
                        "kind": "poster",
                        "tags": [],
                    },
                },
                created_at=1000.0,
                updated_at=1000.0,
            )
        )


class Test唯一性与多日合法:
    def test_同一活动跨三日各采一次(self, daily_scene):
        """同一 `campaign_id`、三个不同归属日 ⇒ 三行、三个 period、三个节点 id、零覆盖。"""
        from agents.promo.daily import ingest_daily

        engine, store = daily_scene["engine"], daily_scene["store"]
        campaign_id = _campaign_rows(engine)[0].campaign_id
        for day in ("2026-09-25", "2026-09-26", "2026-09-27"):
            report = ingest_daily(
                daily_scene["round_id"],
                store,
                _adapter_for_day(engine, day),
                engine,
                daily_scene["config"],
                source="real",
                collected_at=1_700_000_000.0,
            )
            assert report["rejected"] == []
            assert report["periods"] == [day]  # 日级：周期标签即归属日
        rows = [row for row in _rows(engine) if row.campaign_id == campaign_id]
        assert len(rows) == 3
        assert [row.period for row in rows] == ["2026-09-25", "2026-09-26", "2026-09-27"]
        assert len({row.node_id for row in rows}) == 3
        assert all(row.node_id.endswith(f"@{row.period}") for row in rows)
        with engine.connect() as conn:
            overlaps = conn.execute(
                text(
                    "SELECT campaign_id, period FROM promo_daily_metrics "
                    "GROUP BY campaign_id, period HAVING count(*) > 1"
                )
            ).all()
        assert overlaps == []  # 唯一性键（活动, 周期）零覆盖

    def test_幂等拒绝且整批不中断(self, daily_scene):
        """同（活动, 周期）第二次回流 ⇒ `False`、行数/节点数/运营表 `updated_at` 不变。"""
        from agents.promo.daily import ingest_daily

        engine, store = daily_scene["engine"], daily_scene["store"]
        adapter = _adapter_for_day(engine, "2026-09-25")
        first = ingest_daily(
            daily_scene["round_id"],
            store,
            adapter,
            engine,
            daily_scene["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        assert len(first["ingested"]) == 3
        rows_before = len(_rows(engine))
        nodes_before = len(store.nodes_of(daily_scene["tree_id"]))
        with engine.connect() as conn:
            updated_before = {
                row.campaign_id: row.updated_at for row in conn.execute(select(promo_campaigns))
            }

        second = ingest_daily(
            daily_scene["round_id"],
            store,
            adapter,
            engine,
            daily_scene["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        assert second["ingested"] == []
        assert len(second["rejected"]) == 3  # 同（活动, 周期）已回流：幂等拒绝
        assert all("幂等拒绝" in item["reason"] for item in second["rejected"])
        assert len(_rows(engine)) == rows_before
        assert len(store.nodes_of(daily_scene["tree_id"])) == nodes_before
        with engine.connect() as conn:
            updated_after = {
                row.campaign_id: row.updated_at for row in conn.execute(select(promo_campaigns))
            }
        assert updated_after == updated_before  # 运营表零变更

    def test_新表唯一键与既有键并存(self, daily_scene):
        """`promo_campaigns` 的既有唯一键 `(round_id, material_id)` 仍生效（零改动）。"""
        from sqlalchemy.exc import IntegrityError

        engine = daily_scene["engine"]
        existing = _campaign_rows(engine)[0]
        duplicate = next(
            row for row in _campaign_rows(engine) if row.campaign_id != existing.campaign_id
        )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    insert(promo_campaigns).values(
                        campaign_id="camp-duplicate",
                        round_id=existing.round_id,
                        material_id=existing.material_id,  # 同 (round_id, material_id)
                        node_id=None,
                        status="delivered",
                        spent_usd=1.0,
                        external_id="ext-dup",
                        metrics=None,
                        created_at=1000.0,
                        updated_at=1000.0,
                    )
                )
        assert duplicate.campaign_id  # 既有行不受影响


class Test两层规则不互相吞并:
    def test_同周期两轮并留存而同日重复采集零新增(
        self,
        tree_store,
        anchors_engine,
        build_calibration_tree,
        calibration_data_dir,
        daily_scene,
    ):
        """外环产物层（按轮）与快照层（按事件）**两句话必须同时成立**。"""
        from agents.promo.daily import ingest_daily
        from core.calibration.anchors import intake_anchors
        from core.calibration.config import CalibrationConfig
        from core.calibration.rounds import close_round
        from core.calibration.selection import build_blind_list

        short = CalibrationConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        breakdown = {"proxy.aesthetic@1.0.0": {"score": 0.6}}
        _, node_ids = build_calibration_tree(
            [(0.5, dict(breakdown)), (0.7, dict(breakdown)), (0.9, dict(breakdown))],
            agent_id="visual",
            base_created_at=datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp(),
        )
        for index in range(2):
            round_ = build_blind_list(
                tree_store,
                agent_id="visual",
                period_start="2026-09-25",
                period_end="2026-09-25",
                top_k=3,
                data_dir=calibration_data_dir,
                period_days=short.period_days,
                round_id=f"daily-round-{index}",
            )
            entries = [
                {"node_id": nid, "score": score, "reviewer": "r1"}
                for nid, score in zip(node_ids, (0.55, 0.75, 0.95), strict=True)
            ]
            with anchors_engine.begin() as conn:
                intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
            with anchors_engine.connect() as conn:
                close_round(
                    tree_store, conn, calibration_data_dir, round_id=round_.round_id, config=short
                )
        ledger_lines = sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in (calibration_data_dir / "ledger" / "visual").glob("*.jsonl")
        )
        reports = list((calibration_data_dir / "reports").glob("*.json"))

        engine = daily_scene["engine"]
        adapter = _adapter_for_day(engine, "2026-09-25")
        for _ in range(2):
            ingest_daily(
                daily_scene["round_id"],
                daily_scene["store"],
                adapter,
                engine,
                daily_scene["config"],
                source="real",
                collected_at=1_700_000_000.0,
            )
        assert ledger_lines == 2  # 两轮各一行台账（append-only，零覆盖）
        assert len(reports) == 2 and len({path.name for path in reports}) == 2  # 轮数
        assert len(_rows(engine)) == 3  # 同一周期的第二次采集 0 行新增（快照层拒绝）
        assert ledger_lines == 2  # 外环产物层不受快照层影响（两规则各管一层）


class Test节点标识确定性:
    def test_节点_id_含周期派生(self):
        assert daily_node_id("m1", "2026-09-25") == "m1-node@2026-09-25"
        assert daily_node_id("m1", "2026-09-25") == daily_node_id("m1", "2026-09-25")
        assert daily_node_id("m1", "2026-09-26") != daily_node_id("m1", "2026-09-25")
        assert daily_node_id("m2", "2026-09-25") != daily_node_id("m1", "2026-09-25")
        with pytest.raises(ValidationError):
            daily_node_id("", "2026-09-25")

    def test_历史节点不回改且新节点自描述归属日(self, daily_scene):
        from agents.promo.daily import ingest_daily
        from core.tree.models import CostRecord, NodeStatus, TreeNode

        engine, store = daily_scene["engine"], daily_scene["store"]
        legacy = TreeNode(
            node_id="mat-legacy-node",  # 020 之前的形态：f"{material_id}-node"
            tree_id=daily_scene["tree_id"],
            parent_id=None,
            depth=0,
            agent_id="promo",
            policy_version="promo",
            prompt="",
            observation_context={"material_id": "mat-legacy"},
            artifact_hash="cd" * 32,
            eval_breakdown={"rule.material_compliance@1.0.0": {"score": 0.9}},
            score=0.9,
            cost=CostRecord(),
            status=NodeStatus.EVALUATED,
            created_at=1000.0,
        )
        store.append_node(legacy)

        ingest_daily(
            daily_scene["round_id"],
            store,
            _adapter_for_day(engine, "2026-09-25"),
            engine,
            daily_scene["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        reloaded = store.get_node("mat-legacy-node")
        assert reloaded.node_id == "mat-legacy-node"  # 历史 node_id 不被重命名
        assert reloaded.score == 0.9
        assert dict(reloaded.eval_breakdown) == {"rule.material_compliance@1.0.0": {"score": 0.9}}
        assert reloaded.observation_context == {"material_id": "mat-legacy"}

        new_nodes = [
            node
            for node in store.nodes_of(daily_scene["tree_id"])
            if node.parent_id is not None and node.node_id != "mat-legacy-node"
        ]
        assert new_nodes, "日级分片未落新节点"
        for node in new_nodes:
            assert node.node_id.endswith("@2026-09-25")
            assert node.observation_context["metric_date"] == "2026-09-25"
            assert node.observation_context["period"] == "2026-09-25"


class Test历史行可读且不被误判:
    def test_历史单快照计入_legacy_且不进覆盖(self, daily_scene):
        """运营表有 `platform_metrics` 而日级表无该活动行 ⇒ `legacy_single_snapshot`。"""
        from agents.promo.daily import ingest_daily

        engine = daily_scene["engine"]
        _insert_legacy_campaign(engine, campaign_id="camp-legacy", external_id="ext-legacy")
        ingest_daily(
            daily_scene["round_id"],
            daily_scene["store"],
            _adapter_for_day(engine, "2026-09-25"),
            engine,
            daily_scene["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        coverage = daily_coverage(
            engine,
            end="2026-09-25",
            min_window_days=1,
            gap_tolerance_days=0,
            period_days=1,
        )
        assert coverage["legacy_single_snapshots"] == 1
        # 历史行虽在运营表里，但归属日未标定 ⇒ 不进任何归属日、不计入覆盖
        assert coverage["covered_days"] == 1
        assert len(coverage["days"]) == 1
        assert "camp-legacy" not in {entry["campaign_id"] for entry in coverage["days"]}
        assert "历史单快照行 1 条" in coverage["note"]


class Test存储层拒改写:
    def test_update_delete_一律抛错(self, daily_scene):
        from agents.promo.daily import ingest_daily

        engine = daily_scene["engine"]
        ingest_daily(
            daily_scene["round_id"],
            daily_scene["store"],
            _adapter_for_day(engine, "2026-09-25"),
            engine,
            daily_scene["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        count_before = len(_rows(engine))
        for statement in (
            update(promo_daily_metrics).values(source="simulated"),
            promo_daily_metrics.delete(),
        ):
            with pytest.raises(Exception, match="immutable"):
                with engine.begin() as conn:
                    conn.execute(statement)
        assert len(_rows(engine)) == count_before  # 行数不变


class _Seeder:
    """直接把日级行写入库（不落树）——覆盖判定只依赖日级表。"""

    @staticmethod
    def seed(engine, days, *, source="real", campaign_prefix="c"):
        from agents.promo.daily import record_daily_ingest
        from agents.promo.platform.base import MetricSnapshot

        for index, day in enumerate(days):
            assert record_daily_ingest(
                engine,
                campaign_id=f"{campaign_prefix}-{index}",
                round_id="r-cover",
                external_id=f"ext-{campaign_prefix}-{index}",
                material_id=f"mat-{campaign_prefix}-{index}",
                snapshot=MetricSnapshot(
                    ctr=0.05,
                    completion_rate=0.6,
                    conversions=12,
                    impressions=1000,
                    clicks=50,
                    platform_timestamp=1_700_000_000.0,
                    data_version="v1",
                    metric_date=day,
                ),
                period=day,
                collected_at=1_700_000_000.0,
                source=source,
                node_id=f"mat-{campaign_prefix}-{index}-node@{day}",
            )


class Test覆盖与连续双条件:
    def test_短剧态连续十五天达标缺两天不达标且逐段报(self):
        days = [f"2026-09-{day:02d}" for day in range(1, 16)]
        ok_engine = _daily_engine()
        _Seeder.seed(ok_engine, days)
        ok = daily_coverage(
            ok_engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert ok["meets"] is True and ok["covered_days"] == 15 and ok["gaps"] == []

        broken_engine = _daily_engine()
        _Seeder.seed(
            broken_engine, [day for day in days if day not in ("2026-09-04", "2026-09-11")]
        )
        broken = daily_coverage(
            broken_engine,
            end="2026-09-15",
            min_window_days=14,
            gap_tolerance_days=0,
            period_days=1,
        )
        assert broken["meets"] is False and broken["continuous"] is False
        assert [(gap["from"], gap["to"], gap["days"]) for gap in broken["gaps"]] == [
            ("2026-09-04", "2026-09-04", 1),
            ("2026-09-11", "2026-09-11", 1),
        ]
        assert broken["gap_shortfall_days"] == 1
        assert "覆盖不足" in "；".join(broken["reasons"])

    def test_电影态下限七天复跑(self):
        engine = _daily_engine()
        _Seeder.seed(engine, [f"2026-09-{day:02d}" for day in range(1, 8)])
        movie = daily_coverage(
            engine, end="2026-09-07", min_window_days=7, gap_tolerance_days=0, period_days=1
        )
        assert movie["meets"] is True and movie["covered_days"] == 7
        strict = daily_coverage(
            engine, end="2026-09-07", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert strict["meets"] is False and strict["coverage_shortfall_days"] == 7

    def test_不插值_缺口与覆盖无交集(self):
        days = [f"2026-09-{day:02d}" for day in range(1, 16)]
        kept = [day for day in days if day not in ("2026-09-04", "2026-09-11")]
        engine = _daily_engine()
        _Seeder.seed(engine, kept)
        coverage = daily_coverage(
            engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        gap_dates = {gap["from"] for gap in coverage["gaps"]} | {
            gap["to"] for gap in coverage["gaps"]
        }
        assert set(coverage["covered_dates"]) & gap_dates == set()
        assert len(coverage["days"]) == len(_rows(engine))  # 行数核对相等 ⇒ 无插值/补零
        assert {entry["metric_date"] for entry in coverage["days"]} == set(kept)
        assert all(entry["source"] == "real" for entry in coverage["days"])


class Test来源纪律:
    def test_域外来源拒绝且零落盘(self, campaigns_engine):
        from agents.promo.daily import record_daily_ingest
        from agents.promo.platform.base import MetricSnapshot

        snapshot = MetricSnapshot(
            ctr=0.05,
            completion_rate=0.6,
            conversions=12,
            impressions=1000,
            clicks=50,
            platform_timestamp=1_700_000_000.0,
            data_version="v1",
            metric_date="2026-09-25",
        )
        for bad in ("mock", "stub", "test", "REAL", ""):
            with pytest.raises(ValidationError, match="取值域外"):
                record_daily_ingest(
                    campaigns_engine,
                    campaign_id=f"camp-{bad or 'empty'}",
                    round_id="r",
                    external_id="ext",
                    material_id="mat",
                    snapshot=snapshot,
                    period="2026-09-25",
                    collected_at=1.0,
                    source=bad,
                    node_id="mat-node@2026-09-25",
                )
        assert _rows(campaigns_engine) == []

    def test_模拟日不进覆盖_十五天全模拟不达标(self):
        engine = _daily_engine()
        _Seeder.seed(engine, [f"2026-09-{day:02d}" for day in range(1, 16)], source="simulated")
        coverage = daily_coverage(
            engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert coverage["covered_days"] == 0
        assert coverage["covered_dates"] == []
        assert coverage["meets"] is False
        assert coverage["evidence_claim"] == "mechanism_ready_real_feedback_pending"
        assert len(coverage["days"]) == 15  # 有记录（非断档）但都不是真实覆盖
        assert all(entry["source"] == "simulated" for entry in coverage["days"])
        assert coverage["continuous"] is True

    def test_回落无原因即拒绝(self, campaigns_engine):
        from agents.promo.daily import record_daily_ingest
        from agents.promo.platform.base import MetricSnapshot

        snapshot = MetricSnapshot(
            ctr=0.05,
            completion_rate=0.6,
            conversions=12,
            impressions=1000,
            clicks=50,
            platform_timestamp=1_700_000_000.0,
            data_version="v1",
            metric_date="2026-09-25",
        )
        with pytest.raises(ValidationError, match="必须声明原因"):
            record_daily_ingest(
                campaigns_engine,
                campaign_id="camp-fallback",
                round_id="r",
                external_id="ext",
                material_id="mat",
                snapshot=snapshot,
                period="2026-09-25",
                collected_at=1.0,
                source="fallback",
                node_id="mat-node@2026-09-25",
            )
        assert _rows(campaigns_engine) == []

    def test_达标且全真实才允许宣称达成(self):
        days = [f"2026-09-{day:02d}" for day in range(1, 16)]
        engine = _daily_engine()
        _Seeder.seed(engine, days)
        coverage = daily_coverage(
            engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert coverage["meets"] is True
        assert coverage["evidence_claim"] == "real_feedback_met"

        # 同一天掺入模拟来源 ⇒ 该日不再"只来自真实"，不得宣称达成（`sources` 可辨）
        from agents.promo.daily import record_daily_ingest
        from agents.promo.platform.base import MetricSnapshot

        assert record_daily_ingest(
            engine,
            campaign_id="camp-sim-mix",
            round_id="r-cover",
            external_id="ext-sim-mix",
            material_id="mat-mix",
            snapshot=MetricSnapshot(
                ctr=0.05,
                completion_rate=0.6,
                conversions=12,
                impressions=1000,
                clicks=50,
                platform_timestamp=1_700_000_000.0,
                data_version="v1",
                metric_date="2026-09-03",
            ),
            period="2026-09-03",
            collected_at=1_700_000_000.0,
            source="simulated",
            node_id="mat-mix-node@2026-09-03",
        )
        mixed = daily_coverage(
            engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert mixed["meets"] is True
        assert mixed["evidence_claim"] == "mechanism_ready_real_feedback_pending"
        entry = next(item for item in mixed["days"] if item["metric_date"] == "2026-09-03")
        assert entry["sources"] == {"real": 1, "simulated": 1}  # 混合可辨、不冒充


class Test取值域单点:
    def test_来源字面量不在本特性内重复声明(self):
        """`agents/promo/` 与 `core/calibration/` 内不得重复声明来源取值域字面量。"""
        roots = [REPO_ROOT / "agents" / "promo", REPO_ROOT / "core" / "calibration"]
        offences: list[str] = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    quoted = re.findall(r"\"([a-z]+)\"|'([a-z]+)'", line)
                    flat = {value for pair in quoted for value in pair if value}
                    if {"real", "simulated", "fallback"} <= flat:
                        offences.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        assert offences == [], f"来源取值域字面量必须只在 core/billing/runlog.py 声明：{offences}"


class Test样本不足三值各归其位:
    """T2044① 的两侧落点（本文件侧）：台账行不产偏差值；无快照不成判。"""

    def test_样本不足台账行只带_note_偏差值为_none(
        self,
        tree_store,
        anchors_engine,
        build_calibration_tree,
        calibration_data_dir,
        daily_config,
        daily_calibration,
    ):
        """`samples < calibration.min_samples` ⇒ 台账行只带 `note`、三个偏差值全 `None`。"""
        import json

        from core.calibration.anchors import intake_anchors
        from core.calibration.rounds import close_round
        from core.calibration.selection import build_blind_list

        breakdown = {"proxy.aesthetic@1.0.0": {"score": 0.6}}
        _, node_ids = build_calibration_tree(
            [(0.5, dict(breakdown)), (0.7, dict(breakdown))],
            agent_id="visual",
            base_created_at=datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp(),
        )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start="2026-09-25",
            period_end="2026-09-25",
            top_k=5,
            data_dir=calibration_data_dir,
            period_days=daily_config.period_days,
        )
        entries = [
            {"node_id": nid, "score": score, "reviewer": "r1"}
            for nid, score in zip(node_ids, (0.6, 0.8), strict=True)
        ]
        with anchors_engine.begin() as conn:
            intake_anchors(conn, round_.round_id, entries, data_dir=calibration_data_dir)
        with anchors_engine.connect() as conn:
            close_round(
                tree_store,
                conn,
                calibration_data_dir,
                round_id=round_.round_id,
                config=daily_calibration,
            )
        ledger = calibration_data_dir / "ledger" / "visual" / "proxy.aesthetic.jsonl"
        record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        assert "样本不足" in record["note"]  # 只写 note
        assert record["mean_shift"] is None
        assert record["pearson_r"] is None
        assert record["kendall_tau"] is None
        assert record["anchor_count"] == record["samples"]  # 溯源与样本量一致（不顶替）
        assert record["period_days"] == 1

    def test_无快照不成判(self, tmp_path):
        """无该周期快照 ⇒ `verdict == no_data`（该周期不入窗口、缺口如实报，不插值）。"""
        from core.calibration.drift_config import DriftConfig
        from core.calibration.drift_metrics import detect_drift
        from core.calibration.drift_models import DriftVerdict

        cfg = DriftConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        record = detect_drift(
            "visual", "judge.cinematic@1.0.0", "2026-09-25", cfg, tmp_path / "calibration"
        )
        assert record.verdict is DriftVerdict.NO_DATA
        assert record.psi is None and record.quantile_shifts == {}
        assert record.snapshot_fingerprint is None


class Test周期窗口按归属日过滤:
    def test_过滤键是归属日而非创建时刻(self, tree_store, calibration_data_dir):
        """T2038：候选过滤键 = 归属日（`observation_context["metric_date"]`）。

        三支对照：① 创建时刻在窗口内但**归属日在外** ⇒ 排除（旧口径会误收）；
        ② 创建时刻在窗口外但**归属日在内** ⇒ 入选（迟到/回补按归属日归入）；
        ③ 无归属日的历史节点 ⇒ 按 `created_at` 的**日期**回退（周级/历史判定零变化）。
        """
        import blake3

        from core.calibration.selection import build_blind_list
        from core.tree.models import CostRecord, DiscoveryTree, NodeStatus, TreeNode, new_id

        root_id = new_id()
        tree = DiscoveryTree(
            tree_id=new_id(),
            project_id="p",
            agent_id="visual",
            policy_version="a1b2c3d4e5f6",
            root_id=root_id,
            node_ids=[root_id],
            config_snapshot={"evaluator_weights": {"rule.x": "gate"}},
        )
        tree_store.create_tree(tree)
        breakdown = {"proxy.aesthetic@1.0.0": {"score": 0.6}}
        tree_store.append_node(
            TreeNode(
                node_id=root_id,
                tree_id=tree.tree_id,
                parent_id=None,
                depth=0,
                agent_id="visual",
                policy_version="a1b2c3d4e5f6",
                prompt="",
                observation_context={},
                artifact_hash=blake3.blake3(root_id.encode()).hexdigest(),
                eval_breakdown=dict(breakdown),
                score=0.5,
                cost=CostRecord(),
                status=NodeStatus.EVALUATED,
                created_at=0.0,
            )
        )
        specs = (
            # (node_id, created_at 日期小时, metric_date 或 None)
            ("n-created-inside-attr-out", datetime(2026, 9, 25, 12, tzinfo=UTC), "2026-09-20"),
            ("n-created-outside-attr-in", datetime(2026, 9, 20, 12, tzinfo=UTC), "2026-09-25"),
            ("n-no-attribution", datetime(2026, 9, 25, 12, tzinfo=UTC), None),
        )
        for node_id, created, metric_date in specs:
            context = {"material_id": node_id}
            if metric_date is not None:
                context["metric_date"] = metric_date
                context["period"] = metric_date
            tree_store.append_node(
                TreeNode(
                    node_id=node_id,
                    tree_id=tree.tree_id,
                    parent_id=root_id,
                    depth=1,
                    agent_id="visual",
                    policy_version="a1b2c3d4e5f6",
                    prompt="",
                    observation_context=context,
                    artifact_hash=blake3.blake3(node_id.encode()).hexdigest(),
                    eval_breakdown=dict(breakdown),
                    score=0.6,
                    cost=CostRecord(),
                    status=NodeStatus.EVALUATED,
                    created_at=created.timestamp(),
                )
            )
        round_ = build_blind_list(
            tree_store,
            agent_id="visual",
            period_start="2026-09-25",
            period_end="2026-09-25",
            top_k=5,
            data_dir=calibration_data_dir,
            period_days=1,
            round_id="attribution-filter",
        )
        assert set(round_.node_ids) == {
            "n-created-outside-attr-in",  # ② 归属日在窗口内 ⇒ 入选
            "n-no-attribution",  # ③ 无归属日 ⇒ 按 created_at 的日期回退（历史行为保留）
        }


class Test覆盖判定唯一实现:
    def test_agents_promo_零自算_meets_与_max_gap(self):
        """静态断言：`agents/promo/` 的非测试代码不得自算 `meets` / `max_gap_days` 口径。"""
        offences: list[str] = []
        for path in sorted(_AGENTS_PROMO.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)} | {
                    child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
                }
                if {"meets", "max_gap_days"} & names:
                    offences.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert offences == [], f"覆盖判定只能来自 coverage_window：{offences}"

    def test_daily_coverage_委托_coverage_window(self):
        """AST：`daily_coverage` 的函数体内含 `coverage_window` 调用（唯一实现）。"""
        source = (_AGENTS_PROMO / "daily.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "daily_coverage"
        )
        called = {
            child.func.id
            for child in ast.walk(function)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        assert "coverage_window" in called
        # 判定类键一律**回填** `coverage_window` 的返回值（不得就地重算）
        returned = next(
            node for node in ast.walk(function) if isinstance(node, ast.Return) and node.value
        )
        assert isinstance(returned.value, ast.Dict)
        segments = {
            key.value: ast.get_source_segment(source, value)
            for key, value in zip(returned.value.keys, returned.value.values, strict=True)
            if isinstance(key, ast.Constant)
        }
        for key in (
            "covered_days",
            "covered_dates",
            "gaps",
            "max_gap_days",
            "continuous",
            "meets",
            "coverage_shortfall_days",
            "gap_shortfall_days",
            "reasons",
        ):
            segment = segments.get(key, "")
            assert "base[" in segment, f"{key} 未回填自唯一实现：{segment!r}"

    def test_输出键与契约_C9_逐键对齐(self):
        """C9 键集**逐键**核对（缺键即红）；`days[]` 三时间并列。"""
        engine = _daily_engine()
        _Seeder.seed(engine, ["2026-09-25"])
        coverage = daily_coverage(
            engine, end="2026-09-25", min_window_days=1, gap_tolerance_days=0, period_days=1
        )
        missing = [key for key in COVERAGE_KEYS if key not in coverage]
        assert missing == [], f"缺少契约 C9 键：{missing}"
        assert coverage["attribution_based"] is True
        assert "start" not in coverage  # C9 取舍：无 start（起始端点由 days[] 派生）
        assert coverage["window_semantics"] == "half_open"
        entry = coverage["days"][0]
        for key in DAY_KEYS:
            assert key in entry, f"days[] 缺键 {key}"
        assert _ISO_DATE.fullmatch(entry["metric_date"])
        assert set(COVERAGE_KEYS) == set(coverage)
