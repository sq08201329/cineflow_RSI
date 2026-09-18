"""策略版本管理单测（补充 T125：BLAKE3 前 12 位 + history 幂等落盘）。"""

import blake3
import pytest

from policies.versioning import VersionConflictError, policy_version, record_policy

SOURCE_A = "class Policy:\n    pass\n"
SOURCE_B = "class Policy:\n    x = 1\n"


class Test版本号:
    def test_blake3_前12位(self):
        assert policy_version(SOURCE_A) == blake3.blake3(SOURCE_A.encode()).hexdigest()[:12]

    def test_内容不同版本必不同(self):
        assert policy_version(SOURCE_A) != policy_version(SOURCE_B)


class Test落盘:
    def test_写入_history_路径(self, tmp_path):
        version = record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
        target = tmp_path / "agent-x" / f"{version}.py"
        assert target.read_text() == SOURCE_A

    def test_幂等_同内容重复提交不产生变化(self, tmp_path):
        v1 = record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
        v2 = record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
        assert v1 == v2
        assert len(list(tmp_path.rglob("*.py"))) == 1

    def test_内容变更产生新版本文件(self, tmp_path):
        va = record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
        vb = record_policy(SOURCE_B, "agent-x", history_root=tmp_path)
        assert va != vb
        assert len(list(tmp_path.rglob("*.py"))) == 2

    def test_同版本号内容冲突报错(self, tmp_path):
        """防御：版本路径已存在但内容不符（理论上哈希相同则内容相同，此为完整性兜底）。"""
        version = record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
        target = tmp_path / "agent-x" / f"{version}.py"
        target.write_text("tampered")
        with pytest.raises(VersionConflictError):
            record_policy(SOURCE_A, "agent-x", history_root=tmp_path)
