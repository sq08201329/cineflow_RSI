"""人工剧本策略首版（降级模式：策略 = 人编写的代码，不自动进化）。

工艺（人写的结构探索策略，接口 plan(inputs, config) 由 US1 定案）：
- 按目标页数铺满行数（页数-时长换算门禁对**每个阶段工件**生效，故三阶段各自铺满）；
- 阶段工艺由粗到细：大纲按幕块（135 行/块）→ 分场（45 行/场）→ 剧本定稿（27 行/场）；
- 节拍表取配置 required 项（策略不重复声明门禁口径，原则五单一事实源）；
- 对白/动作交替（对白行占比 ≈ 0.5，落在配置区间内）；每场首行标关键行；
- 场景头三段式（内景|外景 - 地点 - 时间描述）与 location/time_marker 机检字段一致，
  剧内时间戳按场景递增（无时间线回退）；
- 出场角色取输入角色设定（缺省用本策略的角色表），角色表即登记口径（无幽灵角色）。
"""


class Policy:
    """人工剧本策略（三阶段结构铺排；纯计算，无 IO/无网络）。"""

    # 阶段工艺：每场（块）行数——大纲粗、剧本细
    LINES_PER_SCENE = {"outline": 135, "scenes": 45, "script": 27}
    # 场景模板循环（地点/内外景/时间描述/出场角色下标）
    SCENES = (
        {"location": "病房", "prefix": "内景", "time_desc": "夜", "cast": (0, 1, 2)},
        {"location": "走廊", "prefix": "内景", "time_desc": "夜", "cast": (0, 2)},
        {"location": "天台", "prefix": "外景", "time_desc": "清晨", "cast": (1, 0)},
        {"location": "手术室外", "prefix": "内景", "time_desc": "日", "cast": (0, 1, 2)},
        {"location": "旧公寓", "prefix": "内景", "time_desc": "黄昏", "cast": (0, 1)},
    )
    DEFAULT_CHARACTERS = ("林静", "陈默", "周医生")
    EMOTIONS = ("calm", "tense", "sorrow", "joyful", "awe")
    STAGE_LABEL = {
        "outline": "大纲节拍",
        "scenes": "分场要点",
        "script": "剧本行",
    }

    def plan(self, inputs, config):
        """产分阶段计划：{stage: {beats, scenes, characters, lines}}。"""
        target_minutes = int(inputs.get("target_duration_min", config.target_duration_min))
        total_lines = max(1, target_minutes * int(config.lines_per_page))
        beats = [
            {
                "beat_id": beat["beat_id"],
                "act": beat["act"],
                "required": beat["required"],
                "description": beat["description"],
            }
            for beat in config.beat_sheet
            if beat["required"]
        ]
        characters = self.character_table(inputs)
        plans = {}
        for stage in self.LINES_PER_SCENE:
            plans[stage] = self.stage_plan(stage, inputs, total_lines, beats, characters)
        return plans

    def character_table(self, inputs):
        """角色表：输入角色设定优先（同源），缺省用本策略默认角色。"""
        names = [name for name in inputs.get("characters", []) if name]
        if not names:
            names = list(self.DEFAULT_CHARACTERS)
        return [{"name": name, "aliases": []} for name in names]

    def stage_plan(self, stage, inputs, total_lines, beats, characters):
        """单阶段结构：铺满目标行数，按场景模板循环排布。"""
        per_scene = self.LINES_PER_SCENE[stage]
        scene_count = max(1, total_lines // per_scene)
        base, remainder = divmod(total_lines, scene_count)
        topic = inputs.get("topic", "未命名题材")
        label = self.STAGE_LABEL[stage]
        scenes = []
        lines = []
        for index in range(scene_count):
            template = self.SCENES[index % len(self.SCENES)]
            cast = [characters[slot % len(characters)]["name"] for slot in template["cast"]]
            scene_id = "scene-" + str(index + 1)
            scenes.append(
                {
                    "scene_id": scene_id,
                    "heading": " - ".join(
                        (template["prefix"], template["location"], template["time_desc"])
                    ),
                    "location": template["location"],
                    "time_marker": index * 30,
                    "characters": list(cast),
                    "axis_base": "A" if index % 2 == 0 else "B",
                }
            )
            count = base + (1 if index < remainder else 0)
            for offset in range(count):
                name = cast[offset % len(cast)]
                is_dialogue = offset % 2 == 0
                lines.append(
                    {
                        "line_id": "s" + str(index + 1) + "-l" + str(offset + 1),
                        "scene_id": scene_id,
                        "kind": "dialogue" if is_dialogue else "action",
                        "text": self.line_text(
                            stage, topic, template["location"], name, label, offset, is_dialogue
                        ),
                        "character": name if is_dialogue else None,
                        "key": offset == 0,
                        "emotion": self.EMOTIONS[(index + offset) % len(self.EMOTIONS)],
                    }
                )
        return {
            "beats": beats,
            "scenes": scenes,
            "characters": [dict(character) for character in characters],
            "lines": lines,
        }

    def line_text(self, stage, topic, location, name, label, offset, is_dialogue):
        counter = str(offset + 1)
        if is_dialogue:
            return name + "（" + stage + " 第 " + counter + " 句）：关于" + topic + "，我必须说清楚。"
        return location + "的动作点 " + counter + "：" + label + "推进" + topic + "的冲突。"
