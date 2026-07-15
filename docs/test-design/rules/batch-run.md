# 大范围运行规则

范围包含多个最小功能标题时，每个标题使用独立 run-dir，分别执行完整 discovery、plan、cases、review、delivery。不同标题不共用 events、facts、plan 或 cases，避免上下文和事实串批。

同一标题内部保持连续页面会话，禁止按元素拆成大量微任务。完成一个标题后，再读取下一标题的 scope 与事实开始新阶段。
