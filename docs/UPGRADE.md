# 外网到内网升级规范

本项目支持外网持续优化框架，再通过离线升级包更新内网项目。升级方式以脚本为主，手动确认为兜底。

## 升级边界

外网负责维护框架规范、模板、脚本和校验规则。内网负责沉淀真实产品版图、历史归档、导入副本和客户交付件。

普通框架升级可以更新：

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
- `scripts/`
- `README.md`
- `README_IMPORT.md`
- `requirements.txt`
- `pyproject.toml`
- `tests/`
- `VERSION`
- `UPGRADE_MANIFEST.md`

普通框架升级必须保护：

- `docs/test-assets/`
- `docs/test-design/current/`
- `docs/test-design/deliverables/`

标识：PROTECTED_ASSET_DIRS

## 版本判断

`VERSION` 中包含两个版本：

```text
framework_version=3.0.0
asset_schema_version=2.0.0
```

- `framework_version` 变化：通常表示规则、模板或脚本升级。
- `asset_schema_version` 变化：表示内部资产结构可能变化，需要检查是否执行迁移。

## 外网生成升级包

在外网项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\new-framework-upgrade-package.ps1
```

脚本会生成只包含框架文件的 zip 包，默认排除内网资产目录。

`docs/test-assets/batch-runs/README.md` 和 `docs/test-assets/batch-runs/templates/` 是框架模板资产，允许随普通框架升级进入升级包；真实批次运行目录和历史业务资产仍受保护，不得覆盖。

框架升级只更新批次模板，不批量改写历史运行目录；对需要继续执行的旧批次，使用 `init-batch-run --resume --product-name "<原产品名>"` 做单批兼容迁移并补齐 `batch-scope.json`。迁移前自动保留 `.pre-structured-ledger.csv` 或旧风险账本备份，新增结构化字段保持待复核状态，不伪造已执行事实。

2.1.0 起一个 run-dir 只能包含一个最小标题批次。旧目录若在 `batch-status.csv` 中保存多行批次，必须先按批次复制并拆分 ledgers、证据和产物到独立目录；框架会拒绝混装，不会猜测性自动拆分历史事实。

2.2.0 起有限选择集合必须补录 `selection-option-observations.csv` 的每个选项事实；旧批次恢复时工具只补齐模板和 scope，不会伪造逐项点击、页面变化或证据。原用例分片若存在重复正文、标题参数未落地、计划功能点串位或临时选择误判为持久化变更，必须回到 discovery/plan 后重新 prepare 和生成。

2.3.0 新增按角色/权限和数据状态采集的 `page-element-inventory.csv`；discovery、逐选项、元素计划和生命周期新增 `交互实例ID`，仅 page discovery 新增证据定位及通用步骤/结果锚点，逐选项新增非平凡 `预期结果锚点`。本次创建对象以同一测试数据 ID 和创建 owner 用例贯穿，各生命周期行使用对应 mutation plan 的交互实例 ID。状态计数按明确 DFX taxonomy 派生；分片从 001 非空连续；正式表与导入表确定性字段有序一致。`--resume` 只补空模板/列，不伪造事实；旧批次必须按角色和数据状态重新独立盘点、补录 ID/锚点，并把真实非空文件迁入当前批次作为证据。

3.0.0 直接启用最终多 Agent 架构，没有 legacy/optional/灰度模式。`init-batch-run` 会建立 `orchestration/` 与 `artifacts/agent-work/`；批次由确定性状态机按 `discovery → plan → risk → cases → review → delivery` 推进，Agent 只写隔离 workspace。新架构强制单 Discovery owner、Plan/DFX、条件 Risk Arbiter、按功能点 Case Worker、独立只读 Reviewer 与单写者交付，并使用冻结输入、source fingerprint、结构化返工和逐用例 traceability；输入、动态选择、分页和弹窗的必测分支统一记录到 `interaction-branch-observations.csv`。完整说明见 `docs/AGENT_ORCHESTRATION.md`。

从旧框架恢复的批次使用 `init-batch-run --resume --product-name "<原产品名>"` 补齐编排目录，不会把旧文件存在当作阶段已通过。已有事实缺少最终架构所需证据、交互 ID、generation session、traceability 或 Review 时，状态机会停在对应阶段；完成任务并提交严格 AgentResult 后才能继续。3.0.0 不改变 `asset_schema_version=2.0.0`，不需要迁移产品事实 catalog。

## 内网应用升级包

在内网项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\upgrade-framework.ps1 -PackagePath <升级包路径>
```

脚本会：

1. 检查升级包和升级清单。
2. 备份受保护目录。
3. 只复制允许升级的框架文件。
4. 跳过 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`。
5. 对比 `asset_schema_version`。
6. 执行稳定性校验。
7. 任一复制、迁移或校验步骤失败时，自动恢复全部被覆盖的框架文件、删除本次新增文件，并恢复受保护资产快照。

`AGENTS.md`、`CODEBUDDY.md` 和测试设计 Skill 的 `LOCAL-OVERRIDES` 区块会在升级时保留；旧入口若没有该区块，升级会在覆盖前中止，要求先人工迁移本地约束，避免静默丢失。

从 `asset_schema_version=1.0.0` 升级到 `2.0.0` 时必须传入 `-RunMigrations`。迁移会读取现有 `product-map.xlsx` 的非模板真实行，保存到 `docs/test-assets/catalog/modules/_legacy.json`，再建立 catalog 索引；不会用空 JSON 覆盖既有资产。

生成正式测试设计交付件后，可额外执行交付件质量校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

如果本次交付包含测试系统导入文件，升级后还应使用一站式收口工具重新抽样生成或校验导入副本，避免内外网 Python 临时脚本按列序号写入导致字段错位：

统一入口 `scripts/run-test-design.ps1` 会选择兼容 Python 后调用 `scripts/test_design_excel_tools.py`。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 complete-deliverables `
  --project-root . `
  --run-dir docs/test-assets/batch-runs/<任务_BATCH-001> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品/系统名称" `
  --batch-id BATCH-001

powershell -ExecutionPolicy Bypass -File scripts\validate-test-design-deliverable.ps1 `
  -WorkbookPath docs/test-design/current/<测试设计.xlsx> `
  -ImportWorkbookPath docs/test-assets/imports/<导入文件.xlsx>
```

大范围任务可追加 `-BatchStatusPath <batch-status.csv>` 校验批次状态中的覆盖数量和质量门禁。
升级到包含独立元素盘点、逐选项/交互分支账本、结构化计划和生命周期门禁的框架后，新批次必须通过 `init-batch-run --product-name` 生成 `batch-scope.json`、`page-element-inventory.csv`、`page-discovery.csv`、`selection-option-observations.csv`、`interaction-branch-observations.csv`、`element-case-plan.csv` 和 `test-data-lifecycle.csv`；旧批次使用 `--resume` 迁移，缺失 inventory、交互实例 ID、逐选项/交互分支事实或当前批次证据时必须重新实探，不应直接用空模板覆盖历史事实。
只需要单独生成导入文件且不做批次收口时，可使用 `generate-import` 兼容命令。

## 资产结构升级

以下情况属于资产结构升级：

- `product-map.xlsx` 新增 Sheet。
- `product-map.xlsx` 新增字段。
- 模块归档索引规则变化。
- 跨模块依赖记录方式变化。
- 可复用测试数据记录方式变化。
- 测试系统导入模板字段或枚举影响内部资产映射。

资产结构升级不能直接覆盖内网真实资产。必须新增迁移脚本，例如：

```text
scripts/migrations/1.0.0_to_1.1.0.ps1
```

迁移脚本必须随升级包一起提供。内网升级脚本会在复制框架文件前检查升级包中是否存在对应迁移脚本；如果未传入 `-RunMigrations`，或升级包缺少迁移脚本，脚本会停止且不复制文件。

迁移脚本必须读取旧资产，保留已有数据，缺 Sheet 就新增，缺字段就追加，并写入变更记录。

## 回滚

升级前脚本会把受保护目录备份到：

```text
.upgrade-backups/
```

如果升级失败，优先恢复备份目录，再检查 Git 状态。已经提交过的内网项目也可以通过备份分支或 Git 历史恢复。
