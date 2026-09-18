# 契约：视频生成适配器（VideoGenAdapter）

**模块**: `agents/visual/platform/` | **实现**: `SimulatedVideoGen`（确定性，开发/CI 默认）、
`HttpRealVideoGen`（真实生成 API 骨架，凭证经环境变量注入）

## 1. 接口

```python
class VideoGenAdapter(Protocol):
    def submit(self, gen_params: dict) -> GenJob: ...
    def get_status(self, external_id: str) -> GenJobStatus: ...
    def fetch_artifact(self, external_id: str) -> bytes: ...   # mp4 字节流
    def cancel(self, external_id: str) -> None: ...
```

## 2. 语义契约

| 规则 | 行为 |
| --- | --- |
| 花费 | `submit` 返回的 GenJob 含预估花费；`fetch_artifact` 时实际扣费入账，实际 ≤ 预估 |
| 幂等 | `submit` 携带客户端幂等键（round_id+params_hash）；重复提交返回同一任务 |
| 工件 | `fetch_artifact` 只在 `completed` 后可用；返回完整 mp4 字节 |
| 错误 | 平台错误统一映射 `VideoGenError`（`rate_limited`/`unavailable`/`invalid_params` 子类） |
| 取消 | `cancel` 幂等；对已完成任务为无操作 |

## 3. 契约测试（tests/contract/test_video_gen_adapter.py）

同一套契约用例对两个实现各跑一遍：预估/实际花费、幂等键、状态机推进、工件可解码性
（ffprobe 可读）、错误映射。真实实现无凭证跳过其用例但保留套件；模拟实现必须全过。

## 4. 模拟实现确定性（research 决策 2）

`SimulatedVideoGen`：`blake3(规范化 gen_params)` 为种子 → numpy 程序化帧（渐变/纹理/
运动主体块，种子派生亮度/色彩/运动幅度/噪声参数）→ imageio-ffmpeg 编码 mp4。
同参数**逐字节相同**；内部账本记录扣费供对账；分布参数进 configs。
