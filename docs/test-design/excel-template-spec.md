# Excel 模板契约

`codebuddy-test-design-template.xlsx` 固定为 8 Sheet，表头名称和顺序是组装契约。`测试用例模板.xlsx` 是测试系统导入模板，生成时复制原模板并从第 2 行连续写入。

正式工作簿的功能用例字段来自 `function-cases.json`；其他 Sheet 从 scope、facts 和 plan 派生。模板样例在写入前清空，不保留空白数据行和 Excel Table 对象。

用例步骤、预期和前置条件按编号换行；工作簿中不得残留 `TODO`、`TBD`、模板样例、内部事实标识或截图指令。
