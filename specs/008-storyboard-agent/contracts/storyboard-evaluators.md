# 契约：分镜五评估器与合成评分

> 对应规格 US2 / FR-006~009。实现：`agents/storyboard/evaluators/`。

## C4 rule.shot_grammar（gate）

相邻镜头景别跳跃 ≤ 规则库上限（景别有序枚举的序号差）、同景别连续 ≤ 上限；违规 → 判 0。

## C5 rule.coverage（gate，澄清 Q1 口径）

每场景 ≥1 镜（硬要求）；`key=True` 行逐条被 covers 承接；普通台词合并/拆分不违规；
必覆盖清单为空 → 降级纯场景级并在 diagnostics 注明。违规 → 判 0。

## C6 rule.axis_rule（gate）

机位侧别（side: A|B）跳变必须有过渡镜头（`allowed_transition_shots` 配置，默认 1）；
无过渡的侧别硬跳 → 判 0；同侧连续/合法过渡 → 通过。

## C7 proxy.emotion_alignment（连续，澄清 Q2 口径）

**输入 = 预演画面帧像素**（分镜卡帧，由 ShotList 确定性派生）：确定性直方图/色板特征
向量 vs 情绪基调配置向量的余弦 → 映射得分（定点 6 位）；情绪缺失 → "不适用"注明；
对齐 vs 背离两组分差显著；同预演重评估逐位一致（误差 < 1e-6）。

## C8 judge.script_fit（连续）

输入 = ShotList 结构化摘要（summary.py，确定性）+ 剧本段落文本；3 固定提示词 × 冻结
锚点集（固定 ShotList 集）成对比较投票 → 胜率；平局 0.5 如实记录；LLM 全过网关计费
（Mock 后端确定性）；**版本号 = `1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}`**。

## C9 合成与版本

合成 = 三 gate + alignment/judge 加权（权重形态配置）+ quantize 6 位定点；gate 短路
不跑 judge（省 LLM 成本）；全部评估器 deterministic=True、实现哈希入版本号；
同预演重评估逐位一致（SC-004）。

### 场景（每评估器 ≥2 条）

1. 相邻景别跳跃超限 → grammar 判 0；合规序列通过
2. 场景无镜头 / 关键行未承接 → coverage 判 0；合并台词手法通过
3. 侧别硬跳无过渡 → axis 判 0；带过渡镜头通过
4. 对齐 vs 背离预演 → 分差显著、误差 < 1e-6、重评估逐位一致
5. judge 同比较重跑逐位一致；版本号含三段哈希
6. gate 违规 → judge 未被调用（网关调用计数不增）
