# 批次运行状态账本

`docs/test-assets/batch-runs/` 是内部任务级执行状态目录，用于记录大范围测试设计任务的分批计划、批次状态、质量门禁和最终复盘。该目录不作为默认客户交付件，客户交付件仍放在 `docs/test-design/current/` 或 `docs/test-design/deliverables/`。

## 适用范围

当任务范围属于全产品、多个一级模块、某个大模块，或超过一个三级菜单/页面域时，必须创建本次任务的批次运行目录：

```text
docs/test-assets/batch-runs/<YYYYMMDD>_<任务标识>/
  batch-plan.md
  batch-status.csv
  batch-review.md
  page-discovery.csv
  artifacts/
```

## 文件职责

- `batch-plan.md`：记录菜单轮廓、页面清单、功能地图、拆分维度、依赖批次和预计交付物。
- `batch-status.csv`：记录每个批次的执行状态、覆盖数量、用例数量和覆盖质量自检结果，是进入下一批的门禁文件。
- `batch-review.md`：记录所有批次完成后的跨模块汇总、回归范围、风险与待确认问题。
- `page-discovery.csv`：记录页面或功能点实探证据，包括页面入口、元素、交互方式、完整点击路径、观察行为、覆盖状态和关联用例 ID。
- `artifacts/`：保存本次批次执行过程中产生的中间截图、页面遍历笔记、临时导出或核对材料。

## 执行规则

1. 建立批次运行目录时，应优先复制 `templates/` 中的模板文件。
2. 每个批次正式写测试用例前，必须先完成当前批次的页面或功能点遍历。
3. 每个批次正式写测试用例前，必须把页面或功能点实探结果写入 `page-discovery.csv`。
4. 每个批次完成后，必须更新 `batch-status.csv`，并填写页面数、元素总数、已覆盖元素数、待确认元素数、功能用例数、性能场景数、异常用例数、边界用例数、权限/状态用例数、数据一致性用例数、页面遍历完成、功能用例完成、性能设计完成、异常边界权限覆盖完成、页面元素覆盖完成、产品版图已更新、导入文件路径、导入文件已生成和覆盖质量自检。
5. 已覆盖元素数、功能用例数、性能场景数和各测试维度数量必须能从正式测试设计 Excel 或页面元素覆盖清单中追溯；不能只填写主观完成状态。
6. 当前批次的覆盖质量自检通过后，才能进入下一批。
7. 禁止创建承载全量测试用例正文的单一中间文件，例如单个 Python、JSON、CSV、Markdown 或临时脚本文件；脚本只能用于当前批次的模板填充、格式转换或校验，并保存到本任务 artifacts/scripts/，不得把多个三级菜单/页面域、多个批次或全产品测试用例先集中写入一个文件后再统一生成 Excel。
8. 最终汇总只引用已归档批次成果和用例 ID，不得重新生成各批完整用例。

## 运行期门禁

`scripts/validate-test-design.ps1` 会扫描 `docs/test-assets/batch-runs/`、`docs/test-design/current/` 和 `docs/test-design/deliverables/`，拦截疑似承载全量测试用例正文的单一中间文件，例如 `all_cases.py`、`full_product_cases.json`、`merged_cases.csv`、`case_pool.md` 或包含“全量测试用例”“多个三级菜单/页面域”“统一生成 Excel”等聚合痕迹的文件。标准批次账本文件 `batch-plan.md`、`batch-status.csv`、`batch-review.md`、`page-discovery.csv` 以及 `templates/` 模板目录不会被误判。
- 每个已通过批次默认只能覆盖 1 个三级菜单/页面域；确需合并时最多允许合并 2 个三级菜单/页面域，且必须在 `batch-status.csv` 的 `拆分/合并原因` 中说明合并原因，超过 2 个必须拆成独立批次。已通过批次的导入文件路径必须真实存在，并能与归档测试设计逐个匹配校验。
