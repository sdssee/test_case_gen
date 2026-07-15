# 测试系统导入文件

正式测试设计与测试系统导入文件是两个独立工作簿。`complete-deliverables` 从已经审查通过的 `function-cases.json` 先生成正式 8 Sheet，再从正式“功能测试用例”Sheet 映射到 `docs/test-design/测试用例模板.xlsx` 的副本。

```powershell
scripts/run-test-design.ps1 complete-deliverables --run-dir <run-dir> --project-root .
```

输出目录为 `<run-dir>/deliverables/`。交付检查会验证：用例数量对应、名称非空、步骤与预期非空、无中间空白行、无 Excel Table 修复风险、正式工作簿恰好包含 8 个标准 Sheet。
