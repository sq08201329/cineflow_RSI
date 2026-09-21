# Quickstart：跨项目发现树合并回放（011-cross-project-replay）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "merged_pool or cross_match or hit_stats or cross_lineage"
uv run pytest tests/contract -k pooling              # 构建/快照/匹配/稀释契约
uv run pytest tests/unbiasedness -k merged           # 合并口径 τ ≥ 0.95 + 注入拒绝
uv run pytest tests/unit -k "pooling_acceptance"        # 做梦开关默认关闭 + 前置注明
```

## 端到端场景（demo 流程）

1. **构建合并池**：项目 A/B 各 ≥2 棵同 Agent 同形态树 → 分组合并、版本分组、快照落盘
2. **跨项目命中**：B 项目缺失的结构键命中 A 的历史得分（不再 UNKNOWN）
3. **冲突口径**：A/B 同结构得分不同 → UNKNOWN + 冲突诊断
4. **稀释控制**：命中分布双报告 + 占比超阈告警（含"单项目构成"标注）
5. **一致性验收**：同树双池回放得分序列一致；合并口径 τ ≥ 0.95
6. **做梦开关**：默认单项目池（快照注明）；开启后合并池可用

## 里程碑验收（立项书 WS2 / SC-001）

合并池构建可重现 + 跨项目命中 + 稀释告警可机检 + 合并口径 τ ≥ 0.95；覆盖率 ≥85% 不降。
