# AI 测试设计规范包架构

本仓库是面向 CodeBuddy/Codex 的测试设计规范包。架构目标是让 AI 在不同入口下读取到一致的约束，并稳定产出可导入、可评审、可执行的测试设计交付物。

## 分层职责

| 层级 | 文件 | 职责 |
| --- | --- | --- |
| 人类入口 | `README.md`、`README_IMPORT.md` | 快速说明项目用途、接入方式和自检命令，不承载完整规则。 |
| AI 入口 | `AGENTS.md`、`CODEBUDDY.md` | Codex 与 CodeBuddy 的项目级记忆，放置高优先级约束和交付边界。 |
| 执行规则 | `.codebuddy/skills/test-design/SKILL.md`、`.codebuddy/.rules/test-design-rule.mdc`、`.codebuddy/rules/test-design-rule.md` | 指导 AI 完成输入识别、页面实探、用例设计、导入文件生成和自检。 |
| 模板契约 | `docs/test-design/excel-template-spec.md`、`docs/test-design/*.xlsx` | 定义 Excel Sheet、字段、枚举、下拉框、导入模板和样式约束。 |
| 客户交付件 | `docs/test-design/current/`、`docs/test-design/deliverables/` | 保存本次任务范围内交付给客户或测试系统的测试设计和导入文件，不包含内部产品全量版图。 |
| 内部测试资产事实 | `docs/test-assets/product-map.xlsx`、`docs/test-assets/modules/`、`docs/test-assets/imports/`、`docs/test-assets/batch-runs/` | 保存产品测试知识图谱、最终测试设计归档、导入文件副本、批次运行状态、模块能力、业务对象、业务链路、跨模块依赖和可复用测试数据。 |
| 升级机制 | `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、`scripts/new-framework-upgrade-package.ps1`、`scripts/upgrade-framework.ps1` | 支持外网生成框架升级包、内网受控应用升级包，并保护内网业务资产。 |
| 自动化校验 | `scripts/validate-test-design.py`、`scripts/validate-test-design.ps1`、`scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1` | 防止模板结构、导入模板下拉框、升级边界和关键规则发生漂移，并校验已生成交付件的覆盖关系、批次状态与产品版图同步。 |

规则归属和精简边界见 `docs/RULE_OWNERSHIP.md`。修改规则时，应先判断规则类型和权威源，再更新摘要引用和校验脚本。

## 关键架构决策

1. 正式测试设计 Excel 只保留 8 个标准 Sheet，不新增 `测试系统导入用例` Sheet。
2. 测试系统独立导入文件必须基于 `docs/test-design/测试用例模板.xlsx` 的副本生成，不能修改原模板，也不能手工仿制空白 Sheet。
3. 页面元素覆盖清单只做覆盖追踪，不承载独立测试步骤或完整预期结果。
4. 页面实探允许操作本次创建且带测试标识的数据；已有数据只能查看、搜索、筛选、打开详情或进入编辑页观察，不保存不提交。
   既有数据必须主动只读深探：可以进入详情、编辑页、删除/停用/提交确认弹窗观察字段、联动、二次确认、权限提示和取消路径，但不得最终确认；可以复制既有数据的非敏感字段，改名或改编码为带测试标识的新数据后新增，再用新数据探索后续页面。
5. 用例标题和测试系统导入文件中的测试用例名称必须正式、简洁、可检索，避免口语化，并使用 `功能点-当前用例标题` 格式补偿测试系统缺少独立功能点字段的问题。
6. 测试系统导入文件的 `执行方式` 默认是 `手动`；只有已有可运行、可维护并覆盖用例主要校验点的自动化资产，且本次交付明确按自动化导入或关联资产时，才填写 `自动化`。
7. 客户交付件与内部维护资产必须分离；`docs/test-assets/product-map.xlsx` 是内部产品版图，不作为默认客户交付件。
8. AI 记忆只保存规则和索引入口，具体业务事实必须保存在产品版图和归档测试设计中。
9. 每次生成前读取 `product-map.xlsx` 和用户指定依赖模块的归档测试设计，正式生成前展示产品理解摘要；每次生成后回存最终测试设计并更新产品版图。
10. 当用户要求为某个模块生成测试设计时，正式写用例前必须先做模块级粗遍历，识别菜单入口、页面清单、核心功能点、业务对象、状态流转和跨模块依赖；具体写测试用例时，再在对应页面或功能点内做深遍历，覆盖所有可点击、可输入、可测试元素。下拉框、级联选择、单选框、复选框、树选择、枚举筛选等选择类控件不得只展开查看选项，必须分别选择代表性选项并记录选项取值、级联/依赖变化、字段或列表变化、按钮禁用态、校验提示和清空重置结果。输入框、搜索框、文本域、数字框、日期框、URL/地址、端口、邮箱、手机号、名称、编码等输入类控件不得只观察字段存在，必须实际输入正常、异常、边界或用户提供的测试数据，并记录真实提示、后续页面、结果分支/后续状态和可恢复路径。新增、创建、添加、新建、保存、提交、下一步、完成、测试连接等新增类流程必须实填实走，成功时进入详情页、下一级页面或后续配置页继续观察，失败时记录真实失败提示、停留页面和可恢复路径。
12. 每一批测试设计都必须严格执行完整 test-design Skill 和 Rule，不得因为分批而降级；每批都必须覆盖功能测试、性能测试、异常流程、边界值、权限/角色、状态流转、数据一致性、兼容性/稳定性、风险与待确认问题、自动化建议和页面元素覆盖清单。
13. 当任务范围超过一个最小标题时，禁止直接生成完整测试用例；必须先建立批次队列，并在 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/` 建立批次运行状态账本，包含 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/`。每批必须按最深可识别标题形成唯一 `最小标题路径`，通过覆盖质量自检并完成资产回存后，才能进入下一批；`batch-status.csv` 必须记录最小标题路径、页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、导入文件路径和导入文件已生成；所有批次完成后只做最终汇总、跨模块汇总、回归范围、风险清单和客户总览，不得重新生成各批完整用例。
14. 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须走增量补充流程，不得只追加用例。补充任务先读取产品版图、归档测试设计、现有交付件、页面元素覆盖清单、`page-discovery.csv` 和 `batch-status.csv`，识别覆盖缺口、受影响最小标题路径、已有用例 ID 和可复用历史用例，再建立或更新补充批次并重新页面实探目标覆盖缺口。
15. 增量补充和二次补充仍执行完整 Skill 规则，新增用例按小功能块合并到正式测试设计和导入文件副本，能复用已有用例时引用已有用例 ID，不重复复制；补充后同步页面元素覆盖清单、性能测试设计、风险与待确认问题、自动化建议、`docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `product-map.xlsx`。
16. 正式测试设计 Excel 生成后，必须使用 `scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>` 做交付件质量校验；大范围任务还应追加 `-BatchStatusPath <batch-status.csv>` 校验批次状态与交付件一致，并强制读取同级 `page-discovery.csv` 与 `docs/test-assets/product-map.xlsx` 做产品版图同步校验；也可以显式追加 `-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>`，校验页面实探、正式 Excel 和产品版图之间的最小标题路径、页面元素、关联用例、用例资产索引和变更记录是否同步。
17. 测试系统导入文件由统一表头映射链路生成，优先使用 `scripts/test_design_excel_tools.py generate-import`。批次临时脚本只能调用统一工具或复用同等映射函数，禁止按固定列序号数组写入模板；生成后必须通过 `-ImportWorkbookPath` 校验字段错位、下拉框、自动字段空值、模板数据验证和多行换行样式。
18. 测试用例必须尽可能详细，这是架构约束。每个测试点、每个页面元素都必须按 Skill 从主流程、异常、边界、权限、状态、数据一致性、组合条件、禁用态/空状态/错误态、兼容性/稳定性、性能影响、审计/日志/通知、副作用和可恢复路径等不同测试方向展开；禁止用一个笼统用例替代多个可验证方向。
19. 模块级粗遍历、菜单轮廓、页面清单和功能地图不是临时分析结果，必须沉淀到 `product-map.xlsx` 的产品模块地图、页面元素地图、业务对象地图、业务链路地图、模块能力索引、跨模块依赖关系和变更记录，形成对整个项目或模块的长期理解。
20. 外网到内网升级以脚本升级为主、手动确认兜底；普通框架升级不得覆盖 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
21. `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。`product-map.xlsx` 是主要可能演进的内部资产结构；历史归档 Excel 默认作为历史快照保留，不批量重写。`asset_schema_version` 变化时必须通过迁移脚本增量补齐，不得用空模板覆盖真实资产。
22. 每次修改规范或模板后必须运行稳定性自检。

## 变更同步规则

- 改交付边界：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`excel-template-spec.md`。
- 改 Excel 字段或枚举：同步模板、`excel-template-spec.md`、自检脚本。
- 改页面实探或测试数据规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule。
- 改导入模板规则：不得直接修改原 `测试用例模板.xlsx`，除非测试系统模板本身发生版本变化。
- 改测试资产归档、批次运行状态或跨模块依赖规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`docs/test-design/archive-and-index-guidelines.md`、`docs/test-assets/batch-runs/README.md` 和自检脚本。
- 改外网到内网升级机制：同步 `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、升级脚本和自检脚本。
- 改内部资产结构：提升 `asset_schema_version`，补充升级清单和迁移脚本；迁移脚本只能读取旧资产并增量补齐。
- 改规则归属或精简策略：同步 `docs/RULE_OWNERSHIP.md`、`docs/ARCHITECTURE.md` 和自检脚本。

## 发布前检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
git status --short
```

提交信息使用中文，简洁说明本次规范、模板或校验变更。
- 分批必须按当前产品或模块可识别的最深标题级别执行，例如一级标题、二级标题、三级标题、四级标题等，哪个标题级别最小就以哪个最小标题作为一个批次。每个已通过批次只能覆盖 1 个最小标题路径，`最小标题路径` 使用 `一级>二级>三级>四级` 形式记录唯一叶子节点；禁止合并多个最小标题，禁止再拆分一个最小标题为多个批次。已通过批次的导入文件路径必须真实存在，并能与归档测试设计逐个匹配校验。
- 当任务范围是全产品、多个一级模块或大模块时，必须先遍历一级菜单、二级菜单、三级菜单及更深层标题，拿到菜单轮廓、页面清单和功能地图后输出分批设计计划，不得一次性生成完整测试用例；各批按最小标题路径执行，所有批次完成后再做跨模块汇总。
- 当任务范围超过一个最小标题时，必须建立批次队列并按最小标题路径逐批执行。
- 每个当前批次正式写测试用例前，如有可访问页面、原型或桌面窗口，必须使用浏览器或 computer use 遍历该最小标题路径下所有可点击/可交互功能点。
- `page-discovery.csv` 必须记录选择类控件的 `选项取值/输入值` 和 `联动/依赖变化`；已生成用例的选择类控件不得缺少这两类证据。
- `page-discovery.csv` 必须记录输入类控件的 `选项取值/输入值`、`预期/观察行为` 和 `结果分支/后续状态`；已生成用例的输入类控件不得缺少实际输入和真实提示证据。
- `page-discovery.csv` 必须记录新增类流程的提交数据、成功/失败结果、进入的下一级页面或失败后的停留状态；已生成用例的新增类流程不得缺少下一级页面或失败状态证据。
