# 升级清单

本文件用于外网生成升级包和内网应用升级包时确认升级边界。普通框架升级只能更新规范、模板和脚本；不得覆盖内网业务资产。

## 版本

- framework_version: 3.0.0
- asset_schema_version: 2.0.0

## 升级类型

- 类型：最终多 Agent 编排架构（直接替换框架运行层）
- 是否需要资产迁移：仅当目标项目的 `asset_schema_version` 不是 `2.0.0` 时需要
- 迁移脚本：`scripts/migrations/1.0.0_to_2.0.0.ps1`

## 允许升级内容

- `AGENTS.md`
- `CODEBUDDY.md`
- `.github/`
- `.codebuddy/`
- `.codebuddy/settings.json`
- `.codebuddy/hooks/guard-agent-tool.py`
- `.codebuddy/hooks/record-page-probe.py`
- `FRAMEWORK_REMOVALS.json`（升级包生成的受限删除元数据，不写入目标仓库）
- `docs/ARCHITECTURE.md`
- `docs/AGENT_ORCHESTRATION.md`
- `docs/CODEBUDDY_AGENT_ADAPTER.md`
- `docs/RULE_OWNERSHIP.md`
- `docs/UPGRADE.md`
- `docs/test-design/*.md`
- `docs/test-design/*.xlsx`
- `docs/test-design/rules/`
- `docs/test-design/schemas/`
- `docs/test-assets/README.md`
- `docs/test-assets/batch-runs/README.md`
- `docs/test-assets/batch-runs/templates/`
- `scripts/`
- `README.md`
- `README_IMPORT.md`
- `requirements.txt`
- `pyproject.toml`
- `tests/`
- `tests/test_codebuddy_page_probe_recorder.py`
- `tests/test_page_probe_receipts.py`
- `tests/test_codebuddy_agent_guard.py`
- `VERSION`
- `UPGRADE_MANIFEST.md`

## 受保护目录

以下目录是内网业务资产区，普通框架升级包不得包含真实业务数据，应用升级包时不得覆盖或删除：

- `docs/test-assets/`
- `docs/test-design/current/`
- `docs/test-design/deliverables/`

标识：PROTECTED_ASSET_DIRS

## 资产结构迁移判断

当 `asset_schema_version` 变化，或 `product-map.xlsx` 的 Sheet、字段、索引规则发生变化时，属于资产结构升级。资产结构升级必须通过迁移脚本读取旧资产并增量补齐，不能用空模板覆盖内网真实资产。

## 升级后校验

3.0.0 直接采用最终多 Agent 架构，不提供旧/新模式切换：确定性编排器强制执行单 Discovery owner、Plan/DFX、条件 Risk Arbiter、按功能点 Case Worker、独立只读 Reviewer 和单写者 Delivery。所有 Agent 使用严格 AgentTask/AgentResult、冻结输入、隔离 workspace、source fingerprint、逐用例 traceability 和结构化返工；任务必须经 `agent-claim` 绑定唯一 `execution_id` 后执行，不设置会重放 CRUD 的自动租约，Reviewer 执行身份必须独立。`codebuddy-subagent` 是当前唯一可认证的正式执行身份；`codebuddy-main-session`、`external-session` 与 Agent Team 均只允许诊断降级，任何成功生成链使用这些身份都会阻断 Reviewer 与正式交付；没有可用 sub-agent 时不得继续正式流程。阶段顺序升级为 `discovery → plan → risk → cases → review → delivery`，Review Gate 通过前 `complete-deliverables` 不得产生交付副作用。CodeBuddy 项目适配随 `.codebuddy/agents/`、`.codebuddy/commands/test-design-run.md`、`docs/CODEBUDDY_AGENT_ADAPTER.md` 和 `docs/RULE_OWNERSHIP.md` 一并升级；不支持后台并行时串行降级，不注册 Delivery Agent。既有批次使用 `init-batch-run --resume --product-name "<原产品名>"` 补齐最终架构目录；已有页面事实不会被伪造或覆盖，缺失 inventory、实例 ID、结果锚点、真实证据或 Review 时必须回到相应阶段。产品事实 schema 仍为 2.0.0，无需产品资产迁移。

安装器不会覆盖已有 CodeBuddy 本地配置：`.codebuddy/settings.json` 采用结构化合并，保留 `permissions`、其他顶层配置与其他 hooks，并把旧/重复 guard command 收敛为升级包中的一个当前 guard。升级包根目录 `FRAMEWORK_REMOVALS.json` 只允许精确声明删除 legacy `.codebuddy/agents/test-delivery.md` 普通文件；settings 非法、删除路径异常或后续验证失败时，事务回滚会恢复合并前 settings、被删除 legacy 文件及其他框架/受保护资产。

外网生成升级包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\new-framework-upgrade-package.ps1
```

内网应用升级包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\upgrade-framework.ps1 -PackagePath <升级包路径>
```

校验命令：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-test-design.ps1
```
