# 测试资产目录

`docs/test-assets/` 保存可复用的最终交付副本和批次运行事实。客户交付件写入 `docs/test-design/current/` 或 `docs/test-design/deliverables/`；内部归档不作为默认客户交付件。

批次运行事实位于 `docs/test-assets/batch-runs/`。

```text
docs/test-assets/
  modules/       # 正式测试设计归档
  imports/       # 测试系统导入文件归档
  batch-runs/    # 批次状态、页面事实和过程材料
```

使用规则：

- 本轮事实以运行目录中的 `page-discovery.csv` 为准。
- 仅在用户指定历史版本、依赖模块或增量补充时，按模块路径直接读取相关归档，不做全库扫描。
- 最终测试设计和导入文件由 `complete-deliverables` 分别归档到 `modules/` 和 `imports/`。
- 历史 Excel 作为快照保留，不因普通框架升级批量改写。
- `docs/test-assets/` 是升级保护目录。标识：PROTECTED_ASSET_DIRS。
