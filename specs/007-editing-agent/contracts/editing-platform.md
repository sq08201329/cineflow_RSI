# 契约：渲染适配器与确定性合成

> 对应规格 US1 / FR-003。实现：`agents/editing/platform/`、`render.py`。

## C10 适配器协议

```python
class EditRenderAdapter(Protocol):
    def estimate(self, edl: EditDecisionList, shots: ShotLibrary) -> float: ...   # 预估 USD（时长 × 价目）
    def render(self, edl: EditDecisionList, shots: ShotLibrary) -> RenderedFilm: ...  # mp4 bytes + 元数据 + 实际成本
```

- 错误分型：`RenderError` / `RateLimitedError` / `UnavailableError`（既有惯例）
- RenderedFilm 元数据：总时长、镜头时长序列、转场序列、音轨标记（评估器输入）

## C11 确定性模拟渲染器（`simulated.py` + `render.py`）

- EDL + 素材帧（夹具/004 模拟帧）→ numpy 程序化拼接、叠化（定点 alpha 混合）、
  混音（定点增益叠加）→ mp4
- **编码固定单线程确定性档**（消除 004 x264 多线程 flake 根因，research 决策 2）；
  同 EDL 两次 render → 字节完全一致（SC-002）；estimated ≥ actual

## C12 真实骨架（`http_real.py`）

- HTTP 渲染服务端点/凭证经环境变量（`EDIT_RENDER_*`）；无凭证构造即报未配置，
  契约用例 skip（既有惯例）

## C13 契约套件

`tests/contract/test_editing_platform_contract.py`：双实现同构——estimate ≥ actual、
mp4 可探测（ffprobe 口径：时长/帧率符合配置）、元数据键齐全、错误分型正确；
模拟全过、真实 skip。

### 场景

1. 同 EDL 两次 render → 字节一致、元数据镜头时长序列与 EDL 一致
2. 叠化转场 → 元数据转场序列记录类型与时长
3. 带音轨 EDL → has_audio=true，音轨按时间戳摆放（定点混音）
4. 真实适配器无凭证 → skip 不报错
