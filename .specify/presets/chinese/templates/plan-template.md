# 实现计划：[FEATURE]

**分支**: `[###-feature-name]` | **日期**: [DATE] | **规格说明**: [链接]

**输入**: 来自 `/specs/[###-feature-name]/spec.md` 的功能规格说明

**注意**: 本模板由 `__SPECKIT_COMMAND_PLAN__` 命令填充。参见 `.specify/templates/plan-template.md` 了解执行工作流。

## 概要

[从功能规格中提取：主要需求 + 技术方案]

## 技术背景

<!--
  必填：替换为本项目的技术细节。
-->

**语言/版本**: [例如：Python 3.11、Swift 5.9、Rust 1.75 或 需要澄清]

**主要依赖**: [例如：FastAPI、UIKit、LLVM 或 需要澄清]

**存储**: [如适用：PostgreSQL、CoreData、文件 或 不适用]

**测试**: [例如：pytest、XCTest、cargo test 或 需要澄清]

**目标平台**: [例如：Linux 服务器、iOS 15+、WASM 或 需要澄清]

**项目类型**: [例如：库/CLI/Web 服务/移动应用/编译器/桌面应用 或 需要澄清]

**性能目标**: [例如：1000 req/s、10k 行/秒、60 fps 或 需要澄清]

**约束**: [例如：p95 响应时间 <200ms、内存 <100MB、支持离线 或 需要澄清]

**规模/范围**: [例如：1 万用户、100 万行代码、50 个页面 或 需要澄清]

## 宪章检查

*门禁：必须在阶段 0 调研前通过。阶段 1 设计后重新检查。*

[根据宪章文件确定的门禁]

## 项目结构

### 文档（此功能）

```text
specs/[###-feature]/
├── plan.md              # 本文件（__SPECKIT_COMMAND_PLAN__ 命令输出）
├── research.md          # 阶段 0 输出（__SPECKIT_COMMAND_PLAN__ 命令）
├── data-model.md        # 阶段 1 输出（__SPECKIT_COMMAND_PLAN__ 命令）
├── quickstart.md        # 阶段 1 输出（__SPECKIT_COMMAND_PLAN__ 命令）
├── contracts/           # 阶段 1 输出（__SPECKIT_COMMAND_PLAN__ 命令）
└── tasks.md             # 阶段 2 输出（__SPECKIT_COMMAND_TASKS__ 命令——不由 __SPECKIT_COMMAND_PLAN__ 创建）
```

### 源代码（仓库根目录）

<!--
  必填：用实际布局替换下面的占位树。删除未使用的选项，用真实路径展开所选结构。
-->

```text
# [如不适用请删除] 选项 1：单一项目（默认）
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [如不适用请删除] 选项 2：Web 应用（检测到"前端"+"后端"时）
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [如不适用请删除] 选项 3：移动端 + API（检测到"iOS/Android"时）
api/
└── [同 backend 结构]

ios/ 或 android/
└── [平台特定结构]
```

**结构决策**: [记录所选结构并引用上述实际目录]

## 复杂度跟踪

> **仅在宪章检查有违规且必须说明理由时填写**

| 违规项 | 为何需要 | 被拒绝的简化方案 |
|--------|----------|-----------------|
| [例如：第 4 个项目] | [当前需求] | [为何 3 个项目不够] |
| [例如：Repository 模式] | [具体问题] | [为何直接 DB 访问不够] |
