"""ops/screenplay.py produce 子命令单测（功能 009 / T915，先于实现编写）。

CLI 纪律（风格对齐 ops/calibrate.py）：默认面向 PG（--dsn 或 CINEFLOW_PG_DSN）；缺 DSN/
缺题材/策略源码非法一律拒绝并给出人可读 JSON；人工策略**版本 = 源码 BLAKE3 前 12 位**
（--policy <version> 读历史目录并核验版本与内容一致；--policy-file 按源码哈希派生版本）；
策略源码过 002 静态检查后才允许执行（未过检查不入产出）。
产出成功路径：三阶段落树 + 工件内容寻址入 <data-dir>/artifacts + 轮次结果 JSON（退出码 0）。
评估器 = 真实七评估器装配（T923 接线完成，CLI 与 loop 共用 `build_screenplay_evaluators`
唯一装配点）；装配失败 → 退出码 1（配置级装配错误退出码 2），不产生半轮次落盘。
产出用例用**缩放页数窗口的配置副本**（默认夹具 9 行对真实 90 分钟配置必然越界）。
"""

import copy
import importlib.util
import json
from pathlib import Path

import blake3
import pytest
import yaml

from agents.screenplay.loop import ScreenplayLoopError

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "ops" / "screenplay.py"
_REAL_CONFIG = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))


@pytest.fixture()
def cli():
    """按路径加载 CLI 模块（ops/ 非包，无 import 路径；与 CLI 自身 sys.path 约定一致）。"""
    spec = importlib.util.spec_from_file_location("ops_screenplay", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def policy_file(tmp_path, screenplay_policy_source):
    """人工策略源码文件（静态检查必过的合法策略）。"""
    path = tmp_path / "policy.py"
    path.write_text(screenplay_policy_source("valid"), encoding="utf-8")
    return path


@pytest.fixture()
def dsn(tmp_path):
    """文件型 SQLite DSN：跨两次 CLI 调用共享同一库（幂等语义可验）。"""
    return f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"


@pytest.fixture()
def scaled_config(tmp_path):
    """页数窗口按夹具策略产出缩放的真实配置副本。

    夹具策略每阶段行数 6/6/9（outline/scenes/script）→ lines_per_page=3 得 2/2/3 页，
    故窗口取目标 2 页 ± 1（真实 90 分钟配置下这些短形态工件必然越界）。
    """
    raw = copy.deepcopy(_REAL_CONFIG)
    raw["screenplay"].update({"target_duration_min": 2, "page_tolerance": 1, "lines_per_page": 3})
    path = tmp_path / "movie_scaled.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _version_of(path: Path) -> str:
    return blake3.blake3(path.read_bytes()).hexdigest()[:12]


def _invoke(cli, capsys, *argv) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _produce_args(
    round_id: str,
    policy_path: Path,
    dsn: str,
    data_dir: Path,
    *,
    config_path: Path | None = None,
    extra=(),
):
    return (
        "produce",
        "--round",
        round_id,
        "--topic",
        "病房里的三个月",
        "--constraints",
        "单场景为主",
        "不出现旁白",
        "--characters",
        "林静",
        "陈默",
        "周医生",
        "--policy-file",
        str(policy_path),
        "--config",
        str(config_path or REPO_ROOT / "configs" / "movie.yaml"),
        "--data-dir",
        str(data_dir),
        "--dsn",
        dsn,
        *extra,
    )


class Test参数与帮助:
    def test_顶层帮助列出_produce(self, cli, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--help"])
        assert exc.value.code == 0
        assert "produce" in capsys.readouterr().out

    def test_produce_帮助列出全部参数(self, cli, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["produce", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in (
            "--round",
            "--topic",
            "--constraints",
            "--characters",
            "--target-duration-min",
            "--policy",
            "--policy-file",
            "--policy-dir",
            "--config",
            "--data-dir",
            "--dsn",
        ):
            assert flag in out

    def test_缺必填参数即报错(self, cli):
        with pytest.raises(SystemExit) as exc:
            cli.main(["produce", "--round", "r1"])
        assert exc.value.code == 2


class Test依赖与策略错误:
    def test_缺_dsn(self, cli, capsys, policy_file, tmp_path, monkeypatch):
        monkeypatch.delenv("CINEFLOW_PG_DSN", raising=False)
        code, payload = _invoke(
            cli,
            capsys,
            "produce",
            "--round",
            "r1",
            "--topic",
            "题材",
            "--policy-file",
            str(policy_file),
            "--data-dir",
            str(tmp_path / "data"),
        )
        assert code == 2
        assert "DSN" in payload["error"]

    def test_缺题材参数即报错(self, cli):
        with pytest.raises(SystemExit) as exc:
            cli.main(["produce", "--round", "r1", "--dsn", "sqlite+pysqlite:///:memory:"])
        assert exc.value.code == 2

    def test_策略源码不存在(self, cli, capsys, tmp_path, dsn):
        """--policy-dir 是**历史根目录**：版本源码位于 <policy-dir>/screenplay/{version}.py。"""
        code, payload = _invoke(
            cli,
            capsys,
            "produce",
            "--round",
            "r1",
            "--topic",
            "题材",
            "--policy",
            "9f2c41ab77de",
            "--policy-dir",
            str(tmp_path / "history"),
            "--data-dir",
            str(tmp_path / "data"),
            "--dsn",
            dsn,
        )
        assert code == 2
        assert "9f2c41ab77de" in payload["error"]
        assert "screenplay" in payload["error"]  # 解析路径含 Agent 子目录

    def test_按历史目录解析策略版本(self, cli, capsys, policy_file, scaled_config, tmp_path, dsn):
        """--policy <version>（历史根目录 + Agent 子目录）可正常产出。"""
        history = tmp_path / "history"
        (history / "screenplay").mkdir(parents=True)
        version = _version_of(policy_file)
        (history / "screenplay" / f"{version}.py").write_text(
            policy_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        args = list(
            _produce_args(
                "cli-hist", policy_file, dsn, tmp_path / "data", config_path=scaled_config
            )
        )
        args[args.index("--policy-file")] = "--policy"
        args[args.index(str(policy_file))] = version
        args += ["--policy-dir", str(history)]
        code, payload = _invoke(cli, capsys, *args)
        assert code == 0
        assert payload["policy_version"] == version

    def test_策略版本与源码不符被拒(self, cli, capsys, policy_file, tmp_path, dsn):
        """版本 = 源码 BLAKE3 前 12 位：版本与内容不一致即拒绝（人工版本可核验）。"""
        history = tmp_path / "history"
        history.mkdir()
        (history / "000000000000.py").write_text(
            policy_file.read_text(encoding="utf-8"), encoding="utf-8"
        )
        code, payload = _invoke(
            cli,
            capsys,
            "produce",
            "--round",
            "r1",
            "--topic",
            "题材",
            "--policy",
            "000000000000",
            "--policy-dir",
            str(history),
            "--data-dir",
            str(tmp_path / "data"),
            "--dsn",
            dsn,
        )
        assert code == 2
        assert "版本" in payload["error"]

    def test_静态检查不过的策略被拒(self, cli, capsys, tmp_path, dsn, screenplay_policy_source):
        """002 静态检查第一道防线：未过检查不执行（0 网关 0 落盘）。"""
        bad = tmp_path / "bad.py"
        bad.write_text(screenplay_policy_source("forbidden_import"), encoding="utf-8")
        code, payload = _invoke(cli, capsys, *_produce_args("r1", bad, dsn, tmp_path / "data"))
        assert code == 2
        assert "静态检查" in payload["error"]

    def test_配置文件不存在(self, cli, capsys, policy_file, tmp_path, dsn):
        args = list(_produce_args("r1", policy_file, dsn, tmp_path / "data"))
        args[args.index("--config") + 1] = str(tmp_path / "missing.yaml")
        code, payload = _invoke(cli, capsys, *args)
        assert code == 2
        assert "配置" in payload["error"]

    def test_配置非法(self, cli, capsys, policy_file, tmp_path, dsn):
        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("form: movie\n", encoding="utf-8")  # 缺 screenplay 段
        args = list(_produce_args("r1", policy_file, dsn, tmp_path / "data"))
        args[args.index("--config") + 1] = str(bad_config)
        code, payload = _invoke(cli, capsys, *args)
        assert code == 2
        assert "形态配置非法" in payload["error"]

    def test_真实后端缺凭证被拒(self, cli, capsys, monkeypatch, policy_file, tmp_path, dsn):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        args = [*_produce_args("r1", policy_file, dsn, tmp_path / "data"), "--backend", "http"]
        code, payload = _invoke(cli, capsys, *args)
        assert code == 2
        assert "网关后端不可用" in payload["error"]

    def test_策略缺_Policy_类被拒(self, cli, capsys, tmp_path, dsn):
        source = tmp_path / "empty.py"
        source.write_text("VALUE = 1\n", encoding="utf-8")
        code, payload = _invoke(cli, capsys, *_produce_args("r1", source, dsn, tmp_path / "data"))
        assert code == 2
        assert "Policy" in payload["error"]


class Test产出:
    def test_produce_三阶段落树并输出轮次结果(
        self, cli, capsys, policy_file, scaled_config, tmp_path, dsn
    ):
        """真实七评估器全链路：三阶段落树 + 工件内容寻址 + 轮次结果 JSON（退出码 0）。"""
        data_dir = tmp_path / "screenplay"
        code, payload = _invoke(
            cli,
            capsys,
            *_produce_args("cli-1", policy_file, dsn, data_dir, config_path=scaled_config),
        )
        assert code == 0
        assert payload["round_id"] == "cli-1"
        assert payload["tree_id"] == "screenplay-round-cli-1"
        assert payload["policy_version"] == _version_of(policy_file)
        assert [job["stage"] for job in payload["jobs"]] == ["outline", "scenes", "script"]
        assert [job["status"] for job in payload["jobs"]] == ["inserted"] * 3
        assert payload["spent_usd"] > 0
        assert payload["cost_reconciliation"]["consistent"] is True
        assert payload["cost_reconciliation"]["evaluator_cost_usd"] > 0  # judge 仅大纲阶段计费
        assert payload["data_dir"] == str(data_dir)
        # 工件内容寻址落 <data-dir>/artifacts（三阶段三份工件）
        artifacts = sorted((data_dir / "artifacts").iterdir())
        assert len(artifacts) == 3
        assert {path.name for path in artifacts} == {
            job["artifact_hash"] for job in payload["jobs"]
        }

    def test_同轮次二次触发幂等(self, cli, capsys, policy_file, scaled_config, tmp_path, dsn):
        data_dir = tmp_path / "screenplay"
        args = _produce_args("cli-2", policy_file, dsn, data_dir, config_path=scaled_config)
        first = _invoke(cli, capsys, *args)
        second = _invoke(cli, capsys, *args)
        assert first[0] == second[0] == 0
        assert second[1]["jobs"] == first[1]["jobs"]
        assert second[1]["spent_usd"] == pytest.approx(first[1]["spent_usd"])
        assert len(list((data_dir / "artifacts").iterdir())) == 3  # 0 重复工件

    def test_评估器装配失败退出码_1(
        self, cli, capsys, monkeypatch, policy_file, scaled_config, tmp_path, dsn
    ):
        """装配失败 → 退出码 1 且不产生半轮次落盘（0 工件）。"""
        from agents.screenplay import evaluators as evaluators_package

        def _unavailable(config, gateway):
            raise ScreenplayLoopError("七评估器装配失败（测试注入）")

        monkeypatch.setattr(evaluators_package, "build_screenplay_evaluators", _unavailable)
        data_dir = tmp_path / "screenplay"
        code, payload = _invoke(
            cli,
            capsys,
            *_produce_args("cli-3", policy_file, dsn, data_dir, config_path=scaled_config),
        )
        assert code == 1
        assert "装配失败" in payload["error"]
        assert list((data_dir / "artifacts").iterdir()) == []  # 0 工件（无半轮次落盘）

    def test_装配与权重节不一致退出码_2(self, cli, capsys, policy_file, tmp_path, dsn):
        """权重节与装配的评估器不一致（配置漂移）→ 配置级拒绝，退出码 2。"""
        raw = copy.deepcopy(_REAL_CONFIG)
        raw["screenplay"].update(
            {"target_duration_min": 2, "page_tolerance": 1, "lines_per_page": 3}
        )
        del raw["evaluator_weights"]["screenplay"]["proxy.timeline_conflict"]
        config_path = tmp_path / "movie_drifted.yaml"
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        code, payload = _invoke(
            cli,
            capsys,
            *_produce_args("cli-3b", policy_file, dsn, tmp_path / "data", config_path=config_path),
        )
        assert code == 2
        assert "评估器装配失败" in payload["error"]

    def test_缺_topic_的输入由闭环拒绝(
        self, cli, capsys, policy_file, scaled_config, tmp_path, dsn
    ):
        """输入预检在闭环内：CLI 把闭环拒绝如实映射为退出码 1（不吞错）。"""
        args = list(
            _produce_args(
                "cli-4", policy_file, dsn, tmp_path / "screenplay", config_path=scaled_config
            )
        )
        args[args.index("--topic") + 1] = ""
        code, payload = _invoke(cli, capsys, *args)
        assert code == 1
        assert payload["error"]


class TestSubmit子命令:
    """C13：CLI submit = 人工策略提交通道（静态检查 + 接口校验 + 版本化落盘）。"""

    def _submit_args(self, source_path: Path, history: Path, *, extra=()):
        return (
            "submit",
            "--source-file",
            str(source_path),
            "--by",
            "sunqi",
            "--policy-dir",
            str(history),
            "--config",
            str(REPO_ROOT / "configs" / "movie.yaml"),
            *extra,
        )

    def test_帮助列出参数(self, cli, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["submit", "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in ("--source-file", "--by", "--parent-version", "--draft", "--policy-dir"):
            assert flag in out

    def test_合法提交落盘并输出记录(self, cli, capsys, policy_file, tmp_path):
        history = tmp_path / "history"
        code, payload = _invoke(cli, capsys, *self._submit_args(policy_file, history))
        assert code == 0
        assert payload["version"] == _version_of(policy_file)
        assert payload["recorded"] is True
        assert payload["submitter"] == "sunqi"
        assert payload["static_check"] == "passed"
        assert Path(payload["source_path"]).is_file()
        assert Path(payload["meta_path"]).is_file()
        meta = json.loads(Path(payload["meta_path"]).read_text(encoding="utf-8"))
        assert meta["source"] == "manual"
        assert meta["no_auto_evolve"] is True  # 降级模式名单审计

    def test_违规提交退出码_2_且历史无新增(self, cli, capsys, tmp_path, screenplay_policy_source):
        bad = tmp_path / "bad.py"
        bad.write_text(screenplay_policy_source("forbidden_import"), encoding="utf-8")
        history = tmp_path / "history"
        code, payload = _invoke(cli, capsys, *self._submit_args(bad, history))
        assert code == 2
        assert "静态检查" in payload["error"]
        assert not history.exists() or list(history.rglob("*")) == []

    def test_签名错退出码_2_且历史无新增(self, cli, capsys, tmp_path, screenplay_policy_source):
        bad = tmp_path / "bad_signature.py"
        bad.write_text(screenplay_policy_source("bad_signature"), encoding="utf-8")
        history = tmp_path / "history"
        code, payload = _invoke(cli, capsys, *self._submit_args(bad, history))
        assert code == 2
        assert "plan" in payload["error"]

    def test_草稿不入历史(self, cli, capsys, policy_file, tmp_path):
        history = tmp_path / "history"
        code, payload = _invoke(
            cli, capsys, *self._submit_args(policy_file, history, extra=("--draft",))
        )
        assert code == 0
        assert payload["recorded"] is False
        assert payload["version"] == _version_of(policy_file)
        assert not history.exists() or list(history.rglob("*")) == []

    def test_重复提交幂等(self, cli, capsys, policy_file, tmp_path):
        history = tmp_path / "history"
        first = _invoke(cli, capsys, *self._submit_args(policy_file, history))
        files = sorted(path.name for path in (history / "screenplay").iterdir())
        second = _invoke(cli, capsys, *self._submit_args(policy_file, history))
        assert second[1]["version"] == first[1]["version"]
        assert sorted(path.name for path in (history / "screenplay").iterdir()) == files

    def test_缺提交人即报错(self, cli, policy_file, tmp_path):
        with pytest.raises(SystemExit) as exc:
            cli.main(["submit", "--source-file", str(policy_file)])
        assert exc.value.code == 2

    def test_源码不存在退出码_2(self, cli, capsys, tmp_path):
        code, payload = _invoke(
            cli, capsys, *self._submit_args(tmp_path / "missing.py", tmp_path / "history")
        )
        assert code == 2
        assert "源码不存在" in payload["error"]


class TestCompareAdoptRejectEvidence子命令:
    """T931 补全：compare / adopt / reject / evidence 子命令（库函数组装 + 退出码语义）。"""

    def test_顶层帮助列出全部子命令(self, cli, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        out = capsys.readouterr().out
        for name in ("produce", "submit", "compare", "adopt", "reject", "evidence"):
            assert name in out

    @pytest.mark.parametrize(
        "command, flags",
        [
            ("compare", ("--new-version", "--deployed-version", "--unbiasedness", "--topic")),
            ("adopt", ("--comparison", "--by", "--reason")),
            ("reject", ("--comparison", "--by", "--reason")),
            ("evidence", ("--period", "--drift", "--calibration-dir", "--override")),
        ],
    )
    def test_子命令帮助列出参数(self, cli, capsys, command, flags):
        with pytest.raises(SystemExit) as exc:
            cli.main([command, "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        for flag in flags:
            assert flag in out

    def test_compare_缺_dsn(self, cli, capsys, tmp_path, monkeypatch):
        monkeypatch.delenv("CINEFLOW_PG_DSN", raising=False)
        report = tmp_path / "unbiased.json"
        report.write_text(
            json.dumps({"verdict": "pass", "tau": 1.0, "threshold": 0.95, "notes": ""}),
            encoding="utf-8",
        )
        code, payload = _invoke(
            cli,
            capsys,
            "compare",
            "--new-version",
            "111111111111",
            "--deployed-version",
            "222222222222",
            "--unbiasedness",
            str(report),
            "--topic",
            "题材",
        )
        assert code == 2
        assert "DSN" in payload["error"]

    def test_compare_无偏性未过拒绝(self, cli, capsys, tmp_path, dsn):
        """FR-013：未过无偏性验收不得产出对比报告（退出码 2 + 原因）。"""
        report = tmp_path / "unbiased.json"
        report.write_text(
            json.dumps({"verdict": "reject", "tau": -1.0, "threshold": 0.95, "notes": ""}),
            encoding="utf-8",
        )
        code, payload = _invoke(
            cli,
            capsys,
            "compare",
            "--new-version",
            "111111111111",
            "--deployed-version",
            "222222222222",
            "--unbiasedness",
            str(report),
            "--topic",
            "题材",
            "--dsn",
            dsn,
        )
        assert code == 2
        assert "无偏性" in payload["error"]

    def test_compare_验收结论缺失(self, cli, capsys, tmp_path, dsn):
        code, payload = _invoke(
            cli,
            capsys,
            "compare",
            "--new-version",
            "111111111111",
            "--deployed-version",
            "222222222222",
            "--unbiasedness",
            str(tmp_path / "missing.json"),
            "--topic",
            "题材",
            "--dsn",
            dsn,
        )
        assert code == 2
        assert "无偏性" in payload["error"]

    def test_adopt_缺对比报告拒绝(self, cli, capsys, tmp_path):
        code, payload = _invoke(
            cli,
            capsys,
            "adopt",
            "--comparison",
            "cmp-screenplay-a-b",
            "--by",
            "sunqi",
            "--reason",
            "具备优势",
            "--config",
            str(REPO_ROOT / "configs" / "movie.yaml"),
            "--data-dir",
            str(tmp_path / "data"),
        )
        assert code == 2
        assert "对比报告" in payload["error"]

    def test_reject_理由为空拒绝(self, cli, capsys, tmp_path):
        code, payload = _invoke(
            cli,
            capsys,
            "reject",
            "--comparison",
            "cmp-screenplay-a-b",
            "--by",
            "sunqi",
            "--reason",
            "   ",
            "--data-dir",
            str(tmp_path / "data"),
        )
        assert code == 2
        assert "理由" in payload["error"]

    def test_evidence_生成材料并标注未测量漂移(self, cli, capsys, tmp_path):
        """判据材料：无台账记录 + 未测漂移 → 结论 below 并如实标注（不暗示可升级）。"""
        code, payload = _invoke(
            cli,
            capsys,
            "evidence",
            "--period",
            "2026-W38",
            "--data-dir",
            str(tmp_path / "events"),
            "--calibration-dir",
            str(tmp_path / "calibration"),
            "--config",
            str(REPO_ROOT / "configs" / "movie.yaml"),
        )
        assert code == 0
        assert payload["conclusion"] == "below"
        assert any("台账" in reason for reason in payload["reasons"])
        assert any("漂移指标缺失" in reason for reason in payload["reasons"])
        assert payload["threshold_snapshot"]["judge_r_target"] == 0.6
        assert Path(tmp_path / "events" / "2026-W38.json").is_file()

    def test_evidence_达标路径需带内漂移与台账(self, cli, capsys, tmp_path, monkeypatch):
        """显式传入带内漂移 + 010 台账达标记录 → meets（判据四条齐达）。"""
        calibration_dir = tmp_path / "calibration"
        ledger_dir = calibration_dir / "ledger" / "screenplay"
        ledger_dir.mkdir(parents=True)
        record = {
            "evaluator_key": "judge.dramatic_tension@1.0.0+j1",
            "period": "2026-W38",
            "samples": 8,
            "kendall_tau": 0.72,
            "note": "",
        }
        (ledger_dir / "judge.dramatic_tension.jsonl").write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        code, payload = _invoke(
            cli,
            capsys,
            "evidence",
            "--period",
            "2026-W38",
            "--drift",
            "0.05",
            "--human-anchor-count",
            "5",
            "--data-dir",
            str(tmp_path / "events"),
            "--calibration-dir",
            str(calibration_dir),
            "--config",
            str(REPO_ROOT / "configs" / "movie.yaml"),
        )
        assert code == 0
        assert payload["conclusion"] == "meets"
        assert payload["raw"]["judge_samples"] == 8
        assert payload["raw"]["drift"] == pytest.approx(0.05)

    def test_evidence_推翻留痕(self, cli, capsys, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        material = {
            "period": "2026-W39",
            "agent_id": "screenplay",
            "threshold_snapshot": {},
            "raw": {},
            "conclusion": "below",
            "reasons": [],
            "alerts": [],
            "human_anchor_count": 0,
            "created_at": "2026-09-20T00:00:00+00:00",
            "overrides": [],
        }
        from agents.screenplay.upgrade_evidence import _system_digest

        material["system_digest"] = _system_digest(material)
        (events / "2026-W39.json").write_text(
            json.dumps(material, ensure_ascii=False), encoding="utf-8"
        )
        code, payload = _invoke(
            cli,
            capsys,
            "evidence",
            "--period",
            "2026-W39",
            "--override",
            "--by",
            "sunqi",
            "--reason",
            "已补齐锚点",
            "--data-dir",
            str(events),
        )
        assert code == 0
        assert payload["conclusion"] == "below"  # 系统结论不变
        assert payload["overrides"] == [
            {"by": "sunqi", "reason": "已补齐锚点", "at": payload["overrides"][0]["at"]}
        ]

    def test_evidence_推翻缺人拒绝(self, cli, capsys, tmp_path):
        code, payload = _invoke(
            cli,
            capsys,
            "evidence",
            "--period",
            "2026-W39",
            "--override",
            "--reason",
            "理由",
            "--data-dir",
            str(tmp_path / "events"),
        )
        assert code == 2
        assert "--by" in payload["error"]
