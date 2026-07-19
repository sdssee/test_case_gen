# CodeBuddy 测试设计入口

直接调用 `.codebuddy/skills/test-design/SKILL.md`。可以在同一任务中手动串行调用 4 个阶段 Agent，也可以在 Agent 不可用时由当前会话按同名阶段 Skill 继续，核心产物始终为：

```text
events.jsonl → facts.json → case-plan.json → function-cases.json → review.json → 两个 Excel
```

允许的阶段 Agent 仅为 `test-page-explorer` → `test-design-planner` → `test-case-author` → `test-review-delivery`。不得自动编排、并行、递归调用，也不得引入页面操作 Hook、义务队列、观察 CSV、用例分片或自动返工循环。详细规则按 `docs/test-design/rules/README.md` 路由读取。
