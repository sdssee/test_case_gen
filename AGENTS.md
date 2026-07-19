# Codex Project Instructions

本仓库用于“页面深探 → 事实编译 → DFX 计划 → 测试用例 → 双 Excel 交付”。支持 4 个手动串行 Agent；Agent 不可用时必须在一个会话中按相同阶段 Skill 降级执行。

## 读取路由

1. 读取 `.codebuddy/skills/test-design/SKILL.md` 和 `.codebuddy/rules/test-design-rule.md`。
2. 若用户手动选择阶段 Agent，读取该 Agent 指向的唯一阶段 Skill。
3. 按 `docs/test-design/rules/README.md` 读取当前阶段专题规则。
4. 产品历史事实按需读取 `docs/test-assets/catalog/`；本轮事实以 run-dir 的 `facts.json` 为准。

## 架构边界

- 直接调用 Skill，不向用户暴露初始化阶段。
- 只允许 `.codebuddy/agents/` 中 4 个阶段 Agent 由用户手动串行调用；禁止自动编排、并行、递归派生、页面 Hook、逐元素义务队列、观察 CSV、用例分片和自动返工循环。
- Agent 调用失败属于正常降级条件：在当前会话调用对应阶段 Skill，复用同一 run-dir、CLI、JSON 契约和校验，不跳过阶段、不降低质量。
- 证据仅为可选诊断，不参与完成判断、计划、用例、Review 和 Excel。
- 输入正常/必填空值/明确格式边界及有限选项由元素登记时的 DFX 实探清单前置声明；每个有限选项和每个实测有效输入等价类分别形成独立 baseline Case，不合并且不默认组合。已完成事实可持续写入，checkpoint 只汇总尚未执行的既定分支。参与控件、触发动作和CRUD闭环仍在事务写入现场校验。
- Review 是一次性语义审计；只做明确的局部修正，不作为逐阶段拒绝器。
- 正式工作簿与测试系统导入文件必须从同一 `function-cases.json` 独立生成。
- 内部只使用标准CLI和JSON负载，不生成临时Python编排脚本；新run-dir固定为 `docs/test-design/current/<run-id>/`，已存在历史运行可原地恢复。
- `case-plan.json` 与 `function-cases.json` 在单文件内按功能幂等upsert；精确重复提交直接吸收，不新增分片、模糊去重或自动重试状态机。

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
