# 契约：剧本七评估器与合成评分

> 对应规格 US2 / FR-003~006。实现：`agents/screenplay/evaluators/`。

## C4 rule.beat_structure（gate）

工件结构化标记的节拍清单对照配置节拍表：三幕/序列结构可解析 + 关键节拍全部存在；
缺失/不可解析 → 判 0。

## C5 rule.page_minutes（gate）

总行数 ÷ `lines_per_page`（配置）→ 页数 ≈ 目标时长（分钟），∈ 目标 ± 容差；越界 → 判 0。

## C6 rule.scene_character（gate）

逐场景：出场角色 ∈ 角色表（幽灵角色 → 判 0）、地点一致（场景头地点与标记一致）；
角色表缺失 → 拒绝启动并报错（配置纪律）。

## C7 rule.dialogue_action_ratio（gate）

对白行 / 动作行比例 ∈ 配置区间；越界 → 判 0。

## C8 proxy.entity_consistency（连续）

角色名规范化（别名表）后的一致性：同名异写/未登记别名/指代歧义 → 扣分并产诊断；
确定性、实现哈希入版本号。

## C9 proxy.timeline_conflict（连续）

场景 `time_marker` 与剧本内部既定顺序的单调性冲突检测；冲突 → 扣分并逐条诊断。

## C10 judge.dramatic_tension（连续，**仅 outline 阶段**）

- 输入 = 大纲结构化摘要（summary.py 确定性产出）；3 judge 投票成对比较；平局 0.5 如实记录
- 版本号 = `1.0.0+j{提示词前8}{锚点集前8}{摘要函数前8}`
- 非 outline 阶段 → "不适用"，合成按适用分量归一（不伪造 0 分拖底）

## C11 合成与版本

合成 = 四 gate + 适用 proxy/judge 加权（权重配置）+ quantize 6 位定点；gate 短路不跑
judge；全部评估器 deterministic=True、实现哈希入版本号；同工件重评估逐位一致（SC-004）。

### 场景（每评估器 ≥2 条）

1. 缺关键节拍 → beat_structure 判 0；完整节拍通过
2. 页数越界 → page_minutes 判 0；界内通过
3. 幽灵角色 / 地点不一致 → scene_character 判 0
4. 比例失衡（对白 >90%）→ dialogue_action_ratio 判 0
5. 同名异写注入 → entity_consistency 扣分并诊断；正确写法满分
6. 时间线矛盾注入 → timeline_conflict 命中
7. 张力高/低大纲 → judge 胜率分差显著；scenes/script 阶段 judge "不适用"
8. gate 违规 → judge 未被调用（网关计数不增）
