# Codex Project Instructions

本仓库是测试设计与测试用例生成规范包，不是传统应用代码项目。Codex 处理本仓库或复制到业务项目后，应把本文件作为轻量项目级执行入口，详细规则按任务类型读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求文档、用户故事、接口文档、页面截图、原型、可访问页面、PR Diff、缺陷单或已有用例，生成结构化测试设计。
- 正式交付物优先使用 `docs/test-design/codebuddy-test-design-template.xlsx`。
- 正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
- 需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，不修改原模板。
- 测试资产事实必须沉淀到项目文件，不依赖 AI 对话记忆。
- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。

## 使用现有规范

Codex 应优先读取并遵守：

- `CODEBUDDY.md`
- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/.rules/test-design-rule.mdc`
- `.codebuddy/rules/test-design-rule.md`
- `docs/test-design/rules/README.md`
- `docs/test-design/excel-template-spec.md`

按任务追加读取：

- 页面、截图、原型、浏览器或 computer use：`docs/test-design/rules/page-discovery.md`
- 全产品、大模块、多菜单或超过一个最小标题：`docs/test-design/rules/batch-run.md`
- 测试系统导入：`docs/test-design/rules/import-template.md`
- 所有任务基础规则：`docs/test-design/rules/case-design.md`、`excel-deliverable.md`、`data-safety.md`
- 异常、边界、性能、安全、兼容、可靠、可用性等测试策略：`docs/test-design/rules/dfx-test-strategy.md`

## 执行摘要

- 正式生成前展示产品理解摘要或模块理解摘要，包括当前模块、依赖模块、业务对象、业务链路、可复用历史用例、预计新增范围、风险项和待确认问题。
- 正式写测试用例前，必须先把风险项与待确认问题发给用户确认，并根据用户确认、补充、排除或调整动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
- 模块或批次正式写测试用例前，必须先综合评估 DFX 12 维度 × 4 场景覆盖，明确适用、不适用、待确认和需补充证据的维度，再进入用例设计。
- 有页面时必须深遍历所有可点击、可输入、可选择、可测试元素。
- 范围超过一个最小标题时，必须按最深标题级别建立批次队列，逐个最小标题路径执行；禁止合并多个最小标题，禁止再拆分一个最小标题。
- 每批都必须执行完整规则，覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单。
- 异常值、边界值和测试策略必须按 DFX 覆盖评估结果落地，不得只写一句笼统策略；正式 Excel 必须填写 `DFX维度` 和 `DFX场景`，`场景类型`、`正向/反向` 不再作为测试策略字段；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须走增量补充流程，不得只追加用例。
- 功能测试用例按模块、页面、业务流程和小功能块连续编排。
- `前置条件`、`操作步骤`、`预期结果` 编号换行；`操作步骤` 从系统或项目入口开始写完整导航路径。
- `用例标题` 和导入文件 `测试用例名称` 使用 `功能点-当前用例标题` 格式。
- 页面已有数据只能查看和只读深探，不得保存、提交、最终确认或改变状态；敏感操作只允许作用于本次创建且带测试标识的数据。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 五件套。
- `batch-status.csv` 和 `page-discovery.csv` 使用标准模板表头，禁止自定义精简表头和字段错位。
- 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
- 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
- 导入文件随批次交付优先由 `scripts/test_design_excel_tools.py complete-deliverables` 统一生成；只需单独生成导入文件时才使用 `generate-import`，保留模板下拉框、必填样式、标红字段和自动生成字段空值。
- 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。
- 交付文件名只使用菜单/模块路径，不拼运行文件夹名、批次目录名或产品名；如 `module-path` 包含产品名前缀，传入 `--product-name` 自动去除，避免重复交付文件。
- 导入文件 `执行方式` 默认 `手动`，也就是默认填写 `手动`；自动化建议或 AI 页面实探不能作为填写 `自动化` 的依据。

## 校验命令

项目稳定性自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```


页面实探或批次任务开始前：

```powershell
python scripts/test_design_excel_tools.py init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001
```

当前批次 Python 临时脚本或 JSON/CSV/Markdown/TXT 中间分片执行前：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```

该预检会检查单文件大小、JSON 语法、Python 语法和中文弯引号风险。

## Git 约定

- 每次完成修改后默认执行 `git status` 检查变更。
- 修改完成且验证通过后，默认提交到当前分支并推送到 `origin`。
- GitHub 提交信息必须使用中文，简洁说明本次规范、模板、脚本或文档变更。
