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

页面事实必须记录实际观察到的 `menu_path` 数组和页面名称。元素事实由运行时追加精简的 `exploration_requirements`，它是页面属性经过 DFX 策略得到的交互前清单，不是新的产物或义务队列。每个事务检查点必须同时记录 `result`、结构化 `result_anchor`、主验证 `element_ref` 和全部 `used_element_refs`；输入检查同时记录 `input_class`。已完成检查可立即写入，尚未执行的既定输入分支和有限选项由记录结果持续扣减并在checkpoint汇总；缺少可观察值/tokens、声明但未使用的控件或触发动作时事务不写入。`target` 和 `field` 用于表达观察语义，不要求逐字进入用例；只有 `tokens`（优先）或 `value` 用于预期锚定。用例导航由页面事实生成。进程中断时只自动丢弃无法解析的最后一个未完整行，中间行损坏仍立即报错。

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
      {"element_ref": "EL-SEVERITY", "used_element_refs": ["EL-SEVERITY"], "action": "选择严重", "option_value": "严重", "result": "列表只显示严重级别告警", "result_anchor": {"assertion": "all_equal", "target": "告警列表", "field": "告警级别", "value": "严重"}},
      {"element_ref": "EL-SEVERITY", "used_element_refs": ["EL-SEVERITY"], "action": "选择警告", "option_value": "警告", "result": "列表只显示警告级别告警", "result_anchor": {"assertion": "all_equal", "value": "警告"}}
    ],
    "recovery_result": "恢复全部级别"
  }
}
```

`facts.json` 只有七类业务事实和一个非业务 `checkpoint` 摘要。正常记录事务不重放全部事件；页面结束或显式checkpoint时编译一次。恢复发现facts落后于events时自动重建。运行时不按 `transaction_type` 内置专用 Case 模板；关系使用内部引用，对外用例不得出现。

## case-plan.json

```json
{
  "schema_version": "2.0",
  "source": "facts.json",
  "functions": [{
    "function_ref": "FN-FILTER",
    "name": "告警级别筛选",
    "cases": [{
      "case_id": "TC-FILTER-001",
      "page_ref": "PAGE-ALARM-LIST",
      "title": "告警级别逐项筛选",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程"
    }]
  }],
  "check_assignments": [
    {"transaction_ref": "TX-FILTER", "check_index": 1, "disposition": "case", "case_id": "TC-FILTER-001"},
    {"transaction_ref": "TX-FILTER", "check_index": 2, "disposition": "case", "case_id": "TC-FILTER-001"}
  ]
}
```

计划只写测试意图和唯一检查点分配账本；`fact_refs`、元素覆盖、功能覆盖和DFX关联由系统派生，不要求模型重复维护。
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
      {"action": "在告警级别中选择严重", "expected": "告警列表刷新，所有记录的告警级别均为严重", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 1}},
      {"action": "在告警级别中选择警告", "expected": "列表只显示警告级别告警", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 2}}
    ],
    "fact_refs": ["FN-FILTER", "EL-SEVERITY", "TX-FILTER"]
  }]
}
```

模型提交步骤时只写 `action+expected`；写入器按 `check_assignments` 顺序自动注入一个内部 `source_check`，并从主验证和辅助使用控件派生 `fact_refs`。结构化 `result_anchor` 只校验明确的 `tokens` 或结果 `value`，允许目标、字段和观察原句使用更完整的等价表述。内部来源不导出Excel。
用例通过内部 `write-cases` 写入；标题、菜单路径、配对步骤、具体数据、功能顺序和事实引用在生成时完成约束。
