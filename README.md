# test_case_gen

测试设计规范包，用于让 CodeBuddy/Codex 按统一规则生成测试设计 Excel 和测试系统导入文件。

本仓库不是业务应用代码。它提供可复制到业务项目根目录的 Memory、Skill、Rule、脚本和 Excel 模板。

## 核心入口

| 文件 | 作用 |
| --- | --- |
| `AGENTS.md` | Codex 项目级执行入口。 |
| `CODEBUDDY.md` | CodeBuddy 项目级 Memory。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计执行流程。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 硬规则。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 硬规则。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计模板，固定 8 个 Sheet。 |
| `docs/test-design/测试用例模板.xlsx` | 测试系统导入模板，使用时复制副本，不修改原模板。 |
| `docs/RULE_OWNERSHIP.md` | 规则归属矩阵，避免重复和漂移。 |

详细规则按任务读取 `docs/test-design/rules/`；Excel 字段以 `docs/test-design/excel-template-spec.md` 为准。

## 标准交付

正式测试设计只包含 8 个标准 Sheet：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计不新增 `测试系统导入用例` Sheet。需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件。

## 主流程

生成正式测试设计后，使用一站式收口命令：

```powershell
python scripts/test_design_excel_tools.py complete-deliverables `
  --project-root . `
  --formal-workbook docs/test-design/deliverables/<测试设计.xlsx> `
  --import-template docs/test-design/测试用例模板.xlsx `
  --module-path "一级模块>二级菜单>三级菜单"
```

只需要单独生成导入文件且不做批次收口时，可使用 `generate-import` 兼容命令。

交付文件名只使用菜单/模块路径，例如 `一级模块_二级菜单_三级菜单_测试设计.xlsx` 和 `一级模块_二级菜单_三级菜单_导入用例.xlsx`，不拼运行文件夹名、批次目录名或产品名。

## 关键规则

- 测试策略以 `DFX维度` 和 `DFX场景` 为主字段，`场景类型`、`正向/反向` 已废弃；详细矩阵见 `docs/test-design/rules/dfx-test-strategy.md`。
- 每条功能测试用例必须把 DFX 落到测试数据、操作步骤、预期结果和恢复路径。
- 已有数据只能查看和只读深探；敏感操作只允许作用于本次创建且带测试标识的数据。
- 所有交付件统一放在 `docs/test-design/deliverables/`。

## 校验

项目稳定性自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```


## 维护

- 规则变化先查 `docs/RULE_OWNERSHIP.md`。
- 模板字段变化同步 `docs/test-design/excel-template-spec.md` 和校验脚本。
- 修改完成后运行稳定性自检。
- 每次修改验证通过后提交并推送，Commit Message 使用中文。
