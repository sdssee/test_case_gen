---
name: test-design
description: 在一个连续会话中完成页面全量深探、事实编译、DFX 左移规划、测试用例编写、审查与 Excel 交付。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# 测试设计执行 Skill

## 执行顺序

1. 初始化 run-dir，读取需求和产品事实。
2. 在同一浏览器上下文连续深探。进入页面和结构变化时全量扫描；每次操作后只局部重扫并比较变化；新元素动态加入事务。
3. 在有意义的观察点追加 event；随后从 `events.jsonl` 重建 `facts.json`。不要为每次点击建立任务或执行一次命令。
4. 基于 facts 识别独立功能，为每个功能建立基线用例，并在写正文前按 DFX 补充适用场景，写入 `case-plan.json`。
5. 只依据 facts 与 plan 写 `function-cases.json`。同一功能的用例连续排列；每条用例引用事实。
6. 执行一次双向审查并局部修复；通过后生成正式 8 Sheet 和独立测试系统导入文件。

## 深探事务

- 扫描只读，操作串行；扫描与事务交错执行，不是独立阶段。
- 默认遍历全部可交互元素。有限集合逐项选择，记录精确选项和页面变化。
- CRUD/配置操作像手工测试一样真实提交。创建成功后验证对象可查询；编辑/单因素配置验证保存、重开回显、实际效果和恢复；删除只操作本轮测试对象。
- 配置默认值可与 CRUD 基线共用事实；不同单因素值分别观察，不做组合。
- 页面可以验证的内容自行操作。工具错误最多重试一次；业务失败就是事实；暂不可执行项记为 pending 并继续其他安全项目，结束时只汇总一次。

## 阶段隔离

- discovery 不写计划和用例。
- plan 不修改 facts。
- cases 不反向修改 plan 或 facts。
- review 对上游只读；交付只消费 review 通过的产物。
- 上下文切换依靠固化文件，不依赖会话记忆。

## 命令

参见根目录 `AGENTS.md`。写阶段文件前读取 `docs/test-design/rules/artifact-contract.md`，其他详细规则按 `docs/test-design/rules/README.md` 路由读取。
