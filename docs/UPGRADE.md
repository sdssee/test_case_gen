# 外网到内网升级规范

本项目支持外网持续优化框架，再通过离线升级包更新内网项目。升级方式以脚本为主，手动确认为兜底。

## 升级边界

外网负责维护框架规范、模板、脚本和校验规则。内网负责沉淀真实产品版图、历史归档、导入副本和客户交付件。

普通框架升级可以更新：

- `AGENTS.md`
- `CODEBUDDY.md`
- `.codebuddy/`
- `docs/ARCHITECTURE.md`
- `docs/UPGRADE.md`
- `docs/test-design/*.md`
- `docs/test-design/*.xlsx`
- `docs/test-assets/README.md`
- `scripts/`
- `README.md`
- `README_IMPORT.md`
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
framework_version=1.1.0
asset_schema_version=1.0.0
```

- `framework_version` 变化：通常表示规则、模板或脚本升级。
- `asset_schema_version` 变化：表示内部资产结构可能变化，需要检查是否执行迁移。

## 外网生成升级包

在外网项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\new-framework-upgrade-package.ps1
```

脚本会生成只包含框架文件的 zip 包，默认排除内网资产目录。

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

迁移脚本必须读取旧资产，保留已有数据，缺 Sheet 就新增，缺字段就追加，并写入变更记录。

## 回滚

升级前脚本会把受保护目录备份到：

```text
.upgrade-backups/
```

如果升级失败，优先恢复备份目录，再检查 Git 状态。已经提交过的内网项目也可以通过备份分支或 Git 历史恢复。
