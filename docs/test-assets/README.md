# 产品级测试资产库

`docs/test-assets/` 是内部维护资产库，用于让 AI 和团队长期维护产品测试知识图谱。该目录不作为默认客户交付件，其中 `docs/test-assets/batch-runs/` 用于保存大范围任务的批次运行状态账本。

## 目录职责

```text
docs/test-assets/
  product-map.xlsx  # 产品测试知识图谱主文件
  modules/          # 按模块归档最终版测试设计
  imports/          # 归档测试系统导入文件副本
  batch-runs/       # 大范围任务的批次计划、状态、复盘和中间材料
  indexes/          # 兼容或补充索引
```

客户交付件应放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。交付件只包含本次任务范围，不默认包含 `product-map.xlsx`、全量业务链路、全量历史用例索引或内部可复用测试数据。

## AI 使用规则

1. 每次生成或维护测试用例前，先读取 `product-map.xlsx`。
2. 如果用户指定依赖模块，读取产品版图中登记的对应模块归档测试设计。
3. 生成前向用户展示产品理解摘要，包括当前模块、依赖模块、业务对象、业务链路、可复用历史用例和待确认问题。
4. 用户确认后生成客户交付件。
5. 生成或人工修订后，将最终版测试设计回存 `modules/`，导入文件副本回存 `imports/`，并更新 `product-map.xlsx`。
6. 当任务范围超过一个二级菜单时，创建或更新 `batch-runs/` 下的批次运行状态账本，按批次记录计划、状态、覆盖质量自检和复盘。

## 维护原则

- 规则存放在 `AGENTS.md`、`CODEBUDDY.md`、Skill 和 Rule 中。
- 事实存放在 `product-map.xlsx` 和归档测试设计中。
- 大范围任务的执行进度和质量门禁存放在 `batch-runs/`，不依赖 AI 对话记忆。
- 不依赖 AI 对话记忆保存具体业务事实。
- 用户人工新增或修改后的最终版本必须回存资产库。
- 外网到内网做普通框架升级时，`docs/test-assets/` 是受保护目录，不得被升级包覆盖或删除。标识：PROTECTED_ASSET_DIRS。
- `product-map.xlsx` 是主要可能演进的资产结构；结构变化时通过 `asset_schema_version`、升级清单、校验脚本和迁移脚本处理。
- `modules/` 和 `imports/` 中的历史 Excel 默认作为历史快照保留，不因框架升级而批量重写。
