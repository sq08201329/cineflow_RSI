"""功能 015 US3（T1521）：`agents/pilot/package.py` 样片包装配测试（契约 C11/C12）。

覆盖：五件套齐备、manifest **"模拟生成"标注** + 形态 + 配置指纹 + 产物清单 + 阶段状态、
缺件即装配失败（不产半包）、账目对账零差异（篡改即报错）、成本载荷用 `ledger_payload`。
"""

import json

import pytest

from agents.pilot.package import (
    PACKAGE_FILES,
    SIMULATED_NOTE,
    PackageError,
    assemble_package,
    load_manifest,
    verify_package,
)
from agents.pilot.pilot import PilotInputs, run_pilot

FORM = "shortdrama"


def _inputs() -> PilotInputs:
    return PilotInputs(
        topic="夜班记录",
        target_duration_min=2,
        characters=("林静", "陈默"),
        constraints=(),
    )


@pytest.fixture()
def package_dir(pilot_form_config_path, pilot_dirs, tmp_path):
    result = run_pilot(
        form=FORM,
        config_path=pilot_form_config_path(FORM),
        inputs=_inputs(),
        data_dir=pilot_dirs,
        artifacts_root=tmp_path / "artifacts",
        run_id="run-pkg",
        clock=lambda: "2026-09-21T00:00:00+00:00",
    )
    return result.package_dir


class Test五件套:
    def test_五件齐备(self, package_dir):
        for name in PACKAGE_FILES:
            assert (package_dir / name).is_file(), name

    def test_清单标注模拟生成(self, package_dir):
        manifest = load_manifest(package_dir)
        assert SIMULATED_NOTE in manifest["note"]
        assert "模拟生成" in manifest["note"]
        assert manifest["form"] == FORM
        assert manifest["config_fingerprint"]
        assert manifest["products"]
        assert [stage["stage_id"] for stage in manifest["stages"]] == [
            "script",
            "storyboard",
            "visual",
            "sound",
            "editing",
            "promo",
        ]

    def test_成片与产物引用齐备(self, package_dir):
        assert (package_dir / "reel.mp4").stat().st_size > 0
        products = json.loads((package_dir / "products.json").read_text(encoding="utf-8"))
        assert products["reel"]["content_hash"]
        assert [item["stage_id"] for item in products["by_stage"]] == [
            "script",
            "storyboard",
            "visual",
            "sound",
            "editing",
            "promo",
        ]

    def test_状态快照含评分与坍缩漂移摘要(self, package_dir):
        state = json.loads((package_dir / "state.json").read_text(encoding="utf-8"))
        assert state["note"]
        assert "scores" in state and "collapse" in state and "drift" in state

    def test_成本载荷对账零差异(self, package_dir):
        cost = json.loads((package_dir / "cost.json").read_text(encoding="utf-8"))
        assert cost["reconciled"] is True
        assert cost["total_usd"] == pytest.approx(sum(cost["by_stage"].values()))
        assert all(line["delta_usd"] == 0.0 for line in cost["lines"])


class Test拒绝语义:
    def test_缺件即装配失败(self, package_dir):
        (package_dir / "cost.json").unlink()
        with pytest.raises(PackageError):
            verify_package(package_dir)

    def test_缺成片即装配失败(self, tmp_path, package_dir):
        (package_dir / "reel.mp4").unlink()
        with pytest.raises(PackageError):
            verify_package(package_dir)

    def test_篡改账目即报错(self, pilot_dirs, tmp_path, package_dir):
        cost_path = package_dir / "cost.json"
        payload = json.loads(cost_path.read_text(encoding="utf-8"))
        payload["by_stage"]["visual"] = payload["by_stage"]["visual"] + 10.0
        tampered = tmp_path / "tampered" / "cost.json"
        tampered.parent.mkdir(parents=True, exist_ok=True)
        tampered.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(PackageError):
            verify_package(package_dir, cost_override=tampered)

    def test_装配前缺件即拒(self, pilot_dirs, tmp_path):
        with pytest.raises(PackageError):
            assemble_package(
                run_id="run-missing",
                package_root=tmp_path / "packages",
                manifest={"note": SIMULATED_NOTE},
                reel=None,
                products={},
                cost={},
                state={},
            )
