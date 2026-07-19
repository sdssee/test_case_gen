---
name: test-case-authoring
description: 按facts.json与case-plan.json编写可直接执行且彼此不同的功能测试用例，输出function-cases.json。用于配对步骤与预期、导航注入、CRUD闭环和同功能集中管理。
allowed-tools: Read, Write, Bash, Grep, Glob
---

# 测试用例编写

只读 facts 和 plan，只通过 `write-cases` 写 `function-cases.json`。读取
`docs/test-design/rules/case-design.md`、`data-safety.md` 和
`artifact-contract.md`。

1. 严格按计划功能顺序和 Case ID 编写；同功能用例一次 upsert 并连续排列。
2. 模型只提供具体业务 `action + expected`、前置、受控测试数据和轻量自动化字段。
   运行时注入第一步完整菜单导航、标题、测试点、主验证目标和内部事实映射。
3. 每个步骤与预期一一配对，动作保留该场景的具体输入、选项和触发动作，预期只使用
   同一实探检查的稳定结果。不同 Case 的核心动作和核心预期必须体现不同主验证分支，
   不能只改标题或数据后复制正文。
4. CRUD、编辑和配置用例写入提交、重开/查询、实际效果及恢复/清理；无效分支写清
   拦截结果和数据未变化。禁止截图、UID、DOM、选择器、内部编号、工具术语及不确定
   预期；环境数据使用已在前置条件声明来源的 `TEST_*` 引用。
5. 写入失败只根据返回问题修当前功能块一次，不绕过 CLI、不整体重写其他功能。

所有计划 Case 均已写入且运行时检查无缺失后，进入 Review 阶段。

