"""校准结论迁移件与迁移面 CLI 单测（功能 020 契约 C15/C17，T2014/T2063/T2065）。

覆盖：

- **条件完备率 100%**：配置声明的每条条件在产物里都有 `declared` / `observed` / `satisfied`；
- `comparability.verdict` 取值域固定 `{transferable, not_transferable}`；不可迁移 ⇒ `reasons`
  非空且每条**前缀点名条件 id**；`status` 取值域固定 `{pending, confirmed, shelved}` 且
  `not_transferable ⇒ status != "confirmed"`（**误迁移次数恒 0**）；
- **零权重键**：`conclusion` 内禁止 `candidate_weights` / `current_weights` / `fit_objective` /
  `ridge_lambda`；`core/calibration/transfer.py` 与 `ops/transfer.py` 零 `refit` import、
  零 `confirm_proposal`、零写树/写库入口（静态断言）；
- **append-only**：同 `transfer_id` 重产 ⇒ 拒绝；系统字段被改写 ⇒ `system_digest` 校验失败 ⇒
  拒采信；
  采纳/搁置**只追加** `overrides[]`；
- **异形态数值不得冒充本形态证据**：迁移件只落 `transfers/`、必带 `source_form` / `target_form`；
  跑迁移后 010 的台账/快照/报告/漂移产物**逐字节不变**（电影线证据面零写入）；
- **CLI**：四个子命令 `--help` 退出 0；`--dry-run` **零落盘**；非 dry-run 落件 + 同键重产 ⇒ 1；
  缺 `calibration.transfer` 任一键 ⇒ 2（两形态各验一次）；`transfer-report` 无来源时如实标注；
- **来源缺失**：如实标注「无可迁移结论（来源缺失）」并给出继续观察条件（不得编造来源）。
"""

import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

from core.calibration.config import (
    TRANSFER_CONDITION_IDS,
    CalibrationConfig,
    TransferConfig,
)
from core.calibration.drift_config import DriftConfig
from core.calibration.drift_metrics import detector_version
from core.calibration.errors import CalibrationConfigError
from core.calibration.transfer import (
    CONCLUSION_KEYS,
    FORBIDDEN_CONCLUSION_KEYS,
    NO_SOURCE_CONCLUSION,
    TRANSFER_STATUSES,
    TransferConflictError,
    TransferSourceMissingError,
    append_transfer_decision,
    build_transfer,
    load_transfer,
    read_source_conclusion,
    save_transfer,
    transfer_id_of,
    transfer_report,
)
from core.evaluators.errors import ValidationError
from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
SHORTDRAMA = REPO_ROOT / "configs" / "shortdrama.yaml"
MOVIE = REPO_ROOT / "configs" / "movie.yaml"
# 形态 id 面（021 T2146）：由 `configs/*.yaml` 的 `form:` 派生 ⇒ 新增形态自动进入遍历面
FORMS = declared_forms(REPO_ROOT / "configs")
PERIOD = "2026-09-25"
EVALUATOR_KEY = "judge.dramatic_tension@1.0.0"
AGENT_ID = "screenplay"


def _section(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _source_dir(
    tmp_path: Path,
    *,
    samples: int = 42,
    kendall_tau: float | None = 0.71,
    mean_shift: float | None = 0.03,
    drift_verdict: str | None = "normal",
    detector_version_from: str | None = "shortdrama",
    with_report: bool = True,
) -> Path:
    """来源件夹具（**只读面**：台账 + 快照 + 信度报告 + 漂移产物）。

    `drift_verdict=None` ⇒ 不落漂移产物（口径不明）；`detector_version_from=None` ⇒
    漂移记录不带口径版本（口径缺失）。
    """
    data_dir = tmp_path / "source"
    ledger = data_dir / "ledger" / AGENT_ID
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / f"{EVALUATOR_KEY.partition('@')[0]}.jsonl").write_text(
        json.dumps(
            {
                "evaluator_key": EVALUATOR_KEY,
                "period": PERIOD,
                "period_days": 1,
                "samples": samples,
                "kendall_tau": kendall_tau,
                "mean_shift": mean_shift,
                "round_id": "r-transfer",
                "anchor_count": samples,
                "snapshot_fingerprint": "a" * 64,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot = data_dir / "snapshots" / AGENT_ID / EVALUATOR_KEY.partition("@")[0]
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / f"{PERIOD}.json").write_text(
        json.dumps({"buckets": [1] * 10, "samples": samples, "period_days": 1}),
        encoding="utf-8",
    )
    if with_report:
        reports = data_dir / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{PERIOD}.json").write_text(
            json.dumps({"period": PERIOD, "agents": {}, "target": 0.6, "alerts": []}),
            encoding="utf-8",
        )
    if drift_verdict is not None:
        drift = data_dir / "drift" / "metrics" / AGENT_ID / EVALUATOR_KEY.partition("@")[0]
        drift.mkdir(parents=True, exist_ok=True)
        record = {"verdict": drift_verdict, "samples": samples, "snapshot_fingerprint": "a" * 64}
        if detector_version_from is not None:
            record["detector_version"] = detector_version(
                DriftConfig.from_yaml(REPO_ROOT / "configs" / f"{detector_version_from}.yaml")
            )
        (drift / f"{PERIOD}.json").write_text(json.dumps(record), encoding="utf-8")
    return data_dir


def _build(data_dir: Path, **kwargs) -> dict:
    payload = {
        "real_coverage_days": 15,
        "coverage_source": "fixture_drill",
        "created_at": "2026-09-25T00:00:00+00:00",
        "source_conclusion": None,
    }
    payload.update(kwargs)
    if payload["source_conclusion"] is None:
        payload.pop("source_conclusion")
    return build_transfer(
        data_dir,
        source_config_path=SHORTDRAMA,
        target_config_path=MOVIE,
        evaluator_key=EVALUATOR_KEY,
        period=PERIOD,
        **payload,
    )


def _conditions(payload: dict) -> dict:
    return {entry["id"]: entry for entry in payload["comparability"]["conditions"]}


def _cli(argv, capsys):
    module = importlib.import_module("ops.transfer")
    code = module.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


def _snapshot_of(root: Path) -> dict:
    """目录 → 文件摘要快照（用于"零落盘"与"逐字节不变"断言）。"""
    import blake3

    return {
        path.relative_to(root).as_posix(): blake3.blake3(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class Test条件完备与判定:
    def test_每条声明条件都带三键且完备率百分之百(self, tmp_path):
        payload = _build(_source_dir(tmp_path))
        declared = CalibrationConfig.from_yaml(SHORTDRAMA).transfer.conditions
        conditions = payload["comparability"]["conditions"]
        assert [entry["id"] for entry in conditions] == list(declared)
        for entry in conditions:
            assert {"id", "declared", "observed", "satisfied"} <= set(entry), entry
            assert entry["declared"] == declared[entry["id"]]
        assert payload["comparability"]["verdict"] == "transferable"
        assert payload["comparability"]["reasons"] == []

    def test_条件全集覆盖五类且在配置面可声明(self):
        declared = CalibrationConfig.from_yaml(MOVIE).transfer.conditions
        # 五类：评估器登记（版本冻结）/ 样本量 / 判定口径 / 周期量纲 / 真实来源
        assert {
            "evaluator_registered",
            "min_samples",
            "detector_version_match",
            "cadence_conversion",
            "real_coverage_days",
        } <= set(declared)
        assert set(declared) == set(TRANSFER_CONDITION_IDS)  # 声明面与实现面一一对应

    def test_形状齐备_迁移件字段面(self, tmp_path):
        payload = _build(_source_dir(tmp_path))
        assert payload["source_form"] == "shortdrama" and payload["target_form"] == "movie"
        assert payload["transfer_basis"] == "conclusion_only"
        assert payload["status"] == "pending"
        assert payload["confirmed_by"] == "" and payload["confirmed_at"] == ""
        assert payload["overrides"] == []
        assert payload["transfer_id"] == transfer_id_of(
            EVALUATOR_KEY, PERIOD, "shortdrama", "movie"
        )
        assert payload["transfer_id"] == f"judge.dramatic_tension-{PERIOD}-shortdrama-movie"
        kinds = [ref["kind"] for ref in payload["source_ref"]]
        assert kinds[:2] == ["ledger", "snapshot"]
        assert all(re.fullmatch(r"[0-9a-f]{64}", ref["digest"]) for ref in payload["source_ref"])
        assert set(payload["conclusion"]) == set(CONCLUSION_KEYS)

    @pytest.mark.parametrize(
        ("kwargs", "condition_ids"),
        (
            ({"samples": 12}, ("min_samples",)),
            ({"kendall_tau": None}, ("reliability_floor",)),
            ({"mean_shift": 0.4}, ("max_abs_mean_shift",)),
            ({"drift_verdict": None}, ("detector_version_match", "require_drift_pass")),
            ({"drift_verdict": "drift"}, ("require_drift_pass",)),
        ),
    )
    def test_不可迁移逐条点名条件_id(self, tmp_path, kwargs, condition_ids):
        payload = _build(_source_dir(tmp_path, **kwargs))
        comparability = payload["comparability"]
        assert comparability["verdict"] == "not_transferable"
        assert comparability["reasons"], "不可迁移必须逐条记原因"
        hit = {entry["id"] for entry in comparability["conditions"] if not entry["satisfied"]}
        assert hit == set(condition_ids)
        for reason in comparability["reasons"]:
            assert reason.split("：")[0] in hit, reason
        assert payload["status"] == "pending"

    def test_覆盖未标定即不可迁移(self, tmp_path):
        payload = _build(
            _source_dir(tmp_path), real_coverage_days=None, coverage_source="untracked"
        )
        entry = _conditions(payload)["real_coverage_days"]
        assert entry["observed"] is None and entry["satisfied"] is False
        assert entry["source"] == "untracked"
        assert payload["comparability"]["verdict"] == "not_transferable"

    def test_量纲不符的周期被拒(self, tmp_path):
        with pytest.raises(ValidationError, match="周期量纲"):
            build_transfer(
                _source_dir(tmp_path),
                source_config_path=SHORTDRAMA,
                target_config_path=MOVIE,
                evaluator_key=EVALUATOR_KEY,
                period="2026-W39",
            )

    def test_覆盖观测来源取值域外即拒(self, tmp_path):
        with pytest.raises(ValidationError, match="取值域外"):
            _build(_source_dir(tmp_path), coverage_source="guess")

    def test_拒绝迁移不降格为参考数字(self, tmp_path):
        """不可比时既不产 `transferable`，也不把数字当"仅供参考"塞进本形态证据面。"""
        payload = _build(_source_dir(tmp_path, samples=12))
        assert payload["comparability"]["verdict"] == "not_transferable"
        assert payload["comparability"]["reasons"]
        # conclusion 仍只含来源侧的**如实**登记（供读者复核），但判定与原因必须齐备
        assert payload["conclusion"]["samples"] == 12


class Test零权重键与静态断言:
    def test_结论内不得出现权重键(self):
        from core.calibration.transfer import _assert_conclusion_shape

        for key in FORBIDDEN_CONCLUSION_KEYS:
            conclusion = dict.fromkeys(CONCLUSION_KEYS)
            conclusion[key] = {"proxy.aesthetic": 0.5}
            with pytest.raises(ValidationError, match="权重键"):
                _assert_conclusion_shape(conclusion)

    def test_结论键取值域外即拒(self):
        from core.calibration.transfer import _assert_conclusion_shape

        with pytest.raises(ValidationError, match="取值域外"):
            _assert_conclusion_shape({**dict.fromkeys(CONCLUSION_KEYS), "extra": 1})

    def test_迁移件内零权重键_机检落件(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        written = json.loads(
            (data_dir / "transfers" / f"{payload['transfer_id']}.json").read_text(encoding="utf-8")
        )
        rendered = json.dumps(written, ensure_ascii=False)
        for key in FORBIDDEN_CONCLUSION_KEYS:
            assert key not in rendered, key

    @pytest.mark.parametrize("name", ["core/calibration/transfer.py", "ops/transfer.py"])
    def test_静态断言_零_refit_与零_confirm_proposal_与零写树写库(self, name):
        """AST 断言（看**代码**而非文案）：零 `refit` import、零 `confirm_proposal` 调用、
        零写树/写库入口（投放/迁移面不得顺带改权重或改写既有节点）。"""
        import ast

        tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
        modules: list[str] = []
        calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Call):
                func = node.func
                calls.append(
                    func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                )
        assert not [module for module in modules if "refit" in module], modules
        banned_calls = ("confirm_proposal", "append_node", "create_tree_store", "create_tree")
        assert not [call for call in calls if call in banned_calls], calls
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        for banned in ("sqlalchemy", "Session(", "CINEFLOW_PG_DSN", "yaml.safe_load_all"):
            assert banned not in source, (name, banned)


class Testappend_only:
    def test_同键重产拒绝(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        before = path.read_bytes()
        with pytest.raises(TransferConflictError, match="append-only"):
            save_transfer(data_dir, payload)
        assert path.read_bytes() == before  # 零字节改写

    def test_系统字段改写即拒采信(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["comparability"]["verdict"] = "transferable"
        tampered["conclusion"]["mean_shift"] = 0.0
        tampered["source_ref"] = []
        path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        from core.billing.bill import SnapshotIntegrityError

        with pytest.raises(SnapshotIntegrityError, match="改写"):
            load_transfer(data_dir, payload["transfer_id"])

    def test_省略系统字段即拒采信(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered.pop("comparability")  # 省略（不是改写）同样被摘要校验抓住
        path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        from core.billing.bill import SnapshotIntegrityError

        with pytest.raises(SnapshotIntegrityError):
            load_transfer(data_dir, payload["transfer_id"])

    def test_人工两键只追加且系统字段逐字节不变(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        before = json.loads(path.read_text(encoding="utf-8"))
        updated = append_transfer_decision(
            data_dir,
            payload["transfer_id"],
            decision="confirmed",
            by="运营A",
            reason="口径复核通过",
        )
        assert updated["status"] == "confirmed"
        assert updated["confirmed_by"] == "运营A" and updated["confirmed_at"]
        after = json.loads(path.read_text(encoding="utf-8"))
        for key in ("comparability", "conclusion", "source_ref", "samples", "period"):
            assert after[key] == before[key], key
        assert after["system_digest"] == before["system_digest"]
        assert [row["decision"] for row in after["overrides"]] == ["confirmed"]
        assert load_transfer(data_dir, payload["transfer_id"])["status"] == "confirmed"


class Test人工两键与误迁移恒零:
    def test_不可迁移件不得采纳(self, tmp_path):
        data_dir = _source_dir(tmp_path, samples=12)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        from core.calibration.transfer import TransferError

        with pytest.raises(TransferError, match="不可比即"):
            append_transfer_decision(
                data_dir, payload["transfer_id"], decision="confirmed", by="运营A", reason="想采纳"
            )
        assert load_transfer(data_dir, payload["transfer_id"])["status"] == "pending"

    def test_不可迁移件可搁置(self, tmp_path):
        data_dir = _source_dir(tmp_path, samples=12)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        updated = append_transfer_decision(
            data_dir, payload["transfer_id"], decision="shelved", by="运营A", reason="样本不足"
        )
        assert updated["status"] == "shelved"

    def test_终态不可逆(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        append_transfer_decision(
            data_dir, payload["transfer_id"], decision="confirmed", by="运营A", reason="通过"
        )
        from core.calibration.transfer import TransferError

        with pytest.raises(TransferError, match="终态不可逆"):
            append_transfer_decision(
                data_dir, payload["transfer_id"], decision="shelved", by="运营B", reason="反悔"
            )

    def test_留痕不得无归属(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        for by, reason in (("", "理由"), ("运营A", "  ")):
            with pytest.raises(ValidationError, match="留痕"):
                append_transfer_decision(
                    data_dir, payload["transfer_id"], decision="confirmed", by=by, reason=reason
                )


class Test只读来源与异形态不冒充:
    def test_跑迁移后来源件逐字节不变(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        before = _snapshot_of(data_dir)
        payload = _build(data_dir)
        save_transfer(data_dir, payload)
        append_transfer_decision(
            data_dir, payload["transfer_id"], decision="confirmed", by="运营A", reason="通过"
        )
        after = _snapshot_of(data_dir)
        # 唯一新增 = transfers/ 下的迁移件；其余（台账/快照/报告/漂移）逐字节不变
        added = {key: value for key, value in after.items() if key not in before}
        assert set(added) == {f"transfers/{payload['transfer_id']}.json"}
        assert before == {key: value for key, value in after.items() if key in before}

    def test_迁移件只落_transfers_且必带两形态(self, tmp_path):
        data_dir = tmp_path / "calibration"
        data_dir.mkdir()
        source = _source_dir(tmp_path)
        payload = _build(source)
        save_transfer(data_dir, payload)
        files = sorted(path.relative_to(data_dir).as_posix() for path in data_dir.rglob("*.json"))
        assert files == [f"transfers/{payload['transfer_id']}.json"]
        assert payload["source_form"] and payload["target_form"]
        # 电影线证据面（报告/快照/漂移）**零**出现迁移数值
        assert not (data_dir / "reports").exists()
        assert not (data_dir / "snapshots").exists()

    def test_来源读取是只读的_无来源即None(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        assert read_source_conclusion(data_dir, evaluator_key=EVALUATOR_KEY, period=PERIOD)
        assert (
            read_source_conclusion(data_dir, evaluator_key=EVALUATOR_KEY, period="2026-09-30")
            is None
        )


class Test来源缺失如实标注:
    def test_无来源即拒且给继续观察条件(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(TransferSourceMissingError) as exc:
            _build(empty)
        message = str(exc.value)
        assert NO_SOURCE_CONCLUSION in message
        assert "min_real_days" not in message  # 文案给的是配置声明的阈值，不是内部字段名
        assert "≥ 14 天" in message and "≥ 30" in message
        assert "不得" in message

    def test_报表无来源时如实标注(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        report = transfer_report(empty, transfer_config=CalibrationConfig.from_yaml(MOVIE).transfer)
        assert report["transfers"] == []
        assert report["conclusion"] == NO_SOURCE_CONCLUSION
        assert report["observe_conditions"]["min_real_days"] == 14
        assert report["observe_conditions"]["min_samples"] == 30
        assert report["evidence_claim"] == "mechanism_ready_real_feedback_pending"
        assert "机制已就绪 / 真实回流待运营" in report["note"]

    def test_报表逐条判定与计数(self, tmp_path):
        data_dir = _source_dir(tmp_path)
        ok = _build(data_dir)
        save_transfer(data_dir, ok)
        report = transfer_report(
            data_dir, transfer_config=CalibrationConfig.from_yaml(MOVIE).transfer
        )
        assert report["counts"]["total"] == 1
        assert report["transfers"][0]["verdict"] == "transferable"
        assert report["transfers"][0]["status"] == "pending"
        assert report["counts"]["not_transferable"] == 0


class Test可比性条件配置化:
    @pytest.mark.parametrize(
        "missing", ("basis", "source_forms", "target_forms", "conditions", "storage", "adoption")
    )
    def test_缺任一项即报错(self, missing):
        section = _section("movie")["calibration"]
        transfer = {key: value for key, value in section["transfer"].items() if key != missing}
        with pytest.raises(CalibrationConfigError, match="transfer"):
            TransferConfig.from_dict({**section, "transfer": transfer})

    def test_缺整段即报错(self):
        with pytest.raises(CalibrationConfigError, match="transfer"):
            TransferConfig.from_dict({"period_days": 7})

    def test_取值域单元素(self):
        section = _section("movie")["calibration"]
        for key, bad in (("basis", "weights_too"), ("adoption", "auto")):
            transfer = {**section["transfer"], key: bad}
            with pytest.raises(CalibrationConfigError, match=key):
                TransferConfig.from_dict({**section, "transfer": transfer})

    def test_未实现的判定项即报错(self):
        section = _section("movie")["calibration"]
        transfer = {
            **section["transfer"],
            "conditions": {**section["transfer"]["conditions"], "invented_check": 1},
        }
        with pytest.raises(CalibrationConfigError, match="未实现的判定项"):
            TransferConfig.from_dict({**section, "transfer": transfer})

    def test_storage_dir_必须是单一相对目录名(self):
        section = _section("movie")["calibration"]
        for bad in ("", "/abs/transfers", "../transfers", "a/b"):
            transfer = {**section["transfer"], "storage": {"dir": bad}}
            with pytest.raises(CalibrationConfigError, match="storage"):
                TransferConfig.from_dict({**section, "transfer": transfer})

    def test_两形态均须声明(self):
        for name in FORMS:
            config = CalibrationConfig.from_yaml(REPO_ROOT / "configs" / f"{name}.yaml")
            assert config.transfer.basis == "conclusion_only"
            assert config.transfer.adoption == "manual"
            assert config.transfer.storage_dir == "transfers"
            assert config.transfer.source_forms and config.transfer.target_forms

    def test_来源与目标形态未声明即拒(self, tmp_path):
        payload = _section("shortdrama")
        payload["calibration"]["transfer"]["source_forms"] = ["movie"]
        path = tmp_path / "shortdrama.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        with pytest.raises(CalibrationConfigError, match="source_forms"):
            build_transfer(
                _source_dir(tmp_path),
                source_config_path=path,
                target_config_path=MOVIE,
                evaluator_key=EVALUATOR_KEY,
                period=PERIOD,
            )


class Test迁移面CLI:
    def test_四子命令_help_均退出零(self, capsys):
        for command in ("transfer", "transfer-confirm", "transfer-shelve", "transfer-report"):
            with pytest.raises(SystemExit) as exc:
                _cli([command, "--help"], capsys)
            assert exc.value.code == 0, command

    def test_dry_run_零落盘(self, tmp_path, capsys):
        data_dir = _source_dir(tmp_path)
        before = _snapshot_of(data_dir)
        code, out = _cli(
            [
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
                "--dry-run",
            ],
            capsys,
        )
        assert code == 0, out
        assert out["dry_run"] is True and out["written"] is None
        assert out["comparability"]["verdict"] == "transferable"
        assert _snapshot_of(data_dir) == before  # **零落盘**：前后文件集合与字节全等

    def test_落件与同键重产(self, tmp_path, capsys):
        data_dir = _source_dir(tmp_path)
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
        code, out = _cli(argv, capsys)
        assert code == 0, out
        assert out["written"].endswith(f"{out['transfer_id']}.json")
        code, out = _cli(argv, capsys)
        assert code == 1 and "append-only" in out["error"]

    def test_不可迁移时退出码一且落件留痕(self, tmp_path, capsys):
        data_dir = _source_dir(tmp_path, samples=12)
        code, out = _cli(
            [
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
            ],
            capsys,
        )
        assert code == 1, out
        assert out["verdict"] == "not_transferable"
        assert out["reasons"] and out["written"]
        assert (data_dir / "transfers" / f"{out['transfer_id']}.json").is_file()

    def test_无来源时退出码一且如实标注(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        code, out = _cli(
            [
                "transfer",
                "--data-dir",
                str(empty),
                "--from",
                str(SHORTDRAMA),
                "--to",
                str(MOVIE),
                "--evaluator",
                EVALUATOR_KEY,
                "--period",
                PERIOD,
            ],
            capsys,
        )
        assert code == 1 and NO_SOURCE_CONCLUSION in out["error"]

    def test_人工两键转绿并留痕(self, tmp_path, capsys):
        data_dir = _source_dir(tmp_path)
        _, out = _cli(
            [
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
            ],
            capsys,
        )
        code, confirmed = _cli(
            [
                "transfer-confirm",
                "--data-dir",
                str(data_dir),
                "--transfer",
                out["transfer_id"],
                "--by",
                "运营A",
                "--reason",
                "口径与可比性复核通过",
            ],
            capsys,
        )
        assert code == 0, confirmed
        assert confirmed["status"] == "confirmed"
        assert confirmed["weights_changed"] is False
        assert confirmed["overrides"][-1]["by"] == "运营A"
        # 已确认的件不得再搁置（终态不可逆）
        code, shelved = _cli(
            [
                "transfer-shelve",
                "--data-dir",
                str(data_dir),
                "--transfer",
                out["transfer_id"],
                "--by",
                "运营B",
                "--reason",
                "反悔",
            ],
            capsys,
        )
        assert code == 1 and "终态不可逆" in shelved["error"]

    def test_report_无来源如实标注且退出零(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()
        code, out = _cli(["transfer-report", "--data-dir", str(empty)], capsys)
        assert code == 0, out
        assert out["conclusion"] == NO_SOURCE_CONCLUSION
        code, out = _cli(
            ["transfer-report", "--data-dir", str(empty), "--config", str(MOVIE)], capsys
        )
        assert code == 0 and out["observe_conditions"]["min_real_days"] == 14

    def test_report_件被改写即拒采信(self, tmp_path, capsys):
        data_dir = _source_dir(tmp_path)
        payload = _build(data_dir)
        path = save_transfer(data_dir, payload)
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["samples"] = 999
        path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        code, out = _cli(["transfer-report", "--data-dir", str(data_dir)], capsys)
        assert code == 1 and "拒采信" in out["error"]

    @pytest.mark.parametrize("form", FORMS)
    def test_缺_transfer_任一键即退出二(self, form, tmp_path, capsys):
        payload = _section(form)
        payload["calibration"]["transfer"].pop("conditions")
        path = tmp_path / f"{form}.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        # 目标形态 = 派生面里**另一个**形态的真实配置（不是固定两形态的特判）
        other_form = next(name for name in FORMS if name != form)
        code, out = _cli(
            [
                "transfer",
                "--data-dir",
                str(tmp_path / "data"),
                "--from",
                str(path),
                "--to",
                str(REPO_ROOT / "configs" / f"{other_form}.yaml"),
                "--evaluator",
                EVALUATOR_KEY,
                "--period",
                PERIOD,
                "--dry-run",
            ],
            capsys,
        )
        assert code == 2, out
        assert "transfer" in out["error"]

    def test_状态取值域常量(self):
        assert TRANSFER_STATUSES == ("pending", "confirmed", "shelved")
