# 页面深探与测试用例生成工具包

本项目让模型先像人工测试人员一样彻底操作页面，再把真实页面行为转换成可执行、可追溯、可直接导入测试系统的测试用例。重点是完整理解功能和配置效果，而不是机械增加用例数量。

## 项目作用

- 从 DOM、可访问性树和可见状态动态发现页面实际功能。
- 按实际交互类型操作页面元素，验证有限选项、输入、状态切换、容器开合及动态联动。
- 对新增、编辑、删除和配置项完成提交、重开、实际生效与恢复闭环。
- 从实探事实识别独立功能，在用例编写前按适用 DFX 策略扩展场景。
- 生成同功能集中、步骤与预期逐项对应的功能测试用例。
- 同时交付正式测试设计 Excel 和独立测试系统导入 Excel。

## Skill、规则与运行方式

用户直接调用 `.codebuddy/skills/test-design/SKILL.md` 并提供测试目标。Skill 自动绑定或恢复内部运行目录，然后直接开始页面扫描，不需要用户执行初始化命令。

规则分层：

- `.codebuddy/rules/test-design-rule.md`：不可违反的简明规则。
- `docs/test-design/rules/README.md`：按当前阶段加载专题规则。
- `docs/test-design/rules/page-discovery.md`：连续页面深探。
- `docs/test-design/rules/dfx-test-strategy.md`：DFX 左移策略。
- `docs/test-design/rules/case-design.md`：计划和用例正文。
- `docs/test-design/rules/excel-deliverable.md`：双 Excel 交付。

整个任务只在一个会话和一个浏览器上下文中运行，不创建 Agent，也不依赖 Hook。

## 执行流程

```text
调用 Skill并理解范围
  → 扫描页面、执行连续功能事务、操作后局部重扫
  → events.jsonl 编译为 facts.json
  → 自动形成事实计划骨架，识别独立功能并在 case-plan.json 中左移展开 DFX
  → 按计划生成配对步骤 function-cases.json
  → 执行一次轻量跨产物 Review
  → 独立生成正式测试设计.xlsx和测试系统导入.xlsx
```

扫描和事务不是两个割裂阶段。进入页面先扫描，发现元素后立即执行相关功能事务，页面变化后局部重扫；新元素动态加入当前或后续事务。最终全量扫描稳定且没有未处理元素时结束深探。

一个功能事务可以连续验证多个相关检查点，但最终 Case 数由独立测试意图、输入状态和可观察结果决定，不按控件数、选项数或点击数固定展开。所有页面能力都使用同一套事实与计划规则；CRUD 和配置采用完整业务闭环，配置暂按单因素验证，不做组合爆炸。

框架不内置分页、搜索、弹窗或某个业务模块的专用 Case 模板。功能名称、元素类型和事务类型都来自当前页面事实；相同控件在不同产品中产生的效果不同，计划和用例也随实际观察结果变化。

## 阶段相对独立

| 阶段 | 只读输入 | 唯一写入 |
| --- | --- | --- |
| discovery | 页面、需求、产品资料 | `events.jsonl`、`facts.json` |
| plan | facts、DFX规则 | `case-plan.json` |
| cases | facts、plan | `function-cases.json` |
| review | facts、plan、cases | `review.json` |
| delivery | cases、两个模板 | 两个 Excel |

独立性来自文件契约，不来自多 Agent。证据只在调试需要时可选生成，后续阶段完全不读取。

## Review 如何避免事后拒绝

- 事务记录前先校验完整的 checks，并将一个完整事务写成一条事件；中断恢复只处理残缺尾行，不把文件追加误称为严格事务原子性。
- 计划只能引用已存在的事实和检查点。
- 新事实编号由运行时生成；批次内使用局部引用，避免模型手写内部 ID。
- 用例只能使用计划中的 Case ID，并以 `action+expected+source_check` 配对保存；内部来源不写入 Excel。
- 计划和用例通过内部原子写入接口在生成当下完成结构约束，错误产物不会先落盘等待 Review 拒绝。
- Review 只检查 facts→plan→cases 的跨产物语义一致性。
- 状态只有 `ready`、`ready_with_notes`、`needs_local_fix`、`blocked_by_fact`。
- 问题只修当前 Case、当前功能或一个缺失事务，不全量回退、不自动循环。

## 用例规范

- 第一操作步骤使用完整菜单路径，例如“进入告警管理-告警列表”。
- 导航不逐级拆分，登录和权限可以写在前置条件中。
- 标题使用“功能点-具体场景”。
- 步骤与预期逐项配对，使用具体且脱敏的数据。
- 同一功能的用例连续排列。
- 公共导航可以重复，核心操作、数据和预期必须与场景对应。
- 不得出现截图要求、UID、DOM、选择器、事实编号或工具操作。

## 最小运行目录

```text
<run-dir>/
├─ events.jsonl
├─ facts.json
├─ case-plan.json
├─ function-cases.json
├─ review.json
└─ deliverables/
   ├─ 正式测试设计.xlsx
   └─ 测试系统导入.xlsx
```

产品事实默认不自动归档；只有用户明确要求时才把稳定、已确认且无敏感信息的事实增量写入共享资产。

## 内部诊断命令

正常运行由 Skill 自动完成。排查和恢复时可以使用：

```powershell
scripts/run-test-design.ps1 status --run-dir <run-dir>
scripts/run-test-design.ps1 plan-skeleton --run-dir <run-dir>
scripts/run-test-design.ps1 review --run-dir <run-dir>
scripts/run-test-design.ps1 deliver --run-dir <run-dir> --project-root .
```

项目自检：

```powershell
scripts/validate-test-design.ps1 -Mode Fast
scripts/validate-test-design.ps1 -Mode Full
```
