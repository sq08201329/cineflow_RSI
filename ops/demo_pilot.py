#!/usr/bin/env python
"""端到端演示：七环节链的试水作品产出（功能 015 / T1525；功能 018 阶段 4 扩到七环节）。

七步（全模拟链路 + 临时目录 + 确定性时钟，退出码 0 = 七步全过）：

1. **配置完整性**（含 `pilot` 段与索引容量下界）：全部加载器逐个通过（缺项即拒绝启动，
   零成本零落树）；加载器计数取**派生量**（写死字面量即恒假）；
2. **七环节串链出包**：`dev → script → storyboard → visual → sound → editing → promo` 按同一
   DAG 依次执行 → 样片包五件套（含"模拟生成"标注）并过 `verify_package`；
3. **可复现对照**：同 run_id + 同输入同配置、独立工件根跑两次 → 五件套逐字节一致；
4. **movie 对照（零代码切换）**：同一套阶段代码换一份形态配置跑通（形态差异全在配置）；
5. **断点续跑**：全部完成后再续跑 → 原记录原样返回（零重跑、零重复落盘）；
6. **拒绝语义**：缺 `pilot.scene_count` / 缺 `budget.tiers.dev` / 两处时长不一致 ⇒ 启动前拒绝；
   任一环失败 ⇒ 整轮失败、其后环节 `skipped` 且**不产半包**；输入变更 ⇒ 拒绝续跑；
7. **排练档标注**：`declared` ⇒ 生效体量取自 `pilot.rehearsal.scale`；`unstandardized` ⇒
   不覆盖（形态原值在 force）并如实标注"未标定"（不发明数字）。

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

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.pilot import scale as scale_module  # noqa: E402
from agents.pilot import stages as stages_module  # noqa: E402
from agents.pilot.package import PACKAGE_FILES, verify_package  # noqa: E402
from agents.pilot.pilot import (  # noqa: E402
    PilotConfig,
    PilotInputs,
    PrecheckError,
    config_completeness,
    precheck,
    resume_pilot,
    run_pilot,
)
from core.orchestration.errors import StageFailedError  # noqa: E402

FIXED_TIMESTAMP = "2026-01-01T00:00:00+00:00"
SEVEN = ("dev", "script", "storyboard", "visual", "sound", "editing", "promo")
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

    交接要求的"本轮进入生产"标记**恰好一条**由形态配置的 `dev.production_marks: {min: 1,
    max: 1}` 声明（两形态同值），派生副本不再改写该键。
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


def _mutated_config(source: Path, target: Path, mutate) -> Path:
    """派生配置副本：`mutate` 按 YAML 语义只改声明键（其余取值逐值不变）。"""
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutate(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _capacity(config_path: Path) -> dict:
    """索引容量下界（步骤 1 的机检面）：派生镜头数只读 `agents/pilot/scale.py` 的唯一公式。

    容量义务按**形态原值**算（排练档缩的是本轮的运行体量，"该形态要能编码多少镜"不随之缩）。
    """
    from agents.pilot.stages import EditingConfig, StoryboardConfig, VisualConfig

    pilot = PilotConfig.from_yaml(config_path)
    grid = StoryboardConfig.from_yaml(config_path).render["index_grid"]
    derived = scale_module.derived_shot_count(
        scene_count=pilot.scene_count,
        target_duration_s=float(EditingConfig.from_yaml(config_path).target_duration_s),
        clip_duration_seconds=float(
            VisualConfig.from_yaml(config_path).clip_spec["duration_seconds"]
        ),
    )
    capacity = 2 ** (int(grid["rows"]) * int(grid["cols"]))
    return {
        "derived_shot_count": derived,
        "capacity": capacity,
        "index_grid": dict(grid),
        "ok": derived <= capacity,
    }


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

        # ---- 步骤 1：配置完整性（全部加载器 + `pilot` 段 + 索引容量下界）----
        pre = precheck(
            form="shortdrama", config_path=short_cfg, inputs=INPUTS, data_dir=root / "pre"
        )
        # 断言的是**派生量**：加载器计数随实现派生（各配置段 + 七环节权重 + `pilot` 段），
        # 写死字面量即恒假（属既有缺陷）；此处以 `config_completeness` 的实测返回为准
        loaders = config_completeness(short_cfg)
        capacity = _capacity(short_cfg)
        report["steps"]["1_配置完整性"] = {
            "loaders": len(pre["loaders"]),
            "config_fingerprint": pre["config_fingerprint"],
            "capacity": capacity,
            "ok": list(pre["loaders"]) == list(loaders) and len(loaders) > 0 and capacity["ok"],
        }

        # ---- 步骤 2：七环节串链出样片包 ----
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
        stages = tuple(state.stage_id for state in first.record.stages)
        report["steps"]["2_七环节串链出包"] = {
            "status": first.record.status.value,
            "stages": list(stages),
            "files": [name for name in PACKAGE_FILES if (package / name).is_file()],
            "total_cost_usd": first.record.total_cost_usd,
            "simulated_note": "模拟生成" in manifest["note"],
            "ok": first.record.status.value == "done"
            and stages == SEVEN
            and all((package / name).is_file() for name in PACKAGE_FILES)
            and "模拟生成" in manifest["note"]
            and verify_package(package)["reconciled"],
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
            "stages": [state.stage_id for state in movie.record.stages],
            "total_cost_usd": movie.record.total_cost_usd,
            "shortdrama_cost_usd": first.record.total_cost_usd,
            "ok": movie.record.status.value == "done"
            and movie_manifest["form"] == "movie"
            and tuple(state.stage_id for state in movie.record.stages) == SEVEN,
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

        # ---- 步骤 6：拒绝语义（预检缺项 / 时长矛盾 / 环节失败零半包 / 续跑指纹）----
        report["steps"]["6_拒绝语义"] = _rejection_step(root=root, short_cfg=short_cfg)

        # ---- 步骤 7：排练档标注（declared 生效 / unstandardized 不覆盖且未标定）----
        report["steps"]["7_排练档标注"] = _rehearsal_step(root=root, movie_cfg=movie_cfg)

        report["ok"] = all(step.get("ok") for step in report["steps"].values())
        report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1


def _rejection_reason(config_path: Path, root: Path, inputs: PilotInputs) -> str:
    """启动前拒绝原因（通过即空串）；演示只机检"确实拒绝了"与点名的内容。"""
    try:
        precheck(form="shortdrama", config_path=config_path, inputs=inputs, data_dir=root / "pre")
    except PrecheckError as exc:
        return str(exc)
    return ""


def _rejection_step(*, root: Path, short_cfg: Path) -> dict:
    """步骤 6：三类启动前拒绝 + 任一环失败的整轮失败与零半包 + 续跑指纹拒绝。"""
    missing_scene = _mutated_config(
        short_cfg,
        root / "configs" / "no-scene-count.yaml",
        lambda payload: payload["pilot"].pop("scene_count"),
    )
    missing_tier = _mutated_config(
        short_cfg,
        root / "configs" / "no-dev-tier.yaml",
        lambda payload: payload["budget"]["channels"]["llm"]["tiers"].pop("dev"),
    )

    def _break_duration(payload: dict) -> None:
        payload["editing"]["target_duration_s"] = 60  # 与 screenplay 2 分钟（120 秒）矛盾

    mismatched = _mutated_config(
        short_cfg, root / "configs" / "duration-mismatch.yaml", _break_duration
    )
    scene_reason = _rejection_reason(missing_scene, root, INPUTS)
    tier_reason = _rejection_reason(missing_tier, root, INPUTS)
    duration_reason = _rejection_reason(mismatched, root, INPUTS)

    # 任一环失败 ⇒ 整轮失败、其后环节 skipped、不装配样片包（零半包）
    def _boom(stage_input):
        del stage_input
        raise StageFailedError("视觉环节注入失败（下半链零调用）")

    original = stages_module._visual_entry
    stages_module._visual_entry = _boom
    try:
        broken = run_pilot(
            form="shortdrama",
            config_path=short_cfg,
            inputs=INPUTS,
            data_dir=root / "c" / "pilot",
            artifacts_root=root / "c" / "artifacts",
            run_id="demo-fail",
            clock=_clock(),
        )
    finally:
        stages_module._visual_entry = original
    downstream = [
        state.stage_id for state in broken.record.stages if state.status.value == "skipped"
    ]
    half_package_gone = not (root / "c" / "pilot" / "packages" / "demo-fail").exists()

    # 输入变更后续跑拒绝（指纹不一致即拒绝，不把旧记录当"幂等成功"返回）
    resume_reason = ""
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
        resume_reason = str(exc)

    return {
        "missing_scene_count": "scene_count" in scene_reason,
        "missing_dev_tier": "dev" in tier_reason,
        "duration_mismatch_named": "120 s" in duration_reason and "60 s" in duration_reason,
        "stage_failure_status": broken.record.status.value,
        "stage_failure_downstream_skipped": downstream == ["sound", "editing", "promo"],
        "stage_failure_no_package": broken.package_dir is None and half_package_gone,
        "resume_fingerprint_rejected": "指纹" in resume_reason or "续跑" in resume_reason,
        "ok": "scene_count" in scene_reason
        and "dev" in tier_reason
        and "120 s" in duration_reason
        and "60 s" in duration_reason
        and broken.record.status.value == "failed"
        and broken.package_dir is None
        and half_package_gone
        and downstream == ["sound", "editing", "promo"]
        and ("指纹" in resume_reason or "续跑" in resume_reason),
    }


def _rehearsal_step(*, root: Path, movie_cfg: Path) -> dict:
    """步骤 7：`declared` 档位生效、`unstandardized` 不覆盖（形态原值在 force）并标注未标定。"""
    declared_cfg = PilotConfig.from_yaml(movie_cfg)
    declared = precheck(form="movie", config_path=movie_cfg, inputs=INPUTS, data_dir=root / "pre")[
        "pilot_volume"
    ]
    unstandardized_cfg = _mutated_config(
        movie_cfg,
        root / "configs" / "unstandardized.yaml",
        lambda payload: payload["pilot"]["rehearsal"].update({"status": "unstandardized"}),
    )
    form_original = _form_originals(movie_cfg)
    unstandardized = precheck(
        form="movie",
        config_path=unstandardized_cfg,
        # 未标定 ⇒ 形态原值在 force，运行级输入须与之同口径（90 分钟 = 5400 秒）
        inputs=PilotInputs(
            topic=INPUTS.topic,
            target_duration_min=90.0,
            characters=INPUTS.characters,
            constraints=INPUTS.constraints,
            genre_bounds=INPUTS.genre_bounds,
            audience=INPUTS.audience,
        ),
        data_dir=root / "pre",
    )["pilot_volume"]
    return {
        "declared_source": declared["source"],
        "declared_effective_s": declared["effective"]["target_duration_s"],
        "unstandardized_source": unstandardized["source"],
        "unstandardized_note": unstandardized["note"],
        "unstandardized_effective_s": unstandardized["effective"]["target_duration_s"],
        "ok": declared["source"] == "declared_scale"
        and declared["effective"]["target_duration_s"] == declared_cfg.scale.target_duration_s
        and unstandardized["source"] == "form_original"
        and "未标定" in unstandardized["note"]
        # 未标定期间不发明数字：生效体量逐键等于形态原值声明
        and unstandardized["effective"] == form_original
        and declared["work_kind"] == unstandardized["work_kind"] == "rehearsal",
    }


def _form_originals(config_path: Path) -> dict:
    """未标定档的对照面：形态原值声明（排练档不覆盖时生效的就是这几个键）。"""
    from agents.pilot.stages import EditingConfig, ScreenplayConfig, VisualConfig

    return {
        "scene_count": PilotConfig.from_yaml(config_path).scene_count,
        "lines_per_scene": PilotConfig.from_yaml(config_path).lines_per_scene,
        "lines_per_page": ScreenplayConfig.from_yaml(config_path).lines_per_page,
        "target_duration_s": float(EditingConfig.from_yaml(config_path).target_duration_s),
        "script_target_minutes": float(ScreenplayConfig.from_yaml(config_path).target_duration_min),
        "script_target_pages": ScreenplayConfig.from_yaml(config_path).target_duration_min,
        "page_tolerance": ScreenplayConfig.from_yaml(config_path).page_tolerance,
        "clip_duration_seconds": float(
            VisualConfig.from_yaml(config_path).clip_spec["duration_seconds"]
        ),
    }


if __name__ == "__main__":
    sys.exit(main())
