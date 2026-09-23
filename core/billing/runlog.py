"""渠道运行记录（按时间索引的 append-only 证据；契约 C3 / C15）。

五模块之一，承接"连续运行"从叙述变成**可机检事实**的记录面：

- 粒度：每次真实调用一条 entry（`at` / `stage` / `source` / `adapter_ref` / `profile_id` /
  `result` / `cost_source` / `fallback_reason`）；按 `peak_windows.timezone` 的**本地日期**
  分片落 `billing/{channel}/runs/{date}.json`（与峰谷判定、额度 day 窗口同一日历）；
- `entries` 追加只增；`head_digest` = BLAKE3(前一条 `head_digest` + 本条 canonical JSON)
  **链式摘要**——改写或删除任一条即断链 ⇒ 读取报错（不静默取）；
- 当日 `sealed` 后追加拒绝（当日证据封存）；
- `source=fallback`（回落）必须显式声明**原因**：真实渠道失败**禁止**静默回落模拟并照常计费
  （FR-013）。该值为保留值——本特性无写入点（装配期即拒、网关不设回落路径），但读取口径
  必须齐备，故本模块在写入侧就要求原因。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import blake3

from core.billing.budget import BudgetConfig, channel_dir

RUN_SOURCES = ("real", "simulated", "fallback")
# 保留值：本特性无写入点（装配期即拒、网关不设回落路径），值域保留 + 写入侧要求原因
FALLBACK_SOURCE = "fallback"
# 每条 entry 的必填字段（缺项即拒绝追加：记录不齐等于证据不齐）
RUN_ENTRY_FIELDS = (
    "at",
    "stage",
    "source",
    "adapter_ref",
    "profile_id",
    "result",
    "cost_source",
    "fallback_reason",
)


class RunLogError(Exception):
    """运行记录错误（来源取值域外、回落无原因、当日已封存、链式摘要断链）。"""


@dataclass(frozen=True)
class RunEntry:
    """一条运行记录（一次真实调用）；`head_digest` 为链式摘要的当前值。"""

    at: str
    stage: str
    source: str
    adapter_ref: str
    profile_id: str
    result: str
    cost_source: str
    fallback_reason: str
    head_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "stage": self.stage,
            "source": self.source,
            "adapter_ref": self.adapter_ref,
            "profile_id": self.profile_id,
            "result": self.result,
            "cost_source": self.cost_source,
            "fallback_reason": self.fallback_reason,
            "head_digest": self.head_digest,
        }


def run_path(root: str | Path, channel_id: str, date: str) -> Path:
    """运行记录路径（C4）：`billing/{channel}/runs/{date}.json`（`{date}` = 渠道本地日期）。"""
    return channel_dir(root, channel_id) / "runs" / f"{date}.json"


def _entry_digest(previous: str, entry: Mapping) -> str:
    """链式摘要：`BLAKE3(前一条 head_digest + 本条 canonical JSON)`（改任一条即断链）。"""
    canonical = json.dumps(
        {key: entry.get(key) for key in RUN_ENTRY_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return blake3.blake3(f"{previous}|{canonical}".encode()).hexdigest()


def _load_raw(path: Path) -> dict:
    if not path.is_file():
        raise RunLogError(f"运行记录不存在：{path}（先追加一次再读取，不静默返回空）")
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_chain(payload: Mapping, path: Path) -> None:
    """链校验：逐条重算摘要，任一处不符即报错（改写/删除/插入都逃不过）。"""
    previous = ""
    for index, entry in enumerate(payload.get("entries", [])):
        expected = _entry_digest(previous, entry)
        if entry.get("head_digest") != expected:
            raise RunLogError(
                f"运行记录链校验失败：{path} 第 {index + 1} 条 entry 的 head_digest 不匹配"
                "（记录被改写或删除，拒绝采信）"
            )
        previous = expected
    if payload.get("head_digest") != previous:
        raise RunLogError(
            f"运行记录链校验失败：{path} 的 head_digest 与 entries 不一致（尾条被改写或删除）"
        )


def append_run(
    channel_id: str,
    *,
    cfg: BudgetConfig,
    root: str | Path,
    moment: datetime,
    stage: str,
    source: str,
    adapter_ref: str = "",
    profile_id: str = "",
    result: str = "",
    cost_source: str = "",
    fallback_reason: str = "",
) -> dict:
    """追加一条运行记录（日期按 `peak_windows.timezone` 归属；当日封存后拒绝）。

    来源取值域 `real|simulated|fallback`；`fallback` 必须显式声明原因——回落是被**声明**的事实，
    不是可静默发生的事。
    """
    if source not in RUN_SOURCES:
        raise RunLogError(f"运行来源必须 ∈ {list(RUN_SOURCES)}，实际为 {source!r}")
    if not str(stage or "").strip():
        raise RunLogError("stage（环节 id）必填：运行记录必须可归到环节")
    if source == FALLBACK_SOURCE and not str(fallback_reason or "").strip():
        raise RunLogError(
            "source=fallback 必须声明 fallback_reason（真实渠道失败禁止静默回落并照常计费）"
        )
    cfg.channel(channel_id)
    date = cfg.local_date(moment)
    path = run_path(root, channel_id, date)
    if path.is_file():
        payload = _load_raw(path)
        _verify_chain(payload, path)
        if payload.get("sealed"):
            raise RunLogError(f"当日运行记录已封存（sealed），追加拒绝：{path}")
    else:
        payload = {
            "channel_id": str(channel_id),
            "date": date,
            "timezone": cfg.peak_windows.timezone,
            "entries": [],
            "head_digest": "",
            "sealed": False,
        }
    if str(payload.get("channel_id")) != str(channel_id):
        raise RunLogError(f"运行记录文件与渠道不符：{path} 属 {payload.get('channel_id')!r}")
    entry = {
        "at": moment.isoformat(),
        "stage": str(stage),
        "source": str(source),
        "adapter_ref": str(adapter_ref),
        "profile_id": str(profile_id),
        "result": str(result),
        "cost_source": str(cost_source),
        "fallback_reason": str(fallback_reason),
    }
    digest = _entry_digest(str(payload.get("head_digest", "")), entry)
    entry["head_digest"] = digest
    payload["entries"] = [*payload.get("entries", []), entry]
    payload["head_digest"] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return entry


def seal_run(date: str, *, channel_id: str, root: str | Path) -> dict:
    """封存当日记录（此后追加拒绝）。封存不改动既有 entries 与链（只置 sealed）。"""
    path = run_path(root, channel_id, date)
    payload = _load_raw(path)
    _verify_chain(payload, path)
    payload["sealed"] = True
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def load_run(date: str, *, channel_id: str, root: str | Path) -> dict:
    """读取当日记录并**链校验**：改写/删除任一条即报错（不静默取）。"""
    path = run_path(root, channel_id, date)
    payload = _load_raw(path)
    if str(payload.get("channel_id")) != str(channel_id):
        raise RunLogError(f"运行记录文件与渠道不符：{path} 属 {payload.get('channel_id')!r}")
    _verify_chain(payload, path)
    return payload
