"""程序化音频合成（research 决策 2，确定性模拟生成的波形底座）。

参数种子 → numpy 正弦叠加（基频由种子微调、谐波数/时长由参数决定、线性包络）
→ 标准库 wave 写 PCM16 单声道。同参数逐字节复现（SC-002，定点运算纪律同 004）；
声学属性注入标记（loudness_gain_db/event_times_ms/cer_injected/emotion_vector）
随元数据字典返回，供四评估器夹具注入与两段式落盘携带。
"""

import io
import wave

import numpy as np


def synthesize_wav(gen_params: dict, distribution: dict, sample_rate: int) -> tuple[bytes, dict]:
    """合成 PCM16 wav 字节与声学属性元数据。

    gen_params：声学属性可控参数（seed/duration_s/loudness_gain_db/event_times_ms/
    cer_injected/emotion_vector，见 conftest 参数工厂）；
    distribution：sound.simulated_gen 段（base_freq_hz/harmonics/duration_seconds）。
    返回 (wav_bytes, metadata)；除 loudness_gain_db 外的注入属性只进元数据、
    不改波形（错字率/情绪向量是评估信号标记，非声学特征）。
    """
    seed = int(gen_params.get("seed", 0))
    duration_s = float(gen_params.get("duration_s", distribution["duration_seconds"]))
    base_freq = float(distribution["base_freq_hz"]) + (seed % 40) - 20.0  # 种子微调基频
    harmonics = int(distribution["harmonics"])
    gain_db = float(gen_params.get("loudness_gain_db", 0.0))

    n = int(round(duration_s * sample_rate))
    t = np.arange(n, dtype=np.float64) / sample_rate
    # 正弦叠加：谐波幅度随次数 1/h 衰减（音色维度由 harmonics 控制）
    signal = np.zeros(n, dtype=np.float64)
    for h in range(1, harmonics + 1):
        signal += np.sin(2.0 * np.pi * base_freq * h * t) / h
    # 线性包络：10% 起音 + 20% 释音（确定性，无随机流）
    attack = max(1, n // 10)
    release = max(1, n // 5)
    envelope = np.ones(n, dtype=np.float64)
    envelope[:attack] = np.linspace(0.0, 1.0, attack)
    envelope[-release:] = np.linspace(1.0, 0.0, release)
    signal *= envelope
    # 归一化到固定幅值后施加注入响度增益（可测：RMS 比值 = 10^(gain/20)）
    signal /= np.abs(signal).max()
    signal *= 0.5 * 10.0 ** (gain_db / 20.0)
    pcm = (np.clip(signal, -1.0, 1.0) * 32767.0).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    metadata = {
        "duration_s": duration_s,
        "sample_rate": sample_rate,
        "channels": 1,
        "loudness_gain_db": gain_db,
        "event_times_ms": list(gen_params.get("event_times_ms", [])),
        "cer_injected": float(gen_params.get("cer_injected", 0.0)),
        "emotion_vector": list(gen_params.get("emotion_vector", [])),
    }
    return buffer.getvalue(), metadata
