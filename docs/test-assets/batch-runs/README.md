# 运行目录

每个用户指定测试范围使用一个 run-dir；不得按最小标题、元素或用例分片。标准结构：

```text
<run-dir>/
  events.jsonl
  facts.json
  case-plan.json
  function-cases.json
  review.json
  deliverables/
    正式测试设计.xlsx
    测试系统导入.xlsx
```

中断时从 facts 与实际页面状态恢复。目录中不使用发现 CSV、证据门禁、逐元素任务状态、Agent 文件、用例分片或临时修复脚本。
