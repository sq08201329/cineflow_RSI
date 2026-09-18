---
name: speckit-constitution
description: 创建或更新项目宪章
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: github-spec-kit
  source: preset:chinese
---

# Speckit Constitution Skill

请按项目宪章模板（`.specify/templates/constitution-template.md`）创建或更新宪章。

**语言要求**：所有输出内容使用中文。

遵循标准的宪章创建流程：
1. 加载现有宪章或从模板创建
2. 与用户确认核心原则
3. 填写占位符，输出完整的宪章文件
4. 保存到 `.specify/memory/constitution.md`
