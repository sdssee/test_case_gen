# 测试资产归档规范

## 目标

测试事实以本轮 `page-discovery.csv`、正式测试设计和导入文件为准，不依赖对话记忆。历史资产只用于按需参考，不参与本轮完成判定。

客户交付件写入 `docs/test-design/current/` 或 `docs/test-design/deliverables/`；内部归档不作为默认客户交付件。

## 目录

```text
docs/test-design/
  current/       # 当前正式测试设计
  deliverables/  # 对外交付副本
docs/test-assets/
  modules/       # 按模块归档正式测试设计
  imports/       # 按模块归档测试系统导入文件
  batch-runs/    # 批次状态、页面事实与过程材料
```

## 读取原则

- 生成前默认只读取本轮输入和本轮运行目录。
- 用户指定历史版本、依赖模块或增量补充时，按模块路径和文件名直接读取相关归档；不扫描无关模块。
- 历史内容与当前页面冲突时，以当前实测事实为准，并把无法验证的差异记录到风险与待确认问题。
- 跨模块能力需要复用时引用已有用例 ID；依赖关系本身需要验证时，新增跨模块链路用例。
- 正式写测试用例前，展示风险项与待确认问题并取得用户确认；根据确认结果动态调整测试范围、测试数据、预期结果和风险等级。

## 写入原则

1. 当前正式测试设计写入 `docs/test-design/current/`，交付副本写入 `docs/test-design/deliverables/`。
2. 最终测试设计和导入文件分别复制到 `docs/test-assets/modules/`、`docs/test-assets/imports/`。
3. 页面事实只记录到本轮 `page-discovery.csv`，并编译到正式工作簿的页面元素覆盖清单。
4. 使用 `complete-deliverables` 一次完成格式修复、双 Excel 生成、复制和校验，不再执行额外的资产同步步骤。
5. `docs/test-assets/`、`docs/test-design/current/` 和 `docs/test-design/deliverables/` 是升级保护目录。标识：PROTECTED_ASSET_DIRS。

## 大范围任务

范围超过一个最小标题时，按最小标题路径建立批次队列。每批维护标准 `batch-status.csv` 和 `page-discovery.csv`，测试正文按功能块分片；全部批次完成后只做一次跨产物 Review。过程材料必须放在当前任务的 `artifacts/`，不得写入共享根目录。

每一批仍须完整覆盖功能、性能、异常、边界、权限、状态、数据一致性、风险、自动化建议和页面元素，不得因分批降低质量。
