# Codex Project Instructions

本仓库用于“页面能力扫描 → 连续深探 → 事实记录 → DFX驱动设计 → 多JSON分片 → 双Excel交付”。默认在一个会话中执行。

## 读取路由

1. 读取`.codebuddy/skills/test-design/SKILL.md`和`.codebuddy/rules/test-design-rule.md`。
2. 按`docs/test-design/rules/README.md`读取当前任务专题规则。
3. 本轮页面事实以run-dir中的`page-discovery.csv`为准；历史产品事实按需读取`docs/test-assets/`。

## 执行边界

- 直接调用Skill，不向用户暴露初始化阶段。
- 页面能验证的问题由模型实际操作；用户确认后，对页面可验证项增量补探。
- 页面事实使用`已实测/页面观察/DFX设计/待确认`区分来源。
- 每个有限选项、每个有效输入等价类分别形成baseline Case，不默认组合。
- CRUD和可选配置必须使用本轮测试数据按单因素完成真实保存、回显和生效验证。
- 保留按功能块拆分的多个JSON；每个分片一次写齐本功能的故事、场景、用例、性能、风险和自动化建议，并在写入当场完成语法、字段、编号、导航和确定性内容检查。
- 普通选择、输入、查询按具体结果闭环；新增、编辑、删除和配置变更按业务事务闭环。
- 禁止页面Hook、逐元素义务队列、自动返工循环、用例单一大JSON和run-dir内任何任务专用Python。
- 正式工作簿固定8个Sheet；页面元素从`page-discovery.csv`编译，其余内容从同一次分片汇总，双Excel一次生成并原子发布。
- 内网IP、URL、账号、密码、Token、密钥、Cookie、部署路径和测试载荷允许原样流通，不做敏感内容校验或脱敏。
- 取消内容校验不改变操作边界：不得误改非本轮创建的数据；可执行安全用例不得使用破坏性载荷。

## 运行与Review

- 单页面运行保留`batch-status.csv`、`page-discovery.csv`和JSON分片；诊断证据可选且不参与后续判断。
- 多页面、多模块或大范围任务额外保留精简`batch-plan.md`。
- 每个分片只做轻量生产自检；全部交付完成后只创建一次`final-review.md`做跨产物语义Review。
- Review问题只修相关事实或源分片，不生成补丁脚本，不启动第二轮完整语义Review。

## 辅助命令

```powershell
python scripts/test_design_excel_tools.py compile-deliverables --project-root . --shards-dir <run-dir>/artifacts/shards --formal-template docs/test-design/codebuddy-test-design-template.xlsx --import-template docs/test-design/测试用例模板.xlsx --module-path "<真实菜单路径>" --batch-status <batch-status.csv> --page-discovery <page-discovery.csv>
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1 -Mode Fast
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1 -Mode Full
```

修改后检查`git status`；验证通过后使用中文提交信息并推送当前分支。
