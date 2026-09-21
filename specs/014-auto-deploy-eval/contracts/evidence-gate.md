# 契约：证据包与门槛判定

> 对应规格 US1 / FR-001~003。实现：`core/deployment/evidence.py`、`gate.py`。

## C1 证据包

```
collect_evidence(agent_id, candidate_version, *, pool, store, cfg, data_dir) -> EvidenceBundle
```

- **前置** = 无偏性验收结论（未通过/缺失 → 前置失败，整体"证据不足"）
- 三要件：①回放 reward 对比（011 池化口径 vs 现部署版本）②validation 排名（005 口径）
  ③漂移 verdict（012 `deploy_evidence_verdict` 对全部相关 judge 版本；无 judge 的 Agent
  标记 `not_applicable`）
- 口径全部复用既有实现（不新造对比/排名/漂移逻辑）

### 场景

1. 三要件齐备 + 前置通过 → 证据包完整（各要件取值与来源引用齐全）
2. 无偏性未通过 → 前置失败（证据包标注"证据不足"）
3. 无 validation 集 / 无池化回放结果 / 无漂移数据 → 对应要件 `missing`
4. promo/sound（无 judge）→ 漂移要件 `not_applicable`（**默认不放宽整体门槛**）

## C2 门槛判定（缺证据即拦截）

```
gate(bundle, cfg) -> GateVerdict
```

- 优先级：`forbidden_agent`（009 名单）> `insufficient_evidence`（前置失败或要件 missing）>
  `blocked`（逐要件 unsatisfied）> `eligible`
- **三要件同时满足** 才 `eligible`；`not_applicable` 的 drift 要件在
  `allow_without_judge=false`（默认）下不构成"满足"（整体仍拦截）

### 场景

1. 全满足 → eligible
2. 单要件不满足（reward 不高于现部署 / validation 跌出前 20% / 漂移 suspect）→ blocked + 理由
3. 任一要件 missing 或前置失败 → insufficient_evidence
4. screenplay/dev → forbidden_agent（优先级最高，即使其他全满足）
5. 无 judge 且 allow_without_judge=false → blocked（默认保守）

## C3 证据快照

- 每次判定落 `deployment/evidence/{agent}/{candidate}.{ts}.json`（不可改写；同判定重复
  → 幂等）
- 场景：重复判定同候选同证据 → 幂等；证据变化 → 新快照（旧快照保留）
