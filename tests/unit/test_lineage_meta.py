"""谱系元数据读写单测（T407，research 决策 3）。

meta.json schema 校验、幂等落盘（同内容重写零变化）、
已存在不同内容冲突报错（文件只增不改）。
"""

import json

import pytest

from dreaming.lineage import (
    LineageConflictError,
    MetaValidationError,
    read_meta,
    validate_meta,
    write_meta,
)


def _meta(**overrides):
    meta = {
        "version": "a1b2c3d4e5f6",
        "parent_version": "f6e5d4c3b2a1",
        "created_round": "dream-promo-3",
        "reward": {"pareto_auc": 0.62, "parallel_penalty": 0.18, "lambda": 0.5, "reward": 0.53},
        "source": "dreaming",
        "approval": None,
    }
    meta.update(overrides)
    return meta


class TestSchema:
    def test_合法_meta(self):
        validate_meta(_meta())

    @pytest.mark.parametrize(
        "missing", ["version", "parent_version", "created_round", "reward", "source"]
    )
    def test_缺字段拒绝(self, missing):
        meta = _meta()
        del meta[missing]
        with pytest.raises(MetaValidationError, match=missing):
            validate_meta(meta)

    def test_source_枚举(self):
        for source in ("dreaming", "epsilon_random", "manual"):
            validate_meta(_meta(source=source))
        with pytest.raises(MetaValidationError, match="source"):
            validate_meta(_meta(source="ghost"))

    def test_approval_结构(self):
        validate_meta(_meta(approval=None))  # 未审批合法
        validate_meta(
            _meta(
                approval={
                    "approver": "张三",
                    "at": "2026-09-19T10:00:00+08:00",
                    "decision": "approved",
                    "reason": "泛化良好",
                }
            )
        )
        with pytest.raises(MetaValidationError, match="decision"):
            validate_meta(
                _meta(approval={"approver": "张三", "at": "t", "decision": "maybe", "reason": ""})
            )
        with pytest.raises(MetaValidationError, match="approver"):
            validate_meta(_meta(approval={"at": "t", "decision": "approved", "reason": ""}))


class Test读写与幂等:
    def test_写入并读回(self, tmp_path):
        write_meta(tmp_path, "promo", _meta())
        assert read_meta(tmp_path, "promo", "a1b2c3d4e5f6") == _meta()

    def test_幂等_同内容重写零变化(self, tmp_path):
        write_meta(tmp_path, "promo", _meta())
        target = tmp_path / "promo" / "a1b2c3d4e5f6.meta.json"
        before = target.read_bytes()
        write_meta(tmp_path, "promo", _meta())
        assert target.read_bytes() == before

    def test_冲突_不同内容同版本报错(self, tmp_path):
        """文件只增不改：同版本不同内容（如不同 parent）冲突报错。"""
        write_meta(tmp_path, "promo", _meta())
        with pytest.raises(LineageConflictError):
            write_meta(tmp_path, "promo", _meta(parent_version="another-parent"))

    def test_读取不存在返回_none(self, tmp_path):
        assert read_meta(tmp_path, "promo", "ghost") is None

    def test_写入即schema校验(self, tmp_path):
        with pytest.raises(MetaValidationError):
            write_meta(tmp_path, "promo", {"version": "x"})

    def test_meta与代码文件同目录(self, tmp_path):
        path = write_meta(tmp_path, "promo", _meta())
        assert path == tmp_path / "promo" / "a1b2c3d4e5f6.meta.json"
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == "a1b2c3d4e5f6"
