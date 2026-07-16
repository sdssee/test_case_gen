# 最终架构设计

```text
直接调用 Skill
  → 页面扫描、现场事务校验与页面checkpoint
  → 自动恢复的facts.json
  → 自动事实计划骨架
  → 有限选项/有效输入类别的独立baseline意图 + 适用DFX计划
  → case-plan.json
  → 配对步骤用例
  → function-cases.json
  → 一次性轻量 Review
  → 正式测试设计.xlsx + 测试系统导入.xlsx
```

## 组件责任

1. 页面工具在同一会话和浏览器上下文中开放扫描、顺序操作并局部重扫。
2. `session_runtime.py` 在元素登记时按 DFX 适用性编译并返回精简实探清单；事务持续记录已完成分支，按页面checkpoint汇总剩余既定分支。参与/触发控件和业务闭环在写入现场校验，恢复时自动重建落后视图。
3. 模型从只关联实测分支的分层DFX骨架补充Case意图；每个有限选项和每个实测有效输入等价类绑定独立baseline Case，不默认组合。模型只维护唯一check_assignments；系统从主验证和辅助使用控件派生引用、覆盖和步骤来源。
4. `session_runtime.py` 使用只约束明确tokens/value的结构化结果锚点和业务语义指纹，生成时局部校验，最终只执行一次跨产物审计。
5. `formal_assembler.py` 从同一结构化用例源独立生成两个 Excel，保留各自模板结构并做确定性技术验证；新运行固定在 `docs/test-design/current/<run-id>/`，交付回执返回完整路径。
6. `test_design_cli.py` 只提供内部诊断、恢复、Review 和交付辅助，不是用户工作流。

## 独立性与轻量化

每个阶段只读上游固化产物并写一个自己的产物，因此不需要 Agent 隔离。计划和用例在各自单文件内按功能幂等upsert。没有 Hook、义务状态机、观察 CSV、分片、manifest、临时Python编排脚本或自动回退。标准CLI将新run-dir固定到项目内的标准目录。正确性由交互前的 DFX 实探清单、事务结构约束和生成时约束形成；Review 只发现跨产物语义问题，并将处理范围限定在一个 Case、一个功能或一个缺失事务。
