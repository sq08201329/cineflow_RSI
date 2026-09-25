"""接入清单与机制侧总账的契约测试（功能 021 A5 / T2156 + T2161，先于实现编写）。

覆盖 `specs/021-form-plugin-validation/contracts/onboarding-ops.md` 的 C12 / C13 可执行面：

1. **清单形状逐字段**（C12 的字段权威表）：`schema` / `baseline_ref` /
   `mechanism_ledger_ref` / `form` / `config_path` / `config_fingerprint` / `changes[]` /
   `counts` / `violations[]` / `mechanism_changes_included` /
   `zero_code_onboarding` / `exit_code`；`index.jsonl` 每行**恰好七键**；
2. **不变量**：`changes[].violation == (category == "out_of_scope")`；
   `counts["既有模块被修改"] == violation ∧ status ∈ {M,D}` 的条数；
   `len(violations) == counts["越界"]`；
3. **文档面一致（集合相等，不写死条数）**：`MECHANISM_LEDGER_PATHS` 与 `quickstart.md` 的
   "机制侧总账"表**路径集合相等**；`len(MECHANISM_LEDGER) == 6`；**A5/接入侧件不在 ledger**；
4. **结构性反证**：`MECHANISM_LEDGER` 至少一项 `kind == "modified"` 且路径前缀 ∈
   {core/, agents/, ops/}
   ——以**机制落地之前**的 ref 为基线跑 onboarding ⇒ `violations` 必然非空且逐条命中 ledger 的
   `modified` 项（"机制侧不是零代码改动"的机器证明）；
5. **退出码语义**：`0` 通过 / `1` 有越界或判定失败（逐条点名）/ `2` 用法或配置错误。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ops.form_onboarding import (
    CATEGORIES,
    COUNTS_KEYS,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    INDEX_KEYS,
    MECHANISM_LEDGER,
    MECHANISM_LEDGER_PATHS,
    SCHEMA,
    build_manifest,
    mechanism_ledger_of,
    write_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = REPO_ROOT / "specs" / "021-form-plugin-validation" / "quickstart.md"
MOVIE_CONFIG = REPO_ROOT / "configs" / "movie.yaml"
# C12 的字段权威表（清单本体；`change_count` 只在 `index.jsonl` 行内）
MANIFEST_KEYS = {
    "schema",
    "baseline_ref",
    "mechanism_ledger_ref",
    "form",
    "config_path",
    "config_fingerprint",
    "changes",
    "counts",
    "violations",
    "mechanism_changes_included",
    "zero_code_onboarding",
    "exit_code",
}
# 可选扩展字段（C11 三层标注的产物层；schema 的"只增不改"允许带默认的新增字段）
OPTIONAL_MANIFEST_KEYS = {"uncalibrated", "uncalibrated_reason"}


def _git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _introducing_commit(relative: str) -> str:
    """在仓库历史里**最早**引入该路径的提交（= 机制落地的起点）。"""
    lines = [
        line
        for line in _git(["log", "--format=%H", "--diff-filter=A", "--", relative]).splitlines()
    ]
    return lines[-1] if lines else ""


def _before_mechanism_ref() -> str:
    commit = _introducing_commit("core/evaluators/plugin.py")
    if not commit:
        pytest.skip("机制侧首个文件尚未入库 ⇒ 结构性反证无法在本仓库取证")
    return f"{commit}^"


def _mechanism_ref() -> str:
    commit = _introducing_commit("core/evaluators/plugin.py")
    return commit or "HEAD"


def _documented_ledger_paths() -> set[str]:
    """读 quickstart 的"机制侧总账"表（按表头 `#/路径/kind/服务哪一项总账` 定位，不引行号）。"""
    text = QUICKSTART.read_text(encoding="utf-8")
    assert "机制侧总账" in text
    rows = re.findall(r"^\| *\d+ *\| *`([^`]+)` *\|", text, re.MULTILINE)
    assert rows, "未解析到总账表的路径列（表头布局变了 ⇒ 本断言会空跑，必须显式修）"
    return set(rows)


class Test清单形状与字段权威:
    @pytest.fixture()
    def manifest(self, tmp_path):
        return build_manifest(
            MOVIE_CONFIG,
            _before_mechanism_ref(),
            mechanism_ledger_ref=_mechanism_ref(),
        )

    def test_字段集恰为权威表加可选扩展(self, manifest):
        assert set(manifest) <= MANIFEST_KEYS | OPTIONAL_MANIFEST_KEYS
        assert MANIFEST_KEYS <= set(manifest)

    def test_两个ref非空且不相等(self, manifest):
        assert manifest["baseline_ref"].strip() and manifest["mechanism_ledger_ref"].strip()
        assert manifest["baseline_ref"] != manifest["mechanism_ledger_ref"]

    def test_形态与配置路径与指纹(self, manifest):
        assert manifest["schema"] == SCHEMA
        assert manifest["form"] == "movie"
        assert manifest["config_path"] == "configs/movie.yaml"
        assert re.fullmatch(r"[0-9a-f]{12}", manifest["config_fingerprint"])

    def test_判定一律走英文category枚举(self, manifest):
        assert manifest["changes"]
        for change in manifest["changes"]:
            assert set(change) == {"path", "status", "category", "violation", "reason"}
            assert change["category"] in CATEGORIES
            assert change["status"] in ("A", "M", "D")
            assert change["violation"] is (change["category"] == "out_of_scope")
            assert change["reason"].strip()

    def test_counts键名与不变量(self, manifest):
        assert set(manifest["counts"]) == set(COUNTS_KEYS)
        changes = manifest["changes"]
        for key, category in (
            ("配置", "config"),
            ("插件", "plugin"),
            ("测试与文档", "test_doc"),
            ("越界", "out_of_scope"),
        ):
            assert manifest["counts"][key] == sum(1 for c in changes if c["category"] == category)
        assert manifest["counts"]["既有模块被修改"] == sum(
            1 for c in changes if c["violation"] and c["status"] in ("M", "D")
        )
        assert len(manifest["violations"]) == manifest["counts"]["越界"]
        assert manifest["violations"] == [c["path"] for c in changes if c["violation"]]

    def test_冒充禁列与零代码改动字段(self, manifest):
        assert manifest["mechanism_changes_included"] is False
        assert isinstance(manifest["zero_code_onboarding"], bool)
        assert manifest["uncalibrated"] is True
        assert manifest["uncalibrated_reason"].strip()

    def test_索引行恰七键与append_only(self, tmp_path, manifest):
        out = tmp_path / "out"
        path = write_manifest(manifest, out)
        write_manifest(manifest, out)
        lines = (out / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            assert set(json.loads(line)) == set(INDEX_KEYS)
        assert json.loads(lines[0])["change_count"] == len(manifest["changes"])
        assert path.read_bytes() == (out / "onboarding-movie-0001.json").read_bytes()

    def test_越界逐条点名(self, manifest):
        if not manifest["violations"]:
            pytest.skip("本次基线之后的改动全部在界内 ⇒ 越界逐条点名无对象")
        for path in manifest["violations"]:
            entry = next(c for c in manifest["changes"] if c["path"] == path)
            assert entry["category"] == "out_of_scope"
            assert path in entry["reason"]


class Test机制侧总账:
    def test_恰好六项且与FR013序号一一对应(self):
        assert len(MECHANISM_LEDGER) == 6
        assert [entry["ordinal"] for entry in MECHANISM_LEDGER] == ["①", "②", "③", "④", "⑤", "⑥"]
        for entry in MECHANISM_LEDGER:
            assert entry["step"]
            assert entry["summary"]
            assert entry["items"]
            for item in entry["items"]:
                assert item["kind"] in ("new", "modified")
                assert item["path"].strip()

    def test_路径集合与文档表集合相等(self):
        documented = _documented_ledger_paths()
        constant = set(MECHANISM_LEDGER_PATHS)
        assert constant - documented == set(), f"常量有而文档表无：{sorted(constant - documented)}"
        assert documented - constant == set(), f"文档表有而常量无：{sorted(documented - constant)}"

    def test_条数由常量给出且去重(self):
        assert len(MECHANISM_LEDGER_PATHS) == len(set(MECHANISM_LEDGER_PATHS))
        raw = [item["path"] for entry in MECHANISM_LEDGER for item in entry["items"]]
        assert set(raw) == set(MECHANISM_LEDGER_PATHS)
        assert len(raw) > len(MECHANISM_LEDGER_PATHS), (
            "重复路径只在首次归属项计数（`ops/form_guard.py` 服务 ②③、"
            "`tests/unit/test_form_switch.py` 服务 ②⑤）"
        )

    def test_接入侧件不得进机制侧ledger(self):
        """新形态配置与其插件**不是**机制侧
        （同一路径既算机制侧、又按 C12 判 plugin 放行 = 自相矛盾）。"""
        offenders = [
            path
            for path in MECHANISM_LEDGER_PATHS
            if (
                path.startswith("configs/")
                and path not in ("configs/movie.yaml", "configs/shortdrama.yaml")
            )
            or path.startswith("core/evaluators/plugins/")
        ]
        assert offenders == [], offenders

    def test_夹具同步面与基线夹具在ledger内(self):
        assert "tests/unit/fixtures/evaluator_assembly_baseline.json" in MECHANISM_LEDGER_PATHS
        assert "tests/unit/test_form_no_new_dependency.py" in MECHANISM_LEDGER_PATHS
        assert "tests/unit/fixtures/dependency_baseline.json" in MECHANISM_LEDGER_PATHS
        assert "tests/unit/test_calibration_config.py" not in MECHANISM_LEDGER_PATHS

    def test_not_ledger_items逐条剔除(self):
        from ops.form_onboarding import NOT_LEDGER_ITEMS

        assert not (set(MECHANISM_LEDGER_PATHS) & set(NOT_LEDGER_ITEMS))
        assert "tests/unit/test_visual_composite.py" in NOT_LEDGER_ITEMS
        assert not (REPO_ROOT / "tests" / "unit" / "test_visual_composite.py").exists()

    def test_每项路径可反查(self):
        for entry in MECHANISM_LEDGER:
            for item in entry["items"]:
                path = REPO_ROOT / item["path"]
                if item["kind"] == "new":
                    assert path.is_file() or item["path"].startswith("ops/"), item["path"]
                assert isinstance(item["kind"], str)

    def test_mechanism_ledger_of返回该路径的归属项(self):
        owners = {entry["ordinal"] for entry in mechanism_ledger_of("ops/form_guard.py")}
        assert owners == {"②", "③"}, owners
        assert mechanism_ledger_of("no/such/path.py") == ()


class Test结构性反证:
    def test_至少一项modified且落在既有模块前缀(self):
        modified = [
            item["path"]
            for entry in MECHANISM_LEDGER
            for item in entry["items"]
            if item["kind"] == "modified"
        ]
        assert modified, "机制侧必然修改既有模块逻辑（本条为结构性反证的前提）"
        assert any(path.startswith(("core/", "agents/", "ops/")) for path in modified), modified

    def test_以机制之前的ref为基线必然非空且命中ledger(self):
        baseline = _before_mechanism_ref()
        manifest = build_manifest(MOVIE_CONFIG, baseline, mechanism_ledger_ref=_mechanism_ref())
        assert manifest["violations"], (
            "以机制之前的 ref 为基线却零越界（'机制侧非零代码改动'的反证失效）"
        )
        ledger_modified = {
            item["path"]
            for entry in MECHANISM_LEDGER
            for item in entry["items"]
            if item["kind"] == "modified"
        }
        hit = ledger_modified & set(manifest["violations"])
        assert hit, (
            "越界集与 ledger 的 modified 项无交集（说明基线取错或 ledger 漏登）："
            f"越界 {sorted(manifest['violations'])[:6]}…"
        )
        assert manifest["mechanism_changes_included"] is False
        assert manifest["zero_code_onboarding"] is False

    def test_机制之前的基线里演示脚本被正确判越界(self):
        """I-09 的可见后果：基线若早于 A5，`ops/demo_form_plugin.py` 作为新增 ops 文件判越界。"""
        baseline = _before_mechanism_ref()
        manifest = build_manifest(MOVIE_CONFIG, baseline, mechanism_ledger_ref=_mechanism_ref())
        entry = next(
            (c for c in manifest["changes"] if c["path"] == "ops/demo_form_plugin.py"), None
        )
        assert entry is not None, "演示脚本未出现在以机制之前为基线的改动集内"
        assert entry["category"] == "out_of_scope" and entry["violation"] is True


class Test退出码与CLI:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "ops/form_plugin.py", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_guard零违规退出零(self):
        completed = self._run("guard")
        assert completed.returncode == EXIT_OK, completed.stdout[-400:]
        assert json.loads(completed.stdout)["ok"] is True

    def test_registration退出零(self):
        completed = self._run("registration", "--config", "configs/movie.yaml")
        assert completed.returncode == EXIT_OK, completed.stdout[-400:]

    def test_sync_versions_check退出零(self):
        completed = self._run("sync-versions", "--check", "--config", "configs/movie.yaml")
        assert completed.returncode == EXIT_OK, completed.stdout[-400:]

    @pytest.mark.parametrize("command", ["guard", "registration", "onboarding", "sync-versions"])
    def test_各子命令help退出零(self, command):
        completed = self._run(command, "--help")
        assert completed.returncode == EXIT_OK, completed.stderr[-200:]

    def test_缺baseline为用法错误(self):
        completed = self._run("onboarding", "--config", "configs/movie.yaml")
        assert completed.returncode == EXIT_USAGE

    def test_基线不可解析为用法错误(self):
        completed = self._run(
            "onboarding", "--baseline", "no-such-ref-021", "--config", "configs/movie.yaml"
        )
        assert completed.returncode == EXIT_USAGE

    def test_遗漏配置为用法错误(self):
        completed = self._run("registration", "--config", "configs/ghost.yaml")
        assert completed.returncode == EXIT_USAGE

    def test_越界时退出码为一且逐条点名(self, tmp_path):
        completed = self._run(
            "onboarding",
            "--baseline",
            _before_mechanism_ref(),
            "--mechanism-ref",
            _mechanism_ref(),
            "--config",
            "configs/movie.yaml",
            "--out",
            str(tmp_path / "out"),
        )
        assert completed.returncode == EXIT_FAILED
        payload = json.loads(completed.stdout)
        assert payload["violations"], "越界却未逐条点名"
        assert payload["counts"]["越界"] == len(payload["violations"])

    def test_out不得落仓库内路径(self):
        completed = self._run(
            "onboarding",
            "--baseline",
            _before_mechanism_ref(),
            "--config",
            "configs/movie.yaml",
            "--out",
            "specs/021-form-plugin-validation",
        )
        assert completed.returncode == EXIT_USAGE
        assert "临时目录" in json.loads(completed.stdout)["error"]
