"""静态导出：把当前数据面导出为可离线浏览的静态资产（导出目录由 `web.export_dir` 配置）。

导出与服务**共用同一查询层**（`web/queries.py`）——预生成 JSON + 资产拷贝，杜绝
"看板一套数、导出另一套数"的双口径风险；导出目录脱离服务可直接浏览。

本模块是 `web/` 内**唯一**允许写盘的模块，且写入必须满足两条约束（见
`tests/contract/test_web_readonly.py` 的静态断言与运行时兜底）：

1. **静态**：每处写调用都位于带 `dest` 参数（导出根目录）的函数内，且写目标表达式由
   `dest` 派生（`target = dest / "data" / "summary.json"` 这类写法）；
2. **运行时**：目标路径一律经 `_target(dest, relative)` 解析——越出导出根即报错，
   拒绝路径穿越。

页面资产由 `web/static/` 拷贝而来（同一套页面，离线与在线一致）。
"""

from pathlib import Path


def _target(dest: str | Path, relative: str) -> Path:
    """导出目标路径：解析后必须仍在导出根目录内（拒绝路径穿越）。"""
    root = Path(dest).resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"导出路径越出导出目录：{relative!r}（拒绝路径穿越）")
    return target
