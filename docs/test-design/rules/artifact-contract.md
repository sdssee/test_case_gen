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

事件类型只有 `scope`、`page`、`function`、`element`、`transaction`、`test_object`、`open_item`。每行包含 `kind`、`fact_id`、`data`；同一 `fact_id` 的最后有效事件形成当前事实。

一个连续分页事务示例：

```json
{
  "kind": "transaction",
  "fact_id": "TX-PAGE",
  "data": {
    "function_ref": "FN-PAGE",
    "element_refs": ["EL-PAGE-SIZE", "EL-PAGER"],
    "transaction_type": "pagination",
    "checks": [
      {"element_ref": "EL-PAGE-SIZE", "action": "选择10条/页", "option_value": 10, "result": "列表与总页数按10条重新计算"},
      {"element_ref": "EL-PAGE-SIZE", "action": "选择20条/页", "option_value": 20, "result": "列表与总页数按20条重新计算"},
      {"element_ref": "EL-PAGER", "action": "进入下一页", "result": "页码增加且列表切换为下一页数据"}
    ],
    "recovery_result": "恢复第一页和初始条数"
  }
}
```

`facts.json` 只有 `scope`、`pages`、`functions`、`elements`、`transactions`、`test_objects`、`open_items` 七类业务事实。关系使用 `page_ref`、`function_ref`、`element_ref`、`test_object_ref`；对外用例不得出现这些内部引用。

## case-plan.json

```json
{
  "schema_version": "2.0",
  "source": "facts.json",
  "functions": [{
    "function_ref": "FN-PAGE",
    "name": "分页",
    "cases": [{
      "case_id": "TC-PAGE-001",
      "title": "每页条数切换",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程",
      "fact_refs": ["FN-PAGE", "EL-PAGE-SIZE", "TX-PAGE"],
      "covered_checks": {"TX-PAGE": [1, 2]}
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
    "case_id": "TC-PAGE-001",
    "function_ref": "FN-PAGE",
    "title": "分页-每页条数切换",
    "preconditions": ["告警列表存在超过30条可查看数据"],
    "test_data": "每页条数：10条、20条",
    "steps": [
      {"action": "进入告警管理-告警列表", "expected": "显示告警查询区、列表和分页区域"},
      {"action": "在每页条数中选择10条/页", "expected": "列表最多显示10条，总页数按10条重新计算"}
    ],
    "fact_refs": ["FN-PAGE", "EL-PAGE-SIZE", "TX-PAGE"]
  }]
}
```

步骤必须以 `action+expected` 配对保存；Excel 组装时再拆成编号换行的“操作步骤”和“预期结果”。
用例通过内部 `write-cases` 写入；标题、菜单路径、配对步骤、具体数据、功能顺序和事实引用在生成时完成约束。
