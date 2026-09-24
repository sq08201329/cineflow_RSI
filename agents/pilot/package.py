"""样片包装配（功能 015 US3 / T1522，契约 C11/C12 + 功能 018 US3 的证据面）：五件套 + 账目对账。

`pilot/packages/{run_id}/`：

- `manifest.json`：**"模拟生成"标注** + 形态 + 配置指纹 + 产物清单 + 阶段状态 + **逐环节
  真实/模拟标注**（`stages[].source`/`channel` 与顶层 `channels` 汇总，C12）+ `work_kind`
  （排练/真实作品的机检标记）；
- `reel.mp4`：成片（内容寻址哈希取自剪辑阶段产物，字节级可复现）；
- `products.json`：各阶段关键产物引用与哈希（按阶段分组）；
- `cost.json`：账目与对账结果（`core.orchestration.ledger.ledger_payload` 口径）
  + **成本第三方腿**（逐环节网关记账增量的确定性段，C13）；
- `state.json`：各阶段评分构成 / 坍缩状态 / 漂移摘要 / **逐环节评估分量**
  （`eval_breakdown`，取自树节点原文，C11）/ **体量指标**（`volume`）。

**缺任一件即装配失败**（先校验后落盘，不产半包）；账目对账不一致即报错（原则二）；
逐环节分量缺项、标注缺项、证据面含墙钟、包内出现第六件——一律**拒绝装配/拒绝复验**
（`verify_package` 走同一套证据面校验）。

**产物标签与内容类型逐项机检**（`check_product_kinds`）：清单是评审看的证据载体，
标签与内容不符会误导评审（曾把预演 mp4 标成 `shotlist`），故装配前按内容寻址字节的
自描述类型核对每个产物的 `kind`——不符即拒绝装配；新增 kind 必须同时声明内容类型
（不静默放行未知标签）。**性能画像与账本窗口口径落报告侧**（`agents/pilot/run_report.py`
的 `pilot/profiles/{run_id}.json`）：它们含墙钟与窗口累计，混进五件套会破坏逐字节可比。
"""

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.pilot import backends as backends_module
from agents.pilot import run_report
from core.orchestration.ledger import ledger_payload, summarize_cost
from core.orchestration.models import RunRecord, RunStatus
from core.tree.artifacts import ArtifactStore

PACKAGE_FILES = ("manifest.json", "reel.mp4", "products.json", "cost.json", "state.json")
REEL_FILENAME = "reel.mp4"
# 诚实边界（宪章原则六）：全链路模拟生成，标注必须进入清单
SIMULATED_NOTE = (
    "模拟生成（simulated）：本样片包由确定性模拟生成器与模拟投放平台产出，"
    "非真实生成/投放结果，不得作为对外发布素材。"
)


class PackageError(Exception):
    """样片装配错误（缺件 / 账目对不上 / 前置条件不满足）。"""


@dataclass(frozen=True)
class PackagePieces:
    """五件套内容：装配前一次性校验，避免产出半包。"""

    manifest: Mapping[str, Any]
    reel: bytes | None
    products: Mapping[str, Any]
    cost: Mapping[str, Any]
    state: Mapping[str, Any]


def assemble_package(
    *,
    run_id: str,
    package_root: str | Path,
    manifest: Mapping[str, Any],
    reel: bytes | None,
    products: Mapping[str, Any],
    cost: Mapping[str, Any],
    state: Mapping[str, Any],
) -> Path:
    """装配五件套（缺件即失败；先校验后落盘）。"""
    pieces = PackagePieces(manifest=manifest, reel=reel, products=products, cost=cost, state=state)
    _validate_pieces(pieces)
    package_dir = Path(package_root) / run_id
    package_dir.mkdir(parents=True, exist_ok=True)
    _write_json(package_dir / "manifest.json", manifest)
    _write_json(package_dir / "products.json", products)
    _write_json(package_dir / "cost.json", cost)
    _write_json(package_dir / "state.json", state)
    (package_dir / REEL_FILENAME).write_bytes(pieces.reel or b"")
    verify_package(package_dir)
    return package_dir


def _validate_pieces(pieces: PackagePieces) -> None:
    if not pieces.reel:
        raise PackageError("成片缺失：reel.mp4 无内容（不产半包）")
    if not pieces.manifest or SIMULATED_NOTE not in str(pieces.manifest.get("note", "")):
        raise PackageError("清单缺失或未标注「模拟生成」（诚实边界：标注不可省）")
    if not pieces.manifest.get("products"):
        raise PackageError("清单缺产物清单（products）")
    if not pieces.manifest.get("stages"):
        raise PackageError("清单缺阶段状态（stages）")
    if not pieces.manifest.get("config_fingerprint"):
        raise PackageError("清单缺配置指纹（版本冻结：必须可追溯）")
    if not pieces.products:
        raise PackageError("products.json 为空（产物引用必须齐备）")
    if not pieces.cost:
        raise PackageError("cost.json 为空（账目必须齐备）")
    if not pieces.state:
        raise PackageError("state.json 为空（状态快照必须齐备）")
    _require_stage_labels(pieces)
    _require_stage_breakdown(pieces)


def _require_stage_labels(pieces: PackagePieces) -> None:
    """逐环节来源标注齐备（C12）：`stages[].source`/`channel` + 顶层 `channels` 汇总。

    空标注（缺键/空串）即拒绝装配——"模拟被标为真实"与"标注缺失"都是证据面缺陷，
    不得以省略冒充分层可机读。
    """
    channels = pieces.manifest.get("channels")
    if (
        not isinstance(channels, Mapping)
        or not isinstance(channels.get("llm"), Mapping)
        or not channels.get("llm")
    ):
        raise PackageError(
            "清单缺顶层 channels 汇总（LLM 腿来源/档案引用 + 平台侧逐环节来源）：缺项即拒绝装配"
        )
    from agents.pilot.stages import PILOT_STAGE_IDS

    declared = channels.get("stages")
    if not isinstance(declared, Mapping) or set(declared) != set(PILOT_STAGE_IDS):
        raise PackageError(
            f"channels.stages 键域 {sorted(declared or ())} != 七环节"
            "（逐环节标注须逐环节声明，缺一处即拒绝装配）"
        )
    llm_summary = channels.get("llm")
    for stage_id in PILOT_STAGE_IDS:
        if not isinstance(declared.get(stage_id), Mapping):
            raise PackageError(f"channels.stages.{stage_id} 不是标注映射：拒绝装配（不静默跳过）")
    if llm_summary.get("source") != declared["script"]["source"]:
        raise PackageError(
            "LLM 腿标注与顶层 channels.llm 不一致（dev/script 无平台槽位，来源只能取 LLM 腿口径）"
        )
    rows = {str(row.get("stage_id")): row for row in pieces.manifest.get("stages") or ()}
    problems = []
    for stage_id in PILOT_STAGE_IDS:
        row = rows.get(stage_id)
        if row is None:
            problems.append(f"{stage_id} 缺阶段记录")
            continue
        if not row.get("source") or not row.get("channel"):
            problems.append(f"{stage_id} 缺来源标注（source/channel）")
            continue
        if row["source"] not in backends_module.BACKEND_SOURCES:
            problems.append(f"{stage_id} 来源标注取值域外 {row['source']!r}")
        if row["source"] != declared[stage_id]["source"]:
            problems.append(
                f"{stage_id} 标注 {row['source']!r} != 装配面声明 {declared[stage_id]['source']!r}"
                "（逐环节标注只来自装配面声明：模拟不得被标为真实）"
            )
    if problems:
        raise PackageError(
            "逐环节真实/模拟标注不齐备（包内不得出现空标注）：" + "；".join(problems)
        )
    _reject_wall_clock(
        {
            "channels": dict(channels),
            "stages": [
                {"source": row.get("source"), "channel": row.get("channel")}
                for row in rows.values()
            ],
            "work_kind": pieces.manifest.get("work_kind"),
        },
        "清单的证据面字段",
    )


def _require_stage_breakdown(pieces: PackagePieces) -> None:
    """逐环节评估分量非空（C11）：七环节**每个**都要有分量面，且不含墙钟字段。

    空对象 / 缺环节 / 带 `created_at` 类墙钟字段一律拒绝装配（不得以"不适用"冒充分量齐备，
    也不得把墙钟混进逐字节比对面）。
    """
    from agents.pilot.stages import PILOT_STAGE_IDS

    breakdown = pieces.state.get("eval_breakdown")
    if not isinstance(breakdown, Mapping):
        raise PackageError(
            "state.json 缺逐环节评估分量（eval_breakdown）：缺项即拒绝装配（不得留空冒充）"
        )
    missing = [stage_id for stage_id in PILOT_STAGE_IDS if not breakdown.get(stage_id)]
    if missing:
        raise PackageError(
            f"评估分量缺项 {missing}：七环节每个都要有非空分量面"
            "（空对象 / 「不适用」 / 缺环节一律拒绝装配）"
        )
    for stage_id in PILOT_STAGE_IDS:
        node_map = breakdown[stage_id]
        if not isinstance(node_map, Mapping) or not node_map:
            raise PackageError(f"环节 {stage_id} 的分量面为空：缺项即拒绝装配")
        for node_id, fragments in node_map.items():
            if not isinstance(fragments, Mapping) or not fragments:
                raise PackageError(f"环节 {stage_id} 节点 {node_id} 的分量面为空：缺项即拒绝装配")
            for key in fragments:
                if "@" not in str(key):
                    raise PackageError(f"环节 {stage_id} 分量键不是 evaluator_id@version：{key!r}")
    _reject_wall_clock({"eval_breakdown": breakdown}, "逐环节分量面")
    _reject_wall_clock({"volume": pieces.state.get("volume") or {}}, "体量指标")


def _reject_wall_clock(payload: Any, where: str) -> None:
    """证据面字段的**墙钟隔离**机检：新增字段不得含 `created_at`/`now`/时间戳类键。

    墙钟只能在报告侧（性能画像）；混进逐字节比对面即破坏可复现性（FR-012），故在装配与
    复验两处都拒绝。
    """
    banned = ("created_at", "now", "timestamp", "wall_clock", "started_at", "finished_at")
    if isinstance(payload, Mapping):
        hits = sorted(str(key) for key in payload if str(key) in banned)
        if hits:
            raise PackageError(f"{where}含墙钟字段 {hits}：新增字段必须确定性（无墙钟/无绝对路径）")
        for key, value in payload.items():
            _reject_wall_clock(value, f"{where}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            _reject_wall_clock(value, f"{where}[{index}]")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_package(package_dir: str | Path, *, cost_override: str | Path | None = None) -> dict:
    """校验样片包：五件齐备 + 账目零差异 + **证据面自洽**（篡改即报错）。

    证据面自洽（功能 018 / C11/C12）：逐环节分量面非空、逐环节标注齐备且与顶层 `channels`
    汇总**一致**、新增字段不含墙钟——包内证据被改写（把 `simulated` 改标 `real`、抹掉某环节
    分量、塞入墙钟、加第六件）都在此**被拒**（不产误导性清单）。
    """
    directory = Path(package_dir)
    missing = [name for name in PACKAGE_FILES if not (directory / name).is_file()]
    if missing:
        raise PackageError(f"样片包缺件：{missing}（五件套缺一即失败）")
    extra = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name not in PACKAGE_FILES
    )
    if extra:
        raise PackageError(
            f"样片包出现五件套之外的文件 {extra}：包面口径不变（画像/报告落报告侧，不入包）"
        )
    if (directory / REEL_FILENAME).stat().st_size <= 0:
        raise PackageError("成片为空文件（不视为齐备）")
    cost_path = Path(cost_override) if cost_override is not None else directory / "cost.json"
    cost = json.loads(cost_path.read_text(encoding="utf-8"))
    by_stage = cost.get("by_stage") or {}
    lines = cost.get("lines") or []
    if not by_stage or not lines:
        raise PackageError("账目载荷不完整（by_stage/lines 缺一即失败）")
    for line in lines:
        recorded = float(line["recorded_usd"])
        ledger = float(line["ledger_usd"])
        if abs(recorded - ledger) > 1e-9:
            raise PackageError(
                f"账目对账不一致：阶段 {line['stage_id']} 记录 {recorded} vs 账目 {ledger}"
            )
        if line["stage_id"] in by_stage and abs(float(by_stage[line["stage_id"]]) - ledger) > 1e-9:
            raise PackageError(f"账目对账不一致：阶段 {line['stage_id']} 汇总与逐项不符")
    if abs(float(cost.get("total_usd", 0.0)) - sum(by_stage.values())) > 1e-9:
        raise PackageError("账目对账不一致：合计与按阶段汇总不符")
    manifest = load_manifest(directory)
    if SIMULATED_NOTE not in str(manifest.get("note", "")):
        raise PackageError("清单未标注「模拟生成」（诚实边界）")
    state = _load_json(directory / "state.json")
    products = _load_json(directory / "products.json")
    _validate_pieces(
        PackagePieces(
            manifest=manifest,
            reel=(directory / REEL_FILENAME).read_bytes(),
            products=products,
            cost=cost,
            state=state,
        )
    )
    return {
        "package_dir": str(directory),
        "files": list(PACKAGE_FILES),
        "total_usd": float(cost["total_usd"]),
        "reconciled": True,
    }


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise PackageError(f"包内文件缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(package_dir: str | Path) -> dict:
    path = Path(package_dir) / "manifest.json"
    if not path.is_file():
        raise PackageError(f"清单缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assemble_from_run(*, record: RunRecord, runtime: Any, package_root: str | Path) -> Path:
    """由运行记录 + 运行时工件库装配样片包（含账目对账，零差异才落盘）。"""
    if record.status is not RunStatus.DONE:
        raise PackageError(
            f"运行未完成（{record.status}）：只有七环节全 done 才装配样片包（不产半包）"
        )
    reel = _reel_of(runtime, record)
    check_product_kinds(record, runtime.artifacts)  # 标签↔内容类型逐项机检（证据载体不误导）
    ledger = summarize_cost(record, _agent_ledgers(record))
    manifest = build_manifest(record, runtime)
    products = build_products(record)
    state = build_state(record, runtime)
    cost = ledger_payload(ledger)
    # 成本第三方腿（C13）：逐环节网关记账增量落 `cost.json` 的**确定性段**
    # （账本窗口可比性口径随报告侧登记；口径不符即在此报错，不产带缺陷的账目）
    cost["gateway"] = run_report.gateway_leg(record)
    return assemble_package(
        run_id=record.run_id,
        package_root=package_root,
        manifest=manifest,
        reel=reel,
        products=products,
        cost=cost,
        state=state,
    )


# 产物 kind ↔ 内容类型对照（清单标签必须如实描述内容：新增 kind 时**必须**在此声明，
# 未登记即拒绝装配——不静默放行未知标签，否则同类标签漂移会重新溜进证据包）
_KIND_CONTENT_TYPE = {
    "slate": "json",  # 立项组合（TopicSlate canonical JSON，链首产出）
    "script": "json",  # 剧本工件（ScriptArtifact canonical JSON）
    "shotlist": "json",  # 分镜清单（ShotList canonical JSON，内容寻址）
    "material": "json",  # 宣发物料（模拟平台的文案载荷）
    "animatic": "video",  # 分镜预演 mp4（renderer 产出）
    "clip": "video",  # 视觉片段 mp4
    "reel": "video",  # 成片 mp4
    "audio": "audio",  # 声音轨 wav
}


def _content_type(content: bytes) -> str:
    """按字节自描述识别内容类型（不信任标签：以容器/魔数为准）。"""
    if len(content) < 12:
        return "too_short"
    if content[4:8] == b"ftyp":  # ISO BMFF（mp4 系）
        return "video"
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "audio"
    if content.lstrip()[:1] in (b"{", b"["):
        return "json"
    return "unknown"


def check_product_kinds(record: RunRecord, artifacts: ArtifactStore) -> None:
    """逐项机检产物 `kind` 与其内容类型一致；不符/未登记即拒绝装配（不产误导性清单）。"""
    mismatches: list[str] = []
    for state in record.stages:
        for product in state.products:
            expected = _KIND_CONTENT_TYPE.get(product.kind)
            actual = _content_type(artifacts.get(product.content_hash))
            if expected is None:
                mismatches.append(
                    f"{state.stage_id}: kind={product.kind!r} 未登记内容类型"
                    "（新增 kind 须在 _KIND_CONTENT_TYPE 声明）"
                )
            elif actual != expected:
                mismatches.append(
                    f"{state.stage_id}: kind={product.kind!r} 期望 {expected}，"
                    f"实际内容为 {actual}（{product.content_hash}）"
                )
    if mismatches:
        raise PackageError(
            "产物标签与内容类型不符（清单是证据载体，标签错误即误导）：" + "；".join(mismatches)
        )


def build_manifest(record: RunRecord, runtime: Any) -> dict:
    """清单：模拟生成标注 + 形态 + 配置指纹 + 产物清单 + 阶段状态 + **逐环节来源标注**。

    确定性（无墙钟/路径/进程内顺序）：来源标注取自装配面声明（`backends.stage_channels`），
    `work_kind` 取自形态配置的排练档声明。**缺声明即装配期拒绝**（`BackendAssemblyError`），
    包内不得出现空标注。
    """
    channels = stage_channels_of(runtime)
    return {
        "note": SIMULATED_NOTE,
        "run_id": record.run_id,
        "form": record.form,
        "config_fingerprint": record.config_fingerprint,
        "input_fingerprint": record.input_fingerprint,
        # 排练/真实作品的机检标记（与预检报告 `pilot_volume.work_kind` 同一取值来源）
        "work_kind": str(runtime.pilot.work_kind),
        "channels": channels,
        "products": [
            {
                "stage_id": state.stage_id,
                "kind": product.kind,
                "ref": product.ref,
                "content_hash": product.content_hash,
            }
            for state in record.stages
            for product in state.products
        ],
        "stages": [
            {
                "stage_id": state.stage_id,
                "status": state.status.value,
                "cost_usd": state.cost_usd,
                "product_count": len(state.products),
                "candidate_count": len(state.candidates),
                # 逐环节真实/模拟标注（C12）：取值只来自装配面声明，不推断、不默认
                "source": channels["stages"][state.stage_id]["source"],
                "channel": channels["stages"][state.stage_id]["channel"],
            }
            for state in record.stages
        ],
    }


def stage_channels_of(runtime: Any) -> dict:
    """逐环节标注 + 顶层汇总（`channels.stages` / `channels.llm` / `channels.platform`）。"""
    rows = backends_module.stage_channels(runtime.backends)
    llm_source = rows["script"]["source"]
    platform = {
        stage_id: rows[stage_id]["source"]
        for stage_id, slot in backends_module.STAGE_BACKEND_SLOT.items()
        if slot != backends_module.LLM_SLOT
    }
    return {
        "stages": rows,
        "llm": {
            "source": llm_source,
            "adapter_ref": _llm_adapter_ref(runtime),
            "profile_id": rows["script"]["channel"],
        },
        "platform": platform,
        "note": (
            "平台侧五环节的标注只来自装配面声明（平台腿无 019 账单/账本/运行记录面，"
            "无交叉核对面，如实登记）；LLM 腿的逐调用来源在 019 运行记录里可交叉核对"
        ),
    }


def _llm_adapter_ref(runtime: Any) -> str:
    """LLM 渠道的适配器引用（019 装配面声明；缺门禁即如实留空，不编造）。"""
    guard = getattr(runtime.gateway, "spend_guard", None)
    cfg = getattr(guard, "cfg", None)
    channel_id = str(getattr(guard, "channel_id", "") or "")
    if cfg is None or not channel_id:
        return ""
    return str(cfg.channel(channel_id).adapter)


def build_products(record: RunRecord) -> dict:
    """产物清单：成片引用 + 各阶段关键产物引用与哈希（按阶段分组）。"""
    reel_stage = record.stage("editing")
    reel = next((product for product in reel_stage.products if product.kind == "reel"), None)
    if reel is None:
        raise PackageError("剪辑阶段无成片产物引用（装配拒绝）")
    return {
        "reel": {"content_hash": reel.content_hash, "ref": reel.ref},
        "by_stage": [
            {
                "stage_id": state.stage_id,
                "products": [
                    {"kind": p.kind, "ref": p.ref, "content_hash": p.content_hash}
                    for p in state.products
                ],
            }
            for state in record.stages
        ],
    }


def build_state(record: RunRecord, runtime: Any) -> dict:
    """状态快照：评分构成 / **逐环节评估分量** / **体量指标** / 坍缩 / 漂移（无数据即"不适用"）。

    - `eval_breakdown`：逐环节**取自树节点原文**（键 = `evaluator_id@version`；不改写、不归一化、
      不重算），派生产物（如分镜 `shotlist`）显式标 `derived: true` + "无节点分量"——
      与"分量缺项"**不混同**；
    - `volume`：镜头数/片段数/成片时长/页数（与报告侧画像**同源**，见
      `run_report.volume_snapshot`）。

    全为确定性字段（无墙钟/绝对路径/进程内顺序），故同输入同配置两次运行仍逐字节一致。
    """
    scores = {}
    for state in record.stages:
        scores[state.stage_id] = {
            "candidates": [
                {"candidate_id": c.candidate_id, "score": c.score, "reasons": list(c.reasons)}
                for c in state.candidates
            ],
            "attempts": state.attempts,
        }
    return {
        "note": SIMULATED_NOTE,
        "form": record.form,
        "scores": scores,
        "eval_breakdown": eval_breakdown_of(record, runtime),
        "derived_products": derived_products_of(record, runtime),
        "volume": run_report.volume_snapshot(record, runtime),
        "collapse": {
            "status": "not_applicable",
            "note": "试水运行为单轮链路，未接做梦层塌缩检测（不做无据推断）",
        },
        "drift": _drift_summary(runtime),
    }


def stage_tree_id(stage_id: str, run_id: str) -> str:
    """环节轮次树的确定性标识（`STAGE_TREE_PREFIX` 的单一消费口径，**不得**以 stage_id 直推）。"""
    from agents.pilot import stages as stages_module

    return stages_module.round_tree_id_of(stage_id, f"{run_id}-{stage_id}")


def stage_nodes(record: RunRecord, runtime: Any, stage_id: str) -> list:
    """某环节的候选节点（`depth >= 1`，按 `node_id` 排序 ⇒ 确定性）。"""
    tree_id = stage_tree_id(stage_id, record.run_id)
    return sorted(
        (node for node in runtime.store.nodes_of(tree_id) if node.depth >= 1),
        key=lambda node: node.node_id,
    )


def eval_breakdown_of(record: RunRecord, runtime: Any) -> dict:
    """逐环节评估分量（**取自树节点原文**：键 = `evaluator_id@version`，不重算、不归一化）。"""
    breakdown: dict[str, dict] = {}
    for state in record.stages:
        breakdown[state.stage_id] = {
            node.node_id: {
                key: dict(value) if isinstance(value, Mapping) else value
                for key, value in (node.eval_breakdown or {}).items()
            }
            for node in stage_nodes(record, runtime, state.stage_id)
        }
    return breakdown


def derived_products_of(record: RunRecord, runtime: Any) -> dict:
    """派生产物标注：不对应任何候选节点的产物**显式标 `derived: true`** + "无节点分量"。

    分镜阶段的 `shotlist`（清单 JSON 另立内容寻址工件）即此类：它的分量面本就不来自节点，
    故必须与"分量缺项"区分登记（缺项是缺陷，派生产物是口径）。
    """
    derived: dict[str, list[dict]] = {}
    for state in record.stages:
        node_hashes = {node.artifact_hash for node in stage_nodes(record, runtime, state.stage_id)}
        rows = [
            {
                "kind": product.kind,
                "ref": product.ref,
                "content_hash": product.content_hash,
                "derived": True,
                "note": "派生产物：无节点分量（分量面取自该环节候选节点）",
            }
            for product in state.products
            if product.content_hash not in node_hashes
        ]
        if rows:
            derived[state.stage_id] = rows
    return derived


def _drift_summary(runtime: Any) -> dict:
    """漂移摘要：runtime 装配了 012 门禁即**如实报告**登记状态与处置口径。

    未装配 → 如实"不适用"（不伪造）。只记状态与阈值快照（不含路径/墙钟），
    故两次同输入同配置运行仍逐字节一致。
    """
    gate = getattr(runtime, "drift_gate", None)
    if gate is None:
        return {
            "status": "not_applicable",
            "note": "本次运行未装配 012 漂移门禁（无状态来源）",
        }
    registered = {
        key: status.status.value for key, status in sorted(gate.registry.current().items())
    }
    disposition = "排除出合成" if gate.cfg.confirmed_exclude else "降权"
    return {
        "status": "wired",
        "registered": registered,
        "thresholds": gate.cfg.thresholds_snapshot(),
        "note": (
            "012 漂移门禁在 runtime 装配一次并透传给四个 judge 阶段"
            "（script/storyboard/visual/editing）；"
            f"当前登记 {len(registered)} 个评估器版本："
            f"suspect → 该分量权重 ×{gate.cfg.suspect_weight}，confirmed_drift → {disposition}"
        ),
    }


def _reel_of(runtime: Any, record: RunRecord) -> bytes:
    reel_stage = record.stage("editing")
    reel = next((product for product in reel_stage.products if product.kind == "reel"), None)
    if reel is None:
        raise PackageError("剪辑阶段无成片产物引用（装配拒绝）")
    try:
        return runtime.artifacts.get(reel.content_hash)
    except Exception as exc:  # noqa: BLE001 - 成片取不到即装配失败（不产半包）
        raise PackageError(f"成片工件缺失：{reel.content_hash}（{exc}）") from exc


def _agent_ledgers(record: RunRecord) -> dict[str, float]:
    """各 Agent 落盘成本（阶段明细里的 `spent_usd`，由各 Agent loop 对账后回填）。"""
    ledgers: dict[str, float] = {}
    for state in record.stages:
        spent = state.detail.get("spent_usd")
        if spent is None:
            raise PackageError(
                f"阶段 {state.stage_id} 缺少落盘成本（spent_usd）：账目无法对账（拒绝装配）"
            )
        ledgers[state.stage_id] = float(spent)
    return ledgers


def copy_reel_to(package_dir: str | Path, target: str | Path) -> Path:
    """成片另存（CLI/演示用；包内成片保持原样不被修改）。"""
    source = Path(package_dir) / REEL_FILENAME
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination
