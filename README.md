# test_case_gen

面向 CodeBuddy 的项目级测试设计规范包，用于统一测试设计、测试用例生成、性能测试规划和自动化建议的交付格式。

本仓库不是传统应用代码项目，而是一组可复制到业务项目根目录的 CodeBuddy 配置、规则、Skill 和 Excel 模板。导入后，CodeBuddy 可以按照统一规范理解需求、拆解测试场景，并生成结构化测试设计 Excel。

## 适用场景

- 敏捷用户故事测试设计
- 需求文档测试分析
- 接口文档测试用例生成
- PR Diff 或代码变更影响分析
- 缺陷单回归测试设计
- 原型图或页面说明测试设计
- 既有测试用例补充与优化
- 混合输入材料的测试分析

## 核心能力

- 提供项目级 `CODEBUDDY.md` Memory，固定团队测试设计偏好。
- 提供 `test-design` Skill，指导 CodeBuddy 按标准流程完成测试设计。
- 提供硬性质量 Rule，约束用例数量、字段格式、覆盖维度和 Excel 输出。
- 提供标准测试设计 Excel 模板，保证正式交付物结构一致。
- 覆盖功能测试、性能测试、风险识别、待确认问题和自动化建议。

## 目录结构

```text
test_case_gen/
├── CODEBUDDY.md
├── README.md
├── README_IMPORT.md
├── .codebuddy/
│   ├── skills/
│   │   └── test-design/
│   │       └── SKILL.md
│   ├── .rules/
│   │   └── test-design-rule.mdc
│   └── rules/
│       └── test-design-rule.md
└── docs/
    └── test-design/
        ├── README.md
        ├── excel-template-spec.md
        └── codebuddy-test-design-template.xlsx
```

## 关键文件

| 文件 | 说明 |
| --- | --- |
| `CODEBUDDY.md` | 项目级 Memory，声明长期测试设计偏好。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计 Skill，定义角色、输入识别、工作流、输出要求和自检规则。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 规则文件。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 兼容规则文件。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计 Excel 模板。 |
| `docs/test-design/excel-template-spec.md` | Excel 模板字段说明。 |
| `README_IMPORT.md` | 导入到其他项目的操作说明。 |

## Excel 模板 Sheet

正式测试设计交付物默认包含 7 个 Sheet：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`

## 测试设计工作流

CodeBuddy 使用本规范时，应按以下流程完成测试设计：

1. 识别输入类型，例如用户故事、需求文档、接口文档、PR Diff 或缺陷单。
2. 提取业务目标、角色、功能点、验收标准、业务规则、依赖系统和待确认问题。
3. 明确测试范围、不测范围、回归范围、测试环境、准入条件和准出条件。
4. 从权限、边界、状态流转、幂等性、数据一致性、第三方依赖、性能和安全等维度识别风险。
5. 使用等价类、边界值、判定表、状态迁移、场景法、风险驱动测试等方法建立测试场景矩阵。
6. 生成可执行、可验证、可追踪的功能测试用例。
7. 输出性能测试设计；如果不适用，也必须说明原因和后续监控建议。
8. 给出自动化建议，包括自动化层级、价值、优先级、依赖数据、Mock 需求和稳定性风险。
9. 交付前自检覆盖完整性、用例质量、编号换行格式、重复用例和待确认问题。

## 质量规则摘要

- 正式测试设计交付物必须是 Excel。
- 任何测试设计都必须包含性能测试内容。
- 敏捷用户故事每条至少生成 10 条功能测试用例。
- `前置条件`、`操作步骤`、`预期结果` 必须使用 `1. 2. 3.` 编号换行。
- 用例必须具备明确前置条件、测试数据、操作步骤和可验证预期结果。
- 每条测试场景和测试用例都必须关联 Story ID 或需求 ID、功能点、测试维度和风险或验收标准。
- 不允许通过重复、拆碎或同质化用例凑数量。

## 使用方式

将本仓库内容复制到目标项目根目录后，可以对 CodeBuddy 使用以下提示词：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下用户故事生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
要求每条用户故事至少 10 条功能测试用例，必须包含性能测试设计。
```

也可以使用更简短的方式：

```text
基于项目测试设计规范，为以下用户故事生成测试设计 Excel。
```

## 维护建议

- 团队测试字段变化时，同步更新 `docs/test-design/excel-template-spec.md` 和 Excel 模板。
- 测试策略变化时，同步更新 `CODEBUDDY.md`、Skill 和 Rule，避免规则不一致。
- 如果确认当前 CodeBuddy 只加载一种规则目录，可以只保留对应规则文件。
- 定期检查 Excel 模板中的示例行、枚举值、冻结表头和自动换行设置。

