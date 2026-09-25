"""渠道命名空间与兼容读单测（功能 020 / 契约 C11 / C12 / C13 / C16 / C18，T2012）。

覆盖面一句话：**019 的门禁机制一个字没改，只是把"额度与账本按渠道分派"补齐**——

- C11 命名空间：`budget.channels.<id>.tiers.<环节>` 是唯一形状（缺 `tiers`/缺 `adapter` 即报错），
  **旧扁平 `tiers` 仍可读**（恰好一个渠道 ⇒ 显式归入、`tiers_shape == "legacy_flat"`、
  `notes` 留痕；多渠道或新旧并存 ⇒ 报错），**静默归并恒 0**；
- C12 分派：`declared_channels` / `channel_for_adapter` / `tiers_of` / `tier_of`；
  装配快照只含**本渠道**档位；跨渠道串用与 `channel_mismatch` 同源被拒；
- C13/C16：`--channel` 三态语义（声明值 0 / 未声明值 2）在**多渠道配置**下同样成立，
  新增第 8 条只读子命令 `channels`；**既有七条子命令一字不改**；
- C16/FR-011 凭证矩阵：只报 `set`/`length`、**绝不回显值**、声明真实而缺凭证 ⇒ 装配期拒绝启动；
- T2052（U-04）媒体渠道账单面：`import-bill --channel media` → `reconcile --channel media`
  **复用 019 的导入与对账**（不新造第二套），六类差异齐备、未解释项 100% 告警、报告必引 `bill_id`；
- T2070 两形态装配通过，**movie 路径不因投放渠道或其凭证而失败**；
- C18 诚实分层：`source` 只能由装配面声明、覆盖只计 `real`、`evidence_claim` 取值域二元素。

零真实花费、零外部网络、零凭证：全部走 Mock 平台 / 夹具账单 / 临时目录。
"""

import ast
import copy
import importlib
import json
import re
from pathlib import Path

import pytest
import yaml

from core.billing.bill import bill_path
from core.billing.budget import (
    AlertLog,
    BudgetConfig,
    BudgetConfigError,
    BudgetRefusedError,
    FileLedger,
    SpendGuard,
    alerts_path,
    assemble_guard,
    channel_dir,
    channel_for_adapter,
    declared_channels,
    ledger_path,
    tier_of,
    tiers_of,
)
from core.billing.reconcile import CLASSIFICATIONS, UNCLASSIFIED, load_report
from core.billing.runlog import RecordingChannelCall, RunLogError, load_run, run_path

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMS = ("movie", "shortdrama")
LLM_ADAPTER = "pilot_llm"  # 019 既有装配引用（原样保留、不重命名）
PROMO_ADAPTER = "promo_platform"  # C11 权威定名：投放渠道的装配引用
MEDIA_CHANNEL = "media"
MOMENT = __import__("datetime").datetime(2026, 9, 25, 3, 0, tzinfo=__import__("datetime").UTC)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


class _Request:
    """门禁请求的最小结构化替身（`SpendRequest` 协议同形：三个只读属性）。"""

    def __init__(self, channel_id: str, stage: str, estimated_usd: float) -> None:
        self.channel_id = channel_id
        self.stage = stage
        self.estimated_usd = estimated_usd


def _section(form: str) -> dict:
    return yaml.safe_load((REPO_ROOT / "configs" / f"{form}.yaml").read_text(encoding="utf-8"))


def _key_of(channels: dict, adapter: str) -> str:
    """按**装配引用**定位渠道键（不在用例里写死渠道 id）：入参 = `budget.channels` 映射。"""
    return next(key for key, spec in channels.items() if str(spec.get("adapter")) == adapter)


def _write(payload: dict, tmp_path: Path, name: str = "form.yaml") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target


def _config(
    tmp_path: Path,
    form: str = "shortdrama",
    *,
    overrides=None,
    name: str = "form.yaml",
) -> Path:
    """形态配置派生：账本根落 tmp（仓库零污染），可按需覆盖 `pilot.overrides`。"""
    payload = copy.deepcopy(_section(form))
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    if overrides:
        payload.setdefault("pilot", {})["overrides"] = dict(overrides)
    return _write(payload, tmp_path, name)


def _cli(argv, capsys):
    module = importlib.import_module("ops.billing")
    code = module.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


def _guard(cfg: BudgetConfig, channel_id: str, root: Path) -> SpendGuard:
    return SpendGuard(
        cfg=cfg,
        channel_id=channel_id,
        ledger=FileLedger(ledger_path(root, channel_id), timeout_seconds=1.0),
        alerts=AlertLog(alerts_path(root, channel_id)),
        window_context={"period": "2026-09", "run": "2026-09-25"},
        clock=lambda: MOMENT,
    )


def _legacy_section(section: dict, adapter: str) -> dict:
    """把某个渠道的档位以**旧扁平**形态暴露（`channels` 只剩该渠道、顶层 `tiers` 承接档位）。"""
    flat = copy.deepcopy(section)
    key = _key_of(flat["channels"], adapter)
    channel = flat["channels"][key]
    flat["tiers"] = channel.pop("tiers")
    flat["channels"] = {key: channel}
    return flat


def _agent_configs(config_path: Path):
    """链路后端装配所需的形态配置聚合（与 `agents/pilot/stages.build_runtime` 同源）。"""
    from agents.pilot import stages as stages_module

    return stages_module.AgentConfigs(
        dev=stages_module.DevConfig.from_yaml(config_path),
        screenplay=stages_module.ScreenplayConfig.from_yaml(config_path),
        storyboard=stages_module.StoryboardConfig.from_yaml(config_path),
        visual=stages_module.VisualConfig.from_yaml(config_path),
        sound=stages_module.SoundConfig.from_yaml(config_path),
        editing=stages_module.EditingConfig.from_yaml(config_path),
        promo=stages_module.PromoConfig.from_yaml(config_path),
    )


# ---------------------------------------------------------------------------
# C11：命名空间、旧形状归一、静默归并恒 0
# ---------------------------------------------------------------------------


class TestC11渠道命名空间:
    def test_每渠道必带非空_tiers_与非空_adapter(self, tmp_path):
        for form in FORMS:
            section = _section(form)["budget"]
            assert section["channels"]
            for key, spec in section["channels"].items():
                assert spec["tiers"], (form, key)
                assert str(spec["adapter"]).strip(), (form, key)
        # 缺 tiers ⇒ BudgetConfigError（不取码内默认）
        base = _section("movie")["budget"]
        llm = _key_of(base["channels"], LLM_ADAPTER)
        for mutate in (
            lambda s: s["channels"][llm].pop("tiers"),
            lambda s: s["channels"][llm].update({"tiers": {}}),
            lambda s: s["channels"][llm].pop("adapter"),
            lambda s: s["channels"][llm].update({"adapter": "  "}),
        ):
            section = copy.deepcopy(base)
            mutate(section)
            with pytest.raises(BudgetConfigError):
                BudgetConfig.from_dict({"budget": section})
        assert not list(tmp_path.iterdir())  # 解析期零落盘

    def test_tiers_shape_可追溯且旧形状显式归一(self):
        for form in FORMS:
            cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / f"{form}.yaml")
            assert cfg.tiers_shape == "channels"
        # 旧扁平形状（019 现形状）：恰好一个渠道 ⇒ 显式归入该渠道 + notes 留痕 + tiers_shape
        flat = _legacy_section(_section("movie")["budget"], LLM_ADAPTER)
        cfg = BudgetConfig.from_dict({"budget": flat})
        assert cfg.tiers_shape == "legacy_flat"
        key = _key_of(flat["channels"], LLM_ADAPTER)
        assert any(key in note and "legacy_flat" in note for note in cfg.notes)
        assert set(cfg.tiers_of(key)) == set(
            BudgetConfig.from_yaml(REPO_ROOT / "configs" / "movie.yaml").tiers_of("llm")
        )

    def test_静默归并恒零(self):
        """多渠道下访问兼容视图 ⇒ 报错（不返回任一渠道档位、不取首个）。"""
        cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        assert len(declared_channels(cfg)) >= 2
        with pytest.raises(BudgetConfigError):
            _ = cfg.tiers
        with pytest.raises(BudgetConfigError):
            cfg.tier("screenplay")
        # 新旧两处**同时**出现（单渠道下也报错：两个来源，不静默择一）
        section = copy.deepcopy(_section("movie")["budget"])
        section["tiers"] = {"screenplay": {"limit_usd": 1.0, "window": {"kind": "day"}}}
        with pytest.raises(BudgetConfigError, match="同时出现"):
            BudgetConfig.from_dict({"budget": section})

    def test_旧形状与新形状同场景结果逐字段一致(self, tmp_path):
        """019 现形状（单渠道 + 扁平 `tiers`）与新形状在同一场景下**结果等价**。"""
        base = copy.deepcopy(_section("movie")["budget"])
        new_section = copy.deepcopy(base)
        new_section["ledger"]["root"] = str(tmp_path / "new" / "billing")
        old_section = _legacy_section(base, LLM_ADAPTER)
        old_section["ledger"]["root"] = str(tmp_path / "old" / "billing")

        results = []
        for tag, section in (("new", new_section), ("old", old_section)):
            cfg = BudgetConfig.from_dict({"budget": section})
            key = declared_channels(cfg)[0].channel_id
            root = cfg.ledger_root()
            guard = _guard(cfg, key, root)
            guard.check(_Request(key, "screenplay", 0.25)).settle(0.2)
            payload = FileLedger(ledger_path(root, key), timeout_seconds=1.0).read()
            snapshot = guard.snapshot()
            snapshot.pop("tiers_shape")  # 形状本身**必须**留下痕迹（其余逐字段一致）
            results.append(
                {
                    "key": key,
                    "tiers": sorted(cfg.tiers_of(key)),
                    "ledger": payload["tiers"],
                    "snapshot": snapshot,
                    "alerts": AlertLog(alerts_path(root, key)).entries(),
                }
            )
            assert tag  # 两形状各跑一遍
        assert results[0] == results[1]

    def test_分派确定性(self):
        cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / "shortdrama.yaml")
        channels = declared_channels(cfg)
        assert [spec.channel_id for spec in channels] == [spec.channel_id for spec in channels]
        assert {spec.adapter for spec in channels} == {LLM_ADAPTER, PROMO_ADAPTER}
        assert (
            channel_for_adapter(cfg, LLM_ADAPTER).channel_id
            != channel_for_adapter(cfg, PROMO_ADAPTER).channel_id
        )
        with pytest.raises(BudgetConfigError, match="未在 budget.channels 登记"):
            channel_for_adapter(cfg, "ghost_adapter")
        with pytest.raises(BudgetConfigError, match="未在 budget.tiers 声明"):
            tier_of(cfg, channel_for_adapter(cfg, LLM_ADAPTER).channel_id, "ghost_tier")
        with pytest.raises(BudgetConfigError, match="未在 budget.channels 登记"):
            tiers_of(cfg, "ghost_channel")
        # 同一个装配引用声明在两个渠道上 ⇒ 歧义即报错（不猜用哪个账本）
        section = copy.deepcopy(_section("movie")["budget"])
        first = _key_of(section["channels"], LLM_ADAPTER)
        template = section["channels"][first]
        section["channels"] = {
            first: template,
            "channel-iso": copy.deepcopy(template),
        }
        ambiguous = BudgetConfig.from_dict({"budget": section})
        with pytest.raises(BudgetConfigError, match="歧义"):
            channel_for_adapter(ambiguous, LLM_ADAPTER)

    def test_声明顺序约定_首个渠道是_LLM(self):
        for form in FORMS:
            cfg = BudgetConfig.from_yaml(REPO_ROOT / "configs" / f"{form}.yaml")
            assert declared_channels(cfg)[0].adapter == LLM_ADAPTER

    def test_跨渠道串用恒零(self, tmp_path):
        config = _config(tmp_path, "shortdrama")
        cfg = BudgetConfig.from_yaml(config)
        root = cfg.ledger_root()
        llm = channel_for_adapter(cfg, LLM_ADAPTER).channel_id
        media = channel_for_adapter(cfg, PROMO_ADAPTER).channel_id
        assert llm != media
        _guard(cfg, llm, root).check(_Request(llm, "screenplay", 0.1)).settle(0.1)
        _guard(cfg, media, root).check(_Request(media, "promo_launch", 0.1)).settle(0.1)
        llm_ledger = FileLedger(ledger_path(root, llm), timeout_seconds=1.0).read()
        media_ledger = FileLedger(ledger_path(root, media), timeout_seconds=1.0).read()
        assert set(llm_ledger["tiers"]) == {"screenplay"}
        assert set(media_ledger["tiers"]) == {"promo_launch"}  # 投放档**不在** LLM 账本里
        assert channel_dir(root, llm) != channel_dir(root, media)
        assert run_path(root, llm, "2026-09-25") != run_path(root, media, "2026-09-25")
        # 跨渠道请求打到另一渠道的守卫 ⇒ `channel_mismatch`（019 既有拒绝保留）
        with pytest.raises(BudgetRefusedError) as excinfo:
            _guard(cfg, llm, root).check(_Request(media, "promo_launch", 0.1))
        assert excinfo.value.reason == "channel_mismatch"
        kinds = [entry["kind"] for entry in AlertLog(alerts_path(root, llm)).entries()]
        assert kinds == ["budget_refused"]

    def test_装配快照只含本渠道档位(self, tmp_path):
        config = _config(tmp_path, "shortdrama")
        cfg = BudgetConfig.from_yaml(config)
        media = channel_for_adapter(cfg, PROMO_ADAPTER).channel_id
        snapshot = _guard(cfg, media, cfg.ledger_root()).snapshot()
        assert snapshot["channel_id"] == media
        assert snapshot["adapter"] == PROMO_ADAPTER
        assert set(snapshot["tiers"]) == set(tiers_of(cfg, media)) == {"promo_launch"}
        assert snapshot["tiers_shape"] == "channels"


# ---------------------------------------------------------------------------
# C12 / C16：CLI 三态语义、八子命令、代码侧 adapter 与配置一致
# ---------------------------------------------------------------------------


class TestC12CLI与装配引用:
    def test_多渠道配置下_tiers_子命令仍退出零(self, capsys, tmp_path):
        """C13 表第 5 行的验收点：退役单渠道硬拒绝后，多渠道配置下 `tiers` 也必须 0。"""
        config = _config(tmp_path, "shortdrama")
        for channel_id in ("llm", MEDIA_CHANNEL):
            code, out = _cli(["tiers", "--channel", channel_id, "--config", str(config)], capsys)
            assert code == 0, out
            assert out["channel_id"] == channel_id
            assert out["tiers"]
        _, llm_rows = _cli(["tiers", "--channel", "llm", "--config", str(config)], capsys)
        media_code, media_out = _cli(
            ["tiers", "--channel", MEDIA_CHANNEL, "--config", str(config)], capsys
        )
        assert media_code == 0
        assert {row["tier_id"] for row in media_out["tiers"]} == {"promo_launch"}
        assert "promo_launch" not in {row["tier_id"] for row in llm_rows["tiers"]}

    def test_未声明渠道退出码二且文案保留不一致(self, capsys, tmp_path):
        """C13 表第 6/12 行（**硬要求**）：未声明的渠道不得开工，退出码 2 + 文案含「不一致」。"""
        config = _config(tmp_path, "shortdrama")
        for channel_id in ("nope", "ghost"):
            code, out = _cli(["tiers", "--channel", channel_id, "--config", str(config)], capsys)
            assert code == 2, out
            assert "不一致" in out["error"]

    def test_channels_子命令只读且含凭证矩阵(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("PROMO_PLATFORM_API_KEY", "secret-value-不该出现")
        monkeypatch.delenv("PROMO_PLATFORM_BASE_URL", raising=False)
        config = _config(tmp_path, "shortdrama")
        code, out = _cli(["channels", "--config", str(config)], capsys)
        assert code == 0, out
        assert out["declared_channels"] == ["llm", MEDIA_CHANNEL]
        assert out["tiers_shape"] == "channels"
        rows = {row["channel_id"]: row for row in out["channels"]}
        assert rows["llm"]["adapter"] == LLM_ADAPTER
        assert rows[MEDIA_CHANNEL]["adapter"] == PROMO_ADAPTER
        assert set(rows[MEDIA_CHANNEL]["credentials"]["credential_envs"]) == {
            "PROMO_PLATFORM_BASE_URL",
            "PROMO_PLATFORM_API_KEY",
        }
        for row in out["channels"]:
            for name, state in row["credentials"]["credential_envs"].items():
                assert set(state) == {"set", "length"}, name  # 只报 set/length
            assert row["ledger_path"].endswith(f"{row['channel_id']}/ledger.json")
            assert row["alerts_path"].endswith(f"{row['channel_id']}/alerts.jsonl")
        rendered = json.dumps(out, ensure_ascii=False)
        assert "secret-value" not in rendered  # **绝不回显凭证值**
        # 未登记 media 的形态：矩阵里**没有**投放行，也不触发投放凭证判定
        movie = _config(tmp_path, "movie", name="movie.yaml")
        code, out = _cli(["channels", "--config", str(movie)], capsys)
        assert code == 0, out
        assert out["declared_channels"] == ["llm"]
        assert all(row["adapter"] != PROMO_ADAPTER for row in out["channels"])

    def test_八子命令_help_均退出零(self, capsys):
        module = importlib.import_module("ops.billing")
        names = (
            "tiers",
            "calibrate",
            "raise-tier",
            "import-bill",
            "reconcile",
            "alert-check",
            "runs",
            "channels",
        )
        for name in names:
            with pytest.raises(SystemExit) as exit_info:
                module.main([name, "--help"])
            assert exit_info.value.code == 0
        capsys.readouterr()
        assert "八条齐备" in (module.__doc__ or "")

    def test_代码侧装配引用与配置一致(self):
        """C11：代码里传出的 adapter id 必须能在两形态 `budget.channels.*.adapter` 里逐字找到。"""
        declared = {
            str(spec["adapter"])
            for form in FORMS
            for spec in _section(form)["budget"]["channels"].values()
        }
        passed = _adapter_ids_passed_to_resolver()
        assert passed, "扫描面为空（装配点不再经 channel_for_adapter？）"
        assert passed <= declared, f"代码侧装配引用未登记配置：{sorted(passed - declared)}"
        assert passed == {LLM_ADAPTER, PROMO_ADAPTER}


def _adapter_ids_passed_to_resolver() -> set[str]:
    """扫描 `agents/` 与 `ops/`：`channel_for_adapter(cfg, <adapter id>)` 的第二个实参取值。

    字面量直接取；模块级常量（含 `from … import CONST` 的跨模块引用）按**定义处**的取值解析——
    否则"装配入口改成常量"就会让本断言变成空跑。
    """
    trees: list[tuple[Path, ast.Module]] = []
    constants: dict[str, str] = {}
    for root in ("agents", "ops"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            trees.append((path, tree))
            for node in tree.body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                            constants[target.id] = node.value.value
    found: set[str] = set()
    for _path, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "channel_for_adapter" or len(node.args) < 2:
                continue
            value = node.args[1]
            if isinstance(value, ast.Constant):
                found.add(str(value.value))
            elif isinstance(value, ast.Name):
                found.add(str(constants.get(value.id, value.id)))
    return found


# ---------------------------------------------------------------------------
# C16 / FR-011：凭证矩阵与装配期拒绝（零落树零扣费、不回落模拟）
# ---------------------------------------------------------------------------


class TestC16凭证矩阵与装配期拒绝:
    def test_矩阵按形态声明生成_只报_set_length(self, tmp_path):
        from ops.check_credentials import channel_matrix

        movie = _config(tmp_path, "movie", name="movie.yaml")
        short = _config(tmp_path, "shortdrama", name="shortdrama.yaml")
        movie_rows = {
            row["channel_id"]: row for row in channel_matrix(movie, environ={})["channels"]
        }
        assert set(movie_rows) == {"llm"}  # 未声明投放渠道 ⇒ 不生成投放行、不触发其判定
        assert movie_rows["llm"]["backend"] == "mock" and movie_rows["llm"]["ready"] is True
        rows = {row["channel_id"]: row for row in channel_matrix(short, environ={})["channels"]}
        assert set(rows) == {"llm", MEDIA_CHANNEL}
        media = rows[MEDIA_CHANNEL]
        assert media["adapter"] == PROMO_ADAPTER
        assert media["backend"] == "simulated" and media["ready"] is True  # 声明模拟 ⇒ 零凭证可跑
        for row in rows.values():
            for state in row["credential_envs"].values():
                assert set(state) == {"set", "length"}
        # 声明真实而凭证缺失 ⇒ ready=false + 逐项点名（装配期拒绝启动、不回落模拟）
        real = channel_matrix(
            _config(tmp_path, "shortdrama", overrides={"promo": "http"}, name="real.yaml"),
            environ={},
        )
        real_media = next(row for row in real["channels"] if row["channel_id"] == MEDIA_CHANNEL)
        assert real_media["declared_real"] is True and real_media["ready"] is False
        assert real_media["missing"] == ["PROMO_PLATFORM_API_KEY", "PROMO_PLATFORM_BASE_URL"]
        assert "装配期显式拒绝启动" in real_media["refusal"]

    def test_声明真实而缺凭证即装配期拒绝且零落树(self, tmp_path, monkeypatch):
        from agents.pilot.backends import BackendAssemblyError, build_backends

        for name in ("PROMO_PLATFORM_BASE_URL", "PROMO_PLATFORM_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        config = _config(tmp_path, "shortdrama", overrides={"promo": "http"})
        with pytest.raises(BackendAssemblyError) as excinfo:
            build_backends(_agent_configs(config), config)
        message = str(excinfo.value)
        assert "PROMO_PLATFORM_BASE_URL" in message or "PROMO_PLATFORM_API_KEY" in message
        assert "不静默回落模拟" in message or "不回落" in message
        assert not (tmp_path / "billing").exists()  # 零落树
        assert not ledger_path(tmp_path / "billing", MEDIA_CHANNEL).exists()  # 零扣费

    def test_movie_声明真实投放即显式拒绝并点名未登记(self, tmp_path, monkeypatch):
        from agents.pilot.backends import BackendAssemblyError, build_backends

        monkeypatch.setenv("PROMO_PLATFORM_API_KEY", "k")
        monkeypatch.setenv("PROMO_PLATFORM_BASE_URL", "https://example.invalid")
        config = _config(tmp_path, "movie", overrides={"promo": "http"})
        with pytest.raises(BackendAssemblyError, match="该形态未登记投放渠道"):
            build_backends(_agent_configs(config), config)
        assert not (tmp_path / "billing").exists()

    def test_两形态装配通过_movie_零投放判定(self, tmp_path):
        """T2070：两形态默认全模拟链路都能装配；movie 路径不因投放渠道或其凭证失败。"""
        from agents.pilot.backends import build_backends

        for form in FORMS:
            config = _config(tmp_path, form, name=f"{form}-assemble.yaml")
            backends = build_backends(_agent_configs(config), config)
            assert backends.gateway is not None
            assert backends.promo is not None
            assert not isinstance(backends.promo, RecordingChannelCall)  # 模拟面不包门禁
        assert not (tmp_path / "billing").exists()  # 装配不落树（模拟面零账本）


# ---------------------------------------------------------------------------
# T2052（U-04）：媒体渠道账单导入与对账（复用 019，不新造第二套）
# ---------------------------------------------------------------------------


class Test投放渠道账单对账:
    def _media_config(self, tmp_path, fixture) -> Path:
        config = _config(tmp_path, "shortdrama", name="media-bill.yaml")
        source = tmp_path / "media-bill.csv"
        source.write_text(fixture["text"], encoding="utf-8")
        return config

    def test_导入与对账六类齐备且未解释项告警(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        config = self._media_config(tmp_path, billing_bill_fixture)
        source = tmp_path / "media-bill.csv"
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        period = billing_bill_fixture["period"]

        code, out = _cli(
            [
                "import-bill",
                "--channel",
                MEDIA_CHANNEL,
                "--file",
                str(source),
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--period",
                period,
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code == 0, out
        bill_file = bill_path(tmp_path / "billing", MEDIA_CHANNEL, out["bill_id"])
        assert bill_file.is_file() and bill_file.parent.name == "bills"
        # 同批次重复导入 ⇒ 拒绝（批次幂等、零部分导入）
        code, out = _cli(
            [
                "import-bill",
                "--channel",
                MEDIA_CHANNEL,
                "--file",
                str(source),
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--period",
                period,
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code != 0 and "批次幂等" in out["error"]

        code, out = _cli(
            [
                "reconcile",
                "--channel",
                MEDIA_CHANNEL,
                "--period",
                period,
                "--bill-id",
                billing_bill_fixture["bill_id"],
                "--gateway-report",
                str(gateway),
                "--config",
                str(config),
            ],
            capsys,
        )
        assert out["unexplained"], out  # 未解释项非空 ⇒ 不可解释项 100% 告警
        assert code == 1 and out["alerts"]  # 有告警 ⇒ 非零退出
        classes = {item["classification"] for item in out["items"]}
        assert classes - {UNCLASSIFIED} == set(CLASSIFICATIONS)  # 六类逐项齐备
        assert UNCLASSIFIED in classes
        assert [ref["bill_id"] for ref in out["bill_refs"]] == [billing_bill_fixture["bill_id"]]
        # 报告落渠道目录且**引用账单批次**（网关记账不得自证）
        payload = load_report(period, channel_id=MEDIA_CHANNEL, root=tmp_path / "billing")
        assert [ref["bill_id"] for ref in payload["bill_refs"]] == [billing_bill_fixture["bill_id"]]
        alert_kinds = [
            entry["kind"]
            for entry in AlertLog(alerts_path(tmp_path / "billing", MEDIA_CHANNEL)).entries()
        ]
        assert "unexplained_delta" in alert_kinds
        assert not bill_path(tmp_path / "billing", "llm", billing_bill_fixture["bill_id"]).exists()

    def test_未识别格式零落盘且无账单拒绝产出(
        self, capsys, tmp_path, billing_bill_fixture, billing_gateway_ledger_fixture
    ):
        payload = copy.deepcopy(_section("shortdrama"))
        payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        media = _key_of(payload["budget"]["channels"], PROMO_ADAPTER)
        payload["budget"]["channels"][media]["bill"]["format"] = "vendor_only"
        config = _write(payload, tmp_path, "vendor-only.yaml")
        source = tmp_path / "media-bill.csv"
        source.write_text(billing_bill_fixture["text"], encoding="utf-8")
        code, out = _cli(
            [
                "import-bill",
                "--channel",
                MEDIA_CHANNEL,
                "--file",
                str(source),
                "--bill-id",
                "media-x",
                "--period",
                billing_bill_fixture["period"],
                "--config",
                str(config),
            ],
            capsys,
        )
        assert code != 0 and "未注册" in out["error"]
        assert not bill_path(tmp_path / "billing", MEDIA_CHANNEL, "media-x").exists()  # 零落盘

        # 账单缺失 ⇒ 拒绝产出（不产"零差异"报告）
        gateway = tmp_path / "gateway.json"
        gateway.write_text(json.dumps(billing_gateway_ledger_fixture), encoding="utf-8")
        good = _config(tmp_path, "shortdrama", name="media-good.yaml")
        code, out = _cli(
            [
                "reconcile",
                "--channel",
                MEDIA_CHANNEL,
                "--period",
                billing_bill_fixture["period"],
                "--bill-id",
                "ghost",
                "--gateway-report",
                str(gateway),
                "--config",
                str(good),
            ],
            capsys,
        )
        assert code == 1 and "拒绝产出" in out["error"]


# ---------------------------------------------------------------------------
# C18：诚实分层（断言本体）
# ---------------------------------------------------------------------------


class TestC18诚实分层:
    def test_source_只能由装配面声明(self, tmp_path):
        config = _config(tmp_path, "shortdrama")
        cfg = BudgetConfig.from_yaml(config)
        media = channel_for_adapter(cfg, PROMO_ADAPTER).channel_id
        assembly = assemble_guard(config, channel_id=media)
        adapter = _StubAdapter()
        with pytest.raises(RunLogError, match="运行来源"):
            RecordingChannelCall(adapter, assembly=assembly, stage="promo_launch", source="made_up")
        with pytest.raises(RunLogError, match="fallback_reason"):
            RecordingChannelCall(
                adapter, assembly=assembly, stage="promo_launch", source="fallback"
            )
        # 装配面显式声明 ⇒ 运行记录如实标注（不按"跑通了"推断）
        for source in ("real", "simulated"):
            call = RecordingChannelCall(
                _StubAdapter(), assembly=assembly, stage="promo_launch", source=source
            )
            campaign = call.create_campaign(_material(), 0.1, idempotency_key="k")
            assert campaign.spent_usd == 0.05
            entry = load_run("2026-09-25", channel_id=media, root=cfg.ledger_root())["entries"][-1]
            assert entry["source"] == source and entry["result"] == "ok"

    def test_覆盖只计_real_且结论文案取值域二元素(self, tmp_path):
        from agents.promo.daily import (
            EVIDENCE_MECHANISM_READY,
            EVIDENCE_REAL_MET,
            daily_coverage,
        )
        from tests.unit.test_promo_daily_ingest import _daily_engine, _Seeder

        assert EVIDENCE_MECHANISM_READY == "mechanism_ready_real_feedback_pending"
        assert EVIDENCE_REAL_MET == "real_feedback_met"
        days = [f"2026-09-{day:02d}" for day in range(1, 16)]
        engine = _daily_engine()
        _Seeder.seed(engine, days[:2])  # 真实两天
        _Seeder.seed(engine, days[2:], source="simulated", campaign_prefix="sim")
        coverage = daily_coverage(
            engine, end="2026-09-15", min_window_days=14, gap_tolerance_days=0, period_days=1
        )
        assert coverage["covered_days"] == 2  # 模拟日**不计入**真实覆盖
        assert coverage["covered_dates"] == days[:2]
        assert coverage["meets"] is False
        assert coverage["evidence_claim"] in {EVIDENCE_MECHANISM_READY, EVIDENCE_REAL_MET}
        assert coverage["evidence_claim"] == EVIDENCE_MECHANISM_READY

    def test_拒绝即零调用零入账且来源记_refused(self, tmp_path):
        config = copy.deepcopy(_section("shortdrama"))
        config["budget"]["ledger"]["root"] = str(tmp_path / "billing")
        media = _key_of(config["budget"]["channels"], PROMO_ADAPTER)
        config["budget"]["channels"][media]["tiers"]["promo_launch"]["limit_usd"] = 1e-6
        path = _write(config, tmp_path, "tiny-media.yaml")
        cfg = BudgetConfig.from_yaml(path)
        assembly = assemble_guard(path, channel_id=media)
        adapter = _StubAdapter()
        call = RecordingChannelCall(adapter, assembly=assembly, stage="promo_launch", source="real")
        with pytest.raises(BudgetRefusedError) as excinfo:
            call.create_campaign(_material(), 1.0, idempotency_key="k")
        assert excinfo.value.reason == "over_limit"
        assert adapter.calls == 0  # 平台调用 0 次
        ledger = FileLedger(ledger_path(cfg.ledger_root(), media), timeout_seconds=1.0).read()
        assert ledger["tiers"]["promo_launch"]["spent_usd"] == 0.0  # 零入账
        assert ledger["tiers"]["promo_launch"]["refusals"] == 1
        alerts = AlertLog(alerts_path(cfg.ledger_root(), media)).entries()
        assert [entry["kind"] for entry in alerts] == ["budget_refused"]
        entry = load_run("2026-09-25", channel_id=media, root=cfg.ledger_root())["entries"][-1]
        assert entry["result"] == "refused" and entry["source"] == "real"


class _StubAdapter:
    """投放适配器桩：**只计调用次数**，返回值 = 平台侧实测花费（0.05）。"""

    def __init__(self) -> None:
        self.calls = 0

    def create_campaign(self, material, budget_usd, *, idempotency_key):
        from agents.promo.platform.base import Campaign, CampaignStatus

        self.calls += 1
        return Campaign(
            campaign_id="c-1",
            external_id="ext-1",
            material_id=getattr(material, "material_id", "mat-1"),
            budget_usd=float(budget_usd),
            spent_usd=0.05,
            status=CampaignStatus.DELIVERED,
        )


def _material():
    from agents.promo.platform.base import PromoMaterial

    return PromoMaterial(
        material_id="mat-1",
        kind="copy",
        content={"text": "夹具文案"},
        artifact_hash="ab" * 32,
        platform="stub",
    )


def test_零渠道与形态字面量在_core_billing():
    """反向机检（C11 反例 6）：`core/billing/` 不得出现渠道 id 或形态字面量。"""
    declared = {str(key) for form in FORMS for key in _section(form)["budget"]["channels"]} | {
        "llm",
        MEDIA_CHANNEL,
    }
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "core" / "billing").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name in declared:
            for match in re.finditer(rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])", text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line} 出现 {name!r}")
    assert offenders == []
