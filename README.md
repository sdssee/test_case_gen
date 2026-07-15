# test_case_gen

测试设计规范包，用于让 CodeBuddy/Codex 按统一规则生成测试设计 Excel、测试系统导入文件，并维护内部产品测试资产。

本仓库不是业务应用代码。它提供可复制到业务项目根目录的 Memory、Skill、Rule、脚本和 Excel 模板。

当前执行架构是“单执行者 + 确定性工具”，不依赖平台提供并行或串行 Agent。模型负责理解页面和执行操作；脚本在操作前给出逐元素义务、在操作时自动留痕、在完成时沉淀结构化事实，阶段校验只作为最后保险。这避免 Agent 调用失败导致流程中断，也避免门禁到阶段末才集中拒绝。

## 核心入口

| 文件 | 作用 |
| --- | --- |
| `AGENTS.md` | Codex 项目级执行入口。 |
| `CODEBUDDY.md` | CodeBuddy 项目级 Memory。 |
| `.codebuddy/skills/test-design/SKILL.md` | 测试设计执行流程。 |
| `.codebuddy/.rules/test-design-rule.mdc` | CodeBuddy IDE 硬规则。 |
| `.codebuddy/rules/test-design-rule.md` | CodeBuddy Code/CLI 硬规则。 |
| `docs/test-design/codebuddy-test-design-template.xlsx` | 正式测试设计模板，固定 8 个 Sheet。 |
| `docs/test-design/测试用例模板.xlsx` | 测试系统导入模板，使用时复制副本，不修改原模板。 |
| `docs/test-assets/catalog/` | 按模块 JSON 保存的内部产品测试权威事实源。 |
| `docs/test-assets/product-map.xlsx` | 从 catalog 重建的 Excel 查询视图，不作为默认客户交付件。 |
| `docs/RULE_OWNERSHIP.md` | 规则归属矩阵，避免重复和漂移。 |

详细规则按任务读取 `docs/test-design/rules/`；Excel 字段以 `docs/test-design/excel-template-spec.md` 为准；归档和跨模块依赖以 `docs/test-design/archive-and-index-guidelines.md` 为准。

## 标准交付

正式测试设计只包含 8 个标准 Sheet：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计不新增 `测试系统导入用例` Sheet。需要导入测试系统时，复制 `docs/test-design/测试用例模板.xlsx` 生成独立导入文件。

## 主流程

推荐统一通过 `scripts/run-test-design.ps1` 运行工具。该入口会选择兼容运行时后调用 `scripts/test_design_excel_tools.py`：优先使用 `TEST_DESIGN_PYTHON`，其次使用 Codex 捆绑运行时，最后检查 PATH 中的 Python，并验证 `openpyxl==3.1.5`。独立环境可先执行 `python -m pip install -r requirements.txt`。

批次任务或页面实探任务先初始化批次目录：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 init-batch-run `
  --project-root . `
  --run-id <YYYYMMDD_任务标识_BATCH-001> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品/系统名称" `
  --batch-id BATCH-001
```

初始化后先采集 `page-element-inventory.csv`，然后循环执行以下实探闭环。`discovery-begin` 不传 ID 时自动领取下一项：

```powershell
scripts/run-test-design.ps1 discovery-next --run-dir <run-dir> --json
scripts/run-test-design.ps1 discovery-begin --run-dir <run-dir> --json
# 在页面工具中依次完成：操作前读取 → discovery-next 指定的真实操作 → 变化后读取
scripts/run-test-design.ps1 discovery-next --run-dir <run-dir> --json
scripts/run-test-design.ps1 discovery-complete --run-dir <run-dir> `
  --obligation-id <当前义务ID> `
  --before-record-id <操作前记录ID> `
  --mutation-record-id <真实操作记录ID> `
  --after-record-id <操作后记录ID> `
  --evidence-path <artifacts内证据> `
  --evidence-location <证据定位> `
  --before-state <操作前页面状态> `
  --executed-action <实际执行动作> `
  --observed-result <实际观察结果> `
  --recovery-result <恢复结果>
```

编辑、配置和状态变更义务需要重复传入 `--mutation-record-id`，依次绑定字段真实修改、保存/提交以及必要的重新进入动作；一个可修改元素只生成一个生效义务，但必须在该义务内同时证明保存回显、重新打开后的持久化和依赖功能实际生效。

CodeBuddy 通过 `.codebuddy/settings.json` 的 PostToolUse Hook 自动记录页面工具调用的顺序、类型和哈希，不保存原始页面内容或输入值。每次完成义务会自动更新 `interaction-branch-observations.csv`；元素类型中英文同义词会语义归一，修正单个元素只重算该元素未完成义务，不清空整阶段成果。

完成批次 JSON 分片后，优先使用真正的一站式组装与收口命令。该命令会从 manifest 和 7 个 Sheet JSON 生成 8 Sheet 正式工作簿，并同时写入 `current/`、`deliverables/`、内部归档和独立导入文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-test-design.ps1 complete-deliverables `
  --project-root . `
  --run-dir docs/test-assets/batch-runs/<任务_BATCH-001> `
  --module-path "一级模块>二级菜单>三级菜单" `
  --product-name "产品/系统名称" `
  --batch-id BATCH-001
```

如需排查组装问题，可先运行 `assemble-formal-workbook --run-dir <批次目录> --output <临时检查.xlsx>`；禁止在批次目录编写 `gen_excel.py` 之类脚本绕过标准组装器。

一个 run-dir 只允许一个最小标题批次和一行 `batch-status.csv`；下一叶子批次必须使用新的 `<任务>_<BATCH-ID>` 目录。初始化时显式传入 `--product-name`，工具会写入 `batch-scope.json`，后续收口即使省略参数也复用同一产品，传入冲突产品则拒绝，避免把一级模块误写成产品名。已存在同名批次时，初始化命令默认拒绝覆盖；继续原批次使用 `--resume`，重建使用 `--force-reinitialize`。`complete-deliverables` 只有在组装、资产同步和最终校验全部通过后才保留输出；它会自动回填 `batch-review.md` 完成行，失败会恢复工作簿、交付副本、账本、产品事实 catalog 和产品版图，成功后写入绑定 generation session 与文件 SHA256 的 `delivery-receipt.json`。

旧资产升级或排障时可使用 `migrate-product-facts`、`validate-product-facts`、`rebuild-product-map`；正常 `sync-product-map` 会自动 upsert 模块 JSON 并重建 Excel 视图。

只需要单独生成导入文件且不做批次收口时，可使用 `generate-import` 兼容命令。

交付文件名只使用菜单/模块路径，例如 `一级模块_二级菜单_三级菜单_测试设计.xlsx` 和 `一级模块_二级菜单_三级菜单_导入用例.xlsx`，不拼运行文件夹名、批次目录名或产品名。

## 关键规则

- 测试策略以 `DFX维度` 和 `DFX场景` 为主字段，`场景类型`、`正向/反向` 已废弃；详细矩阵见 `docs/test-design/rules/dfx-test-strategy.md`。
- DFX 是扩展检查矩阵，不是用例生成主轴；必须先按页面元素和交互路径建立覆盖骨架，再用 DFX 扩展功能、异常、边界、权限、状态、数据一致性、性能、风险和自动化建议。
- 页面实探前先从 DOM/可访问性树/trace/控件树独立采集 `page-element-inventory.csv`，再按稳定 `交互实例ID` 执行并写入 `page-discovery.csv`；两者必须双向一致。实探后沉淀 `element-case-plan.csv` 和 `test-data-lifecycle.csv`；`应生成用例数` 按元素类型 × DFX 最低预算计算，真实新增/编辑/删除形成测试数据生命周期闭环。
- 配置项保存类用例必须验证保存后回显和实际生效，不能只写点击保存或提示成功。
- 生成功能测试用例分片前先运行 `prepare-function-case-generation` 清理旧分片和旧 manifest；功能用例按功能点感知、每片 1–10 条生成从 `artifacts/data/function_cases_part_001.json` 开始连续无断号的三位编号分片，同功能点可容纳时不得跨片，并同步 `function_cases_manifest.json`。
- 功能用例 JSON 只允许标准字段，禁止 `用例编号`、`用侊 ID`、`用侊标题`、`场景类型`、`steps`、`expected`、英文模板或泛化占位文本；Excel 数据按 Sheet 分文件输出，避免单个脚本或 JSON 承载过多内容。
- 七个 Sheet JSON 必须使用目标 Sheet 的精确表头且至少有一个非空值；错误字段在 cases 门禁直接失败，不延迟到 Excel 组装阶段。
- 先运行 `pipeline-status` 获取事实派生的下一步；discovery 内先按 `discovery-next/begin/complete` 关闭逐元素义务，再按 `discovery → plan → risk → cases → delivery` 推进。风险只阻塞 risk/cases，不阻塞元素计划。没有模型不理解项时运行 `record-risk-none`，不伪造用户确认。
- 日常修改运行 `scripts/validate-test-design.ps1 -Mode Fast`；提交、CI 和发布运行 `-Mode Full`。
- `功能测试用例` 不写性能规格测试或 `DFP性能` 场景；性能、并发、大数据量、资源监控和极端压力进入 `性能测试设计`、风险或自动化建议。
- 选择类有限集合逐项实际选择并写入 `selection-option-observations.csv`；每项 `预期结果锚点` 取自真实页面变化、不能只填选项值，并进入关联用例预期。创建对象后续生命周期复用同一测试数据 ID 和创建 owner 用例，各行绑定对应 mutation plan 交互实例 ID。
- 页面可验证问题必须由模型自行操作，未完成时退回 discovery；不同用例在折叠测试实例编号后也不得复用相同操作步骤或相同预期结果，标题参数必须在步骤和预期中落地。
- 页面实探必须记录所有可点击、可输入、可选择、可测试元素；独立 inventory、实探、页面元素覆盖清单和产品版图必须一致。证据必须是当前 run-dir `artifacts/` 内的非空文件，静态截图按内容去重。
- 已有数据只能查看和只读深探；敏感操作只允许作用于本次创建且带测试标识的数据。
- 客户交付件放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。
- 内部资产归档到 `docs/test-assets/modules/`、`docs/test-assets/imports/` 和 `docs/test-assets/product-map.xlsx`。

## 校验

项目稳定性自检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design.ps1
```

交付件校验：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务追加 `-BatchStatusPath <batch-status.csv>`；有导入文件时追加 `-ImportWorkbookPath <导入文件.xlsx>`。如果同级存在 `page-discovery.csv`，校验会自动启用产品版图同步检查。

当前批次生成了 Python/JSON/CSV/Markdown/TXT 中间分片时，执行前先预检：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-generated-python-scripts.ps1 -Path docs/test-assets/batch-runs/<任务_BATCH-001>/artifacts/scripts
```

## 升级

外网生成升级包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/new-framework-upgrade-package.ps1
```

内网应用升级包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/upgrade-framework.ps1 -PackagePath <升级包>
```

升级默认保护 `docs/test-assets/`、`docs/test-design/current/`、`docs/test-design/deliverables/`，不会覆盖内网真实资产；保护清单标识为 `PROTECTED_ASSET_DIRS`。`VERSION` 中的 `framework_version` 表示框架版本，`asset_schema_version` 表示内部资产结构版本。详细流程见 `docs/UPGRADE.md`。

## 维护

- 规则变化先查 `docs/RULE_OWNERSHIP.md`。
- 模板字段变化同步 `docs/test-design/excel-template-spec.md` 和校验脚本。
- 修改完成后运行稳定性自检。
- 验证通过后按项目约定提交并推送到 `origin`。
