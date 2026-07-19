---
name: test-review-delivery
description: 对facts、case-plan与function-cases执行一次语义Review，并从同一用例源生成正式测试设计和测试系统导入两个Excel。用于最终跨产物核对、局部修正和交付。
allowed-tools: Read, Write, Bash, Grep, Glob
---

# Review 与交付

读取 `docs/test-design/rules/excel-deliverable.md`、`import-template.md`、
`case-design.md` 和 `artifact-contract.md`。

1. 对 facts、plan、cases 的紧凑投影只做一次语义审计，覆盖 cases、performance、
   risks、automation、elements、cross_sheet，并提交当前完整 Case ID 顺序。
2. Review 只核对跨产物映射与实际业务语义，不重新运行 discovery/plan/cases 门禁，
   不新增测试义务。若发现问题，明确到一个功能或一个 Case，局部修正一次后重做本次
   Review；禁止全流程回退和自动循环。
3. Review 为 `ready` 或 `ready_with_notes` 后调用 `deliver`。组装器从同一
   `function-cases.json` 独立生成 `deliverables/正式测试设计.xlsx` 和
   `deliverables/测试系统导入.xlsx`，模型不得直接编辑 Excel。
4. 确认正式文件保留既有 8 个 Sheet、数据行连续、无空标题/测试点/中间空行/内部ID，
   性能、风险、自动化和元素覆盖均来自上游事实与计划；两个 Excel 的用例名称、数量、
   步骤和预期一致。
5. 返回 `delivery_dir`、`formal_workbook`、`import_workbook` 的绝对路径。已存在双 Excel
   且 Review 指纹仍有效时直接完成，不重复生成。

任何结构问题都应在上游对应功能局部修复；不得用手工改表掩盖来源问题。

