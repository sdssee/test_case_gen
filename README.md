# test_case_gen

项目级测试设计规范包，用于让 CodeBuddy/Codex 按统一规则生成测试设计 Excel、功能测试用例、性能测试设计、页面元素覆盖清单、风险与待确认问题和自动化建议。

本仓库不是业务应用代码。它提供可复制到业务项目根目录的 Memory、Skill、Rule 和 Excel 模板。

## 适用场景

- 用户故事、需求文档、接口文档、PR Diff、缺陷单的测试设计
- 页面截图、原型图、可访问页面的可交互元素覆盖
- 已有测试用例补充、优化和回归测试设计
- 需要生成测试系统导入文件的测试用例交付

## 关键文件

| 文件 | 说明 |
| --- | --- |
| `AGENTS.md` | Codex 项目级执行说明。 |
| `CODEBUDDY.md` | CodeBuddy 项目级 Memory。 |
| `docs/ARCHITECTURE.md` | AI 规则分层、模板契约和维护边界。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计 Skill。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 规则。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 规则。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计模板，包含 8 个标准 Sheet。 |
| `docs/test-design/测试用例模板.xlsx` | 测试系统导入模板。需要导入时复制该模板生成独立导入文件，不修改原模板。 |
| `docs/test-design/excel-template-spec.md` | Excel 字段和模板规则说明。 |
| `docs/test-design/archive-and-index-guidelines.md` | 测试资产归档、模块能力索引和跨模块依赖维护规范。 |
| `docs/test-assets/product-map.xlsx` | 内部产品测试知识图谱主入口，不作为默认客户交付件。 |
| `docs/test-assets/` | 内部产品级测试资产库，保存模块归档、导入副本和产品版图。 |
| `README_IMPORT.md` | 将本规范复制到业务项目的说明。 |
| `scripts/validate-test-design.ps1` | 模板稳定性自检入口。 |

## 正式测试设计 Sheet

`codebuddy-test-design-template.xlsx` 默认包含：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计工作簿不新增 `测试系统导入用例` Sheet。

## 测试系统导入

需要导入测试系统时：

1. 复制 `docs/test-design/测试用例模板.xlsx`。
2. 将 `功能测试用例` 中需要导入的内容映射填入副本。
3. 保留原模板中的字段顺序、下拉框、必填样式、标红字段和自动生成字段空值。
4. 不修改原始 `测试用例模板.xlsx`。

## 客户交付与内部资产

- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`
- 内部产品测试资产库放在 `docs/test-assets/`
- 产品测试知识图谱主入口为 `docs/test-assets/product-map.xlsx`
- 正式测试设计最终版归档到 `docs/test-assets/modules/`
- 测试系统导入文件副本归档到 `docs/test-assets/imports/`
- 每次生成前读取产品版图和依赖模块归档；正式生成前展示产品理解摘要；每次生成后回存最终版并更新产品版图
- 不依赖 AI 对话记忆保存具体业务事实

## 大范围任务

- 单模块任务正式写用例前，也要先做模块级粗遍历，识别菜单入口、页面清单、核心功能点、业务对象、状态流转和跨模块依赖。
- 全产品、多个一级模块或大模块测试设计必须先遍历一级菜单、二级菜单和必要的三级菜单，拿到菜单轮廓、页面清单和功能地图后，再输出分批设计计划，不得一次性生成完整测试用例。
- 分批拆分维度优先按模块、一级菜单、二级菜单或三级菜单，其次才按页面域或业务链路。
- 每批按模块、页面域或业务链路生成测试设计和导入文件，并立即回存内部资产库。
- 每批正式写测试用例前，如有可访问页面，应使用浏览器或 computer use 遍历当前批次所有可点击/可交互功能点。
- 具体写测试用例时，要在对应页面或功能点内做深遍历，覆盖所有可点击、可输入、可测试元素。
- 所有批次完成后，再生成跨模块汇总、回归范围、风险清单和客户总览交付件。

## 使用方式

在目标项目中引用本规范后，可以直接对 CodeBuddy/Codex 说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下需求生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
如果需要导入测试系统，请复制 docs/test-design/测试用例模板.xlsx 生成独立导入文件，不要修改原模板。
```

更完整的执行规则见：

- `AGENTS.md`
- `CODEBUDDY.md`
- `docs/ARCHITECTURE.md`
- `.codebuddy/skills/test-design/SKILL.md`
- `docs/test-design/excel-template-spec.md`

## 稳定性自检

每次调整规范或 Excel 模板后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

该脚本会检查：

- 正式测试设计模板只包含 8 个标准 Sheet
- 正式模板不包含 `测试系统导入用例` Sheet
- 测试系统导入模板字段顺序正确
- `测试类型`、`测试用例级别`、`执行方式` 的 Excel 下拉框仍保留
- 无已落地自动化资产时，导入文件中的 `执行方式` 默认填写 `手动`
- `用例标题`/`测试用例名称` 使用 `功能点-当前用例标题` 格式，避免导入系统后丢失功能点信息
- 产品版图文件存在且包含标准 Sheet

## 维护原则

- 规则变化时同步更新 `AGENTS.md`、`CODEBUDDY.md`、Skill 和 Rule。
- 模板字段变化时同步更新 `docs/test-design/excel-template-spec.md`。
- 修改完成后运行稳定性自检。
- 按项目约定，修改完成并验证通过后提交并推送到 `origin`。

## 外网到内网升级

- 普通框架升级使用 `scripts/new-framework-upgrade-package.ps1` 生成升级包。
- 内网使用 `scripts/upgrade-framework.ps1 -PackagePath <升级包>` 应用升级包。
- 升级包默认保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`，不得覆盖内网真实资产。标识：PROTECTED_ASSET_DIRS。
- `VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。
- 只有 `asset_schema_version` 变化或 `product-map.xlsx` 结构变化时，才需要资产迁移；迁移必须增量补齐，不得用空模板覆盖真实资产。

详细流程见 `docs/UPGRADE.md`。
