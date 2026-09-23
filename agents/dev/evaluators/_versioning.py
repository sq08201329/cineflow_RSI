"""开发 Agent 评估器共享工具：实现哈希版本号（004/006/007/008/009 `_versioning.py` 同款）。

版本号 = 1.0.0+<实现文件内容与口径参数哈希前12位>——实现或口径参数（阈值/规则库/模拟
数据源参数）任一变更即新版本（宪章原则一，可机检）。**按包复制、不跨包导入**：跨包导入
会把别的 Agent 的实现文件拖进本包版本哈希，版本号就不再指向"本评估器的行为口径"。
"""

from pathlib import Path

import blake3


def implementation_version(*parts: str, base: str = "1.0.0") -> str:
    """版本号：base+<实现文件与附加部件（阈值/规则库/口径参数）哈希前 12 位>。"""
    hasher = blake3.blake3()
    hasher.update(Path(__file__).parent.parent.name.encode())  # 包路径标识（dev）
    return _version_with_caller(hasher, parts, base)


def _version_with_caller(hasher, parts: tuple, base: str) -> str:
    import inspect

    caller_file = inspect.stack()[2].filename  # 调用方评估器的实现文件
    hasher.update(Path(caller_file).read_bytes())
    for part in parts:
        hasher.update(part.encode())
    return f"{base}+{hasher.hexdigest()[:12]}"
