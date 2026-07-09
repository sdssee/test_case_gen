---
name: test-design
description: 测试设计与测试用例生成专家。适用于需求、用户故事、接口文档、PR Diff、缺陷单、截图、原型、页面实探、回归与补充测试，输出测试设计 Excel、导入文件、页面元素覆盖清单和产品版图同步。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# CodeBuddy Skill：测试设计轻入口

本 Skill 是执行入口，不承载完整规则正文。详细规则按任务类型读取 `docs/test-design/rules/`，Excel 字段遵守 `docs/test-design/excel-template-spec.md`，资产归档遵守 `docs/test-design/archive-and-index-guidelines.md`。

## 必读路由

每次任务先读取：

1. `.codebuddy/.rules/test-design-rule.mdc`
2. `docs/test-design/rules/README.md`
3. `docs/test-design/rules/case-design.md`
4. `docs/test-design/rules/excel-deliverable.md`
5. `docs/test-design/rules/data-safety.md`
6. `docs/test-design/rules/dfx-test-strategy.md`
7. `docs/test-design/excel-template-spec.md`

按任务追加：

- 页面、截图、原型、浏览器或 computer use：读取 `docs/test-design/rules/page-discovery.md`。
- 全产品、大模块、多个菜单或超过一个最小标题：读取 `docs/test-design/rules/batch-run.md`。
- 测试系统导入：读取 `docs/test-design/rules/import-template.md`。
- 跨模块依赖、历史归档、二次补充、资产回存：读取 `docs/test-design/rules/product-map-sync.md` 和 `docs/test-design/archive-and-index-guidelines.md`。

## 标准工作流

1. 识别任务类型：需求、用户故事、接口、缺陷、PR Diff、截图/原型、可访问页面、既有用例、补充任务或混合输入。
2. 读取产品资产：生成或补充前读取 `docs/test-assets/product-map.xlsx`；涉及依赖模块时读取对应归档测试设计。
3. 粗遍历和摘要：模块或大范围任务先做菜单轮廓、页面清单、核心功能点、业务对象、状态流转、跨模块依赖识别，并向用户展示产品理解摘要或模块理解摘要，包含风险项与待确认问题。
4. 风险确认：正式写测试用例前，先把风险项与待确认问题发给用户确认；根据用户确认、补充、排除或调整，动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
5. DFX 覆盖评估：模块或批次正式写测试用例前，综合产品理解、页面实探、文档、历史资产、测试数据和风险项，评估 DFX 12 维度 × 4 场景的适用、不适用、待确认和需补充证据结论。
6. 分批执行：范围超过一个最小标题时，按最深标题级别建立批次队列，逐个最小标题路径执行，不能一次性生成完整测试用例。
7. 页面深探：有页面、原型或窗口时，使用浏览器或 computer use 深遍历当前批次所有可点击、可输入、可选择、可测试元素，记录到 `page-discovery.csv` 和页面元素覆盖清单。
8. 用例设计：按小功能块连续编排，基于 DFX 覆盖评估结果覆盖功能、性能、异常、边界、接口、安全、可靠、维护、可用、部署、运维、业务和极端场景。
9. Excel 生成：正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
10. 导入文件：需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件副本，优先使用 `scripts/test_design_excel_tools.py generate-import`。
11. 资产同步：客户交付件放 `docs/test-design/current/` 或 `docs/test-design/deliverables/`；最终版回存 `docs/test-assets/modules/`，导入副本回存 `docs/test-assets/imports/`，并同步 `product-map.xlsx`。
12. 校验与交付：生成后运行交付件校验；大范围任务传入批次账本、页面实探、产品版图和导入文件参数。

## 不可违反的门禁

- `操作步骤` 必须从系统或项目入口开始写完整导航路径，不得默认已经在当前模块页面。
- `前置条件`、`操作步骤`、`预期结果` 必须编号换行。
- `用例标题` 和导入文件 `测试用例名称` 必须使用 `功能点-当前用例标题` 格式。
- 页面元素覆盖清单只是覆盖追踪矩阵，不写独立测试步骤或完整预期。
- 页面已有数据只能查看和只读深探，不能保存、提交、删除或改变状态；敏感操作只允许作用于本次创建且带测试标识的数据。
- 选择类控件必须选择代表性选项并记录联动/依赖变化；输入类控件必须实际输入并记录真实提示和结果分支；新增类流程必须实填实走到成功后续页或失败停留态。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 正式写测试用例前，必须先展示风险项与待确认问题并让用户确认；确认结果必须动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
- 模块或批次正式写测试用例前，必须先完成 DFX 覆盖评估，明确适用、不适用、待确认和需补充证据的维度，再进入用例设计。
- 每一批都必须执行完整规则，不得因为分批而减少功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险和页面覆盖。
- 异常值、边界值和测试策略必须按 DFX 12 维度 × 4 场景矩阵落地，不得只写一句笼统策略；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 五件套。
- `batch-status.csv`、`page-discovery.csv` 必须使用标准模板表头，禁止自定义精简表头和字段错位。
- 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
- 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
- 生成中间文件执行前必须运行 `scripts/validate-generated-python-scripts.ps1`，检查单文件大小、JSON 语法、Python 语法和中文弯引号风险。
- 批次交付收口优先使用 `scripts/test_design_excel_tools.py finalize-deliverables`，同步 current、deliverables、modules、imports 和 `batch-status.csv` 路径；传入 `--page-discovery` 时必须同时传入 `--batch-status`。
- 导入文件 `执行方式` 默认 `手动`；只有已有可运行、可维护并覆盖主要校验点的自动化资产且本次明确关联时，才允许 `自动化`。
- 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。
- 正式交付件、导入文件、批次账本、页面实探记录和产品版图不得保留真实环境 URL/IP、真实账号、真实密钥、Token、密码或敏感数据，必须使用 `<product_login_url>` 等占位符。

## 生成后校验

正式测试设计：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务追加：

```powershell
-BatchStatusPath <batch-status.csv> -ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>
```

页面实探或批次任务开始前：

```powershell
python scripts/test_design_excel_tools.py init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001
```

有导入文件时追加：

```powershell
-ImportWorkbookPath <导入文件.xlsx>
```

当前批次 Python 临时脚本执行前：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```
