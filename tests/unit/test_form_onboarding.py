"""接入改动清单单测（功能 021 A5 / T2155，先于实现编写）：C12 的可执行面。

覆盖八条机检断言（全部在**临时 git 仓库**内跑：不在真仓库里造未跟踪文件、不改工作区）：

1. **一致率 100%**：`changed_files(ref)` 的路径集合 == 由同一 git 派生面解析出的集合（双重取证）；
2. **未跟踪新增文件不遗漏**（最常见的漏项模式）；
3. **类别判定**：含"同前缀相反结论"一对（新增 `agents/<agent>/evaluators/plugins.py` 放行 vs
   修改 `agents/<agent>/evaluators/__init__.py` 越界）；
4. **故意越界 100% 报出**：改既有模块 ⇒ `out_of_scope`、退出码 1、**逐条点名**路径与类别；
5. **append-only**：同目录连跑两次 ⇒ `index.jsonl` 行数 +2、已有清单文件**字节不变**；
6. **配置指纹**：BLAKE3 十六进制**前 12 位**；
7. **基线取错**：`baseline_ref == mechanism_ledger_ref` ⇒ 报错；
8. **只读纪律**：清单与 CLI 模块内零"改造工作区"的 git 子命令字符串（文本 + AST 双层），
   跑完后仓库根未跟踪文件集合**前后相等**。
"""

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from core.orchestration.models import fingerprint_of
from ops.form_onboarding import (
    CATEGORIES,
    COUNTS_KEYS,
    EXIT_FAILED,
    EXIT_OK,
    ChangedFile,
    OnboardingError,
    build_manifest,
    changed_files,
    classify,
    config_fingerprint,
    untracked_files,
    write_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
# "改造工作区"的 git 子命令（清单机制与 CLI 都不得出现）
FORBIDDEN_GIT_SUBCOMMANDS = ("add", "commit", "reset", "checkout")


def _git(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, f"git {' '.join(args)} 失败：{completed.stderr}"
    return completed.stdout


def _write(repo: Path, relative: str, text: str) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit(repo: Path, message: str) -> str:
    _git(["add", "-A"], repo)
    _git(
        ["-c", "user.email=demo@example.com", "-c", "user.name=demo", "commit", "-m", message], repo
    )
    return _git(["rev-parse", "HEAD"], repo).strip()


@pytest.fixture()
def repo(tmp_path):
    """临时 git 仓库：两个提交（机制基点 + 接入基点）+ 接入期改动。"""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _write(root, "README.md", "# demo\n")
    mechanism_ref = _commit(root, "mech")
    _write(
        root,
        "configs/ad.yaml",
        "form: ad\nform_aliases: []\nbudget:\n  ledger:\n    root: billing\n",
    )
    _write(root, "core/evaluators/composite.py", "SCORE = 1.0\n")
    _write(root, "agents/ad/evaluators/__init__.py", "BUILDERS = ()\n")
    _write(root, "tests/unit/test_x.py", "def test_x():\n    assert True\n")
    baseline_ref = _commit(root, "base")
    # 接入期改动（全部落在放行面内）：新增配置 + 两类插件 + 测试与文档
    _write(
        root,
        "configs/nf.yaml",
        "form: nf\nform_aliases: []\nbudget:\n  ledger:\n    root: billing\n",
    )
    _write(root, "core/evaluators/plugins/__init__.py", "")
    _write(root, "core/evaluators/plugins/util.py", "def ping():\n    return 'pong'\n")
    _write(root, "agents/ad/evaluators/plugins.py", "def factory():\n    return None\n")
    _write(root, "tests/unit/test_new.py", "def test_new():\n    assert True\n")
    _write(
        root,
        "tests/unit/test_x.py",
        "def test_x():\n    assert True\n\n\ndef test_y():\n    return None\n",
    )
    return {
        "root": root,
        "mechanism_ref": mechanism_ref,
        "baseline_ref": baseline_ref,
        "config_path": root / "configs" / "nf.yaml",
    }


@pytest.fixture(autouse=True)
def _repo_root_untouched():
    """⑧ 只读纪律：跑完**真仓库根**的未跟踪文件集合前后相等。"""
    before = untracked_files()
    yield
    assert untracked_files() == before, "仓库根未跟踪文件集合发生变化（工具不得触碰工作区）"


class Test改动集合由git派生:
    def test_一致率百分之百(self, repo):
        derived = changed_files(repo["baseline_ref"], repo_root=repo["root"])
        # 同一派生面的**独立**解析（双向取证：不靠被测算法的返回值自我印证）
        expected: dict[str, str] = {}
        for line in _git(
            ["diff", "--name-status", repo["baseline_ref"]], repo["root"]
        ).splitlines():
            parts = line.split("\t")
            expected[parts[-1]] = parts[0][:1]
        for line in _git(["ls-files", "--others", "--exclude-standard"], repo["root"]).splitlines():
            expected.setdefault(line.strip(), "A")
        assert {item.path: item.status for item in derived} == expected
        assert expected, "派生集合为空 ⇒ 本断言会空跑"

    def test_未跟踪新增文件不遗漏(self, repo):
        probe = repo["root"] / "configs" / "untracked_form.yaml"
        probe.write_text("form: untracked_form\nform_aliases: []\n", encoding="utf-8")
        paths = {item.path for item in changed_files(repo["baseline_ref"], repo_root=repo["root"])}
        assert "configs/untracked_form.yaml" in paths

    def test_未跟踪新增文件按A记且类别为配置(self, repo):
        changes = {
            item.path: item for item in changed_files(repo["baseline_ref"], repo_root=repo["root"])
        }
        assert changes["configs/nf.yaml"].status == "A"
        assert classify("configs/nf.yaml", "A") == "config"

    def test_基线不可解析即报错(self, repo):
        with pytest.raises(OnboardingError):
            changed_files("no-such-ref-021", repo_root=repo["root"])


class Test类别判定:
    @pytest.mark.parametrize(
        ("path", "status", "expected"),
        (
            ("configs/nf.yaml", "A", "config"),
            ("configs/existing.yaml", "M", "out_of_scope"),
            ("core/evaluators/plugins/util.py", "A", "plugin"),
            ("agents/ad/evaluators/plugins.py", "A", "plugin"),
            ("agents/ad/evaluators/__init__.py", "M", "out_of_scope"),
            ("agents/ad/evaluators/plugins.py", "M", "out_of_scope"),
            ("tests/unit/test_new.py", "A", "test_doc"),
            ("tests/unit/test_x.py", "M", "test_doc"),
            ("docs/x.md", "M", "test_doc"),
            ("specs/021-x/spec.md", "A", "test_doc"),
            ("ops/demo_new.py", "A", "out_of_scope"),
            ("ops/form_plugin.py", "M", "out_of_scope"),
            ("core/evaluators/composite.py", "M", "out_of_scope"),
            ("web/server.py", "D", "out_of_scope"),
            ("README.md", "A", "out_of_scope"),
        ),
    )
    def test_类别取自英文枚举(self, path, status, expected):
        assert classify(path, status) == expected
        assert classify(path, status) in CATEGORIES

    def test_同前缀相反结论一对(self):
        """`agents/<agent>/evaluators/` 前缀下：新增插件**放行**、修改既有文件**越界**。"""
        assert classify("agents/ad/evaluators/plugins.py", "A") == "plugin"
        assert classify("agents/ad/evaluators/__init__.py", "M") == "out_of_scope"

    def test_ops不设放行面(self):
        for path, status in (("ops/a.py", "A"), ("ops/a.py", "M"), ("ops/a.py", "D")):
            assert classify(path, status) == "out_of_scope"

    def test_非法状态即报错(self):
        with pytest.raises(OnboardingError):
            classify("configs/nf.yaml", "X")


class Test清单形状与越界:
    def test_放行面内的接入清单零越界(self, repo):
        manifest = build_manifest(
            repo["config_path"],
            repo["baseline_ref"],
            mechanism_ledger_ref=repo["mechanism_ref"],
            repo_root=repo["root"],
        )
        assert manifest["violations"] == []
        assert manifest["exit_code"] == EXIT_OK
        assert manifest["counts"]["越界"] == 0
        assert manifest["counts"]["既有模块被修改"] == 0
        assert manifest["counts"]["配置"] == 1
        assert manifest["counts"]["插件"] == 3
        assert manifest["counts"]["测试与文档"] == 2
        assert set(manifest["counts"]) == set(COUNTS_KEYS)
        assert manifest["form"] == "nf"
        assert manifest["config_path"] == "configs/nf.yaml"

    def test_故意越界逐条点名且退出码为1(self, repo):
        _write(repo["root"], "core/evaluators/composite.py", "SCORE = 1.0\n# 越界探针\n")
        manifest = build_manifest(
            repo["config_path"],
            repo["baseline_ref"],
            mechanism_ledger_ref=repo["mechanism_ref"],
            repo_root=repo["root"],
        )
        assert manifest["violations"] == ["core/evaluators/composite.py"]
        assert manifest["exit_code"] == EXIT_FAILED
        assert manifest["counts"]["越界"] == 1
        assert manifest["counts"]["既有模块被修改"] == 1
        entry = next(c for c in manifest["changes"] if c["path"] == "core/evaluators/composite.py")
        assert entry["category"] == "out_of_scope" and entry["violation"] is True
        assert "core/evaluators/composite.py" in entry["reason"]

    def test_字段不变量逐条(self, repo):
        manifest = build_manifest(
            repo["config_path"],
            repo["baseline_ref"],
            mechanism_ledger_ref=repo["mechanism_ref"],
            repo_root=repo["root"],
        )
        assert manifest["schema"] == 1
        assert len(manifest["violations"]) == manifest["counts"]["越界"]
        assert manifest["mechanism_changes_included"] is False
        assert manifest["zero_code_onboarding"] is True
        assert manifest["uncalibrated"] is True
        assert manifest["uncalibrated_reason"].strip()
        for change in manifest["changes"]:
            assert change["category"] in CATEGORIES
            assert change["violation"] is (change["category"] == "out_of_scope")
            assert change["reason"].strip()


class Testappend_only与指纹:
    def test_连跑两次索引加二且旧件字节不变(self, repo, tmp_path):
        out = tmp_path / "out"
        first = build_manifest(
            repo["config_path"],
            repo["baseline_ref"],
            mechanism_ledger_ref=repo["mechanism_ref"],
            repo_root=repo["root"],
        )
        path_one = write_manifest(first, out)
        bytes_one = path_one.read_bytes()
        path_two = write_manifest(first, out)
        assert path_one != path_two
        assert path_one.read_bytes() == bytes_one, "已有清单文件被回改（append-only 失守）"
        lines = (out / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert set(json.loads(line)) == {
                "baseline_ref",
                "form",
                "config_path",
                "config_fingerprint",
                "change_count",
                "violations",
                "exit_code",
            }
        assert json.loads(lines[0])["change_count"] == len(first["changes"])

    def test_配置指纹为blake3前12位(self, repo):
        actual = config_fingerprint(repo["config_path"])
        expected = fingerprint_of(repo["config_path"].read_bytes())[:12]
        assert actual == expected
        assert re.fullmatch(r"[0-9a-f]{12}", actual), actual

    def test_配置不可读即报错(self, repo, tmp_path):
        with pytest.raises(OnboardingError):
            config_fingerprint(tmp_path / "ghost.yaml")

    def test_形态id与文件名不一致即报错(self, repo):
        broken = repo["root"] / "configs" / "mismatch.yaml"
        broken.write_text("form: other\nform_aliases: []\n", encoding="utf-8")
        with pytest.raises(OnboardingError):
            build_manifest(
                broken,
                repo["baseline_ref"],
                mechanism_ledger_ref=repo["mechanism_ref"],
                repo_root=repo["root"],
            )


class Test基线取错即报错:
    def test_两个ref相等即报错(self, repo):
        with pytest.raises(OnboardingError, match="基线取错"):
            build_manifest(
                repo["config_path"],
                repo["baseline_ref"],
                mechanism_ledger_ref=repo["baseline_ref"],
                repo_root=repo["root"],
            )

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_缺任一ref即报错(self, repo, empty):
        with pytest.raises(OnboardingError):
            build_manifest(
                repo["config_path"],
                empty,
                mechanism_ledger_ref=repo["mechanism_ref"],
                repo_root=repo["root"],
            )
        with pytest.raises(OnboardingError):
            build_manifest(
                repo["config_path"],
                repo["baseline_ref"],
                mechanism_ledger_ref=empty,
                repo_root=repo["root"],
            )


class Test只读纪律:
    @pytest.mark.parametrize("relative", ["ops/form_onboarding.py", "ops/form_plugin.py"])
    def test_零改造工作区的git子命令(self, relative):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(text)
        # AST 层：字符串常量里不得出现这些子命令名
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not (literals & set(FORBIDDEN_GIT_SUBCOMMANDS)), (
            f"{relative} 的字符串常量出现 {sorted(literals & set(FORBIDDEN_GIT_SUBCOMMANDS))}"
        )
        for token in FORBIDDEN_GIT_SUBCOMMANDS:
            # 文本层：不得出现 `git <subcommand>` 形态（`^{commit}` 一类 rev 语法不命中）
            assert not re.search(rf"\bgit\s+{token}\b", text), f"{relative} 出现 `git {token}`"

    def test_清单模块只跑只读git子命令(self):
        from ops import form_onboarding

        source = (REPO_ROOT / "ops" / "form_onboarding.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        subcommands = {
            node.elts[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.List)
            and node.elts
            and isinstance(node.elts[0], ast.Constant)
            and isinstance(node.elts[0].value, str)
        }
        assert subcommands <= {"git", "rev-parse", "diff", "ls-files"}, subcommands
        assert form_onboarding.EXIT_OK != form_onboarding.EXIT_FAILED


def test_未跟踪文件辅助与真仓库只读():
    """真仓库的未跟踪集合可读，且本模块不产生任何未跟踪文件。"""
    before = untracked_files()
    assert untracked_files() == before
    assert isinstance(ChangedFile(path="x", status="A"), ChangedFile)


def test_清单可json序列化(repo):
    manifest = build_manifest(
        repo["config_path"],
        repo["baseline_ref"],
        mechanism_ledger_ref=repo["mechanism_ref"],
        repo_root=repo["root"],
    )
    text = json.dumps(manifest, ensure_ascii=False)
    assert yaml.safe_load(text)["form"] == "nf"
