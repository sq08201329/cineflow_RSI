#!/usr/bin/env python
"""端到端演示：短剧形态试水作品（功能 015 / T1525，quickstart 六步）。

六步（全模拟链路 + 临时目录 + 确定性时钟，退出码 0 = 六步全过）：

1. **配置完整性**：全部加载器逐个通过（缺项即拒绝启动，零成本零落树）；
2. **短剧运行**：环节按 DAG 执行 → 产出样片包五件套（含"模拟生成"标注）；
3. **可复现对照**：同 run_id + 同输入同配置、独立工件根跑两次 → 五件套逐字节一致；
4. **movie 对照（零代码切换）**：同一套阶段代码换一份形态配置跑通（形态差异全在配置）；
5. **断点续跑**：全部完成后再续跑 → 原记录原样返回（零重跑、零重复落盘）；
6. **拒绝语义**：输入不足启动前拒绝；输入变更后拒绝续跑。

**缩档只经形态配置**（功能 018 / FR-013、FR-014，契约 C10）：本脚本**不再改写体量键**
（成片时长/剧本目标/页数容差一律不动），短剧的**演示档（30 秒档）以 `pilot.rehearsal` 的
档位声明**落在配置副本里；形态配置声明的排练档是 `declared` 时按档位取值生效、`unstandardized`
时形态原值在 force（如实标注"未标定"）。演示档数字属运营侧输入，运营给定后**只改配置**。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.pilot.package import PACKAGE_FILES  # noqa: E402
from agents.pilot.pilot import (  # noqa: E402
    PilotInputs,
    PrecheckError,
    config_completeness,
    precheck,
    resume_pilot,
    run_pilot,
)

FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
INPUTS = PilotInputs(
    topic="夜班记录",
    target_duration_min=0.5,
    characters=("林静", "陈默"),
    constraints=("单场景为主",),
    genre_bounds=("悬疑", "夜戏"),
    audience="都市女性",
)


def _clock():
    return lambda: FIXED_TIMESTAMP


def _demo_config(source: Path, target: Path, *, demo_scale: bool) -> Path:
    """派生演示配置：账本根落临时目录 + （可选）把**排练档取值**改成演示档。

    **只改排练档声明**（`pilot.rehearsal.scale` 的两处取值），体量键（成片时长/剧本目标/
    页数容差/单镜时长）一个字不动——"缩档只改配置"因此可机检（口径变了即红）。
    """
    text = source.read_text(encoding="utf-8")
    assert "root: billing" in text, "派生点存在（账本根不得落仓库）"
    text = text.replace("root: billing", f"root: {target.parent / 'billing'}")
    if demo_scale:
        for old, new in (
            ("target_duration_s: 120.0", "target_duration_s: 30.0"),
            ("script_target_minutes: 2.0", "script_target_minutes: 0.5"),
        ):
            assert text.count(old) == 1, f"排练档取值行缺失或重复（{old}）：配置口径变了即红"
            text = text.replace(old, new)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def main() -> int:
    started = time.perf_counter()
    report: dict = {"steps": {}, "ok": False}
    with tempfile.TemporaryDirectory(prefix="cineflow-pilot-demo-") as tmp:
        root = Path(tmp)
        # 短剧：声明演示档的排练档（缩档只经配置声明）；movie：用其形态配置自带的排练档
        short_cfg = _demo_config(
            REPO_ROOT / "configs/shortdrama.yaml",
            root / "configs" / "shortdrama-demo.yaml",
            demo_scale=True,
        )
        movie_cfg = _demo_config(
            REPO_ROOT / "configs/movie.yaml",
            root / "configs" / "movie-demo.yaml",
            demo_scale=False,
        )

        # ---- 步骤 1：配置完整性（全部加载器，缺项即拒绝启动）----
        pre = precheck(
            form="shortdrama", config_path=short_cfg, inputs=INPUTS, data_dir=root / "pre"
        )
        # 断言的是**派生量**：加载器计数随实现派生（13 类配置 + 七环节权重 + `pilot` 段），
        # 写死字面量即恒假（属既有缺陷）；此处以 `config_completeness` 的实测返回为准
        loaders = config_completeness(short_cfg)
        report["steps"]["1_配置完整性"] = {
            "loaders": len(pre["loaders"]),
            "config_fingerprint": pre["config_fingerprint"],
            "ok": list(pre["loaders"]) == list(loaders) and len(loaders) > 0,
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
                    target_duration_min=0.5,
                    characters=("林静", "陈默"),
                    constraints=(),
                    genre_bounds=("悬疑",),
                    audience="都市女性",
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
