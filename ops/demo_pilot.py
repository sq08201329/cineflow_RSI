#!/usr/bin/env python
"""端到端演示：短剧形态试水作品（功能 015 / T1525，quickstart 六步）。

六步（全模拟链路 + 临时目录 + 确定性时钟，退出码 0 = 六步全过）：

1. **配置完整性**：全部加载器逐个通过（缺项即拒绝启动，零成本零落树）；
2. **短剧运行**：六阶段按 DAG 执行 → 产出样片包五件套（含"模拟生成"标注）；
3. **可复现对照**：同 run_id + 同输入同配置、独立工件根跑两次 → 五件套逐字节一致；
4. **movie 对照（零代码切换）**：同一套阶段代码换一份形态配置跑通（形态差异全在配置）；
5. **断点续跑**：全部完成后再续跑 → 原记录原样返回（零重跑、零重复落盘）；
6. **拒绝语义**：输入不足启动前拒绝；输入变更后拒绝续跑。

**试水档等值派生**：为把演示运行控制在 CI 预算内（并规避本机 ffmpeg 长连编码抖动），
本脚本把两套形态配置**等值派生**为试水体量（成片 30s / 剧本 2 页）——形态差异
（权重/阈值/曲线/预算/规格）逐字保留；短剧真实配置的 16 镜上限由单测守护。
生产档取 `configs/*.yaml` 原值（短剧 120s、电影 120s/90 页）。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from agents.pilot.package import PACKAGE_FILES  # noqa: E402
from agents.pilot.pilot import (  # noqa: E402
    PilotInputs,
    PrecheckError,
    resume_pilot,
    run_pilot,
)

FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
INPUTS = PilotInputs(
    topic="夜班记录",
    target_duration_min=2,
    characters=("林静", "陈默"),
    constraints=("单场景为主",),
)


def _clock():
    return lambda: FIXED_TIMESTAMP


def _derive_pilot_scale(source: Path, target: Path) -> Path:
    """等值派生：只把**试水体量**（成片时长/剧本目标页数）压到演示档，其余逐字保留。"""
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["editing"]["target_duration_s"] = 30
    payload["screenplay"]["target_duration_min"] = 2
    payload["screenplay"]["page_tolerance"] = 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def main() -> int:
    started = time.perf_counter()
    report: dict = {"steps": {}, "ok": False}
    with tempfile.TemporaryDirectory(prefix="cineflow-pilot-demo-") as tmp:
        root = Path(tmp)
        short_cfg = _derive_pilot_scale(
            REPO_ROOT / "configs/shortdrama.yaml", root / "configs" / "shortdrama-demo.yaml"
        )
        movie_cfg = _derive_pilot_scale(
            REPO_ROOT / "configs" / "movie.yaml", root / "configs" / "movie-demo.yaml"
        )

        # ---- 步骤 1：配置完整性（全部加载器，缺项即拒绝启动）----
        from agents.pilot.pilot import precheck

        pre = precheck(
            form="shortdrama", config_path=short_cfg, inputs=INPUTS, data_dir=root / "pre"
        )
        report["steps"]["1_配置完整性"] = {
            "loaders": len(pre["loaders"]),
            "config_fingerprint": pre["config_fingerprint"],
            "ok": len(pre["loaders"]) == 18,  # 12 个配置类 + 6 个 Agent 权重
        }

        # ---- 步骤 2：短剧运行出样片包 ----
        first = run_pilot(
            form="shortdrama",
            config_path=short_cfg,
            inputs=INPUTS,
            data_dir=root / "a" / "pilot",
            artifacts_root=root / "a" / "artifacts",
            run_id="demo-run",
            clock=_clock(),
        )
        package = first.package_dir
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        report["steps"]["2_短剧运行出样片包"] = {
            "status": first.record.status.value,
            "stages": [state.stage_id for state in first.record.stages],
            "files": [name for name in PACKAGE_FILES if (package / name).is_file()],
            "total_cost_usd": first.record.total_cost_usd,
            "simulated_note": "模拟生成" in manifest["note"],
            "ok": first.record.status.value == "done"
            and all((package / name).is_file() for name in PACKAGE_FILES)
            and "模拟生成" in manifest["note"],
        }

        # ---- 步骤 3：可复现对照（同 run_id，独立工件根，两次逐字节一致）----
        second = run_pilot(
            form="shortdrama",
            config_path=short_cfg,
            inputs=INPUTS,
            data_dir=root / "b" / "pilot",
            artifacts_root=root / "b" / "artifacts",
            run_id="demo-run",
            clock=_clock(),
        )
        diffs = [
            name
            for name in PACKAGE_FILES
            if (package / name).read_bytes() != (second.package_dir / name).read_bytes()
        ]
        report["steps"]["3_可复现对照"] = {
            "files": list(PACKAGE_FILES),
            "byte_identical": not diffs,
            "ok": not diffs,
        }

        # ---- 步骤 4：movie 对照（同一套链代码换形态配置）----
        movie = run_pilot(
            form="movie",
            config_path=movie_cfg,
            inputs=INPUTS,
            data_dir=root / "movie" / "pilot",
            artifacts_root=root / "movie" / "artifacts",
            run_id="movie-run",
            clock=_clock(),
        )
        movie_manifest = json.loads(
            (movie.package_dir / "manifest.json").read_text(encoding="utf-8")
        )
        report["steps"]["4_movie对照零代码切换"] = {
            "form": movie_manifest["form"],
            "status": movie.record.status.value,
            "total_cost_usd": movie.record.total_cost_usd,
            "shortdrama_cost_usd": first.record.total_cost_usd,
            "ok": movie.record.status.value == "done" and movie_manifest["form"] == "movie",
        }

        # ---- 步骤 5：断点续跑（已完成后续跑幂等，零重跑）----
        resumed = resume_pilot(
            form="shortdrama",
            config_path=short_cfg,
            inputs=INPUTS,
            data_dir=root / "a" / "pilot",
            artifacts_root=root / "a" / "artifacts",
            run_id="demo-run",
            clock=_clock(),
        )
        attempts = [state.attempts for state in resumed.record.stages]
        report["steps"]["5_断点续跑幂等"] = {
            "same_record": resumed.record == first.record,
            "attempts": attempts,
            "ok": resumed.record == first.record and all(count == 1 for count in attempts),
        }

        # ---- 步骤 6：拒绝语义（启动前输入不足；输入变更后续跑拒绝）----
        rejected_start = False
        try:
            precheck(
                form="shortdrama",
                config_path=short_cfg,
                inputs=PilotInputs(topic="", target_duration_min=0, characters=()),
                data_dir=root / "pre",
            )
        except PrecheckError:
            rejected_start = True
        rejected_resume = False
        try:
            resume_pilot(
                form="shortdrama",
                config_path=short_cfg,
                inputs=PilotInputs(
                    topic="换一个题材",
                    target_duration_min=2,
                    characters=("林静", "陈默"),
                    constraints=(),
                ),
                data_dir=root / "a" / "pilot",
                artifacts_root=root / "a" / "artifacts",
                run_id="demo-run",
                clock=_clock(),
            )
        except Exception as exc:  # noqa: BLE001 - 演示只机检"确实拒绝了"
            rejected_resume = "指纹" in str(exc) or "续跑" in str(exc)
        report["steps"]["6_拒绝语义"] = {
            "precheck_rejected": rejected_start,
            "resume_rejected": rejected_resume,
            "ok": rejected_start and rejected_resume,
        }

        report["ok"] = all(step.get("ok") for step in report["steps"].values())
        report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
