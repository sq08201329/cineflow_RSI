"""ops/screenplay.py produce 子命令单测（功能 009 / T915，先于实现编写）。

CLI 纪律（风格对齐 ops/calibrate.py）：默认面向 PG（--dsn 或 CINEFLOW_PG_DSN）；缺 DSN/
缺题材/策略源码非法一律拒绝并给出人可读 JSON；人工策略**版本 = 源码 BLAKE3 前 12 位**
（--policy <version> 读历史目录并核验版本与内容一致；--policy-file 按源码哈希派生版本）；
策略源码过 002 静态检查后才允许执行（未过检查不入产出）。
产出成功路径：三阶段落树 + 工件内容寻址入 <data-dir>/artifacts + 轮次结果 JSON（退出码 0）。
评估器装配为 T923 接线点：本批经 `agents.screenplay.loop.build_default_evaluators` 注入桩
验证 CLI 全链路（装配失败 → 退出码 1，不产生半轮次落盘）。
"""

import importlib.util
import json
from pathlib import Path

import blake3
import pytest

from agents.screenplay.loop import ScreenplayLoopError
from tests.stubs import StubJudgeEvaluator, StubProxyEvaluator, StubRuleEvaluator

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "ops" / "screenplay.py"

_GATE_IDS = (
    "rule.beat_structure",
    "rule.page_minutes",
    "rule.scene_character",
    "rule.dialogue_action_ratio",
)


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


def _version_of(path: Path) -> str:
    return blake3.blake3(path.read_bytes()).hexdigest()[:12]


def _stub_evaluators(config, gateway):
    """CLI 产出用的评估器桩装配（T923 前替代默认七评估器）。"""
    return [
        *(StubRuleEvaluator(gate_id) for gate_id in _GATE_IDS),
        StubProxyEvaluator("proxy.entity_consistency", score=0.8),
        StubProxyEvaluator("proxy.timeline_conflict", score=0.6),
        StubJudgeEvaluator("judge.dramatic_tension", score=0.7),
    ]


def _invoke(cli, capsys, *argv) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _produce_args(round_id: str, policy_path: Path, dsn: str, data_dir: Path, *, extra=()):
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
        str(REPO_ROOT / "configs" / "movie.yaml"),
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
        self, cli, capsys, monkeypatch, policy_file, tmp_path, dsn
    ):
        from agents.screenplay import loop

        monkeypatch.setattr(loop, "build_default_evaluators", _stub_evaluators)
        data_dir = tmp_path / "screenplay"
        code, payload = _invoke(cli, capsys, *_produce_args("cli-1", policy_file, dsn, data_dir))
        assert code == 0
        assert payload["round_id"] == "cli-1"
        assert payload["tree_id"] == "screenplay-round-cli-1"
        assert payload["policy_version"] == _version_of(policy_file)
        assert [job["stage"] for job in payload["jobs"]] == ["outline", "scenes", "script"]
        assert [job["status"] for job in payload["jobs"]] == ["inserted"] * 3
        assert payload["spent_usd"] > 0
        assert payload["cost_reconciliation"]["consistent"] is True
        assert payload["data_dir"] == str(data_dir)
        # 工件内容寻址落 <data-dir>/artifacts（三阶段三份工件）
        artifacts = sorted((data_dir / "artifacts").iterdir())
        assert len(artifacts) == 3
        assert {path.name for path in artifacts} == {
            job["artifact_hash"] for job in payload["jobs"]
        }

    def test_同轮次二次触发幂等(self, cli, capsys, monkeypatch, policy_file, tmp_path, dsn):
        from agents.screenplay import loop

        monkeypatch.setattr(loop, "build_default_evaluators", _stub_evaluators)
        data_dir = tmp_path / "screenplay"
        first = _invoke(cli, capsys, *_produce_args("cli-2", policy_file, dsn, data_dir))
        second = _invoke(cli, capsys, *_produce_args("cli-2", policy_file, dsn, data_dir))
        assert first[0] == second[0] == 0
        assert second[1]["jobs"] == first[1]["jobs"]
        assert second[1]["spent_usd"] == pytest.approx(first[1]["spent_usd"])
        assert len(list((data_dir / "artifacts").iterdir())) == 3  # 0 重复工件

    def test_评估器装配失败退出码_1(self, cli, capsys, monkeypatch, policy_file, tmp_path, dsn):
        """装配失败（T923 前默认路径）→ 退出码 1 且不产生半轮次落盘。"""
        from agents.screenplay import loop

        def _unavailable(config, gateway):
            raise ScreenplayLoopError("七评估器装配未就绪（测试注入）")

        monkeypatch.setattr(loop, "build_default_evaluators", _unavailable)
        data_dir = tmp_path / "screenplay"
        code, payload = _invoke(cli, capsys, *_produce_args("cli-3", policy_file, dsn, data_dir))
        assert code == 1
        assert "未就绪" in payload["error"]
        assert list((data_dir / "artifacts").iterdir()) == []  # 0 工件（无半轮次落盘）

    def test_缺_topic_的输入由闭环拒绝(self, cli, capsys, monkeypatch, policy_file, tmp_path, dsn):
        """输入预检在闭环内：CLI 把闭环拒绝如实映射为退出码 1（不吞错）。"""
        from agents.screenplay import loop

        monkeypatch.setattr(loop, "build_default_evaluators", _stub_evaluators)
        args = list(_produce_args("cli-4", policy_file, dsn, tmp_path / "screenplay"))
        args[args.index("--topic") + 1] = ""
        code, payload = _invoke(cli, capsys, *args)
        assert code == 1
        assert payload["error"]
