# 产物契约

## 最小运行目录

```text
run-dir/
├─ events.jsonl
├─ facts.json
├─ case-plan.json
├─ function-cases.json
├─ review.json
└─ deliverables/
   ├─ 正式测试设计.xlsx
   └─ 测试系统导入.xlsx
```

`diagnostics/` 只在工具自然产生 trace、用户明确要求或高风险排查时创建；后续阶段不得读取它。

## events.jsonl 与 facts.json

事件类型只有 `scope`、`page`、`function`、`element`、`transaction`、`test_object`、`open_item`。新事实只需提交 `kind` 和 `data`，运行时自动生成 `fact_id`；同批事件可声明 `local_ref`，并在后续字段中用 `@local_ref` 建立关系。更新既有事实时使用运行时已返回的 `fact_id`。同一 `fact_id` 的最后有效事件形成当前事实。

页面事实必须记录实际观察到的 `menu_path` 数组和页面名称。用例导航由该页面事实生成，不从测试范围文字推断。一个完整业务事务通过校验后才追加为一行；进程中断时只自动丢弃无法解析的最后一个未完整行，中间行损坏仍立即报错。

一个有限选项功能事务示例（仅说明契约，不代表预置功能）：

```json
{
  "kind": "transaction",
  "local_ref": "filter_transaction",
  "data": {
    "function_ref": "FN-FILTER",
    "element_refs": ["EL-SEVERITY"],
    "transaction_type": "selection",
    "checks": [
      {"element_ref": "EL-SEVERITY", "action": "选择严重", "option_value": "严重", "result": "列表只显示严重级别告警"},
      {"element_ref": "EL-SEVERITY", "action": "选择警告", "option_value": "警告", "result": "列表只显示警告级别告警"}
    ],
    "recovery_result": "恢复全部级别"
  }
}
```

`facts.json` 只有 `scope`、`pages`、`functions`、`elements`、`transactions`、`test_objects`、`open_items` 七类业务事实。运行时不按 `transaction_type` 内置搜索、分页、弹窗等专用 Case 模板；该字段只描述本次实际事务。关系使用 `page_ref`、`function_ref`、`element_ref`、`test_object_ref`；对外用例不得出现这些内部引用。

## case-plan.json

```json
{
  "schema_version": "2.0",
  "source": "facts.json",
  "functions": [{
    "function_ref": "FN-FILTER",
    "name": "告警级别筛选",
    "dfx_decisions": [{"element_ref": "EL-SEVERITY", "code": "finite_options", "disposition": "covered_by_baseline", "case_ids": ["TC-FILTER-001"], "reason": "有限选项在基线用例中逐项验证"}],
    "cases": [{
      "case_id": "TC-FILTER-001",
      "page_ref": "PAGE-ALARM-LIST",
      "title": "告警级别逐项筛选",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程",
      "fact_refs": ["FN-FILTER", "EL-SEVERITY", "TX-FILTER"],
      "covered_checks": {"TX-FILTER": [1, 2]}
    }]
  }],
  "non_case_checks": []
}
```

计划只写测试意图、DFX、事实关系和事务检查项分配，不提前撰写完整步骤。
计划通过内部 `write-plan` 写入；结构或映射错误在本次生成动作中局部修正，不把错误计划留给最终 Review。

## function-cases.json

```json
{
  "schema_version": "2.0",
  "source_plan": "case-plan.json",
  "cases": [{
    "case_id": "TC-FILTER-001",
    "function_ref": "FN-FILTER",
    "title": "告警级别筛选-告警级别逐项筛选",
    "preconditions": ["告警列表存在严重和警告级别的可查看数据"],
    "test_data": "告警级别：严重、警告",
    "steps": [
      {"action": "进入告警管理-告警列表", "expected": "显示告警查询区和告警列表"},
      {"action": "在告警级别中选择严重", "expected": "列表只显示严重级别告警", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 1}},
      {"action": "在告警级别中选择警告", "expected": "列表只显示警告级别告警", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 2}}
    ],
    "fact_refs": ["FN-FILTER", "EL-SEVERITY", "TX-FILTER"]
  }]
}
```

步骤必须以 `action+expected` 配对保存；除导航外，每一步只关联一个 `source_check`，不同检查结果不得压缩成“依次操作”的笼统步骤。`source_check` 只用于内部追溯，Excel 不导出。Excel 组装时再拆成编号换行的“操作步骤”和“预期结果”。
用例通过内部 `write-cases` 写入；标题、菜单路径、配对步骤、具体数据、功能顺序和事实引用在生成时完成约束。
