# CodeBuddy 项目级 Memory：测试设计规范包

本仓库是测试设计与测试用例生成规范包，不是传统应用代码项目。CodeBuddy 执行时应把本文件作为轻量项目级入口，详细规则按任务类型读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求文档、用户故事、接口文档、页面截图、原型、可访问页面、缺陷单或已有用例，生成结构化测试设计。
- 正式交付物优先使用 `docs/test-design/codebuddy-test-design-template.xlsx`。
- 正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
- 每次正式交付都必须复制 `docs/test-design/测试用例模板.xlsx` 生成独立测试系统导入文件，不修改原模板，也不询问是否需要生成。
- 所有交付件统一放在 `docs/test-design/deliverables/`。

## 必读文件

每次测试设计任务先读取：

- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/.rules/test-design-rule.mdc`
- `docs/test-design/rules/README.md`
- `docs/test-design/rules/case-design.md`
- `docs/test-design/rules/excel-deliverable.md`
- `docs/test-design/rules/import-template.md`
- `docs/test-design/rules/data-safety.md`
- `docs/test-design/rules/dfx-test-strategy.md`
- `docs/test-design/excel-template-spec.md`

按任务追加：

- 页面、截图、原型、浏览器或 computer use：`docs/test-design/rules/page-discovery.md`
- 全产品、大模块、多菜单或超过一个最小标题：`docs/test-design/rules/batch-run.md`

## 不可违反的摘要规则

- `前置条件`、`操作步骤`、`预期结果` 必须编号换行；`操作步骤` 必须从系统或项目入口开始写完整导航路径。
- `用例标题` 和导入文件 `测试用例名称` 必须使用 `功能点-当前用例标题` 格式。
- 页面元素覆盖清单只是覆盖追踪矩阵，不写独立测试步骤或完整预期。
- 页面已有数据只能查看、搜索、筛选、排序、分页、打开详情、进入编辑页观察或打开危险操作确认弹窗，不得保存、提交、最终确认或改变状态。
- 只能对本次创建且带 `AI_TEST`、`CODEX_TEST`、日期或任务编号的数据执行敏感操作。
- 有页面时必须深遍历所有可点击、可输入、可选择、可测试元素；选择类控件记录选项取值和联动/依赖变化，输入类控件记录实际输入、真实提示和结果分支，新增类流程必须实填实走。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 范围超过一个最小标题时，必须按最深标题级别分批执行，逐个最小标题路径完成完整测试设计，不得合并多个最小标题，不得再拆分一个最小标题。
- 正式写测试用例前，必须先展示风险项与待确认问题并让用户确认；用户确认、补充、排除或调整后，动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
- 模块或批次正式写测试用例前，必须先综合评估 DFX 12 维度 × 4 场景覆盖，明确适用、不适用、待确认和需补充证据的维度，再进入用例设计。
- 每批都必须覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单。
- 异常值、边界值和测试策略必须按 DFX 覆盖评估结果落地，不得只写一句笼统策略；正式 Excel 必须填写 `DFX维度` 和 `DFX场景`，`场景类型`、`正向/反向` 不再作为测试策略字段；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- `功能点` 作为父场景和连续分组；按业务流程、步骤、元素、接口和数据对象展开原子测试点，`测试场景矩阵` 每行只保留一个主 DFX 维度与场景，禁止机械展开元素 × 12 × 4。
- 原子场景必须改变实际角色、状态、数据、动作、环境、观察点或恢复路径；完全相同场景合并，相同步骤用例仅作为非阻塞合并候选，最终用例必须保持可独立执行的业务闭环。
- 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
- 交付文件名只使用菜单/模块路径，不拼运行文件夹名、批次目录名或产品名；如 `module-path` 包含产品名前缀，传入 `--product-name` 自动去除，避免重复交付文件。
- 导入文件 `执行方式` 默认 `手动`，也就是默认填写 `手动`；只有已有可运行、可维护且覆盖主要校验点的自动化资产，并且本次明确按自动化导入或关联资产时，才允许 `自动化`。
- 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。

## 生成后校验

正式测试设计和导入文件生成后一起校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```


当前批次 Python 临时脚本或 JSON/CSV/Markdown/TXT 中间分片执行前运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```

该预检会检查单文件大小、JSON 语法、Python 语法和中文弯引号风险。

## Git 约定

- 每次完成修改后检查 Git 变更。
- 修改完成且验证通过后，提交当前修改并推送到 `origin`。
- Commit Message 使用中文，简洁说明本次修改内容。
