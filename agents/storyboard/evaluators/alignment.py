"""情绪对齐代理评估器：proxy.emotion_alignment（连续分量，C7，澄清 Q2 口径）。

**输入 = 预演画面帧像素**（不是脚本自述）：分镜卡帧由 board_render.storyboard_cards
这一**唯一帧产出函数**重算（渲染件与评估输入同源），并与工件元数据的 `frames_hash` /
逐镜 `frame_hashes` 校验一致——不一致即"两套帧"，如实标不适用（不读取来源不明的帧）。

确定性启发式（实现哈希即版本，真实 CLIP 类模型替换 = 升版本、路径不变）：
- 预测：逐镜分镜卡像素均值（3 维归一化色板向量，帧内像素级读入）× 该镜帧数权重，
  按场景聚合 → 预演画面呈现的情绪基调；
- 目标：剧本该场景**已标注行的情绪向量均值**（场景整体情绪基调，与镜头如何挑选
  台词无关）——策略把镜头全押在非代表性台词上时，画面基调偏离剧本，对齐度下降；
- 得分：cosine(预测, 目标) → `cos ≤ cos_floor → 0`，否则线性归一到 [0,1]（定点 6 位）。

情绪基调缺失（剧本未标注）→ "不适用"注明（合成按适用分量归一）。
"""

import json
import math

from agents.storyboard.board_render import (
    frame_function_hash,
    shot_frame_count,
    storyboard_cards,
)
from agents.storyboard.config import StoryboardConfigError
from agents.storyboard.evaluators._versioning import implementation_version
from agents.storyboard.script import ScriptSegment
from agents.storyboard.shotlist import ShotList
from core.evaluators.base import (
    ArtifactRef,
    EvalResult,
    Evaluator,
    EvaluatorKind,
    EvaluatorSpec,
)
from core.evaluators.quantize import quantize_score

EVALUATOR_ID = "proxy.emotion_alignment"


def _require(config_slice: dict, key: str, where: str):
    value = config_slice.get(key) if isinstance(config_slice, dict) else None
    if value is None:
        raise StoryboardConfigError(f"{where} 缺配置项 {key!r}，拒绝启动（原则五）")
    return value


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（维度不符/零向量 → 0.0，确定性不伪造匹配，同 006 口径）。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def _map_score(cosine: float, cos_floor: float) -> float:
    """余弦 → 得分：≤ 下限判 0，否则线性归一到 [0,1] 并定点 6 位。"""
    if cosine <= cos_floor:
        return 0.0
    return quantize_score(min(1.0, (cosine - cos_floor) / (1.0 - cos_floor)))


class EmotionAlignmentEvaluator(Evaluator):
    """预演画面帧像素 vs 剧本情绪基调的余弦代理（确定性、零成本）。"""

    def __init__(
        self,
        alignment: dict,
        render_cfg: dict,
        grammar_rules: dict,
        emotion_vectors: dict,
    ) -> None:
        self._cos_floor = float(_require(alignment, "cos_floor", "proxy.emotion_alignment"))
        if not isinstance(render_cfg, dict) or not {"fps", "width", "height"} <= set(render_cfg):
            raise StoryboardConfigError(
                "proxy.emotion_alignment 缺渲染配置（fps/width/height），无法重算分镜卡帧"
            )
        self._render_cfg = dict(render_cfg)
        self._grammar_rules = dict(grammar_rules or {})
        self._emotion_vectors = dict(emotion_vectors or {})
        if not self._emotion_vectors:
            raise StoryboardConfigError(
                "proxy.emotion_alignment 缺情绪向量表（emotion_vectors），拒绝启动"
            )
        self.spec = EvaluatorSpec(
            evaluator_id=EVALUATOR_ID,
            version=(
                "1.0.0+a"
                + implementation_version(
                    json.dumps({"alignment": alignment}, sort_keys=True, ensure_ascii=False)
                ).rsplit("+", 1)[1]
                + f"f{frame_function_hash()}"  # 帧产出函数变更即版本变更（原则一）
            ),
            kind=EvaluatorKind.PROXY_MODEL,
            deterministic=True,
            cost_per_call=0.0,
        )

    def _scene_targets(self, script: ScriptSegment) -> dict[str, list[float]]:
        """逐场景目标向量 = 该场景已标注行的情绪向量均值（场景整体情绪基调）。"""
        targets: dict[str, list[float]] = {}
        for scene in script.scenes:
            vectors = [
                list(self._emotion_vectors[line.emotion])
                for line in scene.lines
                if line.emotion is not None and line.emotion in self._emotion_vectors
            ]
            if vectors:
                dims = len(vectors[0])
                targets[scene.scene_id] = [
                    sum(v[d] for v in vectors) / len(vectors) for d in range(dims)
                ]
        return targets

    def evaluate(self, artifact: ArtifactRef, context: dict) -> EvalResult:
        shotlist: ShotList = context["shotlist"]
        script: ScriptSegment = context["script"]
        cards = storyboard_cards(
            shotlist,
            script,
            render_cfg=self._render_cfg,
            grammar_rules=self._grammar_rules,
            emotion_vectors=self._emotion_vectors,
        )
        metadata = artifact.metadata or {}
        frame_hashes = list(cards.frame_hashes)
        verified = (
            metadata.get("frames_hash") == cards.frames_hash
            and list(metadata.get("frame_hashes", [])) == frame_hashes
        )
        base_diagnostics = {
            "cos_floor": self._cos_floor,
            "frames_hash": cards.frames_hash,
            "frame_hashes": frame_hashes,
            "frames_verified": verified,
            "shot_count": len(shotlist.shots),
            "emotions": list(cards.emotions),
        }
        if not verified:
            # 禁止两套帧：帧来源与渲染元数据不符 → 如实标不适用（不评来源不明的帧）
            return EvalResult(
                score=0.0,
                diagnostics={
                    **base_diagnostics,
                    "applicable": False,
                    "note": "预演帧与渲染元数据不一致（禁止两套帧），情绪对齐不适用",
                },
            )

        targets = self._scene_targets(script)
        # 逐镜帧像素特征（3 维归一化色板向量）按场景聚合，权重 = 该镜帧数
        scene_features: list[dict] = []
        for scene_id in shotlist.scene_ids():
            if scene_id not in targets:
                continue
            weights: list[float] = []
            vectors: list[list[float]] = []
            for index, shot in enumerate(shotlist.shots):
                if shot.scene_id != scene_id or cards.emotions[index] is None:
                    continue
                weights.append(float(shot_frame_count(shot, self._render_cfg)))
                vectors.append((cards.frames[index].reshape(-1, 3).mean(axis=0) / 255.0).tolist())
            if not weights:
                continue
            total = sum(weights)
            feature = [
                sum(w * v[d] for w, v in zip(weights, vectors, strict=True)) / total
                for d in range(len(vectors[0]))
            ]
            scene_features.append(
                {
                    "scene_id": scene_id,
                    "frame_weight": total,
                    "feature": [round(value, 9) for value in feature],
                    "target": [round(value, 9) for value in targets[scene_id]],
                }
            )

        if not scene_features:
            return EvalResult(
                score=0.0,
                diagnostics={
                    **base_diagnostics,
                    "applicable": False,
                    "note": "剧本未标注情绪基调，情绪对齐不适用（分量跳过，合成按适用归一）",
                },
            )

        total_weight = sum(item["frame_weight"] for item in scene_features)
        feature = [
            sum(item["feature"][d] * item["frame_weight"] for item in scene_features) / total_weight
            for d in range(len(scene_features[0]["feature"]))
        ]
        target = [
            sum(item["target"][d] * item["frame_weight"] for item in scene_features) / total_weight
            for d in range(len(scene_features[0]["target"]))
        ]
        cosine = _cosine(feature, target)
        return EvalResult(
            score=_map_score(cosine, self._cos_floor),
            diagnostics={
                **base_diagnostics,
                "applicable": True,
                "cosine": round(cosine, 9),
                "labeled_shot_count": sum(1 for emotion in cards.emotions if emotion is not None),
                "scenes": scene_features,
                "feature": [round(value, 9) for value in feature],
                "target": [round(value, 9) for value in target],
            },
        )
