#!/usr/bin/env python
"""离线端到端演示：形态插件的声明 / 装配 / 登记完备（功能 021 / 契约 C14，T2166）。

**唯一入口**（不新造第二个端到端演示）、**形态无关**——演示的形态集合一律遍历
`declared_forms()`（新增一份 `configs/<id>.yaml` 后**对该形态零改动**即可演示；本模块内
**零形态字面量、零形态判断分支**，`--form` 只用来挑选**已声明**的演示对象，未声明 ⇒ 退出码 `2`）。

九步（`steps` 键逐条 `ok`；镜像既有 `ops/demo_*.py` 的演示纪律）：

  ① **配置加载与预检**：临时目录派生副本跑 `config_completeness` ⇒ `form_clauses` 与
     `evaluators` 两项齐备，且 020 口径逐项机检（`FORM_CLAUSE_CHECKS`）全部通过
  ② **缺项即拒绝**：逐项删一个 020 口径键、再删 `evaluators` 段 ⇒ 预检**拒绝启动**并点名段与键
  ③ **插件装配**：`parse_manifest` 声明解析 + 集合与权重键集一一对应 + 保序 + 经**唯一装配点**
     `assemble` 实例化并按 `evaluator_id@version` 注册；同 id 同 version 重复注册被拒；
     非确定性插件被拒；必需元数据非法被拒；插件目录里存在但**未声明** ⇒ 装配期不可用
  ④ **评估与合成分数**：真实评估一组合成功件 ⇒ `eval_breakdown` 的键为 `evaluator_id@version`
     ⇒ `composite_score_versioned` 出分
  ⑤ **留痕**：带 `eval_breakdown` 的节点与一份运行记录落**临时目录**（仓库根零残留）
  ⑥ **两形态共用同一份插件代码**：每一对形态的声明面在**同路径**上逐字相同的叶子数 ≥ 1
     （形态差异**只在配置值**）
  ⑦ **静态守卫**：两层扫描（字面量层 + 判断分支层，覆盖 `core/` + `agents/` **含 `agents/pilot`**）
     零违规；并用**由派生值构造**的合成反例举证"注入即红"（本模块自身也过同一层判定）
  ⑧ **登记点完备**：五处登记点逐一 + 登记完备三条（两两唯一 / 双向集合相等 / 下界 ≥ 2）+ 无第六处
  ⑨ **诚实分层与零成本**：三层"未标定"标注机检（形态层取值域 + 五段段级 `note` + 产物
     `uncalibrated` / `uncalibrated_reason`）——用**未标定派生副本**正面取证、删一个 `note`
     反面取证；
     真实花费 0、外部网络 0、凭证读取 0

**结构性零成本（不是承诺）**：注入槽位只给**零成本桩**与**临时目录工件库**——本模块不读取任何
环境变量、不引入任何 HTTP 客户端、**不构造**真实网关；真实渠道调用 = 0、凭证读取 = 0
（判定面不发任何调用）。

**`--out` 与工作目录一律落 `tmp_path` 类临时目录**（`tempfile.mkdtemp()`；`--keep-work-dir`
仅调试保留），**禁止**落仓库根或任何仓库内路径 ⇒ "跑完仓库根零新增文件"是可断言事实
（判据 = `git ls-files --others --exclude-standard` 的输出集合前后逐条相等）。

退出码：`0` = 九步全 ok 且仓库根未变；`1` = 有任一步未通过或仓库根发生变化；
`2` = 用法错误（`--form` 未声明 / 参数非法）。
"""

import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.evaluators.base import ArtifactRef, Evaluator, EvaluatorKind, EvaluatorSpec  # noqa: E402
from core.evaluators.errors import (  # noqa: E402
    PluginAssemblyError,
    RegistrationError,
    ValidationError,
)
from core.evaluators.plugin import assemble, parse_manifest  # noqa: E402
from core.evaluators.registry import Registry  # noqa: E402
from core.tree.artifacts import LocalArtifactStore  # noqa: E402
from ops.form_guard import (  # noqa: E402
    branch_violations,
    declared_forms,
    form_branch_patterns,
    form_literals,
    iter_sources,
    literal_violations,
    violations_in,
)
from ops.form_onboarding import (  # noqa: E402
    EXIT_FAILED,
    EXIT_OK,
    EXIT_USAGE,
    UNCALIBRATED_REASON,
    changed_files,
    registration_completeness,
    sixth_site_scan,
    untracked_files,
)

CONFIGS_DIR = REPO_ROOT / "configs"
# 020 口径的注入面（逐项删一个键 ⇒ 预检必须拒绝启动并点名；路径由 C11 的逐项表给出）
CLAUSE_INJECTIONS: tuple[str, ...] = (
    "calibration.period_days",
    "calibration.window_semantics",
    "calibration.window_semantics_change_date",
    "promo.attribution_date_required_since",
    "calibration.transfer.basis",
    "budget.runs.min_window_days",
)
# 合成评估器用的工件哈希（内容无关；只作 `ArtifactRef` 的形状）
_PROBE_HASH = "0" * 64
STEP_NAMES: tuple[str, ...] = (
    "配置加载与预检",
    "缺项即拒绝",
    "插件装配",
    "评估与合成分数",
    "留痕",
    "两形态共用同一份插件代码",
    "静态守卫",
    "登记点完备",
    "诚实分层与零成本",
)


class _ZeroCostGateway:
    """**零成本网关桩**（不是真实网关）：满足注入槽位的存在性，调用即报错。

    判定面只构造实例、不发起调用 ⇒ 外部网络 0、凭证读取 0（本模块也不读任何环境变量）。
    """

    total_cost_usd = 0.0

    def chat(self, *args, **kwargs):  # pragma: no cover - 判定面永不调用
        raise RuntimeError("零成本网关桩不得被调用（演示不需要真实渠道调用）")


class _NonDeterministicProbe(Evaluator):
    """非确定性合成评估器（**不得**进入回放打分路径；人类锚点之外一律拒绝注册）。"""

    def __init__(self) -> None:
        self.spec = EvaluatorSpec(
            evaluator_id="rule.demo_probe",
            version="0.0.0+probe0000000",
            kind=EvaluatorKind.RULE,
            deterministic=False,
        )

    def evaluate(self, artifact: ArtifactRef, context: dict):
        raise AssertionError("非确定性探针不得被调用")


def _assert(condition, message: str) -> None:
    if not condition:
        raise AssertionError(f"演示断言失败：{message}")


def _document(form: str) -> dict:
    return yaml.safe_load((CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8"))


def _write(directory: Path, form: str, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{form}.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _derived_copy(form: str, work_dir: Path) -> Path:
    """形态配置的**临时目录派生副本**（只改账本根；注释与其余字节保留）。"""
    source = (CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8")
    _assert("root: billing" in source, "派生点缺失：budget.ledger.root")
    target = work_dir / f"{form}.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        source.replace("root: billing", f"root: {work_dir / 'billing'}"), encoding="utf-8"
    )
    return target


def _drop(document: dict, path: str) -> None:
    keys = path.split(".")
    cursor = document
    for key in keys[:-1]:
        cursor = cursor[key]
    del cursor[keys[-1]]


def _targets():
    """六个 Agent 的绑定模块与配置类（静态导入：不新增第二解析路径）。"""
    from agents.dev.config import DevConfig
    from agents.dev.evaluators import plugins as dev_plugins
    from agents.editing.config import EditingConfig
    from agents.editing.evaluators import plugins as editing_plugins
    from agents.screenplay.config import ScreenplayConfig
    from agents.screenplay.evaluators import plugins as screenplay_plugins
    from agents.sound.config import SoundConfig
    from agents.sound.evaluators import plugins as sound_plugins
    from agents.storyboard.config import StoryboardConfig
    from agents.storyboard.evaluators import plugins as storyboard_plugins
    from agents.visual.config import VisualConfig
    from agents.visual.evaluators import plugins as visual_plugins

    return (
        (screenplay_plugins, ScreenplayConfig),
        (storyboard_plugins, StoryboardConfig),
        (visual_plugins, VisualConfig),
        (sound_plugins, SoundConfig),
        (editing_plugins, EditingConfig),
        (dev_plugins, DevConfig),
    )


def _identical_leaves(left, right) -> int:
    """同路径上逐字相同的叶子数（形态差异必须只落在配置值上）。"""
    if isinstance(left, dict) and isinstance(right, dict):
        return sum(_identical_leaves(left[key], right[key]) for key in set(left) & set(right))
    return 1 if left == right else 0


def _step_config_precheck(form: str, work_dir: Path) -> tuple[bool, str]:
    from agents.pilot.pilot import FORM_CLAUSE_CHECKS, config_completeness, form_clause_completeness

    derived = _derived_copy(form, work_dir / "precheck")
    checked = config_completeness(derived)
    clauses = form_clause_completeness(derived)
    ok = (
        "form_clauses" in checked
        and "evaluators" in checked
        and tuple(clauses) == FORM_CLAUSE_CHECKS
    )
    return (
        ok,
        f"预检 {len(checked)} 项（含 form_clauses / evaluators）；020 口径 {len(clauses)} 项全通过",
    )


def _step_reject_missing(form: str, work_dir: Path) -> tuple[bool, str]:
    from agents.pilot.pilot import PrecheckError, config_completeness, form_clause_completeness

    document = _document(form)
    named: list[str] = []
    for path in CLAUSE_INJECTIONS:
        broken = copy.deepcopy(document)
        _drop(broken, path)
        try:
            form_clause_completeness(_write(work_dir / "reject", form, broken))
        except Exception as exc:  # noqa: BLE001 - 拒绝启动即可（加载器与预检各有自己的错误类型）
            _assert(path.split(".")[-1] in str(exc), f"缺项未点名键路径：{path}")
            named.append(path)
        else:
            return False, f"删掉 {path} 却照常通过（缺项即拒绝的口径被反转）"
    broken = copy.deepcopy(document)
    del broken["evaluators"]
    try:
        config_completeness(_write(work_dir / "reject", form, broken))
    except PrecheckError as exc:
        _assert("evaluators" in str(exc), "缺 evaluators 段未点名段名")
        named.append("evaluators")
    else:
        return False, "删掉 evaluators 段却照常通过（漏声明插件清单静默逃逸）"
    return True, f"{len(named)} 项注入全部被拒并逐条点名：{named}"


def _step_assembly(form: str, work_dir: Path) -> tuple[bool, str]:
    document = _document(form)
    stub = _ZeroCostGateway()
    store = LocalArtifactStore(work_dir / "artifacts")
    registry = Registry()
    count = 0
    for module, config_class in _targets():
        config = config_class.from_dict(document)
        manifest = parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT)
        declared = [declaration.evaluator_id for declaration in manifest.declarations]
        weights = set(document["evaluator_weights"][module.AGENT])
        _assert(set(declared) == weights, f"{module.AGENT} 声明集 != 权重键集")
        assembled = assemble(
            manifest, agent_config=config, gateway=stub, artifacts=store, registry=registry
        )
        order = [ev.spec.evaluator_id for slot in module.SLOT_LAYOUT for ev in assembled[slot]]
        _assert(order == declared, f"{module.AGENT} 装配面未保序")
        for index, declaration in enumerate(manifest.declarations):
            flat = [ev for slot in module.SLOT_LAYOUT for ev in assembled[slot]]
            _assert(flat[index].spec.key == declaration.key, f"{module.AGENT} 版本/键不一致")
        count += len(declared)
    # 重复注册被拒（同一注册中心内同 id 同 version）
    module0, class0 = _targets()[0]
    manifest0 = parse_manifest(document, module0.AGENT, slots=module0.SLOT_LAYOUT)
    try:
        assemble(
            manifest0,
            agent_config=class0.from_dict(document),
            gateway=stub,
            artifacts=store,
            registry=registry,
        )
    except RegistrationError:
        pass
    else:
        return False, "同 id 同 version 重复注册未被拒"
    # 非确定性插件注册被拒 / 必需元数据非法被拒
    try:
        Registry().register(_NonDeterministicProbe())
    except RegistrationError:
        pass
    else:
        return False, "非确定性插件注册未被拒"
    try:
        EvaluatorSpec(evaluator_id="", version="0.0.0", kind=EvaluatorKind.RULE, deterministic=True)
    except ValidationError:
        pass
    else:
        return False, "必需元数据非法未被拒"
    # 目录里存在但**未声明** ⇒ 装配期不可用
    slot0 = next(iter(document["evaluators"]["plugins"][module0.AGENT]))
    dropped_id = next(iter(document["evaluators"]["plugins"][module0.AGENT][slot0]))
    broken = copy.deepcopy(document)
    del broken["evaluators"]["plugins"][module0.AGENT][slot0][dropped_id]
    try:
        assemble(
            parse_manifest(broken, module0.AGENT, slots=module0.SLOT_LAYOUT),
            agent_config=class0.from_dict(broken),
            gateway=stub,
            artifacts=store,
        )
    except PluginAssemblyError:
        pass
    else:
        return False, "目录里存在但未声明的插件仍可装配"
    return True, f"{count} 条声明经唯一装配点实例化并注册；四条拒绝面全部成立"


def _breakdown_of(form: str, work_dir: Path) -> tuple[dict, dict]:
    """对一组合成功件**真实评估**一次 ⇒ `{evaluator_id@version: EvalResult}` + 该 Agent 的权重。

    最小上下文下评估失败的 Agent 直接换下一个（不构造任何真实渠道调用；键形状是判据）。
    """
    document = _document(form)
    stub = _ZeroCostGateway()
    store = LocalArtifactStore(work_dir / "artifacts")
    for module, config_class in _targets():
        assembled = assemble(
            parse_manifest(document, module.AGENT, slots=module.SLOT_LAYOUT),
            agent_config=config_class.from_dict(document),
            gateway=stub,
            artifacts=store,
        )
        breakdown: dict = {}
        try:
            for slot in module.SLOT_LAYOUT:
                for evaluator in assembled[slot]:
                    breakdown[evaluator.spec.key] = evaluator.evaluate(ArtifactRef(_PROBE_HASH), {})
        except Exception:  # noqa: BLE001 - 该 Agent 的评估器需要更完整的上下文 ⇒ 换下一个
            continue
        if breakdown:
            from core.evaluators.weights import load_evaluator_weights

            # 权重走**既有加载器**（`gate` 哨兵值由它归一为注入 `composite_score` 的口径）
            weights = load_evaluator_weights(CONFIGS_DIR / f"{form}.yaml", module.AGENT)
            return breakdown, dict(weights)
    return {}, {}


def _step_composite(form: str, work_dir: Path) -> tuple[bool, str]:
    from core.evaluators.composite import composite_score_versioned

    breakdown, weights = _breakdown_of(form, work_dir)
    _assert(breakdown, "没有任何 Agent 的评估器可在最小上下文下评估")
    _assert(all("@" in key for key in breakdown), "eval_breakdown 的键必须为 evaluator_id@version")
    score = composite_score_versioned(breakdown, weights)
    return True, f"eval_breakdown {len(breakdown)} 键（evaluator_id@version）⇒ 合成分 {score:.4f}"


def _step_evidence(form: str, work_dir: Path) -> tuple[bool, str]:
    breakdown, _ = _breakdown_of(form, work_dir)
    evidence = work_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    node = {
        "node_id": f"demo-{form}-0001",
        "form": form,
        "stage": "form-plugin",
        "eval_breakdown": {
            key: {"score": result.score, "diagnostics": dict(result.diagnostics)}
            for key, result in breakdown.items()
        },
        "uncalibrated": True,
        "uncalibrated_reason": UNCALIBRATED_REASON,
    }
    run = {
        "form": form,
        "status": "ok",
        "stage": "form-plugin-validation",
        "network": "none",
        "credentials_required": False,
        "uncalibrated": True,
    }
    (evidence / "node.json").write_text(
        json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (evidence / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _assert((evidence / "node.json").is_file() and (evidence / "run.json").is_file(), "留痕未落盘")
    return True, f"带 eval_breakdown 的节点与运行记录落临时目录（{evidence}）"


def _step_shared_plugin_code(forms: tuple[str, ...]) -> tuple[bool, str]:
    plugins = {name: _document(name)["evaluators"]["plugins"] for name in forms}
    shared: dict[str, int] = {}
    for left in forms:
        for right in forms:
            if left < right:
                shared[f"{left}×{right}"] = _identical_leaves(plugins[left], plugins[right])
    _assert(shared, "派生面只有一个形态 ⇒ 逐对举证空跑")
    bad = {pair: n for pair, n in shared.items() if n < 1}
    _assert(not bad, f"这些形态对没有任何一条声明逐字相同：{bad}")
    return True, f"逐对逐字相同的叶子数：{shared}"


def _step_guard() -> tuple[bool, str]:
    forms = declared_forms(CONFIGS_DIR)
    literal = literal_violations(CONFIGS_DIR)
    branch = branch_violations(CONFIGS_DIR)
    _assert(not literal, f"字面量层违规：{[hit.path for hit in literal]}")
    _assert(not branch, f"判断分支层违规：{[hit.path for hit in branch]}")
    # 注入即红：合成反例由**派生值**构造（本模块与守卫都零人工形态常量）
    injected = f'FORM_PROBE = "{forms[0]}"\n'
    _assert(violations_in(injected, forms[0]), "字面量层注入未变红（断言空跑）")
    source = Path(__file__).read_text(encoding="utf-8")
    names = [name for name in form_literals(CONFIGS_DIR) if violations_in(source, name)]
    patterns = [
        pattern
        for pattern in form_branch_patterns()
        if violations_in(source, pattern, layer="branch")
    ]
    _assert(not names and not patterns, f"演示脚本自身含形态字面量/分支：{names}{patterns}")
    return True, f"两层扫描零违规（{len(iter_sources())} 个源文件）；注入即红；本模块形态无关"


def _step_registration(baseline: str | None) -> tuple[bool, str]:
    completeness = registration_completeness()
    sixth = sixth_site_scan()
    _assert(completeness.unique, "① 两两唯一不成立")
    _assert(completeness.at_least_two, "③ 下界（≥2）不成立")
    _assert(completeness.registered_matches, f"② 双向集合相等不成立：{completeness.violations()}")
    _assert(not sixth, f"出现第六处形态枚举点：{[site.describe() for site in sixth]}")
    detail = f"五处登记点逐一已登记 + 三条成立；第六处 {len(sixth)} 处"
    if baseline:
        changes = {change.path for change in changed_files(baseline)}
        script = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
        _assert(
            script not in changes, f"演示脚本落进以 {baseline} 为基线的接入改动集（基线取早了）"
        )
        detail += f"；以 {baseline} 为基线时演示脚本不在改动集内"
    return True, detail


def _step_honest_layers(form: str, work_dir: Path) -> tuple[bool, str]:
    from agents.pilot.pilot import (
        REHEARSAL_STATUSES,
        UNCALIBRATED_NOTE_SEGMENTS,
        PrecheckError,
        form_clause_completeness,
    )

    document = _document(form)
    status = document["pilot"]["rehearsal"]["status"]
    _assert(status in REHEARSAL_STATUSES, f"rehearsal.status 取值越界：{status!r}")
    marked = copy.deepcopy(document)
    marked["pilot"]["rehearsal"] = {"status": "unstandardized", "work_kind": "rehearsal"}
    for segment in UNCALIBRATED_NOTE_SEGMENTS:
        marked[segment]["note"] = f"未标定（业务数字待运营给定）：{segment} 段"
    marked["calibration"]["cadence_note"] = "所取档位与业务侧真实节律为近似关系；未标定"
    _assert(
        form_clause_completeness(_write(work_dir / "layers", form, marked)), "三层标注齐备却未通过"
    )
    broken = copy.deepcopy(marked)
    del broken[UNCALIBRATED_NOTE_SEGMENTS[0]]["note"]
    try:
        form_clause_completeness(_write(work_dir / "layers", form, broken))
    except PrecheckError as exc:
        _assert(UNCALIBRATED_NOTE_SEGMENTS[0] in str(exc), "段层缺项未点名段名")
    else:
        return False, "删掉段级 note 却照常通过（三层标注无牙齿）"
    _assert(UNCALIBRATED_REASON.strip(), "uncalibrated_reason 不得为空")
    _assert(
        "受众 / 指标口径 / 素材规格 / 预算档属业务侧输入，未给定" in UNCALIBRATED_REASON,
        "理由未点名业务侧输入",
    )
    return True, "形态层 + 段层 + 产物层三层标注齐备；缺一段即红；未标定理由非空"


# 形态无关的全局步骤（⑥⑦⑧）与形态无关 ⇒ 逐形态循环里只算一次（缓存，不重复扫描）
_GLOBAL_STEPS: dict[str, tuple[bool, str]] = {}


def _cached(key: str, runner) -> tuple[bool, str]:
    if key not in _GLOBAL_STEPS:
        try:
            _GLOBAL_STEPS[key] = runner()
        except AssertionError as exc:
            _GLOBAL_STEPS[key] = (False, str(exc))
    return _GLOBAL_STEPS[key]


def _run_form(form: str, work_root: Path, baseline: str | None) -> dict[str, tuple[bool, str]]:
    work_dir = work_root / form
    work_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, tuple[bool, str]] = {}

    def record(name: str, runner) -> None:
        try:
            results[name] = runner()
        except AssertionError as exc:
            results[name] = (False, str(exc))

    record("①", lambda: _step_config_precheck(form, work_dir))
    record("②", lambda: _step_reject_missing(form, work_dir))
    record("③", lambda: _step_assembly(form, work_dir))
    record("④", lambda: _step_composite(form, work_dir))
    record("⑤", lambda: _step_evidence(form, work_dir))
    results["⑥"] = _cached("⑥", lambda: _step_shared_plugin_code(declared_forms(CONFIGS_DIR)))
    results["⑦"] = _cached("⑦", _step_guard)
    results["⑧"] = _cached(f"⑧:{baseline}", lambda: _step_registration(baseline))
    record("⑨", lambda: _step_honest_layers(form, work_dir))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops/demo_form_plugin.py",
        description="形态插件验证的离线端到端演示（九步；形态无关；零花费 / 零外部网络 / 零凭证）",
    )
    parser.add_argument(
        "--form", default=None, help="演示对象（必须是已声明形态；缺省 = 全部已声明形态）"
    )
    parser.add_argument("--out", default=None, help="产物目录（必须落临时目录；缺省即临时目录）")
    parser.add_argument(
        "--baseline", default=None, help="机制落地后的提交 ref（用于反证演示脚本不在接入改动集内）"
    )
    parser.add_argument("--keep-work-dir", action="store_true", help="保留工作目录（仅调试）")
    args = parser.parse_args(argv)

    forms = declared_forms(CONFIGS_DIR)
    if args.form and args.form not in forms:
        print(
            json.dumps(
                {"ok": False, "error": f"未声明形态：{args.form!r}（已声明：{list(forms)}）"},
                ensure_ascii=False,
            )
        )
        return EXIT_USAGE

    started = time.time()
    selected = (args.form,) if args.form else forms
    work_root = Path(tempfile.mkdtemp(prefix="form-plugin-demo-"))
    if args.out:
        candidate = Path(args.out).resolve()
        try:
            candidate.relative_to(REPO_ROOT)
        except ValueError:
            out_dir = candidate
        else:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"--out 不得落仓库内路径（{candidate}）："
                        ".gitignore 无 .specify 规则，"
                        "产物会让「仓库根零新增文件」的判据失真 ⇒ 请用 tmp_path 类临时目录",
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_USAGE
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="form-plugin-demo-out-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    before = untracked_files()
    per_form = {name: _run_form(name, work_root, args.baseline) for name in selected}
    after = untracked_files()

    steps = []
    for index, title in enumerate(STEP_NAMES):
        key = chr(0x2460 + index)
        details = [per_form[name][key] for name in selected]
        steps.append(
            {
                "step": index + 1,
                "name": title,
                "ok": all(entry[0] for entry in details),
                "forms": {
                    name: {"ok": per_form[name][key][0], "detail": per_form[name][key][1]}
                    for name in selected
                },
            }
        )
    ok = all(step["ok"] for step in steps) and before == after
    report = {
        "ok": ok,
        "forms": list(selected),
        "steps": steps,
        "network": "none",
        "credentials_required": False,
        "uncalibrated": True,
        "uncalibrated_reason": UNCALIBRATED_REASON,
        "repo_root_untracked_before": list(before),
        "repo_root_untracked_after": list(after),
        "repo_root_unchanged": before == after,
        "work_dir": str(work_root) if args.keep_work_dir else "<已清理>",
        "out_dir": str(out_dir),
        "elapsed_seconds": round(time.time() - started, 3),
    }
    (out_dir / "demo-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if not args.keep_work_dir:
        import shutil

        shutil.rmtree(work_root, ignore_errors=True)
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
