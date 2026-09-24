"""分镜索引块网格的容量上下界与升版规则单测（功能 018 / T1813，先于实现编写；契约 C8/C9）。

C8：索引条 = 帧顶 `R` 行 × `C` 列块网格（容量 `2**(R·C)`）——编/解/绘三处**同取**
`storyboard.render.index_grid`，码内常量退役、缺项即拒绝（**不得**静默回落 4×2）；
**容量下界** = 该形态**派生镜头数**（唯一公式持有者 `agents/pilot/scale.py`：movie 原值
`ceil(5400/2.0) = 2700` 镜 ⇒ 容量 ≥ 12 位；shortdrama `ceil(120/7.5) = 16` 镜 ⇒ ≥ 4 位）；
**量子上界** `2**C <= render.width`（块宽在块数超像素数时退化、位间互相吞并）且
`1 <= R <= render.height`；越界即拒绝并给出实测数字（要更少镜头就改单镜时长，**不是**放宽校验）。

C9（原则一，**强定义务**）：生效网格参数进版本材料——同一实现下两种网格取值 ⇒ 两个不同版本号，
只改配置网格取值亦然；旧版本字面量冻结（网格参数入版本材料**之前**的实测值）；既有工件为
内容寻址、节点为一次性 INSERT ⇒ 无迁移、无回填、无改写历史节点。
"""

import ast
import copy
import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from agents.pilot.stages import build_runtime
from agents.storyboard import board_render
from agents.storyboard.config import (
    StoryboardConfig,
    StoryboardConfigError,
    require_index_capacity,
)
from agents.storyboard.evaluators.alignment import EVALUATOR_ID, EmotionAlignmentEvaluator
from core.tree.errors import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
_MOVIE = yaml.safe_load((REPO_ROOT / "configs" / "movie.yaml").read_text(encoding="utf-8"))
_SHORTDRAMA = yaml.safe_load(
    (REPO_ROOT / "configs" / "shortdrama.yaml").read_text(encoding="utf-8")
)
# 真实渲染段（两形态取值不同：电影 320×240 网格 2×8、短剧 144×256 网格 1×4）
_MOVIE_RENDER = _MOVIE["storyboard"]["render"]
_SHORT_RENDER = _SHORTDRAMA["storyboard"]["render"]
_GRAMMAR = _MOVIE["storyboard"]["shot_grammar"]
_VECTORS = _MOVIE["storyboard"]["emotion_vectors"]

# 网格参数入版本材料**之前**的实测版本号（冻结：索引条固定 4 位时代的对齐代理版本）
_网格前的版本 = "1.0.0+a891dd9955e11f3d5f321f"

_SHOT = {
    "shot_id": "shot-x",
    "scene_id": "scene-1",
    "covers": ["s1-l1"],
    "shot_size": "medium",
    "camera": "eye_level",
    "side": "A",
    "movement": "static",
    "est_duration_ms": 2000,
    "alternatives": 1,
}


def _form_scale(config: dict) -> dict:
    """形态**原值**三键（`agents/pilot/scale.py` 唯一公式的三个入参）。"""
    return {
        "scene_count": config["pilot"]["scene_count"],
        "target_duration_s": config["editing"]["target_duration_s"],
        "clip_duration_seconds": config["visual"]["clip_spec"]["duration_seconds"],
    }


_MOVIE_FORM = _form_scale(_MOVIE)
_SHORT_FORM = _form_scale(_SHORTDRAMA)


def _card(render_cfg: dict, *, index: int, shot_size: str = "medium") -> np.ndarray:
    return board_render.render_shot_card(
        {**_SHOT, "shot_size": shot_size},
        index=index,
        emotion="tense",
        render_cfg=render_cfg,
        grammar_rules=_GRAMMAR,
        emotion_vectors=_VECTORS,
    )


def _版本(render_cfg: dict) -> str:
    """同一实现（同一评估器文件字节）、仅网格取值不同的版本号。"""
    evaluator = EmotionAlignmentEvaluator({"cos_floor": 0.90}, render_cfg, _GRAMMAR, _VECTORS)
    return evaluator.spec.version


class Test网格取值与容量校验:
    def test_缺网格即拒绝不静默回落4x2(self):
        """缺项在配置加载期与渲染器入口两处都拒绝（过去的 2×4 是当前设置、不是口径）。"""
        config = copy.deepcopy(_MOVIE)
        del config["storyboard"]["render"]["index_grid"]
        with pytest.raises(StoryboardConfigError, match="index_grid"):
            StoryboardConfig.from_dict(config)
        render_cfg = {"fps": 8, "width": 320, "height": 240}
        with pytest.raises(ValidationError, match="index_grid"):
            _card(render_cfg, index=0)
        with pytest.raises(ValidationError, match="index_grid"):
            board_render.decode_index_code(np.zeros((240, 320, 3), dtype=np.uint8), render_cfg)

    @pytest.mark.parametrize(
        ("grid", "capacity"),
        [({"rows": 2, "cols": 2}, "16"), ({"rows": 2, "cols": 4}, "256")],
    )
    def test_容量下界不足即拒绝并给出实测数字(self, grid, capacity):
        """movie 原值派生 2700 镜：4 位（16）与 8 位（256）都不足 ⇒ 点名两处实测值。"""
        with pytest.raises(StoryboardConfigError, match=f"{capacity}.*2700"):
            require_index_capacity({**_MOVIE_RENDER, "index_grid": grid}, **_MOVIE_FORM)

    def test_容量下界恰够即通过并返回派生镜头数(self):
        assert (
            require_index_capacity(
                {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 8}}, **_MOVIE_FORM
            )
            == 2700
        )
        # 短剧形态：1×4 = 4 位（容量 16）恰够 16 镜（无余量，如实登记）
        assert (
            require_index_capacity(
                {**_SHORT_RENDER, "index_grid": {"rows": 1, "cols": 4}}, **_SHORT_FORM
            )
            == 16
        )

    @pytest.mark.parametrize("grid", [{"rows": 1, "cols": 3}, {"rows": 1, "cols": 2}])
    def test_短剧容量不足即拒绝(self, grid):
        """短剧派生 16 镜：容量 < 16（4 位）即拒绝（不因形态"小"而放宽）。"""
        with pytest.raises(StoryboardConfigError, match="16"):
            require_index_capacity({**_SHORT_RENDER, "index_grid": grid}, **_SHORT_FORM)

    @pytest.mark.parametrize(
        ("grid", "expected"),
        [
            ({"rows": 2, "cols": 9}, "512"),  # 2**9 = 512 > 320（量子上界）
            ({"rows": 0, "cols": 8}, "rows"),  # 1 <= R <= height
            ({"rows": 241, "cols": 8}, "241"),
        ],
    )
    def test_量子上界与行数越界即拒绝(self, grid, expected):
        config = copy.deepcopy(_MOVIE)
        config["storyboard"]["render"]["index_grid"] = grid
        with pytest.raises(StoryboardConfigError, match=expected):
            StoryboardConfig.from_dict(config)

    def test_渲染器入口同取网格并拒绝越界(self):
        """渲染器入口（`_require_render_cfg`）与配置加载期同口径：越界不画、缺项不画。"""
        render_cfg = {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 9}}
        with pytest.raises(ValidationError, match="512"):
            _card(render_cfg, index=0)

    def test_改单镜时长即改容量下界而校验条件不变(self):
        """派生镜头数取决于**配置的单镜时长**：体量变小不等于把容量校验放宽。"""
        grid_8位 = {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 4}}  # 容量 256
        with pytest.raises(StoryboardConfigError, match="2700"):
            require_index_capacity(grid_8位, **_MOVIE_FORM)
        coarse = {**_MOVIE_FORM, "clip_duration_seconds": 20.0}  # 5400 / 20 = 270 镜
        with pytest.raises(StoryboardConfigError, match="270"):
            require_index_capacity(grid_8位, **coarse)  # 256 < 270 ⇒ 仍拒绝
        # 换成 16 位（65536）后新下界通过——下界随单镜时长移动，拒绝条件一字未改
        assert (
            require_index_capacity(
                {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 8}}, **coarse
            )
            == 270
        )

    def test_装配期按形态原值校验容量(self, pilot_form_config_path, pilot_dirs, tmp_path):
        """装配期同口径（`build_runtime`）：真实精简 movie 配置的 2×8 通过、缩到 2×2 即拒绝。"""
        path = pilot_form_config_path("movie")
        kwargs = {"form": "movie", "data_dir": pilot_dirs, "artifacts_root": tmp_path / "artifacts"}
        assert build_runtime(config_path=path, **kwargs).pilot.scene_count >= 1
        text = path.read_text(encoding="utf-8")
        assert "index_grid: {rows: 2, cols: 8}" in text  # 声明点存在（口径变了即红）
        path.write_text(
            text.replace("index_grid: {rows: 2, cols: 8}", "index_grid: {rows: 2, cols: 2}"),
            encoding="utf-8",
        )
        with pytest.raises(StoryboardConfigError, match="2700"):
            build_runtime(config_path=path, **kwargs)


class Test全量编解码往返:
    def test_movie原值2700镜逐序号可编码(self):
        """SC-010：`decode_index_code(render_shot_card(index=i)) == i` 对 i < 2700 全成立。

        **宽度取真实配置**（320：量子上界 `2**8 = 256 <= 320` 的真实约束面），高度压到 24
        以控单测耗时——索引条占帧顶 `R` 行，编解码口径与帧高无关（既有 `render_cfg` 夹具
        同款缩小尺寸惯例）；真实 320×240 的代表性序号见下一条用例。
        """
        derived = require_index_capacity(
            {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 8}}, **_MOVIE_FORM
        )
        assert derived == 2700
        render_cfg = {**_MOVIE_RENDER, "height": 24}
        for index in range(derived):
            frame = _card(render_cfg, index=index)
            assert board_render.decode_index_code(frame, render_cfg) == index

    def test_真实尺寸下各景别代表性序号往返(self):
        """真实 320×240：五种景别的主体占比不同 ⇒ 整卡均值不同 ⇒ 解码阈值各异，仍全成立。"""
        render_cfg = dict(_MOVIE_RENDER)
        for shot_size in _GRAMMAR["shot_sizes"]:
            for index in (0, 1, 15, 16, 255, 256, 1023, 1024, 2699):
                frame = _card(render_cfg, index=index, shot_size=shot_size)
                decoded = board_render.decode_index_code(frame, render_cfg)
                assert decoded == index, (shot_size, index)

    def test_跨网格帧字节不同且错网格解不出原序号(self):
        """不得用 4 列解码器解 8 列编码：同序号两网格帧不同，错网格解出的不是原序号。"""
        wide = dict(_MOVIE_RENDER)
        narrow = {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 4}}
        assert not np.array_equal(_card(wide, index=5), _card(narrow, index=5))
        encoded = _card(wide, index=5)
        assert board_render.decode_index_code(encoded, wide) == 5
        assert board_render.decode_index_code(encoded, narrow) != 5


class Test版本规则:
    def test_同一实现两种网格取值即两个版本号(self):
        """原则一（**强定义务**）：仅改配置网格取值也必须升版本，否则同 id@version 行为漂移。"""
        assert _版本({**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 4}}) != _版本(
            {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 6}}
        )
        # 同网格取值 ⇒ 同版本（版本材料是确定性函数，不受 dict 键序影响）
        assert _版本({**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 4}}) == _版本(
            {**_MOVIE_RENDER, "index_grid": {"cols": 4, "rows": 2}}
        )

    def test_网格前的版本字面量已冻结且新版本不同(self):
        """镜像 `BOOTSTRAP_VERSION` 冻结法：冻结值是网格参数入版本材料**之前**的实测版本。"""
        current = _版本(_MOVIE_RENDER)
        assert current != _网格前的版本
        assert re.fullmatch(r"1\.0\.0\+a[0-9a-f]{12}f[0-9a-f]{8}", current)

    def test_缺网格的评估器拒绝构造(self):
        """对齐代理读帧像素 ⇒ 无网格即无法解码，拒绝构造（不静默取默认网格）。"""
        with pytest.raises(StoryboardConfigError, match="index_grid"):
            EmotionAlignmentEvaluator(
                {"cos_floor": 0.90}, {"fps": 8, "width": 320, "height": 240}, _GRAMMAR, _VECTORS
            )

    def test_受影响面只有对齐代理一处(self):
        """如实登记：`rule.*` 与 `judge.script_fit` 不读帧 ⇒ 网格取值不进其版本材料。"""
        for name in ("axis_rule", "coverage", "shot_grammar", "script_fit"):
            source = (REPO_ROOT / "agents" / "storyboard" / "evaluators" / f"{name}.py").read_text(
                encoding="utf-8"
            )
            assert "render_cfg" not in source, name
        assert _版本(dict(_MOVIE_RENDER)) != _版本(
            {**_MOVIE_RENDER, "index_grid": {"rows": 2, "cols": 6}}
        )

    def test_对齐代理_id未变(self):
        """网格参数只进版本材料（`a<hash>` 段），`evaluator_id` 与版本正则形状不变。"""
        assert EVALUATOR_ID == "proxy.emotion_alignment"


class Test派生镜头数单一持有者与历史保全:
    def test_派生镜头数只有一份公式(self):
        """F-04：公式只在 `agents/pilot/scale.py` 一处；`agents/storyboard/**` 只读它。"""
        holders, callers = [], []
        for path in sorted(REPO_ROOT.glob("agents/**/*.py")) + sorted(
            REPO_ROOT.glob("core/**/*.py")
        ):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "def derived_shot_count" in source:
                holders.append(str(path.relative_to(REPO_ROOT)))
            for node in ast.walk(ast.parse(source, filename=str(path))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == (
                    "derived_shot_count"
                ):
                    callers.append(str(path.relative_to(REPO_ROOT)))
        assert holders == ["agents/pilot/scale.py"]
        assert sorted(set(callers)) == ["agents/pilot/stages.py", "agents/storyboard/config.py"]
        for path in sorted((REPO_ROOT / "agents" / "storyboard").rglob("*.py")):
            assert "ceil(" not in path.read_text(encoding="utf-8"), path

    def test_既有节点与已落盘工件无写路径(self):
        """原则一/二：节点一次性 INSERT、工件内容寻址 ⇒ 无迁移、无回填、无改写历史节点。"""
        migrations = sorted((REPO_ROOT / "ops" / "migrations" / "versions").glob("*.py"))
        assert migrations  # 迁移目录存在（口径变了即红）
        for path in migrations:
            assert "index_grid" not in path.read_text(encoding="utf-8"), path.name
        for root in ("agents", "core"):
            for path in sorted((REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                for banned in ("tree_nodes.update", "artifacts.update", "update(tree_nodes"):
                    assert banned not in source, (str(path.relative_to(REPO_ROOT)), banned)
