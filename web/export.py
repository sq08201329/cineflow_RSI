"""静态导出：把当前数据面导出为可离线浏览的静态资产（导出目录由 `web.export_dir` 配置）。

导出与服务**共用同一查询层**（`web/queries.py`）——预生成 JSON + 资产拷贝，杜绝
"看板一套数、导出另一套数"的双口径风险；导出目录脱离服务可直接浏览。

本模块是 `web/` 内**唯一**允许写盘的模块：目标恒为配置的导出目录，写入路径经
`_target()` 做包含性校验（拒绝路径穿越）。静态断言的白名单口径见
`tests/contract/test_web_readonly.py`。
"""

from pathlib import Path

_EXPORT_ROOT_HELP = "导出根目录（web.export_dir）；本模块全部写入必须落在其下"


def _target(dest: str | Path, relative: str) -> Path:
    """导出目标路径：解析后必须仍在导出根目录内（拒绝路径穿越）。"""
    root = Path(dest).resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"导出路径越出导出目录：{relative!r}（拒绝路径穿越）")
    return target
