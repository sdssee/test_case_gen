# CodeBuddy运行说明

本项目通过`test-design` Skill完成页面深探、DFX测试设计和双Excel交付。

## 运行流程

1. 直接调用`.codebuddy/skills/test-design/SKILL.md`。
2. 扫描页面实际能力并连续深探。
3. 在`page-discovery.csv`记录`已实测/页面观察/DFX设计/待确认`事实。
4. 用户确认后，对页面可验证项增量返回深探。
5. 每个有限选项和有效输入等价类分别形成baseline Case。
6. CRUD及可选配置使用本轮数据按单因素完成真实保存验证。
7. 按功能块生成多个JSON分片，本功能的故事、场景、用例、性能、风险和自动化建议一次写齐，写入当场完成语法、字段、编号、导航和具体预期检查。
8. 使用统一编译器从同一次分片和`page-discovery.csv`生成固定8-Sheet正式Excel和独立导入Excel。
9. 原子发布双Excel后执行一次最终语义Review。

## 质量边界

- 不使用多Agent自动编排、Hook、逐元素义务队列、run-dir任务专用Python或自动返工循环。
- 普通选择、输入和查询以实际结果闭环；状态变更事务必须验证保存、回显、生效、取消或删除结果。
- 多JSON分片设计保留，不合并为单一大JSON。
- 内网IP、URL、账号、密码、Token、密钥、Cookie、部署路径和测试载荷允许原样流通，不做脱敏或拦截。
- 只能改变本轮创建的测试数据；共享环境变更后必须恢复；可执行安全用例不得使用破坏性载荷。

## 批次与恢复

- 单页面保留轻量`batch-status.csv`、`page-discovery.csv`和JSON分片。
- 多页面、多模块或大范围任务额外保留精简`batch-plan.md`。
- 不预创建空白Review；最终只生成一次`final-review.md`。

## 交付

```powershell
python scripts/test_design_excel_tools.py compile-deliverables --project-root . --shards-dir <run-dir>/artifacts/shards --formal-template docs/test-design/codebuddy-test-design-template.xlsx --import-template docs/test-design/测试用例模板.xlsx --module-path "<真实菜单路径>" --batch-status <batch-status.csv> --page-discovery <page-discovery.csv>
```

以工具输出的`FORMAL_WORKBOOK`和`IMPORT_WORKBOOK`为准，不自行拼接文件名。
