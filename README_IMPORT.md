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
  deliverables/
  docs/
    ARCHITECTURE.md
    RULE_OWNERSHIP.md
    test-design/
      codebuddy-test-design-template.xlsx
      测试用例模板.xlsx
      excel-template-spec.md
      rules/
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
- 原子场景核账后的 DFX 阶段：`dfx-test-strategy.md`
- Excel 写入与交付阶段：`excel-deliverable.md`、`import-template.md` 和 `docs/test-design/excel-template-spec.md`
- 页面实探数据操作边界：`data-safety.md`

这种结构让 Skill、Rule、AGENTS、CODEBUDDY 保持低于 10000 字符，避免 CodeBuddy 加载入口时出现截断或规则遗漏。

## 推荐提示词

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果涉及页面、截图、原型或可访问系统，请按 docs/test-design/rules/page-discovery.md 做页面实探。
如果范围超过一个最小标题，请按 docs/test-design/rules/batch-run.md 分批执行。
每次正式交付都必须复制 docs/test-design/测试用例模板.xlsx 生成独立测试系统导入文件，不要修改原模板，也不需要询问是否生成。
页面任务先用 validate-discovery 完成深探准出，生成后运行 scripts/test_design_excel_tools.py complete-deliverables 并传入同一 discovery-state.json，一站式生成导入文件、同步交付件并校验。
```

## 测试系统导入

正式测试设计 Excel 不新增 `测试系统导入用例` Sheet。每次正式交付都同步复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，并保留模板下拉框、必填样式、标红字段和自动生成字段空值。

推荐随批次交付使用统一收口工具：

```powershell
python scripts/test_design_excel_tools.py complete-deliverables `
  --project-root . `
  --formal-workbook deliverables/<测试设计.xlsx> `
  --import-template docs/test-design/测试用例模板.xlsx `
  --module-path "一级模块>二级菜单>三级菜单"
```

页面任务追加 `--discovery-state <discovery-state.json>`；非页面任务不需要该参数。

`generate-import` 仅用于维护或重新转换已有正式测试设计，不作为完整交付流程。

`complete-deliverables` 已同步完成正式测试设计、导入文件和一次完整校验；成功后不追加重复校验。

## 自检命令

项目结构和模板自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

已有交付件独立审计（正常流程不重复运行）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx> [-DiscoveryStatePath <discovery-state.json>]
```
