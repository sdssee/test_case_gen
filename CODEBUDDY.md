# CodeBuddy 项目级 Memory：测试设计规范包

本仓库是测试设计与测试用例生成规范包，不是传统应用代码项目。CodeBuddy 执行时应把本文件作为轻量项目级入口，详细规则按任务类型读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求文档、用户故事、接口文档、页面截图、原型、可访问页面、PR Diff、缺陷单或已有用例，生成结构化测试设计。
- 正式交付物优先使用 `docs/test-design/codebuddy-test-design-template.xlsx`。
- 正式测试设计只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
- 需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，不修改原模板。
- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。
- 内部产品级测试资产库放在 `docs/test-assets/`；`catalog/modules/*.json` 是权威事实源，`product-map.xlsx` 是可重建查询视图，均不作为默认客户交付件。

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
- 页面、截图、原型或可访问系统相关任务默认直接全量深度探索，不需要二次确认；必须捕捉全部可交互元素，并对本次创建的测试数据完成创建成功、逐项编辑/修改、保存后回显和实际生效验证。只有真实账号/验证码/权限、疑似生产环境、缺少必要测试数据或真实密钥风险时才暂停确认。
- DFX 是扩展检查矩阵，不是用例生成主轴；必须先建立页面元素/交互路径覆盖骨架，再按适用 DFX 扩展用例，禁止按“每个 DFX 场景一条”压缩功能覆盖。
- 页面深探后必须生成或更新 `element-case-plan.csv`，功能测试用例必须从该计划派生；`应生成用例数` 必须按元素类型 × DFX 最低覆盖预算计算，禁止所有行统一写 1；真实新增、编辑、删除必须同步 `test-data-lifecycle.csv`。
- 页面发现、元素计划和用例分片阶段必须分别运行 `powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 validate-batch-artifacts --run-dir <batch-run-dir> --phase discovery|plan|cases`，门禁失败时先补深探、计划、生命周期或分片，不得继续生成 Excel。
- 生成新一轮功能用例分片前，必须先运行 `powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 prepare-function-case-generation --run-dir <batch-run-dir>` 清理旧分片和旧 manifest。
- 功能测试用例必须按每 10 条一个 `artifacts/data/function_cases_part_001.json` 这类三位编号分片生成，并同步 `artifacts/data/function_cases_manifest.json`；Excel 写入只能读取 manifest 中列出的分片，禁止直接 glob 所有历史分片。
- 功能用例 JSON 只能使用标准字段 `用例 ID`、`用例标题`、`DFX维度`、`DFX场景`、`操作步骤`、`预期结果` 等，禁止 `用例编号`、`用侊 ID`、`用侊标题`、`场景类型`、`steps`、`expected`、英文模板或泛化占位文本。
- Excel 数据必须按 Sheet 分文件输出，禁止一个大 Python 或大 JSON 承载全部 Sheet 和全部用例正文。
- `功能测试用例` 禁止写入 `测试类型=性能规格测试` 或 `DFX维度=DFP性能`；性能、并发、大数据量、资源监控和极端压力场景必须进入 `性能测试设计`、风险或自动化建议。
- 分页和下拉是复合控件：下拉必须实际选择代表项并记录联动，分页必须拆出每页条数、翻页/跳转和边界/禁用态。
- 新增、编辑、删除等会落库或改变状态的操作必须绑定本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户提供测试数据标识的数据；既有数据只能查看、编辑不保存或删除确认弹窗取消。
- 配置项保存类用例必须验证保存后回显和实际生效，不能只写点击保存或提示成功。
- 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
- 范围超过一个最小标题时，必须按最深标题级别分批执行，逐个最小标题路径完成完整测试设计，不得合并多个最小标题，不得再拆分一个最小标题。
- 正式写测试用例前，必须先展示风险项与待确认问题并让用户确认；用户确认、补充、排除或调整后，动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
- 默认全量深探完成后，仅把模型仍无法理解的业务语义、规则歧义或页面无法观察的内容写入 `risk-confirmation.csv` 请用户确认；风险确认不是是否深探的开关。
- 模块或批次正式写测试用例前，必须先综合评估 DFX 12 维度 × 4 场景覆盖，明确适用、不适用、待确认和需补充证据的维度，再进入用例设计。
- 每批都必须覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单。
- 异常值、边界值和测试策略必须按 DFX 覆盖评估结果落地，不得只写一句笼统策略；正式 Excel 必须填写 `DFX维度` 和 `DFX场景`，`场景类型`、`正向/反向` 不再作为测试策略字段；无法验证的 DFX 场景写入风险、性能设计或自动化建议。
- 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/run-test-design.ps1 init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv`、`risk-confirmation.csv` 和 `artifacts/`；同名批次继续执行时使用 `--resume`，强制重建必须使用 `--force-reinitialize` 并保留自动备份。
- `batch-status.csv` 和 `page-discovery.csv` 必须使用标准模板表头，禁止自定义精简表头和字段错位。
- 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
- 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
- 批次交付收口使用 `powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 complete-deliverables --run-dir <batch-run-dir> --module-path "<模块路径>" --batch-id <批次ID>`，由标准组装器从 manifest 与按 Sheet JSON 生成 8 Sheet 正式 Excel，并同步生成 `current/`、`deliverables/`、内部归档和独立导入文件；禁止编写 `gen_excel.py` 直接保存正式 Excel。
- 交付文件名只使用菜单/模块路径，不拼运行文件夹名、批次目录名或产品名；如 `module-path` 包含产品名前缀，传入 `--product-name` 自动去除，避免重复交付文件。
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
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 init-batch-run --project-root . --run-id <YYYYMMDD_任务标识> --module-path "<一级>><二级>><三级>" --batch-id BATCH-001
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
