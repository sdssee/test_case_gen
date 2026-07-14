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
| 内部测试资产事实 | `docs/test-assets/catalog/`、`docs/test-assets/product-map.xlsx`、`docs/test-assets/modules/`、`docs/test-assets/imports/`、`docs/test-assets/batch-runs/` | catalog 保存按模块 JSON 权威事实，product-map.xlsx 是可重建查询视图，其余目录保存归档、导入副本和批次状态。 |
| 升级机制 | `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、`scripts/new-framework-upgrade-package.ps1`、`scripts/upgrade-framework.ps1` | 支持外网生成框架升级包、内网受控应用升级包，并保护内网业务资产。 |
| 自动化校验 | `scripts/validate-test-design.py`、`scripts/validate-test-design.ps1`、`scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1` | 防止模板结构、导入模板下拉框、升级边界和关键规则发生漂移，并校验已生成交付件的覆盖关系、批次状态与产品版图同步。 |
| 领域实现 | `scripts/test_design/`、`scripts/test_design_excel_tools.py` | 兼容 CLI 只负责编排；batch、formal_assembler、excel_utils、io_utils、paths、fact_store、product_map_sync 分别承载批次、8 Sheet 组装、Excel、事务、路径和事实领域逻辑。 |
| 最终多 Agent 编排 | `scripts/test_design/orchestration/`、`docs/test-design/schemas/orchestration/`、`docs/AGENT_ORCHESTRATION.md` | 以必选确定性状态机发放严格任务，隔离 Agent 写入，校验指纹与契约，按功能点合并、结构化返工、独立 Review，并把交付限制为单写者。 |
| CodeBuddy Agent 适配 | `.codebuddy/agents/`、`.codebuddy/commands/test-design-run.md`、`docs/CODEBUDDY_AGENT_ADAPTER.md` | 注册 5 个认知角色，由主会话把确定性任务路由为串行阶段或冻结的 Case 并行波次；平台不支持并行时无损退化为串行。 |
| 运行时与事务保护 | `pyproject.toml`、`requirements.txt`、`scripts/run-test-design.ps1`、`tests/`、`.github/workflows/validate.yml` | 固定运行时契约，执行跨平台 CI，并验证批次幂等性、事实迁移、并发锁、交付回滚和升级回滚。 |

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
8. 客户交付件与内部维护资产必须分离；`docs/test-assets/catalog/` 和 `product-map.xlsx` 都是内部资产，不作为默认客户交付件。
9. AI 记忆只保存规则和索引入口，具体业务事实必须保存在按模块 JSON catalog 和归档测试设计中，Excel 只作为可重建视图。
10. 每次生成前读取 catalog、`product-map.xlsx` 查询视图和依赖模块归档，正式生成前展示产品理解摘要、风险项与待确认问题；只有全量深探后模型仍不理解的内容需要用户确认，没有则记录 `RISK-NONE`。确认结论动态调整设计，生成后回存最终测试设计并更新产品事实与版图。
11. 页面深探前从 DOM、可访问性树、trace 或控件树按角色/数据状态独立采集 `page-element-inventory.csv`，再按稳定 `交互实例ID` 与 `page-discovery.csv` 双向对账。所有交互实际执行并用当前批次非空证据证明；选择类有限集合逐项操作并写入 `selection-option-observations.csv`，输入、动态选择、分页和弹窗逐分支写入 `interaction-branch-observations.csv`，真实锚点进入各自唯一关联用例。创建必须成功；本次创建对象以同一测试数据 ID 和创建 owner 用例贯穿后续生命周期，各行使用对应 mutation plan 的交互实例 ID。
12. 每一批测试设计都必须严格执行完整 test-design Skill 和 Rule，不得因为分批而降级；每批都必须覆盖功能测试、性能测试、异常流程、边界值、权限/角色、状态流转、数据一致性、兼容性/稳定性、风险与待确认问题、自动化建议和页面元素覆盖清单。
13. 大范围任务建立最小标题批次队列，每个叶子批次使用独立 run-dir、单行状态账本和 `batch-scope.json`；初始化显式保存真实产品名，生成与收口必须复用，避免把一级模块误作产品。每批严格执行 `discovery → plan → risk → cases → review → delivery`：先全量深探和逐修改项生效闭环，plan 通过后才归纳模型不理解项；页面可验证内容由模型自行操作验证并在未完成时退回 discovery，只有真实不理解的外部语义需要用户确认。`pipeline-status` 从实际资产派生下一步，阶段验证报告以文件、模板、证据和验证器哈希失效下游缓存。所有批次完成后只读各批归档做跨模块汇总，不重新生成各批用例。
    `batch-status.csv`、`page-element-inventory.csv`、`page-discovery.csv`、`selection-option-observations.csv`、`interaction-branch-observations.csv`、`element-case-plan.csv`、`test-data-lifecycle.csv` 和 `risk-confirmation.csv` 必须使用标准模板表头，禁止自定义精简表头、增删列或字段错位；这些 CSV 必须通过 CSV writer 或等价结构化方式写入。功能测试用例必须从 `element-case-plan.csv` 派生，真实新增/编辑/删除必须同步 `test-data-lifecycle.csv`；已完成批次在 `batch-plan.md` 中不得仍标记为执行中或待开始，页面清单数量必须与 `batch-status.csv` 页面数一致。
    批次状态数从 discovery、manifest 和明确 DFX taxonomy 派生；异常、边界、权限/状态、数据一致性允许重叠，不得人工估算。
    分批前必须先遍历一级菜单、二级菜单、三级菜单及更深层级，形成菜单轮廓和分批设计计划，并按最深标题级别确定批次；当前批次只处理当前最小标题路径，禁止合并多个最小标题，禁止再拆分一个最小标题；已通过批次的归档测试设计和导入文件路径必须真实存在，并能按最小标题路径逐个匹配校验。
14. 首次交付后的补充、追加、二次补充或页面未覆盖反馈必须走增量补充流程，不得只追加用例。补充任务先读取产品版图、归档测试设计、现有交付件、页面元素覆盖清单、`page-discovery.csv` 和 `batch-status.csv`，识别覆盖缺口、受影响最小标题路径、已有用例 ID 和可复用历史用例，再建立或更新补充批次并重新页面实探目标覆盖缺口。
15. 增量补充和二次补充仍执行完整 Skill 规则，新增用例按小功能块合并到正式测试设计和导入文件副本，能复用已有用例时引用已有用例 ID，不重复复制；补充后同步页面元素覆盖清单、性能测试设计、风险与待确认问题、自动化建议、`docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `product-map.xlsx`。
16. 正式测试设计 Excel 生成后，必须使用 `scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>` 做交付件质量校验；大范围任务还应追加 `-BatchStatusPath <batch-status.csv>` 校验批次状态与交付件一致，并强制读取同级 `page-discovery.csv` 与 `docs/test-assets/product-map.xlsx` 做产品版图同步校验；也可以显式追加 `-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>`，校验页面实探、正式 Excel 和产品版图之间的最小标题路径、页面元素、关联用例、用例资产索引和变更记录是否同步。
17. 测试系统导入文件由统一表头映射链路生成，随批次交付优先使用 `scripts/run-test-design.ps1 complete-deliverables`；只需单独生成导入文件时才使用 `generate-import`。批次临时脚本只能调用统一工具或复用同等映射函数，禁止按固定列序号数组写入模板；生成后必须通过 `-ImportWorkbookPath` 校验字段错位、下拉框、自动字段空值、模板数据验证、多行换行样式和 DFX 标签落地。
18. 测试用例必须尽可能详细，这是架构约束。每个测试点、每个页面元素都必须按 Skill 从主流程、异常、边界、权限、状态、数据一致性、组合条件、禁用态/空状态/错误态、兼容性/稳定性、性能影响、审计/日志/通知、副作用和可恢复路径等不同测试方向展开；禁止用一个笼统用例替代多个可验证方向。
19. 模块或批次正式写测试用例前，必须先完成 DFX 覆盖评估，综合评估 DFX 12 维度 × 4 场景覆盖，权威规则为 `docs/test-design/rules/dfx-test-strategy.md`；评估结论必须明确适用、不适用、待确认和需补充证据的维度，并据此展开异常值、边界值和测试策略，不得只写一句笼统策略。每批至少考虑 DFT、DFB、DFS、DFR、DFU、DFP 的适用场景，涉及接口、兼容、维护、部署、运维、极端压测时追加 DFI、DFC、DFM、DFD、DFO、DFX。
20. 模块级粗遍历、菜单轮廓、页面清单和功能地图不是临时分析结果，必须沉淀到 `catalog/modules/*.json`，再投影到 `product-map.xlsx` 的产品模块地图、页面元素地图、业务对象地图、业务链路地图、模块能力索引、跨模块依赖关系、用例资产索引、可复用测试数据、变更影响分析和变更记录；投影不得保留 `示例产品`、`示例模块`、`示例页面` 等模板行。
21. 外网到内网升级以脚本升级为主、手动确认兜底；普通框架升级不得覆盖 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
22. `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部事实结构版本。2.0.0 起 `catalog/modules/*.json` 是权威事实源，`product-map.xlsx` 是投影视图；历史归档 Excel 继续作为快照保留。结构升级必须通过迁移脚本先保留旧 Excel 真实行，再生成 catalog。
23. `init-batch-run` 对已存在批次默认拒绝覆盖，`--resume` 只读恢复账本，`--force-reinitialize` 必须先备份再重建；`complete-deliverables --run-dir` 必须先通过独立 Review Gate 和批次校验，再由 `formal_assembler` 从 manifest 与按 Sheet JSON 组装正式工作簿，获取 `.test-design-locks/delivery.lock` 项目级排他锁，并对正式工作簿、导入文件、`current/`、`deliverables/`、内部归档、批次账本和产品版图提供失败回滚。编排交付的事实源固定为当前 run-dir 的规范 `batch-status.csv`、`page-discovery.csv` 与项目规范 `docs/test-assets/product-map.xlsx`，拒绝外部覆盖路径绕过 Review。正式持久输出只允许事务覆盖的 5 份 canonical 文件，禁止外部 `--import-workbook` 兼容副本，状态进入 `FINALIZED` 后不得再发生复制副作用。独立 `assemble-formal-workbook` 只能写当前 run-dir 的 `artifacts/previews/`，直接导入生成、样式修复和产品版图同步不得写正式或受保护目录；批次脚本不得直接保存正式 Excel。
24. 正式测试设计、测试系统导入文件、批次账本、页面实探记录、Agent 产物、证据和 `product-map.xlsx` 都不得保留真实 URL/IP、主机、账号、密钥、Token、密码或内部敏感凭据；必须使用占位符。文本全量扫描；二进制证据必须裁剪/遮蔽敏感可见信息并提供相邻、同 SHA256 的可视脱敏审计 sidecar，编排器在接受与 Review 前失败关闭。
25. 每次修改规范或模板后必须运行稳定性自检。
26. Rule 镜像和轻量入口引用由 `docs/test-design/rules/entry-contract.json` 与 `scripts/sync-rule-entrypoints.py` 校验；修改权威 Rule 后使用 `--write` 更新镜像，禁止分别编辑两份 Rule。
27. 3.0.0 直接采用最终多 Agent 架构，不保留可选旧模式。确定性编排器是唯一阶段推进者：Discovery 为单 owner，Plan/DFX 冻结预算与功能点，Risk Arbiter 仅处理真实外部语义，Case Worker 只按精确功能点并行，Reviewer 独立只读，Delivery 单写。AgentTask/AgentResult、冻结输入、隔离 workspace、source fingerprint、逐用例 traceability、追加事件链和结构化返工共同拒绝接纳越界或过期产物、防止旧产物混入和模型自报通过；CodeBuddy 写入前能力隔离由项目最小工具和保护 hook 执行，完整设计以 `docs/AGENT_ORCHESTRATION.md` 与 `docs/CODEBUDDY_AGENT_ADAPTER.md` 为准。
28. 确定性编排器不调用模型；CodeBuddy 主会话是唯一 coordinator，项目级 `.codebuddy/agents/` 只承载 Discovery、Plan/DFX、Risk、Case、Reviewer 五个认知角色。任务执行前必须经 `agent-claim` 原子领取并绑定 `execution_id`，不使用会让 CRUD 重放的自动租约；非 Case 串行，Case 只并行一次 `runnable_tasks` 的冻结波次；无后台并行能力时串行执行仍保持同一门禁和合并语义。当前正式流程只认证 `codebuddy-subagent`；`codebuddy-main-session`、`external-session` 与 Agent Team 只可诊断，枚举存在不代表隔离已认证。无 sub-agent 时正式流程必须阻断，不能用未认证执行器模拟成功。Reviewer 执行身份必须独立于全部成功生成者，Delivery 不建模为 Agent，完整适配以 `docs/CODEBUDDY_AGENT_ADAPTER.md` 为准。

## 变更同步规则

- 改交付边界：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`excel-template-spec.md`。
- 改 Excel 字段或枚举：同步模板、`excel-template-spec.md`、自检脚本。
- 改页面实探或测试数据规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule。
- 改导入模板规则：不得直接修改原 `测试用例模板.xlsx`，除非测试系统模板本身发生版本变化。
- 改测试资产归档、批次运行状态或跨模块依赖规则：同步 `docs/test-design/rules/` 对应专题文档、`docs/test-design/archive-and-index-guidelines.md`、`docs/test-assets/batch-runs/README.md`、必要入口摘要和自检脚本。
- 改外网到内网升级机制：同步 `VERSION`、`UPGRADE_MANIFEST.md`、`docs/UPGRADE.md`、升级脚本和自检脚本。
- 改内部资产结构：提升 `asset_schema_version`，补充升级清单和迁移脚本；迁移脚本只能读取旧资产并增量补齐。
- 改规则归属或精简策略：同步 `docs/RULE_OWNERSHIP.md`、`docs/ARCHITECTURE.md` 和自检脚本。
- 改多 Agent 角色、契约、状态、返工或 Review：同步 `docs/AGENT_ORCHESTRATION.md`、orchestration schema、专题规则、CLI、自检与测试；专题质量门禁仍保持权威，入口只同步 Gate 摘要。
- 改 CodeBuddy Agent 注册、权限边界、主会话调度或平台降级：同步 `.codebuddy/agents/`、`.codebuddy/commands/test-design-run.md`、`.codebuddy/settings.json`、`.codebuddy/hooks/guard-agent-tool.py`、`docs/CODEBUDDY_AGENT_ADAPTER.md`、导入说明、自检与测试，不得另建第二套状态机或 Delivery Agent。

## 发布前检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
git status --short
```

提交信息使用中文，简洁说明本次规范、模板或校验变更。
