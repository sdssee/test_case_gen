# CodeBuddy 项目级 Memory：测试设计规范包

本仓库是测试设计与测试用例生成规范包，不是传统应用代码项目。CodeBuddy 执行时应把本文件作为轻量项目级入口，详细规则按任务类型读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求文档、用户故事、接口文档、页面截图、原型、可访问页面、PR Diff、缺陷单或已有用例，生成结构化测试设计。
- 正式交付物优先使用 `docs/test-design/codebuddy-test-design-template.xlsx`。
- 正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
- 需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，不修改原模板。
- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。
- 内部产品级测试资产库放在 `docs/test-assets/`，主入口为 `docs/test-assets/product-map.xlsx`，不作为默认客户交付件。

## 必读文件

每次测试设计任务先读取：

- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/.rules/test-design-rule.mdc`
- `docs/test-design/rules/README.md`
- `docs/test-design/rules/case-design.md`
- `docs/test-design/rules/excel-deliverable.md`
- `docs/test-design/rules/data-safety.md`
- `docs/test-design/rules/dfx-test-strategy.md`
- `docs/test-design/excel-template-spec.md`

按任务追加：

- 页面、截图、原型、浏览器或 computer use：`docs/test-design/rules/page-discovery.md`
- 全产品、大模块、多菜单或超过一个最小标题：`docs/test-design/rules/batch-run.md`
- 测试系统导入：`docs/test-design/rules/import-template.md`
- 跨模块依赖、历史归档、补充任务或资产同步：`docs/test-design/rules/product-map-sync.md`、`docs/test-design/archive-and-index-guidelines.md`

## 不可违反的摘要规则

- `前置条件`、`操作步骤`、`预期结果` 必须编号换行；`操作步骤` 必须从系统或项目入口开始写完整导航路径。
- `用例标题` 和导入文件 `测试用例名称` 必须使用 `功能点-当前用例标题` 格式。
- 页面元素覆盖清单只是覆盖追踪矩阵，不写独立测试步骤或完整预期。
- 页面已有数据只能查看、搜索、筛选、排序、分页、打开详情、进入编辑页观察或打开危险操作确认弹窗，不得保存、提交、最终确认或改变状态。
- 只能对本次创建且带 `AI_TEST`、`CODEX_TEST`、日期或任务编号的数据执行敏感操作。
- 有页面时必须深遍历所有可点击、可输入、可选择、可测试元素；选择类控件记录选项取值和联动/依赖变化，输入类控件记录实际输入、真实提示和结果分支，新增类流程必须实填实走。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 范围超过一个最小标题时，必须按最深标题级别分批执行，逐个最小标题路径完成完整测试设计，不得合并多个最小标题，不得再拆分一个最小标题。
- 每批都必须覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单。
- 异常值、边界值和测试策略必须按 DFX 12 维度 × 4 场景矩阵落地，不得只写一句笼统策略；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 五件套。
- `batch-status.csv` 和 `page-discovery.csv` 必须使用标准模板表头，禁止自定义精简表头和字段错位。
- 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
- 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
- 批次交付收口优先使用 `scripts/test_design_excel_tools.py finalize-deliverables`，同步 current、deliverables、modules、imports 和 `batch-status.csv` 路径；传入 `--page-discovery` 时必须同时传入 `--batch-status`。
- 导入文件 `执行方式` 默认 `手动`，也就是默认填写 `手动`；只有已有可运行、可维护且覆盖主要校验点的自动化资产，并且本次明确按自动化导入或关联资产时，才允许 `自动化`。
- 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。
- 正式交付件、导入文件、批次账本、页面实探记录、临时脚本和产品版图不得保留真实环境 URL/IP、真实账号、真实密钥、Token、密码或内部敏感凭据，必须使用 `<product_login_url>` 等占位符。

## 生成后校验

正式测试设计生成后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务追加 `-BatchStatusPath <batch-status.csv>`，并传入或自动发现 `page-discovery.csv` 与 `docs/test-assets/product-map.xlsx`。有导入文件时追加 `-ImportWorkbookPath <导入文件.xlsx>`。

页面实探或批次任务开始前运行：

```powershell
python scripts/test_design_excel_tools.py init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001
```

当前批次 Python 临时脚本或 JSON/CSV/Markdown/TXT 中间分片执行前运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```

该预检会检查单文件大小、JSON 语法、Python 语法和中文弯引号风险。

## 升级与 Git

- 外网到内网普通框架升级使用 `scripts/new-framework-upgrade-package.ps1` 和 `scripts/upgrade-framework.ps1`。
- 普通框架升级不得覆盖 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
- 每次完成修改后运行 `git status`。
- 修改完成且验证通过后，默认提交并推送到当前分支。
- GitHub 提交信息使用中文。
