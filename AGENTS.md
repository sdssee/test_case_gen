# Codex Project Instructions

本项目是测试设计与测试用例生成规范包，不是传统应用代码项目。本文件只作为轻量入口；执行流程以 `.codebuddy/skills/test-design/SKILL.md` 为准，详细规则按阶段读取 `docs/test-design/rules/`。

## 核心目标

- 基于需求、用户故事、接口、截图、原型、可访问页面、缺陷或已有用例生成结构化测试设计。
- 正式测试设计以 `docs/test-design/codebuddy-test-design-template.xlsx` 为唯一结构与样式基线，只包含 7 个标准 Sheet。
- 每次正式交付复制 `docs/test-design/测试用例模板.xlsx` 生成独立测试系统导入文件，不修改原模板，不询问是否需要生成。
- 交付件只放项目根目录 `deliverables/`。

## 按阶段读取

任务入口只读取并遵守：

- `.codebuddy/skills/test-design/SKILL.md`
- `.codebuddy/.rules/test-design-rule.mdc`
- `docs/test-design/rules/README.md`

阶段规则只在进入对应阶段时读取，同一阶段不重复加载：

- 需求、Story、场景和用例：`case-design.md`
- 页面、截图、原型、浏览器或 computer use：`page-discovery.md`、`data-safety.md`
- 出现分页证据：追加 `docs/test-design/rules/pagination.md`
- 多菜单或超过一个最小标题：`batch-run.md`
- 原子场景核账后进入 DFX：`dfx-test-strategy.md`
- 写入或交付 Excel：`excel-deliverable.md`、`import-template.md`、`docs/test-design/excel-template-spec.md`

## 最高优先级摘要

- 正式测试设计默认使用中文；UI 文案、ID、URL、代码、协议及 API、UI、DFX、Mock 等必要术语保留原文。
- 粗遍历只定位模块、入口、依赖和深探范围。页面任务在首次浏览器操作前用 `init-discovery` 创建单份 `discovery-state.json` 动态队列；深探执行基线盘点、业务流程补充和最终复扫，进入 DFX 前必须通过 `validate-discovery`。
- 页面实探的真实状态变化必须走完 `触发元素 → 状态出现 → 状态内操作 → 终态动作 → 页面/数据结果`；已有数据只读，本次创建且带测试标识的数据只做任务范围内、可恢复且无外部影响的操作。
- 风险先分为页面/DOM可验证、测试数据可验证、外部条件依赖和业务理解/决策。可验证项默认补探，不得询问用户是否需要深探；外部不足记录需联调、待环境或缺权限。
- 待确认理解问题非空时展示编号清单并结束当前轮次；总览、Story 或风险表仍有待确认内容、风险状态为空或待确认时，不得进入 DFX、用例或 Excel 生成。
- 深探事实必须先按测试对象、角色/状态、动作/输入、数据、观察点和恢复路径形成并核账原子场景，再标记一个主 DFX。DFX 只能标记或补充，不得抽样删除不同事实或机械展开元素 × 12 × 4。
- 功能点作为父场景；用例从同一原子场景同步生成 `功能点-当前用例标题`，一次形成入口、`一级菜单-二级菜单-目标页面`、操作、终态、结果和恢复，再统一编号。环境快照不得成为通用前置或预期。
- 弹窗、抽屉、编辑、删除确认、动态行和下拉浮层必须形成真实闭环；步骤与预期按顺序对应，未实际观察的内容标记设计预期，不能虚构提示、数量或实现位置。
- 页面元素的 `发现方式` 必填且使用模板规定枚举；浏览器实探、computer use、代码/DOM 必须复用同一状态文件完成准出、事实去向和场景到用例映射。
- 非敏捷或未声明敏捷时不设固定用例数量；敏捷 Story 的数量要求见 Rule 和 `case-design.md`。分页按 `pagination.md` 的高成本例外执行。
- 写入前集中执行一次中文自查，统一检查标题、编号、入口、角色/状态、环境抽象、闭环、场景/用例映射和枚举，只修改唯一结构化数据源；不得把自动翻译写入 Excel 工具。
- 唯一草稿先执行 `preflight-deliverables` 集中预检，首次失败只允许集中修正一次；通过后执行唯一一次 `complete-deliverables`。工具硬限制最多两次预检和一次正式交付，失败即停止，不自动修改、重生成、逐单元格重试或手工降级生成。
- 通用 xlsx/表格 Skill 只用于读取、渲染和视觉检查；正式文件只复制模板并填充，保留第 2 行样式、下拉验证和自动行高。
- 中间 Python 建议小于 200KB，JSON/CSV/Markdown/TXT 建议小于 256KB；不得通过 `pip install`、`pip uninstall` 修改全局环境。
- 测试设计执行与交付流程不涉及 Git；不得为生成交付件初始化仓库或执行提交推送。仅当项目维护者明确要求维护源码版本时执行对应 Git 操作。
- 精确重复合并；相似用例告警必须在交付前分类。成功后按明确路径清理本批临时脚本、数据、中间 Excel、缓存和非交付截图，不新增清理器。

## 校验命令

项目稳定性自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

已有交付件独立审计；正常流程在 `complete-deliverables` 成功后不重复执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```

当前批次中间文件执行前：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```
