# AI 测试设计规范包架构

本仓库是面向 CodeBuddy/Codex 的测试设计规范包。架构目标是让 AI 在不同入口下读取到一致的约束，并稳定产出可导入、可评审、可执行的测试设计交付物。

## 分层职责

| 层级 | 文件 | 职责 |
| --- | --- | --- |
| 人类入口 | `README.md`、`README_IMPORT.md` | 快速说明项目用途、接入方式和自检命令，不承载完整规则。 |
| AI 入口 | `AGENTS.md`、`CODEBUDDY.md` | Codex 与 CodeBuddy 的项目级记忆，放置高优先级约束和交付边界。 |
| 执行入口 | `.codebuddy/skills/test-design/SKILL.md`、`.codebuddy/.rules/test-design-rule.mdc`、`.codebuddy/rules/test-design-rule.md` | 保持低于 10000 字符，只承载读取路由、硬门禁、流程摘要和校验命令。 |
| 专题规则 | `docs/test-design/rules/` | 按任务类型保存详细规则，包括用例设计、页面实探、批次运行、导入文件、产品版图和数据安全。 |
| 模板契约 | `docs/test-design/excel-template-spec.md`、`docs/test-design/*.xlsx` | 定义 Excel Sheet、字段、枚举、下拉框、导入模板和样式约束。 |
| 客户交付件 | `docs/test-design/current/`、`docs/test-design/deliverables/` | 保存本次任务范围内交付给客户或测试系统的测试设计和导入文件，不包含内部产品全量版图。 |
| 内部测试资产事实 | `docs/test-assets/product-map.xlsx`、`docs/test-assets/modules/`、`docs/test-assets/imports/`、`docs/test-assets/batch-runs/` | 保存产品测试知识图谱、最终测试设计归档、导入文件副本、批次运行状态、模块能力、业务对象、业务链路、跨模块依赖和可复用测试数据。 |
| 升级机制 | `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、`scripts/new-framework-upgrade-package.ps1`、`scripts/upgrade-framework.ps1` | 支持外网生成框架升级包、内网受控应用升级包，并保护内网业务资产。 |
| 自动化校验 | `scripts/validate-test-design.py`、`scripts/validate-test-design.ps1`、`scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1` | 防止模板结构、导入模板下拉框、升级边界和关键规则发生漂移，并校验已生成交付件的覆盖关系、批次状态与产品版图同步。 |
| 运行时与事务保护 | `pyproject.toml`、`requirements.txt`、`scripts/run-test-design.ps1`、`scripts/test_design_excel_tools.py`、`tests/` | 固定 Python/openpyxl 契约，统一选择运行时，并验证批次初始化幂等性、资产同步字段和交付失败回滚。 |

规则归属和精简边界见 `docs/RULE_OWNERSHIP.md`。修改规则时，应先判断规则类型和权威源，再更新摘要引用和校验脚本。

## 关键架构决策

1. 正式测试设计 Excel 只保留 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
2. Skill、Rule、AGENTS、CODEBUDDY 是轻入口，目标低于 10000 字符；详细规则必须拆到 `docs/test-design/rules/`，由入口按任务类型读取，避免 CodeBuddy 加载超过 1 万字后截断或遗漏。
3. 测试系统独立导入文件必须基于 `docs/test-design/测试用例模板.xlsx` 的副本生成，不能修改原模板，也不能手工仿制空白 Sheet。
4. 页面元素覆盖清单只做覆盖追踪，不承载独立测试步骤或完整预期结果。
5. 页面实探允许操作本次创建且带测试标识的数据；已有数据只能查看、搜索、筛选、打开详情或进入编辑页观察，不保存不提交。
   既有数据必须主动只读深探：可以进入详情、编辑页、删除/停用/提交确认弹窗观察字段、联动、二次确认、权限提示和取消路径，但不得最终确认；可以复制既有数据的非敏感字段，改名或改编码为带测试标识的新数据后新增，再用新数据探索后续页面。
6. 用例标题和测试系统导入文件中的测试用例名称必须正式、简洁、可检索，避免口语化，并使用 `功能点-当前用例标题` 格式补偿测试系统缺少独立功能点字段的问题。
7. 测试系统导入文件的 `执行方式` 默认是 `手动`；只有已有可运行、可维护并覆盖用例主要校验点的自动化资产，且本次交付明确按自动化导入或关联资产时，才填写 `自动化`。
8. 客户交付件与内部维护资产必须分离；`docs/test-assets/product-map.xlsx` 是内部产品版图，不作为默认客户交付件。
9. AI 记忆只保存规则和索引入口，具体业务事实必须保存在产品版图和归档测试设计中。
10. 每次生成前读取 `product-map.xlsx` 和用户指定依赖模块的归档测试设计，正式生成前展示产品理解摘要，包含风险项与待确认问题；正式写测试用例前必须先让用户确认风险项与待确认问题，并根据确认结果动态调整测试范围、测试数据、优先级、步骤、预期结果和风险等级；每次生成后回存最终测试设计并更新产品版图。
11. 当用户要求为某个模块生成测试设计时，正式写用例前必须先做模块级粗遍历，识别菜单入口、页面清单、核心功能点、业务对象、状态流转和跨模块依赖；具体写测试用例时，再在对应页面或功能点内做深遍历，有可访问页面时使用浏览器或 computer use 覆盖所有可点击、可输入、可测试元素以及所有可点击/可交互功能点。下拉框、级联选择、单选框、复选框、树选择、枚举筛选等选择类控件不得只展开查看选项，必须分别选择代表性选项并记录 `选项取值/输入值`、`联动/依赖变化`、字段或列表变化、按钮禁用态、校验提示和清空重置结果。输入框、搜索框、文本域、数字框、日期框、URL/地址、端口、邮箱、手机号、名称、编码等输入类控件不得只观察字段存在，必须实际输入正常、异常、边界或用户提供的测试数据，并记录真实提示、后续页面、结果分支/后续状态和可恢复路径。新增、创建、添加、新建、保存、提交、下一步、完成、测试连接等新增类流程必须实填实走，成功时进入详情页、下一级页面或后续配置页继续观察，失败时记录真实失败提示、停留页面和可恢复路径。
12. 每一批测试设计都必须严格执行完整 test-design Skill 和 Rule，不得因为分批而降级；每批都必须覆盖功能测试、性能测试、异常流程、边界值、权限/角色、状态流转、数据一致性、兼容性/稳定性、风险与待确认问题、自动化建议和页面元素覆盖清单。
13. 当任务范围是全产品、大模块或超过一个最小标题时，禁止直接生成完整测试用例，不得一次性生成完整测试用例；必须先建立批次队列，并在 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/` 建立批次运行状态账本，包含 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 和 `artifacts/`。只要发生页面实探或生成 `page-discovery.csv`，即使当前任务只有一个最小标题路径，也必须先执行 `scripts/run-test-design.ps1 init-batch-run` 初始化批次目录；已存在批次使用 `--resume`，禁止无提示覆盖。每批必须按最深可识别标题形成唯一 `最小标题路径`，通过覆盖质量自检并完成资产回存后，才能进入下一批；`batch-status.csv` 必须记录最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、导入文件路径和导入文件已生成；所有批次完成后只做最终汇总、跨模块汇总、回归范围、风险清单和客户总览，不得重新生成各批完整用例。
    `batch-status.csv`、`page-discovery.csv`、`element-case-plan.csv` 和 `test-data-lifecycle.csv` 必须使用标准模板表头，禁止自定义精简表头、增删列或字段错位；这些 CSV 必须通过 CSV writer 或等价结构化方式写入。功能测试用例必须从 `element-case-plan.csv` 派生，真实新增/编辑/删除必须同步 `test-data-lifecycle.csv`；已完成批次在 `batch-plan.md` 中不得仍标记为执行中或待开始，页面清单数量必须与 `batch-status.csv` 页面数一致。
    分批前必须先遍历一级菜单、二级菜单、三级菜单及更深层级，形成菜单轮廓和分批设计计划，并按最深标题级别确定批次；当前批次只处理当前最小标题路径，禁止合并多个最小标题，禁止再拆分一个最小标题；已通过批次的归档测试设计和导入文件路径必须真实存在，并能按最小标题路径逐个匹配校验。
14. 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须走增量补充流程，不得只追加用例。补充任务先读取产品版图、归档测试设计、现有交付件、页面元素覆盖清单、`page-discovery.csv` 和 `batch-status.csv`，识别覆盖缺口、受影响最小标题路径、已有用例 ID 和可复用历史用例，再建立或更新补充批次并重新页面实探目标覆盖缺口。
15. 增量补充和二次补充仍执行完整 Skill 规则，新增用例按小功能块合并到正式测试设计和导入文件副本，能复用已有用例时引用已有用例 ID，不重复复制；补充后同步页面元素覆盖清单、性能测试设计、风险与待确认问题、自动化建议、`docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `product-map.xlsx`。
16. 正式测试设计 Excel 生成后，必须使用 `scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>` 做交付件质量校验；大范围任务还应追加 `-BatchStatusPath <batch-status.csv>` 校验批次状态与交付件一致，并强制读取同级 `page-discovery.csv` 与 `docs/test-assets/product-map.xlsx` 做产品版图同步校验；也可以显式追加 `-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>`，校验页面实探、正式 Excel 和产品版图之间的最小标题路径、页面元素、关联用例、用例资产索引和变更记录是否同步。
17. 测试系统导入文件由统一表头映射链路生成，随批次交付优先使用 `scripts/run-test-design.ps1 complete-deliverables`；只需单独生成导入文件时才使用 `generate-import`。批次临时脚本只能调用统一工具或复用同等映射函数，禁止按固定列序号数组写入模板；生成后必须通过 `-ImportWorkbookPath` 校验字段错位、下拉框、自动字段空值、模板数据验证、多行换行样式和 DFX 标签落地。
18. 测试用例必须尽可能详细，这是架构约束。每个测试点、每个页面元素都必须按 Skill 从主流程、异常、边界、权限、状态、数据一致性、组合条件、禁用态/空状态/错误态、兼容性/稳定性、性能影响、审计/日志/通知、副作用和可恢复路径等不同测试方向展开；禁止用一个笼统用例替代多个可验证方向。
19. 模块或批次正式写测试用例前，必须先完成 DFX 覆盖评估，综合评估 DFX 12 维度 × 4 场景覆盖，权威规则为 `docs/test-design/rules/dfx-test-strategy.md`；评估结论必须明确适用、不适用、待确认和需补充证据的维度，并据此展开异常值、边界值和测试策略，不得只写一句笼统策略。每批至少考虑 DFT、DFB、DFS、DFR、DFU、DFP 的适用场景，涉及接口、兼容、维护、部署、运维、极端压测时追加 DFI、DFC、DFM、DFD、DFO、DFX。
20. 模块级粗遍历、菜单轮廓、页面清单和功能地图不是临时分析结果，必须沉淀到 `product-map.xlsx` 的产品模块地图、页面元素地图、业务对象地图、业务链路地图、模块能力索引、跨模块依赖关系和变更记录，形成对整个项目或模块的长期理解。
    `product-map.xlsx` 的十个 Sheet 都必须沉淀真实产品资产，禁止保留 `示例产品`、`示例模块`、`示例页面` 等模板样例行；`用例资产索引` 必须覆盖正式测试设计中的全部功能用例 ID，`页面元素地图` 必须覆盖正式测试设计中的全部页面元素。
21. 外网到内网升级以脚本升级为主、手动确认兜底；普通框架升级不得覆盖 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
22. `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。`product-map.xlsx` 是主要可能演进的内部资产结构；历史归档 Excel 默认作为历史快照保留，不批量重写。`asset_schema_version` 变化时必须通过迁移脚本增量补齐，不得用空模板覆盖真实资产。
23. `init-batch-run` 对已存在批次默认拒绝覆盖，`--resume` 只读恢复账本，`--force-reinitialize` 必须先备份再重建；`complete-deliverables` 必须先获取 `.test-design-locks/delivery.lock` 项目级排他锁，再预校验并对正式工作簿、导入文件、交付副本、批次账本和产品版图提供失败回滚。Excel、CSV 和 Markdown 的单文件写入使用同目录临时文件加原子替换。
24. 正式测试设计、测试系统导入文件、批次账本、页面实探记录、临时脚本和 `product-map.xlsx` 都不得保留疑似真实密钥、Token、密码或内部敏感凭据；必须使用 `<valid_api_key>`、`<test_token>`、`<test_service_url>` 等占位符。
25. 每次修改规范或模板后必须运行稳定性自检。

## 变更同步规则

- 改交付边界：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`excel-template-spec.md`。
- 改 Excel 字段或枚举：同步模板、`excel-template-spec.md`、自检脚本。
- 改页面实探或测试数据规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule。
- 改导入模板规则：不得直接修改原 `测试用例模板.xlsx`，除非测试系统模板本身发生版本变化。
- 改测试资产归档、批次运行状态或跨模块依赖规则：同步 `docs/test-design/rules/` 对应专题文档、`docs/test-design/archive-and-index-guidelines.md`、`docs/test-assets/batch-runs/README.md`、必要入口摘要和自检脚本。
- 改外网到内网升级机制：同步 `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、升级脚本和自检脚本。
- 改内部资产结构：提升 `asset_schema_version`，补充升级清单和迁移脚本；迁移脚本只能读取旧资产并增量补齐。
- 改规则归属或精简策略：同步 `docs/RULE_OWNERSHIP.md`、`docs/ARCHITECTURE.md` 和自检脚本。

## 发布前检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
git status --short
```

提交信息使用中文，简洁说明本次规范、模板或校验变更。
