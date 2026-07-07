# Excel 交付件规则

## 正式测试设计

正式测试设计 Excel 默认包含 8 个 Sheet：

1. `测试设计总览`
2. `需求用户故事拆解`
3. `测试场景矩阵`
4. `功能测试用例`
5. `性能测试设计`
6. `风险与待确认问题`
7. `自动化建议`
8. `页面元素覆盖清单`

正式测试设计工作簿不得新增 `测试系统导入用例` Sheet。

## 页面元素覆盖清单

- `页面元素覆盖清单` 只是覆盖追踪矩阵，不是测试用例 Sheet。
- 只记录页面元素、业务依据、覆盖状态、发现方式、素材来源和关联的 `覆盖用例 ID`。
- 不得在该 Sheet 编写独立测试用例、操作步骤、测试数据或完整预期结果正文。
- 所有功能测试用例必须写入 `功能测试用例` Sheet。
- 所有性能测试场景必须写入 `性能测试设计` Sheet。

## 单元格格式

- `前置条件`、`操作步骤`、`预期结果` 必须编号换行。
- 多行字段必须启用自动换行。
- 工作簿中的 Excel 表格对象、自动筛选范围和实际数据区域必须一致；不得出现新增数据行后表格对象仍停留在模板前三行、打开文件触发 Microsoft Excel 修复提示或部分内容损坏提示的情况。
- 表头、Sheet、字段顺序和枚举必须遵守 `docs/test-design/excel-template-spec.md`。
- 正式测试设计和导入文件不得残留 `{NAV}`、`{NL}`、`{Q}`、`{E}`、`${...}`、`{{...}}`、`TODO`、`TBD` 等模板占位符或未完成标记。

## 交付件校验

生成正式测试设计 Excel 后，必须运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx>
```

大范围任务追加：

```powershell
-BatchStatusPath <batch-status.csv>
```

如需校验产品版图和页面实探同步，传入或自动发现：

```powershell
-ProductMapPath docs/test-assets/product-map.xlsx -PageDiscoveryPath <page-discovery.csv>
```

生成导入文件后追加：

```powershell
-ImportWorkbookPath <导入文件.xlsx>
```

校验必须覆盖字段错位、下拉框、自动字段空值、模板数据验证、多行换行样式、页面元素覆盖关系、标题格式、编号步骤、性能设计、批次状态和产品版图同步。
