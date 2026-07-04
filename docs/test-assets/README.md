# 产品级测试资产库

`docs/test-assets/` 是内部维护资产库，用于让 AI 和团队长期维护产品测试知识图谱。该目录不作为默认客户交付件。

## 目录职责

```text
docs/test-assets/
  product-map.xlsx  # 产品测试知识图谱主文件
  modules/          # 按模块归档最终版测试设计
  imports/          # 归档测试系统导入文件副本
  indexes/          # 兼容或补充索引
```

客户交付件应放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。交付件只包含本次任务范围，不默认包含 `product-map.xlsx`、全量业务链路、全量历史用例索引或内部可复用测试数据。

## AI 使用规则

1. 每次生成或维护测试用例前，先读取 `product-map.xlsx`。
2. 如果用户指定依赖模块，读取产品版图中登记的对应模块归档测试设计。
3. 生成前向用户展示产品理解摘要，包括当前模块、依赖模块、业务对象、业务链路、可复用历史用例和待确认问题。
4. 用户确认后生成客户交付件。
5. 生成或人工修订后，将最终版测试设计回存 `modules/`，导入文件副本回存 `imports/`，并更新 `product-map.xlsx`。

## 维护原则

- 规则存放在 `AGENTS.md`、`CODEBUDDY.md`、Skill 和 Rule 中。
- 事实存放在 `product-map.xlsx` 和归档测试设计中。
- 不依赖 AI 对话记忆保存具体业务事实。
- 用户人工新增或修改后的最终版本必须回存资产库。
