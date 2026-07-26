# CodeBuddy 测试设计规范导入说明

这个项目是测试设计规范包。把规范包内容复制到业务项目根目录后，CodeBuddy/Codex 会通过轻入口读取规则，并按任务类型加载详细规则模块。

## 复制后的关键结构

```text
your-project/
  AGENTS.md
  CODEBUDDY.md
  .codebuddy/
    skills/test-design/SKILL.md
    .rules/test-design-rule.mdc
    rules/test-design-rule.md
  docs/
    ARCHITECTURE.md
    RULE_OWNERSHIP.md
    test-design/
      codebuddy-test-design-template.xlsx
      测试用例模板.xlsx
      excel-template-spec.md
      rules/
      deliverables/
  scripts/
```

## 规则加载方式

- 主 Skill：`.codebuddy/skills/test-design/SKILL.md`
- 主 Rule：`.codebuddy/.rules/test-design-rule.mdc`
- 兼容 Rule 镜像：`.codebuddy/rules/test-design-rule.md`
- 项目 Memory：`CODEBUDDY.md`
- 规则归属：`docs/RULE_OWNERSHIP.md`
- 架构说明：`docs/ARCHITECTURE.md`

详细规则按任务类型读取 `docs/test-design/rules/`：

- 基础用例设计：`case-design.md`
- 页面实探：`page-discovery.md`
- 大范围分批：`batch-run.md`
- Excel 交付件：`excel-deliverable.md`
- 所有任务都读取测试系统导入规则：`import-template.md`
- 页面实探数据操作边界：`data-safety.md`

这种结构让 Skill、Rule、AGENTS、CODEBUDDY 保持低于 10000 字符，避免 CodeBuddy 加载入口时出现截断或规则遗漏。

## 推荐提示词

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果涉及页面、截图、原型或可访问系统，请按 docs/test-design/rules/page-discovery.md 做页面实探。
如果范围超过一个最小标题，请按 docs/test-design/rules/batch-run.md 分批执行。
每次正式交付都必须复制 docs/test-design/测试用例模板.xlsx 生成独立测试系统导入文件，不要修改原模板，也不需要询问是否生成。
生成后请优先运行 scripts/test_design_excel_tools.py complete-deliverables 一站式生成导入文件、同步交付件并校验。
```

## 测试系统导入

正式测试设计 Excel 不新增 `测试系统导入用例` Sheet。每次正式交付都同步复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，并保留模板下拉框、必填样式、标红字段和自动生成字段空值。

推荐随批次交付使用统一收口工具：

```powershell
python scripts/test_design_excel_tools.py complete-deliverables `
  --project-root . `
  --formal-workbook docs/test-design/deliverables/<测试设计.xlsx> `
  --import-template docs/test-design/测试用例模板.xlsx `
  --module-path "一级模块>二级菜单>三级菜单"
```

`generate-import` 仅用于维护或重新转换已有正式测试设计，不作为完整交付流程。

导入文件生成后，用 `-ImportWorkbookPath <导入文件.xlsx>` 追加校验。

## 自检命令

项目结构和模板自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```
