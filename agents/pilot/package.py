"""样片包装配（功能 015 US3 / T1522，契约 C11/C12）：五件套 + 账目对账。

`pilot/packages/{run_id}/`：

- `manifest.json`：**"模拟生成"标注** + 形态 + 配置指纹 + 产物清单 + 阶段状态（不含墙钟/
  路径，故同输入同配置两次运行逐字节一致）；
- `reel.mp4`：成片（内容寻址哈希取自剪辑阶段产物，字节级可复现）；
- `products.json`：各阶段关键产物引用与哈希（按阶段分组）；
- `cost.json`：账目与对账结果（`core.orchestration.ledger.ledger_payload` 口径）；
- `state.json`：各阶段评分构成 / 坍缩状态 / 漂移摘要（缺数据一律如实"不适用"，不伪造）。

**缺任一件即装配失败**（先校验后落盘，不产半包）；账目对账不一致即报错（原则二）。
"""

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.orchestration.ledger import ledger_payload, summarize_cost
from core.orchestration.models import RunRecord, RunStatus

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


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_package(package_dir: str | Path, *, cost_override: str | Path | None = None) -> dict:
    """校验样片包：五件齐备 + 账目零差异（篡改即报错）。"""
    directory = Path(package_dir)
    missing = [name for name in PACKAGE_FILES if not (directory / name).is_file()]
    if missing:
        raise PackageError(f"样片包缺件：{missing}（五件套缺一即失败）")
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
    return {
        "package_dir": str(directory),
        "files": list(PACKAGE_FILES),
        "total_usd": float(cost["total_usd"]),
        "reconciled": True,
    }


def load_manifest(package_dir: str | Path) -> dict:
    path = Path(package_dir) / "manifest.json"
    if not path.is_file():
        raise PackageError(f"清单缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assemble_from_run(*, record: RunRecord, runtime: Any, package_root: str | Path) -> Path:
    """由运行记录 + 运行时工件库装配样片包（含账目对账，零差异才落盘）。"""
    if record.status is not RunStatus.DONE:
        raise PackageError(
            f"运行未完成（{record.status}）：只有六阶段全 done 才装配样片包（不产半包）"
        )
    reel = _reel_of(runtime, record)
    ledger = summarize_cost(record, _agent_ledgers(record))
    manifest = build_manifest(record)
    products = build_products(record)
    state = build_state(record, runtime)
    return assemble_package(
        run_id=record.run_id,
        package_root=package_root,
        manifest=manifest,
        reel=reel,
        products=products,
        cost=ledger_payload(ledger),
        state=state,
    )


def build_manifest(record: RunRecord) -> dict:
    """清单：模拟生成标注 + 形态 + 配置指纹 + 产物清单 + 阶段状态（确定性，无墙钟/路径）。"""
    return {
        "note": SIMULATED_NOTE,
        "run_id": record.run_id,
        "form": record.form,
        "config_fingerprint": record.config_fingerprint,
        "input_fingerprint": record.input_fingerprint,
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
            }
            for state in record.stages
        ],
    }


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


def build_state(record: RunRecord, runtime: Any = None) -> dict:
    """状态快照：各阶段评分构成 / 坍缩状态 / 漂移摘要（无数据即如实"不适用"）。"""
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
        "collapse": {
            "status": "not_applicable",
            "note": "试水运行为单轮链路，未接做梦层塌缩检测（不做无据推断）",
        },
        "drift": {
            "status": "not_applicable",
            "note": "试水运行未接 012 漂移状态（校准/漂移数据面在形态配置中另行启用）",
        },
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
