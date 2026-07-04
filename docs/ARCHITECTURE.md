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
| 内部测试资产事实 | `docs/test-assets/product-map.xlsx`、`docs/test-assets/modules/`、`docs/test-assets/imports/` | 保存产品测试知识图谱、最终测试设计归档、导入文件副本、模块能力、业务对象、业务链路、跨模块依赖和可复用测试数据。 |
| 自动化校验 | `scripts/validate-test-design.py`、`scripts/validate-test-design.ps1` | 防止模板结构、导入模板下拉框和关键规则发生漂移。 |

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
10. 每次修改规范或模板后必须运行稳定性自检。

## 变更同步规则

- 改交付边界：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`excel-template-spec.md`。
- 改 Excel 字段或枚举：同步模板、`excel-template-spec.md`、自检脚本。
- 改页面实探或测试数据规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule。
- 改导入模板规则：不得直接修改原 `测试用例模板.xlsx`，除非测试系统模板本身发生版本变化。
- 改测试资产归档或跨模块依赖规则：同步 `AGENTS.md`、`CODEBUDDY.md`、Skill、Rule、`docs/test-design/archive-and-index-guidelines.md` 和自检脚本。

## 发布前检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
git status --short
```

提交信息使用中文，简洁说明本次规范、模板或校验变更。
