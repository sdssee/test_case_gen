# 测试设计模板

本目录用于保存项目测试设计输出模板和字段说明。

- `codebuddy-test-design-template.xlsx`：正式测试设计 Excel 模板。
- `测试用例模板.xlsx`：测试系统导出的导入模板参考文件。
- `excel-template-spec.md`：模板字段说明。
- `test-system-field-reference.md`：测试系统字段解释、必填/自动生成/下拉字段说明，已替代原 1.jpg、2.jpg、3.jpg 截图。
- `rules/dfx-test-strategy.md`：DFX 12 维度 × 4 场景测试策略矩阵，用于规范异常、边界、性能、安全、可靠性等用例设计。
- `deliverables/`：正式测试设计和测试系统导入文件的统一交付目录。

使用 CodeBuddy 生成测试设计时，必须以 `codebuddy-test-design-template.xlsx` 为唯一结构与样式基线，并通过统一工具生成正式交付件。

本目录 README 只说明目录用途，不承载完整规则。完整规则请读取：

- 硬性测试质量规则：`../../.codebuddy/rules/test-design-rule.md`
- 执行流程：`../../.codebuddy/skills/test-design/SKILL.md`
- Excel 字段、下拉框、导入模板：`excel-template-spec.md`
- 规则归属矩阵：`../RULE_OWNERSHIP.md`
