# 最终架构设计

```text
直接调用 Skill
  → 页面扫描、现场事务校验与页面checkpoint
  → 自动恢复的facts.json
  → 自动事实计划骨架
  → 独立功能与 DFX 计划
  → case-plan.json
  → 配对步骤用例
  → function-cases.json
  → 一次性轻量 Review
  → 正式测试设计.xlsx + 测试系统导入.xlsx
```

## 组件责任

1. 页面工具在同一会话和浏览器上下文中开放扫描、顺序操作并局部重扫。
2. `session_runtime.py` 在事务写入现场校验闭环，按页面checkpoint编译事实；恢复时自动重建落后视图。
3. 模型从分层DFX骨架补充Case意图，只维护唯一check_assignments；系统派生引用、覆盖和步骤来源。
4. `session_runtime.py` 使用结构化结果锚点和业务语义指纹，生成时局部校验，最终只执行一次跨产物审计。
5. `formal_assembler.py` 从同一结构化用例源独立生成两个 Excel，保留各自模板结构并做确定性技术验证。
6. `test_design_cli.py` 只提供内部诊断、恢复、Review 和交付辅助，不是用户工作流。

## 独立性与轻量化

每个阶段只读上游固化产物并写一个自己的产物，因此不需要 Agent 隔离。没有 Hook、义务状态机、观察 CSV、分片、manifest 或自动回退。正确性尽量由数据结构和生成时约束形成；Review 只发现跨产物语义问题，并将处理范围限定在一个 Case、一个功能或一个缺失事务。
