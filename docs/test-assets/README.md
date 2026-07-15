# 测试资产

`docs/test-assets/` 保存用户长期产品事实和运行目录，框架修改不得删除真实内容。

- `catalog/`：用户明确要求时增量归档的稳定产品事实。
- `product-map.xlsx`：产品事实查询视图。
- `batch-runs/`：按用户指定测试范围保存的单会话运行产物。
- `modules/`、`imports/`：可选正式归档与导入副本。

运行事实保存在 run-dir 根目录的 `facts.json`。交付默认留在 `deliverables/`；诊断证据不是必需产物，也不会自动写入长期资产。
