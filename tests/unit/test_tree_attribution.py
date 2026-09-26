"""attribution 薄工具单测（功能 022 / plan D4）：add_call 归集与 merge 合并。

口径：纯函数、零网关依赖；空 role/profile_id 即 ValidationError（FR-004）；
cached 调用由调用侧跳过，本工具不复查。
"""

import inspect

import pytest

from core.tree.attribution import add_call, merge
from core.tree.errors import ValidationError


def _entry(calls=1, prompt=10, completion=5, cost=0.25):
    return {
        "calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_usd": cost,
    }


class TestAddCall:
    def test_首次调用建条目(self):
        bd = add_call(
            {},
            role="screenwriter",
            profile_id="p-cheap",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.25,
        )
        assert bd == {"screenwriter": {"p-cheap": _entry()}}

    def test_同键累加(self):
        bd = add_call(
            {}, role="r", profile_id="p", prompt_tokens=10, completion_tokens=5, cost_usd=0.25
        )
        bd = add_call(
            bd, role="r", profile_id="p", prompt_tokens=20, completion_tokens=7, cost_usd=0.125
        )
        assert bd["r"]["p"] == _entry(calls=2, prompt=30, completion=12, cost=0.375)

    def test_不改入参(self):
        src = {"r": {"p": _entry()}}
        out = add_call(
            src, role="r2", profile_id="p2", prompt_tokens=1, completion_tokens=1, cost_usd=0.5
        )
        assert "r2" not in src
        assert out is not src

    @pytest.mark.parametrize("role,profile_id", [("", "p"), ("r", ""), ("", "")])
    def test_空归属拒入(self, role, profile_id):
        with pytest.raises(ValidationError):
            add_call(
                {},
                role=role,
                profile_id=profile_id,
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
            )

    def test_非字符串归属拒入(self):
        with pytest.raises(ValidationError):
            add_call(
                {}, role=None, profile_id="p", prompt_tokens=1, completion_tokens=1, cost_usd=0.0
            )

    def test_cached_跳过由调用侧负责_工具不携带cached参数(self):
        # D4：缓存命中（零计费）不进分解，跳过动作在调用侧，工具本身无 cached 形参
        assert "cached" not in inspect.signature(add_call).parameters


class TestMerge:
    def test_同键累加(self):
        a = {"r": {"p": _entry()}}
        b = {"r": {"p": _entry(calls=2, prompt=20, completion=10, cost=0.125)}}
        merged = merge(a, b)
        assert merged["r"]["p"] == _entry(calls=3, prompt=30, completion=15, cost=0.375)

    def test_异键并集(self):
        a = {"r1": {"p1": _entry()}}
        b = {"r2": {"p2": _entry(calls=2)}}
        merged = merge(a, b)
        assert merged == {"r1": {"p1": _entry()}, "r2": {"p2": _entry(calls=2)}}

    def test_可结合性(self):
        a = {"r": {"p": _entry()}}
        b = {"r": {"p": _entry(calls=2, prompt=20, cost=0.125)}}
        c = {"r": {"q": _entry(calls=3, completion=15)}}
        assert merge(merge(a, b), c) == merge(a, merge(b, c))

    def test_不改入参(self):
        a = {"r": {"p": _entry()}}
        b = {"r": {"p": _entry(calls=2, cost=0.125)}}
        merge(a, b)
        assert a == {"r": {"p": _entry()}}
        assert b == {"r": {"p": _entry(calls=2, cost=0.125)}}

    def test_空合并(self):
        assert merge({}, {}) == {}
