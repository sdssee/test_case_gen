# 批次运行状态账本

`docs/test-assets/batch-runs/` 是内部批次级执行状态目录。大范围任务先建立最小标题队列，再为每个最小标题创建独立 run-dir；一个 run-dir 只能承载一个批次、一行 `batch-status.csv` 和一套 manifest/Sheet 产物。该目录不作为默认客户交付件。

## 适用范围

当任务范围属于全产品、多个一级模块、某个大模块，或超过一个最小标题时，必须创建本次任务的批次运行目录：

```text
docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>_<BATCH-ID>/
  batch-plan.md
  batch-status.csv
  batch-review.md
  page-discovery.csv
  element-case-plan.csv
  test-data-lifecycle.csv
  risk-confirmation.csv
  artifacts/
    data/
      overview.json
      requirements.json
      scenarios.json
      function_cases_part_001.json
      function_cases_part_002.json
      performance.json
      risks.json
      automation.json
      page_elements.json
    scripts/
      assemble_workbook.py
      build_function_cases.py
```

只要发生页面实探或生成 `page-discovery.csv`，即使当前任务只有一个最小标题路径，也必须先执行 `scripts/run-test-design.ps1 init-batch-run` 初始化批次目录，禁止临时手写旧版 `page-discovery.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 表头或跳过 `batch-status.csv`。已存在同名批次时使用 `--resume` 原样恢复；强制重建使用 `--force-reinitialize`，工具会先创建时间戳备份。

## 文件职责

- `batch-plan.md`：记录菜单轮廓、页面清单、功能地图、拆分维度、依赖批次和预计交付物。
- `batch-scope.json`：初始化时固定产品名称、批次 ID 和唯一叶子模块；后续恢复、生成会话与交付收口必须复用，不得把一级模块误当产品。
- `batch-status.csv`：只记录当前独立批次的执行状态、唯一最小标题路径、覆盖数量、用例数量和覆盖质量自检结果。
- `batch-review.md`：记录当前批次复盘；所有批次完成后的跨模块汇总应读取各批归档另行生成。
- `page-discovery.csv`：记录页面或功能点实探证据，包括最小标题路径、页面入口、元素、交互方式、选择类控件的选项取值/输入值、联动/依赖变化、输入类控件的实际输入、结果分支/后续状态、完整点击路径、观察行为、覆盖状态和关联用例 ID。
- `selection-option-observations.csv`：选择类控件逐选项事实账本；有限集合每个选项一行，可选项实际选择，真实禁用项记录尝试结果、具体阻塞状态与独立证据；只有明确远程、异步、懒加载、分页加载或按需服务端来源才可使用动态集合，本地可搜索枚举仍按有限集合处理；动态集合记录搜索、滚动/分页、边界与清空策略，每行保存前后状态、结果分支、恢复结果、证据路径/定位和关联用例 ID。
- `element-case-plan.csv`：记录每个页面元素到 DFX 扩展方向、应生成用例数、计划用例ID和实际用例ID的映射，功能测试用例必须从该计划派生。
- `test-data-lifecycle.csv`：记录本次创建或用户提供测试数据的创建、查看、编辑、配置生效、删除取消、删除确认和清理状态。
- `risk-confirmation.csv`：默认全量深探完成后，仅记录模型仍无法理解的业务语义、规则歧义或页面无法观察项、已完成深探依据、用户结论和处置策略；它不是是否深探的开关。
- `element-case-plan.csv`：使用结构化 `操作类别/验证要求/数据策略/执行状态`，按页面与元素精确映射，禁止同名按钮跨页面互相覆盖。
- `test-data-lifecycle.csv`：编辑、配置和状态变更按每个 `关联页面/入口 + 修改项/元素` 独立记录修改前后值、保存后回显和实际生效结果。
- `artifacts/`：保存本次批次执行过程中产生的中间截图、页面遍历笔记、临时导出或核对材料；证据只能放在当前任务目录下，禁止写入共享的 `docs/test-assets/batch-runs/artifacts/` 根目录。

## 执行规则

1. 建立批次运行目录时，应通过 `init-batch-run` 复制 `templates/` 并显式传入真实 `--product-name`；工具写入 `batch-scope.json`，后续交付自动复用并拒绝冲突产品名。
2. 每个批次正式写测试用例前，必须先完成当前批次最小标题路径下的页面或功能点遍历。
3. 每个批次正式写测试用例前，必须把当前批次最小标题路径和页面或功能点实探结果写入 `page-discovery.csv`。
   `batch-status.csv`、`page-discovery.csv`、`selection-option-observations.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 和 `risk-confirmation.csv` 必须复制 `templates/` 中的标准模板或保持与模板完全一致的表头，禁止自定义精简表头、增删列或在末尾追加汇总行。
   `page-discovery.csv` 必须使用 CSV writer 或等价结构化写入方式生成，每一行列数必须与表头一致，禁止手工拼接导致字段错位。
   下拉框、级联选择、单选框、复选框、树选择、枚举筛选等选择类控件不得只展开查看选项；有限集合的每个可选项必须实际选择并写入 `selection-option-observations.csv`，页面真实禁用项必须尝试选择并记录具体阻塞状态与独立证据，动态集合必须记录覆盖策略，同时汇总 `选项取值/输入值` 和 `联动/依赖变化`。
   输入框、搜索框、文本域、数字框、日期框、URL/地址、端口、邮箱、手机号、名称、编码等输入类控件不得只观察字段存在，必须实际输入测试数据并记录真实提示、`选项取值/输入值`、`预期/观察行为` 和 `结果分支/后续状态`。
   新增、创建、添加、新建、保存、提交、下一步、完成、测试连接等新增类流程必须实填实走；成功时进入详情页、下一级页面或后续配置页继续观察，失败时记录真实失败提示、停留页面和可恢复路径。
   既有数据必须主动只读深探：进入详情、编辑页、删除/停用/提交确认弹窗观察字段、联动、二次确认、权限提示和取消路径，但不得最终确认；可以复制既有数据的非敏感字段，改名或改编码为带测试标识的新数据后新增。
4. 必须先默认全量深探并回写 `page-discovery.csv`，通过 plan 后再进行 risk；页面可验证问题由模型自行操作验证，未完成时退回 discovery，风险只阻塞 risk/cases。创建类操作必须真实创建成功，所有修改项必须逐项验证保存回显和实际生效。没有模型不理解项时运行 `record-risk-none`，无需打扰用户。
5. 不同用例不得复用相同的操作步骤与预期结果；标题中的选项值、页码、每页条数和状态值必须同时落到步骤与预期，JSON、正式工作簿和导入工作簿必须逐字段一致。
6. 使用 `pipeline-status --run-dir <run-dir>` 从资产事实派生下一步，不信任人工状态文字；阶段成功会写入带输入哈希的 `.validation-cache.json`，任一上游输入、模板、证据或验证器变化都会使缓存失效。
7. 功能用例必须按每 10 条一个 `function_cases_part_*.json` 分片生成，Excel 数据按 Sheet 分文件输出，最终由 `assemble_workbook.py` 或等价组装脚本写入模板。
8. 每个批次完成后，必须更新 `batch-status.csv`，并填写最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、页面遍历完成、功能用例完成、性能设计完成、异常边界权限覆盖完成、页面元素覆盖完成、产品版图已更新、导入文件路径、导入文件已生成和覆盖质量自检。
   已完成批次在 `batch-plan.md` 中不得仍标记为执行中或待开始；`batch-plan.md` 的页面清单数量必须与 `batch-status.csv` 的页面数一致。
   `complete-deliverables` 会把状态、覆盖数量、正式归档路径、导入路径、质量自检和遗留问题自动回填到 `batch-review.md` 的唯一完成行；空模板行、重复批次行或与 `batch-status.csv` 不一致的评审行均不得交付。
   `product-map.xlsx` 的十个 Sheet 都必须沉淀真实产品资产，禁止保留 `示例产品`、`示例模块`、`示例页面` 等模板样例行；其中 `用例资产索引` 必须覆盖正式测试设计中的全部功能用例 ID，`页面元素地图` 必须覆盖正式测试设计中的全部页面元素。
9. 已覆盖元素数、功能用例数、性能场景数和各 DFX 维度/场景数量必须能从正式测试设计 Excel、页面元素覆盖清单或 `element-case-plan.csv` 中追溯；不能只填写主观完成状态。
10. 当前批次的覆盖质量自检通过后，才能进入下一批；下一最小标题必须初始化新的独立 run-dir，禁止向当前 `batch-status.csv` 追加第二个批次。
11. 禁止创建承载全量测试用例正文的单一中间文件，例如单个 Python、JSON、CSV、Markdown 或临时脚本文件；脚本只能用于当前批次的模板填充、格式转换或校验，并保存到本任务 artifacts/scripts/，不得把多个最小标题路径、多个批次或全产品测试用例先集中写入一个文件后再统一生成 Excel。
   如确需生成当前批次 Python 临时脚本，写入中文文本、菜单路径、测试步骤、预期结果或 JSON 数据时，必须使用 `repr()`、`json.dumps(..., ensure_ascii=False)` 或结构化数据文件读取，禁止手工拼接包含中文弯引号、智能引号或未转义双引号的字符串字面量。执行前必须运行 `scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>`，通过语法编译和高风险引号扫描后才能执行。
   正式测试设计、导入文件、`batch-plan.md`、`batch-status.csv`、`page-discovery.csv`、`batch-review.md`、临时脚本和 `product-map.xlsx` 都不得保留真实环境 URL/IP、内部域名/主机名、真实账号、真实密码、疑似真实密钥、Token 或内部敏感凭据；必须改写为 `<product_login_url>`、`<test_env_base_url>`、`<test_host>`、`<test_user_account>`、`<test_user_password>`、`<valid_api_key>`、`<test_token>`、`<test_service_url>` 等占位符。
12. 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须建立或更新补充批次，不得只追加用例；补充前读取产品版图、归档测试设计、现有交付件、页面元素覆盖清单、`page-discovery.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 和 `batch-status.csv`，识别覆盖缺口、受影响最小标题路径、已有用例 ID 和可复用历史用例。
13. 增量补充必须按最小标题路径重新页面实探目标覆盖缺口，新增用例放在对应小功能块附近，能复用已有用例时引用已有用例 ID，不重复复制；二次补充完成后同步正式测试设计、独立导入文件副本、页面元素覆盖清单、性能测试设计、风险与待确认问题、自动化建议、`docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `product-map.xlsx`。
14. 最终汇总只引用已归档批次成果和用例 ID，不得重新生成各批完整用例。

## 运行期门禁

`scripts/validate-test-design.ps1` 会扫描 `docs/test-assets/batch-runs/`、`docs/test-design/current/` 和 `docs/test-design/deliverables/`，拦截疑似承载全量测试用例正文的单一中间文件，例如 `all_cases.py`、`full_product_cases.json`、`merged_cases.csv`、`case_pool.md` 或包含“全量测试用例”“多个最小标题”“统一生成 Excel”等聚合痕迹的文件。标准批次账本文件 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv`、`selection-option-observations.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv`、`risk-confirmation.csv` 以及 `templates/` 模板目录不会被误判。
`scripts/validate-generated-python-scripts.ps1` 会扫描当前批次 `artifacts/scripts/` 下的 Python、JSON、CSV、Markdown 和 TXT 中间文件，拦截单文件大小超限、JSON 语法错误、Python 语法错误和高风险中文弯引号。单个 Python 建议小于 200KB，单个 JSON/CSV/Markdown/TXT 中间文件建议小于 256KB；超过时必须继续按最小标题路径、页面域或功能块分片，禁止用一个大 Python 或大 JSON 承载大量用例正文。
`scripts/validate-test-design-deliverable.ps1` 会在传入 `-BatchStatusPath` 后校验批次账本、页面实探、归档测试设计、导入文件、产品版图和 artifacts 归属；任一已通过批次缺少归档文件、导入文件、标准表头、页面证据或质量自检数据时都应失败。
