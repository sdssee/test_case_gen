# 分批运行与批次账本规则

## 分批策略

- 当任务范围是全产品、多个一级模块、大模块，或超过一个最小标题时，禁止直接生成完整测试用例。
- 必须先遍历一级菜单、二级菜单、三级菜单及更深层标题，拿到菜单轮廓、页面清单和功能地图后输出分批设计计划。
- 分批必须按当前产品或模块可识别的最深标题级别执行，哪个标题级别最小就以哪个最小标题作为一个批次。
- 每个已通过批次只能覆盖 1 个最小标题路径，`最小标题路径` 使用 `一级>二级>三级>四级` 形式记录唯一叶子节点。
- 禁止合并多个最小标题，禁止再拆分一个最小标题。

## 批次账本

范围超过一个最小标题时，先建立任务级批次队列；随后每个最小标题必须使用独立目录
`docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>_<BATCH-ID>/` 执行。一个 run-dir 只能有一个批次、一个最小标题、一个 manifest 和一组 Sheet JSON；`batch-status.csv` 必须且只能有一行。每个独立目录包含：

- `batch-plan.md`
- `batch-status.csv`
- `batch-review.md`
- `page-discovery.csv`
- `element-case-plan.csv`
- `test-data-lifecycle.csv`
- `risk-confirmation.csv`
- `artifacts/`

必须优先复制 `docs/test-assets/batch-runs/templates/` 中的模板。
只要发生页面实探或生成 `page-discovery.csv`，即使当前任务只有一个最小标题路径，也必须先执行 `scripts/run-test-design.ps1 init-batch-run` 初始化批次目录，禁止临时手写旧版 `page-discovery.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 表头或跳过 `batch-status.csv`。同名批次默认禁止重复初始化；继续已有批次必须使用 `--resume`，强制重建必须使用 `--force-reinitialize`，并保留工具自动生成的时间戳备份。
所有截图、临时脚本、页面证据和过程材料必须保存到当前独立批次目录的 `artifacts/` 下，禁止写入共享根目录或其他批次目录，避免证据、会话和分片混淆。
`init-batch-run` 会创建 `artifacts/scripts/`、`artifacts/data/` 和 `artifacts/screenshots/`；功能用例分片、Sheet JSON 和页面证据必须写入这些目录，禁止把 `function_cases_part_*.json`、页面发现副本或元素计划副本直接写到 `artifacts/` 根目录。

## 每批质量门禁

每批必须完成：

- 当前批次页面遍历和页面元素覆盖。
- 当前批次元素用例计划和测试数据生命周期记录。
- 功能测试、性能测试、异常、边界、权限、状态、数据一致性。
- 风险与待确认问题、自动化建议。
- 资产回存和产品版图同步。
- `batch-status.csv` 覆盖质量自检。

`batch-status.csv` 必须记录最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、导入文件路径、导入文件已生成。

阶段性门禁必须按顺序执行：

1. 页面发现后运行：`powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 validate-batch-artifacts --run-dir <batch-run-dir> --phase discovery`，校验 `page-discovery.csv` 表头、列数、真实可交互元素和 `batch-status.csv` 状态。
2. 先默认完成全部页面、元素、交互路径和 CRUD 生效闭环并通过 plan 门禁；随后仅把模型仍无法理解的业务语义、规则歧义或页面无法观察项写入 `risk-confirmation.csv`。真实确认项由用户确认后更新为 `已确认/否`；没有模型不理解项时由模型运行 `record-risk-none` 写入唯一的 `RISK-NONE/无需用户确认/否`，不得伪造用户确认。
3. 功能用例分片生成前运行：`powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 prepare-function-case-generation --run-dir <batch-run-dir>`，清理旧分片和旧 manifest，确保本轮只保留当前批次有效 JSON。
4. 功能用例分片、Sheet JSON 和正式 Excel 生成前运行：`powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 validate-batch-artifacts --run-dir <batch-run-dir> --phase cases`，先校验 `function_cases_manifest.json`、三位编号分片、标准字段、步骤/预期完整性和每片最多 10 条，再校验 Sheet 分文件、计划用例数量和实际分片数量一致。
5. 严格按 `discovery → plan → risk → cases → delivery` 累积门禁执行；任一阶段失败时回到当前阶段补充页面深探、元素计划、逐修改项生命周期、模型不理解项确认或用例分片，禁止降低预算、删除元素或跳过 DFX 绕过门禁。
6. `element-case-plan.csv` 必须填写 `操作类别`、`验证要求`、`数据策略`、`执行状态`。创建、编辑、删除、配置、状态变更必须使用本次创建或用户提供测试数据并标记实际执行完成；编辑/配置/状态变更的每个修改项必须在 `test-data-lifecycle.csv` 独立一行记录保存后回显和实际生效结果。
7. 生成用例前运行 `prepare-function-case-generation`；该命令先通过 risk 门禁，再清理旧分片、manifest 和七个 Sheet JSON，并生成绑定当前 discovery/plan/lifecycle/risk 哈希的 `generation-session.json`。manifest 必须携带同一 session ID 和 source fingerprint，避免新旧轮次混装或上游变化后继续复用旧用例。

当前独立批次覆盖质量自检通过后，才能初始化下一批的独立 run-dir。所有批次完成后，任务级汇总只读取各批归档、receipt 和用例 ID，生成跨模块汇总、回归范围、风险清单和客户总览，不得把多个批次重新合并到一个 manifest 或重新生成各批完整用例。

批次交付收口必须使用统一工具 `scripts/run-test-design.ps1 complete-deliverables --run-dir <batch-run-dir>`，由框架从 manifest 与按 Sheet JSON 组装 8 Sheet 正式 Excel，再一站式完成格式修复、导入文件生成、交付复制、产品版图同步和交付件校验。禁止编写批次级 `gen_excel.py` 或使用 openpyxl 直接保存正式 Excel。收口工具必须先校验正式工作簿和导入文件，再更新正式目录、批次账本与产品版图；任何校验或同步失败时必须恢复本次调用前的文件状态。禁止手工在 `current/`、`deliverables/`、`docs/test-assets/modules/`、`docs/test-assets/imports/` 之间反复复制，禁止把正常批次拆成多轮修复和校验脚本。`batch-status.csv` 中已通过批次的 `归档路径` 必须指向 `docs/test-assets/modules/` 下的内部模块归档，`导入文件路径` 必须指向 `docs/test-assets/imports/` 下的导入归档。

交付文件名必须只使用菜单/模块路径，例如 `一级模块_二级菜单_三级菜单_测试设计.xlsx` 和 `一级模块_二级菜单_三级菜单_导入用例.xlsx`。不得把运行文件夹名、批次目录名或产品名拼入文件名；如果 `module-path` 中包含产品名，必须同时传入 `--product-name` 由统一工具自动去除，避免 `文件夹名_产品名_模块名_测试设计.xlsx` 与 `一级菜单_二级菜单_三级菜单_测试设计.xlsx` 形成重复交付。

## 文件格式门禁

- `batch-status.csv`、`page-discovery.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 和 `risk-confirmation.csv` 必须复制标准模板或按完全相同表头生成，禁止自定义精简表头。
- `page-discovery.csv`、`element-case-plan.csv` 和 `test-data-lifecycle.csv` 必须使用 CSV writer 或等价结构化方式写入，保证每行列数与表头一致，防止字段错位。
- `batch-plan.md` 不得仍标记已完成批次为执行中或待开始；页面清单数量必须与 `batch-status.csv` 页面数一致。
- `batch-review.md` 必须引用已完成批次的批次 ID、归档路径和导入文件路径。

## 中间文件限制

- 禁止创建承载全量测试用例正文的单一中间文件，例如单个 Python、JSON、CSV、Markdown 或临时脚本文件。
- 脚本只能用于当前批次的模板填充、格式转换或校验，并保存到本任务 `artifacts/scripts/`。
- 当前批次 Python 临时脚本或 JSON 数据分片也不得过大；单个 Python 建议小于 200KB，单个 JSON/CSV/Markdown/TXT 中间文件建议小于 256KB，超过时必须继续按最小标题路径或页面域分片。
- 不得把大量用例正文、步骤、预期结果或页面元素清单内联到一个 Python 列表/字典或一个 JSON 文件中；Python 只保留模板填充逻辑，数据优先来自当前批次正式 Excel、`page-discovery.csv`、`batch-status.csv` 或小型分片。
- Excel 数据必须按 Sheet 分文件输出到 `artifacts/data/`：`overview.json`、`requirements.json`、`scenarios.json`、`performance.json`、`risks.json`、`automation.json`、`page_elements.json` 等；功能用例必须按 `function_cases_part_001.json`、`function_cases_part_002.json` 分片，每个分片最多 10 条。
- 功能用例分片必须同步写 `function_cases_manifest.json`，禁止保留 `function_cases_part_01.json`、旧批次分片或未被 manifest 引用的分片。
- 功能用例分片字段必须使用标准字段，禁止 `用例编号`、`用侊 ID`、`用侊标题`、`场景类型`、`steps`、`expected` 等错字段、旧字段或英文模板字段进入 JSON 和 Excel。
- Sheet 构建脚本必须按职责拆分，`assemble_workbook.py` 只负责复制模板、调用 Sheet 写入和保存，禁止一个大脚本内联全部 Sheet 数据和用例正文。
- 当前批次 Python 临时脚本写入中文文本、菜单路径、测试步骤、预期结果或 JSON 数据时，必须使用 `repr()`、`json.dumps(..., ensure_ascii=False)` 或结构化数据文件读取。
- 执行前必须运行 `scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>`，通过单文件大小、JSON 语法、Python 语法编译和中文弯引号、智能引号、未转义双引号风险扫描后才能执行。
- 交付前必须清理 `artifacts/scripts/__pycache__/`，不得把 Python 缓存目录作为运行结果保留。
