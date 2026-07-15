# CodeBuddy 测试设计入口

本项目使用单会话连续执行。先读取 `.codebuddy/skills/test-design/SKILL.md` 与 `.codebuddy/rules/test-design-rule.md`，再根据 `docs/test-design/rules/README.md` 加载当前阶段规则。

运行链路只有五个核心产物：

```text
events.jsonl → facts.json → case-plan.json → function-cases.json → review.json / deliverables
```

不得重新引入逐点击任务队列、页面操作 Hook、多份发现 CSV、用例分片 manifest 或自动返工循环。阶段命令统一通过 `scripts/run-test-design.ps1` 执行。
