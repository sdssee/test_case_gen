# Codex Project Instructions

本仓库用于“页面深探 → 事实编译 → DFX 计划 → 测试用例 → 双 Excel 交付”。整个测试设计在一个会话和一个浏览器上下文中完成。

## 读取路由

1. 读取 `.codebuddy/skills/test-design/SKILL.md` 和 `.codebuddy/rules/test-design-rule.md`。
2. 按 `docs/test-design/rules/README.md` 读取当前阶段专题规则。
3. 产品历史事实按需读取 `docs/test-assets/catalog/`；本轮事实以 run-dir 的 `facts.json` 为准。

## 架构边界

- 直接调用 Skill，不向用户暴露初始化阶段。
- 禁止多 Agent、页面 Hook、逐元素义务队列、观察 CSV、用例分片和自动返工循环。
- 证据仅为可选诊断，不参与完成判断、计划、用例、Review 和 Excel。
- Review 是一次性语义审计；只做明确的局部修正，不作为逐阶段拒绝器。
- 正式工作簿与测试系统导入文件必须从同一 `function-cases.json` 独立生成。

## 内部辅助命令

用户正常流程不需要手工运行命令。排查或恢复时可以使用：

```powershell
scripts/run-test-design.ps1 status --run-dir <run-dir>
scripts/run-test-design.ps1 checkpoint --run-dir <run-dir>
scripts/run-test-design.ps1 review --run-dir <run-dir>
scripts/run-test-design.ps1 deliver --run-dir <run-dir> --project-root .
scripts/validate-test-design.ps1 -Mode Fast
scripts/validate-test-design.ps1 -Mode Full
```

修改后检查 `git status`；验证通过后使用中文提交信息并推送当前分支。
