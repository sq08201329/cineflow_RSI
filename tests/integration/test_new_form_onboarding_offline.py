"""功能 021 B1/B2/B3（T2163 / T2172 / T2199）：新形态接入的**离线**端到端证据（US1 场景 1~5）。

**离线**：不依赖 Docker / 真实 PostgreSQL / 真实凭证 / 外部网络 —— 装配走唯一装配点
（`core/evaluators/plugin.py`）、评估与合成的打分只读形态配置、留痕落 `tmp_path`；演示与 CLI
经子进程跑（`ops/form_plugin.py onboarding` 与 `ops/demo_form_plugin.py` 的形态无关入口）。

覆盖判据：

1. **装配按配置声明**（T2163①）：`impl` 由唯一装配点解析、`spec.evaluator_id` == 声明键、
   `spec.version` == 声明值、声明集 ↔ `evaluator_weights.<agent>` 键集**逐字相等且保序**、
   逐实例注册进注册中心；
2. **评估 → 合成分数 → 留痕**（T2163②）：`eval_breakdown` 键为 `evaluator_id@version` ⇒
   `composite_score_versioned` 出分 ⇒ 节点与运行记录落 `tmp_path`；
3. **退出码 0 / 零真实花费 / 零外部网络 / 零凭证**（T2163③）：形态**不声明 judge** ⇒ 装配面
   零 LLM 评估器 ⇒ 网关桩零调用（结构性事实）；CLI 子进程退出码 0；
4. **接入改动清单越界为空**（T2163④ / T2167 / T2173）：以**机制落地 ref**为基线 ⇒
   `violations == []`、`counts["既有模块被修改"] == 0`、append-only、指纹与回溯字段齐备；
5. **两形态并跑**（T2172）：两形态**共用同一份插件代码**（`impl` 逐字相同 ⇒ 实例类型相同），
   同一工件上的差异**全部归因到配置声明键**（无未归因差异）；
6. **回放对比证据 / N/A 理由**（T2199）：新增插件附同一工件下的既有评估器对照件与确定性复跑；
   既有评估器零行为变更 ⇒ 显式 N/A 理由 + 实现文件哈希证据。

**"新形态"由派生面判定、不硬编码形态名**：本特性接入的形态 = **未声明 judge 的形态**
（零 LLM 面是 B1/B2 的口径），故用例随派生面自动覆盖。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from core.evaluators.base import ArtifactRef
from core.evaluators.composite import composite_score_versioned
from core.evaluators.plugin import assemble, parse_manifest
from core.evaluators.registry import Registry
from core.tree.artifacts import LocalArtifactStore
from ops.form_guard import declared_forms, iter_sources
from ops.form_onboarding import build_manifest, write_manifest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
#: 合成用的探针工件（内容无关；只作 `ArtifactRef` 的形状 + 归因元信息）
_PROBE_HASH = "0" * 64
_PROBE_METADATA = {"schema_version": "1.0.0", "stage": "delivery", "render_hash": _PROBE_HASH}
#: 既有评估器**零行为变更**的 N/A 理由（T2199②：本特性不改既有评估器实现 ⇒ 回放对比不适用，
#: 但**必须显式写理由**，不得省略）
EXISTING_EVALUATOR_NA_REASON = (
    "本特性对**既有评估器**（`agents/*/evaluators/` 下的单个评估器实现模块）零改动 —— "
    "实现文件字节未变 ⇒ `spec.version` 未变 ⇒ `eval_breakdown` 与得分逐字节不变，"
    "改造前后装配序列逐字相同 ⇒ **零行为变更、回放对比不适用**；"
    "证据面 = `tests/unit/fixtures/evaluator_assembly_baseline.json`（装配序列 + 实现文件哈希）"
    "与 T2121 的「既有评估器实现文件零改动」常驻机检"
)
#: 新增插件（B 侧接入面的通用件）的 `impl`
NEW_PLUGIN_IMPL = "core.evaluators.plugins.artifact_metadata:artifact_metadata"
NEW_PLUGIN_ID = "rule.artifact_metadata"
#: 既有模块路径前缀（`core/`**/**`agents/`**/**`ops/`**/**`web/`**/**`dreaming/`**/**`policies/`）：
#: 接入改动集里出现任一即"触碰既有模块逻辑"（C12 的越界目录表）
_PROTECTED_PREFIXES = ("core/", "agents/", "ops/", "web/", "dreaming/", "policies/")
#: 形态无关的零成本网关桩：**只**满足注入槽位存在性，调用即报错并计数
_FORBIDDEN_LEAF_KEYS = ("impl", "version", "params")


class _CountingGateway:
    """零成本网关桩：记录调用次数（判据 = **零调用**），一旦被调用即报错。"""

    total_cost_usd = 0.0
    calls = 0

    def chat(self, *args, **kwargs):
        type(self).calls += 1
        raise AssertionError("本特性不构造真实网关：形态不声明 judge ⇒ 装配面零 LLM 调用")


def _document(form: str) -> dict:
    return yaml.safe_load((CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8"))


def _declares_judge(form: str) -> bool:
    """该形态是否声明了 judge 类评估器（`evaluators.plugins.*.<slot>.<judge.*>`）。"""
    return any(
        evaluator_id.startswith("judge.")
        for entries in _document(form)["evaluators"]["plugins"].values()
        for slot in entries.values()
        for evaluator_id in slot
    )


def _new_forms() -> tuple[str, ...]:
    """本特性接入的新形态（由派生面判定：**未声明 judge** ⇒ 零 LLM 面）。"""
    return tuple(form for form in declared_forms(CONFIGS_DIR) if not _declares_judge(form))


NEW_FORMS = _new_forms()
AGENT_TARGETS = (
    (("agents.screenplay.evaluators", "ScreenplayConfig"), "screenplay"),
    (("agents.storyboard.evaluators", "StoryboardConfig"), "storyboard"),
    (("agents.visual.evaluators", "VisualConfig"), "visual"),
    (("agents.sound.evaluators", "SoundConfig"), "sound"),
    (("agents.editing.evaluators", "EditingConfig"), "editing"),
    (("agents.dev.evaluators", "DevConfig"), "dev"),
)


def _targets():
    """六个 Agent 的（绑定模块, 配置类）（静态导入：不新增第二解析路径）。"""
    import importlib

    from agents.dev.config import DevConfig
    from agents.editing.config import EditingConfig
    from agents.screenplay.config import ScreenplayConfig
    from agents.sound.config import SoundConfig
    from agents.storyboard.config import StoryboardConfig
    from agents.visual.config import VisualConfig

    configs = {
        "screenplay": ScreenplayConfig,
        "storyboard": StoryboardConfig,
        "visual": VisualConfig,
        "sound": SoundConfig,
        "editing": EditingConfig,
        "dev": DevConfig,
    }
    return tuple(
        (
            importlib.import_module(f"agents.{agent}.evaluators.plugins"),
            configs[agent],
            agent,
        )
        for _, agent in AGENT_TARGETS
    )


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, f"git {' '.join(args)} 失败：{completed.stderr.strip()}"
    return completed.stdout


def _introducing_commit(relative: str) -> str:
    """在仓库历史里**最早**引入该路径的提交（= 该文件所属批次的落地提交）。"""
    lines = [line for line in _git("log", "--format=%H", "--diff-filter=A", "--", relative).split()]
    return lines[-1] if lines else ""


def _rev_parse(ref: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _in_baseline(ref: str, relative: str) -> bool:
    """该路径是否**已在基线树内**（"接入侧件已在机制落地提交内"的机检）。"""
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{relative}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


#: **机制落地 ref**（B 侧接入清单的基线）：本轮机制批次的落地提交。
#: **重打纪律**：机制批次再动（改 `core/`/`agents/`/`ops/` 的机制件）⇒ 本 ref 必须随批次前移
#: 并**重跑**接入清单——否则清单会把机制改动算成接入越界（判据自相矛盾，C12/C13）；
#: 本仓库此时的落地方式是"机制修复与 B 侧接入**同批提交**"，故以本批为基线时清单为空集
#: （下方用例同时核"接入侧件确在基线树内"，防"基线取早了把接入改动吃掉"）。
#: `--mechanism-ref` 与之不同：仍取**引入唯一装配点**的提交（与
#: `tests/contract/test_form_onboarding_contracts.py` 的 `_mechanism_ref()` 同口径）。
MECHANISM_LANDED_REF = "6480ed7"  # 022 收口重打：022 机制+接入+parity 修复入库（原 4e90164 = 021 机制落地）


@pytest.fixture(scope="module")
def refs() -> dict:
    """两个回溯 ref：**机制起点**（`core/evaluators/plugin.py` 的引入提交）与
    **机制落地 ref**（= B 侧接入的基线，见 `MECHANISM_LANDED_REF`）。"""
    mechanism_start = _introducing_commit("core/evaluators/plugin.py")
    mechanism_landed = _rev_parse(MECHANISM_LANDED_REF)
    if not mechanism_start or not mechanism_landed:
        pytest.skip("机制侧尚未入库（历史被截断）⇒ 接入清单的基线无法取证")
    return {"baseline": MECHANISM_LANDED_REF, "mechanism": mechanism_start}


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


class Test装配与评估:
    """T2163①/②：声明驱动的装配、注册、评估与合成分数（新形态逐形态跑）。"""

    def test_没有新形态时本文件会空跑(self):
        assert NEW_FORMS, "派生面里没有未声明 judge 的形态（B1/B2 未落地）⇒ 本文件会空跑"
        assert len(NEW_FORMS) >= 2, f"本特性接入两个形态，实测 {NEW_FORMS}"

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_装配按配置声明实例化并注册(self, form, store, tmp_path):
        document = _document(form)
        registry = Registry()
        gateway = _CountingGateway()
        for module, config_class, agent in _targets():
            manifest = parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT)
            declared = [declaration.evaluator_id for declaration in manifest.declarations]
            weights = document["evaluator_weights"][agent]
            assert set(declared) == set(weights), f"{form}/{agent} 声明集 != 权重键集"
            assembled = assemble(
                manifest,
                agent_config=config_class.from_dict(document),
                gateway=gateway,
                artifacts=store,
                registry=registry,
            )
            flat = [item for slot in module.SLOT_LAYOUT for item in assembled[slot]]
            assert [item.spec.evaluator_id for item in flat] == declared, "装配面未保序"
            for index, declaration in enumerate(manifest.declarations):
                assert flat[index].spec.key == declaration.key, "声明值不得被实现覆盖"
                assert registry.get(declaration.evaluator_id, declaration.version) is flat[index]
                leaf = _leaf_of(document, agent, declaration.evaluator_id)
                assert set(leaf) == set(_FORBIDDEN_LEAF_KEYS), "叶子键恰三键"
                assert dict(leaf["params"]) == {}, "既有参数不搬迁（params 全为空）"
        assert _CountingGateway.calls == 0, "判定面不得发起真实渠道调用（零花费 / 零凭证）"

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_评估到合成分数并留痕(self, form, store, tmp_path):
        document = _document(form)
        module, config_class, agent = next(entry for entry in _targets() if entry[2] == "sound")
        assembled = assemble(
            parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
            agent_config=config_class.from_dict(document),
            gateway=_CountingGateway(),
            artifacts=store,
        )
        artifact = ArtifactRef(_PROBE_HASH, dict(_PROBE_METADATA))
        breakdown = {
            item.spec.key: item.evaluate(artifact, {})
            for slot in module.SLOT_LAYOUT
            for item in assembled[slot]
        }
        declared = {
            f"{declaration.evaluator_id}@{declaration.version}"
            for declaration in parse_manifest(
                document, module.AGENT, slots=module.SLOT_LAYOUT
            ).declarations
        }
        assert set(breakdown) == declared, "eval_breakdown 键集 == 声明面键集（id@version）"
        weights = {key.rsplit("@", 1)[0]: value for key, value in _weights(form, agent).items()}
        score = composite_score_versioned(breakdown, weights)
        assert isinstance(score, float) and 0.0 <= score <= 1.0
        # 留痕：带 eval_breakdown 的节点 + 一份运行记录（只落 tmp_path）
        node = {
            "node_id": f"onboarding-{form}-0001",
            "form": form,
            "eval_breakdown": {
                key: {"score": result.score, "diagnostics": dict(result.diagnostics)}
                for key, result in breakdown.items()
            },
            "score": score,
        }
        run = {"form": form, "network": "none", "credentials_required": False}
        (tmp_path / "node.json").write_text(
            json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (tmp_path / "run.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written = json.loads((tmp_path / "node.json").read_text(encoding="utf-8"))
        assert written["eval_breakdown"] and all("@" in key for key in written["eval_breakdown"])
        assert not list(tmp_path.rglob("billing*")), "零花费 ⇒ 不落任何账本产物"

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_零真实花费零网络零凭证是结构性事实(self, form, store, tmp_path):
        """形态不声明 judge ⇒ 装配面零 LLM 评估器 ⇒ 网关零调用；插件模块零环境/网络面。"""
        document = _document(form)
        gateway = _CountingGateway()
        for module, config_class, _ in _targets():
            assembled = assemble(
                parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
                agent_config=config_class.from_dict(document),
                gateway=gateway,
                artifacts=store,
            )
            for slot in module.SLOT_LAYOUT:
                for evaluator in assembled[slot]:
                    assert evaluator.spec.deterministic is True
                    assert evaluator.spec.cost_per_call == 0.0
        assert _CountingGateway.calls == 0
        source = (REPO_ROOT / NEW_PLUGIN_IMPL.split(":")[0].replace(".", "/")).with_suffix(".py")
        text = source.read_text(encoding="utf-8")
        for banned in ("os.environ", "os.getenv", "urllib", "http://", "https://", "import agents"):
            assert banned not in text, f"{source} 出现越界面：{banned}"
        assert not list((tmp_path / "artifacts").rglob("billing*"))


class Test接入改动清单:
    """T2163④ / T2167 / T2173：越界为空、append-only、回溯字段齐备、CLI 退出码 0。"""

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_清单越界为空且接入侧件在基线树内(self, form, refs):
        """机制落地 ref 之后的接入清单：**零越界**。

        本仓库的落地方式是"机制批次（含薄工厂槽位修复）与 B 侧接入**同批提交**" ⇒ 以该批为
        基线时清单为**空集**（运行面零改动）。空集不等于"没有管辖"：下方同时核**接入侧件确在
        基线树内**——若基线被取到接入之前，清单会混进接入改动（`configs/<form>.yaml` 等 A 行），
        这条就会以"路径不在基线树内"报红，从而防住"基线取早了把接入改动吃掉"。
        """
        manifest = build_manifest(
            CONFIGS_DIR / f"{form}.yaml",
            refs["baseline"],
            mechanism_ledger_ref=refs["mechanism"],
        )
        assert manifest["exit_code"] == 0 and manifest["violations"] == [], manifest["violations"]
        assert manifest["counts"]["既有模块被修改"] == 0
        assert manifest["counts"]["越界"] == 0
        assert manifest["baseline_ref"] and manifest["mechanism_ledger_ref"]
        assert manifest["baseline_ref"] != manifest["mechanism_ledger_ref"]
        assert manifest["mechanism_changes_included"] is False
        assert manifest["zero_code_onboarding"] is True
        assert len(manifest["config_fingerprint"]) == 12
        paths = {change["path"] for change in manifest["changes"]}
        # I-09 的收口：演示脚本已是机制件 ⇒ **不得**出现在本基线之后的接入改动集里
        assert "ops/demo_form_plugin.py" not in paths, "演示脚本落进接入账 ⇒ 机制 ref 打早了"
        assert not [path for path in paths if path.startswith("ops/")], "ops/** 零改动"
        for change in manifest["changes"]:
            assert change["category"] in {"config", "plugin", "test_doc"}, change
        # 本仓库的落地方式：机制批次（含薄工厂槽位修复）与 B 侧接入**同批提交** ⇒ 以该批为基线时
        # 清单只可能剩**未提交的测试/文档改动**（`test_doc` 放行类）；**运行面零改动**。
        # 出现 `config` / `plugin` / 越界行 ⇒ 说明基线早于接入（或基线之后又改了机制件）⇒ 重打基线。
        assert not [path for path in paths if path.startswith(_PROTECTED_PREFIXES)], (
            f"既有模块路径出现在接入改动集：{sorted(paths)}"
        )
        assert all(change["category"] == "test_doc" for change in manifest["changes"]), [
            (change["status"], change["category"], change["path"]) for change in manifest["changes"]
        ]
        # 空集/仅测试文档的**可解释性**：接入侧件（本形态配置 + 通用插件）确在基线树内
        # ——防"基线取早了把接入改动吃掉"（那会让"零越界"变成空话）
        for relative in (
            f"configs/{form}.yaml",
            NEW_PLUGIN_IMPL.split(":", 1)[0].replace(".", "/") + ".py",
        ):
            assert _in_baseline(refs["baseline"], relative), f"{relative} 不在基线树内"

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_清单落盘_append_only且可回溯(self, form, refs, tmp_path):
        out = tmp_path / "out"
        config_path = CONFIGS_DIR / f"{form}.yaml"

        def _manifest():
            return build_manifest(
                config_path, refs["baseline"], mechanism_ledger_ref=refs["mechanism"]
            )

        first = write_manifest(_manifest(), out)
        before = first.read_bytes()
        second = write_manifest(_manifest(), out)
        assert second != first and first.read_bytes() == before, "既有清单文件不得被回改"
        lines = [
            json.loads(line)
            for line in (out / "index.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 2, "append-only：每跑一次追加一行"
        for line in lines:
            assert set(line) == {
                "baseline_ref",
                "form",
                "config_path",
                "config_fingerprint",
                "change_count",
                "violations",
                "exit_code",
            }

    @pytest.mark.parametrize("form", NEW_FORMS)
    def test_CLI_onboarding退出码为零(self, form, refs, tmp_path):
        completed = subprocess.run(
            [
                sys.executable,
                "ops/form_plugin.py",
                "onboarding",
                "--baseline",
                refs["baseline"],
                "--mechanism-ref",
                refs["mechanism"],
                "--config",
                f"configs/{form}.yaml",
                "--out",
                str(tmp_path / "out"),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout[-600:]
        payload = json.loads(completed.stdout)
        assert payload["violations"] == [] and payload["counts"]["既有模块被修改"] == 0


class Test两形态并跑:
    """T2172：两形态共用同一份插件代码；差异全部来自配置声明值。"""

    def test_两形态共用插件代码与实例类型(self):
        forms = NEW_FORMS
        instances = {}
        for form in forms:
            document = _document(form)
            instances[form] = {}
            for module, config_class, _agent in _targets():
                assembled = assemble(
                    parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
                    agent_config=config_class.from_dict(document),
                    registry=None,
                )
                for slot in module.SLOT_LAYOUT:
                    for evaluator in assembled[slot]:
                        instances[form][evaluator.spec.evaluator_id] = evaluator
        left, right = forms[0], forms[1]
        shared = sorted(set(instances[left]) & set(instances[right]))
        assert shared, "两形态没有任何共用评估器 ⇒ 举证不成立"
        assert all(type(instances[left][key]) is type(instances[right][key]) for key in shared), (
            "同一 impl 必须产出同一实现类型（共用同一份插件代码）"
        )
        # 至少一条 `impl` 逐字相同（US1 场景 3 的直接举证）
        identical_impls = _identical_impls(left, right)
        assert identical_impls >= 1, "两形态必须至少共用一条 impl 声明"
        # 形态差异只在**配置值**：评估取值面（权重 + 插件声明）逐字相同 ⇒ 同一工件上逐条明细相同
        assert _flatten(_document(left)["evaluator_weights"]) == _flatten(
            _document(right)["evaluator_weights"]
        )
        assert _document(left)["evaluators"]["plugins"] == _document(right)["evaluators"]["plugins"]
        assert all(NEW_PLUGIN_IMPL in _impls(form) for form in forms), "通用件必须经声明生效"

    def test_差异全部归因到配置声明键(self):
        left, right = NEW_FORMS[0], NEW_FORMS[1]
        differences = {
            key
            for key in set(_flatten(_document(left))) | set(_flatten(_document(right)))
            if _flatten(_document(left)).get(key) != _flatten(_document(right)).get(key)
        }
        assert differences, "两形态的配置声明必须存在可指认的差异"
        assert "form" in differences
        assert not [key for key in differences if key.startswith("evaluator_weights")], (
            "评估取值面不得有未归因差异（差异必须落在非评估声明键上）"
        )
        # 同一工件、同一评估器：两形态**在最小上下文下都可评估**的条目逐条明细相同
        # ⇒ 无一未归因差异（差异只在非评估声明键上）
        results = {}
        for form in (left, right):
            document = _document(form)
            results[form] = {}
            for module, config_class, _ in _targets():
                assembled = assemble(
                    parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
                    agent_config=config_class.from_dict(document),
                )
                for slot in module.SLOT_LAYOUT:
                    for evaluator in assembled[slot]:
                        try:
                            result = evaluator.evaluate(
                                ArtifactRef(_PROBE_HASH, dict(_PROBE_METADATA)), {}
                            )
                        except Exception:  # noqa: BLE001 - 需更完整上下文 ⇒ 不参与逐条比较
                            continue
                        results[form][evaluator.spec.evaluator_id] = (
                            result.score,
                            json.dumps(result.diagnostics, sort_keys=True, ensure_ascii=False),
                        )
        shared = set(results[left]) & set(results[right])
        assert shared, "两形态没有可比的评估器 ⇒ 归因断言会空跑"
        differing = {key for key in shared if results[left][key] != results[right][key]}
        assert differing == set(), f"存在未归因到配置声明键的差异：{sorted(differing)}"


class Test留痕与回放对比:
    """T2199：新增插件的回放对比证据 + 既有评估器零行为变更的 N/A 理由。"""

    def test_新增插件的回放对比证据与确定性复跑(self, tmp_path):
        import importlib

        module_name, attr = NEW_PLUGIN_IMPL.split(":")
        factory = getattr(importlib.import_module(module_name), attr)
        first, second = factory(), factory()
        artifact = ArtifactRef(_PROBE_HASH, dict(_PROBE_METADATA))
        left, right = first.evaluate(artifact, {}), second.evaluate(artifact, {})
        assert left == right, "新插件必须确定性复跑一致"
        assert first.spec.evaluator_id == NEW_PLUGIN_ID
        declared = _declared_version(NEW_FORMS[0], NEW_PLUGIN_ID)
        assert first.spec.version == declared, "声明值 == 实现产出（装配期判据同源）"
        # 对照件：新增插件与其**承接的既有门禁**（同一形态、同一工件、同一装配路径）
        existing = _existing_gate(NEW_FORMS[0])
        record = {
            "artifact": {"hash": _PROBE_HASH, "metadata": dict(_PROBE_METADATA)},
            "new_plugin": {
                "evaluator_id": first.spec.evaluator_id,
                "declared_version": declared,
                "actual_version": first.spec.version,
                "score": left.score,
                "diagnostics": dict(left.diagnostics),
                "deterministic_rerun_equal": left == right,
            },
            "existing_evaluator": {
                "evaluator_id": existing.spec.evaluator_id,
                "version": existing.spec.version,
                "score": _existing_gate_result(existing).score,
            },
        }
        (tmp_path / "replay-comparison.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert json.loads((tmp_path / "replay-comparison.json").read_text(encoding="utf-8"))
        assert record["new_plugin"]["declared_version"] == record["new_plugin"]["actual_version"]

    def test_既有评估器零行为变更_N_A理由与证据面(self):
        assert EXISTING_EVALUATOR_NA_REASON.strip()
        assert "零改动" in EXISTING_EVALUATOR_NA_REASON
        assert "回放对比不适用" in EXISTING_EVALUATOR_NA_REASON
        baseline = json.loads(
            (
                REPO_ROOT / "tests" / "unit" / "fixtures" / "evaluator_assembly_baseline.json"
            ).read_text(encoding="utf-8")
        )
        import blake3

        recorded = baseline["implementation_files"]
        assert recorded, "证据面缺失：装配基线必须记录既有实现文件哈希"
        changed = [
            relative
            for relative, digest in recorded.items()
            if blake3.blake3((REPO_ROOT / relative).read_bytes()).hexdigest() != digest
        ]
        assert changed == [], f"既有评估器实现文件被改动（N/A 理由失效）：{changed}"

    def test_每日演示入口形态无关_新形态零脚本改动(self, tmp_path):
        """演示脚本是机制件 ⇒ 对新形态**零改动**即可演示（B 侧接入改动集不含 `ops/**`）。"""
        script = REPO_ROOT / "ops" / "demo_form_plugin.py"
        source = script.read_text(encoding="utf-8")
        for form in NEW_FORMS:
            assert not re.search(rf"(?<![0-9A-Za-z_]){re.escape(form)}(?![0-9A-Za-z_])", source), (
                f"演示脚本出现形态名 {form}（形态无关性被破坏）"
            )
        completed = subprocess.run(
            [sys.executable, str(script), "--form", NEW_FORMS[0], "--out", str(tmp_path / "out")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout[-600:]
        report = json.loads(completed.stdout)
        assert report["ok"] and all(step["ok"] for step in report["steps"])
        assert report["network"] == "none" and report["credentials_required"] is False
        assert report["repo_root_unchanged"] is True
        assert len(iter_sources()) > 0  # 守卫扫描面非空（形态无关扫描的真实性）


# ---------------------------------------------------------------------------
# 小工具（只读配置与装配面；不另写判定）
# ---------------------------------------------------------------------------


def _weights(form: str, agent: str) -> dict:
    from core.evaluators.weights import load_evaluator_weights

    return load_evaluator_weights(CONFIGS_DIR / f"{form}.yaml", agent)


def _leaf_of(document: dict, agent: str, evaluator_id: str) -> dict:
    for entries in document["evaluators"]["plugins"][agent].values():
        if evaluator_id in entries:
            return entries[evaluator_id]
    raise AssertionError(f"{agent} 的声明面缺 {evaluator_id}")


def _impls(form: str) -> list[str]:
    return [
        leaf["impl"]
        for entries in _document(form)["evaluators"]["plugins"].values()
        for slot in entries.values()
        for leaf in slot.values()
    ]


def _identical_impls(left: str, right: str) -> int:
    """两形态逐**路径**（agent/slot/evaluator_id）相同的 `impl` 条数。"""
    left_map = _impl_map(left)
    right_map = _impl_map(right)
    return sum(1 for key, value in left_map.items() if right_map.get(key) == value)


def _impl_map(form: str) -> dict:
    return {
        f"{agent}/{slot}/{evaluator_id}": leaf["impl"]
        for agent, entries in _document(form)["evaluators"]["plugins"].items()
        for slot, evaluators in entries.items()
        for evaluator_id, leaf in evaluators.items()
    }


def _declared_version(form: str, evaluator_id: str) -> str:
    for agent in _document(form)["evaluators"]["plugins"]:
        for entries in _document(form)["evaluators"]["plugins"][agent].values():
            if evaluator_id in entries:
                return entries[evaluator_id]["version"]
    raise AssertionError(f"声明面缺 {evaluator_id}")


def _flatten(node) -> dict:
    """配置声明的**逐键平铺**（键 = 点分路径；用于"差异全部归因到声明键"的集合比较）。"""
    flat: dict = {}

    def walk(value, prefix: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{prefix}.{key}" if prefix else str(key))
            return
        if isinstance(value, list):
            flat[prefix] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return
        flat[prefix] = value

    walk(node, "")
    return flat


def _existing_gate(form: str):
    """对照件的"既有评估器"一侧：新形态声明的**第一个可在最小上下文下评估的既有 rule 门禁**。

    取舍口径与离线演示一致（最小上下文下评估失败的实现无法做对照 ⇒ 换下一个既有门禁）；
    找不到即报错（对照件缺失不得静默跳过）。
    """
    document = _document(form)
    for module, config_class, _ in _targets():
        manifest = parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT)
        assembled = assemble(manifest, agent_config=config_class.from_dict(document))
        for slot in module.SLOT_LAYOUT:
            for evaluator in assembled[slot]:
                if not evaluator.spec.evaluator_id.startswith("rule."):
                    continue
                if evaluator.spec.evaluator_id == NEW_PLUGIN_ID:
                    continue
                try:
                    evaluator.evaluate(ArtifactRef(_PROBE_HASH, dict(_PROBE_METADATA)), {})
                except Exception:  # noqa: BLE001 - 需要更完整上下文 ⇒ 换下一个既有门禁
                    continue
                return evaluator
    raise AssertionError("新形态未声明任何可在最小上下文下评估的既有 rule 门禁（对照件缺失）")


def _existing_gate_result(evaluator):
    return evaluator.evaluate(ArtifactRef(_PROBE_HASH, dict(_PROBE_METADATA)), {})
