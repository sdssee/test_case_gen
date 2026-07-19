---
name: test-design-planning
description: 从已完成的facts.json生成事实驱动的DFX测试计划。用于独立功能识别、实测分支到Case映射、性能/风险/自动化设计与case-plan.json写入。
allowed-tools: Read, Write, Bash, Grep, Glob
---

# 测试规划

只读 `facts.json`，只写 `case-plan.json`。读取
`docs/test-design/rules/dfx-test-strategy.md`、`case-design.md` 和
`artifact-contract.md`。

1. 先调用 `plan-skeleton`，从每个真实检查生成主验证目标、测试点和稳定场景签名。
2. 每个实测有效关联形成一个 baseline Case；不存在关联时，每个有限选项和每个有效
   输入等价类分别成例。空值、格式、边界、重复等已实测分支形成相应 DFX Case；不做
   笛卡尔积，不用一个辅助操作抵消另一个元素的独立用例。
3. 为每个功能一次性补充紧凑 `design_context` 和 `automation_profile`。性能根据事实中的
   读取、写入、异步、批量传输或数据渲染链路生成基线、预期峰值和容量探索；没有 SLA
   时记录实测基线方法，不编造阈值。只有纯本地静态交互可判性能不适用。
4. 风险和其他 DFX 维度用于驱动场景生成与专项 Sheet，不在 Review 阶段反向贴标签。
   非预期实探结果必须分配给用例、风险或性能结论。
5. 按功能调用 `write-plan` 幂等 upsert。运行时补齐无歧义的一对一分配；出现错误只修
   当前功能块一次，不直接编辑计划文件。

完成前确认所有事实功能都有计划、所有检查有唯一处置、Case 标题和
`verification_focus` 非空，然后进入用例阶段。

