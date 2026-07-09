# test_case_gen

项目级测试设计规范包，用于让 CodeBuddy/Codex 按统一规则生成测试设计 Excel、功能测试用例、性能测试设计、页面元素覆盖清单、风险与待确认问题和自动化建议。

本仓库不是业务应用代码。它提供可复制到业务项目根目录的 Memory、Skill、Rule 和 Excel 模板。

## 适用场景

- 用户故事、需求文档、接口文档、PR Diff、缺陷单的测试设计
- 页面截图、原型图、可访问页面的可交互元素覆盖
- 已有测试用例补充、优化和回归测试设计
- 需要生成测试系统导入文件的测试用例交付

## 关键文件

| 文件 | 说明 |
| --- | --- |
| `AGENTS.md` | Codex 项目级执行说明。 |
| `CODEBUDDY.md` | CodeBuddy 项目级 Memory。 |
| `docs/ARCHITECTURE.md` | AI 规则分层、模板契约和维护边界。 |
| `docs/RULE_OWNERSHIP.md` | 规则归属矩阵，定义权威源、摘要引用和不应承载完整规则的位置。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计 Skill。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 规则。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 规则。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计模板，包含 8 个标准 Sheet。 |
| `docs/test-design/测试用例模板.xlsx` | 测试系统导入模板。需要导入时复制该模板生成独立导入文件，不修改原模板。 |
| `docs/test-design/excel-template-spec.md` | Excel 字段和模板规则说明。 |
| `docs/test-design/rules/` | 按任务类型拆分的详细规则，避免 Skill/Rule 入口超过 1 万字。 |
| `docs/test-design/rules/dfx-test-strategy.md` | DFX 12 维度 × 4 场景测试策略矩阵，用于把异常、边界、性能、安全、可靠性等要求落到具体用例。 |
| `docs/test-design/archive-and-index-guidelines.md` | 测试资产归档、模块能力索引和跨模块依赖维护规范。 |
| `docs/test-assets/product-map.xlsx` | 内部产品测试知识图谱主入口，不作为默认客户交付件。 |
| `docs/test-assets/` | 内部产品级测试资产库，保存模块归档、导入副本和产品版图。 |
| `docs/test-assets/batch-runs/` | 内部批次运行状态目录，保存大范围任务的计划、状态和复盘。 |
| `README_IMPORT.md` | 将本规范复制到业务项目的说明。 |
| `scripts/validate-test-design.ps1` | 模板稳定性自检入口。 |
| `scripts/validate-test-design-deliverable.ps1` | 已生成测试设计 Excel 的交付件质量校验入口。 |
| `scripts/test_design_excel_tools.py` | 统一 Excel 工具，用于从正式测试设计按表头生成测试系统导入文件，并修复多行字段样式。 |
| `scripts/validate-generated-python-scripts.ps1` | 当前批次 Python/JSON/文本中间文件预检入口，执行前检查单文件大小、JSON/Python 语法和中文弯引号/智能引号风险。 |

## 正式测试设计 Sheet

`codebuddy-test-design-template.xlsx` 默认包含：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计工作簿不新增 `测试系统导入用例` Sheet。

## 测试系统导入

需要导入测试系统时：

1. 复制 `docs/test-design/测试用例模板.xlsx`。
2. 将 `功能测试用例` 中需要导入的内容映射填入副本。
3. 保留原模板中的字段顺序、下拉框、必填样式、标红字段和自动生成字段空值。
4. 不修改原始 `测试用例模板.xlsx`。

推荐使用统一工具一站式收口，避免多轮脚本校验和修改：

```powershell
python scripts/test_design_excel_tools.py complete-deliverables `
  --project-root . `
  --formal-workbook docs/test-design/current/<测试设计.xlsx> `
  --import-template docs/test-design/测试用例模板.xlsx `
  --module-path "一级模块>二级菜单>三级菜单" `
  --batch-status docs/test-assets/batch-runs/<任务>/batch-status.csv `
  --page-discovery docs/test-assets/batch-runs/<任务>/page-discovery.csv `
  --product-map docs/test-assets/product-map.xlsx `
  --scripts-path docs/test-assets/batch-runs/<任务>/artifacts/scripts
```

交付文件名只使用菜单/模块路径，例如 `一级模块_二级菜单_三级菜单_测试设计.xlsx` 和 `一级模块_二级菜单_三级菜单_导入用例.xlsx`；不要把运行文件夹名、批次目录名或产品名拼入文件名。

只需要单独生成导入文件时，仍可使用 `python scripts/test_design_excel_tools.py generate-import ...`。

## 客户交付与内部资产

- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`
- 内部产品测试资产库放在 `docs/test-assets/`
- 产品测试知识图谱主入口为 `docs/test-assets/product-map.xlsx`
- 正式测试设计最终版归档到 `docs/test-assets/modules/`
- 测试系统导入文件副本归档到 `docs/test-assets/imports/`
- 大范围任务的批次计划、状态和复盘归档到 `docs/test-assets/batch-runs/`
- 每次生成前读取产品版图和依赖模块归档；正式写测试用例前展示产品理解摘要、风险项和待确认问题并让用户确认；每次生成后回存最终版并更新产品版图
- 不依赖 AI 对话记忆保存具体业务事实

## 规则入口

- 硬性测试质量规则以 `.codebuddy/.rules/test-design-rule.mdc` 和 `.codebuddy/rules/test-design-rule.md` 为准。
- 执行流程以 `.codebuddy/skills/test-design/SKILL.md` 为准。
- 详细规则按任务类型读取 `docs/test-design/rules/`，入口文件保持低于 10000 字符，避免 CodeBuddy 加载 Skill 时截断或遗漏。
- 异常、边界、权限、状态、性能、安全、可靠性等测试策略以 `docs/test-design/rules/dfx-test-strategy.md` 为细化口径；模块测试例生成前先做 DFX 覆盖评估，DFX 不替代原测试维度，而是把原要求落到测试数据、操作步骤、预期结果和恢复路径。
- Excel 字段、下拉框和导入模板以 `docs/test-design/excel-template-spec.md` 为准。
- 产品版图、归档和跨模块依赖以 `docs/test-design/archive-and-index-guidelines.md` 为准。
- 规则归属和精简边界以 `docs/RULE_OWNERSHIP.md` 为准。

## 使用方式

在目标项目中引用本规范后，可以直接对 CodeBuddy/Codex 说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果需要导入测试系统，请复制 docs/test-design/测试用例模板.xlsx 生成独立导入文件，不要修改原模板。
```

更完整的执行规则见：

- `AGENTS.md`
- `CODEBUDDY.md`
- `docs/ARCHITECTURE.md`
- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/rules/test-design-rule.md`
- `docs/test-design/excel-template-spec.md`
- `docs/RULE_OWNERSHIP.md`

## 稳定性自检

每次调整规范或 Excel 模板后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

该脚本会检查：

- 正式测试设计模板只包含 8 个标准 Sheet
- 正式模板不包含 `测试系统导入用例` Sheet
- 测试系统导入模板字段顺序正确
- `测试类型`、`测试用例级别`、`执行方式` 的 Excel 下拉框仍保留
- 无已落地自动化资产时，导入文件中的 `执行方式` 默认填写 `手动`
- `用例标题`/`测试用例名称` 使用 `功能点-当前用例标题` 格式，避免导入系统后丢失功能点信息
- 产品版图文件存在且包含标准 Sheet
- 批次运行状态目录和模板存在
- 规则归属矩阵存在，Rule 双入口保持一致，入口文档引用权威源而不是复制完整规则
- DFX 测试策略矩阵存在，并被基础用例设计规则引用

生成正式测试设计 Excel 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务可追加 `-BatchStatusPath <batch-status.csv>`，用于校验批次状态中的覆盖数量、用例数量和质量门禁。

如果 `batch-status.csv` 同级存在 `page-discovery.csv`，脚本会自动使用 `docs/test-assets/product-map.xlsx` 启用产品版图同步校验；也可以显式追加 `-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>`，用于校验页面实探、正式 Excel 和产品版图之间的页面元素、关联用例、用例资产索引和变更记录是否同步。

页面实探或批次任务开始前，先运行 `python scripts/test_design_excel_tools.py init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001`，确保 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 同步创建。

交付件校验会拦截 `batch-status.csv`/`page-discovery.csv` 自定义精简表头、CSV 字段错位、`batch-plan.md` 状态与页面数不一致、`product-map.xlsx` 未沉淀真实资产或仍保留 `示例产品`/`示例模块`/`示例页面`、用例资产索引和页面元素地图未覆盖正式 Excel、以及疑似真实密钥/Token/密码未替换为 `<valid_api_key>`、`<test_token>`、`<test_service_url>` 等占位符的问题。

如果当前批次生成了 Python 临时脚本或 JSON/CSV/Markdown/TXT 中间分片，执行前先预检。单个 Python 建议小于 200KB，单个 JSON/CSV/Markdown/TXT 建议小于 256KB；超过时继续按最小标题路径、页面域或功能块分片：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path docs/test-assets/batch-runs/<任务>/artifacts/scripts
```

## 维护原则

- 规则变化时先查 `docs/RULE_OWNERSHIP.md`，更新权威源，再同步必要摘要引用。
- 模板字段变化时同步更新 `docs/test-design/excel-template-spec.md`。
- 修改完成后运行稳定性自检。
- 按项目约定，修改完成并验证通过后提交并推送到 `origin`。

## 外网到内网升级

- 普通框架升级使用 `scripts/new-framework-upgrade-package.ps1` 生成升级包。
- 内网使用 `scripts/upgrade-framework.ps1 -PackagePath <升级包>` 应用升级包。
- 升级包默认保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`，不得覆盖内网真实资产。标识：PROTECTED_ASSET_DIRS。
- `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。
- 只有 `asset_schema_version` 变化或 `product-map.xlsx` 结构变化时，才需要资产迁移；迁移必须增量补齐，不得用空模板覆盖真实资产。

详细流程见 `docs/UPGRADE.md`。
