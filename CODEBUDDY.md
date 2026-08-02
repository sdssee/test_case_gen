# CodeBuddy 项目级 Memory：测试设计规范包

本项目是测试设计规范包，不是传统应用代码。执行时以 `.codebuddy/skills/test-design/SKILL.md` 为流程入口，以单份 Rule 为硬门禁，专题规则按阶段加载。

## 交付目标

- 正式测试设计只包含 7 个标准 Sheet，以 `docs/test-design/codebuddy-test-design-template.xlsx` 为唯一基线。
- 每次正式交付复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件，不修改原模板、不询问是否生成。
- 正式测试设计和导入文件只放项目根目录 `deliverables/`。

## 阶段按需读取

每次任务入口只读取 Skill、`.codebuddy/.rules/test-design-rule.mdc` 和 `docs/test-design/rules/README.md`。进入阶段时追加读取一次：

- 需求、Story、场景和用例：`case-design.md`
- 页面实探：`page-discovery.md`、`data-safety.md`
- 分页证据：`pagination.md`
- 多菜单或多个最小标题：`batch-run.md`
- 原子场景核账后的 DFX：`dfx-test-strategy.md`
- Excel 写入交付：`excel-deliverable.md`、`import-template.md`、`excel-template-spec.md`

## 执行门禁摘要

- 正式内容默认中文；真实 UI 文案、ID、URL、代码、协议和必要技术术语保留原文。
- 粗遍历只确定模块位置、入口、依赖和深探范围。页面任务在首次浏览器操作前用 `init-discovery` 创建并只维护一份 `discovery-state.json`；基线扫描、动态深探、最终复扫和 `validate-discovery` 全部通过后才能进入 DFX。
- 可页面/DOM或安全测试数据验证的风险默认定向补探，不向用户询问是否继续；接口、日志、环境、权限和联调不足记录客观状态。只有需求含义、业务规则、范围边界、角色职责或预期结果需要决策时才列待确认。
- 总览、Story、风险表或状态文件仍有未关闭待确认问题时，展示编号清单并结束当前轮次，不得生成场景、用例或 Excel。
- 页面真实状态变化必须走完触发、状态出现、状态内操作、终态动作、页面/数据结果。已有数据只读；本次创建的测试数据只做可恢复、无外部影响的操作。
- 页面元素 `发现方式` 使用固定枚举；浏览器实探、computer use、代码/DOM 必须关联同一深探状态文件。每项事实有场景、风险、性能或不适用去向，生成场景必须映射真实用例 ID。
- 深探事实先按测试对象、角色/状态、动作/输入、数据、观察点、恢复路径形成原子场景，再标记主 DFX。禁止用 DFX 抽样删除事实或机械展开元素 × 12 × 4。
- 以功能点为父场景，形成原子场景时先判定入口，默认使用已认证后的菜单/页面导航，仅登录、重新认证、会话、退出登录或 URL 直达测试使用会话/直达入口；用例据此同步形成标题、步骤、终态、结果和恢复。普通功能用例将登录状态写入前置条件，不得先生成浏览器或登录步骤再依靠校验删除。环境快照转换成业务角色、当前用例造数和相对断言。
- 数据变更采用一条成功主流程加必要字段分支；离散有效值按规则逐项落用例，不做字段笛卡尔积。弹窗、抽屉、动态行、下拉和删除确认必须闭环。
- 非敏捷任务不设固定用例数量；分页完整覆盖按 `pagination.md` 执行。未实探行为只能写设计预期，不能写成确定页面反馈。
- 写入前只做一次集中自查并修正唯一结构化数据源。唯一草稿只调用 `complete-deliverables`，首次校验失败只集中修正一次并再次调用；工具硬限制最多两次校验尝试和一次成功交付，失败停止，不自动修改、循环重试、重生成或手工降级。
- 正式 Excel 只复制模板并填充；通用 xlsx Skill 只读、渲染和视觉检查。导入执行方式默认手动。
- 禁止 `pip install`、`pip uninstall` 修改全局环境；中间文件按规则小分片并预检。
- 测试设计执行不初始化或维护 Git；项目维护者明确要求源码版本维护时除外。交付后仅清理本批明确临时文件。

## 命令

项目自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

已有交付件独立审计；正常流程在 `complete-deliverables` 成功后不重复运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```

中间文件预检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path <artifacts/scripts>
```
