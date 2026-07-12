# Codex Project Instructions

本仓库是测试设计规范与交付工具包。Codex 只把本文件作为轻量路由入口，不重复加载 `CODEBUDDY.md` 或 `.codebuddy/.rules/test-design-rule.mdc`。

## 读取路由

1. 读取 `.codebuddy/skills/test-design/SKILL.md` 和 `.codebuddy/rules/test-design-rule.md`。
2. 按阶段读取 `docs/test-design/rules/README.md` 指向的专题规则：页面实探读 `page-discovery.md`，大范围批次读 `batch-run.md`，DFX 计划读 `dfx-test-strategy.md`，交付时再读 Excel 与归档规则。
3. 测试事实读取 `docs/test-assets/catalog/index.json`、相关模块 JSON；`product-map.xlsx` 只是可重建查询视图。

<!-- TEST-DESIGN-GENERATED:BEGIN -->
- [TD-GATE-DELIVERY] 正式测试设计只含 8 个标准 Sheet；测试系统导入文件必须由独立模板副本生成。
- [TD-GATE-FULL-DISCOVERY] 页面默认全量深探；选择类控件不得只展开，有限集合必须覆盖每个选项：每个可选项实际选择，真实禁用项尝试并记录阻塞证据，全部写入 `selection-option-observations.csv`，同步 `选项取值/输入值` 与 `联动/依赖变化`；风险确认不是是否深探的开关。
- [TD-GATE-CRUD-EFFECT] 创建必须成功；编辑/修改项必须逐项执行并验证持久化回显和实际生效。
- [TD-GATE-DATA-SAFETY] 既有数据只读；变更只作用于本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供的数据。
- [TD-GATE-RISK-UNCERTAINTY] 页面可验证内容由模型自行操作验证并在未完成时退回 discovery；仅把模型仍不理解的外部语义交给用户确认；风险只阻塞 risk/cases，不阻塞 plan。
- [TD-GATE-DFX] 先建立元素与交互骨架，再按 `docs/test-design/rules/dfx-test-strategy.md` 完成 DFX 12×4 评估和扩展；性能规格测试和 DFP性能不进入功能用例。
- [TD-GATE-LEAF-BATCH] 超过一个最小标题时逐最深标题分批，不合并、不再拆分；每个最小标题使用独立 run-dir，禁止在同一账本和 manifest 混装多个批次。
- [TD-GATE-PHASES] 批次严格按 discovery → plan → risk → cases → delivery 累积门禁执行。
- [TD-GATE-SHARDS] 新一轮先清旧产物；功能用例每 10 条写入 `artifacts/data/function_cases_part_001.json` 等分片，`function_cases_manifest.json` 是唯一读取源。
- [TD-GATE-ASSEMBLY] 正式 Excel 只能由标准组装器生成，并由 `complete-deliverables` 一站式收口。
- [TD-GATE-ASSET-FACTS] `catalog/modules/*.json` 是产品事实源，`product-map.xlsx` 是查询投影。
- [TD-GATE-SENSITIVE-DATA] 交付件、账本、证据和产品资产不得保留真实 URL/IP、域名/主机名、账号、密钥、Token 或密码。
- [TD-GATE-PROTECTED-ASSETS] 框架升级必须保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
- [TD-GATE-CASE-QUALITY] 前置条件、操作步骤、预期结果编号换行并从系统入口完整导航；标题保持“功能点-当前用例标题”，不同用例不得复用相同“操作步骤+预期结果”，标题参数必须在步骤和预期中落地；JSON、正式表、导入表逐字段一致；临时交互必须闭环。
<!-- TEST-DESIGN-GENERATED:END -->

## 执行入口

- 初始化：`scripts/run-test-design.ps1 init-batch-run ...`
- 下一步判断：`scripts/run-test-design.ps1 pipeline-status --run-dir <run-dir>`
- 阶段校验：`scripts/run-test-design.ps1 validate-batch-artifacts --phase discovery|plan|risk|cases ...`
- 快速自检：`scripts/validate-test-design.ps1 -Mode Fast`
- 完整自检与交付：`scripts/validate-test-design.ps1 -Mode Full`、`complete-deliverables`

## Git

- 修改后检查 `git status`；验证通过后默认提交并推送当前分支。
- GitHub 提交信息必须使用中文。

<!-- LOCAL-OVERRIDES:BEGIN -->
<!-- 业务项目可以在本区块追加本地约束；同步脚本不得覆盖。 -->
<!-- LOCAL-OVERRIDES:END -->
