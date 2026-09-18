# 契约：ArtifactStore（工件内容寻址存储）

**模块**: `core/tree/artifacts.py` | **消费方**: 生产 Agent、回放模拟器（读）、ops 审计

```python
class ArtifactStore(Protocol):
    def put(self, content: bytes) -> str:
        """返回 BLAKE3 十六进制哈希（64 字符）。"""
    def get(self, artifact_hash: str) -> bytes: ...
    def exists(self, artifact_hash: str) -> bool: ...
```

## 语义契约

| 规则 | 行为 |
| --- | --- |
| 内容寻址 | key 恒等于 `blake3(content).hexdigest()`；调用方传入的 hash 与内容不符时 `get` 抛 `ArtifactCorruptedError` |
| 幂等去重 | `put` 相同内容两次：第二次零写入、返回同一 hash（PUT-if-absent，FR-005 / SC-006） |
| 不可变 | 接口不存在删除/覆盖方法；底层对象锁交由运维配置，不在本契约 |
| 未命中 | `get` 抛 `ArtifactNotFoundError` |
| 实现 | `S3ArtifactStore`（boto3，前缀可配）；`LocalArtifactStore`（目录，仅测试用） |

## 安全边界

- 本接口的客户端凭证仅存在于 `core/tree` 装配层；评估器与未来的策略沙箱**不得**获得
  对象存储凭证（宪章原则四预留，`hash_oracle` 对抗测试的前置条件）；
- `get` 由哈希驱动，不接受任意 key——杜绝以哈希为 oracle 的枚举（所有 key 均为内容派生）。
