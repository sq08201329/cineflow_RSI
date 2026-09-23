"""ArtifactStore 契约单测（US1 / T013）。

覆盖 contracts/artifact-store.md：内容寻址、幂等去重（PUT-if-absent）、
未命中 ArtifactNotFoundError、内容不符 ArtifactCorruptedError。
LocalArtifactStore 用临时目录；S3ArtifactStore 用内存桩 client（不触真实服务）。
"""

import blake3
import pytest

from core.tree.artifacts import S3ArtifactStore
from core.tree.errors import (
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ValidationError,
)

CONTENT = b"fake-video-bytes-\x00\x01"
CONTENT_HASH = blake3.blake3(CONTENT).hexdigest()


class _FakeS3Client:
    """内存版 S3 client 桩：实现 head/put/get_object 的最小语义。"""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_count = 0

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def put_object(self, Bucket, Key, Body):
        self.put_count += 1
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.objects[(Bucket, Key)])}


class TestLocalArtifactStore:
    def test_put_返回_blake3_哈希(self, artifact_store):
        assert artifact_store.put(CONTENT) == CONTENT_HASH
        assert len(CONTENT_HASH) == 64

    def test_put_get_往返一致(self, artifact_store):
        h = artifact_store.put(CONTENT)
        assert artifact_store.get(h) == CONTENT

    def test_put_幂等去重(self, artifact_store, tmp_path):
        """相同内容二次 put：返回同一 hash，且不产生第二个对象（FR-005/SC-006）。"""
        h1 = artifact_store.put(CONTENT)
        h2 = artifact_store.put(CONTENT)
        assert h1 == h2
        assert len(list(tmp_path.rglob("*"))) == 2  # 目录本身 + 唯一对象文件

    def test_get_未命中报_ArtifactNotFoundError(self, artifact_store):
        with pytest.raises(ArtifactNotFoundError):
            artifact_store.get(CONTENT_HASH)

    def test_get_内容被篡改报_ArtifactCorruptedError(self, artifact_store):
        h = artifact_store.put(CONTENT)
        target = next(tmp for tmp in artifact_store._root.iterdir() if tmp.is_file())
        target.write_bytes(b"tampered")
        with pytest.raises(ArtifactCorruptedError):
            artifact_store.get(h)

    @pytest.mark.parametrize("bad_hash", ["../etc/passwd", "not-hex", "ab" * 31, "ZZ" * 32])
    def test_get_哈希格式非法报_ValidationError(self, artifact_store, bad_hash):
        """哈希驱动读取：非法格式直接拒绝，杜绝路径穿越与枚举。"""
        with pytest.raises(ValidationError):
            artifact_store.get(bad_hash)

    def test_exists(self, artifact_store):
        assert not artifact_store.exists(CONTENT_HASH)
        artifact_store.put(CONTENT)
        assert artifact_store.exists(CONTENT_HASH)

    def test_exists_非法哈希报_ValidationError(self, artifact_store):
        with pytest.raises(ValidationError):
            artifact_store.exists("not-a-hash")


class TestS3ArtifactStore:
    @pytest.fixture()
    def s3_store(self):
        return S3ArtifactStore(client=_FakeS3Client(), bucket="test-bucket", prefix="pfx/")

    def test_put_返回_blake3_哈希且_key_带前缀(self, s3_store):
        h = s3_store.put(CONTENT)
        assert h == CONTENT_HASH
        assert ("test-bucket", f"pfx/{CONTENT_HASH}") in s3_store._client.objects

    def test_put_幂等去重_零写入(self, s3_store):
        s3_store.put(CONTENT)
        assert s3_store._client.put_count == 1
        assert s3_store.put(CONTENT) == CONTENT_HASH
        assert s3_store._client.put_count == 1  # 第二次 put 命中 head，零写入

    def test_get_往返一致(self, s3_store):
        h = s3_store.put(CONTENT)
        assert s3_store.get(h) == CONTENT

    def test_get_未命中报_ArtifactNotFoundError(self, s3_store):
        with pytest.raises(ArtifactNotFoundError):
            s3_store.get(CONTENT_HASH)

    def test_get_内容不符报_ArtifactCorruptedError(self, s3_store):
        h = s3_store.put(CONTENT)
        s3_store._client.objects[("test-bucket", f"pfx/{h}")] = b"tampered"
        with pytest.raises(ArtifactCorruptedError):
            s3_store.get(h)

    def test_exists(self, s3_store):
        assert not s3_store.exists(CONTENT_HASH)
        s3_store.put(CONTENT)
        assert s3_store.exists(CONTENT_HASH)

    def test_哈希格式非法报_ValidationError(self, s3_store):
        with pytest.raises(ValidationError):
            s3_store.get("bad")
        with pytest.raises(ValidationError):
            s3_store.exists("bad")


def test_非字符串哈希归_ValidationError(tmp_path):
    """纵深防御：`artifacts.get(None)` 必须给 ValidationError（带实际值），
    而不是裸 `TypeError: expected string or bytes-like object, got 'NoneType'`
    ——真实故障里这种消息落进运行记录后看不出任何业务信息。"""
    from core.tree.artifacts import LocalArtifactStore
    from core.tree.errors import ValidationError

    store = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValidationError) as excinfo:
        store.get(None)
    assert "artifact_hash" in str(excinfo.value) and "None" in str(excinfo.value)
    with pytest.raises(ValidationError):
        store.exists("")
