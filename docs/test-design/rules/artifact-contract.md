# 阶段产物契约

## discovery 事件

`events.jsonl` 每行一个 JSON 对象，必填 `kind`、`fact_id`、`data`。支持：`scope`、`requirement`、`page`、`function`、`element`、`observation`、`test_object`、`risk`、`pending`、`absence`。同一 `fact_id` 后写的有效事件覆盖先前事实，`facts.json` 可通过 `compile-facts` 重建。

典型元素：

```json
{"kind":"element","fact_id":"FACT-E-001","data":{"element_id":"E-001","function_id":"F-001","name":"每页条数","interactive":true,"option_set":"finite","options":[10,20,30]}}
```

连续分页观察可以一次记录多个选项：

```json
{"kind":"observation","fact_id":"FACT-O-001","element_id":"E-001","data":{"function_id":"F-001","action":"依次选择各条数并操作实际存在的翻页控件","option_values":[10,20,30],"before":"记录初始页和总数","result":"列表条数、页数、页码与按钮状态按操作变化","recovery_result":"恢复初始状态"},"evidence":[{"path":"artifacts/discovery/evidence/pagination.txt","location":"事务记录"}]}
```

配置元素增加 `configuration:true` 和 `default_value`；默认基线可以复用同一元素上的 CRUD 创建/编辑观察（`variant=default`），其他每个有限选项分别使用一个 `closure=configuration` 的观察。有效闭环带 `option_value`、`outcome=success`、`test_object_id`、`commit_result`、`persistence_result`、`effect_result`、`recovery_result`。CRUD 的 `closure=create|edit|delete` 同样绑定已登记的 `test_object_id`。

## case-plan.json

```json
{
  "schema_version":"1.0",
  "source":"artifacts/discovery/facts.json",
  "functions":[{
    "function_id":"F-001",
    "name":"分页",
    "cases":[{
      "case_id":"TC-PAGE-001",
      "title":"每页条数切换",
      "strategy":"baseline",
      "dfx_dimension":"DFT功能",
      "dfx_scenario":"正向流程",
      "fact_ids":["FACT-F-001","FACT-E-001","FACT-O-001"]
    }]
  }],
  "risks":[]
}
```

## function-cases.json

```json
{
  "schema_version":"1.0",
  "source_plan":"case-plan.json",
  "cases":[{
    "case_id":"TC-PAGE-001",
    "function_id":"F-001",
    "title":"分页-每页条数切换",
    "priority":"P1",
    "test_type":"功能测试",
    "dfx_dimension":"DFT功能",
    "dfx_scenario":"正向流程",
    "preconditions":["进入列表并记录总记录数"],
    "test_data":"列表存在超过30条测试数据",
    "steps":["选择每页10条","依次选择20条和30条"],
    "expected_results":["列表和总页数按10条重新计算","列表和总页数分别按20条、30条重新计算"],
    "fact_ids":["FACT-F-001","FACT-E-001","FACT-O-001"],
    "automation":true
  }]
}
```

步骤和预期必须是等长 JSON 数组；同一功能的用例连续排列。
