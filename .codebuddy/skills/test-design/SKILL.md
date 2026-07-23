---
name: test-design
description: 基于页面实探、需求参考与DFX策略生成8-Sheet测试设计和测试系统导入Excel；适用于页面深探、功能/DFX用例设计、现有多JSON分片编译及双Excel交付。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# 测试设计 Skill

本 Skill 直接执行“页面能力扫描 → 连续深探 → 事实记录 → DFX驱动设计 → 多JSON分片 → 双Excel交付 → 一次最终Review”。不向用户暴露初始化阶段。

## 必读路由

1. `.codebuddy/.rules/test-design-rule.mdc`
2. `docs/test-design/rules/README.md`
3. `docs/test-design/rules/case-design.md`
4. `docs/test-design/rules/excel-deliverable.md`
5. `docs/test-design/rules/data-safety.md`
6. `docs/test-design/rules/dfx-test-strategy.md`
7. `docs/test-design/excel-template-spec.md`

页面任务追加 `page-discovery.md`；多页面、多模块或大范围任务追加 `batch-run.md`；需要导入测试系统时追加 `import-template.md`。

## 标准流程

1. 结合页面、用户材料、需求文档和历史资产理解当前功能；页面是功能实现事实的主要来源，文档与模型推断只作参考。
2. 从 DOM、可访问性树、可见文案、悬停状态和实际页面状态识别全部可交互能力，包括规则中未预设的新控件。
3. 在同一会话按业务事务连续深探。页面能验证的问题由模型实际操作；只有页面和资料无法确认的内容才询问用户。
4. 能力扫描后、相关深探事务前运行一次`checkpoint-page-facts`：页面可执行项继续实探；真正依赖权限、外部系统、不可逆影响或业务规则的待确认项一次性询问用户。用户确认后只补探受影响分支，不重复已实测内容。
5. 每个连续事务使用`upsert-page-facts`按字段名幂等写入现有`page-discovery.csv`。使用`已实测`、`页面观察`、`DFX设计`、`待确认`区分来源；不得批量把观察或推断改成实测。
6. DFX在元素登记和Case规划时参与扩展。每个有限选项、每个有效输入等价类分别形成baseline Case；其他控件使用默认稳定值，不默认生成笛卡尔积。标记生成的DFX场景必须落到同一功能的Case或同一Story的性能设计；不可执行方向进入风险且不冒充已生成用例。
7. CRUD及可选配置项按单因素实际保存验证。每个独立可编辑字段分别形成主验证Case；新增验证创建成功与生效；编辑验证保存、回显和生效；删除只作用于本轮测试数据并分别验证取消和确认。
8. 分页、筛选、刷新、显示隐藏、列设置和悬停后可识别的图标按页面实际能力动态加入；分页大小的每个有限选项、上一页、下一页、跳页及页面实际存在的首页/末页分别实探和规划，不生成固定分页套餐。
9. 保留按功能块拆分的多个JSON。每个分片包含本功能的故事、场景、功能用例、性能、风险和自动化建议；全局总览只在一个分片写一次。写完当前分片立即做JSON语法与结构自检，不生成任务专用Python。
10. 正式测试设计固定8个Sheet；页面元素清单由`page-discovery.csv`确定性生成，其余Sheet由现有分片汇总生成。
11. 只调用统一`compile-deliverables`命令一次生成、校验和原子发布双Excel。编译后状态为`待Review`；JSON失败时只修正当前分片，禁止绕过分片直接写Excel。
12. 全部产物完成后只执行一次跨产物语义Review，写入`final-review.md`后调用`complete-final-review`收口；Review不重新深探、不批量补Case、不建立自动返工循环。

## 关键质量规则

- 操作步骤从系统入口开始，并代入本轮页面的真实菜单层级与目标页；不得照抄规则中的示意名称，也不得保留占位符。
- 同一功能点用例连续排列；标题、步骤和预期必须围绕同一验证目标。
- 下拉和输入以实际选择/输入及具体结果为闭环；新增、编辑、删除、配置变更必须完成业务事务并验证结果。
- `关联用例ID`只记录该元素或分支的主验证Case；辅助使用不占用主验证映射。独立可配置字段不得共用同一个主验证Case。
- 待确认问题必须在受影响事务执行前集中确认；页面能够安全验证的内容不得进入待确认问题。
- 重置、取消、关闭等独立功能不能因为在其他Case中辅助使用而丢失自己的主验证Case。
- 不使用“页面正常响应”“结果符合预期”“确认操作完成后页面功能正常可用”等空泛语句。
- 不使用“结果或错误”“接受或截断”“提示或报错”等不可判定预期；未实测内容使用`DFX设计`或`待确认`来源，不冒充页面结论。
- 性能设计必须结合当前功能；无需求阈值时标记为实测基线、建议目标或待确认，不编造验收标准。
- 内网IP、URL、账号、密码、Token、密钥、Cookie、部署路径和安全测试载荷允许原样用于本轮内网实探和交付，不做识别、脱敏、替换或拦截。
- 内容可流通不改变操作边界：不得误改非本轮创建的数据；可执行安全用例使用无破坏性标记或只读载荷。

## 运行产物

单页面/单一最小功能默认保留：

- `batch-status.csv`
- `page-discovery.csv`
- 可选诊断证据（不参与后续判断）
- 按功能块拆分的JSON
- `final-review.md`（仅在最终Review时创建）

多页面、多模块或大范围任务额外保留精简 `batch-plan.md`。不预创建空白Review，不在run-dir生成任何`.py`。

## 交付命令

```powershell
python scripts/test_design_excel_tools.py compile-deliverables --project-root . --shards-dir <run-dir>/artifacts/shards --formal-template docs/test-design/codebuddy-test-design-template.xlsx --import-template docs/test-design/测试用例模板.xlsx --module-path "<真实菜单路径>" --batch-status <batch-status.csv> --page-discovery <page-discovery.csv>
```

完成后使用命令输出的 `FORMAL_WORKBOOK` 和 `IMPORT_WORKBOOK` 作为最终交付路径。
