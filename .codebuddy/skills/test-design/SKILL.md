---
name: test-design
description: 测试设计编排：实探、计划、风险、用例、Review、交付。
allowed-tools: Read, Write, Bash, Grep, Glob, Agent, TaskOutput, ToolSearch, DeferExecuteTool, WaitForMcpServers
---

# 测试设计执行 Skill

本 Skill 只负责编排；硬门禁读 Rule，专题规则按 `docs/test-design/rules/README.md` 延迟加载。

## 阶段编排

1. 读 catalog/需求并全量深探；分支写入 `interaction-branch-observations.csv`。
2. 生成 `element-case-plan.csv`、`test-data-lifecycle.csv` 和 DFX；仅确认模型不理解项，否则 `record-risk-none`。
3. Case Worker 按功能点生成，独立 Reviewer 审查并结构化返工。
4. 单写者组装 8 Sheet、导入文件、归档和产品事实。

<!-- TEST-DESIGN-GENERATED:BEGIN -->
- [TD-GATE-DELIVERY] 正式测试设计只含 8 个标准 Sheet；测试系统导入文件必须由独立模板副本生成。
- [TD-GATE-FULL-DISCOVERY] 默认全量深探；角色和数据状态建独立元素清单，以交互实例 ID 双向对账；实际执行并引用 artifacts 内非空证据文件；静态截图改名不复用；有限集合每项实际选择，观察结果锚点进入预期；输入/动态选择/分页/弹窗逐分支→`interaction-branch-observations.csv`；数据不足停留 discovery；风险确认不是豁免。
- [TD-GATE-CRUD-EFFECT] 创建必须成功；修改项逐项验证持久化回显和实际生效；本次创建对象以同一数据 ID、创建 owner 贯穿，各行用其 mutation plan 交互实例 ID。
- [TD-GATE-DATA-SAFETY] 既有数据只读；变更只作用于本次创建且带 `AI_TEST`、`CODEX_TEST` 或用户明确提供的数据。
- [TD-GATE-RISK-UNCERTAINTY] 页面可验证内容由模型自行操作验证并在未完成时退回 discovery；仅把模型仍不理解的外部语义交给用户确认；风险只阻塞 risk/cases，不阻塞 plan。
- [TD-GATE-DFX] 先建立元素与交互骨架，再按 `docs/test-design/rules/dfx-test-strategy.md` 完成 DFX 12×4 评估和扩展；性能规格测试和 DFP性能不进入功能用例。
- [TD-GATE-LEAF-BATCH] 超过一个最小标题时逐最深标题分批，不合并、不再拆分；每个最小标题使用独立 run-dir，禁止在同一账本和 manifest 混装多个批次。
- [TD-GATE-PHASES] 批次严格按 discovery → plan → risk → cases → review → delivery 累积门禁执行。
- [TD-GATE-ORCHESTRATION] 确定性编排器：单 Discovery owner → Plan/DFX → 条件 Risk Arbiter → 按功能点 Case Worker → 独立只读 Reviewer → 单写者 Delivery；Agent 限隔离 workspace；AgentTask/AgentResult 校验 source fingerprint；Review 未通过不得交付。
- [TD-GATE-SHARDS] 新轮清旧产物；功能用例按功能点感知，每片 1–10 条；`function_cases_part_001.json` 起三位编号无断号；同功能点不得跨片；`function_cases_manifest.json` 是唯一读取源。
- [TD-GATE-ASSEMBLY] 正式 Excel 只能由标准组装器生成，并由 `complete-deliverables` 一站式收口。
- [TD-GATE-ASSET-FACTS] `catalog/modules/*.json` 是产品事实源，`product-map.xlsx` 是查询投影。
- [TD-GATE-SENSITIVE-DATA] 禁含URL/IP、主机、账号凭据；二进制证据须同哈希审计 sidecar，缺失回 discovery。
- [TD-GATE-PROTECTED-ASSETS] 框架升级必须保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。标识：PROTECTED_ASSET_DIRS。
- [TD-GATE-CASE-QUALITY] 前置、步骤、预期编号换行并完整导航；标题“功能点-当前用例标题”；折叠 AI_TEST/CODEX_TEST 实例编号后步骤/预期仍分别唯一、可判定；实探→计划→用例一致；同功能点连续区块；状态分类计数从用例派生；确定性字段逐行有序一致；交互闭环。
<!-- TEST-DESIGN-GENERATED:END -->

## 命令

```powershell
/test-design-run <run-dir>
scripts/run-test-design.ps1 agent-status --run-dir <run-dir> --json
scripts/run-test-design.ps1 validate-review-artifacts --run-dir <run-dir>
scripts/run-test-design.ps1 complete-deliverables --run-dir <run-dir> --module-path "<模块路径>" --batch-id <批次ID>
```

正式入口是 `/test-design-run`；恢复时 `agent-submit` 必须带原 `--execution-id`。`pipeline-status` 等命令仅供诊断/门禁，不得绕过 Agent 与 Review。

<!-- LOCAL-OVERRIDES:BEGIN -->
<!-- 业务项目可以在本区块追加本地约束；同步脚本不得覆盖。 -->
<!-- LOCAL-OVERRIDES:END -->
