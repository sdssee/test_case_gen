# 运行目录

每个最小功能标题使用独立 run-dir。标准结构：

```text
<run-dir>/
  scope.json
  artifacts/discovery/events.jsonl
  artifacts/discovery/facts.json
  artifacts/discovery/evidence/
  case-plan.json
  function-cases.json
  review.json
  deliverables/
```

目录中不再使用发现 CSV、逐元素任务状态、用例分片或临时修复脚本。`events.jsonl` 是原始观察记录，`facts.json` 可随时重建。
