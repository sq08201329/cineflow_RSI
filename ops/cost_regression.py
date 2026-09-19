#!/usr/bin/env python
"""成本回归每日告警脚本（宪章门禁：相同回放任务成本突增 > 20% → 每日定时告警）。

形态对齐 ops/audit_immutable.py：纯函数核心 + 薄 CLI，JSON 报告打印 stdout，
任一告警即非零退出（0=无告警，1=有告警，2=参数/配置错误）。

口径决策（现有数据中最接近"相同回放任务"的可机检标识）：
- 回放任务标识 = agent_id。做梦管线每轮对同一模拟器池回放 M 个候选，
  DreamRound 落盘 dreaming/history/{agent_id}/dream-{agent_id}-{seq}.json（只增不改），
  同一 agent_id 的轮次序列即"同一回放任务按时间的成本序列"。
- 任务成本 = 该轮全部成功回放轨迹 trajectory.total_cost.generation_api_cost_usd 合计
  （USD 口径与 promo/visual 三方对账一致：网关折算 + 生成/投放花费同入该字段）。
- 基线 = 同一 agent_id 的前一个有效轮次（按 round_id 序号升序）。"突增"语义取
  相邻轮次比较；无成功回放轨迹的轮次（全 rejected/全失败）不计入序列。
- 不判定情形（均不告警，note 注明）：首轮无历史基线；基线成本 ≤ 0（增幅无定义）。
- 判定为严格大于：增幅 > threshold 才告警，等于阈值放行（门禁原文"突增 > 20%"）。

阈值走 configs/movie.yaml 的 cost_regression 段（可配），CLI --threshold 可临时覆盖。

用法：
    uv run python ops/cost_regression.py [--config configs/movie.yaml]
        [--history-root DIR] [--threshold 0.2]
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "movie.yaml"
DEFAULT_THRESHOLD = 0.2  # 宪章门禁阈值；配置缺失项时的兜底


class CostRegressionConfigError(Exception):
    """成本回归配置缺失/非法。"""


@dataclass(frozen=True)
class CostRegressionConfig:
    """cost_regression 段配置（告警阈值 + 做梦轮次落盘根目录）。"""

    threshold: float
    history_root: str = "dreaming/history"

    @classmethod
    def from_dict(cls, config: dict) -> "CostRegressionConfig":
        section = config.get("cost_regression")
        if not isinstance(section, dict):
            raise CostRegressionConfigError("形态配置缺少 cost_regression 段")
        threshold = float(section.get("threshold", DEFAULT_THRESHOLD))
        if threshold <= 0:
            raise CostRegressionConfigError(
                f"cost_regression.threshold 必须为 > 0，实际为 {threshold}"
            )
        return cls(
            threshold=threshold,
            history_root=str(section.get("history_root", "dreaming/history")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CostRegressionConfig":
        return cls.from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def round_replay_cost_usd(round_payload: dict) -> float | None:
    """单轮回放总成本（纯函数）：全部成功回放轨迹 generation_api_cost_usd 合计。

    无成功轨迹（全 rejected / 全回放失败）返回 None——该轮不进成本序列。
    """
    costs = [
        candidate["trajectory"]["total_cost"]["generation_api_cost_usd"]
        for candidate in round_payload.get("candidates", [])
        if candidate.get("trajectory") is not None
    ]
    if not costs:
        return None
    return float(sum(costs))


def _round_seq(round_id: str) -> int:
    """从 round_id（dream-{agent_id}-{seq}）解析序号，用于按时间排序。"""
    return int(round_id.rsplit("-", 1)[1])


def load_cost_series(history_root: str | Path, agent_id: str) -> list[tuple[str, float]]:
    """读取同一 agent_id 的有效轮次成本序列：按轮次序号升序的 (round_id, cost_usd)。"""
    directory = Path(history_root) / agent_id
    rounds: list[tuple[int, str, float]] = []
    for path in directory.glob("dream-*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cost = round_replay_cost_usd(payload)
        if cost is not None:
            rounds.append((_round_seq(payload["round_id"]), payload["round_id"], cost))
    rounds.sort(key=lambda item: item[0])
    return [(round_id, cost) for _, round_id, cost in rounds]


def check_agent_series(
    agent_id: str, series: list[tuple[str, float]], threshold: float
) -> dict[str, Any]:
    """单任务判定（纯函数）：最近一轮 vs 前一轮，增幅 > threshold 即告警。"""
    result: dict[str, Any] = {
        "agent_id": agent_id,
        "checked_rounds": len(series),
        "alert": False,
        "baseline_round": None,
        "latest_round": None,
        "baseline_cost_usd": None,
        "latest_cost_usd": None,
        "increase_ratio": None,
        "note": "",
    }
    if len(series) < 2:
        result["note"] = "首轮无历史基线，不判定"
        if series:
            result["latest_round"], result["latest_cost_usd"] = series[-1]
        return result

    (baseline_round, baseline_cost), (latest_round, latest_cost) = series[-2], series[-1]
    result.update(
        baseline_round=baseline_round,
        latest_round=latest_round,
        baseline_cost_usd=baseline_cost,
        latest_cost_usd=latest_cost,
    )
    if baseline_cost <= 0:
        result["note"] = "基线成本为 0，增幅无定义，不判定"
        return result

    ratio = (latest_cost - baseline_cost) / baseline_cost
    result["increase_ratio"] = ratio
    if ratio > threshold:  # 严格大于：等于阈值放行（门禁原文"突增 > 20%"）
        result["alert"] = True
        result["note"] = f"成本突增 {ratio:.1%} > 阈值 {threshold:.0%}"
    return result


def run_check(history_root: str | Path, threshold: float) -> dict[str, Any]:
    """全量检查（纯函数）：每个 agent_id 独立判定，汇总告警报告。"""
    root = Path(history_root)
    agents = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    results = [check_agent_series(a, load_cost_series(root, a), threshold) for a in agents]
    alerts = [r for r in results if r["alert"]]
    return {
        "threshold": threshold,
        "history_root": str(root),
        "agents": results,
        "alerts": alerts,
        "ok": not alerts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="成本回归每日检查：相同回放任务成本突增告警")
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG), help="形态配置（默认 configs/movie.yaml）"
    )
    parser.add_argument("--history-root", default=None, help="做梦轮次落盘根目录（默认读配置）")
    parser.add_argument("--threshold", type=float, default=None, help="告警阈值（默认读配置）")
    args = parser.parse_args(argv)

    try:
        config = CostRegressionConfig.from_yaml(args.config)
    except (OSError, CostRegressionConfigError) as exc:
        print(json.dumps({"error": f"配置加载失败：{exc}"}, ensure_ascii=False))
        return 2

    threshold = args.threshold if args.threshold is not None else config.threshold
    history_root = args.history_root or config.history_root
    report = run_check(history_root, threshold)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
