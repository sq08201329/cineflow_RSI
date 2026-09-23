"""开发 Agent（选题 / IP 评估 / 立项组合）——降级模式（功能 017）。

**降级模式**（宪章原则六）：记录-回放 + 人工策略 + 回放沙盘；**不做自动进化**——策略来源
是人，候选生成由 dreaming 层显式拒绝（`dreaming.no_auto_evolve_agents` 名单已含 `dev`）。

**评估器组合**（原则五：形态配置承载）：2 硬规则门禁（`rule.slate_structure` /
`rule.slate_combination`）+ 2 代理模型（`proxy.genre_regression` / `proxy.buzz_heat`，
驱动自**模拟数据源**，非真实商业数据）。**无 judge 层、无人类锚点**——技术方案只为本环节
规定"代理信号"一层，且 010 明确把开发 Agent 排除在周校准之外。

产出的"立项组合工件"、轮次循环、回放对比与判据材料分列于各模块；通用件（策略通道/回放
对比/采纳留痕/判据材料）下沉在业务无关的 `core/degraded/`。
"""
