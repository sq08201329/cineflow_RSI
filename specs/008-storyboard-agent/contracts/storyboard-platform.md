# 契约：预演渲染适配器与确定性合成

> 对应规格 US1 / FR-003。实现：`agents/storyboard/platform/`、`board_render.py`。

## C10 适配器协议

```python
class StoryboardRenderAdapter(Protocol):
    def estimate(self, shotlist: ShotList, cfg) -> float: ...   # 镜头数 × 价目
    def render(self, shotlist: ShotList, cfg) -> RenderedAnimatic: ...  # mp4 bytes + 元数据 + 实际成本
```

- 错误分型：`RenderError` / `RateLimitedError` / `UnavailableError`（既有惯例）
- 元数据：时长、镜头数、景别序列、临时音轨标记（评估器输入）

## C11 确定性模拟渲染器（`simulated.py` + `board_render.py`）

- ShotList → 每镜一张分镜卡（程序化构图表达景别/机位/运动 + 情绪色板注入）→ 拼接
  （可选临时音轨）→ mp4；**编码单线程确定性档**（007 同参数）
- 同 ShotList 两次 render → 字节完全一致（SC-002）；estimated ≥ actual
- **分镜卡帧口径即 C7 的评估输入**（同一函数产出，避免"评估看到的"与"渲染出的"两套帧）

## C12 真实骨架（`http_real.py`）

- `STORYBOARD_RENDER_*` 环境变量；无凭证构造即报未配置，契约用例 skip

## C13 契约套件

`tests/contract/test_storyboard_platform_contract.py`：双实现同构——estimate ≥ actual、
mp4 可探测（时长/帧率符合配置）、元数据键齐全、分镜卡帧数与镜头数一致、错误分型正确。

### 场景

1. 同 ShotList 两次 render → 字节一致、元数据景别序列与 ShotList 一致
2. 带临时音轨 → has_temp_audio=true
3. 分镜卡帧由 C11 同一函数产出（帧哈希进元数据，供 C7 校验来源一致）
4. 真实适配器无凭证 → skip 不报错
