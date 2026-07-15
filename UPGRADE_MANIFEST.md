# 升级清单

本文件用于外网生成升级包和内网应用升级包时确认升级边界。普通框架升级只能更新规范、模板和脚本；不得覆盖内网业务资产。

## 版本

- framework_version: 3.0.0
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

3.0.0 移除多 Agent 正确性依赖，统一为单执行者确定性流程。页面实探从阶段末拒绝改为逐元素执行前义务：`discovery-next/begin/complete` 绑定同一会话的读取、真实操作、变化后读取，CodeBuddy Hook 只保存哈希和顺序；输入、动态选择、分页、弹窗分支自动写入独立账本。编辑/配置按每个可修改元素执行一次真实变更，并在同一义务内绑定保存、回显、重新打开持久化和实际生效。功能用例拒绝内部 UID/探针路径、证据截图步骤、待观察预期、空名称和非标准字段；正式 Excel 拒绝物理空白行和内部执行标识。旧批次恢复时会补齐控制配置和分支模板，但不会伪造执行记录，继续推进前必须重新关闭未证明的义务。产品事实 schema 仍为 2.0.0。

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
