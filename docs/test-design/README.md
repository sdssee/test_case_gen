# 测试设计资源

- `codebuddy-test-design-template.xlsx`：正式 8 Sheet 测试设计模板。
- `测试用例模板.xlsx`：测试系统导入模板。
- `rules/`：按阶段读取的专题规则。
- `excel-template-spec.md`：工作簿契约。
- `archive-and-index-guidelines.md`：归档约束。
- `current/<run-id>/`：标准运行目录；每轮两个 Excel 位于其 `deliverables/` 子目录。
- `deliverables/`：仅作为受保护的历史用户目录，不再作为新运行的输出位置。

执行入口为根目录 Skill、Rule 和 `scripts/run-test-design.ps1`。
