# CodeBuddy 项目级测试设计配置导入说明

这个目录已经整理成项目级结构。把本目录中的全部内容复制到你的项目根目录后，CodeBuddy 就可以在该项目中读取测试设计 Skill、Rule、Memory 和 Excel 模板，并按统一规则处理需求文档、截图、原型和可访问页面。

## 复制后的项目结构

```text
your-project/
├── CODEBUDDY.md
├── .codebuddy/
│   ├── skills/
│   │   └── test-design/
│   │       └── SKILL.md
│   ├── .rules/
│   │   └── test-design-rule.mdc
│   └── rules/
│       └── test-design-rule.md
└── docs/
    └── test-design/
        ├── codebuddy-test-design-template.xlsx
        ├── 测试用例模板.xlsx
        ├── excel-template-spec.md
        ├── archive-and-index-guidelines.md
        ├── current/
        └── deliverables/
├── docs/
│   └── test-assets/
│       ├── product-map.xlsx
│       ├── modules/
│       ├── imports/
│       └── indexes/
```

## 每个部分的作用

`CODEBUDDY.md`

项目级 Memory。用于告诉 CodeBuddy 本项目长期采用的测试设计偏好，例如 Excel 输出、每个用户故事至少 10 条用例、必须包含性能测试、页面元素覆盖清单、前置条件/操作步骤/预期结果必须编号换行。

`.codebuddy/skills/test-design/SKILL.md`

项目级 Skill。用于告诉 CodeBuddy “如何做测试设计”：先做需求理解、业务逻辑抽取、页面元素识别和风险识别，再拆测试场景、生成测试用例、规划性能测试、输出自动化建议。

`.codebuddy/.rules/test-design-rule.mdc`

CodeBuddy IDE 项目规则文件。用于强制约束测试设计输出质量。

`.codebuddy/rules/test-design-rule.md`

CodeBuddy Code/CLI 兼容规则文件。内容与 `.codebuddy/.rules/test-design-rule.mdc` 一致，用于兼容不同 CodeBuddy 入口。

`docs/test-design/codebuddy-test-design-template.xlsx`

测试设计 Excel 模板。正式输出测试设计时，让 CodeBuddy 按这个模板生成。正式测试设计模板不包含 `测试系统导入用例` Sheet；需要导入测试系统时，应复制测试系统导出的 `测试用例模板.xlsx` 生成独立导入文件。

`docs/test-design/excel-template-spec.md`

Excel 字段说明，方便 CodeBuddy 和团队成员理解每个 Sheet 的结构，包括页面元素覆盖清单和测试系统导入字段。

`docs/test-design/archive-and-index-guidelines.md`

测试资产归档、模块能力索引、跨模块依赖和可复用测试数据维护规范。

`docs/test-assets/product-map.xlsx`

内部产品级测试知识图谱主入口。用于记录产品模块地图、业务对象地图、业务链路地图、页面元素地图、用例资产索引、模块能力、跨模块依赖、可复用测试数据、变更影响分析和变更记录。AI 每次生成前应读取该产品版图，不能只依赖对话记忆。

## 推荐使用提示词

导入后可以这样对 CodeBuddy 说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
根据以下用户故事生成测试设计 Excel，模板使用 docs/test-design/codebuddy-test-design-template.xlsx。
要求每条用户故事至少 10 条功能测试用例，必须包含性能测试设计。
```

处理页面截图、原型或可访问页面时可以这样说：

```text
请使用项目级 test-design Skill，并遵守项目测试设计 Rule。
我会提供需求文档和页面截图/页面地址，请先识别页面所有可点击和可交互元素，再生成测试设计 Excel。
要求功能测试用例覆盖所有按钮、链接、菜单、图标按钮、筛选、分页、弹窗和表格行操作；无法确认的元素写入页面元素覆盖清单和待确认问题。
页面具体业务逻辑请参考设计文档、需求说明、接口文档和验收标准；如果文档只写大概功能，请结合页面入口补全测试场景，并把无法确认的业务规则登记为待确认问题。
页面元素覆盖清单仅用于记录页面元素、覆盖状态和关联用例 ID；测试步骤和预期结果必须写入功能测试用例 Sheet，性能场景必须写入性能测试设计 Sheet。
如果需要导入测试系统，请复制 docs/test-design/测试用例模板.xlsx 生成独立导入文件，字段顺序、下拉框和标红/必填样式必须保留，内容从功能测试用例 Sheet 映射；不要修改原模板文件，也不要在正式测试设计 Excel 中新增测试系统导入 Sheet。
每次生成前请读取 docs/test-assets/product-map.xlsx；如果当前模块依赖其他模块，请读取产品版图中登记的依赖模块归档测试设计。正式生成前请先展示产品理解摘要，确认当前模块、依赖模块、业务对象、业务链路、可复用历史用例和待确认问题。客户交付件保存到 docs/test-design/current/ 或 docs/test-design/deliverables/；最终版测试设计应回存 docs/test-assets/modules/，导入文件副本回存 docs/test-assets/imports/，并同步更新产品版图。
如果任务范围是全产品、多个一级模块或某个大模块，请先输出分批设计计划，不得一次性生成完整测试用例；按模块、页面域或业务链路分批生成并回存后，再做跨模块汇总。
每个批次正式写测试用例前，如果存在可访问页面、原型或桌面窗口，应使用浏览器或 computer use 遍历当前批次所有可点击/可交互功能点，并把发现结果写入页面元素覆盖清单和功能测试用例。
测试系统导入模板中的红色字段和必填字段必须根据每条测试用例动态填写；测试用例名称、步骤描述、预期结果、测试类型、用例级别、执行方式不能固定套示例值。
测试用例名称必须正式、简洁、可检索，避免口语化、聊天式或操作随笔式表达，并使用功能点-当前用例标题格式，例如搜索筛选-按智能体名称查询列表。
测试系统导入模板中带下拉框的字段只能填写下拉允许值：测试类型为功能测试、性能规格测试、可靠性测试、兼容性测试、可维护性测试、安全性测试、易用性测试；测试用例级别为 L1、L2、L3、L4；执行方式为自动化、手动。测试系统自动生成字段不需要填写。
执行方式表示当前在测试系统中如何执行该条用例，默认填写手动；只有已有可运行、可维护并覆盖该用例主要校验点的自动化资产，且本次交付明确按自动化导入或关联自动化资产时，才填写自动化。
独立导入文件必须保留测试类型、测试用例级别、执行方式的 Excel 数据验证下拉框，不能只填合法文本值。
页面实探时，对已有数据可以查看、搜索、筛选、排序、分页、打开详情，或进入编辑/修改页面观察字段、校验、联动、多级子菜单和保存前提示，但不能保存、提交或改变已有数据；如需新增/编辑/删除/状态变更，只能操作带有 `AI_TEST` 或 `CODEX_TEST` 等标识且由本次实探创建的数据。
如果我提供测试数据，请优先拿这些数据进行实际页面操作；正常数据用于确认成功路径，异常/边界数据用于观察页面真实校验、错误提示和恢复路径，最终用例中的敏感数据请脱敏。
功能测试用例请按小功能块连续编排，同一块功能的主流程、异常、边界、权限/状态等用例放在附近。
```

也可以更短：

```text
基于项目测试设计规范，为以下用户故事生成测试设计 Excel。
```

## 注意

如果 CodeBuddy 同时加载 `.codebuddy/.rules` 和 `.codebuddy/rules`，可能会看到重复规则，但内容一致，不影响结果。若你确认当前 CodeBuddy 只使用其中一种规则目录，可以保留对应目录，删除另一份规则文件。

## 外网到内网升级

普通框架升级应通过升级包完成，不建议整包覆盖业务项目。升级包由 `scripts/new-framework-upgrade-package.ps1` 生成，内网通过 `scripts/upgrade-framework.ps1 -PackagePath <升级包>` 应用。

以下目录是内网受保护资产区，普通框架升级不得覆盖或删除：

- `docs/test-assets/`
- `docs/test-design/current/`
- `docs/test-design/deliverables/`

标识：PROTECTED_ASSET_DIRS

如果 `asset_schema_version` 或 `product-map.xlsx` 结构发生变化，必须执行资产迁移脚本增量补齐旧资产，不得用外网空模板覆盖内网真实资产。
