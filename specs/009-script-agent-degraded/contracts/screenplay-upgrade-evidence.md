# 契约：升级判据材料与周校准接入

> 对应规格 FR-010~011、US3 场景 5/6 与 SC-006。实现：`agents/screenplay/upgrade_evidence.py`。

## C15 判据材料（澄清 Q1 口径）

```
build_upgrade_evidence(period, cfg, ledger, calibration) -> UpgradeEvidence
```

- 内容：判据阈值快照、原始数值（judge 信度相关系数与样本量、漂移指标、门禁违规分布、
  人评锚点计数）、**系统结论（meets | below）**（按配置阈值自动计算：相关系数 ≥ target、
  样本量 ≥ N、漂移在带内、门禁违规率 ≤ X）、推翻记录
- 阈值缺失即报错（不允许静默"无判据"）；信号不达标 → 结论 below + 显式标注"不得据此升级"
- 材料为**不可变快照**（每次生成一条，含当时阈值）

### 场景

1. 阈值达标数据 → 结论 meets，数值与阈值快照齐全
2. 样本不足 → below + 标注"样本不足"（不得暗示可升级）
3. 相关系数为负 → below + 告警条目
4. 人推翻 below 结论 → 推翻记录（人/时间/理由）追加，系统结论字段逐字节不变

## C16 周校准接入

- `build_blind_list(agent_id="screenplay")` 正常产出（仅 promo 被特判拒绝）；
  盲评对象 = 大纲阶段 top-k（每周 3~5 份，技术方案 §2.2 锚点口径）
- judge 信度数据来源 = 010 台账（screenplay 的 judge 分量配对）

### 场景

1. 010 盲评对 screenplay 产出清单（大纲阶段节点）
2. 信度报告含 screenplay judge 条目（相关系数/样本量/达标标记）

## C17 与 008 分镜的 schema 对接

```
export_segment(artifact) -> ScriptSegment   # 场景/台词/行动/关键行/情绪
```

- 导出函数确定性；schema 快照断言双向锁定（本侧导出 + 008 侧输入字段一致）
- 场景：导出 → 008 `validate_script` 通过（真实剧本产出可替换分镜夹具）
