---
name: test-design
description: 路由页面深探、事实驱动测试规划、测试用例编写、一次性Review与双Excel交付。适用于完整测试设计任务；支持4个手动串行Agent，也支持Agent不可用时在同一会话按相同阶段Skill继续执行。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# 测试设计路由

直接理解用户给出的测试范围，不向用户展示初始化阶段。绑定或恢复
`docs/test-design/current/<run-id>/` 后，选择一种执行方式且全程复用同一 run-dir：

1. CodeBuddy 可手动调用 Agent 时，提示用户依次手动选择 `test-page-explorer`、
   `test-design-planner`、`test-case-author`、`test-review-delivery`。禁止并行，
   路由 Skill 自身不得派发 Agent；禁止 Agent 递归和自动返工。
2. Agent 不可用或调用失败时，不中断流程；在当前会话依次执行同名阶段 Skill。
   降级只改变执行者，不改变输入、输出、CLI、校验和质量标准。

每一阶段只读取上一阶段固化产物，不依赖会话记忆传递事实：

```text
页面 → events.jsonl/facts.json → case-plan.json
     → function-cases.json → review.json → 两个 Excel
```

阶段完成后读取一次 `status` 决定下一阶段。若当前阶段有明确局部错误，只修当前
功能或当前 Case 一次；不得回到全流程、生成返工队列或循环重试。工具瞬时错误最多
原地重试一次，仍失败则记录真实阻塞并继续不受影响的功能。

各阶段必须通过 `scripts/run-test-design.ps1` 调用标准 CLI。JSON 负载放在系统临时
目录并使用 `test-design-` 前缀；若命令执行能力不可用，只降级一次到
`test_design_cli.execute_request`，不得直接写 run-dir JSON、不得调用底层运行时函数、
不得生成临时 Python 编排脚本。

阶段规则：

- 页面深探：读取 `test-page-exploration` Skill。
- 计划与 DFX：读取 `test-design-planning` Skill。
- 用例正文：读取 `test-case-authoring` Skill。
- Review 与交付：读取 `test-review-delivery` Skill。

完整规则按 `docs/test-design/rules/README.md` 只加载当前阶段需要的专题文件。
