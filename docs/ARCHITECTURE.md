# 最终架构设计

```text
直接调用 Skill
  → 页面扫描与连续功能事务
  → facts.json
  → 独立功能与 DFX 计划
  → case-plan.json
  → 配对步骤用例
  → function-cases.json
  → 一次性轻量 Review
  → 正式测试设计.xlsx + 测试系统导入.xlsx
```

## 组件责任

1. 页面工具在同一会话和浏览器上下文中开放扫描、顺序操作并局部重扫。
2. `session_runtime.py` 透明绑定运行范围、原子记录事务、编译七类事实并执行一次跨产物审计。
3. 模型从 facts 识别独立功能，在计划阶段完成适用 DFX，再严格按计划写配对步骤。
4. `formal_assembler.py` 从同一结构化用例源独立生成两个 Excel，并做确定性技术验证。
5. `test_design_cli.py` 只提供内部诊断、恢复、Review 和交付辅助，不是用户工作流。

## 独立性与轻量化

每个阶段只读上游固化产物并写一个自己的产物，因此不需要 Agent 隔离。没有 Hook、义务状态机、观察 CSV、分片、manifest 或自动回退。正确性尽量由数据结构和生成时约束形成；Review 只发现跨产物语义问题，并将处理范围限定在一个 Case、一个功能或一个缺失事务。
