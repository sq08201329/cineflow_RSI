# 契约：生成适配器（三类型 × 双实现）

> 对应规格 US1 / FR-002。实现：`agents/sound/platform/`。

## C9 适配器协议

```python
class SoundGenAdapter(Protocol):
    gen_type: str  # "tts" | "sfx" | "music"
    def estimate(self, params: SoundGenParams) -> float: ...      # 预估成本（USD）
    def generate(self, params: SoundGenParams) -> GeneratedAudio: ...  # wav bytes + 元数据 + 实际成本
```

- 错误分型：`SoundGenError` / `RateLimitedError` / `UnavailableError`（004 platform/base 同款）
- 元数据必须含声学属性注入标记（loudness_gain / event_times / cer_injected / 情绪向量）——
  评估器的确定性输入

## C10 确定性模拟器（`simulated.py`，三类型）

- 参数种子 → numpy 波形（基频/谐波/包络由参数决定）→ PCM16 wav（标准库 wave）
- 同参数两次 generate → 字节完全一致（SC-002）；estimated ≥ actual（004 纪律）

## C11 真实骨架（`http_real.py`，三类型）

- HTTP 端点/凭证经环境变量（`SOUND_TTS_*` / `SOUND_SFX_*` / `SOUND_MUSIC_*`）；
  无凭证构造即报"未配置"，契约用例按 skip 语义处理（003/004 同款）

## C12 契约套件

`tests/contract/test_sound_platform_contract.py`：对三类型 × 双实现同构执行——
estimate ≤ 实际语义、工件 wav 可解析（采样率/声道符合配置）、元数据键齐全、
错误分型正确；模拟全过、真实无凭证跳过。

### 场景

1. TTS 模拟器同参两次 → 字节一致、元数据 cer_injected 与种子一致
2. SFX/music 模拟器 → 事件时间/情绪向量元数据齐全
3. 真实适配器无凭证 → 契约用例 skip（不报错不假装）
4. 模拟器 estimate $0.5、actual $0.5 → estimated ≥ actual 成立
