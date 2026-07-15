# Codex Project Instructions

本仓库用于“页面深探 → 测试规划 → 测试用例 → Excel 交付”。整个任务在一个会话、一个浏览器上下文中顺序执行。

## 必读入口

1. `.codebuddy/skills/test-design/SKILL.md`
2. `.codebuddy/rules/test-design-rule.md`
3. 按阶段读取 `docs/test-design/rules/README.md` 指向的专题规则。

## 不可违反的约束

- 默认全量深探。扫描从 DOM、可访问性树和实际页面状态开始，但采用开放发现；新出现的控件立即进入当前事务或后续事务。
- 扫描只读，操作顺序执行。一次事务内完成“扫描、操作、局部重扫、观察差异、恢复”。工具瞬时错误最多重试一次；真实页面结果只记录一次；最终只输出一份未决缺口。
- 有限下拉选项逐项真实选择。CRUD 和配置项必须验证提交、持久化回显、实际生效、恢复/清理；配置仅做单因素，不做组合。
- 既有数据只读；变更只作用于本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供的测试数据。
- 页面能验证的问题必须自行操作，仅把页面外部且仍不理解的业务语义交给用户确认。
- DFX 在用例计划阶段展开。每个独立功能先有基线用例，再按适用策略扩展；一个用例归属一个功能，辅助使用其他控件不能替代其独立用例。
- 每条用例引用 `fact_id`；标题为“功能点-当前用例标题”；步骤和预期一一对应、可执行、可判定且互不重复，不得出现截图要求、内部标识或占位文本。
- 正式测试设计保持 8 个标准 Sheet；测试系统导入文件必须独立生成。
- 保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/` 中用户资产。

## 阶段与写入权

1. discovery：只写 `artifacts/discovery/events.jsonl`、`facts.json`、`evidence/`。
2. plan：只读 facts，写 `case-plan.json`。
3. cases：只读 facts 和 plan，写 `function-cases.json`。
4. review/delivery：只读上游产物，写 `review.json` 和 `deliverables/`。

阶段边界只校验一次；发现问题仅修复受影响产物，不启动自动返工循环。

## 命令

```powershell
scripts/run-test-design.ps1 init-run --run-dir <run-dir> --module-path "<模块路径>"
scripts/run-test-design.ps1 record-observation --run-dir <run-dir> --file <event-or-events.json>
scripts/run-test-design.ps1 pipeline-status --run-dir <run-dir>
scripts/run-test-design.ps1 validate-stage --run-dir <run-dir> --stage discovery|plan|cases|review
scripts/run-test-design.ps1 review-run --run-dir <run-dir>
scripts/run-test-design.ps1 complete-deliverables --run-dir <run-dir> --project-root .
scripts/validate-test-design.ps1 -Mode Fast
scripts/validate-test-design.ps1 -Mode Full
```

修改后检查 `git status`；验证通过后使用中文提交信息并推送当前分支。
