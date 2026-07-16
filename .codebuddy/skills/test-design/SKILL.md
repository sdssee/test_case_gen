---
name: test-design
description: 在一个连续会话中完成页面全量深探、事实编译、DFX 左移规划、测试用例编写、轻量审计与双 Excel 交付。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# 测试设计执行 Skill

## 直接开始

用户调用本 Skill 后直接理解测试范围并进入页面扫描。运行目录的创建、匹配和恢复属于内部引导，不向用户暴露初始化步骤：相同目标恢复未完成运行，不同目标使用新运行目录。

## 连续执行流程

1. 在同一浏览器上下文扫描 DOM、可访问性树和可见状态，动态识别页面实际能力。
2. 扫描与功能事务交替：操作后局部重扫，结构明显变化或结束时全量重扫；新元素加入当前或后续事务。
3. 先记录扫描发现的元素；模型根据页面语义、需求参考和自身推断，在登记输入元素时给出 `valid_input_classes`，运行时连同必填、格式、边界、有限选项和配置基线生成精简的 `exploration_requirements`。交互前读取该清单：每个有效等价类、每个有限选项分别实测；必填输入实测空值；页面声明格式或边界时实测相应分支。需要按钮提交/执行才能产生结果时，每个检查必须包含选择、输入、触发和观察完整动作。CRUD 与配置项按手工操作完成提交、重开、实际生效、恢复或清理；配置只做单因素，不做组合。
4. 一个功能事务完成一个或多个已声明分支后立即记录 `checks`；`element_ref` 是主验证控件，`used_element_refs` 记录主验证和辅助使用的全部控件。辅助使用不能完成另一控件的独立分支。用 `input_class`、`option_value`、可选 `action_tokens` 与结构化结果锚点固化实际动作和稳定观察点；加载、禁用、成功、失败、超时、恢复等已观察状态直接写在同一事务，不新增状态文件或执行轮次。运行时只在写入现场校验引用、触发动作和CRUD/配置闭环，不因尚有分支未执行而拒绝已完成事实；checkpoint只汇总剩余既定分支。新事实 ID 由运行时分配，同批关系使用局部引用。完成一个页面或恢复检查点时才编译 `facts.json`；更新保留首次发现顺序，恢复发现facts落后时自动重建。
5. 从事实自动生成计划骨架，完整带入动作、具体选项/输入类、稳定结果锚点及已观察状态。模型为每个功能一次填写紧凑 `design_context` 和功能级 `automation_profile`，补充Case意图，并在唯一 `check_assignments` 中分配每个检查点。每个有限选项和每个有效输入等价类形成独立 baseline Case，不合并、不默认笛卡尔积；DFX在本次计划生成时扩展。性能和风险适用时写结构化场景，不适用时写真实原因，Excel不得自行补业务结论。计划按功能 upsert 到同一文件。
6. 严格按计划顺序生成用例，通过内部 `write-cases` 按功能 upsert 到同一 `function-cases.json`。页面事实生成完整导航；模型只写action+expected，并为每条用例填写轻量 `automation_value`、`automation_priority` 和必要的局部覆盖。系统按分配顺序注入内部来源并派生事实引用。动作必须保留实测选项/输入，预期使用稳定结果锚点，不复制偶发数值。重复提交相同功能内容时直接成功且不重复写入。
7. 模型对当前 facts、plan、cases 的紧凑投影执行一次语义 Review，并把覆盖的Case顺序、八类语义判断、问题和明确局部修正作为 `review --file` 负载；运行时合并确定性检查写入 `review.json`。缺少语义Review不得交付；只允许修正受影响 Case、功能映射或一个确实缺失的页面事实，禁止全流程回退和自动循环。
8. 从同一 `function-cases.json` 独立生成正式测试设计 Excel 和测试系统导入 Excel。

## 页面实探完成条件

- 初始及动态发现的安全可操作元素均已处理或记录真实阻塞。
- 有限选项已逐项选择并观察差异。
- 输入类的正常、必填空值及页面明确支持的格式/边界分支已实际触发并观察。
- CRUD、普通编辑项和单因素配置形成真实效果闭环。
- 最终扫描稳定，且没有未处理元素。
- 页面可验证内容已自行操作；仅外部业务语义汇总询问用户一次。

工具瞬时错误最多重试一次；真实业务失败记录一次；缺少权限、数据或环境时记录 `open_items` 并继续不受影响的功能。

## 阶段写入边界

- discovery：只写 `events.jsonl`，页面checkpoint时刷新 `facts.json`。
- plan：只读 facts，只写 `case-plan.json`。
- cases：只读 facts 与 plan，只写 `function-cases.json`。
- review：只读上游，只写 `review.json`。
- delivery：只读结构化用例，只写两个 Excel。

阶段独立依靠固化产物，不依赖 Agent、Hook、分片或会话记忆。详细契约读取 `docs/test-design/rules/README.md` 指向的当前专题规则。

内部执行只调用 `test_design_cli.py` 的 `record/checkpoint/write-plan/write-cases/review/deliver`；JSON负载以 `test-design-` 前缀写入系统临时目录，CLI读取后自动删除，不得在run-dir生成负载文件或临时 Python 编排脚本。新运行固定在 `docs/test-design/current/<run-id>/`，历史运行仅在已存在事实文件时原地恢复。`status` 只在恢复时内部读取一次；两个Excel存在且Review仍有效时直接返回完成态，不重复交付。
