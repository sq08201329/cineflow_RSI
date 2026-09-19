"""视觉评估器共享工具：实现哈希版本号（research 决策 4/5）。

版本号 = 1.0.0+<实现文件内容与采样参数/提示词/锚点哈希前12位>——
实现、采样规则、提示词、锚点集任一变更即新版本（SC-007 可机检）。
"""

from pathlib import Path

import blake3


def implementation_version(*parts: str, base: str = "1.0.0") -> str:
    """版本号：base+<实现文件与附加部件（采样规格/提示词/锚点）哈希前 12 位>。"""
    hasher = blake3.blake3()
    hasher.update(Path(__file__).parent.name.encode())  # 包路径标识
    return _version_with_caller(hasher, parts, base)


def _version_with_caller(hasher, parts: tuple, base: str) -> str:
    import inspect

    caller_file = inspect.stack()[2].filename  # 调用方评估器的实现文件
    hasher.update(Path(caller_file).read_bytes())
    for part in parts:
        hasher.update(part.encode())
    return f"{base}+{hasher.hexdigest()[:12]}"
