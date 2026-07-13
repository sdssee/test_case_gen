# 测试设计硬性门禁

`.codebuddy/rules/test-design-rule.md` 是唯一 Rule 权威源；当前文件位于 `.mdc` 路径时只是自动生成镜像。详细做法按 `docs/test-design/rules/README.md` 路由读取。

- [TD-GATE-DELIVERY] 正式测试设计只含 8 个标准 Sheet；测试系统导入文件必须由独立模板副本生成。
- [TD-GATE-FULL-DISCOVERY] 默认全量深探；按角色和数据状态建独立元素清单，以交互实例 ID 双向对账；实际执行并引用 artifacts 内非空证据文件和定位，静态截图改名不复用；有限集合每项实际选择，观察结果锚点进入预期；数据不足停留 discovery，风险确认不是豁免。
- [TD-GATE-CRUD-EFFECT] 创建必须成功；修改项逐项验证持久化回显和实际生效；本次创建对象以同一数据 ID、创建 owner 贯穿，各行用其 mutation plan 交互实例 ID。
- [TD-GATE-DATA-SAFETY] 既有数据只读；变更只作用于本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供的数据。
- [TD-GATE-RISK-UNCERTAINTY] 页面可验证内容由模型自行操作验证并在未完成时退回 discovery；仅把模型仍不理解的外部语义交给用户确认；风险只阻塞 risk/cases，不阻塞 plan。
- [TD-GATE-DFX] 先建立元素与交互骨架，再按 `docs/test-design/rules/dfx-test-strategy.md` 完成 DFX 12×4 评估和扩展；性能规格测试和 DFP性能不进入功能用例。
- [TD-GATE-LEAF-BATCH] 超过一个最小标题时逐最深标题分批，不合并、不再拆分；每个最小标题使用独立 run-dir，禁止在同一账本和 manifest 混装多个批次。
- [TD-GATE-PHASES] 批次严格按 discovery → plan → risk → cases → review → delivery 累积门禁执行。
- [TD-GATE-ORCHESTRATION] 最终架构强制使用确定性编排器：单 Discovery owner → Plan/DFX → 条件 Risk Arbiter → 按功能点 Case Worker → 独立只读 Reviewer → 单写者 Delivery；Agent 只写隔离 workspace，AgentTask/AgentResult 必须校验 source fingerprint；Review 未通过或存在返工不得交付。
- [TD-GATE-SHARDS] 新一轮先清旧产物；功能用例按功能点感知且每片 1–10 条，使用从 `function_cases_part_001.json` 开始无断号的三位编号分片，可容纳的同功能点不得跨片，`function_cases_manifest.json` 是唯一读取源。
- [TD-GATE-ASSEMBLY] 正式 Excel 只能由标准组装器生成，并由 `complete-deliverables` 一站式收口。
- [TD-GATE-ASSET-FACTS] `catalog/modules/*.json` 是产品事实源，`product-map.xlsx` 是查询投影。
- [TD-GATE-SENSITIVE-DATA] 交付件、账本、证据和产品资产不得保留真实 URL/IP、域名/主机名、账号、密钥、Token 或密码。
- [TD-GATE-PROTECTED-ASSETS] 框架升级必须保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
- [TD-GATE-CASE-QUALITY] 前置、步骤、预期编号换行并完整导航；标题为“功能点-当前用例标题”；折叠 AI_TEST/CODEX_TEST 实例编号后步骤和预期仍分别唯一、可判定；实探→计划→用例一致，同功能点一连续区块；状态分类计数从用例派生；确定性字段逐行有序一致；交互闭环。

阶段命令统一通过 `scripts/run-test-design.ps1` 执行；最终交付必须通过 `scripts/validate-test-design-deliverable.ps1`。
