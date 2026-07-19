---
name: test-page-exploration
description: 深探网页或桌面页面并固化可用于测试设计的事实。用于初始扫描、动态控件发现、有限选项逐项验证、输入DFX分支、CRUD与单因素配置闭环，输出events.jsonl和facts.json。
allowed-tools: Read, Write, Bash, Grep, Glob, Browser, ComputerUse
---

# 页面深探

只负责 discovery，不编写计划、用例或 Excel。读取
`docs/test-design/rules/page-discovery.md`、`data-safety.md` 和
`artifact-contract.md`。

1. 从实际菜单进入目标页面，结合 DOM、可访问性树、可见状态和必要的 hover 动态
   识别功能。先扫描，按功能连续执行事务；操作改变页面后局部重扫，容器展开后递归
   登记子控件。不得使用固定的分页、按钮或业务清单。
2. 登记元素时声明页面可推断的有效输入等价类；运行时立即生成必填空值、明确格式、
   边界、唯一性、有限选项和配置基线等 `exploration_requirements`。按返回清单实测，
   不在 checkpoint 临时增加义务。
3. 每个有限选项和每个有效输入类分别实际触发并观察。有关联时只记录页面真实支持的
   关联，不做组合穷举；一个控件作为辅助使用不能替代其独立功能验证。
4. 新增、编辑、删除、普通修改和配置必须像人工操作一样完成提交、查询或重开、实际
   生效与恢复/清理。配置仅做默认基线和单因素变化。
5. 图标控件先从可见文本、aria、title、tooltip识别，必要时 hover；hover 只识别名称，
   安全点击并观察结果后才算覆盖。页面可验证问题自行操作，只把外部业务语义、权限、
   数据或环境阻塞记入 `open_items`。
6. 每个已完成检查立即用 `record` 写入稳定结果锚点。所有事务完成后做一次稳定全量
   扫描，确认 `unhandled_element_refs` 为空，再调用 `checkpoint` 编译 facts。

只使用 CLI 写入。完成条件是 checkpoint `ready=true`；未完成时只继续返回的既定分支，
不得转入计划阶段，也不得创建 CSV、截图义务、证据账本或自动重试任务。

