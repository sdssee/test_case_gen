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
3. 先记录扫描发现的元素及所属功能；运行时统一为 `input/select/trigger/toggle/container`，并区分 `actionable/disabled/container/display_only`。组合控件展开后递归登记子控件；图标按钮先读可见文案、aria、title或tooltip，仍不明确才悬停并局部重扫，悬停只识别语义，安全点击并观察后才算覆盖。分页、页容量、翻页、跳页、刷新、列显隐等能力完全以当前页面实际发现为准，不使用固定按钮清单。无法分类的交互控件、未实际展开取得选项的选择控件必须先补充页面事实。模型根据页面语义、需求参考和自身推断声明 `valid_input_classes`；运行时连同必填、格式、边界、有限选项和配置基线生成精简 `exploration_requirements`，负向类不重复生成baseline。每个有效类、有限选项及明确DFX分支分别实测。相关控件共同改变一次提交效果时只执行页面实际支持的有效关联，不做全量组合。CRUD与配置按手工操作完成提交、重开、实际生效、恢复或清理；配置只做单因素。
4. 一个功能事务完成一个或多个已声明分支后立即记录 `checks`；`element_ref` 是主交互控件，`used_element_refs` 记录全部参与控件。只有共同决定本次实测结果的控件值进入可选 `branch_bindings`；常见的唯一输入类与唯一有限选项由运行时从 `input_class`、`option_value` 自动归一，存在多个同类控件时才显式绑定。一个真实关联检查可以同时完成其绑定分支，普通辅助使用仍不能完成另一控件的独立分支；各独立关系必须来自真实执行且不能复用同一物理动作冒充。用结构化结果锚点固化稳定观察点；加载、禁用、成功、失败、超时、恢复、实测耗时及非预期结果直接写在同一事务，不新增状态文件或执行轮次。非预期结果下游必须进入用例、风险或性能结论，不能静默标记不适用。运行时只在写入现场校验引用、触发动作和CRUD/配置闭环，不因尚有分支未执行而拒绝已完成事实；checkpoint只汇总剩余既定分支。新事实 ID 由运行时分配，同批关系使用 `local_ref`；跨批恢复使用稳定 `client_ref`，精确命中后合并更新，不做模糊去重。调用方误把 `fact_id/status/client_ref/local_ref` 放进 `data` 时运行时在落盘前提升到事件外层，冲突则一次性拒绝，不得产生幽灵事实。完成页面操作后再执行一次全量扫描，明确记录未处理元素为空，随后才编译 `facts.json`；相同扫描重复提交会吸收，新事务发生后必须重新扫描。更新保留首次发现顺序，恢复发现facts落后时自动重建。
5. 从事实自动生成计划骨架，带入动作、关联值、稳定结果锚点、Case级测试点和主验证建议。每个默认选项只在一个有效关联中先切换再切回；只有独立状态效果才另成Case。模型为每个功能一次填写紧凑 `design_context`、`automation_profile` 和可确认的 `interaction_profile`；遗漏的机械性一对一 `check_assignments` 由运行时无歧义补齐，DFX策略直接继承实探分支。每个实测有效关联、有限选项和有效输入等价类分别形成baseline Case；空值、格式、边界等已实探分支分别形成DFX Case，不做笛卡尔积。性能按功能事实聚合：网络读取、写入、异步、批量传输和数据渲染形成基线、预期峰值、容量探索三档；没有SLA时记录实际P50/P95/P99而不发明阈值，只有本地静态交互可判不适用。计划按功能upsert到同一文件。
6. 严格按计划顺序生成用例，通过内部 `write-cases` 按功能upsert到同一 `function-cases.json`。模型只写业务action+expected及轻量自动化字段；运行时注入完整菜单导航、计划标题、Case级测试点、主验证和事实引用。机械步骤缺失时直接从已实探检查编译，CRUD/配置的提交、重开、实际效果和恢复结论并入该用例，不留到Review补写。动作保留具体选项/输入和触发；预期只使用同一检查的稳定结果，不接受“可能”“成功或失败”等不确定结论，不复制偶发样本。精确重复提交直接吸收。
7. 模型对 facts、plan、cases 的紧凑投影执行一次语义Review，集中检查 `cases/performance/risks/automation/elements/cross_sheet`。结构规则已在各阶段写入现场完成；Review只复核跨产物指纹/映射和语义，不重复运行 discovery、plan、cases 门禁，不新增工作。缺少语义Review不得交付；只允许明确的局部修正，禁止全流程回退和自动循环。
8. 从同一 `function-cases.json` 独立生成正式测试设计 Excel 和测试系统导入 Excel。

## 页面实探完成条件

- 初始及动态发现的安全可操作元素均已处理或记录真实阻塞。
- 有限选项已逐项选择并观察差异。
- 输入类的正常、必填空值，以及根据页面语义、需求参考或功能推断可以明确的格式/边界分支已在登记时声明并实际触发观察。
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

内部执行只调用 `test_design_cli.py` 的 `record/checkpoint/write-plan/write-cases/review/deliver`；JSON负载以 `test-design-` 前缀写入系统临时目录，CLI读取后自动删除，不得在run-dir生成负载文件或临时 Python 编排脚本。若外层命令工具不可用，只允许降级一次为同一模块的 `execute_request` 入口，使用完全相同的校验、写入和返回契约；不得直接调用底层运行时函数。新运行固定在 `docs/test-design/current/<run-id>/`，历史运行仅在已存在事实文件时原地恢复。`status` 只在恢复时内部读取一次；两个Excel存在且Review仍有效时直接返回完成态，不重复交付。
