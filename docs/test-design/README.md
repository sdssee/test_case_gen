# 测试设计模板

本目录用于保存项目测试设计输出模板和字段说明。

- `codebuddy-test-design-template.xlsx`：正式测试设计 Excel 模板。
- `测试用例模板.xlsx`：测试系统导出的导入模板参考文件。
- `excel-template-spec.md`：模板字段说明。
- `test-system-field-reference.md`：测试系统字段解释、必填/自动生成/下拉字段说明，已替代原 1.jpg、2.jpg、3.jpg 截图。
- `rules/dfx-test-strategy.md`：DFX 12 维度 × 4 场景测试策略矩阵，用于规范异常、边界、性能、安全、可靠性等用例设计。
- `archive-and-index-guidelines.md`：测试资产归档、模块能力索引和跨模块依赖维护规范。
- `current/`：当前任务客户交付件目录。
- `deliverables/`：已交付给客户或测试系统的文件副本目录。
- `../test-assets/product-map.xlsx`：内部产品测试知识图谱主入口，不作为默认客户交付件。

使用 CodeBuddy 生成测试设计时，优先引用 `codebuddy-test-design-template.xlsx`。

本目录 README 只说明目录用途，不承载完整规则。完整规则请读取：

- 硬性测试质量规则：`../../.codebuddy/rules/test-design-rule.md`
- 执行流程：`../../.codebuddy/skills/test-design/SKILL.md`
- 最终多 Agent 编排、契约与 Review：`../AGENT_ORCHESTRATION.md`
- Excel 字段、下拉框、导入模板：`excel-template-spec.md`
- 产品版图、资产归档、跨模块依赖：`archive-and-index-guidelines.md`
- 规则归属矩阵：`../RULE_OWNERSHIP.md`
