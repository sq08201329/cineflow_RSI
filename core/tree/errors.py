"""发现树与工件存储的错误类型体系。

契约见 specs/001-tree-evaluators/contracts/tree-store.md 与 artifact-store.md。
"""


class TreeStoreError(Exception):
    """发现树存储层全部错误的基类。"""


class ValidationError(TreeStoreError):
    """写入前置校验失败（字段非法、状态机违规、谱系不一致等）。"""


class DuplicateError(TreeStoreError):
    """主键冲突（tree_id / node_id 已存在）。"""


class NotFoundError(TreeStoreError):
    """按 id 读取时目标不存在。"""


class ImmutableViolationError(TreeStoreError):
    """存储层拒绝 UPDATE/DELETE（触发器或权限拦截），统一转换为此类型上抛。"""


class ArtifactStoreError(Exception):
    """工件内容寻址存储全部错误的基类。"""


class ArtifactNotFoundError(ArtifactStoreError):
    """按哈希读取工件时未命中。"""


class ArtifactCorruptedError(ArtifactStoreError):
    """读取到的内容与其哈希不符（内容寻址完整性校验失败）。"""
