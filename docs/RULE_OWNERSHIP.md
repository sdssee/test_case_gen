# 规则归属矩阵

本文件定义测试设计规范包中各类规则的权威来源、允许引用位置和不应承载位置。目标是减少重复规则正文，避免多个入口之间规则漂移。

## 归属原则

- 权威源保存完整规则正文；超过入口加载阈值的规则必须沉淀到 `docs/test-design/rules/` 专题文档。
- 摘要引用文件只说明入口、边界、读取路由和必须读取的权威源，不复制完整规则。
- 校验脚本优先检查权威源是否完整，再检查摘要引用是否指向权威源。
- `.codebuddy/rules/test-design-rule.md` 是唯一 Rule 权威源；`.codebuddy/.rules/test-design-rule.mdc` 是自动生成镜像。入口的 Gate 区块由稳定 Gate ID 自动生成，本地扩展只写入 `LOCAL-OVERRIDES` 区块。

## 规则归属矩阵

| 规则类型 | 权威源 | 可摘要引用 | 不应承载完整规则 |
| --- | --- | --- | --- |
| 硬性测试质量规则 | `.codebuddy/rules/test-design-rule.md`、`docs/test-design/rules/case-design.md` | `.codebuddy/.rules/test-design-rule.mdc`、`AGENTS.md`、`CODEBUDDY.md`、Skill | `README.md`、`docs/test-design/README.md` |
| DFX 测试策略矩阵 | `docs/test-design/rules/dfx-test-strategy.md`、`docs/test-design/rules/case-design.md` | Rule、Skill、`AGENTS.md`、`CODEBUDDY.md`、`README.md`、`docs/test-design/README.md` | 入口文件中的完整 12 维度矩阵正文 |
| 执行流程与自检步骤 | `.codebuddy/skills/test-design/SKILL.md`、`docs/test-design/rules/README.md` | `AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | `README.md` |
| 页面实探与数据安全 | `docs/test-design/rules/page-discovery.md`、`docs/test-design/rules/data-safety.md` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md` | `README.md` |
| 元素用例计划与测试数据生命周期 | `docs/test-design/rules/page-discovery.md`、`docs/test-design/rules/case-design.md`、`docs/test-assets/batch-runs/templates/element-case-plan-template.csv`、`docs/test-assets/batch-runs/templates/test-data-lifecycle-template.csv` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md`、`README.md` | `README.md` 中的完整字段说明 |
| Excel Sheet、字段、枚举、导入模板 | `docs/test-design/excel-template-spec.md`、`docs/test-design/rules/excel-deliverable.md`、`docs/test-design/rules/import-template.md` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md` | `README.md` |
| 产品版图、资产归档、跨模块依赖 | `docs/test-design/archive-and-index-guidelines.md`、`docs/test-design/rules/product-map-sync.md` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | `README.md` |
| 批次运行状态与质量门禁 | `docs/test-design/rules/batch-run.md`、`docs/test-assets/batch-runs/README.md`、`docs/test-assets/batch-runs/templates/` | Rule、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md`、`docs/test-design/archive-and-index-guidelines.md` | `README.md`、`docs/test-design/README.md` |
| 交付件质量校验 | `scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1`、`scripts/validate-generated-python-scripts.py`、`scripts/validate-generated-python-scripts.ps1`、`scripts/test_design_excel_tools.py`、`docs/test-design/excel-template-spec.md` | `README.md`、`README_IMPORT.md`、Skill、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | Rule 中的脚本实现细节 |
| 客户交付与内部资产边界 | `docs/test-design/archive-and-index-guidelines.md`、`docs/test-assets/README.md` | `README.md`、`README_IMPORT.md`、`docs/ARCHITECTURE.md` | Skill 中的长篇资产目录说明 |
| 外网到内网升级 | `docs/UPGRADE.md`、`UPGRADE_MANIFEST.md`、`scripts/new-framework-upgrade-package.ps1`、`scripts/upgrade-framework.ps1` | `README.md`、`README_IMPORT.md`、`docs/ARCHITECTURE.md` | Skill、Rule 中的长篇升级流程 |
| 架构分层与维护边界 | `docs/ARCHITECTURE.md`、`docs/RULE_OWNERSHIP.md` | `README.md`、`README_IMPORT.md` | Skill、Rule |
| 轻量入口与 Rule 镜像契约 | `docs/test-design/rules/entry-contract.json`、`.codebuddy/rules/test-design-rule.md` | `scripts/sync-rule-entrypoints.py`、AGENTS、CODEBUDDY、Skill | 分别手工维护两份 Rule 镜像 |

## 精简要求

- README 只保留用途、关键文件、使用方式、自检命令和升级入口。
- `README_IMPORT.md` 只作为复制到业务项目的导入说明和提示词示例，不作为规则权威源；示例提示词应引用 Skill、Rule 和归属矩阵。
- `docs/test-design/README.md` 只保留目录说明和指向权威源的链接。
- `AGENTS.md` 与 `CODEBUDDY.md` 只保留最高优先级规则摘要和读取路由，目标低于 10000 字符。
- Skill 保留执行步骤、自检命令和读取路由，目标低于 10000 字符；硬规则正文以 Rule 与专题规则为准。
- Rule 保留不可违反的硬门禁和读取路由，目标低于 10000 字符；两个 Rule 文件必须完全一致。
- `.codebuddy/rules/test-design-rule.md` 是 Rule 权威文件，`.codebuddy/.rules/test-design-rule.mdc` 由 `scripts/sync-rule-entrypoints.py --write` 生成；发布前使用无参数模式检查入口契约。
- `docs/test-design/rules/` 保存可按任务类型读取的详细规则，避免 CodeBuddy 加载 Skill 时超过 1 万字。
- 模板字段、下拉框、导入文件、Excel 格式只在 `excel-template-spec.md` 中完整描述。
- 资产归档、产品版图、跨模块依赖只在 `archive-and-index-guidelines.md` 中完整描述。

## 变更同步

| 变更类型 | 必改文件 | 可选同步 |
| --- | --- | --- |
| 修改硬性测试质量规则 | canonical Rule、专题规则、entry contract | 运行 `sync-rule-entrypoints.py --write` 自动更新镜像与入口 Gate 区块 |
| 修改执行流程 | Skill、校验脚本 | AGENTS/CODEBUDDY 摘要 |
| 修改 Excel 字段或枚举 | Excel 模板、`excel-template-spec.md`、校验脚本 | Skill/Rule 中的摘要 |
| 修改归档、批次运行状态或产品版图规则 | `archive-and-index-guidelines.md`、`docs/test-assets/batch-runs/README.md`、批次模板、校验脚本 | Skill/Rule/AGENTS/CODEBUDDY 摘要 |
| 修改元素计划、测试数据生命周期或 `function_cases_part_*.json` 功能用例分片规则 | `page-discovery.md`、`case-design.md`、批次模板、校验脚本 | Skill/Rule/AGENTS/CODEBUDDY/README 摘要 |
| 修改交付件质量校验 | `scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1`、`scripts/validate-generated-python-scripts.py`、`scripts/validate-generated-python-scripts.ps1`、`scripts/test_design_excel_tools.py`、`excel-template-spec.md`、校验脚本 | README/Skill/AGENTS/CODEBUDDY 摘要 |
| 修改升级机制 | `docs/UPGRADE.md`、`UPGRADE_MANIFEST.md`、升级脚本、校验脚本 | README/README_IMPORT |
| 修改架构分层 | `docs/ARCHITECTURE.md`、`docs/RULE_OWNERSHIP.md`、校验脚本 | README |
