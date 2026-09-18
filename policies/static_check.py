"""策略代码静态检查（research 决策 6，沙箱第一道防线）。

AST 扫描：import 白名单（纯计算模块）+ 危险调用形态拒绝
（open/socket/eval/exec/__import__/getattr 字符串逃逸、dunder/私有属性访问）。
静态检查不过 → 不起容器，直接 policy_error；运行时隔离是第二道防线，
两者独立失效才算失守。
"""

import ast

# import 白名单：纯计算模块（无网络/文件/进程能力）
ALLOWED_IMPORTS = frozenset(
    {"math", "random", "collections", "itertools", "functools", "statistics", "json", "heapq"}
)

# 危险内建/调用形态（含 getattr 字符串逃逸）
FORBIDDEN_CALLS = frozenset(
    {
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
    }
)


class StaticCheckError(Exception):
    """静态检查拒绝：消息汇总全部违规点（人可读，供做梦层修正策略代码）。"""


def find_violations(source: str) -> list[str]:
    """扫描策略源码，返回全部违规描述（空列表 = 通过）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"语法错误：{exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    violations.append(f"第 {node.lineno} 行：白名单外 import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                violations.append(f"第 {node.lineno} 行：白名单外 from {node.module!r} import")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            violations.append(f"第 {node.lineno} 行：危险内建 {node.id}() 被引用")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            violations.append(
                f"第 {node.lineno} 行：私有/dunder 属性访问 {node.attr!r} "
                "（杜绝反射偷看与字符串逃逸）"
            )
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            violations.append(f"第 {node.lineno} 行：dunder 名字引用 {node.id!r}")
    return violations


def check_policy_source(source: str) -> None:
    """静态检查入口：任一违规即 StaticCheckError（汇总全部违规点）。"""
    violations = find_violations(source)
    if violations:
        raise StaticCheckError("策略静态检查未通过：\n" + "\n".join(f"- {v}" for v in violations))
