# 规则归属矩阵

本文件定义测试设计规范包中各类规则的权威来源、允许引用位置和不应承载位置。目标是减少重复规则正文，避免多个入口之间规则漂移。

## 归属原则

- 权威源保存完整规则正文。
- 摘要引用文件只说明入口、边界和必须读取的权威源，不复制完整规则。
- 校验脚本优先检查权威源是否完整，再检查摘要引用是否指向权威源。
- `.codebuddy/.rules/test-design-rule.mdc` 与 `.codebuddy/rules/test-design-rule.md` 是同一硬规则的双入口镜像，必须保持内容一致。

## 规则归属矩阵

| 规则类型 | 权威源 | 可摘要引用 | 不应承载完整规则 |
| --- | --- | --- | --- |
| 硬性测试质量规则 | `.codebuddy/.rules/test-design-rule.mdc`、`.codebuddy/rules/test-design-rule.md` | `AGENTS.md`、`CODEBUDDY.md`、`.codebuddy/skills/test-design/SKILL.md` | `README.md`、`docs/test-design/README.md` |
| 执行流程与自检步骤 | `.codebuddy/skills/test-design/SKILL.md` | `AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | `README.md` |
| Excel Sheet、字段、枚举、导入模板 | `docs/test-design/excel-template-spec.md` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md` | `README.md` |
| 产品版图、资产归档、跨模块依赖 | `docs/test-design/archive-and-index-guidelines.md` | Skill、Rule、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | `README.md` |
| 批次运行状态与质量门禁 | `docs/test-assets/batch-runs/README.md`、`docs/test-assets/batch-runs/templates/`、`.codebuddy/skills/test-design/SKILL.md` | Rule、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md`、`docs/test-design/archive-and-index-guidelines.md` | `README.md`、`docs/test-design/README.md` |
| 交付件质量校验 | `scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1`、`docs/test-design/excel-template-spec.md` | `README.md`、`README_IMPORT.md`、Skill、`AGENTS.md`、`CODEBUDDY.md`、`docs/ARCHITECTURE.md` | Rule 中的脚本实现细节 |
| 客户交付与内部资产边界 | `docs/test-design/archive-and-index-guidelines.md`、`docs/test-assets/README.md` | `README.md`、`README_IMPORT.md`、`docs/ARCHITECTURE.md` | Skill 中的长篇资产目录说明 |
| 外网到内网升级 | `docs/UPGRADE.md`、`UPGRADE_MANIFEST.md`、`scripts/new-framework-upgrade-package.ps1`、`scripts/upgrade-framework.ps1` | `README.md`、`README_IMPORT.md`、`docs/ARCHITECTURE.md` | Skill、Rule 中的长篇升级流程 |
| 架构分层与维护边界 | `docs/ARCHITECTURE.md`、`docs/RULE_OWNERSHIP.md` | `README.md`、`README_IMPORT.md` | Skill、Rule |

## 精简要求

- README 只保留用途、关键文件、使用方式、自检命令和升级入口。
- `README_IMPORT.md` 只作为复制到业务项目的导入说明和提示词示例，不作为规则权威源；示例提示词应引用 Skill、Rule 和归属矩阵。
- `docs/test-design/README.md` 只保留目录说明和指向权威源的链接。
- `AGENTS.md` 与 `CODEBUDDY.md` 可保留最高优先级规则摘要，但应引用权威源。
- Skill 保留执行步骤和自检清单；硬规则正文以 Rule 为准。
- Rule 保留完整硬规则；两个 Rule 文件必须完全一致。
- 模板字段、下拉框、导入文件、Excel 格式只在 `excel-template-spec.md` 中完整描述。
- 资产归档、产品版图、跨模块依赖只在 `archive-and-index-guidelines.md` 中完整描述。

## 变更同步

| 变更类型 | 必改文件 | 可选同步 |
| --- | --- | --- |
| 修改硬性测试质量规则 | 两个 Rule 文件、校验脚本 | Skill 自检摘要、AGENTS/CODEBUDDY 摘要 |
| 修改执行流程 | Skill、校验脚本 | AGENTS/CODEBUDDY 摘要 |
| 修改 Excel 字段或枚举 | Excel 模板、`excel-template-spec.md`、校验脚本 | Skill/Rule 中的摘要 |
| 修改归档、批次运行状态或产品版图规则 | `archive-and-index-guidelines.md`、`docs/test-assets/batch-runs/README.md`、批次模板、校验脚本 | Skill/Rule/AGENTS/CODEBUDDY 摘要 |
| 修改交付件质量校验 | `scripts/validate-test-design-deliverable.py`、`scripts/validate-test-design-deliverable.ps1`、`excel-template-spec.md`、校验脚本 | README/Skill/AGENTS/CODEBUDDY 摘要 |
| 修改升级机制 | `docs/UPGRADE.md`、`UPGRADE_MANIFEST.md`、升级脚本、校验脚本 | README/README_IMPORT |
| 修改架构分层 | `docs/ARCHITECTURE.md`、`docs/RULE_OWNERSHIP.md`、校验脚本 | README |
