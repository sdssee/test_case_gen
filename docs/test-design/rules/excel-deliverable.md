# Excel 交付规则

正式测试设计恰好包含以下 8 个 Sheet：测试设计总览、需求用户故事拆解、测试场景矩阵、功能测试用例、性能测试设计、风险与待确认问题、自动化建议、页面元素覆盖清单。

测试系统导入文件必须从独立模板副本生成，不得作为正式工作簿的第 9 个 Sheet。导入行与正式功能用例逐条对应。

组装器先清除模板样例行，再从第 2 行连续写入；禁止中间空行、空名称、残留模板占位、Excel Table 部件和错位字段。步骤、预期、前置条件启用自动换行。交付后运行：

```powershell
scripts/validate-test-design-deliverable.ps1 -WorkbookPath <正式.xlsx> -ImportWorkbookPath <导入.xlsx>
```
