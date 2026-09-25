"""校准结论迁移面契约（功能 020 / T2064）：C15 的"不改既有节点"机检 + C17 的退出码语义。

复用的机检口径与 `tests/contract/test_calibration_contracts.py` **逐条同源**（SC-006）：

- 采纳迁移件**不得**改动任何既有节点的 `score` 与 `eval_breakdown`（逐字节一致）；
- 采纳**不得**改动形态配置的 `evaluator_weights`（权重零改动，hash 不变）；
- 跑迁移（含采纳）后 010 的台账 / 快照 / 信度报告 / 漂移产物**逐字节不变**（来源只读）；
- `not_transferable ⇒ status != "confirmed"`（**误迁移次数恒 0**）；不可比即**拒绝迁移**、
  按条件逐条记原因；
- 改写/省略系统字段 **100% 被拒**（append-only + `system_digest` 机检）。
"""

import hashlib
import importlib
import json
from pathlib import Path

import pytest
import yaml

from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"
MOVIE = REPO_ROOT / "configs" / "movie.yaml"
PERIOD = "2026-09-25"
EVALUATOR_KEY = "judge.dramatic_tension@1.0.0"
AGENT_ID = "screenplay"
_BREAKDOWN = {
    "proxy.aesthetic@1.0.0": {"score": 0.6},
    "judge.dramatic_tension@1.0.0": {"score": 0.5},
}


def _source_data_dir(tmp_path: Path, *, samples: int = 42) -> Path:
    """来源件夹具：台账 + 快照 + 信度报告 + 漂移产物（**只读面**）。"""
    from core.calibration.drift_config import DriftConfig
    from core.calibration.drift_metrics import detector_version

    data_dir = tmp_path / "calibration"
    ledger = data_dir / "ledger" / AGENT_ID
    ledger.mkdir(parents=True)
    evaluator_id = EVALUATOR_KEY.partition("@")[0]
    (ledger / f"{evaluator_id}.jsonl").write_text(
        json.dumps(
            {
                "evaluator_key": EVALUATOR_KEY,
                "period": PERIOD,
                "period_days": 1,
                "samples": samples,
                "kendall_tau": 0.71,
                "mean_shift": 0.03,
                "round_id": "r-contract",
                "anchor_count": samples,
                "snapshot_fingerprint": "b" * 64,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = data_dir / "snapshots" / AGENT_ID / evaluator_id
    snapshot.mkdir(parents=True)
    (snapshot / f"{PERIOD}.json").write_text(
        json.dumps({"buckets": [1] * 10, "samples": samples}), encoding="utf-8"
    )
    reports = data_dir / "reports"
    reports.mkdir(parents=True)
    (reports / f"{PERIOD}.json").write_text(
        json.dumps({"period": PERIOD, "agents": {}, "target": 0.6, "alerts": []}),
        encoding="utf-8",
    )
    drift = data_dir / "drift" / "metrics" / AGENT_ID / evaluator_id
    drift.mkdir(parents=True)
    (drift / f"{PERIOD}.json").write_text(
        json.dumps(
            {
                "verdict": "normal",
                "samples": samples,
                "snapshot_fingerprint": "b" * 64,
                "detector_version": detector_version(
                    DriftConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
                ),
            }
        ),
        encoding="utf-8",
    )
    return data_dir


def _files_digest(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "transfers" not in path.parts
    }


def _build(data_dir: Path, **kwargs) -> dict:
    from core.calibration.transfer import build_transfer

    options = {"real_coverage_days": 15, "coverage_source": "fixture_drill"}
    options.update(kwargs)
    return build_transfer(
        data_dir,
        source_config_path=SHORTDRAMA,
        target_config_path=MOVIE,
        evaluator_key=EVALUATOR_KEY,
        period=PERIOD,
        **options,
    )


def _cli(argv, capsys):
    code = importlib.import_module("ops.transfer").main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


@pytest.fixture()
def calibration_tree(tree_store, build_calibration_tree):
    """既有节点夹具（视觉线 3 节点）：迁移前后 `score` / `eval_breakdown` 必须逐字节一致。"""
    from datetime import UTC, datetime

    _, node_ids = build_calibration_tree(
        [(0.5, dict(_BREAKDOWN)), (0.7, dict(_BREAKDOWN)), (0.9, dict(_BREAKDOWN))],
        agent_id="visual",
        base_created_at=datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp(),
    )
    return node_ids


def _node_snapshot(tree_store, node_ids) -> dict:
    return {
        node_id: (
            tree_store.get_node(node_id).score,
            json.dumps(tree_store.get_node(node_id).eval_breakdown, sort_keys=True),
        )
        for node_id in node_ids
    }


class TestC15迁移不改既有节点:
    def test_采纳前后节点与配置逐字节一致(self, tmp_path, calibration_tree, tree_store):
        data_dir = _source_data_dir(tmp_path)
        before_nodes = _node_snapshot(tree_store, calibration_tree)
        before_config = hashlib.sha256(MOVIE.read_bytes()).hexdigest()
        before_sources = _files_digest(data_dir)

        from core.calibration.transfer import append_transfer_decision, save_transfer

        payload = _build(data_dir)
        assert payload["comparability"]["verdict"] == "transferable"
        save_transfer(data_dir, payload)
        append_transfer_decision(
            data_dir,
            payload["transfer_id"],
            decision="confirmed",
            by="制片-甲",
            reason="口径复核通过",
        )
        # SC-006 同源机检：既有节点 score / eval_breakdown 逐字节一致（权重零改动）
        assert _node_snapshot(tree_store, calibration_tree) == before_nodes
        # 形态配置（权重面）逐字节不变
        assert hashlib.sha256(MOVIE.read_bytes()).hexdigest() == before_config
        # 010 既有产物逐字节不变（来源只读）
        assert _files_digest(data_dir) == before_sources

    def test_迁移件只落_transfers_目录(self, tmp_path):
        data_dir = _source_data_dir(tmp_path)
        from core.calibration.transfer import save_transfer

        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        assert sorted(
            path.relative_to(data_dir).as_posix()
            for path in data_dir.rglob("*.json")
            if "transfers" in path.parts
        ) == [f"transfers/{payload['transfer_id']}.json"]
        assert payload["source_form"] == "shortdrama" and payload["target_form"] == "movie"
        assert payload["transfer_basis"] == "conclusion_only"

    def test_短剧线数字不出现在电影线证据面(self, tmp_path):
        """异形态数值不得冒充本形态证据：来源侧数字只在迁移件里（不在报告/快照/漂移面）。"""
        data_dir = _source_data_dir(tmp_path)
        from core.calibration.transfer import save_transfer

        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        for path in data_dir.rglob("*.json"):
            if "transfers" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            assert "target_form" not in text
            assert "comparability" not in text


class TestC15误迁移恒零与逐条原因:
    def test_不可迁移即拒绝迁移并留痕(self, tmp_path, capsys):
        data_dir = _source_data_dir(tmp_path, samples=12)
        payload = _build(data_dir)
        assert payload["comparability"]["verdict"] == "not_transferable"
        assert payload["comparability"]["reasons"]
        for reason in payload["comparability"]["reasons"]:
            assert reason.split("：")[0] in {
                entry["id"] for entry in payload["comparability"]["conditions"]
            }
        from core.calibration.transfer import TransferError, append_transfer_decision, save_transfer

        save_transfer(data_dir, payload)
        with pytest.raises(TransferError, match="不可比即"):
            append_transfer_decision(
                data_dir,
                payload["transfer_id"],
                decision="confirmed",
                by="制片-甲",
                reason="试采纳",
            )
        from core.calibration.transfer import load_transfer

        assert load_transfer(data_dir, payload["transfer_id"])["status"] == "pending"

    def test_不可迁移件只能搁置(self, tmp_path):
        data_dir = _source_data_dir(tmp_path, samples=12)
        from core.calibration.transfer import append_transfer_decision, save_transfer

        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        assert (
            append_transfer_decision(
                data_dir,
                payload["transfer_id"],
                decision="shelved",
                by="制片-乙",
                reason="样本不足",
            )["status"]
            == "shelved"
        )

    def test_改写与省略系统字段百分百被拒(self, tmp_path):
        from core.billing.bill import SnapshotIntegrityError
        from core.calibration.transfer import load_transfer, save_transfer

        data_dir = _source_data_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        original = json.loads(path.read_text(encoding="utf-8"))
        for mutate in (
            lambda data: data.__setitem__(
                "comparability", {"conditions": [], "verdict": "transferable"}
            ),
            lambda data: data.__setitem__("conclusion", {"kendall_tau": 0.99}),
            lambda data: data.__setitem__("source_ref", []),
            lambda data: data.__setitem__("samples", 999),
            lambda data: data.__setitem__("period", "2026-09-26"),
            lambda data: data.pop("comparability"),
        ):
            tampered = json.loads(json.dumps(original))
            mutate(tampered)
            path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
            with pytest.raises(SnapshotIntegrityError):
                load_transfer(data_dir, payload["transfer_id"])
        path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        assert load_transfer(data_dir, payload["transfer_id"])["status"] == "pending"


class TestC17CLI与退出码:
    def test_四子命令_help_退出零(self, capsys):
        for command in ("transfer", "transfer-confirm", "transfer-shelve", "transfer-report"):
            with pytest.raises(SystemExit) as exc:
                _cli([command, "--help"], capsys)
            assert exc.value.code == 0

    def test_dry_run_零落盘_非_dry_run_落件_同键重产退出一(self, tmp_path, capsys):
        data_dir = _source_data_dir(tmp_path)
        argv = [
            "transfer",
            "--data-dir",
            str(data_dir),
            "--from",
            str(SHORTDRAMA),
            "--to",
            str(MOVIE),
            "--evaluator",
            EVALUATOR_KEY,
            "--period",
            PERIOD,
            "--real-covered-days",
            "15",
            "--coverage-source",
            "fixture_drill",
        ]
        before = sorted(path.name for path in data_dir.rglob("*") if path.is_file())
        code, out = _cli([*argv, "--dry-run"], capsys)
        assert code == 0 and out["written"] is None
        assert sorted(path.name for path in data_dir.rglob("*") if path.is_file()) == before
        code, out = _cli(argv, capsys)
        assert code == 0 and Path(out["written"]).is_file()
        code, out = _cli(argv, capsys)
        assert code == 1 and "append-only" in out["error"]

    def test_报表无来源如实标注退出零(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        code, out = _cli(
            ["transfer-report", "--data-dir", str(empty), "--config", str(MOVIE)], capsys
        )
        assert code == 0
        assert out["conclusion"] == "无可迁移结论（来源缺失）"
        assert out["observe_conditions"] == {
            "min_real_days": 14,
            "min_samples": 30,
            "note": "继续观察条件：来源真实覆盖 ≥ min_real_days 且样本 ≥ min_samples",
        }
        assert out["evidence_claim"] == "mechanism_ready_real_feedback_pending"

    def test_报表件被改写即拒采信退出一(self, tmp_path, capsys):
        data_dir = _source_data_dir(tmp_path)
        from core.calibration.transfer import save_transfer

        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["comparability"]["verdict"] = "not_transferable"  # 改写系统字段
        path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        code, out = _cli(["transfer-report", "--data-dir", str(data_dir)], capsys)
        assert code == 1 and "拒采信" in out["error"]

    @pytest.mark.parametrize("form", declared_forms(REPO_ROOT / "configs"))
    @pytest.mark.parametrize(
        "missing", ("basis", "source_forms", "target_forms", "conditions", "storage", "adoption")
    )
    def test_缺_transfer_任一键退出二(self, form, missing, tmp_path, capsys):
        payload = yaml.safe_load(
            (REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8")
        )
        payload["calibration"]["transfer"].pop(missing)
        path = tmp_path / f"{form}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        # 对照形态取派生面里**另一个**形态的真实配置（不是"另一形态"特判，也不是固定两形态）
        other_form = next(name for name in declared_forms(REPO_ROOT / "configs") if name != form)
        other = REPO_ROOT / "configs" / f"{other_form}.yaml"
        code, out = _cli(
            [
                "transfer",
                "--data-dir",
                str(tmp_path / "data"),
                "--from",
                str(path) if form == "shortdrama" else str(other),
                "--to",
                str(path) if form == "movie" else str(other),
                "--evaluator",
                EVALUATOR_KEY,
                "--period",
                PERIOD,
                "--dry-run",
            ],
            capsys,
        )
        assert code == 2, (form, missing, out)
        assert "transfer" in out["error"]

    def test_人工两键退出码与只采纳结论(self, tmp_path, capsys):
        data_dir = _source_data_dir(tmp_path)
        base = [
            "transfer",
            "--data-dir",
            str(data_dir),
            "--from",
            str(SHORTDRAMA),
            "--to",
            str(MOVIE),
            "--evaluator",
            EVALUATOR_KEY,
            "--period",
            PERIOD,
            "--real-covered-days",
            "14",
            "--coverage-source",
            "coverage_view",
        ]
        _, out = _cli(base, capsys)
        assert out["verdict"] == "transferable"
        code, confirmed = _cli(
            [
                "transfer-confirm",
                "--data-dir",
                str(data_dir),
                "--transfer",
                out["transfer_id"],
                "--by",
                "运营-甲",
                "--reason",
                "逐条条件复核通过",
            ],
            capsys,
        )
        assert code == 0 and confirmed["status"] == "confirmed"
        assert confirmed["weights_changed"] is False
        code, again = _cli(
            [
                "transfer-confirm",
                "--data-dir",
                str(data_dir),
                "--transfer",
                out["transfer_id"],
                "--by",
                "运营-乙",
                "--reason",
                "重复采纳",
            ],
            capsys,
        )
        assert code == 1 and "终态不可逆" in again["error"]
