"""外环周校准（功能 010）：人评/平台真值锚点 → 偏差检测 → 权重再拟合提案。

- 原始锚点落 DB 表 calibration_anchors（迁移 0004，INSERT-only 触发器强制冻结）；
- 派生产物（轮次/台账/报告/提案）文件化，随 git 版本化（research 决策 1/2）；
- 本包业务无关：promo 平台真值适配在 agents/promo/anchors.py（单向依赖）。
"""
