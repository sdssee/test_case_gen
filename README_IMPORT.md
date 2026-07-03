# CodeBuddy 项目级测试设计配置导入说明

这个目录已经整理成项目级结构。把本目录中的全部内容复制到你的项目根目录后，CodeBuddy 就可以在该项目中读取测试设计 Skill、Rule、Memory 和 Excel 模板，并按统一规则处理需求文档、截图、原型和可访问页面。

## 复制后的项目结构

```text
your-project/
├── CODEBUDDY.md
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
        ├── codebuddy-test-design-template.xlsx
        └── excel-template-spec.md
```

## 每个部分的作用

`CODEBUDDY.md`

项目级 Memory。用于告诉 CodeBuddy 本项目长期采用的测试设计偏好，例如 Excel 输出、每个用户故事至少 10 条用例、必须包含性能测试、页面元素覆盖清单、前置条件/操作步骤/预期结果必须编号换行。

`.codebuddy/skills/test-design/SKILL.md`

项目级 Skill。用于告诉 CodeBuddy “如何做测试设计”：先做需求理解、业务逻辑抽取、页面元素识别和风险识别，再拆测试场景、生成测试用例、规划性能测试、输出自动化建议。

`.codebuddy/.rules/test-design-rule.mdc`

CodeBuddy IDE 项目规则文件。用于强制约束测试设计输出质量。

`.codebuddy/rules/test-design-rule.md`

CodeBuddy Code/CLI 兼容规则文件。内容与 `.codebuddy/.rules/test-design-rule.mdc` 一致，用于兼容不同 CodeBuddy 入口。

`docs/test-design/codebuddy-test-design-template.xlsx`

测试设计 Excel 模板。正式输出测试设计时，让 CodeBuddy 按这个模板生成。

`docs/test-design/excel-template-spec.md`

Excel 字段说明，方便 CodeBuddy 和团队成员理解每个 Sheet 的结构，包括页面元素覆盖清单字段。

## 推荐使用提示词

导入后可以这样对 CodeBuddy 说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下用户故事生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
要求每条用户故事至少 10 条功能测试用例，必须包含性能测试设计。
```

处理页面截图、原型或可访问页面时可以这样说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
我会提供需求文档和页面截图/页面地址，请先识别页面所有可点击和可交互元素，再生成测试设计 Excel。
要求功能测试用例覆盖所有按钮、链接、菜单、图标按钮、筛选、分页、弹窗和表格行操作；无法确认的元素写入页面元素覆盖清单和待确认问题。
页面具体业务逻辑请参考设计文档、需求说明、接口文档和验收标准；如果文档只写大概功能，请结合页面入口补全测试场景，并把无法确认的业务规则登记为待确认问题。
页面元素覆盖清单仅用于记录页面元素、覆盖状态和关联用例 ID；测试步骤和预期结果必须写入功能测试用例 Sheet，性能场景必须写入性能测试设计 Sheet。
```

也可以更短：

```text
基于项目测试设计规范，为以下用户故事生成测试设计 Excel。
```

## 注意

如果 CodeBuddy 同时加载 `.codebuddy/.rules` 和 `.codebuddy/rules`，可能会看到重复规则，但内容一致，不影响结果。若你确认当前 CodeBuddy 只使用其中一种规则目录，可以保留对应目录，删除另一份规则文件。
