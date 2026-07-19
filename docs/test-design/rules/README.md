# 专题规则索引

无论当前阶段由手动 Agent 还是单会话降级 Skill 执行，都只加载下列对应专题；不得因执行者不同复制另一套规则。

- 页面访问、浏览器、桌面窗口：读取 `page-discovery.md` 与 `data-safety.md`。
- 写入或读取阶段文件：读取 `artifact-contract.md`。
- 用例规划和正文：读取 `dfx-test-strategy.md` 与 `case-design.md`。
- 大范围或多个最小功能标题：读取 `batch-run.md`。
- 正式 Excel 和测试系统导入：读取 `excel-deliverable.md`、`import-template.md`。
- 产品事实归档：读取 `product-map-sync.md`。

同一轮只读取当前阶段所需专题，避免上下文被重复规则占满。
