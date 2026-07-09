# CodeBuddy Rule：测试设计硬性门禁

本文件是轻量硬规则入口。详细规则按任务类型读取 `docs/test-design/rules/`，不得把专题规则正文复制回本入口。

## 必须读取

- 所有任务：`docs/test-design/rules/case-design.md`、`excel-deliverable.md`、`data-safety.md`、`docs/test-design/rules/dfx-test-strategy.md`、`docs/test-design/excel-template-spec.md`。
- 页面/截图/原型/浏览器/computer use：追加 `docs/test-design/rules/page-discovery.md`。
- 全产品/大模块/多菜单/超过一个最小标题：追加 `docs/test-design/rules/batch-run.md`。
- 测试系统导入：追加 `docs/test-design/rules/import-template.md`。
- 跨模块依赖/历史归档/补充任务/资产同步：追加 `docs/test-design/rules/product-map-sync.md`、`docs/test-design/archive-and-index-guidelines.md`。

## 最高优先级规则

1. 正式测试设计交付物必须是 Excel，且只包含 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
2. 任何测试设计都必须包含性能测试内容；页面元素覆盖清单只是追踪矩阵，不是测试用例 Sheet。
3. 敏捷用户故事每条至少 10 条功能测试用例，不得同质化凑数。
4. `前置条件`、`操作步骤`、`预期结果` 必须编号换行；`操作步骤` 必须从系统或项目入口开始写完整导航路径。
5. `用例标题` 和导入文件 `测试用例名称` 必须使用 `功能点-当前用例标题` 格式，正式、简洁、可检索。
6. 页面、截图、原型或可访问地址必须覆盖所有可点击/可交互功能；每个元素必须关联用例 ID、不适用、不测范围或待确认问题。
7. 页面业务逻辑必须结合需求、设计文档、接口文档和验收标准；页面实探用于补齐入口、控件、路径和真实状态。
8. 页面已有数据只能查看、搜索、筛选、排序、分页、详情、编辑页观察和危险操作确认弹窗观察，不得保存、提交、最终确认或改变状态。
9. 只能对本次创建且带 `AI_TEST`、`CODEX_TEST`、日期或任务编号的数据执行新增、编辑、删除、启停、审批、发布、外部调用等敏感操作。
10. 用户提供测试数据必须优先用于实探和用例设计；最终输出中的敏感数据必须脱敏或替换为占位符。
11. 选择类控件不得只展开查看，必须选择代表性选项并记录 `选项取值/输入值` 与 `联动/依赖变化`。
12. 输入类控件不得只观察字段存在，必须实际输入正常、异常、边界或用户提供数据，并记录真实提示、结果分支/后续状态和可恢复路径。
13. 新增类流程必须实填实走；成功进入详情页、下一级页面或后续配置页继续观察，失败记录真实失败提示、停留页面和可恢复路径。
14. 弹窗、下拉、输入、编辑、删除确认、新增变量等交互必须写到确认、取消、关闭、返回或数据不变的闭环。
15. 生成或补充前必须读取 `docs/test-assets/product-map.xlsx`；不得依赖 AI 对话记忆判断已有模块能力、已有用例或跨模块依赖。
16. 正式生成前必须展示产品理解摘要或模块理解摘要，包含风险项与待确认问题；正式写测试用例前必须先让用户确认，并根据确认结果动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级。
17. 范围超过一个最小标题时，必须按最深标题级别建立批次队列，逐个最小标题路径执行；禁止合并多个最小标题，禁止再拆分一个最小标题。
18. 每一批都必须完整覆盖功能测试、性能测试、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素覆盖清单，不得因为分批而降级。
19. 异常值、边界值和测试策略必须按 `docs/test-design/rules/dfx-test-strategy.md` 的 DFX 12 维度 × 4 场景矩阵落地，不得只写一句笼统策略。
20. 只要发生页面实探或生成 `page-discovery.csv`，必须先执行 `scripts/test_design_excel_tools.py init-batch-run` 初始化批次目录，并保留 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/` 五件套。
21. `batch-status.csv` 和 `page-discovery.csv` 必须使用标准模板表头，禁止自定义精简表头；`page-discovery.csv` 必须结构化写入，防止字段错位。
22. 禁止创建承载全量测试用例正文的单一 Python/JSON/CSV/Markdown/临时脚本；脚本只能处理当前批次并放在 `artifacts/scripts/`。
23. 当前批次 Python/JSON/CSV/Markdown/TXT 中间文件必须小分片，Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；禁止用一个大 Python 或大 JSON 承载大量用例正文。
24. 批次截图、临时脚本和证据必须放在当前任务 `docs/test-assets/batch-runs/<task>/artifacts/`，不得写入共享根目录 artifacts。
25. 批次交付收口优先使用 `scripts/test_design_excel_tools.py finalize-deliverables`，同步 current、deliverables、modules、imports 和 `batch-status.csv` 路径；传入 `--page-discovery` 时必须同时传入 `--batch-status`。
26. 当前批次 Python 临时脚本必须使用 `repr()`、`json.dumps(..., ensure_ascii=False)` 或结构化数据文件写入中文文本，执行前运行生成脚本预检，检查单文件大小、JSON 语法、Python 语法和中文弯引号风险。
27. 测试系统导入文件必须复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，优先使用 `scripts/test_design_excel_tools.py generate-import`，保留下拉框、必填样式、标红字段和自动生成字段空值。
28. 正式测试设计和导入文件只能填充内容；新增数据行必须沿用模板第 2 行示例数据格式，保留边框、字体、填充、对齐、数字格式和下拉验证范围。
29. 导入文件 `执行方式` 默认 `手动`；只有已有可运行、可维护且覆盖主要校验点的自动化资产，并且本次明确按自动化导入或关联资产时，才允许填写 `自动化`。
30. 正式交付件、导入文件、批次账本、页面实探记录、临时脚本和产品版图不得写入真实环境 URL/IP、真实账号、真实密钥、Token、密码或内部敏感凭据；使用 `<product_login_url>` 等占位符。
31. 生成正式测试设计后必须运行 `scripts/validate-test-design-deliverable.ps1`；有批次、页面实探、产品版图或导入文件时必须追加对应参数。
32. 外网到内网普通框架升级必须保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
