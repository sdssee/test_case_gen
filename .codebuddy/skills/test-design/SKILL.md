---
name: test-design
description: 基于需求、页面实探和既有资产生成测试设计 Excel、测试系统导入文件及页面覆盖记录。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# CodeBuddy Skill：测试设计轻入口

本 Skill 只保存执行顺序、阶段路由和停止条件；详细判断以 `docs/test-design/rules/` 和模板规范为准。

## 阶段按需路由

每次任务入口只读取：

1. `.codebuddy/.rules/test-design-rule.mdc`
2. `docs/test-design/rules/README.md`

进入对应阶段时追加读取一次，同一阶段连续执行不重复加载：

- 需求理解、Story、场景和用例：`docs/test-design/rules/case-design.md`
- 页面、截图、原型、浏览器或 computer use：`page-discovery.md`、`data-safety.md`
- 出现分页证据：`pagination.md`
- 多菜单或超过一个最小标题：`batch-run.md`
- 原子场景核账后进入 DFX：`dfx-test-strategy.md`
- 写入或交付 Excel：`excel-deliverable.md`、`import-template.md`、`docs/test-design/excel-template-spec.md`

## 阶段规则加载

- 进入首次深探或定向补探前重新读取 `page-discovery.md`；发现分页证据后，在操作分页控件前读取一次 `pagination.md`。
- 深探准出后、形成原子场景和用例前重新读取 `case-design.md`；原子场景核账后、标记 DFX 前读取 `dfx-test-strategy.md`；写入 Excel 前读取交付规则。
- 返回上一阶段或出现新的专项证据时才重新读取。不记录没有执行约束力的“已加载”标记，以状态证据和产物为准；阶段切换不新增用户确认、中间文件、生成轮次或自动重试。

## 标准工作流

1. **识别任务**：确定输入类型、范围、交付语言和是否属于补充任务。正式说明性内容默认中文。
2. **粗遍历定位**：只确认模块位置、入口、导航、依赖和深探范围；多标题按 `batch-run.md` 建立最小标题队列，不枚举元素或推断行为。
3. **页面深探**：第一次浏览器操作前用 `init-discovery` 创建并只维护一份临时 `discovery-state.json`。按页面区域建立语义目标，已发现不等于已验证；新状态用 `parent_id` 追加目标，相同目标去重。真实状态变化执行 `触发 → 状态出现 → 状态内操作 → 终态动作 → 页面/数据结果`。分页按专项规则拆分实际能力。
4. **风险分流**：页面/DOM和安全测试数据可验证项默认补探；接口、日志、环境、权限或联调不足记录客观状态。只有需求含义、业务规则、范围边界、角色职责或预期结果需要决策时列待确认。待确认非空时展示编号清单并结束当前轮次；用户补充产生新目标时返回定向补探。
5. **深探准出**：最终复扫新增目标为 0、队列无待执行/执行中目标、待确认为空后运行：

   ```powershell
   python scripts/test_design_excel_tools.py validate-discovery --discovery-state <discovery-state.json>
   ```

   失败时一次处理完整报告并继续对应目标，不自动重试。固定选项记录 `discovered_values` 和分支策略；事实填写场景、风险、性能或不适用去向。
6. **Story 与原子场景**：重新读取 `case-design.md`，先按来源或独立业务价值与验收结果拆解并冻结 Story；再把事实抽象为角色/状态、动作/输入、数据、观察点和恢复路径并逐项核账。此时同步判定入口：默认使用“已认证后的菜单/页面导航”，只有测试对象本身属于登录、重新认证、会话、退出登录或 URL 直达时才使用“会话/直达入口”。不得用页面元素、测试点或 DFX 反向增加 Story，不直接复制环境快照。
7. **DFX 评估**：事实核账后读取 `dfx-test-strategy.md`，为每个原子场景标记一个主 DFX，并补充适用质量场景。DFX 不得抽样、删除或合并不同事实；新产生理解问题时停止等待用户。
8. **用例设计**：以功能点为父场景连续编排。生成每条用例前先沿用原子场景的入口判定：普通功能用例把登录和权限写入前置条件，第一条操作直接写已认证后的 `一级菜单-二级菜单-目标页面`；会话/直达入口用例才生成浏览器、登录或 URL 动作。入口确定后再从同一原子场景一次生成 `功能点-当前用例标题`、操作、终态、结果和恢复，不得先生成登录步骤再等待校验删除。数据变更先形成一条完整成功主流程，再按有效值、边界、异常、只读和取消分支展开，不做字段笛卡尔积；完成后统一编号，每条用例独立造数和清理。
9. **写入前集中自查**：一次检查标题、编号、入口、特殊角色/状态、环境抽象、数据变更主流程、逐项值、临时交互闭环、步骤预期、场景/用例映射和枚举，只修正唯一结构化数据源。入口检查只审计生成阶段是否遗漏，不在校验阶段决定入口类型或自动删除、插入步骤。页面任务补全同一状态文件的 `scenario_case_mapping`。
10. **一次交付**：唯一草稿只调用 `complete-deliverables`，由命令在临时文件中集中校验并在通过后立即原子交付；页面任务传入同一 `--discovery-state`。首次失败只允许集中修正唯一数据源一次并再次调用，工具硬限制最多两次校验尝试和一次成功交付；失败停止，不自动修改、重生成或叠加 `fix_*`。

## 不可违反的停止条件

- 总览、Story、风险表或状态文件仍有待确认问题，或风险状态为空、待确认时，停止并等待用户。
- 页面存在可操作的待实探目标、最终复扫仍产生新目标或分页专项未准出时，不得进入 DFX。
- 未完成事实核账不得标记 DFX；未完成场景到用例映射不得交付。
- 页面已有数据只读；本次创建的测试数据只做范围内、可恢复且无真实权限、通知或外部影响的操作。
- 未实际观察的行为只能写设计预期；环境账号、现存数量、运行日期和当前页状态不得直接成为通用用例断言。
- 普通功能用例将已登录及角色权限写入前置条件，操作步骤直接从已认证后的菜单/页面入口开始，导航使用 `一级菜单-二级菜单-目标页面`；不重复打开浏览器、访问登录地址或执行登录。只有登录、重新认证、会话或URL直达测试才保留相应入口步骤。UI 名称不加括号或引号；弹窗、抽屉、动态行、下拉和删除确认必须形成真实闭环。
- 正式 Excel 只复制模板并填充内容，保留第 2 行样式、下拉验证和自动行高；通用 xlsx Skill 只用于读取、渲染和视觉检查。
- 不得通过 `pip install`、`pip uninstall` 修改全局环境；中间文件按专题规则小分片并预检。
- `complete-deliverables` 失败后不得手工降级或循环生成；成功后按本批明确路径清理临时文件，不新增清理器。
- 测试设计执行不初始化或维护 Git；项目维护者明确要求源码版本维护时除外。

## 校验

项目自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

已有交付件独立审计；正常流程在 `complete-deliverables` 成功后不重复校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```

中间文件执行前：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```
