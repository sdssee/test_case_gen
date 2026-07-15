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
3. 输入框至少实测正常值；必填输入同时实测空值；页面声明格式或边界时分别实测无效格式及上下边界。有限选项逐项实际选择；需要按钮提交/执行才能产生结果时，每个检查必须包含选择、输入、触发和观察完整动作。CRUD 与配置项按手工操作完成提交、重开、实际生效、恢复或清理。配置只做单因素，不做组合。
4. 一个功能事务先在现场校验完整性，再立即作为一条事件记录多个 `checks`；每个检查用 `used_element_refs` 记录主验证和辅助使用的全部控件，并用 `input_class` 标记有效、空值、格式或边界分支。事务声明但未实际使用的控件、缺少输入分支、有限选项或CRUD/配置闭环时不写入。新事实 ID 由运行时分配，同批关系使用局部引用。完成一个页面或恢复检查点时才编译 `facts.json` 并形成一次分组完成摘要；恢复发现facts落后时自动重建，不要求手工补编译。
5. 从事实自动生成计划骨架，按元素级、事务级和功能级给出事实驱动的 DFX 提示。模型只补充Case意图，并在唯一 `check_assignments` 中把每个检查点分配给Case或performance、risk、not_applicable；其他引用和覆盖关系由系统派生。DFX在本次计划生成中考虑，不留到Review。
6. 严格按计划顺序生成用例，通过内部 `write-cases` 原子写入 `function-cases.json`。页面事实生成完整导航；模型只写action+expected，系统按分配顺序注入内部 `source_check` 并派生 `fact_refs`。预期使用结构化结果锚点校验，不要求复制事实原句。
7. 运行一次跨产物 Review。只允许修正受影响 Case、功能映射或一个确实缺失的页面事务；禁止全流程回退和自动循环。
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

内部执行只调用 `test_design_cli.py` 的 `record/checkpoint/write-plan/write-cases/review/deliver`；JSON负载使用 `--file`，不得生成临时 Python 编排脚本。`run-dir` 必须位于当前项目根目录内。
