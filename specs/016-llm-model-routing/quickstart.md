# Quickstart：LLM 模型档案与角色路由（016-llm-model-routing）

## 验证命令

```bash
uv sync
uv run pytest tests/unit -k "llm_profile or routing or gateway"     # 解析/校验/迁移/路由/折算
uv run pytest tests/contract -k llm                                  # 契约 C1~C10
uv run python ops/check_credentials.py                               # 档案化就绪矩阵（零网络）
uv run python ops/smoke_llm.py --profile deepseek-flash              # 按档案冒烟（需凭证）
uv run python ops/smoke_llm.py --config configs/movie.yaml --round --dry-run   # 装配校验（零花费）
```

## 端到端场景（demo 流程）

1. **档案解析**：单档案（现状等价）/ 多档案 / 缺价目报错 / 零价目需显式声明
2. **角色路由**：`judge` → 强模型、`dreaming_candidates` → 本地模型；未映射回默认；枚举外报错
3. **成本分解**：多档案一轮 → 账目分解到"角色 × 档案"，金额与各自价目相符
4. **凭证中立**：环境存在无关 `OPENAI_API_KEY` 时，不参与任何档案就绪判定
5. **迁移**：旧扁平配置 → 自动映射单档案 + 启动报告列出映射规则
6. **冻结可证伪**：改配置价目后重跑审计 → 历史节点成本不变

## 里程碑验收（SC-001~007）

换厂商 = 仅改配置（静态断言零厂商字面量）+ 历史成本不漂移 + 缺项 100% 报错 +
凭证假阳性归零 + 单档案等价现状（既有测试全绿）+ 机检锁可证伪；覆盖率 ≥85% 不降。
