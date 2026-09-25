"""归属日与三时间并列单测（功能 020 契约 C7/C9，T2009）。

机检边界（本文件是**权威落点**）：

- **写入路径主断言**：两个适配器的采集出口与 `ingest_daily` 的回流写入，对任一夹具输入
  产出 `snapshot.metric_date is None` 的次数恒 **0**；
- **数据类不承担必填**：`MetricSnapshot(..., metric_date=None)` **构造成功**，
  而 `validate_metrics(...)` 抛错且消息含「平台未提供指标归属日」；
- **兜底次数恒 0**：反向扫描 `agents/promo/`（`platform/simulated.py` 的确定性基线除外，
  契约 C7 明文授权其"缺省取 platform_timestamp 的 UTC 日期"）不得把
  `platform_timestamp` 或采集时刻派生为 `metric_date`；
- **按归属日聚合**：覆盖视图的 `days[]` 与 `covered_days` 只认归属日，不认采集日；
- **三时间并列**：`days[]` 每条同时含 `metric_date` / `collected_at` / `platform_timestamp`；
- **历史回退可计数**：`attribution_fallback_count(required_since)` == 覆盖视图
  `attribution_missing_anchors`（回退只作用于历史行，如实登记、不静默补值）；
- **新写入不得走回退**：`created_at` 的日期 ≥ 生效日而快照缺归属日 ⇒ 拒绝落锚点。
"""

import ast
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from agents.promo.anchors import attribution_fallback_count, collect_platform_anchors
from agents.promo.daily import daily_coverage, record_daily_ingest
from agents.promo.db import promo_daily_metrics
from agents.promo.platform.base import MetricSnapshot, validate_metrics
from core.calibration.db import calibration_anchors

REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_PROMO = REPO_ROOT / "agents" / "promo"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUIRED_SINCE = "2026-09-25"


def _snapshot(metric_date: str | None = "2026-09-25", **overrides) -> MetricSnapshot:
    fields = {
        "ctr": 0.05,
        "completion_rate": 0.6,
        "conversions": 12,
        "impressions": 1000,
        "clicks": 50,
        "platform_timestamp": 1_700_000_000.0,
        "data_version": "v1",
        "metric_date": metric_date,
    }
    fields.update(overrides)
    return MetricSnapshot(**fields)


class _StubPromoPolicy:
    policy_version = "a1b2c3d4e5f6"

    def __init__(self, briefs):
        self._briefs = briefs

    def plan_materials(self, config):
        return list(self._briefs)


def _brief(budget: float = 2.0, temperature: float = 0.3) -> dict:
    return {
        "prompt": "写一句宣发文案",
        "gen_params": {"temperature": temperature},
        "kind": "copy",
        "tags": ["剧情"],
        "budget_usd": budget,
    }


@pytest.fixture()
def delivered_round(
    tree_store, campaigns_engine, tmp_path, simulated_platform, mock_gateway, promo_config
):
    """真实链路夹具：跑完一轮投放（3 条全部 delivered），返回 (轮结果, 环境 dict)。"""
    from agents.promo.loop import run_round
    from core.tree.artifacts import LocalArtifactStore

    result = run_round(
        "r-attr",
        _StubPromoPolicy([_brief(2.0), _brief(2.0, 0.31), _brief(2.0, 0.32)]),
        tree_store,
        LocalArtifactStore(tmp_path / "artifacts"),
        simulated_platform,
        mock_gateway,
        promo_config,
        engine=campaigns_engine,
    )
    return result, {
        "store": tree_store,
        "engine": campaigns_engine,
        "adapter": simulated_platform,
        "config": promo_config,
    }


def _daily_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(promo_daily_metrics)).all()


def _anchor_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(calibration_anchors)).all()


def _write_day(engine, *, campaign_id, metric_date, collected_at, source="real"):
    return record_daily_ingest(
        engine,
        campaign_id=campaign_id,
        round_id="r-agg",
        external_id=f"ext-{campaign_id}",
        material_id=f"mat-{campaign_id}",
        snapshot=_snapshot(metric_date=metric_date, platform_timestamp=collected_at - 1.0),
        period=metric_date,
        collected_at=collected_at,
        source=source,
        node_id=f"{campaign_id}-node@{metric_date}",
    )


class Test采集出口恒产归属日:
    def test_模拟适配器缺省由自身固定时间戳派生(self, promo_config):
        """确定性：缺省取自身固定 `platform_timestamp` 的 UTC 日期（同源、可复现）。"""
        from agents.promo.platform.simulated import SimulatedPlatform

        platform = SimulatedPlatform(promo_config.simulated_platform)
        snapshot = _simulated_snapshot(platform)
        assert snapshot.metric_date == "2023-11-14"  # 1700000000.0 的 UTC 日期
        assert _ISO_DATE.fullmatch(snapshot.metric_date)
        # 可显式注入以推进跨日夹具（确定性：同注入同输出）
        injected = SimulatedPlatform(promo_config.simulated_platform, metric_date="2026-09-25")
        assert _simulated_snapshot(injected).metric_date == "2026-09-25"
        # 注入非法值即拒（不猜测、不格式化）
        from agents.promo.platform.base import MetricValidationError

        with pytest.raises(MetricValidationError, match="指标归属日非法"):
            SimulatedPlatform(promo_config.simulated_platform, metric_date="2026/09/25")

    def test_真实适配器读平台字段_缺失即拒(self):
        """`http_real` 的采集出口从平台响应读 `metric_date`；缺失即拒、不兜底。"""
        from agents.promo.platform.base import MetricValidationError
        from agents.promo.platform.http_real import HttpRealPlatform

        adapter = HttpRealPlatform("https://example.invalid", "k")
        payload = {
            "ctr": 0.05,
            "completion_rate": 0.6,
            "conversions": 12,
            "impressions": 1000,
            "clicks": 50,
            "platform_timestamp": 1_700_000_000.0,
            "data_version": "v1",
            "metric_date": "2026-09-25",
        }
        snapshot = adapter._snapshot(dict(payload), where="夹具响应")
        assert snapshot.metric_date == "2026-09-25"
        assert _ISO_DATE.fullmatch(snapshot.metric_date)

        missing = {key: value for key, value in payload.items() if key != "metric_date"}
        with pytest.raises(MetricValidationError, match="平台未提供指标归属日"):
            adapter._snapshot(missing, where="夹具响应")
        with pytest.raises(MetricValidationError, match="指标归属日非法"):
            adapter._snapshot({**payload, "metric_date": "2026/09/25"}, where="夹具响应")
        with pytest.raises(MetricValidationError, match="指标归属日非法"):
            # 形态合规但非真实日历日（2026-13-01）⇒ 同样拒绝，不静默接受
            adapter._snapshot({**payload, "metric_date": "2026-13-01"}, where="夹具响应")

    def test_回流写入路径产生_none_的次数恒零(self, delivered_round):
        """适配器采集出口 + `ingest_daily` 写入：`metric_date is None` 恒为 0。"""
        from agents.promo.daily import ingest_daily

        _, env = delivered_round
        snapshots = [
            env["adapter"].fetch_metrics(external_id)
            for external_id in (row.external_id for row in _campaign_rows(env["engine"]))
        ]
        assert snapshots and all(item.metric_date is not None for item in snapshots)

        report = ingest_daily(
            "r-attr",
            env["store"],
            env["adapter"],
            env["engine"],
            env["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        assert report["rejected"] == []
        rows = _daily_rows(env["engine"])
        assert len(rows) == 3
        assert all(row.metric_date == "2023-11-14" for row in rows)
        assert all(_ISO_DATE.fullmatch(row.metric_date) for row in rows)

    def test_不应由时间戳或采集时刻派生归属日(self):
        """反向扫描：`agents/promo/` 内不得把时间戳/采集时刻**派生**为归属日。

        扫描口径（AST，避免误伤文档与键清单）：凡把 `metric_date` 赋成/传成
        `fromtimestamp(...)` / `now()` / `time()` / `today()` / `utcnow()` 的**调用结果**
        之处即违规。`platform/simulated.py` 豁免——契约 C7 明文授权其
        "缺省取自身固定 `platform_timestamp` 的 UTC 日期"（确定性基线）。
        """
        forbidden_calls = {"fromtimestamp", "now", "utcnow", "today", "time"}

        def _derived(value: ast.AST | None) -> bool:
            if value is None:
                return False
            for child in ast.walk(value):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name in forbidden_calls:
                    return True
            return False

        offences: list[str] = []
        for path in sorted(_AGENTS_PROMO.rglob("*.py")):
            if path.name == "simulated.py":
                continue  # 契约 C7 明文授权（确定性基线）
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.keyword)
                    and node.arg == "metric_date"
                    and _derived(node.value)
                ):
                    offences.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    if "metric_date" in targets and _derived(node.value):
                        offences.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values, strict=False):
                        if isinstance(key, ast.Constant) and key.value == "metric_date":
                            if _derived(value):
                                offences.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert offences == [], f"归属日不得由时间戳/采集时刻派生：{offences}"


def _simulated_snapshot(platform) -> MetricSnapshot:
    """让模拟平台的一个活动走到 delivered 并取指标（适配器采集出口的真实路径）。"""
    from agents.promo.platform.base import PromoMaterial

    material = PromoMaterial(
        material_id="m-1",
        kind="poster",
        content={"copy": "文案"},
        artifact_hash="ab" * 32,
        platform="douyin",
        tags=[],
    )
    campaign = platform.create_campaign(material, 2.0, idempotency_key="sim-key")
    platform.get_status(campaign.external_id)
    platform.get_status(campaign.external_id)  # created → delivering → delivered
    return platform.fetch_metrics(campaign.external_id)


def _campaign_rows(engine):
    from agents.promo.db import promo_campaigns

    with engine.connect() as conn:
        return conn.execute(select(promo_campaigns)).all()


class Test数据类不承担必填:
    def test_构造成功但校验失败(self):
        """存在性检查落在**写入路径**（`validate_metrics`），不在数据类构造上。"""
        from agents.promo.platform.base import MetricValidationError

        snapshot = _snapshot(metric_date=None)
        assert snapshot.metric_date is None  # 构造成功（兼容历史 dict 重建）
        with pytest.raises(MetricValidationError, match="平台未提供指标归属日"):
            validate_metrics(snapshot)
        assert validate_metrics(_snapshot()) is None  # 有归属日 ⇒ 通过

    def test_历史_payload_重建不抛构造期异常(self, promo_config):
        """`platform_metrics` 评估器按 `raw.get("metric_date")` 重建：缺键 ⇒ None、不抛错。"""
        from agents.promo.evaluators.platform_metrics import PlatformMetricsEvaluator
        from core.evaluators.base import ArtifactRef

        evaluator = PlatformMetricsEvaluator(
            promo_config.metric_weights, ctr_cap=promo_config.ctr_cap
        )
        legacy_raw = {
            "ctr": 0.05,
            "completion_rate": 0.6,
            "conversions": 12,
            "impressions": 1000,
            "clicks": 50,
            "platform_timestamp": 1000.0,
            "data_version": "v1",
        }  # 无 metric_date 键（020 之前的历史落盘形态）
        result = evaluator.evaluate(ArtifactRef(artifact_hash="ab" * 32), {"metrics": legacy_raw})
        assert 0.0 <= result.score <= 1.0  # 读路径保留：历史行仍可计分


class Test缺失即失败且整批不中断:
    def test_缺归属日的条目被拒且零落盘(self, delivered_round, anchors_engine):
        from agents.promo.daily import ingest_daily

        _, env = delivered_round
        adapter = _FirstSnapshotWithoutDate(env["adapter"])
        report = ingest_daily(
            "r-attr",
            env["store"],
            adapter,
            env["engine"],
            env["config"],
            source="real",
            collected_at=1_700_000_000.0,
        )
        # 整批不中断：一条被拒（原因点名归属日） + 其余照常入库
        assert len(report["rejected"]) == 1
        assert "平台未提供指标归属日" in report["rejected"][0]["reason"]
        assert len(report["ingested"]) == 2
        rows = _daily_rows(env["engine"])
        assert len(rows) == 2  # 缺归属日的那条**零落盘**
        assert all(row.metric_date for row in rows)
        assert _anchor_rows(anchors_engine) == []  # 锚点侧亦零新增


class _FirstSnapshotWithoutDate:
    """夹具适配器：第一条把归属日置 `None`（模拟平台未提供），其后照常。"""

    def __init__(self, inner):
        self._inner = inner
        self._first = True

    def fetch_metrics(self, external_id: str) -> MetricSnapshot:
        snapshot = self._inner.fetch_metrics(external_id)
        if self._first:
            self._first = False
            return replace(snapshot, metric_date=None)
        return snapshot


class Test按归属日聚合与三时间并列:
    def test_归属日全同而采集日跨三天只算一天(self, campaigns_engine):
        base = 1_700_000_000.0
        for index in range(3):
            assert _write_day(
                campaigns_engine,
                campaign_id=f"c-{index}",
                metric_date="2026-09-25",
                collected_at=base + index * 86_400,
            )
        coverage = daily_coverage(
            campaigns_engine,
            end="2026-09-25",
            min_window_days=1,
            gap_tolerance_days=0,
            period_days=1,
        )
        assert coverage["covered_days"] == 1
        assert len(coverage["days"]) == 1
        entry = coverage["days"][0]
        assert entry["metric_date"] == "2026-09-25"
        assert entry["collected_at"] == base + 2 * 86_400  # 该日**最晚**采集墙钟
        assert entry["platform_timestamp"] == base + 2 * 86_400 - 1.0

    def test_归属日跨十五天而采集日全同算十五天(self, campaigns_engine):
        first = date(2026, 9, 1)
        for index in range(15):
            assert _write_day(
                campaigns_engine,
                campaign_id=f"c-{index}",
                metric_date=(first + timedelta(days=index)).isoformat(),
                collected_at=1_700_000_000.0,  # 采集墙钟完全相同
            )
        coverage = daily_coverage(
            campaigns_engine,
            end="2026-09-15",
            min_window_days=14,
            gap_tolerance_days=0,
            period_days=1,
        )
        assert coverage["covered_days"] == 15
        assert coverage["meets"] is True
        assert len(coverage["days"]) == 15

    def test_三时间齐备率百分之百(self, campaigns_engine):
        for index in range(3):
            assert _write_day(
                campaigns_engine,
                campaign_id=f"c-{index}",
                metric_date=f"2026-09-2{index + 3}",
                collected_at=1_700_000_000.0 + index,
            )
        coverage = daily_coverage(
            campaigns_engine,
            end="2026-09-25",
            min_window_days=1,
            gap_tolerance_days=0,
            period_days=1,
        )
        assert len(coverage["days"]) == 3
        for entry in coverage["days"]:
            assert _ISO_DATE.fullmatch(entry["metric_date"])
            assert isinstance(entry["collected_at"], float) and entry["collected_at"] > 0
            assert isinstance(entry["platform_timestamp"], float)
            assert entry["platform_timestamp"] > 0


class Test迟到回补只追加:
    def test_迟到回补不改写既有锚点(
        self, campaigns_engine, anchors_engine, promo_config, make_platform_backfill
    ):
        """归属日 `d` 已有锚点 ⇒ 同键幂等拒绝、既有锚点字段逐字节不变。"""
        make_platform_backfill()
        with anchors_engine.begin() as conn:
            first = collect_platform_anchors(
                campaigns_engine, conn, round_id="r-1", config=promo_config
            )
        assert len(first) == 1
        before = _anchor_rows(anchors_engine)[0]
        before_fields = (before.anchor_id, before.node_id, before.score, before.created_at)

        from core.calibration.anchors import insert_anchor

        replay = replace(first[0])  # 同键（node_id, reviewer, round_id）⇒ 必被拒
        with anchors_engine.begin() as conn:
            assert insert_anchor(conn, replay) is False
        after = _anchor_rows(anchors_engine)
        assert len(after) == 1
        assert (after[0].anchor_id, after[0].node_id, after[0].score, after[0].created_at) == (
            before_fields
        )

    def test_迟到时两时间在该日条目并列可见(self, campaigns_engine, anchors_engine, promo_config):
        assert _write_day(
            campaigns_engine,
            campaign_id="camp-late",
            metric_date="2026-09-25",
            collected_at=1_700_000_000.0,
        )
        coverage = daily_coverage(
            campaigns_engine,
            end="2026-09-25",
            min_window_days=1,
            gap_tolerance_days=0,
            period_days=1,
            anchors_engine=anchors_engine,
            required_since=_REQUIRED_SINCE,
        )
        entry = coverage["days"][0]
        assert entry["metric_date"] == "2026-09-25"
        assert entry["collected_at"] == 1_700_000_000.0
        assert entry["platform_timestamp"] == 1_700_000_000.0 - 1.0  # 与归属日**不等同**


class Test历史回退计数与覆盖视图同值:
    def test_回退计数等于视图的_missing_anchors(
        self, campaigns_engine, anchors_engine, promo_config, make_platform_backfill
    ):
        make_platform_backfill(legacy=True)  # 历史行：payload 无 metric_date 键
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="r-legacy", config=promo_config
            )
        assert len(anchors) == 1 and anchors[0].metric_date is None
        expected = attribution_fallback_count(anchors_engine, required_since=_REQUIRED_SINCE)
        assert expected == 1

        assert _write_day(
            campaigns_engine,
            campaign_id="camp-now",
            metric_date="2026-09-25",
            collected_at=1_700_000_000.0,
        )
        coverage = daily_coverage(
            campaigns_engine,
            end="2026-09-25",
            min_window_days=1,
            gap_tolerance_days=0,
            period_days=1,
            anchors_engine=anchors_engine,
            required_since=_REQUIRED_SINCE,
        )
        assert coverage["attribution_missing_anchors"] == expected
        assert "归属日缺失（按 created_at 回退）" in coverage["note"]

    def test_新写入不得走回退(
        self, campaigns_engine, anchors_engine, promo_config, make_platform_backfill
    ):
        """`created_at` 的日期 ≥ 生效日而快照缺归属日 ⇒ **拒绝**（不落锚点、不补值）。"""
        # 平台时间戳取"生效日之后" ⇒ 锚点 created_at 落在归属日必填口径生效期内
        after_since = datetime(2027, 1, 1, tzinfo=UTC).timestamp()
        make_platform_backfill(legacy=True, snapshot={"platform_timestamp": after_since})
        with anchors_engine.begin() as conn:
            anchors = collect_platform_anchors(
                campaigns_engine, conn, round_id="r-new", config=promo_config
            )
        assert anchors == []
        assert _anchor_rows(anchors_engine) == []
