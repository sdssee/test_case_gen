# 测试系统导入文件

正式测试设计与测试系统导入文件是两个独立工作簿，并从同一个 `function-cases.json` 生成。导入文件不再经过正式 Excel 二次转抄。

内部排查命令：

```powershell
scripts/run-test-design.ps1 deliver --run-dir <run-dir> --project-root .
```

新运行固定在 `docs/test-design/current/<run-id>/`，输出固定为该运行目录下的 `deliverables/正式测试设计.xlsx` 和 `deliverables/测试系统导入.xlsx`。交付回执返回两个文件的完整路径。
