# 契约：四段交接（agents/pilot）

> 对应规格 US2 / FR-003~004。实现：`agents/pilot/handoffs.py`。

## C5 剧本 → 分镜

```
script_to_segment(artifact: ScriptArtifact) -> ScriptSegment   # 复用 009 export_segment
```

- 双向快照：上游导出字段集 == 008 `ScriptSegment` 输入字段集（含枚举值）
- 场景：导出 → 008 `validate_script` 通过；缺字段即红

## C6 分镜 → 视觉

```
shotlist_to_gen_params(shotlist: ShotList, cfg) -> list[VisualGenParams]
```

- 每镜产出视觉生成参数（景别/时长/风格/尺寸按形态配置）；**镜头数 == 参数数**（一一对应，
  不静默丢弃）
- 场景：3 镜 ShotList → 3 组参数；参数在视觉侧样式校验通过；字段集双向一致

## C7 视觉 + 声音 → 剪辑

```
av_to_edit_inputs(clips: list[VisualArtifact], audio: SoundArtifact | None, cfg) -> EditInputs
```

- 产出：镜头库（工件引用 + 时长 + 元数据）+ 音轨（引用 + 时序）；无音轨时如实标注
  （剪辑按无音轨语义处理，不伪造）
- 场景：片段齐备 → 镜头库条目数一致；含音轨/无音轨两路径；剪辑侧校验通过

## C8 成片 → 宣发

```
reel_to_promo_materials(reel: FilmArtifact, cfg) -> PromoMaterials
```

- 产出宣发物料素材引用（成片片段/封面素材按配置裁剪规则）；素材引用与元数据齐备
- 场景：成片 → 物料素材齐备；宣发侧校验通过

## C9 交接的拒绝语义（澄清 Q1 落地）

- 上游未过门禁/不合格 → 下游**拒绝启动**并注明上游原因（不静默降级、不伪造输入）
- 中间环节候选全不合格 → 环节 `failed` → 运行终止（记录全部候选判 0 理由）
- 场景：上游 FAILED → 下游拒绝（机检：下游执行计数 0）；全部候选判 0 → 运行 failed
