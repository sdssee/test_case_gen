# CodeBuddy 测试设计入口

直接调用 `.codebuddy/skills/test-design/SKILL.md`。任务在一个连续会话中执行，核心产物为：

```text
events.jsonl → facts.json → case-plan.json → function-cases.json → review.json → 两个 Excel
```

不得引入多 Agent、页面操作 Hook、义务队列、观察 CSV、用例分片或自动返工循环。详细规则按 `docs/test-design/rules/README.md` 路由读取。
