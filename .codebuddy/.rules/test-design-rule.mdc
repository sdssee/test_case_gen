# 测试设计硬性门禁

`.codebuddy/rules/test-design-rule.md` 是唯一 Rule 权威源；当前文件位于 `.mdc` 路径时只是自动生成镜像。详细做法按 `docs/test-design/rules/README.md` 路由读取。

- [TD-GATE-DELIVERY] 正式测试设计只含 8 个标准 Sheet；测试系统导入文件必须由独立模板副本生成。
- [TD-GATE-FULL-DISCOVERY] 页面默认全量深探，风险确认不是是否深探的开关。
- [TD-GATE-CRUD-EFFECT] 创建必须成功；编辑/修改项必须逐项执行并验证持久化回显和实际生效。
- [TD-GATE-DATA-SAFETY] 既有数据只读；变更只作用于本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供的数据。
- [TD-GATE-RISK-UNCERTAINTY] 仅把模型仍不理解的内容交给用户确认；风险只阻塞 risk/cases，不阻塞 plan。
- [TD-GATE-DFX] 先建立元素与交互骨架，再按 `docs/test-design/rules/dfx-test-strategy.md` 完成 DFX 12×4 评估和扩展；性能规格测试和 DFP性能不进入功能用例。
- [TD-GATE-LEAF-BATCH] 超过一个最小标题时逐最深标题分批，不合并、不再拆分；每个最小标题使用独立 run-dir，禁止在同一账本和 manifest 混装多个批次。
- [TD-GATE-PHASES] 批次严格按 discovery → plan → risk → cases → delivery 累积门禁执行。
- [TD-GATE-SHARDS] 新一轮先清旧产物；功能用例每 10 条写入 `artifacts/data/function_cases_part_001.json` 等分片，`function_cases_manifest.json` 是唯一读取源。
- [TD-GATE-ASSEMBLY] 正式 Excel 只能由标准组装器生成，并由 `complete-deliverables` 一站式收口。
- [TD-GATE-ASSET-FACTS] `catalog/modules/*.json` 是产品事实源，`product-map.xlsx` 是查询投影。
- [TD-GATE-SENSITIVE-DATA] 交付件、账本、证据和产品资产不得保留真实 URL/IP、账号、密钥、Token 或密码。
- [TD-GATE-PROTECTED-ASSETS] 框架升级必须保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
- [TD-GATE-CASE-QUALITY] 前置条件、操作步骤、预期结果编号换行；步骤从系统入口写完整导航；标题为“功能点-当前用例标题”；临时交互必须闭环。

阶段命令统一通过 `scripts/run-test-design.ps1` 执行；最终交付必须通过 `scripts/validate-test-design-deliverable.ps1`。
