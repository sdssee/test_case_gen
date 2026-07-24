# 分批运行与批次账本规则

## 分批策略

- 当任务范围是全产品、多个一级模块、大模块，或超过一个最小标题时，禁止直接生成完整测试用例。
- 必须先遍历一级菜单、二级菜单、三级菜单及更深层标题，拿到菜单轮廓、页面清单和功能地图后输出分批设计计划。
- 分批必须按当前产品或模块可识别的最深标题级别执行，哪个标题级别最小就以哪个最小标题作为一个批次。
- 每个已通过批次只能覆盖 1 个最小标题路径，`最小标题路径` 使用 `一级>二级>三级>四级` 形式记录唯一叶子节点。
- 禁止合并多个最小标题，禁止再拆分一个最小标题。

## 批次账本

范围超过一个最小标题时，必须在 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/` 创建或更新：

- `batch-plan.md`
- `batch-status.csv`
- `batch-review.md`
- `page-discovery.csv`
- `artifacts/`

必须优先复制 `docs/test-assets/batch-runs/templates/` 中的模板。
只要发生页面实探或生成 `page-discovery.csv`，即使当前任务只有一个最小标题路径，也必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，禁止临时手写旧版 `page-discovery.csv` 表头或跳过 `batch-status.csv`。
所有截图、临时脚本、页面证据和过程材料必须保存到当前任务目录的 `docs/test-assets/batch-runs/<task>/artifacts/` 下，禁止写入共享的 `docs/test-assets/batch-runs/artifacts/` 根目录 artifacts，避免不同任务证据混淆。

## 每批质量门禁

每批必须完成：

- 当前批次页面遍历和页面元素覆盖。
- 功能测试、性能测试、异常、边界、权限、状态、数据一致性。
- 风险与待确认问题、自动化建议。
- 资产回存和产品版图同步。
- `batch-status.csv` 覆盖质量自检。

`batch-status.csv` 必须记录最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、导入文件路径、导入文件已生成。

当前批次覆盖质量自检通过后，才能进入下一批。所有批次完成后只做最终汇总、跨模块汇总、回归范围、风险清单和客户总览，不得重新生成各批完整用例。

批次交付收口必须使用统一工具 `scripts/test_design_excel_tools.py complete-deliverables` 一站式完成中间文件预检、正式 Excel 格式修复、导入文件生成、交付复制、产品版图同步和交付件校验。禁止手工在 `current/`、`deliverables/`、`docs/test-assets/modules/`、`docs/test-assets/imports/` 之间反复复制，禁止把正常批次拆成多轮修复和校验脚本。`batch-status.csv` 中已通过批次的 `归档路径` 必须指向 `docs/test-assets/modules/` 下的内部模块归档，`导入文件路径` 必须指向 `docs/test-assets/imports/` 下的导入归档。需要同步产品版图时传入 `--product-map`、`--page-discovery` 和 `--batch-status`，由工具调用 `sync-product-map`。

交付文件名必须只使用菜单/模块路径，例如 `一级模块_二级菜单_三级菜单_测试设计.xlsx` 和 `一级模块_二级菜单_三级菜单_导入用例.xlsx`。不得把运行文件夹名、批次目录名或产品名拼入文件名；如果 `module-path` 中包含产品名，必须同时传入 `--product-name` 由统一工具自动去除，避免 `文件夹名_产品名_模块名_测试设计.xlsx` 与 `一级菜单_二级菜单_三级菜单_测试设计.xlsx` 形成重复交付。

## 文件格式门禁

- `batch-status.csv` 和 `page-discovery.csv` 必须复制标准模板或按完全相同表头生成，禁止自定义精简表头。
- `page-discovery.csv` 必须使用 CSV writer 或等价结构化方式写入，保证每行列数与表头一致，防止字段错位。
- `batch-plan.md` 不得仍标记已完成批次为执行中或待开始；页面清单数量必须与 `batch-status.csv` 页面数一致。
- `batch-review.md` 必须引用已完成批次的批次 ID、归档路径和导入文件路径。

## 中间文件限制

- 禁止创建承载全量测试用例正文的单一中间文件，例如单个 Python、JSON、CSV、Markdown 或临时脚本文件。
- 脚本只能用于当前批次的模板填充、格式转换或校验，并保存到本任务 `artifacts/scripts/`。
- 当前批次 Python 临时脚本或 JSON 数据分片也不得过大；单个 Python 建议小于 200KB，单个 JSON/CSV/Markdown/TXT 中间文件建议小于 256KB，超过时必须继续按最小标题路径或页面域分片。
- 不得把大量用例正文、步骤、预期结果或页面元素清单内联到一个 Python 列表/字典或一个 JSON 文件中；Python 只保留模板填充逻辑，数据优先来自当前批次正式 Excel、`page-discovery.csv`、`batch-status.csv` 或小型分片。
- 当前批次 Python 临时脚本写入中文文本、菜单路径、测试步骤、预期结果或 JSON 数据时，必须使用 `repr()`、`json.dumps(..., ensure_ascii=False)` 或结构化数据文件读取。
- 执行前必须运行 `scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>`，通过单文件大小、JSON 语法、Python 语法编译和中文弯引号、智能引号、未转义双引号风险扫描后才能执行。
- 交付前必须清理 `artifacts/scripts/__pycache__/`，不得把 Python 缓存目录作为运行结果保留。
