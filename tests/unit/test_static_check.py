"""策略静态检查单测（US2 / T119）。

决策 6：AST 白名单——白名单内 import 通过；socket/open/eval/getattr 等
逃逸形态全部拒绝。静态检查是第一道防线（运行时隔离是第二道）。
"""

import pytest

from policies.static_check import StaticCheckError, check_policy_source, find_violations

CLEAN_POLICY = """
import math
import random
from collections import Counter
from itertools import islice


class Policy:
    def solve(self, env, budget):
        best = None
        for _ in range(min(3, budget.max_probes)):
            observations = env.observed()
            for node_id, obs in observations.items():
                if obs.score is not None and (best is None or obs.score > best[0]):
                    best = (obs.score, node_id)
            if best is None:
                break
            env.probe(best[1], {"temperature": random.choice([0.3, 0.7])})
        return best[1] if best else ""
"""


class Test白名单放行:
    def test_干净策略通过(self):
        check_policy_source(CLEAN_POLICY)

    @pytest.mark.parametrize(
        "module",
        ["math", "random", "collections", "itertools", "functools", "statistics", "json", "heapq"],
    )
    def test_白名单模块逐个放行(self, module):
        check_policy_source(f"import {module}\n\n\nclass Policy:\n    pass\n")

    def test_from_import_白名单放行(self):
        check_policy_source("from math import ceil, floor\n")


class Test逃逸拒绝:
    @pytest.mark.parametrize(
        "module",
        [
            "os",
            "sys",
            "socket",
            "subprocess",
            "pathlib",
            "builtins",
            "ctypes",
            "importlib",
            "shutil",
        ],
    )
    def test_白名单外_import_拒绝(self, module):
        with pytest.raises(StaticCheckError, match=module):
            check_policy_source(f"import {module}\n")

    def test_from_白名单外_拒绝(self):
        with pytest.raises(StaticCheckError):
            check_policy_source("from os import environ\n")

    @pytest.mark.parametrize(
        "name",
        [
            "open",
            "eval",
            "exec",
            "compile",
            "__import__",
            "getattr",
            "setattr",
            "delattr",
            "globals",
            "locals",
            "vars",
            "breakpoint",
            "input",
        ],
    )
    def test_危险内建调用拒绝(self, name):
        with pytest.raises(StaticCheckError, match=name):
            check_policy_source(f"{name}('x')\n")

    def test_dunder_属性访问拒绝(self):
        """getattr 字符串逃逸的静态形态：`x.__class__.__bases__` 等。"""
        with pytest.raises(StaticCheckError):
            check_policy_source("c = ().__class__.__bases__\n")

    def test_私有属性访问拒绝(self):
        """env._latent 之类的下划线属性访问一律拒绝。"""
        with pytest.raises(StaticCheckError):
            check_policy_source("x = env._latent\n")

    def test_dunder_名字拒绝(self):
        with pytest.raises(StaticCheckError):
            check_policy_source("print(__builtins__)\n")

    def test_语法错误拒绝(self):
        with pytest.raises(StaticCheckError):
            check_policy_source("def broken(:\n")


class Test诊断可读:
    def test_find_violations_汇总全部问题(self):
        violations = find_violations("import os\nimport socket\neval('1')\n")
        assert len(violations) == 3
        assert any("os" in v for v in violations)
        assert any("socket" in v for v in violations)

    def test_干净代码零违规(self):
        assert find_violations(CLEAN_POLICY) == []

    def test_报错信息含全部违规(self):
        with pytest.raises(StaticCheckError) as exc_info:
            check_policy_source("import os\nopen('f')\n")
        message = str(exc_info.value)
        assert "os" in message and "open" in message
