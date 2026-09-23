"""确定性模拟数据源（功能 017 / 契约 C9）：历史同类型票房回归 + 舆情热度。

**非真实商业数据**（澄清末条，已裁决）：仓库内不存在真实票房库与舆情接口，两类代理信号
以确定性函数 + 夹具参数（`dev.signals`）实现——零 LLM、零网络、零边际成本、可回放。
真实渠道接入属 G3（受预算门禁与账单对账约束），届时换实现并升评估器版本、不改评估器算法。

**数据源即行为口径**（原则一）：`dev.signals` 任一参数变更 ⇒ 新 `evaluator_id@version`
（参数规范化哈希进版本号），历史节点的 `eval_breakdown` 永不重算。

**来源标注**（SC-009）：每路数据源都给出机读标记载荷（`source` + `simulated=true` +
含"非真实商业数据"的说明 + 参数摘要），载荷与产物本体同源
（`agents/dev/artifact.py:simulated_signal_sources`，单一事实源，三处标注不得各写一份）。

取值口径（两代理共用，避免两份口径漂移）：

- 票房回归预测（百万美元）= `baseline_usd_million × sensitivity × 同类型夹具系数`；
  分量得分 = 预测值 / (2 × 基线)，即"达到基线两倍即满分"；
- 舆情热度 ∈ [0,1] = `buzz_baseline × sensitivity × 同类型夹具系数`（超界夹取，原值随诊断落盘）；
- 夹具（可选 `fixtures.genres.{题材}.{box_office_factor|buzz_factor}`）钉住逐题材取值；
  未列题材按 1.0（基线口径）——不臆造取值，缺项即按基线如实计算。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import blake3

from agents.dev.artifact import simulated_signal_sources
from agents.dev.config import SIGNAL_KEYS, DevConfigError

# 来源标识（与产物 `signal_sources` 的标注同源：SOURCE_IDS == artifact.SIMULATED_SOURCE_IDS）
SOURCE_BOX_OFFICE = "simulated.box_office_regression"
SOURCE_BUZZ = "simulated.buzz_heat"
SOURCE_IDS = (SOURCE_BOX_OFFICE, SOURCE_BUZZ)

# 票房回归的归一上界：预测值达到基线两倍即分量满分（口径进版本号：见两代理实现）
BOX_OFFICE_CAP_FACTOR = 2.0

# 数据源实现摘要（本模块字节的 BLAKE3）：取值函数即行为口径，改动本文件必须换版本
# （原则一：实现变更未升版即被拒——两代理的实现哈希覆盖本模块，不只覆盖各自评估器文件）
IMPLEMENTATION_DIGEST = blake3.blake3(Path(__file__).read_bytes()).hexdigest()

_FIXTURE_SECTION = "genres"
# 夹具键：逐题材的票房系数 / 热度系数（缺省 1.0 = 基线口径）
BOX_OFFICE_FACTOR_KEY = "box_office_factor"
BUZZ_FACTOR_KEY = "buzz_factor"
_FACTOR_KEYS = (BOX_OFFICE_FACTOR_KEY, BUZZ_FACTOR_KEY)


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _require_number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise DevConfigError(f"{where} 必须为 ≥0 的数值，实际为 {value!r}")
    return float(value)


def _require_fixtures(value, where: str) -> dict:
    """夹具表：`{genres: {题材: {box_office_factor|buzz_factor: 数值 ≥0}}}`（缺项非错）。"""
    if not isinstance(value, dict) or not value:
        raise DevConfigError(f"{where} 必须为非空 mapping，实际为 {value!r}")
    genres = value.get(_FIXTURE_SECTION)
    if not isinstance(genres, dict) or not genres:
        raise DevConfigError(f"{where}.{_FIXTURE_SECTION} 必须为非空 mapping，实际为 {genres!r}")
    for genre, factors in genres.items():
        if not isinstance(genre, str) or not genre:
            raise DevConfigError(
                f"{where}.{_FIXTURE_SECTION} 的键必须为非空题材名，实际为 {genre!r}"
            )
        if not isinstance(factors, dict) or not factors:
            raise DevConfigError(
                f"{where}.{_FIXTURE_SECTION}.{genre} 必须为非空 mapping，实际为 {factors!r}"
            )
        for key, factor in factors.items():
            if key not in _FACTOR_KEYS:
                raise DevConfigError(
                    f"{where}.{_FIXTURE_SECTION}.{genre} 的键必须为 {list(_FACTOR_KEYS)} 之一，"
                    f"实际为 {key!r}"
                )
            _require_number(factor, f"{where}.{_FIXTURE_SECTION}.{genre}.{key}")
    return dict(value)


@dataclass(frozen=True)
class SimulatedSignalSource:
    """一路模拟数据源：来源标识 + `dev.signals` 参数（含可选夹具表），确定性取值。

    参数即行为口径：`params_digest()` 是参数的规范化 BLAKE3 前 12 位，随评估器版本号入树；
    变更即新版本，旧节点的分量按键（`evaluator_id@version`）冻结，不重算。
    """

    source_id: str
    parameters: dict

    def __post_init__(self) -> None:
        if self.source_id not in SOURCE_IDS:
            raise DevConfigError(
                f"模拟数据源标识必须为 {list(SOURCE_IDS)} 之一，实际为 {self.source_id!r}"
                "（真实票房库/舆情接口属 G3 渠道范围，本环节只有模拟源）"
            )
        if not isinstance(self.parameters, dict) or not self.parameters:
            raise DevConfigError(f"dev.signals 必须为非空 mapping，实际为 {self.parameters!r}")
        parameters = dict(self.parameters)
        for key in SIGNAL_KEYS:
            if key not in parameters:
                raise DevConfigError(f"dev.signals 缺少配置项 {key!r}")
            parameters[key] = _require_number(parameters[key], f"dev.signals.{key}")
        if parameters.get("fixtures") is not None:
            parameters["fixtures"] = _require_fixtures(
                parameters["fixtures"], "dev.signals.fixtures"
            )
        object.__setattr__(self, "parameters", parameters)

    def params_digest(self) -> str:
        """参数摘要（12 位 hex）：行为口径的机检印记（改参数即改摘要）。"""
        return blake3.blake3(_canonical(self.parameters).encode()).hexdigest()[:12]

    def provenance(self) -> dict:
        """来源标记载荷（机读）：与产物本体同源，随取值进分量诊断（SC-009）。"""
        for payload in simulated_signal_sources(self.parameters):
            if payload["source"] == self.source_id:
                return dict(payload)
        raise DevConfigError(
            f"模拟数据源标识 {self.source_id!r} 不在标注清单内（须与产物 signal_sources 同源）"
        )

    def fixture_factor(self, genre: str, key: str) -> float:
        """逐题材夹具系数（缺省 1.0 = 基线口径，不臆造偏离）。"""
        fixtures = self.parameters.get("fixtures") or {}
        factors = (fixtures.get(_FIXTURE_SECTION) or {}).get(genre) or {}
        return float(factors.get(key, 1.0))

    @property
    def baseline_usd_million(self) -> float:
        return float(self.parameters["baseline_usd_million"])

    @property
    def sensitivity(self) -> float:
        return float(self.parameters["sensitivity"])

    @property
    def buzz_baseline(self) -> float:
        return float(self.parameters["buzz_baseline"])

    def box_office_usd_million(self, *, genre: str) -> float:
        """历史同类型票房回归预测（百万美元）：基线 × 灵敏度 × 同类型票房系数。"""
        return (
            self.baseline_usd_million
            * self.sensitivity
            * self.fixture_factor(genre, BOX_OFFICE_FACTOR_KEY)
        )

    def buzz_heat(self, *, genre: str) -> float:
        """舆情检索热度 ∈ [0,1]：基线 × 灵敏度 × 同类型热度系数（超界夹取）。"""
        heat = self.buzz_baseline * self.sensitivity * self.fixture_factor(genre, BUZZ_FACTOR_KEY)
        return min(1.0, max(0.0, heat))

    def box_office_normalizer_usd_million(self) -> float:
        """票房分量的归一基准（预测值达到基线两倍即满分）。"""
        return BOX_OFFICE_CAP_FACTOR * self.baseline_usd_million

    def version_part(self) -> str:
        """进评估器版本号的参数载荷：来源标识 + 规范化参数 + **数据源实现摘要**。

        口径参数即行为口径（改参数 ⇒ 新版本）；实现摘要覆盖本模块（取值函数即行为口径），
        故改数据源算法同样换版本——不是只有参数变了才算行为变更（原则一）。
        """
        return _canonical(
            {
                "source": self.source_id,
                "parameters": self.parameters,
                "implementation_digest": IMPLEMENTATION_DIGEST,
            }
        )
