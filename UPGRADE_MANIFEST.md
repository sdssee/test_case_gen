# 升级清单

本文件用于外网生成升级包和内网应用升级包时确认升级边界。普通框架升级只能更新规范、模板和脚本；不得覆盖内网业务资产。

## 版本

- framework_version: 2.0.0
- asset_schema_version: 2.0.0

## 升级类型

- 类型：框架与资产结构升级
- 是否需要资产迁移：是
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

2.0.0 将产品测试事实迁移到 `docs/test-assets/catalog/modules/*.json`，以 `product-map.xlsx` 作为可重建投影视图；升级会先从既有 Excel 迁移真实行，再启用分模块事实 upsert。框架同时拆出 I/O 与事实仓库领域模块，并新增 CI、升级回滚和一致性测试。

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
