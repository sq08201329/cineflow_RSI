"""漂移口径版本化与只读审计单测（功能 012 US1 / T1111，先于实现编写；契约 C2）。

- 口径版本 `detector_version = drift_detector@1.0.0+{算法+阈值哈希}`：同一口径同输入
  → 记录逐字节一致；判定口径变更（阈值/窗口/分桶/样本下限/检测范围）→ 新版本；
  门禁与报表参数（降权系数/排除开关/双信号规则）不改变检测口径；
- 口径升级不作回溯改写：历史记录保持原样，已存在的同周期记录内容不同即拒绝改写；
- 只读审计（SC-005）：树零写入、评估器零变更、生成/LLM 调用计数 0、
  010 产物目录（snapshots/ledger/reports）零写入（只新增本特性自己的 drift/metrics）。
"""

import re
from dataclasses import replace
from pathlib import Path

import pytest

from core.calibration.drift_metrics import (
    DETECTOR_SEMVER,
    detect_drift,
    detector_version,
    metric_hash,
    record_path,
)
from core.calibration.errors import DriftRecordConflictError

REPO_ROOT = Path(__file__).resolve().parents[2]
_KEY = "judge.cinematic@1.0.0"
_AGENT = "visual"
_CURRENT = "2026-W39"


def _fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    """目录指纹：相对路径 →（字节数，mtime_ns）；不存在 → 空。"""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _bytes(data_dir, period=_CURRENT, key=_KEY) -> bytes:
    return record_path(data_dir, _AGENT, key, period).read_bytes()


class Test口径版本:
    def test_版本形态与稳定性(self, drift_config):
        version = detector_version(drift_config)
        assert re.fullmatch(rf"drift_detector@{DETECTOR_SEMVER}\+[0-9a-f]{{12}}", version)
        assert version == detector_version(drift_config)  # 同口径稳定（无随机源）
        assert version.endswith(metric_hash(drift_config)[:12])  # 版本尾段 = 口径哈希前 12 位

    @pytest.mark.parametrize(
        "change",
        [
            {"psi_threshold": 0.25},
            {"quantile_threshold": 0.2},
            {"window": 4},
            {"buckets": 5},
            {"min_samples": 5},
            {"scope_kinds": ("judge", "proxy")},
        ],
    )
    def test_判定口径变更即新版本(self, drift_config, change):
        assert detector_version(replace(drift_config, **change)) != detector_version(drift_config)

    @pytest.mark.parametrize(
        "change",
        [
            {"suspect_weight": 0.3},  # 门禁降权系数
            {"confirmed_exclude": False},  # 门禁排除开关
        ],
    )
    def test_门禁参数不改变检测口径(self, drift_config, change):
        assert detector_version(replace(drift_config, **change)) == detector_version(drift_config)

    def test_双信号规则不改变检测口径(self, drift_config):
        rule = replace(drift_config.double_signal, escalated_level="blocker")
        assert detector_version(replace(drift_config, double_signal=rule)) == detector_version(
            drift_config
        )

    def test_记录携带口径版本(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        assert metrics.detector_version == detector_version(drift_config)


class TestC2场景1_同输入同口径一致:
    def test_重复检测幂等且逐字节一致(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)
        detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        path = record_path(drift_data_dir, _AGENT, _KEY, _CURRENT)
        first, mtime = path.read_bytes(), path.stat().st_mtime_ns

        metrics = detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)  # 幂等
        assert path.read_bytes() == first  # 逐字节一致（含 detector_version）
        assert path.stat().st_mtime_ns == mtime  # 相同内容不重写
        assert metrics.detector_version == detector_version(drift_config)

    def test_两处数据目录同输入逐字节一致(
        self, tmp_path, drift_data_dir, drift_config, drift_sequence_writer
    ):
        other = tmp_path / "calibration-other"
        for target in (drift_data_dir, other):
            drift_sequence_writer(
                "variance_widen", agent_id=_AGENT, evaluator_key=_KEY, data_dir=target
            )
            detect_drift(_AGENT, _KEY, _CURRENT, drift_config, target)
        assert _bytes(drift_data_dir) == _bytes(other)  # 无隐藏状态（可重放）


class TestC2场景2_口径升级不回溯:
    def test_口径升级新版本且历史不改写(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        detect_drift(_AGENT, _KEY, "2026-W38", drift_config, drift_data_dir)
        historical = _bytes(drift_data_dir, period="2026-W38")

        upgraded = replace(drift_config, psi_threshold=0.05)  # 判定口径升级
        metrics = detect_drift(_AGENT, _KEY, _CURRENT, upgraded, drift_data_dir)
        assert metrics.detector_version == detector_version(upgraded)
        assert metrics.detector_version != detector_version(drift_config)
        assert _bytes(drift_data_dir, period="2026-W38") == historical  # 历史记录不被改写

    def test_同周期异口径拒绝改写(self, drift_data_dir, drift_config, drift_sequence_writer):
        drift_sequence_writer("stable", agent_id=_AGENT, evaluator_key=_KEY)
        detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)
        historical = _bytes(drift_data_dir)

        upgraded = replace(drift_config, psi_threshold=0.05)
        with pytest.raises(DriftRecordConflictError, match="不可改写"):
            detect_drift(_AGENT, _KEY, _CURRENT, upgraded, drift_data_dir)
        assert _bytes(drift_data_dir) == historical  # 拒绝后原记录保持原样


class TestC2场景3_只读审计:
    def test_检测全程只读(
        self,
        drift_data_dir,
        drift_config,
        drift_sequence_writer,
        tree_store,
        build_calibration_tree,
        mock_gateway,
    ):
        tree, _ = build_calibration_tree([(0.5, {"judge.cinematic@1.0.0": {"score": 0.6}})])
        drift_sequence_writer("mean_shift", agent_id=_AGENT, evaluator_key=_KEY)

        nodes_before = len(tree_store.nodes_of(tree.tree_id))
        data_before = _fingerprint(drift_data_dir)
        source_dirs = (
            REPO_ROOT / "core" / "evaluators",
            REPO_ROOT / "policies",
            REPO_ROOT / "configs",
            REPO_ROOT / "calibration",  # 仓库自身数据目录（测试不得污染）
        )
        sources_before = {path: _fingerprint(path) for path in source_dirs}

        detect_drift(_AGENT, _KEY, _CURRENT, drift_config, drift_data_dir)

        # ① 树零写入
        assert len(tree_store.nodes_of(tree.tree_id)) == nodes_before
        # ② 评估器/配置/仓库数据零变更
        for path in source_dirs:
            assert _fingerprint(path) == sources_before[path]
        # ③ 零生成 / 零 LLM 调用（网关账本为空）
        assert mock_gateway.call_count == 0
        assert mock_gateway.total_cost_usd == 0.0
        # ④ 010 产物零写入：新增/变更仅限本特性自己的 drift/metrics
        data_after = _fingerprint(drift_data_dir)
        changed = {name for name, meta in data_after.items() if data_before.get(name) != meta}
        assert changed, "检测必须落盘记录"
        assert all(name.startswith("drift/metrics/") for name in changed), changed
        assert data_before.keys() <= data_after.keys()  # 010 产物只增不改（未删改）
