# CodeBuddy 测试设计规范导入说明

这个仓库是测试设计规范包。把仓库内容复制到业务项目根目录后，CodeBuddy/Codex 会通过轻入口读取规则，并按任务类型加载详细规则模块。

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
      archive-and-index-guidelines.md
      rules/
      current/
      deliverables/
    test-assets/
      product-map.xlsx
      modules/
      imports/
      batch-runs/
      indexes/
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
- 测试系统导入：`import-template.md`
- 产品版图同步：`product-map-sync.md`
- 数据安全与脱敏：`data-safety.md`

这种结构让 Skill、Rule、AGENTS、CODEBUDDY 保持低于 10000 字符，避免 CodeBuddy 加载入口时出现截断或规则遗漏。

## 推荐提示词

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果涉及页面、截图、原型或可访问系统，请按 docs/test-design/rules/page-discovery.md 做页面实探。
如果范围超过一个最小标题，请按 docs/test-design/rules/batch-run.md 分批执行。
如果需要导入测试系统，请复制 docs/test-design/测试用例模板.xlsx 生成独立导入文件，不要修改原模板。
生成后请运行 scripts/validate-test-design-deliverable.ps1 校验交付件。
```

## 测试系统导入

正式测试设计 Excel 不新增 `测试系统导入用例` Sheet。需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，并保留模板下拉框、必填样式、标红字段和自动生成字段空值。

推荐使用统一工具：

```powershell
python scripts/test_design_excel_tools.py generate-import `
  --formal-workbook docs/test-design/current/<测试设计.xlsx> `
  --import-template docs/test-design/测试用例模板.xlsx `
  --output docs/test-assets/imports/<导入文件.xlsx> `
  --module-path "一级模块>二级菜单>三级菜单"
```

导入文件生成后，用 `-ImportWorkbookPath <导入文件.xlsx>` 追加校验。

## 批次与资产

- 客户交付件：`docs/test-design/current/`、`docs/test-design/deliverables/`
- 内部产品版图：`docs/test-assets/product-map.xlsx`
- 模块归档：`docs/test-assets/modules/`
- 导入副本：`docs/test-assets/imports/`
- 批次账本：`docs/test-assets/batch-runs/`

大范围任务必须建立 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/`，并维护 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/`。

## 自检命令

项目结构和模板自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

当前批次 Python 临时脚本预检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path docs/test-assets/batch-runs/<任务>/artifacts/scripts
```

## 外网到内网升级

普通框架升级不要整包覆盖业务项目。

- 外网生成升级包：`scripts/new-framework-upgrade-package.ps1`
- 内网应用升级包：`scripts/upgrade-framework.ps1 -PackagePath <升级包>`

受保护目录：

- `docs/test-assets/`
- `docs/test-design/current/`
- `docs/test-design/deliverables/`

标识：PROTECTED_ASSET_DIRS

如果 `asset_schema_version` 或 `product-map.xlsx` 结构变化，必须通过迁移脚本增量补齐旧资产，不能用空模板覆盖内网真实资产。
