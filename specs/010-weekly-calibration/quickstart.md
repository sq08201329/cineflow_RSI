# Quickstart：外环周校准（010-weekly-calibration）

## 验证命令

```bash
uv sync
docker compose -f ops/dev.compose.yml up -d --wait postgres minio
uv run alembic -c ops/alembic.ini upgrade head    # 含 0004_calibration_anchors
uv run pytest tests/unit -k calibration           # 偏差数学/配对剔除/幂等/版本不变
uv run pytest tests/contract -k calibration       # 清单零泄露/提案门禁/台账 append-only
uv run pytest tests/integration -k calibration    # PG 触发器 INSERT-only 证明
uv run python ops/demo_calibration.py             # 端到端：盲评→录入→偏差→台账→提案→确认
```

## 端到端场景（demo 流程）

1. **生成盲评清单**：visual 夹具树（周期内 ≥5 节点）→ 清单 5 条，断言无 score 键
2. **录入人评**：夹具人评文件（含 1 条重复 + 1 条越界 score）→ 合法入库、重复拒绝、
   越界拒绝
3. **平台真值锚点**：promo 回流夹具 → platform_truth 锚点入库
4. **偏差与台账**：注入已知偏移 → mean_shift/pearson_r 断言；台账追加，注册版本不变
5. **信度报告**：JSON 产出，四要素 + meets_target 齐全
6. **提案与生效**：超阈 → pending 提案 → confirm → composite 新版本注册 +
   `configs/movie.yaml`（demo 副本）权重段定点更新且注释保留 → 历史节点审计一致

## 里程碑验收（立项书周 1~2 / SC-001）

首轮 top-k 盲评入库 + 权重再拟合提案流程完整走通（生成 → 确认/搁置 → 状态落盘）。

## 验证记录（2026-09-19，T530）

| 命令 | 结果 |
| --- | --- |
| `uv run pytest tests/unit -k calibration` | 62 passed |
| `uv run pytest tests/contract -k calibration` | 17 passed |
| `uv run pytest tests/integration -k calibration`（真实 PG） | 7 passed |
| `uv run python ops/demo_calibration.py` | 退出码 0（六步全通，verdict=PASS） |
| `uv run pytest tests/unit tests/contract` | 752 passed |
| 覆盖率 `uv run pytest tests/unit --cov=core --cov=agents --cov=dreaming --cov-fail-under=85` | TOTAL 92.39%（≥85% 达标）；core/calibration 各模块 88%~100%；pyproject `[tool.coverage.run] source=["core","agents","dreaming"]` 已自然覆盖 core/calibration，无需改动 |
| `uv run ruff check core agents ops tests` | All checks passed |

注：覆盖率全量跑时 visual 视频测试（test_visual_consistency / test_visual_flicker
的重算逐字节一致用例）各出现过一次失败，单独复跑均通过——判定为 x264 多线程编码
在 coverage 插桩负载下的既有抖动（一期 CI 已记录同类共享 runner 抖动），与本特性无关。
