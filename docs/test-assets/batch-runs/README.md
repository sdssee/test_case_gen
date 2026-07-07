# 批次运行状态账本

`docs/test-assets/batch-runs/` 是内部任务级执行状态目录，用于记录大范围测试设计任务的分批计划、批次状态、质量门禁和最终复盘。该目录不作为默认客户交付件，客户交付件仍放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。

## 适用范围

当任务范围属于全产品、多个一级模块、某个大模块，或超过一个最小标题时，必须创建本次任务的批次运行目录：

```text
docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/
  batch-plan.md
  batch-status.csv
  batch-review.md
  page-discovery.csv
  artifacts/
```

## 文件职责

- `batch-plan.md`：记录菜单轮廓、页面清单、功能地图、拆分维度、依赖批次和预计交付物。
- `batch-status.csv`：记录每个批次的执行状态、最小标题路径、覆盖数量、用例数量和覆盖质量自检结果，是进入下一批的门禁文件。
- `batch-review.md`：记录所有批次完成后的跨模块汇总、回归范围、风险与待确认问题。
- `page-discovery.csv`：记录页面或功能点实探证据，包括最小标题路径、页面入口、元素、交互方式、选择类控件的选项取值/输入值、联动/依赖变化、输入类控件的实际输入、结果分支/后续状态、完整点击路径、观察行为、覆盖状态和关联用例 ID。
- `artifacts/`：保存本次批次执行过程中产生的中间截图、页面遍历笔记、临时导出或核对材料；证据只能放在当前任务目录下，禁止写入共享的 `docs/test-assets/batch-runs/artifacts/` 根目录。

## 执行规则

1. 建立批次运行目录时，应优先复制 `templates/` 中的模板文件。
2. 每个批次正式写测试用例前，必须先完成当前批次最小标题路径下的页面或功能点遍历。
3. 每个批次正式写测试用例前，必须把当前批次最小标题路径和页面或功能点实探结果写入 `page-discovery.csv`。
   `batch-status.csv` 和 `page-discovery.csv` 必须复制 `templates/` 中的标准模板或保持与模板完全一致的表头，禁止自定义精简表头、增删列或在末尾追加汇总行。
   `page-discovery.csv` 必须使用 CSV writer 或等价结构化写入方式生成，每一行列数必须与表头一致，禁止手工拼接导致字段错位。
   下拉框、级联选择、单选框、复选框、树选择、枚举筛选等选择类控件不得只展开查看选项，必须选择代表性选项并记录 `选项取值/输入值` 和 `联动/依赖变化`。
   输入框、搜索框、文本域、数字框、日期框、URL/地址、端口、邮箱、手机号、名称、编码等输入类控件不得只观察字段存在，必须实际输入测试数据并记录真实提示、`选项取值/输入值`、`预期/观察行为` 和 `结果分支/后续状态`。
   新增、创建、添加、新建、保存、提交、下一步、完成、测试连接等新增类流程必须实填实走；成功时进入详情页、下一级页面或后续配置页继续观察，失败时记录真实失败提示、停留页面和可恢复路径。
   既有数据必须主动只读深探：进入详情、编辑页、删除/停用/提交确认弹窗观察字段、联动、二次确认、权限提示和取消路径，但不得最终确认；可以复制既有数据的非敏感字段，改名或改编码为带测试标识的新数据后新增。
4. 每个批次完成后，必须更新 `batch-status.csv`，并填写最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、页面遍历完成、功能用例完成、性能设计完成、异常边界权限覆盖完成、页面元素覆盖完成、产品版图已更新、导入文件路径、导入文件已生成和覆盖质量自检。
   已完成批次在 `batch-plan.md` 中不得仍标记为执行中或待开始；`batch-plan.md` 的页面清单数量必须与 `batch-status.csv` 的页面数一致。
   `product-map.xlsx` 的十个 Sheet 都必须沉淀真实产品资产，禁止保留 `示例产品`、`示例模块`、`示例页面` 等模板样例行；其中 `用例资产索引` 必须覆盖正式测试设计中的全部功能用例 ID，`页面元素地图` 必须覆盖正式测试设计中的全部页面元素。
5. 已覆盖元素数、功能用例数、性能场景数和各测试维度数量必须能从正式测试设计 Excel 或页面元素覆盖清单中追溯；不能只填写主观完成状态。
6. 当前批次的覆盖质量自检通过后，才能进入下一批。
7. 禁止创建承载全量测试用例正文的单一中间文件，例如单个 Python、JSON、CSV、Markdown 或临时脚本文件；脚本只能用于当前批次的模板填充、格式转换或校验，并保存到本任务 artifacts/scripts/，不得把多个最小标题路径、多个批次或全产品测试用例先集中写入一个文件后再统一生成 Excel。
   如确需生成当前批次 Python 临时脚本，写入中文文本、菜单路径、测试步骤、预期结果或 JSON 数据时，必须使用 `repr()`、`json.dumps(..., ensure_ascii=False)` 或结构化数据文件读取，禁止手工拼接包含中文弯引号、智能引号或未转义双引号的字符串字面量。执行前必须运行 `scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>`，通过语法编译和高风险引号扫描后才能执行。
   正式测试设计、导入文件、`batch-plan.md`、`batch-status.csv`、`page-discovery.csv`、`batch-review.md`、临时脚本和 `product-map.xlsx` 都不得保留真实环境 URL/IP、真实账号、真实密码、疑似真实密钥、Token 或内部敏感凭据；必须改写为 `<product_login_url>`、`<test_env_base_url>`、`<test_user_account>`、`<test_user_password>`、`<valid_api_key>`、`<test_token>`、`<test_service_url>` 等占位符。
8. 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须建立或更新补充批次，不得只追加用例；补充前读取产品版图、归档测试设计、现有交付件、页面元素覆盖清单、`page-discovery.csv` 和 `batch-status.csv`，识别覆盖缺口、受影响最小标题路径、已有用例 ID 和可复用历史用例。
9. 增量补充必须按最小标题路径重新页面实探目标覆盖缺口，新增用例放在对应小功能块附近，能复用已有用例时引用已有用例 ID，不重复复制；二次补充完成后同步正式测试设计、独立导入文件副本、页面元素覆盖清单、性能测试设计、风险与待确认问题、自动化建议、`docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `product-map.xlsx`。
10. 最终汇总只引用已归档批次成果和用例 ID，不得重新生成各批完整用例。

## 运行期门禁

`scripts/validate-test-design.ps1` 会扫描 `docs/test-assets/batch-runs/`、`docs/test-design/current/` 和 `docs/test-design/deliverables/`，拦截疑似承载全量测试用例正文的单一中间文件，例如 `all_cases.py`、`full_product_cases.json`、`merged_cases.csv`、`case_pool.md` 或包含“全量测试用例”“多个最小标题”“统一生成 Excel”等聚合痕迹的文件。标准批次账本文件 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 以及 `templates/` 模板目录不会被误判。
`scripts/validate-test-design-deliverable.ps1` 会在传入 `-BatchStatusPath` 后校验批次账本、页面实探、归档测试设计、导入文件、产品版图和 artifacts 归属；任一已通过批次缺少归档文件、导入文件、标准表头、页面证据或质量自检数据时都应失败。
