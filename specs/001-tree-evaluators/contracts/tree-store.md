# 契约：TreeStore（发现树存储与谱系查询）

**模块**: `core/tree/store.py` | **消费方**: 回放模拟器、生产 Agent、做梦层、ops 审计脚本

公开接口（Protocol，实现类对外部不可见）：

```python
class TreeStore(Protocol):
    def create_tree(self, tree: DiscoveryTree) -> None: ...
    def append_node(self, node: TreeNode) -> None: ...
    def get_node(self, node_id: str) -> TreeNode: ...
    def children(self, node_id: str) -> list[TreeNode]: ...
    def trees_by(self, *, project_id: str | None = None,
                 agent_id: str | None = None,
                 policy_version: str | None = None) -> list[DiscoveryTree]: ...
    def nodes_of(self, tree_id: str) -> list[TreeNode]: ...
```

## 语义契约

| 操作 | 前置条件 | 成功保证 | 失败语义 |
| --- | --- | --- | --- |
| `create_tree` | tree_id 唯一；config_snapshot 非空 | 树结构落盘、快照冻结 | `DuplicateError`（tree_id 冲突）；`ValidationError`（快照为空） |
| `append_node` | 所属树已存在；parent 同树存在（根除外）；status ∈ {evaluated, failed}；eval_breakdown 键均为 `evaluator_id@version` | 单条 INSERT 原子落盘；绝不修改既有行 | `ValidationError`（任一校验失败）；`DuplicateError`（node_id 冲突） |
| `get_node` | — | 返回完整节点 | `NotFoundError`（不存在） |
| `children` | — | 按 created_at 升序返回 | 空列表（无子节点或节点不存在） |
| `trees_by` | 至少一个过滤条件非 None | 仅返回全匹配的树 | 空列表 |
| `nodes_of` | — | 返回该树全部节点（按 depth、created_at 排序） | 空列表（树不存在） |

## 不变量（调用方可依赖）

1. 任何读取结果与写入时刻逐字节一致（immutable，存储层强制）；
2. `children(parent)` 返回的节点 depth 恒等于 `get_node(parent).depth + 1`；
3. `score is None` 当且仅当 `status == FAILED`；
4. 本接口**不存在**任何更新/删除方法——调用方在类型层面就无法发起变更。

## 错误类型

`core.tree.errors`：`TreeStoreError`（基类）→ `ValidationError` / `DuplicateError` / `NotFoundError`。
存储层触发器异常一律转换为 `ImmutableViolationError`（TreeStoreError 子类）后上抛。
