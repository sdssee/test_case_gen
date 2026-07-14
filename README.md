# test_case_gen

`test_case_gen` 是一套面向 CodeBuddy/Codex 的测试设计规范、确定性编排器和交付工具包。它不是业务应用，也不是单纯的 Excel 生成脚本；它用于把需求、产品事实和真实页面操作，经过可追踪的实探、计划、风险、用例和 Review，转换为标准测试设计、测试系统导入文件及可持续维护的产品测试资产。

项目重点解决四类问题：

- 页面实探不彻底：默认全量深探，有限下拉项逐项选择，输入、动态选择、分页、弹窗逐分支执行，创建与修改必须验证真实生效。
- 用例与页面事实脱节：以稳定的交互实例 ID 串联实探、元素计划、测试数据生命周期、用例和 Review。
- 多 Agent 输出不稳定：由确定性状态机控制任务领取、指纹、隔离写入、返工、合并与交付，Agent 只负责认知工作。
- Excel 与资产容易漂移：正式工作簿、独立导入文件、内部归档和产品事实由标准组装器一次事务性生成。

## 项目组成

项目由四层协同工作：

| 层 | 主要职责 | 权威入口 |
| --- | --- | --- |
| Skill | 告诉模型如何编排测试设计任务、何时加载专题规则、如何调用 Agent | `.codebuddy/skills/test-design/SKILL.md` |
| Rule | 定义不可绕过的硬门禁和各阶段详细质量标准 | `.codebuddy/rules/test-design-rule.md`、`docs/test-design/rules/` |
| Agent | 分工完成页面实探、计划/DFX、风险仲裁、用例编写和独立 Review | `.codebuddy/agents/` |
| 确定性工具 | 管理状态机、任务包、指纹、产物校验、Excel 组装、归档与升级 | `scripts/run-test-design.ps1`、`scripts/test_design_excel_tools.py` |

Codex 从 `AGENTS.md` 进入，CodeBuddy 从 `CODEBUDDY.md` 进入。二者都是轻量路由，不复制全部规则正文。规则归属与权威源见 `docs/RULE_OWNERSHIP.md`。

## Skill 如何工作

测试设计 Skill 是流程协调层，不自行降低 Rule 门禁。其主要职责是：

1. 识别任务范围，读取产品事实与需求，决定是否按最小标题拆分批次。
2. 按阶段延迟加载需要的专题规则，避免一次塞入全部文档导致上下文臃肿。
3. 推动 `discovery → plan → risk → cases → review → delivery`，为每个认知任务选择正确 Agent。
4. 只接受结构化 AgentResult，由确定性编排器判断阶段是否通过。
5. 在 Review 通过后调用单写者完成 Excel、导入文件和产品事实归档。

推荐正式入口：

```text
/test-design-run <run-dir>
```

`pipeline-status`、`agent-status` 和阶段校验命令用于诊断事实，不能绕过 Agent、Review 或交付门禁。

## 规则体系

`.codebuddy/rules/test-design-rule.md` 是硬门禁权威源，`.codebuddy/.rules/test-design-rule.mdc` 是同步镜像。详细规则由 `docs/test-design/rules/README.md` 路由：

| 场景 | 需要读取的规则 |
| --- | --- |
| 所有测试设计 | `case-design.md`、`excel-deliverable.md`、`data-safety.md`、`dfx-test-strategy.md` |
| 页面、截图、浏览器或 computer use | `page-discovery.md` |
| 全产品、大模块或多个菜单 | `batch-run.md` |
| 测试系统导入 | `import-template.md` |
| 历史用例、跨模块依赖和资产归档 | `product-map-sync.md` |

关键规则包括：

- 默认全量深探。有限集合逐项实际选择；输入、动态选择、分页、弹窗在 `interaction-branch-observations.csv` 中逐分支独立执行、恢复和关联用例。
- 页面可验证的问题由模型自行操作，不能转给用户规避实探；只有页面无法解释的外部业务语义才进入风险确认。
- 创建必须成功；配置项和所有修改项必须验证保存后回显、持久化和实际生效。既有数据只读，变更仅作用于带 `AI_TEST`、`CODEX_TEST` 或用户指定标识的本次测试数据。
- 先通过 `page-element-inventory.csv` 和实探建立元素骨架，再生成 `element-case-plan.csv` 与 `test-data-lifecycle.csv`。DFX 使用 `DFX维度`、`DFX场景` 做扩展检查矩阵；旧字段 `场景类型`、`正向/反向` 已废弃，性能规格测试和 `DFP性能` 不进入功能用例。
- 功能用例按功能点集中管理；每个 `function_cases_part_001.json` 等分片包含 1–10 条，同功能点不得跨片，`function_cases_manifest.json` 是唯一读取源。折叠测试实例编号后，不同用例的步骤和预期仍必须分别唯一、可判定。
- 正式测试设计只含 8 个标准 Sheet，导入文件从独立模板副本生成。正式交付只能由 `complete-deliverables` 一站式收口。

## Agent 架构

仓库注册 5 个认知 Agent，不注册 Delivery Agent：

| Agent | 阶段 | 职责 | 执行方式 |
| --- | --- | --- | --- |
| `test-discovery` | discovery | 单一页面事实 owner；全量实探、证据采集、安全 CRUD 和修改生效验证 | 串行 |
| `test-plan-dfx` | plan | 将实探事实转换为元素计划、数据生命周期和 DFX 评估 | 串行 |
| `test-risk-arbiter` | risk | 区分页面可验证问题与必须由用户确认的外部语义 | 条件串行 |
| `test-case-worker` | cases | 按单一功能点生成步骤、预期均有差异的功能用例 | 可按冻结波次并行 |
| `test-reviewer` | review | 独立只读检查实探、计划、用例、覆盖、隐私和交付条件 | 串行且身份独立 |

Delivery 由确定性单写者完成，避免多个 Agent 同时改写正式工作簿、catalog 或 deliverables。

Agent 只能读取冻结输入并写自己的隔离 workspace。每个任务都通过 `agent-claim` 绑定 `task_id`、`execution_id`、source fingerprint 和物理 sub-agent transcript；`agent-submit`、Review 与 Delivery 会重复验证。Review 未通过不得交付。

### 串行与并行

Discovery、Plan/DFX、Risk 和 Reviewer 始终串行。只有同一轮返回的 Case Worker 可以组成冻结 wave 并行执行；整波收齐后，成功结果按冻结顺序提交，失败或返工时先安全释放其余任务再提交控制结果。

如果 CodeBuddy 支持 sub-agent 但不支持后台并行，Case wave 仍先完整 claim，再按固定顺序逐个前台执行并暂存结果，收齐后统一决策。这样只降低吞吐，不降低质量。如果完全不支持 sub-agent，主会话只能做非正式诊断，不能进入正式 Review/Delivery。

CodeBuddy 项目 Agent 位于 `.codebuddy/agents/`。导入仓库不会在 IDE Agent 页面自动创建 5 个任务；新 CodeBuddy Code 会话中应分别执行 `/agents`、`/hooks` 完成注册和保护 Hook 预检，再执行 `/test-design-run <run-dir>`。完整适配边界见 `docs/CODEBUDDY_AGENT_ADAPTER.md`。

## 运行流程

### 1. 初始化最小标题批次

一个 run-dir 只允许一个最小标题批次。超过一个最小标题时，按最深标题级别逐批运行，禁止合并、禁止再拆分。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 init-batch-run `
  --project-root . `
  --run-id <YYYYMMDD_任务标识_BATCH-001> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品名" `
  --batch-id BATCH-001
```

批次目录位于 `docs/test-assets/batch-runs/`。已存在批次用 `--resume`，需要重建时显式使用 `--force-reinitialize`。

### 2. 推进确定性多 Agent 流程

```powershell
scripts/run-test-design.ps1 agent-run --run-dir <run-dir> --json
scripts/run-test-design.ps1 agent-status --run-dir <run-dir> --json
```

编排器生成隔离任务包，协调会话按角色完成 `agent-claim → sub-agent 执行 → agent-submit`。每个任务必须使用稳定唯一的 `execution_id`；领取没有自动超时重派，只有确认没有页面或数据副作用时才能通过 `agent-release --confirm-no-side-effects` 释放。

### 3. Discovery 页面实探

Discovery claim 前必须连接可操作当前页面的 MCP，并真实完成“前读 → 点击/选择/输入 → 变化后读”探针。snapshot、click、fill、select、navigate 等拆分工具需要逐个成功预探；未预探工具不授权。

正式实探维护元素清单、页面事实、有限选项、交互分支、证据和测试数据生命周期。页面事实不足、选项未逐项执行或修改未验证生效时，流程停留在 discovery。

### 4. Plan、Risk 与 Cases

Plan/DFX 基于冻结的实探事实生成覆盖预算。Risk 只处理模型仍不理解的外部语义；页面可验证项退回 Discovery。Case Worker 按功能点读取对应事实与计划，输出 `artifacts/data/function_cases_part_*.json`，并通过以下门禁：

```powershell
scripts/run-test-design.ps1 validate-batch-artifacts --phase discovery --run-dir <run-dir>
scripts/run-test-design.ps1 validate-batch-artifacts --phase plan --run-dir <run-dir>
scripts/run-test-design.ps1 validate-batch-artifacts --phase risk --run-dir <run-dir>
scripts/run-test-design.ps1 validate-batch-artifacts --phase cases --run-dir <run-dir>
```

### 5. 独立 Review

Reviewer 检查元素覆盖、交互闭环、步骤和预期唯一性、功能点连续区块、DFX 落位、敏感信息和二进制证据审计。发现问题时生成结构化返工请求，并使目标阶段及其后续阶段失效。

### 6. 一站式交付

状态进入 `DELIVERY_RUNNING` 后才能执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 complete-deliverables `
  --project-root . `
  --run-dir <run-dir> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品名" `
  --batch-id BATCH-001
```

命令会事务性生成正式工作簿、独立导入文件、current/deliverables 副本、内部模块归档和 catalog 事实，并写入交付 receipt。交付文件名只使用菜单/模块路径，不拼批次目录名或产品名。单独生成导入文件可使用 `generate-import`。

## 标准输出

正式测试设计固定包含：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

客户交付件位于 `docs/test-design/current/` 和 `docs/test-design/deliverables/`；内部事实归档到 `docs/test-assets/catalog/modules/`、`docs/test-assets/modules/`、`docs/test-assets/imports/`。`docs/test-assets/product-map.xlsx` 是可重建查询视图，不是事实权威源。

## 校验与维护

日常修改执行 Fast，提交、CI 和发布执行 Full：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1 -Mode Fast
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1 -Mode Full
```

交付件单独校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

升级包通过 `scripts/new-framework-upgrade-package.ps1` 生成，通过 `scripts/upgrade-framework.ps1` 应用。`framework_version` 表示框架版本，`asset_schema_version` 表示资产结构版本。升级脚本以 `PROTECTED_ASSET_DIRS` 保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`，不会覆盖真实业务资产；详细流程见 `docs/UPGRADE.md`。

模板字段、规则或交付逻辑变化前先查看 `docs/RULE_OWNERSHIP.md`，避免在 README、Skill、Rule 和专题文档之间重复维护完整规则。
