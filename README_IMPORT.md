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
    AGENT_ORCHESTRATION.md
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
- 最终多 Agent 运行架构：`docs/AGENT_ORCHESTRATION.md`

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
批次先用 agent-run 获取最终多 Agent 架构任务，按 AgentTask 隔离执行并用 agent-submit 提交；独立 Review Gate 通过后，再运行 scripts/test_design_excel_tools.py complete-deliverables 一站式生成导入文件、同步交付件并校验。
```

## 测试系统导入

正式测试设计 Excel 不新增 `测试系统导入用例` Sheet。需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，并保留模板下拉框、必填样式、标红字段和自动生成字段空值。

推荐随批次交付使用统一收口工具：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 complete-deliverables `
  --project-root . `
  --run-dir docs/test-assets/batch-runs/<任务_BATCH-001> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品/系统名称" `
  --batch-id BATCH-001
```

只需要单独生成导入文件时，可使用 `generate-import` 兼容命令。

导入文件生成后，用 `-ImportWorkbookPath <导入文件.xlsx>` 追加校验。

## 批次与资产

- 客户交付件：`docs/test-design/current/`、`docs/test-design/deliverables/`
- 内部产品版图：`docs/test-assets/product-map.xlsx`
- 模块归档：`docs/test-assets/modules/`
- 导入副本：`docs/test-assets/imports/`
- 批次账本：`docs/test-assets/batch-runs/`

大范围任务必须建立 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/`，并维护 `batch-scope.json`、`batch-plan.md`、`batch-status.csv`、`batch-review.md`、独立采集的 `page-element-inventory.csv`、带稳定交互实例 ID 的 `page-discovery.csv`、`selection-option-observations.csv`、结构化计划、生命周期和 `artifacts/`。页面证据必须是当前批次 `artifacts/` 内非空文件。
页面实探或批次任务开始前，先运行 `powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "一级模块>二级菜单>三级菜单" --product-name "<产品/系统名称>" --batch-id BATCH-001` 初始化标准批次账本和 `batch-scope.json`；已存在批次使用 `--resume` 并传入原产品名，不得重复初始化覆盖；传入 `--page-discovery` 收口时必须同时传入 `--batch-status`。

初始化会直接启用必选的最终多 Agent 架构。调度器用 `agent-run --run-dir <run-dir> --json` 取得任务，Agent 只写 task packet 指定的 workspace，再用 `agent-submit --task-id <task-id> --result <agent-result.json>` 提交；`agent-status` 可只读查看状态，真实外部阻塞解除后使用 `agent-resume`。完整角色、契约、返工和 Review 规则见 `docs/AGENT_ORCHESTRATION.md`。

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
