"""020 口径声明完备与 cadence 收口单测（功能 021 A4 / T2148 + T2150~T2153，先于实现编写）。

覆盖 `specs/021-form-plugin-validation/contracts/form-registration.md` 的 C11 可执行面：

1. **七项口径逐项声明**（cadence / 窗口口径与生效日 / 渠道命名空间 / 归属日生效日 / 迁移六键 /
   运行窗口下限与断档容差）：缺任一项 ⇒ `config_completeness` **拒绝启动**并**点名段与键**；
2. **cadence 越界即显式报错**：取值域取自 `core/calibration/periods.py` 的 `SUPPORTED_CADENCES`，
   文案**点名取值域 `(1, 7)`**、**不回落**日级/周级，且 `periods.py` 一字不改；
3. **"不适用"必须显式声明**：只允许 `budget` 与 `calibration.transfer` 两处、
   理由非空且含「不适用」、
   双向无歧义；
4. **三层"未标定"标注**：形态层取值域 / 五段段级 `note` / `calibration.cadence_note` 的近似关系；
5. **既有两形态取值零改动**：读**原始文件**逐字节比对（`period_days` / `min_window_days` /
   `gap_tolerance_days`）。
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

from agents.pilot.pilot import (
    CALIBRATION_CLAIM_WORDS,
    FORM_CLAUSE_CHECKS,
    NOT_APPLICABLE_SITES,
    REHEARSAL_STATUSES,
    UNCALIBRATED_NOTE_SEGMENTS,
    PrecheckError,
    config_completeness,
    form_clause_completeness,
)
from core.calibration.config import CalibrationConfig
from core.calibration.errors import CalibrationConfigError
from core.calibration.periods import SUPPORTED_CADENCES
from ops.form_guard import declared_forms

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
FORMS = declared_forms(CONFIGS_DIR)

_CALIBRATION_SECTION = {
    "period_days": 7,
    "top_k": 5,
    "min_samples": 3,
    "bias_threshold": 0.15,
    "reliability_target": 0.6,
    "ridge_lambda": 1.0,
    "window_semantics": "half_open",
    "window_semantics_change_date": "2026-09-25",
    "self_pairing_exclusions": {"platform_truth": ["human.platform_metrics"]},
    "transfer": {
        "basis": "conclusion_only",
        "source_forms": ["shortdrama"],
        "target_forms": ["movie"],
        "conditions": {"min_samples": 3, "real_coverage_days": 1},
        "storage": {"dir": "transfers"},
        "adoption": "manual",
    },
}


def _document(form: str) -> dict:
    return yaml.safe_load((CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "form.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _drop(document: dict, path: str) -> None:
    """按点分键路径删一个口径键（缺项注入；**不回退校验、不改既有文件**——一律临时副本）。"""
    keys = path.split(".")
    cursor = document
    for key in keys[:-1]:
        cursor = cursor[key]
    del cursor[keys[-1]]


def _unstandardize(document: dict) -> None:
    """把一个形态文档改成**未标定**形态（形态层取值域内），供三层标注的注入取证。"""
    document["pilot"]["rehearsal"] = {"status": "unstandardized", "work_kind": "rehearsal"}


def _state_uncailbrated(document: dict) -> None:
    """把一个形态文档改成**未标定**形态（形态层取值域内），供三层标注的注入取证。"""
    _unstandardize(document)
    for segment in UNCALIBRATED_NOTE_SEGMENTS:
        document[segment]["note"] = f"未标定（业务数字待运营给定）：{segment} 段"
    document["calibration"]["cadence_note"] = "档位近似业务侧真实节律；未标定"


class Test七项口径齐备:
    """C11 机检断言①②：逐项声明 + 缺项即拒绝启动并点名段与键。"""

    @pytest.mark.parametrize("form", FORMS)
    def test_返回项与声明的机检项清单逐字相同(self, form):
        """返回清单逐字等于模块常量（漏一项 ⇒ 返回值比对即红，不靠"看起来跑过了"）。"""
        checked = form_clause_completeness(CONFIGS_DIR / f"{form}.yaml")
        assert checked == FORM_CLAUSE_CHECKS
        # 七项口径逐项在内（C11 的逐项表 + "不适用"显式声明面 + 三层未标定标注）
        for item in (
            "cadence",
            "window_semantics",
            "window_change_date",
            "channels",
            "attribution_date",
            "transfer",
            "runs",
            "not_applicable",
        ):
            assert item in checked, item

    @pytest.mark.parametrize(
        ("path", "token"),
        (
            ("calibration.period_days", "period_days"),
            ("calibration.window_semantics", "window_semantics"),
            ("calibration.window_semantics_change_date", "window_semantics_change_date"),
            ("promo.attribution_date_required_since", "attribution_date_required_since"),
            ("calibration.transfer.basis", "basis"),
            ("budget.runs.min_window_days", "min_window_days"),
            ("budget.runs.gap_tolerance_days", "gap_tolerance_days"),
        ),
    )
    def test_逐项删掉一个020口径键即拒绝启动并点名(self, tmp_path, path, token):
        document = _document("movie")
        _drop(document, path)
        with pytest.raises(PrecheckError) as exc:
            config_completeness(_write(tmp_path, document))
        message = str(exc.value)
        assert token in message, message
        # 段名也点名（缺项不得"静默取码内默认"）
        assert path.split(".")[0] in message, message

    def test_删掉evaluators段即拒绝启动并点名(self, tmp_path):
        document = _document("movie")
        del document["evaluators"]
        with pytest.raises(PrecheckError) as exc:
            config_completeness(_write(tmp_path, document))
        assert "evaluators" in str(exc.value)

    def test_不扩量纲_periods取值域一字不改(self):
        source = (REPO_ROOT / "core" / "calibration" / "periods.py").read_text(encoding="utf-8")
        assert re.search(r"^SUPPORTED_CADENCES = \(1, 7\)$", source, re.MULTILINE)
        assert SUPPORTED_CADENCES == (1, 7)


class TestCadence收口:
    """C11 机检断言②：取值域收口到唯一加载入口，越界即显式报错且不回落。"""

    @pytest.mark.parametrize("bad", [14, 0, 2, -1, "7", 1.5, True, None])
    def test_越界即报错且点名取值域(self, bad):
        section = {**_CALIBRATION_SECTION, "period_days": bad}
        with pytest.raises(CalibrationConfigError) as exc:
            CalibrationConfig.from_dict({"calibration": section})
        message = str(exc.value)
        assert "period_days" in message, message
        assert "(1, 7)" in message, message
        assert "不回落" in message, message

    @pytest.mark.parametrize("good", [1, 7])
    def test_取值域内的两档照常通过(self, good):
        section = {**_CALIBRATION_SECTION, "period_days": good}
        assert CalibrationConfig.from_dict({"calibration": section}).period_days == good

    @pytest.mark.parametrize("bad", [14, 0, 2, "7"])
    def test_预检同样越界即拒绝(self, tmp_path, bad):
        document = _document("movie")
        document["calibration"]["period_days"] = bad
        with pytest.raises(PrecheckError) as exc:
            config_completeness(_write(tmp_path, document))
        # 预检逐段点名（cadence 收口在 CalibrationConfig；promo 段的同源校验先命中同样点名该键）
        assert "period_days" in str(exc.value)

    def test_口径模型层的同源校验原样保留(self):
        """既有两处同取值域校验（漂移配置 / promo 配置）**原样保留**：同一口径的多个检查点。"""
        drift = (REPO_ROOT / "core" / "calibration" / "drift_config.py").read_text(encoding="utf-8")
        promo = (REPO_ROOT / "agents" / "promo" / "config.py").read_text(encoding="utf-8")
        for source in (drift, promo):
            assert "SUPPORTED_CADENCES" in source


class Test不适用必须显式声明:
    """C11："不适用"是**段内键（映射）**，只允许两处，理由非空且含「不适用」，双向无歧义。"""

    def test_只允许两处_取值域闭合(self):
        assert set(NOT_APPLICABLE_SITES) == {"calibration.transfer", "budget"}
        assert set(NOT_APPLICABLE_SITES["calibration.transfer"]) == {"source_forms", "target_forms"}
        assert set(NOT_APPLICABLE_SITES["budget"]) == {"channels"}

    def test_预算段不登记投放渠道却省略不适用即报错(self, tmp_path):
        document = _document("movie")  # movie 无 promo_platform 渠道 ⇒ 判为不适用
        del document["budget"]["not_applicable"]
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "budget" in str(exc.value) and "不适用" in str(exc.value)

    def test_不适用写成空串或null或空映射即报错(self, tmp_path):
        for bad in ("", "   ", None, {}):
            document = _document("movie")
            document["budget"]["not_applicable"] = {"channels": bad}
            with pytest.raises(PrecheckError):
                form_clause_completeness(_write(tmp_path, document))

    def test_理由不含不适用二字即报错(self, tmp_path):
        document = _document("movie")
        document["budget"]["not_applicable"] = {"channels": "本形态无投放渠道"}
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "不适用" in str(exc.value)

    def test_判为适用却声明不适用即报错(self, tmp_path):
        document = _document("shortdrama")  # 短剧态登记 promo_platform 渠道 ⇒ 判为适用
        document["budget"]["not_applicable"] = {"channels": "不适用：无投放"}
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "适用" in str(exc.value) and "not_applicable" in str(exc.value)

    def test_两处之外的段出现不适用即报错(self, tmp_path):
        for segment in ("promo", "visual", "pilot"):
            document = _document("movie")
            document[segment]["not_applicable"] = {"anything": "不适用：想省事"}
            with pytest.raises(PrecheckError) as exc:
                form_clause_completeness(_write(tmp_path, document))
            assert segment in str(exc.value)

    def test_不适用映射多出键即报错(self, tmp_path):
        document = _document("movie")
        document["budget"]["not_applicable"] = {
            "channels": "不适用：无投放",
            "tiers": "不适用：无档位",
        }
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "tiers" in str(exc.value)

    def test_迁移口径自锁且未显式声明不适用即报错(self, tmp_path):
        """C11 第 ⑥ 项：`source_forms ∪ target_forms ⊆ {该形态自身}` ⇒ **空声明 = 沉默失效**。"""
        document = _document("movie")
        document["calibration"]["transfer"]["source_forms"] = ["movie"]
        document["calibration"]["transfer"]["target_forms"] = ["movie"]
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "不适用" in str(exc.value)

    def test_迁移口径自锁但给出非空理由即通过(self, tmp_path):
        document = _document("movie")
        document["calibration"]["transfer"]["source_forms"] = ["movie"]
        document["calibration"]["transfer"]["target_forms"] = ["movie"]
        document["calibration"]["transfer"]["not_applicable"] = {
            "source_forms": "不适用：本形态不作为其它形态的迁移来源",
            "target_forms": "不适用：本形态不接收其它形态的迁移结论",
        }
        assert "transfer" in form_clause_completeness(_write(tmp_path, document))

    def test_迁移面六键缺任一即报错(self, tmp_path):
        for key in (
            "basis",
            "source_forms",
            "target_forms",
            "conditions",
            "storage",
            "adoption",
        ):
            document = _document("movie")
            _drop(document, f"calibration.transfer.{key}")
            with pytest.raises(PrecheckError) as exc:
                form_clause_completeness(_write(tmp_path, document))
            assert key in str(exc.value)

    def test_段级标注键不参与插件清单解析(self, tmp_path):
        """C11 §4：`evaluators.note` 位于 `plugins` 子树**之上** ⇒ 二者层级不同、不碰撞。"""
        document = _document("movie")
        document["evaluators"]["note"] = "未标定：插件事项说明"
        path = _write(tmp_path, document)
        checked = form_clause_completeness(path)
        assert "channels" in checked


class Test三层未标定标注:
    """C11 的三层标注（形态层 / 段层 / 产物层）与 `calibration.cadence_note` 的规则。"""

    def test_形态层取值域(self):
        assert REHEARSAL_STATUSES == ("declared", "unstandardized")

    def test_形态层取值非法即报错(self, tmp_path):
        document = _document("movie")
        document["pilot"]["rehearsal"]["status"] = "calibrated"
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "rehearsal.status" in str(exc.value)

    @pytest.mark.parametrize("segment", UNCALIBRATED_NOTE_SEGMENTS)
    def test_段层缺任一段即报错并点名段名(self, tmp_path, segment):
        document = _document("movie")
        _state_uncailbrated(document)
        del document[segment]["note"]
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert segment in str(exc.value)

    @pytest.mark.parametrize("bad", ["   ", "待运营给定", None, 5])
    def test_段层note非空且必须含未标定字样(self, tmp_path, bad):
        document = _document("movie")
        _state_uncailbrated(document)
        for segment in UNCALIBRATED_NOTE_SEGMENTS:
            document[segment]["note"] = bad
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "未标定" in str(exc.value)

    def test_未标定形态缺cadence_note即报错(self, tmp_path):
        document = _document("movie")
        _state_uncailbrated(document)
        del document["calibration"]["cadence_note"]
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "cadence_note" in str(exc.value)

    @pytest.mark.parametrize("bad", ["档位近似业务节律", "未标定：待运营给定", "   ", None])
    def test_cadence_note必须同时含近似与未标定(self, tmp_path, bad):
        document = _document("movie")
        _state_uncailbrated(document)
        document["calibration"]["cadence_note"] = bad
        with pytest.raises(PrecheckError):
            form_clause_completeness(_write(tmp_path, document))

    def test_非未标定形态给出cadence_note不得含近似(self, tmp_path):
        document = _document("movie")  # movie 的 rehearsal.status == declared
        document["calibration"]["cadence_note"] = "按周近似真实节律"
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert "近似" in str(exc.value)

    def test_非未标定形态给出中性cadence_note即通过(self, tmp_path):
        document = _document("movie")
        document["calibration"]["cadence_note"] = "周级外环取自形态声明的 period_days"
        assert "cadence" in form_clause_completeness(_write(tmp_path, document))

    @pytest.mark.parametrize("word", CALIBRATION_CLAIM_WORDS)
    def test_cadence_note不得宣称已达标(self, tmp_path, word):
        document = _document("movie")
        _state_uncailbrated(document)
        document["calibration"]["cadence_note"] = f"近似真实节律；未标定；{word}"
        with pytest.raises(PrecheckError) as exc:
            form_clause_completeness(_write(tmp_path, document))
        assert word in str(exc.value)

    def test_禁列取值域固定(self):
        assert CALIBRATION_CLAIM_WORDS == ("已标定", "已达标", "已投产")

    def test_以模拟冒充标定次数恒零(self, tmp_path):
        """三层标注齐备的未标定形态**才**通过；把"未标定"换成正向结论即红。"""
        document = _document("movie")
        _state_uncailbrated(document)
        assert form_clause_completeness(_write(tmp_path, document))
        for segment in UNCALIBRATED_NOTE_SEGMENTS:
            document[segment]["note"] = f"{segment} 已标定（模拟回流）"
        with pytest.raises(PrecheckError):
            form_clause_completeness(_write(tmp_path, document))

    def test_产物层固定字段常量非空且点名业务侧输入(self):
        from ops.form_onboarding import UNCALIBRATED_REASON

        assert UNCALIBRATED_REASON.strip()
        assert "受众 / 指标口径 / 素材规格 / 预算档属业务侧输入，未给定" in UNCALIBRATED_REASON


class Test既有两形态取值零改动:
    """T2153：读**原始文件**逐字节比对；新增用例一律用临时文件注入，不改既有配置。"""

    @pytest.mark.parametrize(
        ("form", "fragment"),
        (
            ("movie", "  period_days: 7 # 校准周期（天）\n"),
            ("shortdrama", "  period_days: 1 # 校准周期（天；日级外环）\n"),
        ),
    )
    def test_period_days逐字节不变(self, form, fragment):
        text = (CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8")
        assert text.count(fragment) == 1, f"{form} 的 period_days 取值行被改动：{fragment!r}"

    @pytest.mark.parametrize(
        ("form", "fragment"),
        (
            ("movie", "    min_window_days: 7 # 覆盖下限（≥1 周里程碑）\n"),
            (
                "shortdrama",
                "    min_window_days: 14 # 覆盖下限（= 立项书 G4 验收原文"
                '"短剧线真实数据回流 ≥2 周"，'
                "**不是发明数字**）\n",
            ),
            (
                "movie",
                "    gap_tolerance_days: 0 # 断档容差（0 = 不容断档；缺口如实列出、不插值）\n",
            ),
            (
                "shortdrama",
                "    gap_tolerance_days: 0 # 断档容差（0 = 不容断档；缺口如实列出、不插值）\n",
            ),
        ),
    )
    def test_运行窗口两键逐字节不变(self, form, fragment):
        text = (CONFIGS_DIR / f"{form}.yaml").read_text(encoding="utf-8")
        assert text.count(fragment) == 1, f"{form} 的运行窗口取值行被改动：{fragment!r}"

    def test_既有夹具取值仍在取值域内(self):
        """T2153②：既有夹具的 `period_days` 取值只有 `1` / `7` ⇒ cadence 收口后**不变红**。"""
        source = (REPO_ROOT / "tests" / "unit" / "test_calibration_config.py").read_text(
            encoding="utf-8"
        )
        values = [int(match) for match in re.findall(r'"period_days": (\d+)', source)]
        assert values, "既有夹具的 period_days 取值未被识别（本断言不得空跑）"
        assert set(values) <= set(SUPPORTED_CADENCES), values

    def test_新增用例只改临时副本(self, tmp_path):
        """越界取值一律用**临时文件**注入：跑完既有两形态配置字节不变。"""
        before = {form: (CONFIGS_DIR / f"{form}.yaml").read_bytes() for form in FORMS}
        document = _document("movie")
        document["calibration"]["period_days"] = 14
        with pytest.raises(PrecheckError):
            form_clause_completeness(_write(tmp_path, document))
        after = {form: (CONFIGS_DIR / f"{form}.yaml").read_bytes() for form in FORMS}
        assert before == after

    def test_两形态真实配置逐字节可复读(self):
        """配置集合由派生面给出（新形态自动纳入）；形态 id 与文件名 stem 一致。"""
        for form in FORMS:
            document = _document(form)
            assert document["form"] == form
            assert copy.deepcopy(document) == document
