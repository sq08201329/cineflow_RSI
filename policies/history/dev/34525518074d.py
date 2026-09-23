"""人工编写的题材方向探索策略（开发 Agent 引导树 / 谱系根，功能 017）。

降级模式（宪章原则六）：开发环节的评估信号噪声最大，策略来源是**人**——本文件由选题负责人
编写并提交（版本 = 源码 BLAKE3 前 12 位；谱系根 `parent_version = null`）。**不做自动进化**：
`dreaming.no_auto_evolve_agents` 名单含 `dev`，候选生成一律显式拒绝；改进版必须由人按
`ops/dev.py submit --parent-version` 提交，经静态检查后在回放沙盘上与现部署版本零成本对比，
**人工采纳**才更新部署指针。

策略只定**结构**：探索哪些题材方向、一轮探索多少条（落在形态配置 `dev.slate` 区间）、
组合内哪一条本轮进入生产（落在 `dev.production_marks` 区间）。每条方向的立项论证要点正文由
执行器经 LLM 网关生成（计费入账、响应哈希落盘，原则三）；人写的是判断力——方向池、受众取舍、
风险与论证草稿，不是评分公式（评分由二门禁 + 两代理模型给出，实测分量以节点 `eval_breakdown`
为准）。
"""


class Policy:
    """题材方向探索策略（人工编写）：按题材边界与受众取舍方向池，产出立项组合计划。"""

    # 方向池：方向标识 / 题材 / 题材约束要点 / 角色设定要点 / 受众标签 / 论证草稿
    DIRECTIONS = (
        {
            "direction_id": "dir-midnight-ward",
            "genre": "医疗悬疑",
            "constraints": ("单场景为主", "夜戏", "低成本"),
            "characters": ("值班护士 林静", "住院医 陈默"),
            "audiences": ("都市女性", "悬疑受众"),
            "rationale_seed": "夜班病房是天然的密闭空间：信任与隐瞒在同一个夜班里互相试探。",
        },
        {
            "direction_id": "dir-river-town",
            "genre": "都市犯罪",
            "constraints": ("群像", "夜戏为主", "中成本"),
            "characters": ("刑警 方原", "线人 邵岚"),
            "audiences": ("悬疑受众", "男性受众"),
            "rationale_seed": "小城的旧案与新的权力结构撞在一起，真相的代价由留下的人承担。",
        },
        {
            "direction_id": "dir-second-sun",
            "genre": "科幻悬疑",
            "constraints": ("高概念", "单一场景", "中成本"),
            "characters": ("观测员 江离", "系统 织女"),
            "audiences": ("科幻受众", "z 世代"),
            "rationale_seed": "当观测者开始怀疑观测数据，唯一可信的只剩自己的记忆——而它也被改过。",
        },
        {
            "direction_id": "dir-third-floor",
            "genre": "家庭剧情",
            "constraints": ("室内戏", "小成本", "克制叙事"),
            "characters": ("长女 苏禾", "母亲 苏玉兰"),
            "audiences": ("都市女性", "家庭受众"),
            "rationale_seed": "一场关于照护的谈判：谁留下、谁离开，是同一个问题的两种答案。",
        },
        {
            "direction_id": "dir-broken-seal",
            "genre": "古装权谋",
            "constraints": ("架空朝代", "中成本", "群像"),
            "characters": ("少帝 萧砚", "权臣 裴照"),
            "audiences": ("古装受众", "悬疑受众"),
            "rationale_seed": "印玺与人心同时松动：少年君主必须在被废之前学会当皇帝。",
        },
        {
            "direction_id": "dir-summer-road",
            "genre": "公路喜剧",
            "constraints": ("外景为主", "轻喜剧", "低成本"),
            "characters": ("货车司机 阿康", "搭车客 小满"),
            "audiences": ("家庭受众", "z 世代"),
            "rationale_seed": "两个陌生人共用一条省道：说得越多，越难在终点说再见。",
        },
        {
            "direction_id": "dir-open-plan",
            "genre": "现实职场",
            "constraints": ("室内戏", "低成本", "群像"),
            "characters": ("项目负责人 何雨", "新同事 罗一"),
            "audiences": ("都市女性", "职场受众"),
            "rationale_seed": "开放办公区里没有秘密，只有还没被听见的辞职理由。",
        },
        {
            "direction_id": "dir-last-rehearsal",
            "genre": "青春成长",
            "constraints": ("室内外结合", "小成本", "音乐元素"),
            "characters": ("主唱 沈知", "鼓手 田甜"),
            "audiences": ("z 世代", "青春受众"),
            "rationale_seed": "最后一次排练之后，四个人要决定乐队是解散还是改名继续。",
        },
    )

    def plan(self, inputs, config):
        bounds = inputs["genre_bounds"]
        audience = inputs["audience"]
        ranked = self.rank(bounds, audience)
        branch_count = self.branch_count(config.slate_entries, len(ranked))
        entries = [
            self.entry_of(direction, audience, bounds) for direction in ranked[:branch_count]
        ]
        return {"entries": entries, "production_marks": self.marks_of(entries, config)}

    def branch_count(self, interval, available):
        """分支数：落在形态区间内取上界，方向池不足时按池大小如实产出（不硬凑）。"""
        count = min(interval[1], available)
        return count if count >= interval[0] else available

    def rank(self, bounds, audience):
        """方向排序：题材边界命中优先、受众亲和度次之，末位用方向标识保证确定性。"""
        scored = [
            (self.affinity(direction, bounds, audience), direction)
            for direction in self.DIRECTIONS
        ]
        ordered = sorted(scored, key=self.sort_key, reverse=True)
        return [direction for score, direction in ordered]

    def sort_key(self, item):
        return (item[0], item[1]["direction_id"])

    def affinity(self, direction, bounds, audience):
        score = 0
        for bound in bounds:
            if bound in direction["genre"]:
                score += 3
            elif bound in direction["constraints"]:
                score += 1
        if audience in direction["audiences"]:
            score += 2
        return score

    def entry_of(self, direction, audience, bounds):
        constraints = list(direction["constraints"])
        constraints.append("受众：" + audience)
        constraints.append("题材边界：" + "／".join(bounds))
        return {
            "direction_id": direction["direction_id"],
            "genre": direction["genre"],
            "constraints": constraints,
            "characters": list(direction["characters"]),
            "rationale_seed": direction["rationale_seed"],
        }

    def marks_of(self, entries, config):
        """本轮进入生产的指向：按排序取前 N 条（N = 标记区间上界与条目数的较小者）。

        组合内无达标条目时如实产出 0 标记（越界由组合门禁判 0 并点名），不降格硬凑。
        """
        count = min(config.production_marks[1], len(entries))
        return [entry["direction_id"] for entry in entries[:count]]
