#!/usr/bin/env python
"""真实 LLM 冒烟器（DeepSeek 示例）：只有 LLM 凭证时验证 B 路径的"LLM 腿"。

用法（在**自己的 shell** 里导出凭证；密钥只读环境变量，脚本不打印、不落盘）：

**按档案调用（功能 016）**：端点/密钥/价目都取自配置 `llm.profiles` 的**档案声明**——
`--profile deepseek-flash`（`--model` 保留为等价别名）；环境里无关的 `OPENAI_*`
只在某档案**显式声明**它时才参与（假阳性归零）。

```bash
export OPENAI_BASE_URL=https://api.deepseek.com
export OPENAI_API_KEY=...           # 你自己的密钥（本仓任何文件都不含真实密钥）

# ① 网关级冒烟（一次调用，最小花费）
uv run python ops/smoke_llm.py --config configs/movie.yaml --model deepseek-flash

# ② 最小规模的真实试水单轮（剧本线真实；视觉/分镜/声音/剪辑/宣发的平台适配器仍为模拟）
uv run python ops/smoke_llm.py --config configs/shortdrama.yaml --round

# ③ 零真实调用的装配校验（同一最小档，但 LLM 走 mock）：验证"规模最小化 + 装配"能跑通
uv run python ops/smoke_llm.py --config configs/shortdrama.yaml --round --dry-run
```

两种模式：

- **网关级（默认）**：经既有 `LLMGateway` + 真实 `HttpBackend` 发一次 prompt，打印模型、
  base **host**（不含路径/查询串/密钥）、prompt/completion tokens、按配置价目表折算的成本、
  是否缓存命中；
- **`--round`**：把形态配置复制到工作目录并用 `core.yaml_edit` **定点改写**（注释逐字保留）——
  ① 把全部模型引用（`screenplay.model`、`screenplay.judge.model`、`storyboard.judge.model`、
  `promo.default_model`）换成 `--model`；② 把规模与预算压到最小（镜头数 4、各环节单轮组合数 1、
  单轮预算下调）；③ 在 `pilot` 段声明 `llm_backend`。然后跑一次最小规模试水并打印运行记录摘要、
  账目与产物清单。**预算门禁与其余形态语义不变**（只改取值，不改机制）。

退出码：`0` 成功｜`1` 凭证缺失（指向 `ops/check_credentials.py`）｜`2` 其它失败。

隐私与成本纪律：

- 只报凭证"已设置/未设置 + 长度"，**绝不回显值**；base 只打印 `scheme://host[:port]`；
- 真实调用会**真的计费**（DeepSeek 按 token 收费）：`--round` 会跑整条链的 LLM 调用，
  先用 `--dry-run` 验证装配与规模，再按需去掉它；最小档的**量级估算**见
  `docs/二期升级路径-真实生成与投放.md` 的"真实 LLM 冒烟"一节；
- 除 LLM 外的适配器仍无凭证：视觉/分镜/声音/剪辑/宣发在 `--round` 里全部保持模拟
  （`backend: simulated`），C 路径与真实生成仍 `not_delivered`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.pilot.pilot import PilotInputs, run_pilot  # noqa: E402
from core.llm_gateway.backends.http import HttpBackend  # noqa: E402
from core.llm_gateway.gateway import GatewayError, LLMGateway  # noqa: E402
from core.llm_gateway.profiles import (  # noqa: E402
    ProfileConfigError,
    host_of_url,
    load_or_migrate,
)
from core.llm_gateway.routing import Role  # noqa: E402
from core.yaml_edit import upsert_section_entries  # noqa: E402

# 端点展示口径（遗留 4 收敛）：与 core 的档案快照口径同一实现，不再各写一份
base_host = host_of_url

EXIT_OK = 0
EXIT_NO_CREDENTIALS = 1
EXIT_FAILED = 2

REQUIRED_ENV = ("OPENAI_BASE_URL", "OPENAI_API_KEY")
DEFAULT_MODEL = "deepseek-flash"  # 兼容别名：等价于 --profile deepseek-flash
_ALL_ROLES = tuple(role for role in Role)  # 四个角色：--round 改写 llm.roles 用
DEFAULT_PROMPT = "用一句话写出「雨夜便利店」的开场镜头（中文，不超过 40 字）。"
DEFAULT_ROUND_TOPIC = "雨夜便利店"
DEFAULT_ROUND_CHARACTERS = "林静,陈默"
DEFAULT_WORK_DIR = ".smoke-llm"
DEFAULT_RUN_ID = "smoke-llm"
CREDENTIAL_HINT = (
    "凭证缺失：请导出 OPENAI_BASE_URL / OPENAI_API_KEY（DeepSeek："
    "https://api.deepseek.com），并用 `uv run python ops/check_credentials.py "
    "--probe --path B` 复核"
)

# 模型名引用的实际位置（按代码读取点）：剧本生成 + 两个 judge 委员会 + 宣发文案
MODEL_PATHS = (
    ("screenplay",),
    ("screenplay", "judge"),
    ("storyboard", "judge"),
    ("promo",),
)
MODEL_KEYS = {
    ("screenplay",): "model",
    ("screenplay", "judge"): "model",
    ("storyboard", "judge"): "model",
    ("promo",): "default_model",
}

# 最小规模的单轮预算（美元；够跑通最小档即可——被门禁的只是模拟平台开销）
MINIMAL_BUDGETS = {
    ("storyboard",): 10.0,
    ("visual",): 10.0,
    ("sound",): 40.0,
    ("editing",): 10.0,
    ("promo",): 60.0,
}
MINIMAL_SHOT_COUNT = 4  # 镜头数下限（= 场景数）：目标时长 = 单镜时长 × 4


class SmokeError(Exception):
    """冒烟失败（退出码 2）：凭证已就位但调用/运行不成功。"""


def credential_report(environ: dict[str, str] | None = None) -> dict[str, dict]:
    """凭证就位报告：**只报是否设置与长度**（绝不回显值）。"""
    env = os.environ if environ is None else environ
    report: dict[str, dict] = {}
    for name in REQUIRED_ENV:
        value = env.get(name) or ""
        report[name] = {"set": bool(value), "length": len(value)}
    return report


def missing_credentials(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    report = credential_report(environ)
    return tuple(name for name in REQUIRED_ENV if not report[name]["set"])


def load_profile_set(config_path: str | Path):
    """读配置档案（功能 016）：有 `llm` 段用新写法，否则按旧扁平写法迁移为单档案。"""
    payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    return load_or_migrate(payload if isinstance(payload, dict) else {})


def resolve_profile(config_path: str | Path, requested: str | None):
    """定位要冒烟的档案：显式 id 必须存在（缺失即列出可用档案）；缺省取默认档案。"""
    loaded = load_profile_set(config_path)
    profile_id = requested or loaded.routing.default_profile
    if profile_id not in loaded.profiles:
        raise SmokeError(
            f"档案不存在：{profile_id!r}；可用档案为 {sorted(loaded.profiles)}"
            "（档案定义在形态配置 llm.profiles）"
        )
    return loaded, loaded.profiles[profile_id]


def profile_env_status(profile, environ: dict | None = None) -> dict:
    """档案各自的凭证就位状态（只报设置与否 + 长度，不回显值）。"""
    env = os.environ if environ is None else environ
    names = [profile.api_key_env]
    if not profile.base_url and profile.base_url_env:
        names.append(profile.base_url_env)
    report: dict[str, dict] = {}
    for name in names:
        value = env.get(name) or ""
        report[name] = {"set": bool(value), "length": len(value)}
    return report


def restricted_profile_set(loaded, profile_id: str):
    """只保留目标档案的视图（角色空 → 任何角色都回落该档案）：冒烟只打这一条档案。"""
    from core.llm_gateway.profiles import ProfileLoad
    from core.llm_gateway.routing import RoleRouting

    return ProfileLoad(
        profiles={profile_id: loaded.profiles[profile_id]},
        routing=RoleRouting(roles={}, default_profile=profile_id, default_reason="single_profile"),
        notes=(),
        source=loaded.source,
    )


def load_price_book(config_path: str | Path) -> dict:
    """价目表视图（兼容入口）：返回 `{profile_id: {prompt_per_1k, completion_per_1k}}`。"""
    return load_profile_set(config_path).snapshot().price_book()


def build_gateway(
    config_path: str | Path, *, backend=None, profile_id: str | None = None, environ=None
) -> LLMGateway:
    """按目标**档案**装配网关（只含该档案的视图；LLM 必须过网关，脚本不直连后端）。"""
    loaded, profile = resolve_profile(config_path, profile_id)
    scoped = restricted_profile_set(loaded, profile.profile_id)
    return LLMGateway(
        backend if backend is not None else HttpBackend.from_profile(profile, environ=environ),
        price_book=scoped.snapshot().price_book(),
        sleep=lambda _: None,
        profiles=scoped,
    )


def run_gateway_smoke(
    config_path: str | Path,
    *,
    profile_id: str | None = None,
    prompt: str = DEFAULT_PROMPT,
    backend=None,
    environ: dict | None = None,
) -> dict:
    """网关级冒烟：按**档案**调用一次；打印 tokens / 按档案价目折算的成本 / 缓存命中 / 口径备注。"""
    _, profile = resolve_profile(config_path, profile_id)
    if backend is None:
        # 凭证按**档案声明**注入（不隐式读 OPENAI_*；未声明/未设置即报错）
        backend = HttpBackend.from_profile(profile, environ=environ)
    gateway = build_gateway(config_path, backend=backend, profile_id=profile.profile_id)
    result = gateway.chat(prompt, role=Role.GENERATION)  # 角色回落唯一档案
    _, price = gateway.prices_for(role=Role.GENERATION)
    return {
        "mode": "gateway",
        "profile_id": profile.profile_id,
        "endpoint": profile.endpoint_ref,  # 只记 host 或变量名（不含密钥）
        "legacy_env": profile.legacy_env,
        "legacy_env_note": getattr(backend, "legacy_env_note", ""),
        "prices": price,
        "price_note": profile.price_note,
        "prompt_tokens": result.usage["prompt_tokens"],
        "completion_tokens": result.usage["completion_tokens"],
        "cost_usd": round(result.cost_usd, 8),
        "zero_marginal": bool(profile.zero_marginal),
        "cached": result.cached,
        "role": result.role,
        "gateway_call_count": gateway.call_count,
        "text_preview": result.text[:80],
    }


def minimal_scale_entries(config_payload: dict) -> list[tuple[tuple[str, ...], dict]]:
    """最小规模改写项（只改取值，不改机制）：镜头数 4、单轮组合数 1、预算下调。

    目标成片时长 = 单镜时长 × 4（`build_shot_plan` 取 `max(场景数=4, 目标时长/单镜时长)`），
    编辑门禁的时长容差沿用原配置（4 镜落点确定，无须放宽容差）。
    """
    clip_seconds = float(config_payload["visual"]["clip_spec"]["duration_seconds"])
    updates: dict[tuple[str, ...], dict] = {
        ("screenplay",): {"target_duration_min": 1, "page_tolerance": 1},
        # 目标成片时长 = 单镜时长 × 4：镜头数落在下界（4 镜），编辑门禁的时长容差不用放宽
        ("editing",): {
            "target_duration_s": int(clip_seconds * MINIMAL_SHOT_COUNT),
            "edits_per_round": 1,
        },
        ("storyboard",): {"boards_per_round": 1},
        ("visual",): {"clips_per_round": 1},
        ("promo",): {"materials_per_round": 1},
    }
    for path, budget in MINIMAL_BUDGETS.items():
        updates.setdefault(path, {})["exploration_per_round_usd"] = budget
    return list(updates.items())


def rewrite_config(
    config_path: str | Path,
    target_path: str | Path,
    *,
    profile_id: str,
    llm_backend: str,
    minimal: bool = True,
) -> Path:
    """复制形态配置到 target_path 并**定点改写**（`core.yaml_edit`，注释逐字保留）。

    改写三项（功能 016：路由只在配置，**不再散落改写模型名**）：
    ① `llm.roles`：四个角色全部指向 `profile_id`（角色映射是唯一路由入口）；
    ② （`minimal`）规模与预算压到最小；
    ③ `pilot.llm_backend` → `llm_backend`（让临时配置如实声明本次实际后端）。
    """
    source = Path(config_path).read_text(encoding="utf-8")
    text = source
    assert profile_id in load_profile_set(config_path).profiles, profile_id
    text = upsert_section_entries(
        text, ("llm", "roles"), {str(role): profile_id for role in _ALL_ROLES}
    )
    text = upsert_section_entries(text, ("llm",), {"default_profile": profile_id})
    if minimal:
        payload = yaml.safe_load(source)
        for path, updates in minimal_scale_entries(payload):
            text = upsert_section_entries(text, path, updates)
    text = upsert_section_entries(text, ("pilot",), {"llm_backend": llm_backend})
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def run_round_smoke(
    config_path: str | Path,
    *,
    profile_id: str | None = None,
    llm_backend: str = "http",
    work_dir: str | Path = DEFAULT_WORK_DIR,
    topic: str = DEFAULT_ROUND_TOPIC,
    characters: str = DEFAULT_ROUND_CHARACTERS,
    run_id: str = DEFAULT_RUN_ID,
    runner=None,
) -> dict:
    """最小规模单轮：改写临时配置 → 跑一次试水（平台适配器仍模拟，LLM 走 `llm_backend`）。

    `runner` 可注入（测试用假 runner，零网络零落盘）；默认走既有 `run_pilot`。
    工作目录只放临时配置与运行数据（`--work-dir`，默认 `.smoke-llm`，可随时删除）。
    """
    work = Path(work_dir)
    _, profile = resolve_profile(config_path, profile_id)
    temp_config = rewrite_config(
        config_path,
        work / "configs" / f"{Path(config_path).stem}-smoke.yaml",
        profile_id=profile.profile_id,
        llm_backend=llm_backend,
    )
    form = str(yaml.safe_load(temp_config.read_text(encoding="utf-8")).get("form", "shortdrama"))
    inputs = PilotInputs(
        topic=topic,
        target_duration_min=1,
        characters=tuple(name for name in characters.split(",") if name),
    )
    run = (runner or run_pilot)(
        form=form,
        config_path=temp_config,
        inputs=inputs,
        data_dir=work / "pilot",
        run_id=run_id,
        # 平台适配器**不覆盖**（None = 取形态配置取值，即 simulated）：只有 LLM 腿变真实
        backend=None,
        llm_backend=llm_backend,
    )
    return {
        "mode": "round",
        "profile_id": profile.profile_id,
        "llm_backend": llm_backend,
        "temp_config": str(temp_config),
        "work_dir": str(work),
        "run_id": getattr(run, "run_id", run_id),
        "status": _run_status(run),
        "stages": _stage_summary(run),
        "cost": _cost_summary(run),
        "package_dir": str(getattr(run, "package_dir", "") or ""),
        "products": _product_summary(run),
    }


def _run_status(run) -> str:
    record = getattr(run, "record", None)
    status = getattr(record, "status", None)
    return getattr(status, "value", str(status))


def _stage_summary(run) -> list[dict]:
    record = getattr(run, "record", None)
    stages = getattr(record, "stages", ()) or ()
    return [
        {
            "stage_id": state.stage_id,
            "status": getattr(state.status, "value", str(state.status)),
            "attempts": state.attempts,
            "cost_usd": round(float(state.cost_usd), 6),
            "failure_reason": state.failure_reason,
        }
        for state in stages
    ]


def _cost_summary(run) -> dict:
    """账目摘要：优先读样片包 `cost.json`（对账结果），否则汇总运行记录的成本。"""
    package_dir = getattr(run, "package_dir", None)
    if package_dir:
        cost_path = Path(package_dir) / "cost.json"
        if cost_path.is_file():
            payload = json.loads(cost_path.read_text(encoding="utf-8"))
            return {
                "source": "package/cost.json",
                "total_usd": payload.get("total_usd"),
                "by_stage": payload.get("by_stage"),
                "reconciled": payload.get("reconciled"),
            }
    record = getattr(run, "record", None)
    by_stage = {
        state.stage_id: round(float(state.cost_usd), 6)
        for state in (getattr(record, "stages", ()) or ())
    }
    return {
        "source": "run-record",
        "total_usd": round(sum(by_stage.values()), 6),
        "by_stage": by_stage,
        "detail": "按阶段成本合计（样片包未产时）；LLM 侧 token 量级取自各阶段运行记录",
    }


def _product_summary(run) -> list[dict]:
    """产物清单：各阶段冻结的产物引用（哈希截断展示，完整值在运行记录里）。"""
    record = getattr(run, "record", None)
    products: list[dict] = []
    for state in getattr(record, "stages", ()) or ():
        for product in getattr(state, "products", ()) or ():
            products.append(
                {
                    "stage_id": state.stage_id,
                    "kind": product.kind,
                    "ref": product.ref,
                    "content_hash_16": product.content_hash[:16],
                }
            )
    return products


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="真实 LLM 冒烟器（DeepSeek 示例）：网关级一次调用，或最小规模真实试水单轮"
    )
    parser.add_argument("--config", required=True, help="形态配置路径（价目表与形态取值来源）")
    parser.add_argument(
        "--profile",
        default=None,
        help="模型档案 id（端点/凭证/价目都取自该档案；缺省取配置的 default_profile）",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"档案 id 的等价别名（兼容旧用法，如 --model {DEFAULT_MODEL}）",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="网关级冒烟的提示词")
    parser.add_argument(
        "--round",
        action="store_true",
        help="最小规模真实试水单轮（改写临时配置：模型引用 + 最小规模 + pilot.llm_backend）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="与 --round 同一最小档，但 LLM 走 mock（**零真实调用**，只验证装配与规模）",
    )
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR, help="--round 的工作目录")
    parser.add_argument("--topic", default=DEFAULT_ROUND_TOPIC, help="--round 的题材")
    parser.add_argument("--characters", default=DEFAULT_ROUND_CHARACTERS, help="--round 的角色表")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID, help="--round 的运行标识")
    parser.add_argument(
        "--keep-work-dir", action="store_true", help="保留工作目录（默认 --round 成功后清理）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # --dry-run（LLM 走 mock、零真实调用）不要求凭证：校验装配与最小规模用
    dry = bool(args.round and args.dry_run)
    requested = args.profile or args.model
    if args.profile and args.model and args.profile != args.model:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "usage_error",
                    "error": "--profile 与 --model 必须一致（后者是别名）",
                },
                ensure_ascii=False,
            )
        )
        return EXIT_FAILED
    try:
        _, profile = resolve_profile(args.config, requested)
    except (SmokeError, ProfileConfigError) as exc:
        print(
            json.dumps(
                {"ok": False, "reason": "profile_error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_FAILED
    status = profile_env_status(profile)
    missing = tuple(name for name, item in status.items() if not item["set"])
    if missing and not dry:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "credentials_missing",
                    "profile_id": profile.profile_id,
                    "missing": list(missing),
                    "present": status,
                    "legacy_env": profile.legacy_env,
                    "hint": CREDENTIAL_HINT,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_NO_CREDENTIALS
    try:
        if not args.round:
            payload = run_gateway_smoke(
                args.config, profile_id=requested, prompt=args.prompt, backend=None
            )
        else:
            payload = run_round_smoke(
                args.config,
                profile_id=requested,
                llm_backend="mock" if args.dry_run else "http",
                work_dir=args.work_dir,
                topic=args.topic,
                characters=args.characters,
                run_id=args.run_id,
            )
    except (SmokeError, GatewayError, ProfileConfigError) as exc:
        print(
            json.dumps(
                {"ok": False, "reason": "smoke_failed", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - 冒烟器边界：如实报错给运行方，不吞异常
        print(
            json.dumps(
                {"ok": False, "reason": "unexpected_error", "error": repr(exc)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return EXIT_FAILED
    if args.round and not args.keep_work_dir:
        shutil.rmtree(Path(args.work_dir), ignore_errors=True)  # 冒烟产物不留在仓库里
        payload["work_dir"] = f"{args.work_dir}（已清理；--keep-work-dir 可保留）"
    payload["ok"] = True
    payload["credentials"] = status  # 只报是否设置与长度
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
