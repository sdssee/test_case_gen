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
- `docs/ARCHITECTURE.md`
- `docs/AGENT_ORCHESTRATION.md`
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

3.0.0 直接采用最终多 Agent 架构，不提供旧/新模式切换：确定性编排器强制执行单 Discovery owner、Plan/DFX、条件 Risk Arbiter、按功能点 Case Worker、独立只读 Reviewer 和单写者 Delivery。所有 Agent 使用严格 AgentTask/AgentResult、冻结输入、隔离 workspace、source fingerprint、逐用例 traceability 和结构化返工；阶段顺序升级为 `discovery → plan → risk → cases → review → delivery`，Review Gate 通过前 `complete-deliverables` 不得产生交付副作用。既有批次使用 `init-batch-run --resume --product-name "<原产品名>"` 补齐最终架构目录；已有页面事实不会被伪造或覆盖，缺失 inventory、实例 ID、结果锚点、真实证据或 Review 时必须回到相应阶段。产品事实 schema 仍为 2.0.0，无需产品资产迁移。

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
