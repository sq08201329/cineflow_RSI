# 契约：降级模式治理（提交通道 + 回放沙盘 + 采纳留痕 + 禁止自动进化）

> 对应规格 US3 / FR-007~009。实现：`agents/screenplay/policy_versions.py`、`sandbox_compare.py`、
> `adoption.py`，以及 dreaming 侧的拒绝语义。

## C12 禁用自动进化（宪章原则六的工程落点）

```
dreaming.pipeline.run_dream_round(agent_id=...)   # agent_id ∈ cfg.no_auto_evolve_agents → 拒绝
```

- 配置 `dreaming.no_auto_evolve_agents: [screenplay, dev]`（默认值断言测试兜底）
- 命中即抛 `AutoEvolutionForbiddenError`（**显式拒绝，非静默跳过**），消息注明宪章原则六
- 契约测试：候选生成前拒绝 + 审计断言（dreaming 为 screenplay 生成候选的次数恒 0）

### 场景

1. `run_dream_round(agent_id="screenplay")` → 立即拒绝，未生成任何候选、未计费
2. 默认配置包含 `screenplay`（防配置漂移）
3. 其他 Agent（如 visual）不受影响（回归）

## C13 人工策略提交通道

```
submit_policy(source_text, submitter, cfg) -> HumanPolicyVersion   # 版本 = 源码 BLAKE3 前 12 位
```

- 落 `policies/history/screenplay/{version}.py` + meta.json（parent_version / submitter /
  time / 静态检查结果），复用 002 静态检查；未过检查即拒绝且不入历史
- 草稿不入历史、不参与回放；**参数调整走 configs**（不产生策略版本——澄清 Q2）

### 场景

1. 合法策略提交 → 版本化落盘、meta 含 parent_version 与提交人
2. 违规策略（import socket / 签名错）→ 拒绝且历史目录无新增
3. 同源码重复提交 → 同版本号，幂等（不重复落盘）

## C14 回放对比与采纳

```
compare_versions(new_version, deployed_version, pool, cfg) -> ReplayComparison
adopt(comparison_id, decision, by, reason) -> AdoptionRecord
```

- 对比：逐树得分、分项评估器差异、pareto_auc 曲线（复用 `dreaming/reward.py` 口径）、
  UNKNOWN 覆盖说明；回放零 LLM（C2）
- 采纳才更新部署指针；拒绝同样留痕（理由非空）；未采纳更新指针次数恒 0（SC-002 机检）

### 场景

1. 新版本优于部署版本 → 报告呈现优势；采纳后指针更新、记录落盘
2. 新版本全劣 → 报告如实呈现并建议保留现版本（不做"矮子里拔将军"）
3. 未采纳 → 部署指针不变（机检）
4. 回放命中 UNKNOWN → 该树 0 分 + 报告提示扩大记录（不编造）
