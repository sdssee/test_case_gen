# Codex Project Instructions

本仓库是测试设计与测试用例生成规范包，不是传统应用代码项目。Codex 处理本仓库或复制到业务项目后，应把本文件作为轻量项目级执行入口，详细规则按任务类型读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求文档、用户故事、接口文档、页面截图、原型、可访问页面、PR Diff、缺陷单或已有用例，生成结构化测试设计。
- 正式交付物优先使用 `docs/test-design/codebuddy-test-design-template.xlsx`。
- 正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
- 需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，不修改原模板。
- 测试资产事实必须沉淀到项目文件，不依赖 AI 对话记忆。
- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。
- 内部产品级测试资产库放在 `docs/test-assets/`，主入口为 `docs/test-assets/product-map.xlsx`，不作为默认客户交付件。

## 使用现有规范

Codex 应优先读取并遵守：

- `CODEBUDDY.md`
- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/.rules/test-design-rule.mdc`
- `.codebuddy/rules/test-design-rule.md`
- `docs/test-design/rules/README.md`
- `docs/test-design/excel-template-spec.md`
- `docs/test-design/archive-and-index-guidelines.md`

按任务追加读取：

- 页面、截图、原型、浏览器或 computer use：`docs/test-design/rules/page-discovery.md`
- 全产品、大模块、多菜单或超过一个最小标题：`docs/test-design/rules/batch-run.md`
- 测试系统导入：`docs/test-design/rules/import-template.md`
- 跨模块依赖、历史归档、补充任务或资产同步：`docs/test-design/rules/product-map-sync.md`
- 所有任务基础规则：`docs/test-design/rules/case-design.md`、`excel-deliverable.md`、`data-safety.md`
- 异常、边界、性能、安全、兼容、可靠、可用性等测试策略：`docs/test-design/rules/dfx-test-strategy.md`

## 执行摘要

- 生成或补充测试用例前，读取 `docs/test-assets/product-map.xlsx` 和用户指定依赖模块的归档测试设计。
- 正式生成前展示产品理解摘要或模块理解摘要，包括当前模块、依赖模块、业务对象、业务链路、可复用历史用例、预计新增范围和待确认问题。
- 模块任务先做粗遍历，识别菜单入口、页面清单、核心功能点、业务对象、状态流转和跨模块依赖，并沉淀到 `product-map.xlsx`。
- 有页面时必须深遍历所有可点击、可输入、可选择、可测试元素。
- 范围超过一个最小标题时，必须按最深标题级别建立批次队列，逐个最小标题路径执行；禁止合并多个最小标题，禁止再拆分一个最小标题。
- 每批都必须执行完整规则，覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单。
- 异常值、边界值和测试策略必须按 DFX 12 维度 × 4 场景矩阵落地，不得只写一句笼统策略；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须走增量补充流程，不得只追加用例。
- 功能测试用例按模块、页面、业务流程和小功能块连续编排。
- `前置条件`、`操作步骤`、`预期结果` 编号换行；`操作步骤` 从系统或项目入口开始写完整导航路径。
- `用例标题` 和导入文件 `测试用例名称` 使用 `功能点-当前用例标题` 格式。
- 页面已有数据只能查看和只读深探，不得保存、提交、最终确认或改变状态；敏感操作只允许作用于本次创建且带测试标识的数据。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 五件套。
- `batch-status.csv` 和 `page-discovery.csv` 使用标准模板表头，禁止自定义精简表头和字段错位。
- 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
- 导入文件优先使用 `scripts/test_design_excel_tools.py generate-import`，保留模板下拉框、必填样式、标红字段和自动生成字段空值。
- 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。
- 批次交付收口优先使用 `scripts/test_design_excel_tools.py finalize-deliverables`，同步 current、deliverables、modules、imports 和 `batch-status.csv` 路径；传入 `--page-discovery` 时必须同时传入 `--batch-status`。
- 导入文件 `执行方式` 默认 `手动`，也就是默认填写 `手动`；自动化建议或 AI 页面实探不能作为填写 `自动化` 的依据。
- 正式交付件、导入文件、批次账本、页面实探记录、临时脚本和产品版图不得保留真实环境 URL/IP、真实账号、真实密钥、Token、密码或内部敏感凭据，使用 `<product_login_url>` 等占位符。

## 校验命令

项目稳定性自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务追加 `-BatchStatusPath <batch-status.csv>`，并传入或自动发现 `page-discovery.csv` 与 `docs/test-assets/product-map.xlsx`；有导入文件时追加 `-ImportWorkbookPath <导入文件.xlsx>`。

页面实探或批次任务开始前：

```powershell
python scripts/test_design_excel_tools.py init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001
```

当前批次 Python 临时脚本执行前：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```

## Git 约定

- 每次完成修改后默认执行 `git status` 检查变更。
- 修改完成且验证通过后，默认提交到当前分支并推送到 `origin`。
- GitHub 提交信息必须使用中文，简洁说明本次规范、模板、脚本或文档变更。
