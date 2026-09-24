"""账单导入面（功能 019 / US2 / T1932）：契约 C2 + C3。

- 规范化：来源（`export|api` 同构）/ 批次 / 币种 / 周期 / `raw_ref`（原始行摘要）/
  `fetched_at` 全留痕；条目字段齐备（`entry_id` 账单内唯一、`amount` 非负且非 bool）；
- 格式由配置声明（内置通用键 `csv_lines`/`json_lines` + 注册表），**未注册格式报错且零落盘**；
- **列映射必填**（`entry_id`/`amount`/`currency`/`period`/`line_kind`/`amount_sign`），异币种
  条件必填 `fx_rate`/`fx_source`/`fx_at`——缺声明即导入报错，**不得改用金额启发式**；
- 全成或全败（任一行非法 ⇒ 整体拒绝、零文件产生）；批次幂等（同 `(channel_id, bill_id)` 拒绝）；
- **本特性的对抗面**：伪造条目（自造金额/假 `raw_ref`）、改写已落盘账单后再导入、
  以及门禁绕过尝试（不传守卫的装配、伪造 `--tier`）——由本文件自行举证。
"""

import json
from pathlib import Path

import pytest
import yaml

from core.billing.bill import (
    BILL_SYSTEM_FIELDS,
    BUILTIN_FORMAT_IDS,
    BillError,
    BillImportError,
    SnapshotIntegrityError,
    append_override,
    bill_path,
    builtin_format_ids,
    importer_for,
    load_bill,
    normalize_bill,
    register_importer,
    save_bill,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "configs" / "movie.yaml"


def _bill_spec(budget_config_factory):
    cfg = budget_config_factory()
    channel = next(iter(cfg.channels))
    return cfg, channel, cfg.channel(channel).bill


def _normalize(cfg, channel, spec, fixture, *, source="export", **overrides):
    params = {
        "channel_id": channel,
        "bill_id": fixture["bill_id"],
        "period": fixture["period"],
        "currency": fixture["currency"],
        "source": source,
        "fmt": spec.format_id,
        "columns": spec.columns,
    }
    params.update(overrides)
    return normalize_bill(fixture["text"], **params)


class Test规范化与留痕:
    def test_字段齐备且来源批次币种周期留痕(self, budget_config_factory, billing_bill_fixture):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        assert bill.channel_id == channel and bill.bill_id == billing_bill_fixture["bill_id"]
        assert bill.period == "2026-09" and bill.currency == "USD"
        assert bill.source == "export" and bill.format_id == spec.format_id
        assert bill.raw_ref and len(bill.raw_ref) == 64  # BLAKE3 原始行摘要
        assert bill.fetched_at  # 抓取时刻留痕（缺省 = 现在，可显式传入）
        assert len(bill.entries) == len(billing_bill_fixture["rows"])
        for entry in bill.entries:
            assert entry.entry_id and entry.currency and entry.period
            assert entry.line_kind and entry.amount_sign  # 分类驱动列齐备
            assert entry.amount >= 0 and not isinstance(entry.amount, bool)

    def test_raw_ref_是原始内容的摘要_改一个字节即变(
        self, budget_config_factory, billing_bill_fixture
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        first = _normalize(cfg, channel, spec, billing_bill_fixture)
        tweaked = _normalize(
            cfg,
            channel,
            spec,
            {**billing_bill_fixture, "text": billing_bill_fixture["text"] + "\n"},
        )
        assert first.raw_ref != tweaked.raw_ref

    def test_异币种条目带_fx_三元组(self, budget_config_factory, billing_bill_fixture):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        foreign = [entry for entry in bill.entries if entry.currency != bill.currency]
        assert foreign and foreign[0].fx == {
            "from": "EUR",
            "to": "USD",
            "rate": 1.25,
            "source": "fixture-fx",
            "at": "2026-09-30T00:00:00+00:00",
        }
        assert foreign[0].amount_in("USD") == pytest.approx(0.05)

    @pytest.mark.parametrize("source", ["export", "api"])
    def test_两种来源形态同构(self, budget_config_factory, billing_bill_fixture, source):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture, source=source)
        assert bill.source == source
        assert len(bill.entries) == len(billing_bill_fixture["rows"])  # 同构：条目一致


class Test格式与注册表:
    def test_内置通用格式键只有两个(self):
        assert BUILTIN_FORMAT_IDS == ("csv_lines", "json_lines")
        assert builtin_format_ids() == BUILTIN_FORMAT_IDS
        assert importer_for("csv_lines").format_id == "csv_lines"
        assert importer_for("json_lines").format_id == "json_lines"

    def test_注册表可注册渠道专有格式(self, budget_config_factory, billing_bill_fixture):
        class _PipeImporter:
            format_id = "fixture_pipe"

            def parse(self, raw, *, columns):
                text = raw if isinstance(raw, str) else raw.decode("utf-8")
                header = text.splitlines()[0].split("|")
                return [
                    {
                        field: dict(zip(header, line.split("|"), strict=True))[column]
                        for field, column in columns.items()
                    }
                    for line in text.splitlines()[1:]
                ]

        register_importer("fixture_pipe", _PipeImporter())
        cfg, channel, spec = _bill_spec(budget_config_factory)
        text = "\n".join(
            "|".join(line.split(",")) for line in billing_bill_fixture["text"].splitlines()
        )
        bill = normalize_bill(
            text + "\n",
            channel_id=channel,
            bill_id="fixture-pipe-1",
            period="2026-09",
            currency="USD",
            source="export",
            fmt="fixture_pipe",
            columns=spec.columns,
        )
        assert len(bill.entries) == len(billing_bill_fixture["rows"])

    def test_未注册格式报错且零落盘(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        with pytest.raises(BillError, match="未注册的账单格式"):
            normalize_bill(
                billing_bill_fixture["text"],
                channel_id=channel,
                bill_id="unknown-fmt",
                period="2026-09",
                currency="USD",
                source="export",
                fmt="vendor_only_format",
                columns=spec.columns,
            )
        assert not bill_path(billing_root, channel, "unknown-fmt").exists()


class Test列映射与条目校验:
    @pytest.mark.parametrize(
        "missing", ["entry_id", "amount", "currency", "period", "line_kind", "amount_sign"]
    )
    def test_列映射必填_缺声明即导入报错(
        self, budget_config_factory, billing_bill_fixture, missing
    ):
        """C2：分类驱动列必须声明（**不得改用金额阈值或符号猜测**推断分类）。"""
        cfg, channel, spec = _bill_spec(budget_config_factory)
        columns = {key: value for key, value in spec.columns.items() if key != missing}
        with pytest.raises(BillImportError, match=missing):
            normalize_bill(
                billing_bill_fixture["text"],
                channel_id=channel,
                bill_id="missing-column",
                period="2026-09",
                currency="USD",
                source="export",
                fmt=spec.format_id,
                columns=columns,
            )

    @pytest.mark.parametrize(
        "case",
        [
            "negative_amount",
            "bool_amount",
            "duplicate_entry_id",
            "missing_entry_id",
            "missing_fx",
            "missing_driver_column",
        ],
    )
    def test_坏账单整体拒绝且零落盘(
        self, budget_config_factory, billing_broken_bills, billing_root, case
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        fmt, text = billing_broken_bills[case]
        with pytest.raises(BillError):
            normalize_bill(
                text,
                channel_id=channel,
                bill_id=f"bad-{case}",
                period="2026-09",
                currency="USD",
                source="export",
                fmt=fmt,
                columns=spec.columns,
            )
        assert not bill_path(billing_root, channel, f"bad-{case}").exists()  # 零部分导入

    def test_批次幂等_同批次重复导入拒绝(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        target = save_bill(bill, root=billing_root)
        assert target.is_file()
        with pytest.raises(Exception, match="快照已存在"):
            save_bill(bill, root=billing_root)
        assert (
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)["system_digest"]
            == bill.system_digest
        )


class Test快照纪律与对抗面:
    def test_系统字段改写即机检失败(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"][0]["amount"] = 99.0  # 改写系统字段（自造金额）
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        with pytest.raises(SnapshotIntegrityError, match="完整性校验失败"):
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)

    def test_省略条目同样被机检挡住(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        """**省略条目**（删掉一条以免被对账发现）= 改写系统字段：digest 机检 100% 拒绝。"""
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"] = payload["entries"][:-1]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        with pytest.raises(SnapshotIntegrityError):
            load_bill(bill.bill_id, channel_id=channel, root=billing_root)

    def test_人工批注只追加_系统字段逐字节不变(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        path = save_bill(bill, root=billing_root)
        before = json.loads(path.read_text(encoding="utf-8"))
        annotated = append_override(
            path,
            by="运营",
            reason="与厂商逐条核对后确认分类",
            at="2026-10-01T00:00:00+00:00",
            system_fields=BILL_SYSTEM_FIELDS,
        )
        assert [item["by"] for item in annotated["overrides"]] == ["运营"]
        assert annotated["system_digest"] == before["system_digest"]  # 系统字段不变
        assert {key: before[key] for key in BILL_SYSTEM_FIELDS} == {
            key: annotated[key] for key in BILL_SYSTEM_FIELDS
        }

    def test_伪造_raw_ref_与自造金额来自同一份文本的摘要(
        self, budget_config_factory, billing_bill_fixture
    ):
        """`raw_ref` 是**原始内容**的摘要：自造金额必然与 `raw_ref` 脱钩（导入时即暴露）。"""
        cfg, channel, spec = _bill_spec(budget_config_factory)
        honest = _normalize(cfg, channel, spec, billing_bill_fixture)
        forged_text = billing_bill_fixture["text"].replace("1.20", "9.99")  # 自造金额
        forged = _normalize(cfg, channel, spec, {**billing_bill_fixture, "text": forged_text})
        assert forged.raw_ref != honest.raw_ref
        assert forged.entries[0].amount == pytest.approx(9.99)  # 伪造可被指认（对照原始摘要）

    def test_改写已落盘账单后再导入即被拒(
        self, budget_config_factory, billing_bill_fixture, billing_root
    ):
        cfg, channel, spec = _bill_spec(budget_config_factory)
        bill = _normalize(cfg, channel, spec, billing_bill_fixture)
        save_bill(bill, root=billing_root)
        with pytest.raises(Exception, match="已存在"):  # 改写后再导入：同批次拒绝覆盖
            save_bill(
                _normalize(
                    cfg,
                    channel,
                    spec,
                    {**billing_bill_fixture, "text": billing_bill_fixture["text"]},
                ),
                root=billing_root,
            )


class Test门禁绕过尝试被拒:
    """本特性的对抗面：**门禁绕过**（不传守卫的装配 / 伪造 `--tier`）100% 被拒。"""

    def test_不传守卫的真实装配点已被机检钉死(self):
        from tests.unit.test_billing_core_purity import (
            OFFLINE_ASSEMBLIES,
            OFFLINE_GUARDED_ASSEMBLIES,
            REAL_ASSEMBLY_POINTS,
            _gateway_constructions,
        )

        sites = _gateway_constructions()
        real = sorted({site["path"] for site in sites if site["real"]})
        assert real == sorted(REAL_ASSEMBLY_POINTS)  # 真实装配点必须传非 None 守卫
        offline = [site for site in sites if not site["real"]]
        # 两类离线装配各自清单常驻（新增一处即红，不能靠"随手传个守卫"混过去）
        assert sorted(
            {site["path"] for site in offline if site["guard_is_explicit_none"]}
        ) == sorted(OFFLINE_ASSEMBLIES)
        assert sorted(
            {site["path"] for site in offline if not site["guard_is_explicit_none"]}
        ) == sorted(OFFLINE_GUARDED_ASSEMBLIES)

    def test_伪造_tier_被拒(self, capsys, tmp_path):
        import importlib

        module = importlib.import_module("ops.billing")
        config = _temp_config(tmp_path)
        code = module.main(
            [
                "raise-tier",
                "--channel",
                "llm",
                "--tier",
                "ghost_tier",  # 伪造环节（未在 budget.tiers 声明）
                "--limit-usd",
                "9.0",
                "--calibration",
                "ghost",
                "--by",
                "运营",
                "--reason",
                "绕过尝试",
                "--config",
                str(config),
            ]
        )
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 2 and "ghost_tier" in payload["error"]  # 缺档即配置错误（不发明档位）


def _temp_config(tmp_path: Path) -> Path:
    """真实 movie.yaml 的派生：账本根落 tmp（测试零污染仓库）。"""
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["budget"]["ledger"]["root"] = str(tmp_path / "billing")
    target = tmp_path / "movie.yaml"
    target.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return target
