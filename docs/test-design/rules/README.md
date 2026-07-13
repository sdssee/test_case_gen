# 测试设计规则模块索引

本目录保存 CodeBuddy/Codex 执行测试设计时按需读取的详细规则。入口文件保持轻量，具体规则以本目录和模板规范为准。

## 读取路由

- 所有任务：读取 `case-design.md`、`excel-deliverable.md`、`data-safety.md`、`dfx-test-strategy.md`。
- 最终架构运行：读取 `docs/AGENT_ORCHESTRATION.md` 了解角色、契约、状态机和 CLI；质量判定仍以本目录专题规则及现有验证器为唯一依据。
- 涉及页面、截图、原型、浏览器或 computer use：追加读取 `page-discovery.md`，并按其中规则维护逐选项与 `interaction-branch-observations.csv` 交互分支事实。
- 范围超过一个最小标题、全产品、大模块或多个菜单：追加读取 `batch-run.md`。
- 需要导入测试系统：追加读取 `import-template.md`。
- 涉及历史用例、跨模块依赖、补充用例或产品资产归档：追加读取 `product-map-sync.md`。

## 入口瘦身约束

- `.codebuddy/skills/test-design/SKILL.md`、`.codebuddy/.rules/test-design-rule.mdc`、`.codebuddy/rules/test-design-rule.md`、`CODEBUDDY.md`、`AGENTS.md` 都是轻入口，目标是低于 10000 字符。
- 入口文件只保留任务路由、不可违反的硬规则和必须运行的校验命令。
- 不在入口文件复制本目录的完整规则正文，避免 CodeBuddy 加载超过 1 万字后出现截断、遗漏或执行不稳定。
