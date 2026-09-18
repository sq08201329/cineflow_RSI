"""策略版本管理（data-model §3，FR-015）。

版本 = 策略代码内容的 BLAKE3 前 12 位；历史版本落盘
`policies/history/{agent_id}/{version}.py`，同内容重复提交幂等
（同路径同内容），内容不同版本号必不同。
"""

from pathlib import Path

import blake3


class VersionConflictError(Exception):
    """版本路径已存在但内容不符（哈希完整性兜底，理论上不应发生）。"""


def policy_version(source: str) -> str:
    """策略版本号：代码内容 BLAKE3 前 12 位。"""
    return blake3.blake3(source.encode("utf-8")).hexdigest()[:12]


def record_policy(source: str, agent_id: str, *, history_root: Path | str) -> str:
    """把策略代码落盘到 history/{agent_id}/{version}.py（幂等），返回版本号。"""
    version = policy_version(source)
    target = Path(history_root) / agent_id / f"{version}.py"
    if target.exists():
        if target.read_text(encoding="utf-8") != source:
            raise VersionConflictError(f"版本 {version} 路径内容不符（哈希完整性被破坏）")
        return version  # 幂等：同内容重复提交零变化
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".py.tmp")
    tmp.write_text(source, encoding="utf-8")
    tmp.replace(target)  # 原子落盘
    return version
