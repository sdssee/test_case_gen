# 页面深探与测试用例生成工具包

本项目让模型先像人工测试人员一样彻底操作页面，再把真实页面行为转换成可执行、可追溯、可直接导入测试系统的测试用例。重点是完整理解功能和配置效果，而不是机械增加用例数量。

## 项目作用

- 从 DOM、可访问性树和可见状态动态发现页面实际功能。
- 按实际交互类型操作页面元素，递归发现组合控件，验证有限选项、输入、状态切换及动态联动；图标控件必要时悬停识别后再安全点击。
- 对新增、编辑、删除和配置项完成提交、重开、实际生效与恢复闭环。
- 从实探事实识别独立功能，在用例编写前按适用 DFX 策略扩展场景。
- 生成同功能集中、步骤与预期逐项对应的功能测试用例。
- 同时交付正式测试设计 Excel 和独立测试系统导入 Excel。

## Skill、规则与运行方式

用户直接调用 `.codebuddy/skills/test-design/SKILL.md` 并提供测试目标。Skill 自动绑定或恢复内部运行目录，不需要用户执行初始化命令。执行可选择 4 个手动串行 Agent；CodeBuddy 无法调用 Agent 时，当前会话执行对应阶段 Skill，使用同一套 CLI 和质量规则继续。

规则分层：

- `.codebuddy/rules/test-design-rule.md`：不可违反的简明规则。
- `.codebuddy/agents/`：4 个可选的手动串行阶段 Agent。
- `.codebuddy/skills/test-*/SKILL.md`：Agent 与单会话降级共同使用的阶段 Skill。
- `docs/test-design/rules/README.md`：按当前阶段加载专题规则。
- `docs/test-design/rules/page-discovery.md`：连续页面深探。
- `docs/test-design/rules/dfx-test-strategy.md`：DFX 左移策略。
- `docs/test-design/rules/case-design.md`：计划和用例正文。
- `docs/test-design/rules/excel-deliverable.md`：双 Excel 交付。

页面深探始终保持一个连续浏览器上下文。Agent 仅用于隔离阶段上下文，由用户按顺序手动调用；不自动编排、不并行、不递归派生，也不依赖 Hook。Agent 不可用不会改变产物链或降低质量。

## 执行流程

```text
调用 test-design Skill 并理解范围
  → test-page-explorer（或 test-page-exploration Skill）
  → test-design-planner（或 test-design-planning Skill）
  → test-case-author（或 test-case-authoring Skill）
  → test-review-delivery（或 test-review-delivery Skill）
  → 返回正式测试设计.xlsx和测试系统导入.xlsx
```

扫描和事务不是两个割裂阶段。进入页面先扫描，发现元素后立即执行相关功能事务，页面变化后局部重扫；新元素动态加入当前或后续事务。最终全量扫描稳定且没有未处理元素时结束深探。

一个功能事务可以连续验证多个相关检查点，避免重复扫描页面。相关控件共同决定一次提交结果时，只按页面实际执行并观察到的有效关联分别形成 baseline Case；没有关联时，每个有限选项和每个有效输入等价类仍分别成例。空值、格式、边界等已声明分支分别形成独立 DFX Case，不做笛卡尔积。页面模型字段先经过通用归一化：常见输入、选择、触发、切换和容器控件使用统一语义，对象/字符串形式的输入类和选项得到相同结果，未知交互控件不能静默跳过。有效输入类在元素登记时由模型结合页面语义、需求参考和功能推断声明；必填输入同时验证空值，明确结构化格式或边界时验证对应DFX分支。需要按钮产生结果的检查必须包含完整触发动作；`branch_bindings` 只表达共同决定结果的分支，普通辅助使用不能替代独立覆盖。所有页面能力都使用同一套事实与计划规则；CRUD 和配置采用完整业务闭环，配置暂按单因素验证，不做组合爆炸。全部事务结束后只做一次全量扫描，确认没有新增或遗漏控件；后续若又执行事务，旧扫描自动失效。

框架不内置分页、搜索、弹窗或某个业务模块的专用 Case 模板。页容量、页码、翻页、跳页、刷新、列显隐等能力由DOM、可访问性语义、tooltip/hover和实际状态动态发现，出现什么就实测什么；功能名称、元素类型和事务类型都来自当前页面事实。

## 阶段相对独立

| 阶段 | 只读输入 | 唯一写入 |
| --- | --- | --- |
| discovery | 页面、需求、产品资料 | `events.jsonl`、`facts.json` |
| plan | facts、DFX规则 | `case-plan.json` |
| cases | facts、plan | `function-cases.json` |
| review | facts、plan、cases | `review.json` |
| delivery | cases、两个模板 | 两个 Excel |

独立性来自文件契约，而不是 Agent 内存。每个 Agent 只读上游固化产物并写自己的唯一产物；单会话降级也遵守同一边界。证据只在调试需要时可选生成，后续阶段完全不读取。

## 4 个阶段 Agent 与降级

| 顺序 | Agent | 对应 Skill | 完成标志 |
| --- | --- | --- | --- |
| 1 | `test-page-explorer` | `test-page-exploration` | `facts.json` 的 checkpoint ready |
| 2 | `test-design-planner` | `test-design-planning` | 所有事实功能与检查已进入计划 |
| 3 | `test-case-author` | `test-case-authoring` | 计划 Case 与已写 Case 一致 |
| 4 | `test-review-delivery` | `test-review-delivery` | Review 有效且两个 Excel 均生成 |

这些 Agent 不生成任务包、分片、观察 CSV 或返工队列。一个 Agent 失败时，继续在当前会话调用其对应 Skill；阶段输入、输出和校验完全相同，因此降级不会绕过规则。错误最多局部修正当前功能或 Case 一次，不形成自动重试风暴。

## Review 如何避免事后拒绝

- 元素登记时由 DFX 策略返回输入分支、有限选项和配置基线的精简实探清单；事务持续记录已完成事实，页面checkpoint只汇总剩余既定分支。触发控件和业务闭环仍在记录现场校验，恢复时自动重建落后的facts。
- 计划只维护唯一 `check_assignments`；fact_refs、元素覆盖和DFX关联由系统派生。
- 每个Case使用唯一 `verification_focus` 区分主验证对象、独立分支和可观察效果；缺省值由运行时从实探事实派生，避免只改标题不改步骤和预期。
- 新事实编号由运行时生成；批次内使用局部引用，跨批使用稳定 `client_ref` 精确合并，避免模型手写内部 ID 或重复发现。
- 模型只写 `action+expected`，系统自动注入内部source_check；结果锚点只约束明确tokens/value，不强迫目标、字段和观察原句逐字进入预期。
- 标准CLI是主入口，JSON使用 `--file` 传入；若命令工具不可用，只降级一次到同模块的 `execute_request`，两者共享完全相同的执行实现。不得直接调用底层函数，不生成临时Python编排脚本，run-dir不能逃逸项目根目录。
- PowerShell入口和Python CLI在首条命令前统一使用UTF-8输入输出；编码问题不得通过直接改写events、facts或临时修复脚本处理。
- 页面入口自动化层归一为UI；画像中的同类稳定性风险聚合为一个风险。性能按功能事实聚合：读取、写入、异步、批量传输和数据渲染形成基线/预期峰值/容量探索三档，没有SLA时记录实际基线而不发明阈值，只有本地静态功能可判不适用。非预期实探结果必须进入用例、风险或性能结论。
- Excel组装从最终用例和主验证目标派生场景观察点，使用计划中的真实风险和专项结论；元素覆盖以简短中文完整列出分支和实际交互，不截断为“另有N项”，并按实际列宽计算行高、校验跨Sheet一致性及无阻塞未覆盖元素。
- 计划和用例通过内部原子写入接口在生成当下完成结构约束，错误产物不会先落盘等待 Review 拒绝。
- 计划骨架在首次生成前提供实测动作、具体选项/输入类、稳定结果锚点、设计上下文和专项结论字段；Excel只组合这些上游内容，不自行补业务结论。
- discovery、plan、cases 的结构约束在各自写入现场完成；Review只复核跨产物指纹/映射并合并一次模型实际语义发现，不重复前序门禁，不接受模型自报通过清单，也不会自动循环返工。
- Review和状态使用业务语义指纹，时间戳、重新编译和JSON格式变化不会触发重复Review。
- 双Excel存在且Review仍有效时状态直接为 `completed`，恢复不会重复交付。
- 问题只修当前 Case、当前功能或一个缺失事务，不全量回退、不自动循环。

## 用例规范

- 模型只写业务步骤，运行时统一注入第一步完整实际菜单路径。
- 导航不逐级拆分，登录和权限可以写在前置条件中。
- 标题使用“功能名称-具体场景”；正式测试设计中的功能点使用Case级测试点。
- 步骤与预期逐项配对，使用具体且脱敏的数据。
- 同一功能的用例连续排列。
- 公共导航可以重复，核心操作、数据和预期必须与场景对应。
- 不得出现截图要求、UID、DOM、选择器、事实编号或工具操作。

## 最小运行目录

新运行固定在 `docs/test-design/current/<run-id>/`；相同目标恢复既有运行，历史运行仅在已有事实文件时原地续跑。

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
scripts/run-test-design.ps1 checkpoint --run-dir <run-dir>
scripts/run-test-design.ps1 plan-skeleton --run-dir <run-dir>
scripts/run-test-design.ps1 review --run-dir <run-dir> --file <semantic-review.json>
scripts/run-test-design.ps1 deliver --run-dir <run-dir> --project-root .
```

`<run-dir>` 可以使用短运行 ID，CLI 会解析为 `docs/test-design/current/<run-id>/`。交付回执返回两个 Excel 的完整路径。

项目自检：

```powershell
scripts/validate-test-design.ps1 -Mode Fast
scripts/validate-test-design.ps1 -Mode Full
```
