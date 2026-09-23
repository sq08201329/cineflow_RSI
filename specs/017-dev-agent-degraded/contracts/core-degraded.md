# 契约：通用件抽取（`core/degraded/`）与 009 行为等价

> 对应规格 FR-008~010 的机制面、plan.md 项目结构 / 阶段 0 决策 1~3。实现：`core/degraded/` 四模块（新）
> + `agents/screenplay/{policy_versions,sandbox_compare,adoption,upgrade_evidence}.py` 薄适配。
>
> ⚠️ **前置裁决（plan.md D1）**：`compare.py` 的策略执行隔离与宪章原则四冲突（现状为同进程
> `exec`，未过 002 沙箱）。本契约涉及 `compare.py` 的实现须在裁决路径 (a) 沙箱化 或 (b) 宪章
> 豁免 落盘后动手；**裁决前不得以"沿用 009 现状"为由开工**。

## C1 通用件落点与业务无关性

```
core/degraded/policy.py    submit_policy(source_text, submitter, cfg, *, agent_id, history_root,
                                         parent_version=None, draft=False) -> HumanPolicyVersion
core/degraded/compare.py   compare_versions(new_version, deployed_version, pool, cfg, *, store, inputs,
                                            unbiasedness, match_key, min_comparable_trees, agent_id,
                                            history_root, comparison_dir) -> ReplayComparison
core/degraded/adoption.py  adopt(comparison_id, decision, by, reason, *, config_path, agent_id,
                                 comparison_dir, adoption_dir, history_root) -> AdoptionRecord
core/degraded/evidence.py  build_upgrade_evidence(period, cfg, items, *, agent_id, data_dir) -> UpgradeEvidence
                           # items = ({key, threshold_key, provider}, …)：provider 回实测值，无来源则 None + 缺失原因
```

- 四模块 = 009 同名模块的机制抽出（决策 1/2）；参数化 = `agent_id` + 注入可调用（匹配键
  `match_key(stage, *, policy_version, inputs, config, markers)`、评估器装配、判据项提供者）；**不用**继承与注册表魔法。
- `core/degraded/` 不得 import `agents.*`（原则五）；业务件（工件 schema、评估器、匹配键、轮次循环）留 `agents/dev/`；
  `agent_id` 只作参数值，不得出现 `agent_id == …` 分支。
- 机检（既有断言递归覆盖 `core/`，新包无需改测试）：`tests/unit/test_form_switch.py:291-314`（BANNED_LITERALS
  `("shortdrama", '"movie"', "'movie'")` + BANNED_PATTERNS 形态判断）；`tests/unit/test_no_vendor_literals.py:21-24`
  （`core/agents/dreaming` 内零厂商词、零配置声明的档案 id/端点）。
- **新增断言**（既有套件无 Agent 名禁令）：`core/degraded/` 无 Agent 名与业务词，先例 =
  `tests/unit/test_orchestration_executor.py:317-336`（现只扫 `core/orchestration/*.py`，不递归）。

### 场景

1. 扫描 `core/degraded/**/*.py`：无形态字面量 / 厂商字面量 / Agent 名 / 业务词 → 全绿；零 `from agents.`
2. 同一份 `policy.py` 以 `agent_id="screenplay"` 与 `"dev"` 调用 → 各落 `{history_root}/{agent_id}/`
   （`agents/screenplay/__init__.py:12`"骨架是开发 Agent 的复用对象"落地，无第二份实现）

## C2 009 行为等价（薄适配）

```
agents/screenplay/policy_versions.py
  submit_policy(source_text, submitter, cfg, *, parent_version=None, draft=False, history_root, agent_id)
  / load_policy_source(version, *, history_root, agent_id) / read_policy_meta / list_policy_versions
agents/screenplay/sandbox_compare.py
  compare_versions(new_version, deployed_version, pool, cfg, *, store, inputs, unbiasedness=None,
                   history_root, agent_id, comparison_dir) / replay_policy / load_comparison / parse_created_at
agents/screenplay/adoption.py
  adopt(comparison_id, decision, by, reason, *, config_path, comparison_dir, adoption_dir, history_root, agent_id)
  / deployed_version / load_adoption_record
agents/screenplay/upgrade_evidence.py
  build_upgrade_evidence(period, cfg, ledger, calibration, *, drift=None, gate_violation_rate=None,
                         human_anchor_count=0, data_dir, agent_id)
  / load_evidence / override_conclusion / judge_reliability / gate_violation_rate_of
```

- 四处只做适配：上述导出名、签名、关键字参数与默认值**逐字不变**（`agent_id` 默认 `"screenplay"`、
  `history_root="policies/history"`）；函数体只注入 `agent_id` 与业务可调用（`loop.stage_match_key`、评估器装配、判据项）。
- 同名类与异常同样保留：`HumanPolicyVersion`/`PolicySubmissionError`、`ReplayComparison`/`UnbiasednessAttestation`/
  `CompareError`、`AdoptionRecord`/`AdoptionError`/`DECISIONS`、`UpgradeEvidence`/`UpgradeEvidenceError`。
- 默认目录不变（例外仅 C3）：`screenplay/comparisons/`（`sandbox_compare.py:32`）、`screenplay/adoptions/`
  （`adoption.py:29`）、`policies/history/screenplay/`（`policy_versions.py:31`）。
- 回归门禁：`uv run pytest tests/unit tests/contract tests/unbiasedness -k screenplay` 全绿（改造前基线实测
  449 passed）；`ops/demo_screenplay_loop.py` 退出码 0、六步 ok；009 `quickstart.md:5-14` 验证命令逐条复跑。

### 场景

1. 四模块导出名与签名（含默认值）快照不变；三套件 `-k screenplay` 全绿 + demo 退出码 0（改造前后同结果）
2. 改 `core/degraded/` 内机制（如 append-only 落盘纪律）→ 009 与 017 同时生效（无第二份实现）

## C3 判据材料按 agent 分目录

```
calibration/upgrade-events/{agent_id}/{period}.json    # 009 有效路径：…/screenplay/{period}.json
```

- 动机（既有缺陷）：`agents/screenplay/upgrade_evidence.py:26`（`DEFAULT_EVIDENCE_DIR`）与 `:237`
  （`directory / f"{period}.json"`）不含 agent id → 两个降级 Agent 同周期互相覆盖（规格边界情况）。
- 采用 `{data_dir}/{agent_id}/{period}.json`：`data_dir` 仍是"根"（默认 `calibration/upgrade-events`）；
  `override_conclusion` 写同一路径；同周期已存在即拒绝（幂等口径不变）。
- **不做 legacy 读取**（明确裁决）：旧路径文件不读、不迁移、不改写，避免"同周期两份材料"二义性；
  009 既有 `calibration/upgrade-events/{period}.json` 原地保留（不可变快照不可回写）。
- 迁移同步：`tests/unit/test_screenplay_evidence.py:118`、`:139`、`:274` 与 `tests/unit/test_screenplay_cli.py:605`、
  `:662` 的路径断言加 `/screenplay/`；`ops/screenplay.py:572`、`ops/demo_screenplay_loop.py:476` 口径同步；
  009 `quickstart.md` 验证记录**追加**"路径迁移复核"条目（现记录未含该路径字面量，属追加非改写）；
  `README.md:303` 的写死路径一并同步（判断项）。

### 场景

1. 同 period 下 `screenplay` 与 `dev` 各产一份、互不覆盖；同 agent 同 period 重产 → 拒绝（只增不改）
2. 路径断言更新后 `-k screenplay` 全绿；`calibration/upgrade-events/` 既有旧文件逐字节不变
