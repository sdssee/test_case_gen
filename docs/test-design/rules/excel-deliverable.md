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

正式测试设计必须以 `docs/test-design/codebuddy-test-design-template.xlsx` 为唯一结构与样式基线。生成时先复制模板，再填充数据内容；通用 xlsx/表格 Skill 只可用于读取、渲染和视觉检查，不得直接写正式交付件。禁止临时脚本通过 `Workbook()`、`create_sheet()` 或直接修改 Excel XML 创建、重建或修补 `docs/test-design/deliverables/*.xlsx`。

## 页面元素覆盖清单

- `页面元素覆盖清单` 只是覆盖追踪矩阵，不是测试用例 Sheet。
- 只记录页面元素、业务依据、覆盖状态、发现方式、素材来源和关联的 `覆盖用例 ID`。
- 不得在该 Sheet 编写独立测试用例、操作步骤、测试数据或完整预期结果正文。
- 所有功能测试用例必须写入 `功能测试用例` Sheet。
- 所有性能测试场景必须写入 `性能测试设计` Sheet。

## 单元格格式

- `前置条件`、`操作步骤`、`预期结果` 必须编号换行。
- 多行字段必须启用自动换行和顶部对齐；写入内容后必须根据列宽、显式换行、文本长度和字号自动调整行高，模板示例行高作为下限，禁止统一写死固定行高。
- 正式测试设计和导入文件不得保留 Excel Table 对象或 `/xl/tables/table*.xml` 部件；页面元素覆盖清单等 Sheet 使用普通单元格区域、样式和自动筛选，避免打开文件触发 Microsoft Excel 修复提示或部分内容损坏提示。
- 表头、Sheet、字段顺序和枚举必须遵守 `docs/test-design/excel-template-spec.md`。
- 只允许数据内容、数据行数量、按内容计算的数据行行高，以及下拉验证和筛选的末行范围发生变化；Sheet 顺序、表头、列宽、表头样式、数据行基础样式、下拉规则和筛选列必须与正式模板一致。
- 正式测试设计和导入文件不得残留 `{NAV}`、`{NL}`、`{Q}`、`{E}`、`${...}`、`{{...}}`、`TODO`、`TBD` 等模板占位符或未完成标记。

## 交付件校验

生成正式测试设计 Excel 后，必须运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/validate-test-design-deliverable.ps1 -WorkbookPath <测试设计.xlsx> -ImportWorkbookPath <导入文件.xlsx>
```

正式测试设计与导入文件必须同时存在并一起通过校验。

完整交付必须使用 `scripts/test_design_excel_tools.py complete-deliverables`。该命令只能在临时文件中完成模板重建、内容填充、行高调整、导入文件生成和校验；全部通过后才写入正式路径。任一步失败都必须停止并保留原交付件，不得手工降级生成或继续叠加样式修补。
