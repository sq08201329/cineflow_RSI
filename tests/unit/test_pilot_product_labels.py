"""样片包产物标签与内容类型一致性测试（015 收口：标签漂移防回归）。

背景缺陷：分镜阶段把节点的 `artifact_hash`（实际是**预演 mp4**）标成 `kind="shotlist"`——
ShotList JSON 只留在 `detail["shotlist"]` 里。清单是给评审看的证据载体，标签错会误导
（评审按 "shotlist" 去找结构化镜头表，拿到的是视频）。

本文件守住两件事：

1. **整包逐项机检**：每个产物的 `kind` 与其内容寻址字节的**实际类型**一致
   （mp4→animatic/clip/reel，wav→audio，json→script/shotlist/material）；
2. **标签不符即拒绝装配**：`check_product_kinds` 对不上就抛 `PackageError`（不产自相矛盾的包）；
3. 分镜阶段必须**同时**有 `animatic`（预演 mp4）与 `shotlist`（内容寻址的 ShotList JSON）两项。
"""

import json
import struct
from pathlib import Path

import pytest

from agents.pilot.package import PackageError, check_product_kinds
from agents.pilot.pilot import PilotInputs, run_pilot
from agents.storyboard.shotlist import ShotList
from core.orchestration.models import (
    ProductRef,
    RunRecord,
    RunStatus,
    StageState,
    StageStatus,
)
from core.tree.artifacts import LocalArtifactStore

FORM = "shortdrama"
REPO_ROOT = Path(__file__).resolve().parents[2]
# 期望对照（测试侧独立声明，不复用实现里的表：口径分叉才会被测出来）
_EXPECTED_CONTENT = {
    "animatic": "video",
    "clip": "video",
    "reel": "video",
    "audio": "audio",
    "script": "json",
    "shotlist": "json",
    "material": "json",
}


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=2,
        characters=("林静", "陈默"),
        constraints=("单场景为主",),
    )


def _sniff(content: bytes) -> str:
    """按字节自描述识别内容类型（测试侧独立实现，不借用被测代码）。"""
    if len(content) < 12:
        return "too_short"
    if content[4:8] == b"ftyp":  # ISO BMFF / mp4
        return "video"
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "audio"
    if content.lstrip()[:1] in (b"{", b"["):
        return "json"
    return "unknown"


@pytest.fixture(scope="module")
def pilot_run(tmp_path_factory):
    """一次真实试水运行（整包校验用例共用，避免重复跑链路）。"""
    tmp_path = tmp_path_factory.mktemp("product-labels")
    config_path = tmp_path / "configs" / "shortdrama-demo.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    source = (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
    config_path.write_text(
        source.replace("target_duration_s: 120", "target_duration_s: 30"), encoding="utf-8"
    )
    artifacts_root = tmp_path / "artifacts"
    result = run_pilot(
        form=FORM,
        config_path=config_path,
        inputs=_inputs(),
        data_dir=tmp_path / "pilot",
        artifacts_root=artifacts_root,
        run_id="run-labels",
        clock=lambda: "2026-09-21T00:00:00+00:00",
    )
    assert result.record.status.value == "done"
    return result, artifacts_root


def test_分镜阶段产物标签与内容类型一致(pilot_run):
    """预演 mp4 → animatic；ShotList JSON → shotlist（两项并存，各自引用真实内容）。"""
    result, artifacts_root = pilot_run
    products = result.record.stage("storyboard").products
    by_kind = {product.kind: product for product in products}
    assert sorted(by_kind) == ["animatic", "shotlist"]  # 不再把 mp4 标成 shotlist

    animatic = by_kind["animatic"]
    assert _sniff((artifacts_root / animatic.content_hash).read_bytes()) == "video"
    assert animatic.content_hash == result.record.stage("storyboard").detail["artifact_hash"]

    shotlist = by_kind["shotlist"]
    payload = json.loads((artifacts_root / shotlist.content_hash).read_bytes())
    assert _sniff((artifacts_root / shotlist.content_hash).read_bytes()) == "json"
    assert payload == result.record.stage("storyboard").detail["shotlist"]  # 内容寻址 = 该清单
    parsed = ShotList.from_dict(payload)
    assert len(parsed.shots) == result.record.stage("storyboard").detail["shot_count"]


def test_整包每个产物_kind_与内容类型一致(pilot_run):
    """全包逐项校验（防同类标签漂移：任一 kind 与字节类型不符即失败）。"""
    result, artifacts_root = pilot_run
    checked = 0
    for state in result.record.stages:
        for product in state.products:
            expected = _EXPECTED_CONTENT[product.kind]
            actual = _sniff((artifacts_root / product.content_hash).read_bytes())
            assert actual == expected, f"{state.stage_id}/{product.kind} 内容类型为 {actual}"
            checked += 1
    assert checked >= 6  # 六阶段全部有产物（脚本+分镜2+视觉+声音+剪辑+宣发）
    # 实现自带的整包校验与测试侧口径一致（同一 store 上执行，不重复读字节）
    check_product_kinds(result.record, LocalArtifactStore(artifacts_root))


def _record_with(kind: str, content_hash: str) -> RunRecord:
    """单阶段完成记录（探针用：只关心产物引用，不跑链路）。"""
    return RunRecord(
        run_id="probe",
        form=FORM,
        config_fingerprint="a" * 64,
        input_fingerprint="b" * 64,
        stages=(
            StageState(
                stage_id="storyboard",
                status=StageStatus.DONE,
                input_fingerprint="c" * 64,
                products=(ProductRef(kind=kind, ref=content_hash, content_hash=content_hash),),
                started_at="2026-09-21T00:00:00+00:00",
                finished_at="2026-09-21T00:00:00+00:00",
            ),
        ),
        started_at="2026-09-21T00:00:00+00:00",
        status=RunStatus.DONE,
        finished_at="2026-09-21T00:00:00+00:00",
    )


def _mp4_bytes() -> bytes:
    # 最小 ISO BMFF 头（仅用于类型识别；不要求可播放）
    return b"\x00\x00\x00 ftypisom" + b"\x00" * 32


def test_标签与内容不符即拒绝装配(tmp_path):
    """mp4 标成 shotlist → 装配拒绝（缺陷原样复现即红）。"""
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    video_hash = artifacts.put(_mp4_bytes())
    with pytest.raises(PackageError) as excinfo:
        check_product_kinds(_record_with("shotlist", video_hash), artifacts)
    assert "shotlist" in str(excinfo.value) and "video" in str(excinfo.value)

    json_hash = artifacts.put(json.dumps({"shots": []}).encode())
    check_product_kinds(_record_with("shotlist", json_hash), artifacts)  # 类型相符 → 通过


def test_未登记_kind_即拒绝(tmp_path):
    """新增 kind 必须同时声明内容类型（不静默放行未知标签）。"""
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    with pytest.raises(PackageError) as excinfo:
        check_product_kinds(_record_with("mystery", artifacts.put(b"{}")), artifacts)
    assert "mystery" in str(excinfo.value)


def test_内容类型识别覆盖包内各类字节():
    """识别口径自检：mp4/wav/json 三类字节各归其类（含 struct 打包的 ftyp 变体）。"""
    assert _sniff(_mp4_bytes()) == "video"
    assert _sniff(b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt ") == "audio"
    assert _sniff(b'  {"shots": []}') == "json"
    assert _sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) != "json"
