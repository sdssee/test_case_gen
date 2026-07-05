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
5. 用例标题和测试系统导入文件中的测试用例名称必须正式、简洁、可检索，避免口语化，并使用 `功能点-当前用例标题` 格式补偿测试系统缺少独立功能点字段的问题。
6. 测试系统导入文件的 `执行方式` 默认是 `手动`；只有已有可运行、可维护并覆盖用例主要校验点的自动化资产，且本次交付明确按自动化导入或关联资产时，才填写 `自动化`。
7. 客户交付件与内部维护资产必须分离；`docs/test-assets/product-map.xlsx` 是内部产品版图，不作为默认客户交付件。
8. AI 记忆只保存规则和索引入口，具体业务事实必须保存在产品版图和归档测试设计中。
9. 每次生成前读取 `product-map.xlsx` 和用户指定依赖模块的归档测试设计，正式生成前展示产品理解摘要；每次生成后回存最终测试设计并更新产品版图。
10. 当用户要求为某个模块生成测试设计时，正式写用例前必须先做模块级粗遍历，识别菜单入口、页面清单、核心功能点、业务对象、状态流转和跨模块依赖；具体写测试用例时，再在对应页面或功能点内做深遍历，覆盖所有可点击、可输入、可测试元素。
11. 当用户要求对全产品、多个一级模块或某个大模块进行测试设计时，必须先遍历所有可见一级菜单和二级菜单，必要时继续展开三级菜单，拿到产品或大模块的菜单轮廓、页面清单和功能地图，再输出分批设计计划，不得一次性生成完整测试用例；分批默认按一级模块下的二级菜单执行，二级菜单过大时按三级菜单、页面域或小功能块继续拆分，二级菜单过小时可与同一一级模块下相邻二级菜单合并，跨二级菜单强依赖时按业务对象或业务链路合并成批。每个批次正式写测试用例前，如有可访问页面、原型或桌面窗口，必须使用浏览器能力或 computer use 遍历当前批次所有可点击/可交互功能点；所有批次完成后再做跨模块汇总。
12. 每一批测试设计都必须严格执行完整 test-design Skill 和 Rule，不得因为分批而降级；每批都必须覆盖功能测试、性能测试、异常流程、边界值、权限/角色、状态流转、数据一致性、兼容性/稳定性、风险与待确认问题、自动化建议和页面元素覆盖清单。
13. 当任务范围超过一个二级菜单时，禁止直接生成完整测试用例；必须先建立批次队列，并在 `docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/` 建立批次运行状态账本，包含 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 和 `artifacts/`。每批通过覆盖质量自检并完成资产回存后，才能进入下一批；`batch-status.csv` 必须记录页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数和数据一致性用例数；所有批次完成后只做最终汇总、跨模块汇总、回归范围、风险清单和客户总览，不得重新生成各批完整用例。
14. 正式测试设计 Excel 生成后，必须使用 `scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>` 做交付件质量校验；大范围任务还应追加 `-BatchStatusPath <batch-status.csv>` 校验批次状态与交付件一致；如果 `batch-status.csv` 同级存在 `page-discovery.csv`，脚本会自动使用 `docs/test-assets/product-map.xlsx` 启用产品版图同步校验；也可以显式追加 `-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>`，校验页面实探、正式 Excel 和产品版图之间的页面元素、关联用例、用例资产索引和变更记录是否同步。
15. 测试用例必须尽可能详细，这是架构约束。每个测试点、每个页面元素都必须按 Skill 从主流程、异常、边界、权限、状态、数据一致性、组合条件、禁用态/空状态/错误态、兼容性/稳定性、性能影响、审计/日志/通知、副作用和可恢复路径等不同测试方向展开；禁止用一个笼统用例替代多个可验证方向。
16. 模块级粗遍历、菜单轮廓、页面清单和功能地图不是临时分析结果，必须沉淀到 `product-map.xlsx` 的产品模块地图、页面元素地图、业务对象地图、业务链路地图、模块能力索引、跨模块依赖关系和变更记录，形成对整个项目或模块的长期理解。
17. 外网到内网升级以脚本升级为主、手动确认兜底；普通框架升级不得覆盖 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
18. `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。`product-map.xlsx` 是主要可能演进的内部资产结构；历史归档 Excel 默认作为历史快照保留，不批量重写。`asset_schema_version` 变化时必须通过迁移脚本增量补齐，不得用空模板覆盖真实资产。
19. 每次修改规范或模板后必须运行稳定性自检。

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
