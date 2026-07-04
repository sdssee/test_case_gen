# test_case_gen

项目级测试设计规范包，用于让 CodeBuddy/Codex 按统一规则生成测试设计 Excel、功能测试用例、性能测试设计、页面元素覆盖清单、风险与待确认问题和自动化建议。

本仓库不是业务应用代码。它提供可复制到业务项目根目录的 Memory、Skill、Rule 和 Excel 模板。

## 适用场景

- 用户故事、需求文档、接口文档、PR Diff、缺陷单的测试设计
- 页面截图、原型图、可访问页面的可交互元素覆盖
- 已有测试用例补充、优化和回归测试设计
- 需要生成测试系统导入文件的测试用例交付

## 关键文件

| 文件 | 说明 |
| --- | --- |
| `AGENTS.md` | Codex 项目级执行说明。 |
| `CODEBUDDY.md` | CodeBuddy 项目级 Memory。 |
| `docs/ARCHITECTURE.md` | AI 规则分层、模板契约和维护边界。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计 Skill。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 规则。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 规则。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计模板，包含 8 个标准 Sheet。 |
| `docs/test-design/测试用例模板.xlsx` | 测试系统导入模板。需要导入时复制该模板生成独立导入文件，不修改原模板。 |
| `docs/test-design/excel-template-spec.md` | Excel 字段和模板规则说明。 |
| `README_IMPORT.md` | 将本规范复制到业务项目的说明。 |
| `scripts/validate-test-design.ps1` | 模板稳定性自检入口。 |

## 正式测试设计 Sheet

`codebuddy-test-design-template.xlsx` 默认包含：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计工作簿不新增 `测试系统导入用例` Sheet。

## 测试系统导入

需要导入测试系统时：

1. 复制 `docs/test-design/测试用例模板.xlsx`。
2. 将 `功能测试用例` 中需要导入的内容映射填入副本。
3. 保留原模板中的字段顺序、下拉框、必填样式、标红字段和自动生成字段空值。
4. 不修改原始 `测试用例模板.xlsx`。

## 使用方式

在目标项目中引用本规范后，可以直接对 CodeBuddy/Codex 说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果需要导入测试系统，请复制 docs/test-design/测试用例模板.xlsx 生成独立导入文件，不要修改原模板。
```

更完整的执行规则见：

- `AGENTS.md`
- `CODEBUDDY.md`
- `docs/ARCHITECTURE.md`
- `.codebuddy/skills/test-design/SKILL.md`
- `docs/test-design/excel-template-spec.md`

## 稳定性自检

每次调整规范或 Excel 模板后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

该脚本会检查：

- 正式测试设计模板只包含 8 个标准 Sheet
- 正式模板不包含 `测试系统导入用例` Sheet
- 测试系统导入模板字段顺序正确
- `测试类型`、`测试用例级别`、`执行方式` 的 Excel 下拉框仍保留
- 无已落地自动化资产时，导入文件中的 `执行方式` 默认填写 `手动`

## 维护原则

- 规则变化时同步更新 `AGENTS.md`、`CODEBUDDY.md`、Skill 和 Rule。
- 模板字段变化时同步更新 `docs/test-design/excel-template-spec.md`。
- 修改完成后运行稳定性自检。
- 按项目约定，修改完成并验证通过后提交并推送到 `origin`。
