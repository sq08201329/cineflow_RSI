"""工件内容寻址存储（BLAKE3 哈希即 key，PUT-if-absent 幂等去重）。

契约见 specs/001-tree-evaluators/contracts/artifact-store.md：
- key 恒等于 blake3(content).hexdigest()；get 校验内容，不符即 ArtifactCorruptedError；
- put 幂等：相同内容二次写入零开销，返回同一 hash（FR-005 / SC-006）；
- 接口不存在删除/覆盖方法（不可变）。
"""

import re
from pathlib import Path
from typing import Protocol

import blake3

from core.tree.errors import (
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ValidationError,
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def content_hash(content: bytes) -> str:
    """计算内容的 BLAKE3 十六进制哈希（64 字符）。"""
    return blake3.blake3(content).hexdigest()


def _require_hash(artifact_hash: str) -> None:
    """哈希格式校验：非法格式直接拒绝，杜绝路径穿越与枚举（哈希驱动读取）。

    非字符串（如 `None`）也走同一路径：给 **ValidationError**（带收到的值）而不是让
    `re.match` 抛裸 `TypeError: expected string or bytes-like object, got 'NoneType'` ——
    真实故障里这种裸 TypeError 落进运行记录，失败原因看不出任何业务信息（功能 016 收尾）。
    """
    if not isinstance(artifact_hash, str) or not _HASH_RE.match(artifact_hash):
        raise ValidationError(f"artifact_hash 必须为 64 位小写十六进制，实际为 {artifact_hash!r}")


def _verify(artifact_hash: str, content: bytes) -> None:
    if content_hash(content) != artifact_hash:
        raise ArtifactCorruptedError(f"内容与哈希不符：{artifact_hash}")


class ArtifactStore(Protocol):
    """工件存储窄接口（实现类：S3ArtifactStore / LocalArtifactStore）。"""

    def put(self, content: bytes) -> str:
        """写入内容，返回 BLAKE3 十六进制哈希（64 字符）；幂等去重。"""
        ...

    def get(self, artifact_hash: str) -> bytes:
        """按哈希读取；未命中 ArtifactNotFoundError，内容不符 ArtifactCorruptedError。"""
        ...

    def exists(self, artifact_hash: str) -> bool:
        """按哈希探测存在性。"""
        ...


class LocalArtifactStore:
    """本地目录实现（仅测试用）：文件名为哈希，原子写入。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_hash: str) -> Path:
        _require_hash(artifact_hash)
        return self._root / artifact_hash

    def put(self, content: bytes) -> str:
        artifact_hash = content_hash(content)
        target = self._root / artifact_hash
        if not target.exists():  # 幂等去重：已存在则零写入
            tmp = self._root / f".{artifact_hash}.tmp"
            tmp.write_bytes(content)
            tmp.replace(target)  # 原子落盘
        return artifact_hash

    def get(self, artifact_hash: str) -> bytes:
        target = self._path(artifact_hash)
        if not target.exists():
            raise ArtifactNotFoundError(f"工件不存在：{artifact_hash}")
        content = target.read_bytes()
        _verify(artifact_hash, content)
        return content

    def exists(self, artifact_hash: str) -> bool:
        return self._path(artifact_hash).exists()


class S3ArtifactStore:
    """S3 兼容对象存储实现（boto3）：key = 前缀 + BLAKE3 哈希。"""

    def __init__(self, client, bucket: str, prefix: str = "") -> None:
        # client 由装配层注入；评估器与沙箱不得持有对象存储凭证（宪章原则四预留）
        self._client = client
        self._bucket = bucket
        self._prefix = prefix

    def _key(self, artifact_hash: str) -> str:
        _require_hash(artifact_hash)
        return f"{self._prefix}{artifact_hash}"

    def put(self, content: bytes) -> str:
        artifact_hash = content_hash(content)
        key = f"{self._prefix}{artifact_hash}"
        if self._head_hit(key):
            return artifact_hash  # 幂等去重：head 命中即零写入
        self._client.put_object(Bucket=self._bucket, Key=key, Body=content)
        return artifact_hash

    def get(self, artifact_hash: str) -> bytes:
        from botocore.exceptions import ClientError

        key = self._key(artifact_hash)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                raise ArtifactNotFoundError(f"工件不存在：{artifact_hash}") from exc
            raise
        content = response["Body"].read()
        _verify(artifact_hash, content)
        return content

    def exists(self, artifact_hash: str) -> bool:
        return self._head_hit(self._key(artifact_hash))

    def _head_hit(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True
