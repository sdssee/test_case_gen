# 升级清单

本文件用于外网生成升级包和内网应用升级包时确认升级边界。普通框架升级只能更新规范、模板和脚本；不得覆盖内网业务资产。

## 版本

- framework_version: 2.3.0
- asset_schema_version: 2.0.0

## 升级类型

- 类型：普通框架升级（批次账本兼容迁移）
- 是否需要资产迁移：仅当目标项目的 `asset_schema_version` 不是 `2.0.0` 时需要
- 迁移脚本：`scripts/migrations/1.0.0_to_2.0.0.ps1`

## 允许升级内容

- `AGENTS.md`
- `CODEBUDDY.md`
- `.github/`
- `.codebuddy/`
- `docs/ARCHITECTURE.md`
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

2.3.0 新增独立 `page-element-inventory.csv`，并为 discovery、逐选项、元素计划和生命周期增加稳定 `交互实例ID`；逐选项新增 `预期结果锚点`，生命周期按同一测试数据 ID 与创建 owner 用例绑定。证据必须是当前 run-dir `artifacts/` 内非空文件，静态截图按内容哈希去重，复制改名不能复用。同时增加未执行/数据不足退回 discovery、折叠测试实例编号后步骤和预期分别唯一、确定性 oracle、实探→计划→用例精确归属、状态分类计数派生、功能点单区块、001..N 非空连续分片、JSON→正式表→导入表确定性字段逐行有序一致，以及聚合/补丁脚本膨胀拦截。既有批次继续执行前使用 `init-batch-run --resume --product-name "<原产品名>"` 生成备份并补充空模板/空列；迁移不会伪造 inventory、实例 ID、结果锚点或证据，必须重新独立盘点、补录并把真实证据迁入当前 artifacts 后复核。产品事实 schema 仍为 2.0.0。

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
